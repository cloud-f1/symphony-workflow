"""Neutral plan -> Jira (ADLC-275, plan 0008 section 3).

| Layer | Jira |
|---|---|
| Category | Epic |
| Task | Story (`parent` = its Epic) |
| To-Do | Subtask (`parent` = its Story) |

Same contract as the Linear sink: a trailing `sd-id: <id>` line in each description ties a card to
its entry, a re-run updates in place, nothing is ever deleted, `dry_run` writes nothing, and an
existing card keeps the status Jira has (`sync-status` moves it, by the three-way rule).

Two Jira facts shape this file (both found in ADLC-146): the v3 search returns descriptions as ADF,
not the wiki text we wrote, so every candidate is re-read through the v2 issue endpoint; and a JQL
child list lags a few seconds behind a create, so the id -> key map from the last run (`keys`,
kept in `docs/context/sync-state.json`) is trusted first and the marker search is only the way to
adopt cards made before that map existed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.drift import normalize
from symphony_workflow.models import HierarchyIssue
from symphony_workflow.plan import Plan, Status, assign_plan_ids, extract_marker, marker_line
from symphony_workflow.status_sync import Card

# Jira names the transitions after the target status on every default workflow. A team's own
# workflow can differ; `status_name` in the sink call overrides, and a missing transition is
# reported as `no_state`, never forced.
STATUS_NAME: dict[Status, str] = {
    "todo": "To Do",
    "in_progress": "In Progress",
    "done": "Done",
    "blocked": "In Progress",
}


def remote_status(issue: HierarchyIssue, status_map: dict[str, Status] | None = None) -> Status:
    """Jira status -> plan status: a name from the config, a status called Blocked, then the
    workflow-independent status category (new / indeterminate / done)."""
    name = issue.status.strip().lower()
    if status_map and name in status_map:
        return status_map[name]
    if name == "blocked":
        return "blocked"
    return {"new": "todo", "indeterminate": "in_progress", "done": "done"}.get(
        issue.status_category or "", "todo"
    )


@dataclass
class JiraChange:
    kind: Literal["epic", "story", "subtask"]
    id: str
    title: str
    action: Literal["create", "update", "unchanged"]
    key: str | None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "kind": self.kind,
            "id": self.id,
            "title": self.title,
            "action": self.action,
            "key": self.key,
        }


def _description(body: str, entry_id: str, criteria: list[str] | None = None) -> str:
    parts = [body.strip()] if body.strip() else []
    if criteria:
        parts.append("Acceptance criteria:\n" + "\n".join(f"* {c}" for c in criteria))
    parts.append(marker_line(entry_id))
    return "\n\n".join(parts)


def _same(a: str | None, b: str) -> bool:
    return normalize(a) == normalize(b)


class _Finder:
    """Finds the card for an entry: the cached key first, then the marker among a parent's
    children (read through v2). Candidate lists are fetched once per parent."""

    def __init__(self, client: JiraClient, project_key: str, keys: dict[str, str]) -> None:
        self._client, self._project, self.keys = client, project_key, keys
        self._cache: dict[str, list[HierarchyIssue]] = {}

    def _candidates(self, parent_key: str | None) -> list[HierarchyIssue]:
        label = parent_key or ""
        if label not in self._cache:
            if parent_key is None:
                found = self._client.search_hierarchy(
                    f"project = {self._project} AND issuetype = Epic ORDER BY key ASC"
                )
            else:
                found = self._client.list_children(parent_key)
            self._cache[label] = [self._client.get_hierarchy_issue(i.key) for i in found]
        return self._cache[label]

    def find(self, entry_id: str, parent_key: str | None) -> HierarchyIssue | None:
        cached = self.keys.get(entry_id)
        if cached is not None:
            issue = _trusted(self._client, self._project, entry_id, cached)
            if issue is not None:
                return issue
            del self.keys[entry_id]  # stale, deleted by hand, or not ours: never act on it
        for issue in self._candidates(parent_key):
            if extract_marker(issue.description) == entry_id:
                self.keys[entry_id] = issue.key
                return issue
        return None


def _trusted(
    client: JiraClient, project_key: str, entry_id: str, key: str
) -> HierarchyIssue | None:
    """The card `key` when it really is the card for `entry_id`, else None. The key map is a file
    in the project directory, which may be someone else's: a key naming another project, or a card
    whose `sd-id:` marker is not this entry's, must never be updated or moved."""
    if not key.startswith(f"{project_key}-"):
        return None
    try:
        issue = client.get_hierarchy_issue(key)
    except JiraError as exc:
        if exc.status_code == 404:
            return None
        raise
    return issue if extract_marker(issue.description) == entry_id else None


def sync_plan_to_jira(
    plan: Plan,
    client: JiraClient,
    *,
    project_key: str,
    keys: dict[str, str] | None = None,
    dry_run: bool = False,
    subtask_type: str = "Subtask",
) -> tuple[list[JiraChange], dict[str, str]]:
    """Create or update the plan in Jira. Returns the changes and the id -> key map to save.
    Raises `JiraError` on any API failure (what was created before it stays, no transaction)."""
    assign_plan_ids(plan)
    finder = _Finder(client, project_key, dict(keys or {}))
    changes: list[JiraChange] = []

    def upsert(
        kind: Literal["epic", "story", "subtask"],
        entry_id: str,
        title: str,
        description: str,
        issue_type: str,
        parent_key: str | None,
        status: Status,
    ) -> str | None:
        found = finder.find(entry_id, parent_key)
        if found is None:
            changes.append(JiraChange(kind, entry_id, title, "create", None))
            if dry_run or (kind != "epic" and parent_key is None):
                return None  # a dry run, or a parent that was only "would create"
            created = client.create_issue(
                project_key, title, description, issue_type, parent=parent_key
            )
            finder.keys[entry_id] = created.key
            changes[-1].key = created.key
            if status != "todo":
                client.transition_issue(created.key, STATUS_NAME[status])
            return created.key
        if found.summary != title or not _same(found.description, description):
            changes.append(JiraChange(kind, entry_id, title, "update", found.key))
            if not dry_run:
                client.update_issue(found.key, summary=title, description=description)
        else:
            changes.append(JiraChange(kind, entry_id, title, "unchanged", found.key))
        return found.key

    for category in plan.categories:
        cid = category.id or category.title
        epic_key = upsert(
            "epic", cid, category.title, _description(category.description, cid), "Epic", None,
            category.status,
        )  # fmt: skip
        for task in category.tasks:
            tid = task.id or task.title
            story_key = upsert(
                "story", tid, task.title,
                _description(task.description, tid, task.acceptance_criteria), "Story", epic_key,
                task.status,
            )  # fmt: skip
            for todo in task.todos:
                did = todo.id or todo.title
                upsert(
                    "subtask", did, todo.title, _description(todo.description, did), subtask_type,
                    story_key, todo.status,
                )  # fmt: skip
    return changes, finder.keys


class JiraStatusBackend:
    """`sync-status` against Jira. Cards are the ones in the `keys` map (so run `sync-plan --to
    jira` first), each read through v2 and used only when it is in `project_key` and carries this
    entry's marker (the map is a file in the project directory: see `_trusted`)."""

    def __init__(
        self,
        client: JiraClient,
        keys: dict[str, str],
        status_map: dict[str, Status] | None = None,
        *,
        project_key: str,
    ) -> None:
        self._client, self._keys, self._status_map = client, keys, status_map
        self._project = project_key

    def cards(self, plan: Plan) -> dict[str, Card]:
        wanted = [t.id for c in plan.categories for t in c.tasks] + [
            d.id for c in plan.categories for t in c.tasks for d in t.todos
        ]
        found: dict[str, Card] = {}
        for entry_id in wanted:
            if not entry_id or entry_id not in self._keys:
                continue
            issue = _trusted(self._client, self._project, entry_id, self._keys[entry_id])
            if issue is not None:
                found[entry_id] = Card(remote_status(issue, self._status_map), issue)
        return found

    def move(self, card: Card, status: Status, *, dry_run: bool) -> bool:
        issue: HierarchyIssue = card.handle
        name = STATUS_NAME[status]
        if dry_run:
            return name in {t.name for t in self._client.list_transitions(issue.key)}
        try:
            self._client.transition_issue(issue.key, name)
        except JiraError as exc:
            if exc.status_code == 404:  # no transition with that name from here
                return False
            raise
        return True
