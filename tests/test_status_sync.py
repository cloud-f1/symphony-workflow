"""Two-way status sync (ADLC-204): the three-way rule, both directions, conflicts, orphans."""

from __future__ import annotations

import pytest

from symphony_workflow.plan import Category, Plan, Task, Todo
from symphony_workflow.safepath import UnsafePathError
from symphony_workflow.status_sync import (
    STATE_FILE,
    SyncState,
    decide,
    load_state,
    remote_status,
    save_state,
    sync_status,
)

STATES = [
    {"id": "s-todo", "name": "Todo", "type": "unstarted"},
    {"id": "s-prog", "name": "In Progress", "type": "started"},
    {"id": "s-done", "name": "Done", "type": "completed"},
    {"id": "s-blocked", "name": "Blocked", "type": "started"},
]
BY_ID = {s["id"]: s for s in STATES}
BY_TYPE = {"todo": STATES[0], "in_progress": STATES[1], "done": STATES[2]}


class FakeLinear:
    def __init__(self, cards: dict[str, str]) -> None:
        """`cards` maps an entry id to the plan status its card is in."""
        self.cards = [
            {"id": f"card-{k}", "description": f"x\n\nsd-id: {k}", "state": BY_TYPE[v]}
            for k, v in cards.items()
        ]
        self.moves: list[tuple[str, str]] = []

    def find_project(self, name):
        return {"id": "p1", "name": name}

    def list_project_issues(self, project_id):
        return self.cards

    def resolve_team_id(self):
        return "team"

    def team_workflow_states(self, team_id):
        return STATES

    def set_issue_state(self, issue_id, state_id):
        self.moves.append((issue_id, state_id))
        card = next(c for c in self.cards if c["id"] == issue_id)
        card["state"] = BY_ID[state_id]


def _plan(*statuses: str) -> Plan:
    todos = [Todo(id=f"c1.t1.d{i}", title=f"d{i}", status=s) for i, s in enumerate(statuses, 1)]
    task = Task(id="c1.t1", title="T", todos=todos)
    return Plan(categories=[Category(id="c1", title="C", tasks=[task])])


def _cards(**kw: str) -> dict[str, str]:
    return {k.replace("_", "."): v for k, v in kw.items()}


@pytest.mark.parametrize(
    ("mine", "theirs", "was", "expected"),
    [
        ("todo", "todo", "todo", "unchanged"),
        ("done", "done", "todo", "unchanged"),  # both got there
        ("todo", "done", "todo", "pull"),
        ("done", "todo", "todo", "push"),
        ("done", "in_progress", "todo", "conflict"),
    ],
)
def test_the_three_way_rule(mine, theirs, was, expected) -> None:
    assert decide(mine, theirs, was) == expected


def test_a_change_in_linear_is_pulled_into_the_plan() -> None:
    plan, fake = _plan("todo", "todo"), FakeLinear(_cards(c1_t1_d1="done", c1_t1_d2="todo"))
    report = sync_status(plan, fake, project_name="P", base=SyncState())
    assert [t.status for t in plan.categories[0].tasks[0].todos] == ["done", "todo"]
    assert report.new_state.status["c1.t1.d1"] == "done" and not fake.moves


def test_a_ticked_to_do_moves_its_card() -> None:
    plan, fake = _plan("done", "todo"), FakeLinear(_cards(c1_t1_d1="todo", c1_t1_d2="todo"))
    report = sync_status(plan, fake, project_name="P", base=SyncState())
    assert fake.moves[0] == ("card-c1.t1.d1", "s-done")
    assert report.new_state.status["c1.t1.d1"] == "done"


def test_a_change_on_both_sides_is_a_conflict_and_nothing_moves() -> None:
    plan, fake = _plan("done"), FakeLinear(_cards(c1_t1_d1="in_progress"))
    report = sync_status(plan, fake, project_name="P", base=SyncState())
    assert [c.action for c in report.conflicts] == ["conflict"]
    assert fake.moves == [] and plan.categories[0].tasks[0].todos[0].status == "done"
    assert "c1.t1.d1" not in report.new_state.status  # the base stays as it was


def test_dry_run_moves_nothing_but_reports_the_push() -> None:
    plan, fake = _plan("done"), FakeLinear(_cards(c1_t1_d1="todo"))
    report = sync_status(plan, fake, project_name="P", base=SyncState(), dry_run=True)
    assert fake.moves == [] and [c.action for c in report.changes][0] == "push"


def test_direction_push_leaves_a_pull_alone_and_the_reverse() -> None:
    plan, fake = _plan("todo"), FakeLinear(_cards(c1_t1_d1="done"))
    only_push = sync_status(plan, fake, project_name="P", base=SyncState(), direction="push")
    assert only_push.changes[0].action == "skipped"
    assert plan.categories[0].tasks[0].todos[0].status == "todo"
    plan2, fake2 = _plan("done"), FakeLinear(_cards(c1_t1_d1="todo"))
    only_pull = sync_status(plan2, fake2, project_name="P", base=SyncState(), direction="pull")
    assert only_pull.changes[0].action == "skipped" and fake2.moves == []


def test_a_second_run_after_a_sync_is_quiet() -> None:
    plan, fake = _plan("done"), FakeLinear(_cards(c1_t1_d1="todo"))
    first = sync_status(plan, fake, project_name="P", base=SyncState())
    again = sync_status(plan, fake, project_name="P", base=first.new_state)
    assert {c.action for c in again.changes if c.kind == "to-do"} == {"unchanged"}


def test_a_to_do_without_a_card_is_an_orphan_and_nothing_is_created() -> None:
    report = sync_status(_plan("todo"), FakeLinear({}), project_name="P", base=SyncState())
    assert [c.action for c in report.changes] == ["orphan"]


def test_a_task_follows_its_to_dos_forward_only() -> None:
    plan = _plan("done", "done")
    fake = FakeLinear(_cards(c1_t1="todo", c1_t1_d1="done", c1_t1_d2="done"))
    sync_status(plan, fake, project_name="P", base=SyncState())
    assert ("card-c1.t1", "s-done") in fake.moves
    # a Task someone already closed is never moved back by an unfinished roll-up
    plan2 = _plan("todo", "todo")
    fake2 = FakeLinear(_cards(c1_t1="done", c1_t1_d1="todo", c1_t1_d2="todo"))
    sync_status(plan2, fake2, project_name="P", base=SyncState())
    assert fake2.moves == []


def test_a_pull_does_not_touch_the_task_status() -> None:
    plan = _plan("todo", "todo")
    fake = FakeLinear(_cards(c1_t1="done", c1_t1_d1="todo", c1_t1_d2="todo"))
    sync_status(plan, fake, project_name="P", base=SyncState(), direction="pull")
    assert plan.categories[0].tasks[0].status == "todo" and fake.moves == []


@pytest.mark.parametrize(
    ("state", "expected"),
    [({"name": "Blocked", "type": "started"}, "blocked"),
     ({"name": "Review", "type": "started"}, "in_progress"),
     ({"name": "Shipped", "type": "completed"}, "done"), (None, "todo")],
)  # fmt: skip
def test_remote_status(state, expected) -> None:
    assert remote_status(state) == expected


def test_a_config_name_beats_the_state_category() -> None:
    assert remote_status({"name": "In Review", "type": "started"}, {"in review": "done"}) == "done"


def test_state_file_round_trip_and_bad_input(tmp_path) -> None:
    assert load_state(tmp_path) == SyncState()
    save_state(tmp_path, SyncState(status={"c1": "done"}))
    assert load_state(tmp_path).status == {"c1": "done"}
    (tmp_path / STATE_FILE).write_text("{nope")
    with pytest.raises(ValueError, match="not a valid sync state"):
        load_state(tmp_path)


def test_a_symlinked_state_file_is_refused(tmp_path) -> None:
    outside = tmp_path / "out.json"
    outside.write_text("{}")
    root = tmp_path / "proj"
    (root / "docs/context").mkdir(parents=True)
    (root / STATE_FILE).symlink_to(outside)
    with pytest.raises(UnsafePathError):
        load_state(root)


def test_a_task_is_not_pushed_while_one_of_its_to_dos_is_in_conflict() -> None:
    # d1 and d2 are done locally; d2 is also in progress in Linear (changed on both sides)
    plan = _plan("done", "done")
    fake = FakeLinear(_cards(c1_t1="in_progress", c1_t1_d1="done", c1_t1_d2="in_progress"))
    base = SyncState(status={"c1.t1.d1": "done"})
    report = sync_status(plan, fake, project_name="P", base=base)
    assert [c.id for c in report.conflicts] == ["c1.t1.d2"]
    assert fake.moves == []  # the Task stays where it is until the conflict is settled
