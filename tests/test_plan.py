"""Unit tests for the neutral three-layer plan (plan.py, ADLC-198)."""

from __future__ import annotations

from symphony_workflow.decompose import Decomposition, Story
from symphony_workflow.decompose import Task as DecomposeTask
from symphony_workflow.plan import (
    Category,
    Plan,
    Task,
    Todo,
    assign_plan_ids,
    check_plan,
    extract_marker,
    from_decomposition,
    marker_line,
    rollup_status,
)


def _task(title: str, n_todos: int, *, last: str = "Tests: unit") -> Task:
    todos = [Todo(title=f"Do {i}") for i in range(n_todos - 1)] + (
        [Todo(title=last)] if n_todos else []
    )
    return Task(title=title, todos=todos)


def test_assign_ids_fills_gaps_by_position_and_never_touches_existing() -> None:
    plan = Plan(
        categories=[
            Category(
                title="A",
                tasks=[Task(id="keep", title="x", todos=[Todo(title="t")]), Task(title="y")],
            ),
            Category(id="mine", title="B", tasks=[Task(title="z")]),
        ]
    )
    assert assign_plan_ids(plan) is True
    assert plan.categories[0].id == "c1"
    assert plan.categories[0].tasks[0].id == "keep"
    assert plan.categories[0].tasks[0].todos[0].id == "keep.d1"
    assert plan.categories[0].tasks[1].id == "c1.t2"
    assert plan.categories[1].id == "mine" and plan.categories[1].tasks[0].id == "mine.t1"
    assert assign_plan_ids(plan) is False  # second run changes nothing


def test_assign_ids_avoids_a_collision_with_an_existing_id() -> None:
    plan = Plan(categories=[Category(id="c2", title="first"), Category(title="second")])
    assign_plan_ids(plan)
    ids = [c.id for c in plan.categories]
    assert ids[0] == "c2" and ids[1] != "c2" and len(set(ids)) == 2


def test_counts_describe_the_pyramid_top_to_bottom() -> None:
    plan = Plan(categories=[Category(title="A", tasks=[_task("x", 3), _task("y", 2)])])
    assert plan.counts() == (1, 2, 5)


def test_a_healthy_pyramid_has_no_findings() -> None:
    plan = Plan(
        categories=[Category(title="A", tasks=[_task("x", 4), _task("y", 3), _task("z", 5)])]
    )
    assert check_plan(plan) == []


def test_guardrails_warn_on_every_wrong_shape() -> None:
    plan = Plan(
        categories=[
            Category(id="lonely", title="One", tasks=[_task("a", 3)]),
            Category(id="big", title="Big", tasks=[_task(f"t{i}", 3) for i in range(9)]),
            Category(
                id="mixed",
                title="Mixed",
                tasks=[
                    Task(id="empty", title="e"),
                    _task("one", 1),
                    _task("many", 7),
                    _task("huge", 9),
                    _task("notests", 3, last="Ship it"),
                ],
            ),
        ]
    )
    rules = {(f.where, f.rule) for f in check_plan(plan)}
    assert ("lonely", "one_task") in rules
    assert ("big", "too_many_tasks") in rules
    assert ("empty", "no_todos") in rules
    assert ("one", "one_todo") in rules
    assert ("many", "many_todos") in rules
    assert ("huge", "too_many_todos") in rules
    assert ("notests", "no_tests_todo") in rules


def test_from_decomposition_keeps_existing_ids_so_cards_keep_their_identity() -> None:
    decomposition = Decomposition(
        epic_key="PROJ-1",
        definition_of_done=[],
        stories=[
            Story(
                id="s1",
                title="Login",
                user_story="As a user I sign in",
                acceptance_criteria=["It works"],
                tasks=[
                    DecomposeTask(id="s1.t1", title="Build", description="b"),
                    DecomposeTask(title="Tests: unit", description="t"),
                ],
            )
        ],
    )
    plan = from_decomposition(decomposition)
    task = plan.categories[0].tasks[0]
    assert plan.categories[0].title == "PROJ-1" and plan.categories[0].id == "c1"
    assert task.id == "s1" and task.description == "As a user I sign in"
    assert task.acceptance_criteria == ["It works"]
    assert [t.id for t in task.todos] == ["s1.t1", "s1.d2"]


def test_rollup_status_from_children() -> None:
    assert rollup_status([]) == "todo"
    assert rollup_status(["done", "done"]) == "done"
    assert rollup_status(["done", "todo"]) == "in_progress"
    assert rollup_status(["todo", "todo"]) == "todo"
    assert rollup_status(["done", "blocked"]) == "blocked"


def test_marker_helpers_are_the_ones_every_sink_shares() -> None:
    assert extract_marker(f"text\n\n{marker_line('c1.t2')}") == "c1.t2"
    assert extract_marker("no marker") is None


def test_no_tests_warning_is_for_generated_to_dos_not_an_authors_own_breakdown() -> None:
    def task(origin: str) -> Task:
        todos = [Todo(title="Design it"), Todo(title="Build it"), Todo(title="Ship it")]
        return Task(id=f"t-{origin or 'none'}", title="T", todos=todos, todo_origin=origin)

    plan = Plan(
        categories=[
            Category(
                id="c",
                title="C",
                tasks=[task(o) for o in ("stories", "phases", "checklist", "proposal", "")],
            )
        ]
    )
    flagged = {f.where for f in check_plan(plan) if f.rule == "no_tests_todo"}
    assert flagged == {"t-proposal", "t-none"}  # the author's own three are left alone
