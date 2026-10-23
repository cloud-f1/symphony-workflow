"""Unit tests for models.py's shared tracker contract (ADLC-88/95).

Issue/CurrentUser's from_api/from_linear parsing already gets indirect
coverage through test_client.py/test_linear_client.py's respx-mocked
fixtures -- this file makes the shared contract explicit instead of implicit:
missing-optional-field edge cases, and both constructors producing an
equivalent Issue/CurrentUser shape from each backend's own payload shape.
"""

from __future__ import annotations

from symphony_workflow.models import (
    CurrentUser,
    Issue,
    IssueCreateResult,
    SearchResult,
    Transition,
    TriageDecision,
)


def test_issue_from_api_full_payload() -> None:
    issue = Issue.from_api(
        {
            "key": "ADLC-1",
            "id": "10001",
            "fields": {
                "summary": "Do the thing",
                "status": {"name": "In Progress"},
                "assignee": {"displayName": "Alex"},
                "description": "Some plain-text body",
            },
        }
    )
    assert issue == Issue(
        key="ADLC-1",
        id="10001",
        summary="Do the thing",
        status="In Progress",
        assignee="Alex",
        description="Some plain-text body",
    )


def test_issue_from_api_missing_optional_fields() -> None:
    # No assignee, no description, no status object at all -- fields.get(...)
    # or {} guards must not raise on any of these.
    issue = Issue.from_api({"key": "ADLC-2", "id": "10002", "fields": {}})
    assert issue.summary == ""
    assert issue.status == ""
    assert issue.assignee is None
    assert issue.description is None


def test_issue_from_linear_full_payload() -> None:
    issue = Issue.from_linear(
        {
            "id": "uuid-1",
            "identifier": "GAI-5",
            "title": "Fix login bug",
            "description": "details",
            "state": {"name": "In Progress"},
            "assignee": {"name": "Alex"},
        }
    )
    assert issue == Issue(
        key="GAI-5",
        id="uuid-1",
        summary="Fix login bug",
        status="In Progress",
        assignee="Alex",
        description="details",
    )


def test_issue_from_linear_missing_optional_fields() -> None:
    issue = Issue.from_linear(
        {
            "id": "uuid-2",
            "identifier": "GAI-6",
            "title": "Unassigned",
            "description": None,
            "state": None,
            "assignee": None,
        }
    )
    assert issue.status == ""
    assert issue.assignee is None
    assert issue.description is None


def test_issue_from_api_and_from_linear_produce_equivalent_shape() -> None:
    # ADLC-57's shared-model claim, made explicit: the same logical issue,
    # parsed from each backend's own payload shape, lands on the same fields.
    jira_issue = Issue.from_api(
        {
            "key": "ADLC-1",
            "id": "10001",
            "fields": {
                "summary": "Ship the feature",
                "status": {"name": "Done"},
                "assignee": {"displayName": "Alex"},
            },
        }
    )
    linear_issue = Issue.from_linear(
        {
            "id": "uuid-1",
            "identifier": "GAI-1",
            "title": "Ship the feature",
            "description": None,
            "state": {"name": "Done"},
            "assignee": {"name": "Alex"},
        }
    )
    assert jira_issue.summary == linear_issue.summary
    assert jira_issue.status == linear_issue.status
    assert jira_issue.assignee == linear_issue.assignee
    # key/id are backend-native identifiers -- deliberately NOT expected equal.
    assert jira_issue.key != linear_issue.key


def test_current_user_from_api_full_payload() -> None:
    user = CurrentUser.from_api(
        {
            "accountId": "abc123",
            "displayName": "Jira Bot",
            "emailAddress": "bot@example.com",
            "active": True,
        }
    )
    assert user == CurrentUser(
        account_id="abc123", display_name="Jira Bot", email_address="bot@example.com", active=True
    )


def test_current_user_from_api_hidden_email_and_missing_active_default_true() -> None:
    user = CurrentUser.from_api({"accountId": "abc123", "displayName": "Jira Bot"})
    assert user.email_address is None
    assert user.active is True


def test_current_user_from_linear_full_payload() -> None:
    user = CurrentUser.from_linear({"id": "user-1", "name": "Bot", "email": "bot@example.com"})
    assert user == CurrentUser(
        account_id="user-1", display_name="Bot", email_address="bot@example.com", active=True
    )


def test_current_user_from_linear_missing_name_and_email() -> None:
    user = CurrentUser.from_linear({"id": "user-2"})
    assert user.display_name == ""
    assert user.email_address is None
    assert user.active is True  # Linear has no "active" concept -- always True


def test_current_user_from_api_and_from_linear_produce_equivalent_shape() -> None:
    jira_user = CurrentUser.from_api(
        {"accountId": "a1", "displayName": "Alex", "emailAddress": "alex@example.com"}
    )
    linear_user = CurrentUser.from_linear({"id": "u1", "name": "Alex", "email": "alex@example.com"})
    assert jira_user.display_name == linear_user.display_name
    assert jira_user.email_address == linear_user.email_address
    assert jira_user.active == linear_user.active is True


def test_search_result_holds_issues_and_total() -> None:
    result = SearchResult(issues=[Issue.from_api({"key": "A", "id": "1", "fields": {}})], total=1)
    assert result.total == 1
    assert len(result.issues) == 1


def test_search_result_empty() -> None:
    result = SearchResult(issues=[], total=0)
    assert result.issues == []
    assert result.total == 0


def test_issue_create_result_fields() -> None:
    result = IssueCreateResult(
        key="ADLC-1", id="1", url="https://example.atlassian.net/browse/ADLC-1"
    )
    assert result.key == "ADLC-1"
    assert result.url.endswith("/ADLC-1")


def test_transition_fields() -> None:
    transition = Transition(id="31", name="Done")
    assert transition.id == "31"
    assert transition.name == "Done"


def test_triage_decision_accepts_only_literal_actions() -> None:
    decision = TriageDecision(issue_key="ADLC-1", action="flag", reason="needs a human look")
    assert decision.action == "flag"


def test_issue_without_board_fields_leaves_them_empty() -> None:
    issue = Issue.from_api(
        {"key": "CF1-1", "id": "1", "fields": {"summary": "A", "status": {"name": "To Do"}}}
    )
    assert issue.issue_type is None
    assert issue.parent is None
    assert issue.parent_summary is None
    assert issue.status_category is None
    assert (issue.subtasks_done, issue.subtasks_total) == (0, 0)


def test_issue_json_keeps_the_old_keys_first() -> None:
    issue = Issue.from_api(
        {"key": "CF1-1", "id": "1", "fields": {"summary": "A", "status": {"name": "To Do"}}}
    )
    keys = list(issue.model_dump())
    assert keys[:6] == ["key", "id", "summary", "status", "assignee", "description"]
