"""Layer-3 To-Do sources (ADLC-201): own breakdown, approved proposal, five steps, guardrail."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

import symphony_workflow.agent_runner as agent_runner
from symphony_workflow.plan import (
    MAX_TODOS,
    MIN_TODOS,
    STEP_TITLES,
    Category,
    Plan,
    Task,
    Todo,
    assign_plan_ids,
    check_plan,
)
from symphony_workflow.safepath import UnsafePathError
from symphony_workflow.todo_source import (
    PROPOSAL_JSON,
    TESTS_TODO,
    Proposal,
    approve_proposal,
    fill_todos,
    load_proposal,
    normalise_todos,
    propose,
    save_proposal,
    split_with_agent,
)


def _plan(*, own: bool = True) -> Plan:
    own_task = Task(
        title="Has a breakdown",
        todos=[Todo(title="Write it"), Todo(title="Tests: it")] if own else [],
    )
    bare = Task(title="Bare", description="d", acceptance_criteria=["a", "b"])
    plan = Plan(categories=[Category(title="One", tasks=[own_task, bare])])
    assign_plan_ids(plan)
    return plan


def test_five_steps_fill_only_tasks_without_a_breakdown() -> None:
    plan = _plan()
    origins = fill_todos(plan, "steps")
    own, bare = plan.categories[0].tasks
    assert [t.title for t in own.todos] == ["Write it", "Tests: it"]  # the author's list is kept
    assert [t.title for t in bare.todos] == list(STEP_TITLES)
    assert origins == {bare.id: "steps"}


def test_the_five_steps_do_not_trip_the_last_is_tests_warning() -> None:
    plan = _plan()
    fill_todos(plan, "steps")
    assert not [f for f in check_plan(plan) if f.rule == "no_tests_todo"]


def test_breakdown_only_leaves_empty_tasks_alone() -> None:
    plan = _plan()
    assert fill_todos(plan, "breakdown") == {}
    assert plan.categories[0].tasks[1].todos == []
    assert [f.rule for f in check_plan(plan)] == ["no_todos"]


def test_an_unapproved_proposal_is_never_used() -> None:
    plan = _plan()
    bare_id = plan.categories[0].tasks[1].id
    proposal = Proposal(approved=False, tasks={bare_id: ["Do x", "Tests: x"]})
    assert fill_todos(plan, "ai-split", proposal) == {}
    assert plan.categories[0].tasks[1].todos == []


def test_an_approved_proposal_beats_the_five_steps_in_hybrid() -> None:
    plan = _plan()
    bare_id = plan.categories[0].tasks[1].id
    proposal = Proposal(approved=True, tasks={bare_id: ["Do x", "Do y", "Tests: x"]})
    assert fill_todos(plan, "hybrid", proposal) == {bare_id: "proposal"}
    assert [t.title for t in plan.categories[0].tasks[1].todos] == ["Do x", "Do y", "Tests: x"]


def test_hybrid_falls_back_to_steps_for_a_task_the_proposal_skips() -> None:
    plan = _plan()
    proposal = Proposal(approved=True, tasks={"somebody-else": ["a", "b"]})
    origins = fill_todos(plan, "hybrid", proposal)
    assert list(origins.values()) == ["steps"]


@pytest.mark.parametrize(
    ("raw", "expected_len"),
    [
        (["Tests: only"], MIN_TODOS),  # a lone item gets a sibling
        ([], MIN_TODOS),
        (["a", "b", "c", "d", "e", "f", "g", "h", "Tests: x"], MAX_TODOS),  # clipped
        (["  ", "", "a", "Tests: x"], 2),  # blanks dropped
    ],
)
def test_normalise_keeps_every_source_inside_the_guardrail(
    raw: list[str], expected_len: int
) -> None:
    out = normalise_todos(raw)
    assert len(out) == expected_len and MIN_TODOS <= len(out) <= MAX_TODOS
    assert "test" in out[-1].lower()


def test_normalise_adds_a_tests_todo_when_the_agent_forgot_it() -> None:
    assert normalise_todos(["Build", "Wire"])[-1] == TESTS_TODO


def test_proposal_round_trip_and_approval(tmp_path: Path) -> None:
    plan = _plan()
    bare = plan.categories[0].tasks[1]
    proposal = Proposal(tasks={bare.id: ["Build", "Tests: build"]})
    json_path, md_path = save_proposal(tmp_path, proposal, plan)
    assert json_path.is_file() and "pending review" in md_path.read_text()
    assert load_proposal(tmp_path) == proposal  # saved unapproved

    approved = approve_proposal(tmp_path, plan)
    assert approved.approved and load_proposal(tmp_path).approved
    assert "approved" in md_path.read_text()


def test_approval_renormalises_what_a_human_edited(tmp_path: Path) -> None:
    plan = _plan()
    bare = plan.categories[0].tasks[1]
    save_proposal(tmp_path, Proposal(tasks={bare.id: ["Build"]}), plan)
    assert approve_proposal(tmp_path, plan).tasks[bare.id] == ["Build", TESTS_TODO]


def test_approving_without_a_proposal_says_so(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="split-todos"):
        approve_proposal(tmp_path, _plan())


def test_a_broken_proposal_file_is_reported_not_trusted(tmp_path: Path) -> None:
    (tmp_path / "docs/context").mkdir(parents=True)
    (tmp_path / PROPOSAL_JSON).write_text("{nope")
    with pytest.raises(ValueError, match="not a valid To-Do proposal"):
        load_proposal(tmp_path)


def test_a_symlinked_proposal_is_refused(tmp_path: Path) -> None:
    outside = tmp_path / "outside.json"
    outside.write_text('{"approved": true, "tasks": {}}')
    root = tmp_path / "proj"
    (root / "docs/context").mkdir(parents=True)
    (root / PROPOSAL_JSON).symlink_to(outside)
    with pytest.raises(UnsafePathError):
        load_proposal(root)


def test_propose_covers_only_tasks_without_to_dos() -> None:
    seen: list[str] = []

    async def splitter(task: Task) -> list[str]:
        seen.append(task.title)
        return ["Build it", "Tests: it"]

    plan = _plan()
    proposal = asyncio.run(propose(plan, splitter))
    assert seen == ["Bare"] and not proposal.approved
    assert list(proposal.tasks.values()) == [["Build it", "Tests: it"]]


def test_the_agent_path_uses_a_plan_phase_run_and_the_split_schema(monkeypatch) -> None:
    calls: dict = {}

    async def fake_run_plan(prompt, **kwargs):
        calls.update(prompt=prompt, **kwargs)
        return SimpleNamespace(structured={"todos": ["Build", "Tests: build"]})

    monkeypatch.setattr(agent_runner, "run_plan", fake_run_plan)
    task = Task(title="Sign in", acceptance_criteria=["a user can sign in"])
    assert asyncio.run(split_with_agent(task, cwd="/work")) == ["Build", "Tests: build"]
    assert calls["output_schema"]["required"] == ["todos"] and calls["cwd"] == "/work"
    assert "Sign in" in calls["prompt"] and "a user can sign in" in calls["prompt"]


def test_an_agent_answer_without_todos_is_an_error(monkeypatch) -> None:
    async def fake_run_plan(prompt, **kwargs):
        return SimpleNamespace(structured=None)

    monkeypatch.setattr(agent_runner, "run_plan", fake_run_plan)
    with pytest.raises(ValueError, match="no `todos` list"):
        asyncio.run(split_with_agent(Task(id="t1", title="x"), cwd="."))


def test_a_tests_item_that_does_not_start_with_tests_is_not_duplicated() -> None:
    # a real agent run produced "Write and run tests ..." in the middle of its list
    raw = ["Scope it", "Write and run tests for the change", "Wire the API", "Ship it"]
    out = normalise_todos(raw)
    assert out[-1] == "Write and run tests for the change" and len(out) == 4
    assert sum("test" in t.lower() for t in out) == 1


def test_a_word_that_merely_contains_test_is_not_a_tests_item() -> None:
    out = normalise_todos(["Update the latest release notes", "Wire it up"])
    assert out[-1] == TESTS_TODO and "Update the latest release notes" in out


def test_hybrid_waits_for_a_pending_proposal_instead_of_writing_the_steps() -> None:
    # sync-plan before --approve must not bake the five steps in (they'd read as the author's own)
    plan = _plan()
    bare_id = plan.categories[0].tasks[1].id
    pending = Proposal(approved=False, tasks={bare_id: ["Do x", "Tests: x"]})
    assert fill_todos(plan, "hybrid", pending) == {}
    assert plan.categories[0].tasks[1].todos == []
    # a pending proposal that does not cover this Task changes nothing
    other = Proposal(approved=False, tasks={"someone-else": ["a", "b"]})
    assert list(fill_todos(_plan(), "hybrid", other).values()) == ["steps"]
