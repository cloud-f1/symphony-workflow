"""Unit tests for JiraClient — every HTTP call is mocked via respx; no live Jira access."""

from __future__ import annotations

import json

import httpx
import pytest
import respx

from symphony_workflow.client import JiraClient, JiraError, ReadBackError
from symphony_workflow.config import JiraConfig
from symphony_workflow.models import HierarchyIssue

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
def test_search_issues_asks_for_the_board_fields(config: JiraConfig) -> None:
    route = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"total": 0, "issues": []})
    )
    with JiraClient(config) as client:
        client.search_issues("project = CF1")
    sent = json.loads(route.calls.last.request.content)
    assert sent["fields"] == [
        "summary",
        "status",
        "assignee",
        "issuetype",
        "parent",
        "subtasks",
        "labels",
    ]


@respx.mock
def test_search_issues_returns_type_parent_category_and_subtask_counts(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={
                "issues": [
                    {
                        "key": "CF1-10",
                        "id": "10",
                        "fields": {
                            "summary": "A story",
                            "status": {
                                "name": "In Review",
                                "statusCategory": {"key": "indeterminate"},
                            },
                            "issuetype": {"name": "Story"},
                            "parent": {"key": "CF1-1", "fields": {"summary": "The epic"}},
                            "subtasks": [
                                {
                                    "key": "CF1-11",
                                    "fields": {"status": {"statusCategory": {"key": "done"}}},
                                },
                                {
                                    "key": "CF1-12",
                                    "fields": {"status": {"statusCategory": {"key": "new"}}},
                                },
                                {
                                    "key": "CF1-13",
                                    "fields": {"status": {"statusCategory": {"key": "done"}}},
                                },
                            ],
                        },
                    }
                ]
            },
        )
    )
    with JiraClient(config) as client:
        issue = client.search_issues("project = CF1").issues[0]
    assert issue.issue_type == "Story"
    assert issue.parent == "CF1-1"
    assert issue.parent_summary == "The epic"
    assert issue.status_category == "indeterminate"
    assert (issue.subtasks_done, issue.subtasks_total) == (2, 3)


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
def test_create_issue_parent_sets_standard_parent_field(config: JiraConfig) -> None:
    # Team-managed projects (e.g. ADLC) reject the classic Epic Link custom
    # field ("cannot be set. It is not on the appropriate screen") and use
    # the standard `parent` field for both Story->Epic and Subtask->Story
    # linking instead.
    route = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"key": "ADLC-50", "id": "50"})
    )
    with JiraClient(config) as client:
        client.create_issue("ADLC", "A subtask", issue_type="Subtask", parent="ADLC-45")
    sent = json.loads(route.calls.last.request.content)
    assert sent["fields"]["parent"] == {"key": "ADLC-45"}
    assert "customfield_10014" not in sent["fields"]


@respx.mock
def test_update_issue_sends_only_given_fields(config: JiraConfig) -> None:
    route = respx.put(f"{BASE_URL}/rest/api/2/issue/ADLC-28").mock(return_value=httpx.Response(204))
    with JiraClient(config) as client:
        client.update_issue("ADLC-28", summary="New title")
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"fields": {"summary": "New title"}}


@respx.mock
def test_update_issue_can_reparent(config: JiraConfig) -> None:
    route = respx.put(f"{BASE_URL}/rest/api/2/issue/ADLC-5").mock(return_value=httpx.Response(204))
    with JiraClient(config) as client:
        client.update_issue("ADLC-5", parent="ADLC-2")
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"fields": {"parent": {"key": "ADLC-2"}}}


@respx.mock
def test_get_hierarchy_issue_reads_wiki_description(config: JiraConfig) -> None:
    route = respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-2").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-2",
                "fields": {
                    "summary": "S",
                    "description": "text\n\nsd-id: s1",
                    "parent": {"key": "ADLC-1"},
                },
            },
        )
    )
    with JiraClient(config) as client:
        issue = client.get_hierarchy_issue("ADLC-2")
    assert "description" in route.calls.last.request.url.params["fields"]
    assert issue.description == "text\n\nsd-id: s1"
    assert issue.parent_key == "ADLC-1"


def test_hierarchy_issue_ignores_adf_description() -> None:
    issue = HierarchyIssue.from_api(
        {"key": "ADLC-2", "fields": {"summary": "S", "description": {"type": "doc"}}}
    )
    assert issue.description is None


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
def test_create_remote_link_posts_the_web_link_object(config: JiraConfig) -> None:
    route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-17/remotelink").mock(
        return_value=httpx.Response(201, json={"id": 1})
    )
    with JiraClient(config) as client:
        client.create_remote_link(
            "ADLC-17",
            url="https://github.com/x/y/blob/main/docs/runs/ADLC-17/review.md",
            title="review.md",
        )
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "object": {
            "url": "https://github.com/x/y/blob/main/docs/runs/ADLC-17/review.md",
            "title": "review.md",
        }
    }


def test_base_url_property_exposes_config_value(config: JiraConfig) -> None:
    with JiraClient(config) as client:
        assert client.base_url == BASE_URL


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


@respx.mock
def test_search_ready_tickets_defaults_to_excluding_epic_and_subtask(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unset `READY_ISSUETYPES_ENV` must keep today's exact JQL — this is
    ADLC's (and every other unmigrated consumer's) behavior, and it must not
    change just because a different repo now sets the env var."""
    monkeypatch.delenv("SYMPHONY_WORKFLOW_READY_ISSUETYPES", raising=False)
    route = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"total": 0, "issues": []})
    )
    with JiraClient(config) as client:
        client.search_ready_tickets()
    sent = json.loads(route.calls.last.request.content)
    assert sent["jql"] == (
        'project = ADLC AND status = "To Do" '
        "AND issuetype not in (Epic, Subtask) ORDER BY created ASC"
    )


@respx.mock
def test_search_ready_tickets_honors_an_issuetype_allowlist(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A repo whose real work items are Subtasks (an external repo's
    Phase->Epic->Story->Subtask mirror into Jira) needs an inclusion list,
    not a shorter exclusion list — the default query finds zero tickets for
    a repo shaped this way no matter how much backlog exists."""
    monkeypatch.setenv("SYMPHONY_WORKFLOW_READY_ISSUETYPES", "Subtask")
    route = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"total": 0, "issues": []})
    )
    with JiraClient(config) as client:
        client.search_ready_tickets()
    sent = json.loads(route.calls.last.request.content)
    assert sent["jql"] == (
        'project = ADLC AND status = "To Do" AND issuetype in ("Subtask") ORDER BY created ASC'
    )


@respx.mock
def test_search_ready_tickets_allowlist_supports_multiple_types(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_READY_ISSUETYPES", "Subtask, Task")
    route = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"total": 0, "issues": []})
    )
    with JiraClient(config) as client:
        client.search_ready_tickets()
    sent = json.loads(route.calls.last.request.content)
    assert 'issuetype in ("Subtask", "Task")' in sent["jql"]


@respx.mock
def test_get_hierarchy_issue_parses_type_status_category_and_parent(config: JiraConfig) -> None:
    route = respx.get(f"{BASE_URL}/rest/api/2/issue/TEST-44").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "TEST-44",
                "id": "10044",
                "fields": {
                    "summary": "E28",
                    "status": {"name": "Done", "statusCategory": {"key": "done"}},
                    "issuetype": {"name": "Subtask", "hierarchyLevel": -1},
                    "parent": {"key": "TEST-43", "fields": {"summary": "story"}},
                },
            },
        )
    )
    with JiraClient(config) as client:
        issue = client.get_hierarchy_issue("TEST-44")
    fields = route.calls.last.request.url.params["fields"]
    assert fields == "summary,status,issuetype,parent,assignee,subtasks,description"
    assert issue.key == "TEST-44"
    assert issue.is_done is True
    assert issue.issue_type == "Subtask"
    assert issue.hierarchy_level == -1
    assert issue.parent_key == "TEST-43"  # team-managed projects link with `parent`


@respx.mock
def test_get_hierarchy_issue_without_parent_or_level(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/TEST-38").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "TEST-38",
                "id": "1",
                "fields": {"status": {"name": "To Do"}, "issuetype": {"name": "Epic"}},
            },
        )
    )
    with JiraClient(config) as client:
        issue = client.get_hierarchy_issue("TEST-38")
    assert issue.parent_key is None
    assert issue.hierarchy_level is None
    assert issue.is_done is False


@respx.mock
def test_get_hierarchy_issue_404_raises_jira_error(config: JiraConfig) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/NOPE-1").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
    )
    with JiraClient(config) as client, pytest.raises(JiraError) as info:
        client.get_hierarchy_issue("NOPE-1")
    assert info.value.status_code == 404


@respx.mock
def test_list_children_searches_by_parent_and_follows_next_page_token(config: JiraConfig) -> None:
    def page(keys: list[str], *, token: str | None) -> httpx.Response:
        payload: dict = {
            "issues": [
                {
                    "key": k,
                    "id": k,
                    "fields": {"status": {"name": "Done", "statusCategory": {"key": "done"}}},
                }
                for k in keys
            ],
            "isLast": token is None,
        }
        if token:
            payload["nextPageToken"] = token
        return httpx.Response(200, json=payload)

    route = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        side_effect=[page(["S-1", "S-2"], token="tok-2"), page(["S-3"], token=None)]
    )
    with JiraClient(config) as client:
        children = client.list_children("TEST-45", page_size=2)
    assert [c.key for c in children] == ["S-1", "S-2", "S-3"]
    first, second = (json.loads(call.request.content) for call in route.calls)
    assert first["jql"] == "parent = TEST-45 ORDER BY key ASC"
    assert first["maxResults"] == 2
    assert "nextPageToken" not in first
    assert second["nextPageToken"] == "tok-2"


@respx.mock
def test_list_children_empty_and_error(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [], "isLast": True})
    )
    with JiraClient(config) as client:
        assert client.list_children("TEST-1") == []
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(400, json={"errorMessages": ["bad jql"]})
    )
    with JiraClient(config) as client, pytest.raises(JiraError):
        client.list_children("TEST-1")


# ---- search_hierarchy (the audit's one query) and the extra HierarchyIssue fields


def _audit_issue(
    key: str, kind: str, level: int, *, subtasks=None, assignee=None, parent=None
) -> dict:
    fields: dict = {
        "summary": key,
        "status": {"name": "To Do", "statusCategory": {"key": "new"}},
        "issuetype": {"name": kind, "hierarchyLevel": level},
    }
    if subtasks is not None:
        fields["subtasks"] = [{"key": f"{key}-s{i}"} for i in range(subtasks)]
    if assignee:
        fields["assignee"] = {"displayName": assignee}
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "id": key, "fields": fields}


def test_hierarchy_issue_parses_assignee_and_subtask_count() -> None:
    from symphony_workflow.models import HierarchyIssue

    full = HierarchyIssue.from_api(
        _audit_issue("A-1", "Story", 0, subtasks=3, assignee="alex", parent="A-0")
    )
    assert (full.assignee, full.subtask_count, full.parent_key) == ("alex", 3, "A-0")
    bare = HierarchyIssue.from_api(_audit_issue("A-2", "Story", 0))
    # fields not requested: unknown, not zero
    assert (bare.assignee, bare.subtask_count) == (None, None)
    empty = HierarchyIssue.from_api(_audit_issue("A-3", "Story", 0, subtasks=0))
    assert empty.subtask_count == 0


@respx.mock
def test_search_hierarchy_pages_through_next_page_token(config: JiraConfig) -> None:
    pages = [
        {
            "issues": [_audit_issue("A-1", "Story", 0, subtasks=2)],
            "nextPageToken": "t2",
            "isLast": False,
        },
        {"issues": [_audit_issue("A-2", "Story", 0, subtasks=0)], "isLast": True},
    ]
    route = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        side_effect=[httpx.Response(200, json=p) for p in pages]
    )
    with JiraClient(config) as client:
        found = client.search_hierarchy("project = A")
    assert [i.key for i in found] == ["A-1", "A-2"]
    first = json.loads(route.calls[0].request.content)
    assert first["jql"] == "project = A"
    assert {"parent", "assignee", "subtasks", "issuetype", "status"} <= set(first["fields"])
    assert json.loads(route.calls[1].request.content)["nextPageToken"] == "t2"


@respx.mock
def test_search_hierarchy_raises_on_http_error(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=httpx.Response(400, json={}))
    with JiraClient(config) as client, pytest.raises(JiraError):
        client.search_hierarchy("bad jql")


# --- plan 0010: labels_add, identity_label, read-back ---------------------------------------


def _issue_payload(
    key: str = "ADLC-9",
    summary: str = "A card",
    parent: str | None = None,
    labels: list[str] | None = None,
) -> dict:
    fields: dict = {"summary": summary, "status": {"name": "To Do"}, "labels": labels or []}
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "id": "9", "fields": fields}


def _search_hits(*keys: str) -> httpx.Response:
    issues = [_issue_payload(key) for key in keys]
    return httpx.Response(200, json={"total": len(issues), "issues": issues})


@respx.mock
def test_update_issue_labels_add_uses_update_block_not_fields(config: JiraConfig) -> None:
    route = respx.put(f"{BASE_URL}/rest/api/2/issue/ADLC-5").mock(return_value=httpx.Response(204))
    with JiraClient(config) as client:
        client.update_issue("ADLC-5", labels_add=["fu-one", "fu-two"])
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "fields": {},
        "update": {"labels": [{"add": "fu-one"}, {"add": "fu-two"}]},
    }


@respx.mock
def test_update_issue_label_with_whitespace_is_rejected_before_any_call(
    config: JiraConfig,
) -> None:
    route = respx.put(f"{BASE_URL}/rest/api/2/issue/ADLC-5").mock(return_value=httpx.Response(204))
    with JiraClient(config) as client, pytest.raises(JiraError, match="Invalid label"):
        client.update_issue("ADLC-5", labels_add=["two words"])
    assert not route.called


@respx.mock
def test_create_issue_without_identity_label_does_not_search(config: JiraConfig) -> None:
    search = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_hits())
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ADLC-9"})
    )
    with JiraClient(config) as client:
        result = client.create_issue("ADLC", "A card")
    assert not search.called
    assert result.existing is False


@respx.mock
def test_create_issue_identity_label_no_hit_creates_with_label_and_reads_back(
    config: JiraConfig,
) -> None:
    search = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_hits())
    create = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ADLC-9"})
    )
    get = respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-9").mock(
        return_value=httpx.Response(
            200, json=_issue_payload(parent="ADLC-1", labels=["other", "fu-x"])
        )
    )
    with JiraClient(config) as client:
        result = client.create_issue(
            "ADLC", "A card", labels=["other"], parent="ADLC-1", identity_label="fu-x"
        )
    assert json.loads(search.calls.last.request.content)["jql"] == (
        'project = ADLC AND labels = "fu-x"'
    )
    assert json.loads(create.calls.last.request.content)["fields"]["labels"] == ["other", "fu-x"]
    assert get.called  # read back by key
    assert (result.key, result.existing) == ("ADLC-9", False)


@respx.mock
def test_create_issue_identity_label_one_hit_creates_nothing(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_hits("ADLC-3"))
    create = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ADLC-9"})
    )
    with JiraClient(config) as client:
        result = client.create_issue("ADLC", "A card", identity_label="fu-x")
    assert not create.called
    assert (result.key, result.existing) == ("ADLC-3", True)


@respx.mock
def test_create_issue_identity_label_two_hits_is_a_hard_error(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=_search_hits("ADLC-3", "ADLC-4")
    )
    create = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ADLC-9"})
    )
    with JiraClient(config) as client, pytest.raises(ReadBackError, match="ADLC-3, ADLC-4"):
        client.create_issue("ADLC", "A card", identity_label="fu-x")
    assert not create.called


@respx.mock
def test_create_issue_identity_label_readback_catches_a_missing_label(
    config: JiraConfig,
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_hits())
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ADLC-9"})
    )
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-9").mock(
        return_value=httpx.Response(200, json=_issue_payload(labels=[]))
    )
    with JiraClient(config) as client, pytest.raises(ReadBackError, match="label 'fu-x'"):
        client.create_issue("ADLC", "A card", identity_label="fu-x")


@respx.mock
def test_create_issue_identity_label_readback_catches_a_wrong_parent(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_hits())
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ADLC-9"})
    )
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-9").mock(
        return_value=httpx.Response(200, json=_issue_payload(parent="ADLC-7", labels=["fu-x"]))
    )
    with JiraClient(config) as client, pytest.raises(ReadBackError, match="parent is 'ADLC-7'"):
        client.create_issue("ADLC", "A card", parent="ADLC-1", identity_label="fu-x")


@respx.mock
def test_create_issue_identity_label_readback_catches_a_wrong_summary(
    config: JiraConfig,
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_hits())
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={"id": "9", "key": "ADLC-9"})
    )
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-9").mock(
        return_value=httpx.Response(200, json=_issue_payload(summary="Other", labels=["fu-x"]))
    )
    with JiraClient(config) as client, pytest.raises(ReadBackError, match="summary is 'Other'"):
        client.create_issue("ADLC", "A card", identity_label="fu-x")


@respx.mock
def test_create_issue_identity_label_with_quote_is_rejected_before_any_call(
    config: JiraConfig,
) -> None:
    search = respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_hits())
    with JiraClient(config) as client, pytest.raises(JiraError, match="Invalid label"):
        client.create_issue("ADLC", "A card", identity_label='x" OR project = Y')
    assert not search.called


def test_issue_labels_parse_and_default_to_empty() -> None:
    from symphony_workflow.models import Issue

    assert Issue.from_api(_issue_payload(labels=["a", "b"])).labels == ["a", "b"]
    assert Issue.from_api({"key": "K-1", "id": "1", "fields": {}}).labels == []
