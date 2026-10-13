"""Neutral plan -> Linear (ADLC-203, plan 0008 section 3).

| Layer | Linear |
|---|---|
| Category | ProjectMilestone |
| Task | Issue (in the project, under its milestone) |
| To-Do | sub-issue of the Task's issue |

Idempotent the same way the Jira sync is (`drift.py`): every object carries a trailing
`sd-id: <id>` line in its description, so a re-run finds what it made and updates in place
instead of creating a second copy. It never deletes. `dry_run` makes read queries only.
A new issue gets a workflow state from the plan status; an existing one keeps the state the
tracker has (the two-way rule, ADLC-204, decides when the tracker's state flows back).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from symphony_workflow.linear_client import LinearClient, LinearError
from symphony_workflow.plan import Plan, Status, assign_plan_ids, extract_marker, marker_line

# Linear workflow-state categories. There is no native "blocked" type: a team's own state called
# Blocked is used when it has one, else the work counts as started.
STATE_TYPE: dict[Status, str] = {
    "todo": "unstarted",
    "in_progress": "started",
    "done": "completed",
    "blocked": "started",
}
# `duplicate` and `canceled` are Linear state categories a team really has (seen live); the work is
# closed, so it counts as done.
_REVERSE: dict[str, Status] = {
    "unstarted": "todo",
    "backlog": "todo",
    "started": "in_progress",
    "completed": "done",
    "canceled": "done",
    "duplicate": "done",
}


def pick_state_id(status: Status, states: list[dict[str, Any]]) -> str | None:
    """The workflow state a new issue with this plan status should get, or None."""
    if status == "blocked":
        named = next((s for s in states if s.get("name", "").strip().lower() == "blocked"), None)
        if named:
            return named["id"]
    wanted = STATE_TYPE[status]
    matching = [s for s in states if s.get("type") == wanted]
    if not matching:
        return None
    preferred = {"unstarted": "todo", "started": "in progress", "completed": "done"}.get(wanted)
    exact = next((s for s in matching if s.get("name", "").strip().lower() == preferred), None)
    return (exact or matching[0])["id"]


def status_from_state(state_type: str | None) -> Status:
    """Linear state category -> plan status (the pull direction, used by ADLC-204)."""
    return _REVERSE.get(state_type or "", "todo")


@dataclass
class LinearChange:
    kind: Literal["milestone", "issue", "sub-issue"]
    id: str
    title: str
    action: Literal["create", "update", "unchanged"]

    def as_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "id": self.id, "title": self.title, "action": self.action}


def _description(body: str, entry_id: str, criteria: list[str] | None = None) -> str:
    parts = [body.strip()] if body.strip() else []
    if criteria:
        parts.append("Acceptance criteria:\n" + "\n".join(f"- [ ] {c}" for c in criteria))
    parts.append(marker_line(entry_id))
    return "\n\n".join(parts)


def _same(a: str | None, b: str) -> bool:
    return (a or "").strip().replace("\r\n", "\n") == b.strip()


def sync_plan_to_linear(
    plan: Plan, client: LinearClient, *, project_name: str, dry_run: bool = False
) -> list[LinearChange]:
    """Create or update the plan in Linear. Raises `LinearError` on any API failure."""
    assign_plan_ids(plan)
    team_id = client.resolve_team_id()
    project = client.find_project(project_name)
    if project is None and not dry_run:
        project = client.create_project(project_name, team_id)
    project_id = project["id"] if project else None
    milestones = client.list_project_milestones(project_id) if project_id else []
    issues = client.list_project_issues(project_id) if project_id else []
    by_milestone = {m_id: m for m in milestones if (m_id := extract_marker(m.get("description")))}
    by_issue = {i_id: i for i in issues if (i_id := extract_marker(i.get("description")))}
    states = client.team_workflow_states(team_id)

    changes: list[LinearChange] = []
    for category in plan.categories:
        cid = category.id or category.title
        want = _description(category.description, cid)
        found = by_milestone.get(cid)
        milestone_id: str | None = found["id"] if found else None
        if found is None:
            changes.append(LinearChange("milestone", cid, category.title, "create"))
            if not dry_run and project_id:
                milestone_id = client.create_project_milestone(project_id, category.title, want)[
                    "id"
                ]
        elif found.get("name") != category.title or not _same(found.get("description"), want):
            changes.append(LinearChange("milestone", cid, category.title, "update"))
            if not dry_run:
                client.update_project_milestone(found["id"], category.title, want)
        else:
            changes.append(LinearChange("milestone", cid, category.title, "unchanged"))

        for task in category.tasks:
            tid = task.id or task.title
            task_issue_id = _upsert_issue(
                client, changes, by_issue, kind="issue", entry_id=tid, title=task.title,
                description=_description(task.description, tid, task.acceptance_criteria),
                status=task.status, create_args={"team_id": team_id, "project_id": project_id,
                                                 "milestone_id": milestone_id},
                states=states, dry_run=dry_run,
            )  # fmt: skip
            for todo in task.todos:
                did = todo.id or todo.title
                _upsert_issue(
                    client, changes, by_issue, kind="sub-issue", entry_id=did, title=todo.title,
                    description=_description(todo.description, did), status=todo.status,
                    create_args={"team_id": team_id, "project_id": project_id,
                                 "parent_id": task_issue_id},
                    states=states, dry_run=dry_run,
                )  # fmt: skip
    return changes


def _upsert_issue(
    client: LinearClient,
    changes: list[LinearChange],
    existing: dict[str, dict[str, Any]],
    *,
    kind: Literal["issue", "sub-issue"],
    entry_id: str,
    title: str,
    description: str,
    status: Status,
    create_args: dict[str, Any],
    states: list[dict[str, Any]],
    dry_run: bool,
) -> str | None:
    """Returns the Linear id of the issue (None when it would only be created in a dry run)."""
    found = existing.get(entry_id)
    if found is None:
        changes.append(LinearChange(kind, entry_id, title, "create"))
        if dry_run:
            return None
        if kind == "sub-issue" and not create_args.get("parent_id"):
            raise LinearError(f"no parent issue to attach sub-issue {entry_id!r} to")
        created = client.create_plan_issue(
            title=title, description=description, state_id=pick_state_id(status, states),
            **create_args,
        )  # fmt: skip
        existing[entry_id] = {"id": created["id"]}
        return created["id"]
    if found.get("title") != title or not _same(found.get("description"), description):
        changes.append(LinearChange(kind, entry_id, title, "update"))
        if not dry_run:
            client.update_plan_issue(found["id"], title=title, description=description)
    else:
        changes.append(LinearChange(kind, entry_id, title, "unchanged"))
    return found["id"]
