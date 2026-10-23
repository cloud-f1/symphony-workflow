"""Unit tests for the hierarchy audit and add-subtasks (hierarchy.py).

The rules run against an in-memory fake client (no HTTP). The HTTP shape of `search_hierarchy` is
covered in test_client.py and the CLI wiring in test_cli.py.
"""

from __future__ import annotations

import pytest

from symphony_workflow.client import JiraError
from symphony_workflow.hierarchy import (
    AddSubtasksAborted,
    PlanError,
    add_subtasks,
    audit,
    validate_plan,
)
from symphony_workflow.models import HierarchyIssue, IssueCreateResult


def _issue(
    key: str,
    kind: str,
    status: str = "To Do",
    *,
    parent: str | None = None,
    assignee: str | None = "alex hsieh",
    subtasks: int | None = None,
    summary: str = "",
) -> HierarchyIssue:
    level = {"Epic": 1, "Subtask": -1, "Sub-task": -1}.get(kind, 0)
    category = {"Done": "done", "In Progress": "indeterminate"}.get(status, "new")
    return HierarchyIssue(
        key=key,
        summary=summary or key,
        status=status,
        status_category=category,
        issue_type=kind,
        hierarchy_level=level,
        parent_key=parent,
        assignee=assignee,
        subtask_count=subtasks,
    )


class FakeClient:
    """The methods hierarchy.py uses. Searches are answered from the issue list by JQL keywords."""

    def __init__(self, issues: list[HierarchyIssue]) -> None:
        self.issues = {i.key: i for i in issues}
        self.created: list[tuple[str, str, str, str | None]] = []
        self.transitioned: list[tuple[str, str]] = []
        self.fail_on_summary: str | None = None
        self.reject_type: str | None = None  # a type name Jira answers 400 to
        self._next = 100

    def search_hierarchy(self, jql: str) -> list[HierarchyIssue]:
        if "issuetype = Epic" in jql:
            return [i for i in self.issues.values() if i.issue_type == "Epic"]
        out = [i for i in self.issues.values() if i.issue_type not in ("Epic", "Subtask")]
        if "statusCategory != Done" in jql:
            out = [i for i in out if not i.is_done]
        return out

    def list_children(self, parent_key: str) -> list[HierarchyIssue]:
        return [i for i in self.issues.values() if i.parent_key == parent_key]

    def get_hierarchy_issue(self, key: str) -> HierarchyIssue:
        if key not in self.issues:
            raise JiraError(404, "Issue does not exist")
        return self.issues[key]

    def create_issue(
        self,
        project,
        summary,
        description=None,
        issue_type="Task",
        *,
        parent=None,
        assignee_account_id=None,
        **_kw,
    ):  # noqa: ANN001, ANN003
        if summary == self.fail_on_summary:
            raise JiraError(500, "boom")
        if issue_type == self.reject_type:
            raise JiraError(400, "issue type not found")
        self._next += 1
        key = f"{project}-{self._next}"
        self.created.append((key, summary, issue_type, assignee_account_id))
        self.issues[key] = _issue(key, "Subtask", parent=parent)
        return IssueCreateResult(key=key, id=str(self._next), url="https://x/" + key)

    def transition_issue(self, key: str, name: str) -> None:
        self.transitioned.append((key, name))


def _healthy() -> list[HierarchyIssue]:
    return [
        _issue("P-1", "Epic"),
        _issue("P-2", "Story", parent="P-1", subtasks=3),
        _issue("P-10", "Subtask", parent="P-2"),
        _issue("P-11", "Subtask", parent="P-2"),
        _issue("P-12", "Subtask", parent="P-2"),
    ]


def test_a_healthy_project_has_no_findings() -> None:
    result = audit(FakeClient(_healthy()), "P")
    assert result.ok and result.findings == []
    assert (result.epics, result.stories) == (1, 1)


def test_stories_count_excludes_subtasks_spelled_sub_task() -> None:
    """ADLC-142: the JQL excludes `Subtask` by name, so company-managed `Sub-task` issues come back
    in the "stories" search. They must not be counted as Stories (101 reported, 19 exist)."""
    issues = [
        _issue("P-1", "Epic"),
        _issue("P-2", "Story", "Done", parent="P-1", subtasks=2),
        _issue("P-10", "Sub-task", "Done", parent="P-2"),
        _issue("P-11", "Sub-task", "Done", parent="P-2"),
    ]
    result = audit(FakeClient(issues), "P")
    assert (result.epics, result.stories) == (1, 1)
    assert result.ok and result.findings == []


def test_story_without_subtasks_story_without_epic_and_epic_without_stories_are_errors() -> None:
    issues = [
        _issue("P-1", "Epic"),  # no stories
        _issue("P-2", "Epic"),
        _issue("P-3", "Story", parent="P-2", subtasks=0),
        _issue("P-4", "Bug", subtasks=2),  # no epic
    ]
    result = audit(FakeClient(issues), "P")
    rules = {(f.key, f.rule) for f in result.errors}
    assert ("P-1", "epic_without_stories") in rules
    assert ("P-3", "no_subtasks") in rules
    assert ("P-4", "no_epic") in rules
    assert not result.ok


def test_one_subtask_and_too_many_subtasks_are_warnings_only() -> None:
    issues = [
        _issue("P-1", "Epic"),
        _issue("P-2", "Story", parent="P-1", subtasks=1),
        _issue("P-3", "Story", parent="P-1", subtasks=9),
    ]
    result = audit(FakeClient(issues), "P")
    assert result.ok
    assert {f.rule for f in result.findings} == {"one_subtask", "too_many_subtasks"}
    assert all(f.severity == "warning" for f in result.findings)


def test_done_story_with_an_open_subtask_is_a_rollup_error() -> None:
    issues = _healthy()
    issues[1] = _issue("P-2", "Story", "Done", parent="P-1", subtasks=3)
    issues[2] = _issue("P-10", "Subtask", "Done", parent="P-2")
    issues[3] = _issue("P-11", "Subtask", "Done", parent="P-2")
    # P-12 stays open
    result = audit(FakeClient(issues), "P")
    found = [f for f in result.errors if f.rule == "done_with_open_subtasks"]
    assert [f.key for f in found] == ["P-2"] and "P-12" in found[0].detail


def test_assignee_rule_is_off_by_default_and_case_insensitive_when_on() -> None:
    issues = [
        _issue("P-1", "Epic", assignee=None),
        _issue("P-2", "Story", parent="P-1", subtasks=2, assignee="Alex Hsieh"),
        _issue("P-3", "Story", parent="P-1", subtasks=2, assignee="someone else"),
        _issue("P-4", "Task", parent="P-1", subtasks=2, assignee=None),
    ]
    assert audit(FakeClient(issues), "P").ok
    result = audit(FakeClient(issues), "P", assignee="alex hsieh")
    got = {(f.key, f.rule) for f in result.errors}
    assert got == {("P-1", "unassigned"), ("P-3", "wrong_assignee"), ("P-4", "unassigned")}


def test_open_only_skips_done_stories() -> None:
    issues = [
        _issue("P-1", "Epic"),
        _issue("P-2", "Story", "Done", parent="P-1", subtasks=0),
        _issue("P-3", "Story", parent="P-1", subtasks=0),
    ]
    assert {f.key for f in audit(FakeClient(issues), "P").errors} == {"P-2", "P-3"}
    assert {f.key for f in audit(FakeClient(issues), "P", open_only=True).errors} == {"P-3"}


def test_findings_list_errors_first_then_by_key_number() -> None:
    issues = [
        _issue("P-1", "Epic"),
        _issue("P-9", "Story", parent="P-1", subtasks=0),
        _issue("P-10", "Story", parent="P-1", subtasks=1),
        _issue("P-2", "Story", parent="P-1", subtasks=0),
    ]
    order = [(f.severity, f.key) for f in audit(FakeClient(issues), "P").findings]
    assert order == [("error", "P-2"), ("error", "P-9"), ("warning", "P-10")]


# ------------------------------------------------------------------ add_subtasks


def test_validate_plan_lists_every_problem() -> None:
    with pytest.raises(PlanError) as err:
        validate_plan({"P-1": [], "nokey": ["x"], "P-2": ["ok", "  "], "P-3": ["x" * 300]})
    text = str(err.value)
    assert "P-1" in text and "nokey" in text and "P-2" in text and "P-3" in text
    with pytest.raises(PlanError):
        validate_plan({})
    with pytest.raises(PlanError):
        validate_plan(["P-1"])
    assert validate_plan({"P-1": [" Add X ", "Tests: unit"]}) == {"P-1": ["Add X", "Tests: unit"]}


def test_add_subtasks_creates_under_a_story_with_none_and_assigns() -> None:
    fake = FakeClient([_issue("P-1", "Epic"), _issue("P-2", "Story", parent="P-1", subtasks=0)])
    result = add_subtasks(fake, {"P-2": ["Add X", "Tests: unit"]}, assignee_account_id="acc-1")
    assert [c[1:] for c in fake.created] == [
        ("Add X", "Subtask", "acc-1"),
        ("Tests: unit", "Subtask", "acc-1"),
    ]
    assert fake.transitioned == []
    assert result.stories[0].action == "created" and not result.stories[0].done
    assert result.stories[0].warnings == []


def test_a_done_story_gets_its_new_subtasks_rolled_to_done() -> None:
    fake = FakeClient([_issue("P-2", "Story", "Done", parent="P-1", subtasks=0)])
    result = add_subtasks(fake, {"P-2": ["Add X", "Tests: unit"]})
    assert [t for _, t in fake.transitioned] == ["Done", "Done"]
    assert result.stories[0].done


def test_it_is_idempotent_a_story_with_subtasks_is_skipped() -> None:
    fake = FakeClient([_issue("P-2", "Story", parent="P-1", subtasks=2)])
    result = add_subtasks(fake, {"P-2": ["Add X", "Tests: unit"]})
    assert fake.created == []
    assert result.stories[0].action == "skipped" and "already has 2" in result.stories[0].reason


def test_epics_subtasks_and_missing_keys_are_skipped_not_fatal() -> None:
    fake = FakeClient([_issue("P-1", "Epic"), _issue("P-5", "Subtask", parent="P-2")])
    result = add_subtasks(fake, {"P-1": ["a"], "P-5": ["b"], "P-99": ["c"]})
    assert [s.action for s in result.stories] == ["skipped", "skipped", "skipped"]
    assert fake.created == []


def test_dry_run_creates_and_transitions_nothing() -> None:
    fake = FakeClient([_issue("P-2", "Story", "Done", subtasks=0)])
    result = add_subtasks(fake, {"P-2": ["Add X", "Tests: unit"]}, dry_run=True)
    assert fake.created == [] and fake.transitioned == []
    assert result.dry_run and result.stories[0].created == ["Add X", "Tests: unit"]


def test_plan_warnings_say_when_there_is_no_tests_subtask_or_the_size_is_off() -> None:
    fake = FakeClient([_issue("P-2", "Story", subtasks=0), _issue("P-3", "Story", subtasks=0)])
    result = add_subtasks(fake, {"P-2": ["Add X"], "P-3": ["Add A", "Add B"]}, dry_run=True)
    w2, w3 = result.stories[0].warnings, result.stories[1].warnings
    assert any("really a Subtask" in w for w in w2) and any("Tests" in w for w in w2)
    assert w3 == ["no 'Tests: ...' Subtask, so 'done' has no proof"]


def test_the_other_subtask_type_spelling_is_tried_when_jira_answers_400() -> None:
    fake = FakeClient([_issue("P-2", "Story", subtasks=0)])
    fake.reject_type = "Subtask"
    add_subtasks(fake, {"P-2": ["Add X", "Tests: unit"]})
    assert [c[2] for c in fake.created] == ["Sub-task", "Sub-task"]


def test_a_failure_part_way_reports_what_was_created_and_raises() -> None:
    fake = FakeClient([_issue("P-2", "Story", subtasks=0)])
    fake.fail_on_summary = "Tests: unit"
    with pytest.raises(AddSubtasksAborted) as err:
        add_subtasks(fake, {"P-2": ["Add X", "Tests: unit"]})
    assert err.value.cause.status_code == 500
    assert len(fake.created) == 1  # the first one exists; a re-run will skip the story
