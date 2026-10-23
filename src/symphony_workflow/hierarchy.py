"""Keep a project's Epic -> Story -> Subtask hierarchy honest: audit it, add Subtasks to Stories.

Why this exists: two repos that use this tool found the same failure independently. In one repo,
33 of 73 hand-created Stories had no Subtasks, so the board showed no progress and nobody could see
what was left. In another every card was Unassigned and no Story had Subtasks. `decompose` /
`sync-decomposition` only help for an Epic you have not built yet; they do nothing for Stories that
already exist. So:

* `audit` reads a project and reports what breaks the rules (below). It writes nothing.
* `add_subtasks` adds Subtasks to existing Stories from a plan, idempotently, and rolls them up when
  the Story is already Done (so the board shows 4/4, not 0/4).

The rules (the same ones `docs/JIRA_HIERARCHY.md` states):

* every Story-level issue (Story, Task, Bug) sits under an Epic and has Subtasks (2-6 is the target:
  one Subtask means the Story is really a Subtask, more than 8 means split the Story);
* an Epic has Stories;
* a Done Story has no open Subtasks (roll-up);
* optionally, everything is assigned to a named person.

Jira-only: it needs `parent`, `subtasks` and child search, which `TrackerClient` does not model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.models import HierarchyIssue
from symphony_workflow.rollup import _EPIC, _STORY, _SUBTASK, hierarchy_level

DONE = "Done"
SUBTASK_TYPE_NAMES = ("Subtask", "Sub-task")  # team-managed / company-managed spelling
MIN_SUBTASKS, MAX_SUBTASKS = 2, 8  # below / above this the finding is a warning, never an error
MAX_SUMMARY = 255


@dataclass
class Finding:
    key: str
    rule: str
    severity: str  # "error" fails the audit (exit 1); "warning" is reported only
    status: str
    summary: str
    detail: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "key": self.key,
            "rule": self.rule,
            "severity": self.severity,
            "status": self.status,
            "summary": self.summary,
            "detail": self.detail,
        }


@dataclass
class AuditResult:
    project: str
    epics: int = 0
    stories: int = 0
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.severity == "error"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "epics": self.epics,
            "stories": self.stories,
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.findings) - len(self.errors),
            "findings": [f.as_dict() for f in self.findings],
        }


def audit(
    client: JiraClient,
    project: str,
    *,
    open_only: bool = False,
    assignee: str | None = None,
) -> AuditResult:
    """Check `project`. `open_only` skips Stories that are already Done (and their roll-up
    check). `assignee` (a display name) turns "assigned to this person" into a rule."""
    result = AuditResult(project=project)
    not_done = " AND statusCategory != Done" if open_only else ""
    epics = client.search_hierarchy(f"project = {project} AND issuetype = Epic ORDER BY key ASC")
    # The JQL excludes `Subtask` by name only; a company-managed project spells it `Sub-task`, so
    # those come back too. Filter by hierarchy level before counting (ADLC-142).
    stories = [
        issue
        for issue in client.search_hierarchy(
            f"project = {project} AND issuetype not in (Epic, Subtask){not_done} ORDER BY key ASC"
        )
        if hierarchy_level(issue) == _STORY
    ]
    result.epics, result.stories = len(epics), len(stories)

    def add(issue: HierarchyIssue, rule: str, severity: str, detail: str = "") -> None:
        result.findings.append(
            Finding(issue.key, rule, severity, issue.status, issue.summary, detail)
        )

    def check_assignee(issue: HierarchyIssue) -> None:
        if assignee is None:
            return
        if issue.assignee is None:
            add(issue, "unassigned", "error", f"expected {assignee}")
        elif issue.assignee.strip().lower() != assignee.strip().lower():
            add(issue, "wrong_assignee", "error", f"{issue.assignee}, expected {assignee}")

    for epic in epics:
        check_assignee(epic)
        if not client.list_children(epic.key):
            add(epic, "epic_without_stories", "error", "an Epic needs Stories")

    for story in stories:
        check_assignee(story)
        if not story.parent_key:
            add(story, "no_epic", "error", "a Story belongs under an Epic")
        count = story.subtask_count if story.subtask_count is not None else 0
        if count == 0:
            add(
                story,
                "no_subtasks",
                "error",
                "every Story needs Subtasks; the last one is the tests",
            )
        elif count < MIN_SUBTASKS:
            add(story, "one_subtask", "warning", "one Subtask means the Story is really a Subtask")
        elif count > MAX_SUBTASKS:
            add(story, "too_many_subtasks", "warning", f"{count} Subtasks: split the Story")
        if story.is_done and count:
            open_kids = [c.key for c in client.list_children(story.key) if not c.is_done]
            if open_kids:
                add(
                    story,
                    "done_with_open_subtasks",
                    "error",
                    f"roll-up missed: {', '.join(open_kids)}",
                )
    result.findings.sort(key=lambda f: (f.severity != "error", _key_number(f.key)))
    return result


def _key_number(key: str) -> int:
    tail = key.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


# ---------------------------------------------------------------- add_subtasks


class PlanError(ValueError):
    """The plan is not usable. Nothing was created."""


def validate_plan(plan: Any) -> dict[str, list[str]]:
    """`{ "PROJ-12": ["Add X", "Tests: unit + e2e"], ... }`. Raises PlanError listing every
    problem."""
    problems: list[str] = []
    if not isinstance(plan, dict) or not plan:
        raise PlanError(
            "the plan must be a non-empty JSON object: {story key: [subtask summaries]}"
        )
    clean: dict[str, list[str]] = {}
    for key, summaries in plan.items():
        if not isinstance(key, str) or "-" not in key:
            problems.append(f"{key!r}: not an issue key")
            continue
        if not isinstance(summaries, list) or not summaries:
            problems.append(f"{key}: needs a non-empty list of subtask summaries")
            continue
        items = []
        for raw in summaries:
            text = raw.strip() if isinstance(raw, str) else ""
            if not text:
                problems.append(f"{key}: a subtask summary is empty or not a string")
            elif len(text) > MAX_SUMMARY:
                problems.append(f"{key}: a subtask summary is longer than {MAX_SUMMARY} characters")
            else:
                items.append(text)
        clean[key] = items
    if problems:
        raise PlanError("; ".join(problems))
    return clean


@dataclass
class StoryOutcome:
    key: str
    action: str  # "created" | "skipped"
    reason: str = ""
    created: list[str] = field(default_factory=list)
    done: bool = False  # the Story was Done, so the new Subtasks were rolled to Done
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "action": self.action,
            "reason": self.reason,
            "created": self.created,
            "rolled_to_done": self.done,
            "warnings": self.warnings,
        }


@dataclass
class AddSubtasksResult:
    dry_run: bool
    stories: list[StoryOutcome] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"dry_run": self.dry_run, "stories": [s.as_dict() for s in self.stories]}


class AddSubtasksAborted(Exception):
    """A Jira call failed part-way. `result` says exactly what was created before it, so a
    partial application is never silent (and a re-run skips the Story, so fix it by hand)."""

    def __init__(self, result: AddSubtasksResult, cause: JiraError) -> None:
        self.result = result
        self.cause = cause
        super().__init__(str(cause))


def _plan_warnings(summaries: list[str]) -> list[str]:
    out = []
    if len(summaries) < MIN_SUBTASKS:
        out.append("one Subtask means the Story is really a Subtask")
    if len(summaries) > MAX_SUBTASKS:
        out.append(f"{len(summaries)} Subtasks: split the Story")
    if not any(s.lower().startswith("test") for s in summaries):
        out.append("no 'Tests: ...' Subtask, so 'done' has no proof")
    return out


def add_subtasks(
    client: JiraClient,
    plan: dict[str, list[str]],
    *,
    dry_run: bool = False,
    assignee_account_id: str | None = None,
) -> AddSubtasksResult:
    """Add the planned Subtasks under each Story that has none. Idempotent: a Story that already
    has Subtasks is skipped. A Story that is Done gets its new Subtasks transitioned to Done too.
    Plan problems (empty summaries and the like) raise `PlanError` before anything is created."""
    plan = validate_plan(plan)
    result = AddSubtasksResult(dry_run=dry_run)
    subtask_type = SUBTASK_TYPE_NAMES[0]
    try:
        for key, summaries in plan.items():
            try:
                story = client.get_hierarchy_issue(key)
            except JiraError as exc:
                if exc.status_code in (400, 404):
                    result.stories.append(StoryOutcome(key, "skipped", f"not found: {exc}"))
                    continue
                raise
            level = hierarchy_level(story)
            if level in (_EPIC, _SUBTASK):
                result.stories.append(StoryOutcome(key, "skipped", "not a Story-level issue"))
                continue
            if story.subtask_count:
                result.stories.append(
                    StoryOutcome(key, "skipped", f"already has {story.subtask_count} subtask(s)")
                )
                continue
            outcome = StoryOutcome(
                key, "created", done=story.is_done, warnings=_plan_warnings(summaries)
            )
            project = key.rsplit("-", 1)[0]
            for summary in summaries:
                if dry_run:
                    outcome.created.append(summary)
                    continue
                created, subtask_type = _create_subtask(
                    client, project, key, summary, subtask_type, assignee_account_id
                )
                if story.is_done:
                    client.transition_issue(created, DONE)
                outcome.created.append(f"{created} {summary}")
            result.stories.append(outcome)
    except JiraError as exc:
        raise AddSubtasksAborted(result, exc) from exc
    return result


def _create_subtask(
    client: JiraClient,
    project: str,
    parent: str,
    summary: str,
    subtask_type: str,
    assignee_account_id: str | None,
) -> tuple[str, str]:
    """Create one Subtask; if the project spells the type differently, try the other name once.
    Returns (key, the type name that worked)."""
    names = [subtask_type] + [n for n in SUBTASK_TYPE_NAMES if n != subtask_type]
    last: JiraError | None = None
    for name in names:
        try:
            created = client.create_issue(
                project,
                summary,
                issue_type=name,
                parent=parent,
                assignee_account_id=assignee_account_id,
            )
            return created.key, name
        except JiraError as exc:
            if exc.status_code != 400:
                raise
            last = exc
    assert last is not None
    raise last
