"""Unit tests for LinearClient — every HTTP call is mocked via respx; no live
Linear access. Linear's single GraphQL endpoint means every request hits the
same URL, so routes are distinguished by a substring of the query text
(_route_for) rather than by path/method the way test_client.py's Jira routes
are.
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
import respx

from symphony_workflow.config import LinearConfig
from symphony_workflow.linear_client import LinearClient, LinearError

from .conftest import LINEAR_API_URL


def _route_for(marker: str, response_json: dict) -> Callable[[httpx.Request], httpx.Response]:
    """respx side_effect: only responds to a request whose `query` contains
    `marker`; anything else 500s loudly instead of silently matching the
    wrong operation.
    """

    def _responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if marker not in body["query"]:
            return httpx.Response(500, json={"errors": [{"message": f"unexpected query: {body}"}]})
        return httpx.Response(200, json=response_json)

    return _responder


@respx.mock
def test_get_myself_parses_viewer_and_caches_org_url_key(linear_config: LinearConfig) -> None:
    respx.post(f"{LINEAR_API_URL}/").mock(
        side_effect=_route_for(
            "Viewer",
            {
                "data": {
                    "viewer": {
                        "id": "user-1",
                        "name": "Bot",
                        "email": "bot@example.com",
                        "organization": {"urlKey": "cloud-f1"},
                    }
                }
            },
        )
    )
    with LinearClient(linear_config) as client:
        user = client.get_myself()
        assert user.account_id == "user-1"
        assert user.display_name == "Bot"
        assert user.email_address == "bot@example.com"
        assert user.active is True
        # issue_url() shouldn't need a second round-trip now that org urlKey is cached.
        assert client.issue_url("GAI-5") == "https://linear.app/cloud-f1/issue/GAI-5"


@respx.mock
def test_issue_url_lazily_resolves_org_url_key(linear_config: LinearConfig) -> None:
    route = respx.post(f"{LINEAR_API_URL}/").mock(
        side_effect=_route_for(
            "Viewer",
            {
                "data": {
                    "viewer": {"id": "u", "name": "Bot", "organization": {"urlKey": "cloud-f1"}}
                }
            },
        )
    )
    with LinearClient(linear_config) as client:
        assert client.issue_url("GAI-9") == "https://linear.app/cloud-f1/issue/GAI-9"
    assert route.called


@respx.mock
def test_get_issue_parses_by_identifier(linear_config: LinearConfig) -> None:
    respx.post(f"{LINEAR_API_URL}/").mock(
        side_effect=_route_for(
            "IssueByIdentifier",
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "uuid-1",
                                "identifier": "GAI-5",
                                "number": 5,
                                "title": "Fix login bug",
                                "description": "details",
                                "state": {"id": "s1", "name": "In Progress", "type": "started"},
                                "assignee": {"name": "Alex"},
                                "team": {"id": "team-1", "key": "GAI"},
                                "parent": None,
                            }
                        ]
                    }
                }
            },
        )
    )
    with LinearClient(linear_config) as client:
        issue = client.get_issue("GAI-5")
    assert issue.key == "GAI-5"
    assert issue.id == "uuid-1"
    assert issue.summary == "Fix login bug"
    assert issue.status == "In Progress"
    assert issue.assignee == "Alex"
    assert issue.description == "details"


@respx.mock
def test_get_issue_with_no_assignee_returns_none(linear_config: LinearConfig) -> None:
    respx.post(f"{LINEAR_API_URL}/").mock(
        side_effect=_route_for(
            "IssueByIdentifier",
            {
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "uuid-2",
                                "identifier": "GAI-6",
                                "number": 6,
                                "title": "Unassigned",
                                "description": None,
                                "state": {"id": "s1", "name": "Todo", "type": "unstarted"},
                                "assignee": None,
                                "team": {"id": "team-1", "key": "GAI"},
                                "parent": None,
                            }
                        ]
                    }
                }
            },
        )
    )
    with LinearClient(linear_config) as client:
        issue = client.get_issue("GAI-6")
    assert issue.assignee is None
    assert issue.description is None


@respx.mock
def test_get_issue_not_found_raises_linear_error(linear_config: LinearConfig) -> None:
    respx.post(f"{LINEAR_API_URL}/").mock(
        side_effect=_route_for("IssueByIdentifier", {"data": {"issues": {"nodes": []}}})
    )
    with LinearClient(linear_config) as client, pytest.raises(LinearError, match="not found"):
        client.get_issue("GAI-999")


def test_get_issue_rejects_non_identifier_key(linear_config: LinearConfig) -> None:
    with (
        LinearClient(linear_config) as client,
        pytest.raises(LinearError, match="doesn't look like"),
    ):
        client.get_issue("not-an-identifier")


@respx.mock
def test_add_comment_resolves_issue_then_posts(linear_config: LinearConfig) -> None:
    calls: list[str] = []

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        calls.append(body["query"])
        if "IssueIds" in body["query"]:
            return httpx.Response(
                200,
                json={"data": {"issues": {"nodes": [{"id": "uuid-1", "team": {"id": "team-1"}}]}}},
            )
        if "CommentCreate" in body["query"]:
            assert body["variables"]["input"] == {"issueId": "uuid-1", "body": "Looks good"}
            return httpx.Response(200, json={"data": {"commentCreate": {"success": True}}})
        raise AssertionError(f"unexpected query: {body}")

    respx.post(f"{LINEAR_API_URL}/").mock(side_effect=responder)
    with LinearClient(linear_config) as client:
        client.add_comment("GAI-5", "Looks good")
    assert len(calls) == 2
    assert "IssueIds" in calls[0]
    assert "CommentCreate" in calls[1]


@respx.mock
def test_add_comment_failure_raises_linear_error(linear_config: LinearConfig) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "IssueIds" in body["query"]:
            return httpx.Response(
                200,
                json={"data": {"issues": {"nodes": [{"id": "uuid-1", "team": {"id": "team-1"}}]}}},
            )
        return httpx.Response(200, json={"data": {"commentCreate": {"success": False}}})

    respx.post(f"{LINEAR_API_URL}/").mock(side_effect=responder)
    with (
        LinearClient(linear_config) as client,
        pytest.raises(LinearError, match="Failed to add comment"),
    ):
        client.add_comment("GAI-5", "x")


@respx.mock
def test_transition_issue_matches_state_by_name_case_insensitively(
    linear_config: LinearConfig,
) -> None:
    update_calls: list[dict] = []

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "IssueIds" in body["query"]:
            return httpx.Response(
                200,
                json={"data": {"issues": {"nodes": [{"id": "uuid-1", "team": {"id": "team-1"}}]}}},
            )
        if "WorkflowStates" in body["query"]:
            return httpx.Response(
                200,
                json={
                    "data": {
                        "workflowStates": {
                            "nodes": [
                                {"id": "state-done", "name": "Done", "type": "completed"},
                                {"id": "state-review", "name": "In Review", "type": "started"},
                            ]
                        }
                    }
                },
            )
        if "IssueUpdate" in body["query"]:
            update_calls.append(body["variables"])
            return httpx.Response(200, json={"data": {"issueUpdate": {"success": True}}})
        raise AssertionError(f"unexpected query: {body}")

    respx.post(f"{LINEAR_API_URL}/").mock(side_effect=responder)
    with LinearClient(linear_config) as client:
        client.transition_issue("GAI-5", "in review")
    assert update_calls == [{"id": "uuid-1", "input": {"stateId": "state-review"}}]


@respx.mock
def test_transition_issue_raises_when_state_missing(linear_config: LinearConfig) -> None:
    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "IssueIds" in body["query"]:
            return httpx.Response(
                200,
                json={"data": {"issues": {"nodes": [{"id": "uuid-1", "team": {"id": "team-1"}}]}}},
            )
        return httpx.Response(
            200, json={"data": {"workflowStates": {"nodes": [{"id": "s1", "name": "Done"}]}}}
        )

    respx.post(f"{LINEAR_API_URL}/").mock(side_effect=responder)
    with (
        LinearClient(linear_config) as client,
        pytest.raises(LinearError, match="No workflow state"),
    ):
        client.transition_issue("GAI-5", "Cancelled")


@respx.mock
def test_search_ready_tickets_filters_by_team_unstarted_no_parent(
    linear_config: LinearConfig,
) -> None:
    sent_variables: dict = {}

    def responder(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent_variables.update(body.get("variables", {}))
        return httpx.Response(
            200,
            json={
                "data": {
                    "issues": {
                        "nodes": [
                            {
                                "id": "uuid-1",
                                "identifier": "GAI-1",
                                "number": 1,
                                "title": "A",
                                "description": None,
                                "state": {"id": "s1", "name": "Todo", "type": "unstarted"},
                                "assignee": None,
                                "team": {"id": "team-1", "key": "GAI"},
                                "parent": None,
                            }
                        ]
                    }
                }
            },
        )

    respx.post(f"{LINEAR_API_URL}/").mock(side_effect=responder)
    with LinearClient(linear_config) as client:
        result = client.search_ready_tickets(max_results=10)
    assert result.total == 1
    assert result.issues[0].key == "GAI-1"
    assert sent_variables == {"teamKey": "GAI", "first": 10}


@respx.mock
def test_graphql_errors_array_raises_linear_error(linear_config: LinearConfig) -> None:
    respx.post(f"{LINEAR_API_URL}/").mock(
        return_value=httpx.Response(200, json={"errors": [{"message": "Authentication required"}]})
    )
    with LinearClient(linear_config) as client, pytest.raises(
        LinearError, match="Authentication required"
    ):
        client.get_myself()


@respx.mock
def test_http_error_status_raises_linear_error(linear_config: LinearConfig) -> None:
    respx.post(f"{LINEAR_API_URL}/").mock(return_value=httpx.Response(401, text="Unauthorized"))
    with LinearClient(linear_config) as client, pytest.raises(LinearError, match="401"):
        client.get_myself()
