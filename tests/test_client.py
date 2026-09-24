"""Unit tests for JiraClient — every HTTP call is mocked via respx; no live Jira access."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import JiraConfig

from .conftest import BASE_URL


@respx.mock
def test_get_myself_parses_response(config: JiraConfig) -> None:
    route = respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(
            200,
            json={
                "accountId": "5b10ac8d82e05b22cc7d4ef5",
                "displayName": "Jira Bot",
                "emailAddress": "bot@example.com",
                "active": True,
            },
        )
    )
    with JiraClient(config) as client:
        user = client.get_myself()
    assert route.called
    assert route.calls.last.request.method == "GET"
    assert user.account_id == "5b10ac8d82e05b22cc7d4ef5"
    assert user.display_name == "Jira Bot"
    assert user.email_address == "bot@example.com"
    assert user.active is True


@respx.mock
def test_get_myself_hidden_email_is_none(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": "abc", "displayName": "Bot"})
    )
    with JiraClient(config) as client:
        user = client.get_myself()
    assert user.email_address is None


@respx.mock
def test_get_myself_passes_timeout_override(config: JiraConfig) -> None:
    route = respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": "abc", "displayName": "Bot"})
    )
    with JiraClient(config) as client:
        client.get_myself(timeout=2.5)
    timeout = route.calls.last.request.extensions["timeout"]
    assert timeout["connect"] == 2.5
    assert timeout["read"] == 2.5


@respx.mock
def test_get_myself_unauthorized_raises_jira_error(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(401, json={"errorMessages": ["Unauthorized"]})
    )
    with JiraClient(config) as client, pytest.raises(JiraError) as exc_info:
        client.get_myself()
    assert exc_info.value.status_code == 401


@respx.mock
def test_get_issue_parses_response(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CF1-1",
                "id": "10001",
                "fields": {
                    "summary": "Fix login bug",
                    "status": {"name": "In Progress"},
                    "assignee": {"displayName": "Alex"},
                },
            },
        )
    )
    with JiraClient(config) as client:
        issue = client.get_issue("CF1-1")
    assert issue.key == "CF1-1"
    assert issue.summary == "Fix login bug"
    assert issue.status == "In Progress"
    assert issue.assignee == "Alex"


@respx.mock
def test_get_issue_with_no_assignee_returns_none(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-2").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CF1-2",
                "id": "10002",
                "fields": {"summary": "Unassigned", "status": {"name": "Todo"}, "assignee": None},
            },
        )
    )
    with JiraClient(config) as client:
        issue = client.get_issue("CF1-2")
    assert issue.assignee is None


@respx.mock
def test_get_issue_raises_jira_error_on_404(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-999").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
    )
    with JiraClient(config) as client, pytest.raises(JiraError) as exc_info:
        client.get_issue("CF1-999")
    assert exc_info.value.status_code == 404
    assert "does not exist" in str(exc_info.value)


@respx.mock
def test_search_issues_returns_all_matches(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 2,
                "issues": [
                    {
                        "key": "CF1-1",
                        "id": "1",
                        "fields": {"summary": "A", "status": {"name": "Todo"}},
                    },
                    {
                        "key": "CF1-2",
                        "id": "2",
                        "fields": {"summary": "B", "status": {"name": "Done"}},
                    },
                ],
            },
        )
    )
    with JiraClient(config) as client:
        result = client.search_issues("project = CF1")
    assert result.total == 2
    assert [issue.key for issue in result.issues] == ["CF1-1", "CF1-2"]


@respx.mock
def test_search_issues_sends_jql_and_max_results(config: JiraConfig) -> None:
    route = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"total": 0, "issues": []})
    )
    with JiraClient(config) as client:
        client.search_issues("project = CF1", max_results=10)
    sent = json.loads(route.calls.last.request.content)
    assert sent["jql"] == "project = CF1"
    assert sent["maxResults"] == 10


@respx.mock
def test_create_issue_sends_plain_text_description(config: JiraConfig) -> None:
    route = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"key": "CF1-3", "id": "3"})
    )
    with JiraClient(config) as client:
        result = client.create_issue("CF1", "New ticket", "Some description", "Task")
    assert result.key == "CF1-3"
    assert result.url == f"{BASE_URL}/browse/CF1-3"
    sent = json.loads(route.calls.last.request.content)
    assert sent["fields"]["project"] == {"key": "CF1"}
    assert sent["fields"]["description"] == "Some description"


@respx.mock
def test_create_issue_omits_custom_fields_when_not_configured(config: JiraConfig) -> None:
    route = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"key": "CF1-4", "id": "4"})
    )
    with JiraClient(config) as client:
        client.create_issue("CF1", "No custom fields", epic="CF1-11", sprint_id=292)
    sent = json.loads(route.calls.last.request.content)
    assert "customfield_10014" not in sent["fields"]
    assert "customfield_10020" not in sent["fields"]


@respx.mock
def test_create_issue_includes_custom_fields_when_configured() -> None:
    config = JiraConfig(
        base_url=BASE_URL,
        email="bot@example.com",
        api_token="token",
        epic_link_field="customfield_10014",
        sprint_field="customfield_10020",
        story_points_field="customfield_10031",
    )
    route = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"key": "CF1-5", "id": "5"})
    )
    with JiraClient(config) as client:
        client.create_issue(
            "CF1", "Has custom fields", epic="CF1-11", sprint_id=292, story_points=3
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent["fields"]["customfield_10014"] == "CF1-11"
    assert sent["fields"]["customfield_10020"] == 292
    assert sent["fields"]["customfield_10031"] == 3


@respx.mock
def test_add_comment_posts_plain_body(config: JiraConfig) -> None:
    route = respx.post(f"{BASE_URL}/rest/api/2/issue/CF1-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    with JiraClient(config) as client:
        client.add_comment("CF1-1", "Looks good")
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"body": "Looks good"}


@respx.mock
def test_transition_issue_matches_by_name_case_insensitively(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-1/transitions").mock(
        return_value=httpx.Response(
            200,
            json={
                "transitions": [{"id": "31", "name": "Done"}, {"id": "21", "name": "In Progress"}]
            },
        )
    )
    transition_route = respx.post(f"{BASE_URL}/rest/api/2/issue/CF1-1/transitions").mock(
        return_value=httpx.Response(204)
    )
    with JiraClient(config) as client:
        client.transition_issue("CF1-1", "done")
    sent = json.loads(transition_route.calls.last.request.content)
    assert sent["transition"]["id"] == "31"


@respx.mock
def test_transition_issue_raises_when_transition_missing(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-1/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "31", "name": "Done"}]})
    )
    with JiraClient(config) as client, pytest.raises(JiraError, match="No transition named"):
        client.transition_issue("CF1-1", "Cancelled")
