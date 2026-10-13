"""Neutral plan -> Linear (ADLC-203), against an in-memory fake that keeps state, so a second run
really sees what the first one made."""

from __future__ import annotations

import pytest

from symphony_workflow.linear_client import LinearError
from symphony_workflow.linear_sink import (
    pick_state_id,
    status_from_state,
    sync_plan_to_linear,
)
from symphony_workflow.plan import Category, Plan, Task, Todo, extract_marker

STATES = [
    {"id": "s-backlog", "name": "Backlog", "type": "backlog"},
    {"id": "s-todo", "name": "Todo", "type": "unstarted"},
    {"id": "s-prog", "name": "In Progress", "type": "started"},
    {"id": "s-done", "name": "Done", "type": "completed"},
]


class FakeLinear:
    """Just the methods the sink uses; every write is recorded and stored."""

    def __init__(self, *, with_project: bool = True, states=STATES) -> None:
        self.project = {"id": "p1", "name": "Proj"} if with_project else None
        self.milestones: list[dict] = []
        self.issues: list[dict] = []
        self.states = states
        self.calls: list[str] = []

    def resolve_team_id(self) -> str:
        return "team-1"

    def find_project(self, name):
        return self.project

    def create_project(self, name, team_id):
        self.calls.append("create_project")
        self.project = {"id": "p1", "name": name}
        return self.project

    def list_project_milestones(self, project_id):
        return list(self.milestones)

    def list_project_issues(self, project_id):
        return list(self.issues)

    def team_workflow_states(self, team_id):
        return self.states

    def create_project_milestone(self, project_id, name, description):
        self.calls.append("create_milestone")
        node = {"id": f"m{len(self.milestones) + 1}", "name": name, "description": description}
        self.milestones.append(node)
        return node

    def update_project_milestone(self, milestone_id, name, description):
        self.calls.append("update_milestone")
        node = next(m for m in self.milestones if m["id"] == milestone_id)
        node.update(name=name, description=description)

    def create_plan_issue(self, *, team_id, title, description, project_id=None,
                          milestone_id=None, parent_id=None, state_id=None):  # fmt: skip
        self.calls.append("create_issue")
        node = {"id": f"i{len(self.issues) + 1}", "title": title, "description": description,
                "parent": {"id": parent_id} if parent_id else None, "state_id": state_id,
                "milestone_id": milestone_id}  # fmt: skip
        self.issues.append(node)
        return {"id": node["id"], "identifier": f"GAI-{len(self.issues)}"}

    def update_plan_issue(self, issue_id, *, title, description):
        self.calls.append("update_issue")
        node = next(i for i in self.issues if i["id"] == issue_id)
        node.update(title=title, description=description)


def _plan() -> Plan:
    tasks = [
        Task(id="c1.t1", title="Sign in", description="Let users in", acceptance_criteria=["a"],
             status="in_progress",
             todos=[Todo(id="c1.t1.d1", title="Build"), Todo(id="c1.t1.d2", title="Tests: it")]),
        Task(id="c1.t2", title="Sign out", todos=[Todo(id="c1.t2.d1", title="Build")]),
    ]  # fmt: skip
    return Plan(name="Proj", categories=[Category(id="c1", title="Auth", tasks=tasks)])


def test_first_run_creates_the_pyramid_with_markers_and_parents() -> None:
    fake = FakeLinear()
    changes = sync_plan_to_linear(_plan(), fake, project_name="Proj")
    assert [(c.kind, c.action) for c in changes] == (
        [("milestone", "create")]
        + [("issue", "create"), ("sub-issue", "create"), ("sub-issue", "create")]
        + [("issue", "create"), ("sub-issue", "create")]
    )
    ids = [extract_marker(i["description"]) for i in fake.issues]
    assert ids == ["c1.t1", "c1.t1.d1", "c1.t1.d2", "c1.t2", "c1.t2.d1"]
    assert extract_marker(fake.milestones[0]["description"]) == "c1"
    task_issue = fake.issues[0]
    assert task_issue["milestone_id"] == "m1" and "- [ ] a" in task_issue["description"]
    assert fake.issues[1]["parent"] == {"id": task_issue["id"]}  # a to-do hangs off its task


def test_a_second_run_changes_nothing() -> None:
    fake = FakeLinear()
    sync_plan_to_linear(_plan(), fake, project_name="Proj")
    fake.calls.clear()
    changes = sync_plan_to_linear(_plan(), fake, project_name="Proj")
    assert {c.action for c in changes} == {"unchanged"} and fake.calls == []


def test_a_renamed_task_is_updated_in_place_not_duplicated() -> None:
    fake = FakeLinear()
    sync_plan_to_linear(_plan(), fake, project_name="Proj")
    plan = _plan()
    plan.categories[0].tasks[0].title = "Log in"
    fake.calls.clear()
    changes = sync_plan_to_linear(plan, fake, project_name="Proj")
    assert [c.id for c in changes if c.action == "update"] == ["c1.t1"]
    assert fake.calls == ["update_issue"] and len(fake.issues) == 5


def test_an_existing_issue_keeps_the_state_the_tracker_has() -> None:
    fake = FakeLinear()
    sync_plan_to_linear(_plan(), fake, project_name="Proj")
    fake.issues[0]["state_id"] = "s-done"  # someone finished it in Linear
    plan = _plan()
    plan.categories[0].tasks[0].title = "Log in"
    sync_plan_to_linear(plan, fake, project_name="Proj")
    assert fake.issues[0]["state_id"] == "s-done"


def test_new_issues_get_a_state_from_the_plan_status() -> None:
    fake = FakeLinear()
    sync_plan_to_linear(_plan(), fake, project_name="Proj")
    states = [i["state_id"] for i in fake.issues]
    assert states == ["s-prog", "s-todo", "s-todo", "s-todo", "s-todo"]


def test_dry_run_reads_only_even_when_the_project_is_missing() -> None:
    fake = FakeLinear(with_project=False)
    changes = sync_plan_to_linear(_plan(), fake, project_name="Proj", dry_run=True)
    assert fake.calls == [] and fake.project is None and not fake.issues
    assert {c.action for c in changes} == {"create"} and len(changes) == 6


def test_a_missing_project_is_created_on_a_real_run() -> None:
    fake = FakeLinear(with_project=False)
    sync_plan_to_linear(_plan(), fake, project_name="Brand new")
    assert fake.calls[0] == "create_project" and fake.project["name"] == "Brand new"


def test_nothing_in_linear_is_ever_deleted() -> None:
    fake = FakeLinear()
    sync_plan_to_linear(_plan(), fake, project_name="Proj")
    smaller = _plan()
    smaller.categories[0].tasks.pop()  # the task disappears from the plan
    sync_plan_to_linear(smaller, fake, project_name="Proj")
    assert len(fake.issues) == 5 and not [c for c in fake.calls if "delete" in c]


def test_a_hand_made_issue_without_a_marker_is_ignored() -> None:
    fake = FakeLinear()
    fake.issues.append({"id": "x", "title": "Sign in", "description": "no marker"})
    sync_plan_to_linear(_plan(), fake, project_name="Proj")
    assert len(fake.issues) == 6 and fake.issues[0]["description"] == "no marker"


def test_an_api_failure_propagates_as_linear_error() -> None:
    fake = FakeLinear()

    def boom(*a, **k):
        raise LinearError("rate limited")

    fake.create_project_milestone = boom
    with pytest.raises(LinearError, match="rate limited"):
        sync_plan_to_linear(_plan(), fake, project_name="Proj")


@pytest.mark.parametrize(
    ("status", "expected"),
    [("todo", "s-todo"), ("in_progress", "s-prog"), ("done", "s-done"), ("blocked", "s-prog")],
)
def test_state_mapping(status: str, expected: str) -> None:
    assert pick_state_id(status, STATES) == expected


def test_blocked_uses_a_team_state_called_blocked_when_there_is_one() -> None:
    states = [*STATES, {"id": "s-blocked", "name": "Blocked", "type": "started"}]
    assert pick_state_id("blocked", states) == "s-blocked"


def test_no_matching_state_means_none() -> None:
    assert pick_state_id("done", [{"id": "a", "name": "Todo", "type": "unstarted"}]) is None


@pytest.mark.parametrize(
    ("state_type", "status"),
    [("unstarted", "todo"), ("backlog", "todo"), ("started", "in_progress"),
     ("completed", "done"), ("canceled", "done"), ("duplicate", "done"),
     (None, "todo"), ("weird", "todo")],
)  # fmt: skip
def test_reverse_mapping(state_type, status) -> None:
    assert status_from_state(state_type) == status
