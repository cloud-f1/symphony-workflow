"""Unit tests for the Run state machine — the enforced human-review gate
between Plan and Implement (docs/spec.md). Pure in-memory, no I/O.
"""

from __future__ import annotations

import pytest

from jira_agent_cli.runs import Run, RunState, RunStore, RunTransitionError


def test_create_starts_in_created_state() -> None:
    store = RunStore()
    run = store.create("ADLC-1")
    assert run.state == RunState.CREATED
    assert run.issue_key == "ADLC-1"


def test_load_registers_an_already_built_run() -> None:
    # Simulates cli.py's `implement` rehydrating a Run a prior, separate
    # `jira-agent plan` process saved to disk (RunStore is process-scoped).
    store = RunStore()
    saved = Run(
        id="run-1",
        issue_key="ADLC-1",
        state=RunState.PLAN_READY,
        session_id="sess-1",
        workspace_path="/tmp/x",
    )

    loaded = store.load(saved)

    assert loaded is saved
    assert store.get("run-1") is saved
    # A state-mutating call must find it via the normal id lookup, not just load().
    store.mark_implementing("run-1")
    assert store.get("run-1").state == RunState.IMPLEMENTING


def test_full_happy_path_transition_sequence() -> None:
    store = RunStore()
    run = store.create("ADLC-1")
    store.mark_planning(run.id)
    store.set_plan_ready(run.id, plan="do the thing", session_id="sess-1", workspace_path="/tmp/x")
    store.mark_implementing(run.id)
    final = store.mark_completed(run.id, result="done")
    assert final.state == RunState.COMPLETED
    assert final.plan == "do the thing"
    assert final.session_id == "sess-1"
    assert final.result == "done"


def test_set_plan_ready_defaults_plan_structured_to_none() -> None:
    store = RunStore()
    run = store.create("ADLC-1")
    store.mark_planning(run.id)
    updated = store.set_plan_ready(
        run.id, plan="do the thing", session_id="sess-1", workspace_path="/tmp/x"
    )
    assert updated.plan_structured is None


def test_set_plan_ready_stores_plan_structured_when_given() -> None:
    store = RunStore()
    run = store.create("ADLC-1")
    store.mark_planning(run.id)
    structured = {"summary": "do the thing", "files_changed": ["a.py"], "risks": []}
    updated = store.set_plan_ready(
        run.id,
        plan="do the thing",
        session_id="sess-1",
        workspace_path="/tmp/x",
        plan_structured=structured,
    )
    assert updated.plan_structured == structured


def test_implement_cannot_skip_the_plan_gate() -> None:
    store = RunStore()
    run = store.create("ADLC-1")
    with pytest.raises(RunTransitionError):
        store.mark_implementing(run.id)  # still 'created', never went through plan


def test_plan_ready_requires_planning_state_first() -> None:
    store = RunStore()
    run = store.create("ADLC-1")
    with pytest.raises(RunTransitionError):
        store.set_plan_ready(run.id, plan="x", session_id="s", workspace_path="/tmp/x")


def test_mark_failed_works_from_any_state() -> None:
    store = RunStore()
    run = store.create("ADLC-1")
    failed = store.mark_failed(run.id, error="boom")
    assert failed.state == RunState.FAILED
    assert failed.error == "boom"


def test_operations_on_unknown_run_id_raise_key_error() -> None:
    store = RunStore()
    with pytest.raises(KeyError):
        store.mark_planning("does-not-exist")
