"""Unit tests for the Subtask -> Story -> Epic roll-up (rollup.py).

The tree logic is exercised against an in-memory fake client (no HTTP); the HTTP shape of
the two new client methods is covered in test_client.py, the CLI wiring in test_cli.py.
"""

from __future__ import annotations

import pytest

from symphony_workflow.client import JiraError
from symphony_workflow.models import HierarchyIssue
from symphony_workflow.rollup import (
    RollupAborted,
    RollupNotFound,
    hierarchy_level,
    rollup,
)


def _issue(
    key: str,
    *,
    kind: str,
    status: str = "To Do",
    parent: str | None = None,
    level: int | None = None,
) -> HierarchyIssue:
    category = {"Done": "done", "In Progress": "indeterminate"}.get(status, "new")
    return HierarchyIssue(
        key=key,
        status=status,
        status_category=category,
        issue_type=kind,
        hierarchy_level=level,
        parent_key=parent,
    )


class FakeClient:
    """Just the three methods rollup() uses. `stale_children` simulates Jira's eventually
    consistent search: child lookups keep reporting the old status of those keys."""

    def __init__(self, issues: list[HierarchyIssue], *, stale: set[str] | None = None) -> None:
        self.issues = {i.key: i for i in issues}
        self.stale = {k: self.issues[k].model_copy() for k in (stale or set())}
        self.transitioned: list[tuple[str, str]] = []
        self.fail_on: str | None = None

    def get_hierarchy_issue(self, key: str) -> HierarchyIssue:
        if key not in self.issues:
            raise JiraError(404, "Issue does not exist")
        return self.issues[key]

    def list_children(self, parent_key: str) -> list[HierarchyIssue]:
        kids = [i for i in self.issues.values() if i.parent_key == parent_key]
        return [self.stale.get(k.key, k) for k in kids]

    def transition_issue(self, key: str, name: str) -> None:
        if self.fail_on == key:
            raise JiraError(500, "boom")
        self.transitioned.append((key, name))
        issue = self.issues[key]
        self.issues[key] = issue.model_copy(update={"status": name, "status_category": "done"})


def _tree(**overrides: str) -> list[HierarchyIssue]:
    """EPIC-1 -> STORY-1 (SUB-1, SUB-2); STORY-2 (SUB-3). Overrides set statuses by key."""

    def st(key: str, default: str = "To Do") -> str:
        return overrides.get(key, default)

    return [
        _issue("EPIC-1", kind="Epic", level=1, status=st("EPIC-1")),
        _issue("STORY-1", kind="Story", level=0, parent="EPIC-1", status=st("STORY-1")),
        _issue("STORY-2", kind="Story", level=0, parent="EPIC-1", status=st("STORY-2")),
        _issue("SUB-1", kind="Subtask", level=-1, parent="STORY-1", status=st("SUB-1")),
        _issue("SUB-2", kind="Subtask", level=-1, parent="STORY-1", status=st("SUB-2")),
        _issue("SUB-3", kind="Subtask", level=-1, parent="STORY-2", status=st("SUB-3")),
    ]


def test_full_chain_subtask_to_story_to_epic() -> None:
    client = FakeClient(_tree(**{"SUB-2": "Done", "SUB-3": "Done", "STORY-2": "Done"}))
    result = rollup(client, "SUB-1")
    assert [(t.key, t.from_status, t.to) for t in result.transitions] == [
        ("SUB-1", "To Do", "Done"),
        ("STORY-1", "To Do", "Done"),
        ("EPIC-1", "To Do", "Done"),
    ]
    assert result.stopped_at is None
    assert client.transitioned == [("SUB-1", "Done"), ("STORY-1", "Done"), ("EPIC-1", "Done")]


def test_stops_when_a_sibling_subtask_is_not_done() -> None:
    client = FakeClient(_tree())
    result = rollup(client, "SUB-1")
    assert [t.key for t in result.transitions] == ["SUB-1"]
    assert result.stopped_at == {"key": "STORY-1", "reason": "1 of 2 subtask(s) not Done"}
    assert client.transitioned == [("SUB-1", "Done")]


def test_stops_at_epic_when_a_sibling_story_is_not_done() -> None:
    client = FakeClient(_tree(**{"SUB-2": "Done"}))  # STORY-2 and SUB-3 still To Do
    result = rollup(client, "SUB-1")
    assert [t.key for t in result.transitions] == ["SUB-1", "STORY-1"]
    assert result.stopped_at == {"key": "EPIC-1", "reason": "1 of 2 child issue(s) not Done"}
    assert ("EPIC-1", "Done") not in client.transitioned


def test_story_without_subtasks_is_judged_on_its_own_status() -> None:
    issues = [
        _issue("EPIC-1", kind="Epic", level=1),
        _issue("STORY-1", kind="Story", level=0, parent="EPIC-1"),
    ]
    client = FakeClient(issues)
    result = rollup(client, "STORY-1")
    assert result.transitions == []
    assert result.stopped_at is not None
    assert result.stopped_at["key"] == "STORY-1"
    assert client.transitioned == []

    done = FakeClient(
        [
            _issue("EPIC-1", kind="Epic", level=1),
            _issue("STORY-1", kind="Story", level=0, parent="EPIC-1", status="Done"),
        ]
    )
    result = rollup(done, "STORY-1")
    assert [t.key for t in result.transitions] == ["EPIC-1"]  # its only story is Done
    assert result.stopped_at is None


def test_already_done_everything_is_a_no_op_and_idempotent() -> None:
    all_done = {k: "Done" for k in ("EPIC-1", "STORY-1", "STORY-2", "SUB-1", "SUB-2", "SUB-3")}
    client = FakeClient(_tree(**all_done))
    first = rollup(client, "SUB-1")
    second = rollup(client, "SUB-1")
    assert first.transitions == [] and second.transitions == []
    assert first.stopped_at is None and second.stopped_at is None
    assert client.transitioned == []


def test_rerun_after_a_real_rollup_changes_nothing() -> None:
    client = FakeClient(_tree(**{"SUB-2": "Done", "SUB-3": "Done", "STORY-2": "Done"}))
    rollup(client, "SUB-1")
    again = rollup(client, "SUB-1")
    assert again.transitions == []
    assert len(client.transitioned) == 3


def test_dry_run_makes_no_transition_calls_but_reports_the_chain() -> None:
    client = FakeClient(_tree(**{"SUB-2": "Done", "SUB-3": "Done", "STORY-2": "Done"}))
    result = rollup(client, "SUB-1", dry_run=True)
    assert [t.key for t in result.transitions] == ["SUB-1", "STORY-1", "EPIC-1"]
    assert result.dry_run is True
    assert client.transitioned == []


def test_search_lag_does_not_hide_the_subtask_we_just_completed() -> None:
    # Jira's search can still report SUB-1 as To Do right after we transition it; the
    # roll-up must remember it just completed it rather than stop at "1 not done".
    client = FakeClient(
        _tree(**{"SUB-2": "Done", "SUB-3": "Done", "STORY-2": "Done"}), stale={"SUB-1"}
    )
    result = rollup(client, "SUB-1")
    assert [t.key for t in result.transitions] == ["SUB-1", "STORY-1", "EPIC-1"]


def test_story_key_input_reevaluates_story_and_epic_only() -> None:
    client = FakeClient(
        _tree(**{"SUB-1": "Done", "SUB-2": "Done", "SUB-3": "Done", "STORY-2": "Done"})
    )
    result = rollup(client, "STORY-1")
    assert [t.key for t in result.transitions] == ["STORY-1", "EPIC-1"]


def test_epic_key_input_reevaluates_only_the_epic() -> None:
    client = FakeClient(_tree(**{"STORY-1": "Done", "STORY-2": "Done"}))
    result = rollup(client, "EPIC-1")
    assert [t.key for t in result.transitions] == ["EPIC-1"]
    blocked = rollup(FakeClient(_tree(**{"STORY-1": "Done"})), "EPIC-1")
    assert blocked.transitions == []
    assert blocked.stopped_at == {"key": "EPIC-1", "reason": "1 of 2 child issue(s) not Done"}


def test_epic_with_no_stories_is_not_closed_by_rollup() -> None:
    client = FakeClient([_issue("EPIC-1", kind="Epic", level=1)])
    result = rollup(client, "EPIC-1")
    assert result.transitions == []
    assert result.stopped_at is not None and result.stopped_at["key"] == "EPIC-1"


def test_subtask_without_parent_story_stops_after_completing_itself() -> None:
    client = FakeClient([_issue("SUB-9", kind="Subtask", level=-1)])
    result = rollup(client, "SUB-9")
    assert [t.key for t in result.transitions] == ["SUB-9"]
    assert result.stopped_at == {"key": "SUB-9", "reason": "subtask has no parent story"}


def test_story_without_parent_epic_ends_cleanly() -> None:
    client = FakeClient(
        [
            _issue("STORY-1", kind="Story", level=0),
            _issue("SUB-1", kind="Subtask", level=-1, parent="STORY-1"),
        ]
    )
    result = rollup(client, "SUB-1")
    assert [t.key for t in result.transitions] == ["SUB-1", "STORY-1"]
    assert result.stopped_at is None


def test_unknown_key_raises_not_found() -> None:
    with pytest.raises(RollupNotFound):
        rollup(FakeClient([]), "NOPE-1")


def test_jira_error_mid_chain_reports_what_was_already_transitioned() -> None:
    client = FakeClient(_tree(**{"SUB-2": "Done", "SUB-3": "Done", "STORY-2": "Done"}))
    client.fail_on = "STORY-1"
    with pytest.raises(RollupAborted) as info:
        rollup(client, "SUB-1")
    assert [t.key for t in info.value.result.transitions] == ["SUB-1"]
    assert info.value.cause.status_code == 500


def test_non_404_error_on_the_first_lookup_is_not_reported_as_not_found() -> None:
    class Boom(FakeClient):
        def get_hierarchy_issue(self, key: str) -> HierarchyIssue:
            raise JiraError(503, "unavailable")

    with pytest.raises(RollupAborted) as info:
        rollup(Boom([]), "SUB-1")
    assert info.value.result.transitions == []


@pytest.mark.parametrize(
    ("kind", "level", "expected"),
    [
        ("Subtask", -1, -1),
        ("Story", 0, 0),
        ("Epic", 1, 1),
        ("Sub-task", None, -1),
        ("Epic", None, 1),
        ("Task", None, 0),
        ("Bug", None, 0),
    ],
)
def test_hierarchy_level_prefers_jira_level_then_type_name(
    kind: str, level: int | None, expected: int
) -> None:
    assert hierarchy_level(_issue("X-1", kind=kind, level=level)) == expected


def test_done_detection_prefers_status_category_over_name() -> None:
    renamed = HierarchyIssue(key="X-1", status="Shipped", status_category="done")
    assert renamed.is_done
    fallback = HierarchyIssue(key="X-2", status="Done")  # no category in the payload
    assert fallback.is_done
    active = HierarchyIssue(key="X-3", status="In Progress", status_category="indeterminate")
    assert not active.is_done


def test_story_nested_under_a_story_is_not_rolled_further() -> None:
    client = FakeClient(
        [
            _issue("OUTER-1", kind="Story", level=0),
            _issue("INNER-1", kind="Story", level=0, parent="OUTER-1"),
            _issue("SUB-1", kind="Subtask", level=-1, parent="INNER-1"),
        ]
    )
    result = rollup(client, "SUB-1")
    assert [t.key for t in result.transitions] == ["SUB-1", "INNER-1"]
    assert result.stopped_at == {
        "key": "OUTER-1",
        "reason": "parent is not an epic (nested stories are not rolled up)",
    }
