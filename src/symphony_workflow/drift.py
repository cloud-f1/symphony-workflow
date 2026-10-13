"""Content-drift detection and re-sync between a `decomposition.json` and the live Jira
Epic -> Story -> Subtask tree (ADLC-146; design in docs/plans/0007-jira-diff-sync/).

`sync_decomposition.py` creates cards once. This module answers "does Jira still match the
spec?" (`diff_decomposition`, read-only) and applies the safe part of the answer
(`apply_update`: create what is missing, update what changed, never delete).

Identity: every Story/Task has an optional stable `id` (`s1`, `s1.t2`), assigned once and
written back to `decomposition.json`. Created/updated cards end their description with a
`sd-id: <id>` marker line, so a rename in the spec updates the card instead of duplicating
it. A card created before ids existed has no marker; `adopt=True` matches those by exact
summary (the CLI prints the proposal and asks before writing -- never a silent guess).

Status is deliberately not compared (a Story can legitimately be In Progress while its spec
is unchanged), and comments/remote links are not drift. Orphans (cards in Jira only) are
reported, never deleted -- the Jira tools here have no delete.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from symphony_workflow.client import JiraClient
from symphony_workflow.decompose import Decomposition, Story, Task, assign_ids
from symphony_workflow.models import HierarchyIssue
from symphony_workflow.sync_decomposition import (
    project_key_from_issue_key,
    render_story_description,
    render_task_description,
    save_state,
)

_MARKER_RE = re.compile(r"^sd-id:[ \t]*(\S+)[ \t]*$", re.MULTILINE)

OK = "ok"
MISSING = "missing"
SUMMARY_CHANGED = "summary-changed"
DESCRIPTION_CHANGED = "description-changed"
ORPHAN = "orphan"
PARENT_DIFFERS = "parent-differs"


def extract_marker(description: str | None) -> str | None:
    """The id from the last `sd-id:` line of a description, or None."""
    found = _MARKER_RE.findall(description or "")
    return found[-1] if found else None


def normalize(text: str | None) -> str:
    """What Jira round-trips differently from what we send: line endings, trailing
    whitespace, runs of blank lines. The marker line is dropped (compared separately).
    """
    cleaned = _MARKER_RE.sub("", (text or "").replace("\r\n", "\n").replace("\r", "\n"))
    lines = [line.rstrip() for line in cleaned.split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


@dataclass
class DiffItem:
    level: str  # "story" | "subtask"
    kind: str  # OK / MISSING / SUMMARY_CHANGED / DESCRIPTION_CHANGED / ORPHAN
    id: str | None
    key: str | None  # Jira key; None when missing
    parent_id: str | None = None  # spec id of the parent Story (subtasks)
    parent_key: str | None = None  # Jira key of the parent (None when the parent is missing)
    expected: str | None = None  # summary the spec wants (summary-changed) / title (missing)
    actual: str | None = None
    detail: str | None = None


@dataclass
class DiffReport:
    epic_key: str
    items: list[DiffItem] = field(default_factory=list)

    @property
    def drift(self) -> bool:
        return any(i.kind != OK for i in self.items)

    def counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for item in self.items:
            counts[item.kind] = counts.get(item.kind, 0) + 1
        return counts

    def as_dict(self) -> dict[str, Any]:
        return {
            "epic_key": self.epic_key,
            "drift": self.drift,
            "counts": self.counts(),
            "items": [asdict(i) for i in self.items],
        }


def _children(client: JiraClient, parent_key: str) -> list[HierarchyIssue]:
    """Children with their descriptions. The v3 search used by `list_children` returns
    descriptions as Atlassian Document Format (found live, ADLC-146), not the wiki text we
    wrote through v2, so each child is re-read through the v2 issue endpoint.
    """
    return [client.get_hierarchy_issue(c.key) for c in client.list_children(parent_key)]


def _match(
    entries: list[tuple[str, str]],
    children: list[HierarchyIssue],
    adopt: bool,
    known: dict[str, str] | None = None,
) -> tuple[dict[str, HierarchyIssue], list[HierarchyIssue]]:
    """Pair spec entries `(id, title)` with Jira children: by marker first, then (adopt only)
    by exact summary among still-unmatched children. Returns (id -> child, unmatched children).
    """
    ids = {entry_id for entry_id, _ in entries}
    matched: dict[str, HierarchyIssue] = {}
    for child in children:
        marker = extract_marker(child.description)
        if marker in ids and marker not in matched:
            matched[marker] = child
    left = [c for c in children if c not in matched.values()]
    for entry_id, _ in entries:  # cached id -> key, for cards whose marker was edited away
        if entry_id in matched:
            continue
        cached = [c for c in left if c.key == (known or {}).get(entry_id)]
        if cached:
            matched[entry_id] = cached[0]
            left.remove(cached[0])
    if adopt:
        for entry_id, title in entries:
            if entry_id in matched:
                continue
            same = [c for c in left if c.summary.strip() == title.strip()]
            if len(same) == 1:
                matched[entry_id] = same[0]
                left.remove(same[0])
    return matched, left


def _compare(
    level: str,
    entry_id: str,
    title: str,
    expected_description: str,
    child: HierarchyIssue,
    parent_id: str | None,
    parent_key: str,
) -> list[DiffItem]:
    items: list[DiffItem] = []
    if child.summary.strip() != title.strip():
        items.append(
            DiffItem(
                level,
                SUMMARY_CHANGED,
                entry_id,
                child.key,
                parent_id,
                parent_key,
                expected=title,
                actual=child.summary,
            )
        )
    body_differs = normalize(child.description) != normalize(expected_description)
    marker_differs = extract_marker(child.description) != entry_id
    if body_differs or marker_differs:
        why = "text differs" if body_differs else "id marker missing (adopted card)"
        items.append(
            DiffItem(
                level,
                DESCRIPTION_CHANGED,
                entry_id,
                child.key,
                parent_id,
                parent_key,
                detail=why,
            )
        )
    if not items:
        items.append(DiffItem(level, OK, entry_id, child.key, parent_id, parent_key))
    return items


def diff_decomposition(
    epic_key: str,
    decomposition: Decomposition,
    *,
    client: JiraClient,
    adopt: bool = False,
    known: dict[str, str] | None = None,
) -> DiffReport:
    """Compare `decomposition` with the live children of `epic_key`. Read-only. Entries
    without an `id` are given in-memory ids by position (call `assign_ids` + persist first
    if the result will be applied). `known` is the `sync-state.json` id -> key cache.

    A Subtask that carries a spec id but sits under a different Story than the spec says
    (e.g. after splitting an oversized Story by hand) is reported once as `parent-differs`,
    not as a `missing` + `orphan` pair.
    """
    assign_ids(decomposition)
    report = DiffReport(epic_key)
    story_children = _children(client, epic_key)
    matched, orphans = _match(
        [(s.id or "", s.title) for s in decomposition.stories], story_children, adopt, known
    )
    for story in decomposition.stories:
        sid = story.id or ""
        child = matched.get(sid)
        if child is None:
            report.items.append(
                DiffItem("story", MISSING, sid, None, expected=story.title, parent_key=epic_key)
            )
            for task in story.tasks:
                report.items.append(
                    DiffItem("subtask", MISSING, task.id, None, sid, None, expected=task.title)
                )
            continue
        report.items.extend(
            _compare(
                "story",
                sid,
                story.title,
                render_story_description(story, decomposition.definition_of_done),
                child,
                None,
                epic_key,
            )
        )
        report.items.extend(_diff_tasks(story, child.key, client, adopt, known))
    for orphan in orphans:
        report.items.append(
            DiffItem(
                "story",
                ORPHAN,
                extract_marker(orphan.description),
                orphan.key,
                parent_key=epic_key,
                actual=orphan.summary,
            )
        )
        # Subtasks under an unmatched Story may belong to a spec Story (moved by hand).
        for stray in _children(client, orphan.key):
            report.items.append(
                DiffItem(
                    "subtask",
                    ORPHAN,
                    extract_marker(stray.description),
                    stray.key,
                    parent_key=orphan.key,
                    actual=stray.summary,
                )
            )
    _pair_reparented(report)
    return report


def _pair_reparented(report: DiffReport) -> None:
    """Fold a `missing` Subtask and an `orphan` Subtask with the same spec id into one
    `parent-differs` item (key = the live card, parent_id = the spec Story it belongs under,
    parent_key = the Story it currently sits under).
    """
    strays = {i.id: i for i in report.items if i.level == "subtask" and i.kind == ORPHAN and i.id}
    for missing in [i for i in report.items if i.level == "subtask" and i.kind == MISSING]:
        stray = strays.get(missing.id)
        if stray is None or stray.parent_key == missing.parent_key:
            continue
        report.items.remove(missing)
        report.items.remove(stray)
        report.items.append(
            DiffItem(
                "subtask",
                PARENT_DIFFERS,
                missing.id,
                stray.key,
                missing.parent_id,
                stray.parent_key,
                actual=stray.actual,
                detail=f"sits under {stray.parent_key}, spec says Story {missing.parent_id}",
            )
        )


def _diff_tasks(
    story: Story,
    story_key: str,
    client: JiraClient,
    adopt: bool,
    known: dict[str, str] | None = None,
) -> list[DiffItem]:
    items: list[DiffItem] = []
    sid = story.id or ""
    children = _children(client, story_key)
    matched, orphans = _match([(t.id or "", t.title) for t in story.tasks], children, adopt, known)
    for task in story.tasks:
        tid = task.id or ""
        child = matched.get(tid)
        if child is None:
            items.append(
                DiffItem("subtask", MISSING, tid, None, sid, story_key, expected=task.title)
            )
            continue
        items.extend(
            _compare(
                "subtask", tid, task.title, render_task_description(task), child, sid, story_key
            )
        )
    for orphan in orphans:
        items.append(
            DiffItem(
                "subtask",
                ORPHAN,
                extract_marker(orphan.description),
                orphan.key,
                sid,
                story_key,
                actual=orphan.summary,
            )
        )
    return items


@dataclass
class UpdateAction:
    action: str  # "create" | "update"
    level: str
    id: str | None
    key: str | None  # created/updated Jira key (None in a dry run's create)
    summary: str
    fields: list[str] = field(default_factory=list)  # for updates: what changes
    done: bool = False


def apply_update(
    epic_key: str,
    decomposition: Decomposition,
    *,
    client: JiraClient,
    project_key: str | None = None,
    initial_status: str | None = None,
    adopt: bool = False,
    dry_run: bool = False,
    known: dict[str, str] | None = None,
    state_path: Path | None = None,
) -> list[UpdateAction]:
    """Create what is missing, re-parent Subtasks that sit under the wrong Story, then
    update what changed (summary/description), in that order. Never deletes and never
    touches status/comments/links of existing cards.
    Idempotent: a second run finds nothing to do. `dry_run` returns the same plan with
    `done=False` and writes nothing. Jira has no transaction: the first `JiraError` stops
    the run; actions already `done` stay done (the caller prints them) and a re-run
    resumes from the diff. With `state_path`, the id -> key cache is (re)written, also after a
    partial failure.
    """
    assign_ids(decomposition)
    project = project_key or project_key_from_issue_key(epic_key)
    report = diff_decomposition(epic_key, decomposition, client=client, adopt=adopt, known=known)
    stories = {s.id: s for s in decomposition.stories}
    tasks = {t.id: t for s in decomposition.stories for t in s.tasks}
    key_of = {i.id: i.key for i in report.items if i.level == "story" and i.key}
    actions: list[UpdateAction] = []

    for item in report.items:
        if item.kind == MISSING and item.level == "story":
            story = stories[item.id]
            actions.append(UpdateAction("create", "story", item.id, None, story.title))
    for item in report.items:
        if item.kind == MISSING and item.level == "subtask":
            actions.append(UpdateAction("create", "subtask", item.id, None, tasks[item.id].title))
    for item in report.items:
        if item.kind == PARENT_DIFFERS:
            actions.append(
                UpdateAction(
                    "reparent", "subtask", item.id, item.key, tasks[item.id].title, ["parent"]
                )
            )
    for item in report.items:
        if item.kind in (SUMMARY_CHANGED, DESCRIPTION_CHANGED):
            existing = next(
                (a for a in actions if a.action == "update" and a.key == item.key), None
            )
            what = "summary" if item.kind == SUMMARY_CHANGED else "description"
            if existing:
                existing.fields.append(what)
            else:
                title = (stories if item.level == "story" else tasks)[item.id].title
                actions.append(UpdateAction("update", item.level, item.id, item.key, title, [what]))
    if dry_run:
        return actions

    created: dict[str, str] = {}  # spec id -> new Jira key
    try:
        _execute(
            actions,
            report,
            decomposition,
            client,
            project,
            epic_key,
            initial_status,
            created,
            stories,
            tasks,
            key_of,
        )
    finally:
        if state_path is not None:
            ids = {i.id: i.key for i in report.items if i.id and i.key and i.kind != ORPHAN}
            ids.update(created)
            ids.update({a.id: a.key for a in actions if a.done and a.key and a.id})
            save_state(state_path, epic_key, ids)
    return actions


def _execute(
    actions: list[UpdateAction],
    report: DiffReport,
    decomposition: Decomposition,
    client: JiraClient,
    project: str,
    epic_key: str,
    initial_status: str | None,
    created: dict[str, str],
    stories: dict[str | None, Story],
    tasks: dict[str | None, Task],
    key_of: dict[str | None, str | None],
) -> None:
    for action in actions:
        if action.action == "reparent":
            item = next(i for i in report.items if i.id == action.id)
            target = created.get(item.parent_id or "") or key_of.get(item.parent_id)
            if target is None:
                raise RuntimeError(f"No Jira parent for Subtask {action.id!r} (bug in diff)")
            client.update_issue(action.key or "", parent=target)
            action.done = True
            continue
        if action.action == "create" and action.level == "story":
            story = stories[action.id]
            result = client.create_issue(
                project,
                story.title,
                render_story_description(story, decomposition.definition_of_done),
                "Story",
                parent=epic_key,
            )
            if initial_status:
                client.transition_issue(result.key, initial_status)
            created[story.id or ""] = result.key
            action.key, action.done = result.key, True
        elif action.action == "create":
            task = tasks[action.id]
            parent_id = next(i.parent_id for i in report.items if i.id == action.id)
            parent_key = created.get(parent_id or "") or key_of.get(parent_id)
            if parent_key is None:
                raise RuntimeError(f"No Jira parent for Subtask {action.id!r} (bug in diff)")
            result = client.create_issue(
                project, task.title, render_task_description(task), "Subtask", parent=parent_key
            )
            if initial_status:
                client.transition_issue(result.key, initial_status)
            action.key, action.done = result.key, True
        else:
            entry = stories.get(action.id) or tasks[action.id]
            description = (
                render_story_description(entry, decomposition.definition_of_done)
                if isinstance(entry, Story)
                else render_task_description(entry)
            )
            assert isinstance(entry, (Story, Task))
            client.update_issue(
                action.key or "",
                summary=entry.title if "summary" in action.fields else None,
                description=description if "description" in action.fields else None,
            )
            action.done = True
