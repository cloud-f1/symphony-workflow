"""Two-way status sync between a plan and Linear (ADLC-204, plan 0008 section 6).

Content belongs to the plan (`linear_sink` pushes it); **status** belongs to the tracker. For every
To-Do the three sides are compared: the status at the last sync (`base`), the plan's status now,
and the tracker's status now.

| plan vs base | tracker vs base | result |
|---|---|---|
| same | same | nothing |
| same | changed | **pull**: the plan takes the tracker's status |
| changed | same | **push**: the tracker's card is moved |
| changed | changed, equal | nothing (both got there) |
| changed | changed, different | **conflict**: reported, nothing changes for that entry |

With no base yet, `todo` is assumed: a fresh entry nobody has touched counts as unchanged.
A To-Do or Task with no card is an **orphan**, reported, never created here (that is `sync-plan`).
Nothing is deleted and a card is never moved backwards by a Task roll-up.

Tasks are not compared three ways. Their status is the roll-up of their To-Dos, and it is pushed
only forward (todo, in progress, done): the progress matrix has five step columns, the tracker one
status, so writing a tracker status back into the matrix would invent which step finished.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from symphony_workflow.linear_client import LinearClient
from symphony_workflow.linear_sink import pick_state_id, status_from_state
from symphony_workflow.plan import Plan, Status, assign_plan_ids, extract_marker, rollup_status
from symphony_workflow.safepath import check_inside

STATE_FILE = "docs/context/sync-state.json"
Direction = Literal["both", "push", "pull"]
_RANK: dict[str, int] = {"todo": 0, "blocked": 1, "in_progress": 1, "done": 2}


class SyncState(BaseModel):
    """What the last sync left behind: status per entry id, no secrets."""

    status: dict[str, Status] = Field(default_factory=dict)


def load_state(root: Path) -> SyncState:
    path = root / STATE_FILE
    check_inside(root, path)
    if not path.is_file():
        return SyncState()
    try:
        return SyncState.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"{path} is not a valid sync state: {exc}") from exc


def save_state(root: Path, state: SyncState) -> Path:
    path = root / STATE_FILE
    check_inside(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(state.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def remote_status(
    state: dict[str, Any] | None, status_map: dict[str, Status] | None = None
) -> Status:
    """Linear state -> plan status: a name from the config, then a team state called Blocked,
    then the state category."""
    state = state or {}
    name = (state.get("name") or "").strip().lower()
    if status_map and name in status_map:
        return status_map[name]
    if name == "blocked":
        return "blocked"
    return status_from_state(state.get("type"))


@dataclass
class StatusChange:
    id: str
    kind: Literal["task", "to-do"]
    title: str
    action: Literal["unchanged", "pull", "push", "conflict", "orphan", "no_state", "skipped"]
    plan: Status
    tracker: Status | None
    base: Status

    def as_dict(self) -> dict[str, str | None]:
        return {
            "id": self.id,
            "kind": self.kind,
            "title": self.title,
            "action": self.action,
            "plan": self.plan,
            "tracker": self.tracker,
            "base": self.base,
        }


@dataclass
class StatusReport:
    changes: list[StatusChange]
    new_state: SyncState

    @property
    def conflicts(self) -> list[StatusChange]:
        return [c for c in self.changes if c.action == "conflict"]


def decide(
    mine: Status, theirs: Status, was: Status
) -> Literal["unchanged", "pull", "push", "conflict"]:
    """The three-way rule in the module docstring, as a pure function."""
    if mine == theirs:
        return "unchanged"
    if mine == was:
        return "pull"
    if theirs == was:
        return "push"
    return "conflict"


def sync_status(
    plan: Plan,
    client: LinearClient,
    *,
    project_name: str,
    base: SyncState,
    direction: Direction = "both",
    dry_run: bool = False,
    status_map: dict[str, Status] | None = None,
) -> StatusReport:
    """Compare and apply. A pull changes `plan` in memory (the caller writes the Markdown), a push
    moves the card now unless `dry_run`. Returns what happened and the state to save."""
    assign_plan_ids(plan)
    project = client.find_project(project_name)
    issues = client.list_project_issues(project["id"]) if project else []
    cards = {i_id: i for i in issues if (i_id := extract_marker(i.get("description")))}
    states = client.team_workflow_states(client.resolve_team_id()) if issues else []
    new_state = SyncState(status=dict(base.status))
    changes: list[StatusChange] = []

    def move(card: dict[str, Any], status: Status) -> bool:
        state_id = pick_state_id(status, states)
        if state_id is None:
            return False
        if not dry_run:
            client.set_issue_state(card["id"], state_id)
        return True

    def record(entry: str, kind: Literal["task", "to-do"], title: str, action: str,
               mine: Status, theirs: Status | None, was: Status) -> None:  # fmt: skip
        changes.append(StatusChange(entry, kind, title, action, mine, theirs, was))  # type: ignore[arg-type]

    for category in plan.categories:
        for task in category.tasks:
            for todo in task.todos:
                entry = todo.id or todo.title
                card = cards.get(entry)
                if card is None:
                    record(entry, "to-do", todo.title, "orphan", todo.status, None, "todo")
                    continue
                theirs = remote_status(card.get("state"), status_map)
                was = base.status.get(entry, "todo")
                action = decide(todo.status, theirs, was)
                if (action == "pull" and direction == "push") or (
                    action == "push" and direction == "pull"
                ):
                    record(entry, "to-do", todo.title, "skipped", todo.status, theirs, was)
                elif action == "pull":
                    record(entry, "to-do", todo.title, "pull", todo.status, theirs, was)
                    todo.status = theirs
                    new_state.status[entry] = theirs
                elif action == "push":
                    if move(card, todo.status):
                        record(entry, "to-do", todo.title, "push", todo.status, theirs, was)
                        new_state.status[entry] = todo.status
                    else:
                        record(entry, "to-do", todo.title, "no_state", todo.status, theirs, was)
                elif action == "conflict":
                    record(entry, "to-do", todo.title, "conflict", todo.status, theirs, was)
                else:
                    record(entry, "to-do", todo.title, "unchanged", todo.status, theirs, was)
                    new_state.status[entry] = todo.status

            card_task = cards.get(task.id or "")
            if card_task is None or not task.todos or direction == "pull":
                continue
            derived = rollup_status([t.status for t in task.todos])
            theirs = remote_status(card_task.get("state"), status_map)
            if _RANK[derived] > _RANK[theirs] and move(card_task, derived):
                record(task.id or task.title, "task", task.title, "push", derived, theirs, theirs)
    return StatusReport(changes=changes, new_state=new_state)
