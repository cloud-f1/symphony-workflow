"""HTTP client wrapper for the Jira Cloud REST API.

Uses API v2 for issue CRUD (plain-text `description`/comment bodies, not the
v3 Atlassian Document Format) — matches the pattern already proven against
`cloud-f1.atlassian.net` in this org's other tooling. `search_issues` is the
one exception: it uses v3's `/rest/api/3/search/jql`, because the classic v2
`/rest/api/2/search` endpoint is now HTTP 410 (retired by Atlassian) —
verified live against cloud-f1.atlassian.net on 2026-09-24. If you point this
at a different Jira Cloud instance, re-verify both endpoints are still
current before relying on them.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from symphony_workflow.config import JiraConfig
from symphony_workflow.models import (
    CurrentUser,
    HierarchyIssue,
    Issue,
    IssueCreateResult,
    SearchResult,
    Transition,
)
from symphony_workflow.tracker import TrackerError

# Overridable per adopting repo/project — see docs/PORTABILITY.md. Defaults to
# ADLC (this POC's own board) for backward compatibility. Lives here (not
# poller.py) since ADLC-57: building the "ready ticket" query is this
# backend's own concern now, not the poller's — see search_ready_tickets().
JIRA_PROJECT_ENV = "SYMPHONY_WORKFLOW_JIRA_PROJECT"

# **2026-09-30, external-repo adoption**: the default `issuetype not in
# (Epic, Subtask)` assumes each ticket IS the atomic unit of work — true for
# ADLC's flat Task/Story backlog, false for a repo that mirrors its own
# Phase->Epic->Story->Subtask hierarchy into Jira (Epic->Epic, epic->Story,
# dev_task->Subtask — that repo's own sync script does exactly
# this). There, every real unit of work IS a Subtask, so the default query
# finds zero ready tickets no matter how much backlog exists.
#
# **Opt-in, not a behavior change**: unset (`""`) keeps today's exact JQL
# (`issuetype not in (Epic, Subtask)`) for ADLC and any other existing
# consumer. Set it to a comma-separated allowlist to search by inclusion
# instead of exclusion — the two are different questions ("what's NOT an
# epic" vs "what IS our atomic work item") and a repo whose real work lives
# on Subtasks needs the inclusion form, not a shorter exclusion list.
READY_ISSUETYPES_ENV = "SYMPHONY_WORKFLOW_READY_ISSUETYPES"


class JiraError(TrackerError):
    """Raised when the Jira API returns a non-2xx response."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"Jira API error {status_code}: {message}")


class JiraClient:
    """Thin, testable wrapper around the Jira Cloud REST API v2."""

    def __init__(self, config: JiraConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(
            base_url=config.base_url,
            auth=(config.email, config.api_token),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=30.0,
        )

    @property
    def base_url(self) -> str:
        return self._config.base_url

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_myself(self, *, timeout: float | None = None) -> CurrentUser:
        """Return the authenticated user. Cheap enough to double as a connectivity check —
        pass a short `timeout` (seconds) to override the client default for that use.
        """
        kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        response = self._client.get("/rest/api/2/myself", **kwargs)
        self._raise_for_status(response)
        return CurrentUser.from_api(response.json())

    def get_issue(self, key: str) -> Issue:
        response = self._client.get(f"/rest/api/2/issue/{key}")
        self._raise_for_status(response)
        return Issue.from_api(response.json())

    def get_hierarchy_issue(self, key: str) -> HierarchyIssue:
        """Like `get_issue` but with the fields roll-up needs: issue type (+ its
        hierarchy level), status category, and `parent`. Raises `JiraError` (404 for an
        unknown key, 400 for a malformed one).
        """
        response = self._client.get(
            f"/rest/api/2/issue/{key}",
            params={"fields": "summary,status,issuetype,parent,assignee,subtasks"},
        )
        self._raise_for_status(response)
        return HierarchyIssue.from_api(response.json())

    def search_hierarchy(self, jql: str, *, page_size: int = 100) -> list[HierarchyIssue]:
        """Every issue matching `jql`, with the fields the hierarchy audit needs (type + level,
        status category, parent, assignee, and the list of Subtasks). Pages through
        `/rest/api/3/search/jql`'s `nextPageToken`, so a big project is fully counted: a partial
        list would make "no story is missing subtasks" a false positive.
        """
        found: list[HierarchyIssue] = []
        token: str | None = None
        for _ in range(100):  # hard stop: 10k issues in one audit query is a bug
            body: dict[str, Any] = {
                "jql": jql,
                "maxResults": page_size,
                "fields": ["summary", "status", "issuetype", "parent", "assignee", "subtasks"],
            }
            if token:
                body["nextPageToken"] = token
            response = self._client.post("/rest/api/3/search/jql", json=body)
            self._raise_for_status(response)
            payload = response.json()
            found.extend(HierarchyIssue.from_api(item) for item in payload.get("issues", []))
            token = payload.get("nextPageToken")
            if payload.get("isLast", not token) or not token:
                return found
        raise JiraError(500, f"Search did not terminate (pagination): {jql}")

    def list_children(self, parent_key: str, *, page_size: int = 100) -> list[HierarchyIssue]:
        """Every issue whose `parent` is `parent_key` (Subtasks of a Story, Stories of an
        Epic in a team-managed project; also Epic children in company-managed projects on
        current Jira). Pages through `/rest/api/3/search/jql`'s `nextPageToken` so an Epic
        with more than one page of Stories is still fully counted -- a partial list would
        make "all children done" a false positive.
        """
        children: list[HierarchyIssue] = []
        token: str | None = None
        for _ in range(100):  # hard stop: 10k children is a bug, not a real tree
            body: dict[str, Any] = {
                "jql": f"parent = {parent_key} ORDER BY key ASC",
                "maxResults": page_size,
                "fields": ["summary", "status", "issuetype"],
            }
            if token:
                body["nextPageToken"] = token
            response = self._client.post("/rest/api/3/search/jql", json=body)
            self._raise_for_status(response)
            payload = response.json()
            children.extend(HierarchyIssue.from_api(item) for item in payload.get("issues", []))
            token = payload.get("nextPageToken")
            if payload.get("isLast", not token) or not token:
                return children
        raise JiraError(500, f"Child search for {parent_key} did not terminate (pagination)")

    def issue_url(self, key: str) -> str:
        return f"{self._config.base_url}/browse/{key}"

    def search_ready_tickets(self, *, max_results: int = 50) -> SearchResult:
        """TrackerClient's backend-agnostic "what should the poller auto-Plan
        next" query, built as JQL. Default: Epic/Subtask excluded — you
        can't "implement" an Epic, and a Subtask's parent Story is the
        actual unit of work in this POC's model — moved here from poller.py
        (ADLC-57) so the poller doesn't need to know JQL exists.

        `READY_ISSUETYPES_ENV` switches this to an inclusion list instead
        (see its own comment for why some adopting repos need that) — set,
        it becomes `issuetype in (...)`; unset, behavior is unchanged.
        """
        project = os.environ.get(JIRA_PROJECT_ENV, "ADLC")
        included = os.environ.get(READY_ISSUETYPES_ENV, "").strip()
        if included:
            names = ", ".join(
                f'"{name.strip()}"' for name in included.split(",") if name.strip())
            type_clause = f"issuetype in ({names})"
        else:
            type_clause = "issuetype not in (Epic, Subtask)"
        jql = (
            f'project = {project} AND status = "To Do" '
            f"AND {type_clause} ORDER BY created ASC"
        )
        return self.search_issues(jql, max_results=max_results)

    def search_issues(self, jql: str, max_results: int = 50) -> SearchResult:
        # Verified live 2026-09-24: the classic v2 `/rest/api/2/search` endpoint
        # now returns HTTP 410 (Atlassian retired it) — this MUST use the v3
        # enhanced search endpoint even though the rest of this client is v2.
        response = self._client.post(
            "/rest/api/3/search/jql",
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": ["summary", "status", "assignee"],
            },
        )
        self._raise_for_status(response)
        payload = response.json()
        issues = [Issue.from_api(item) for item in payload.get("issues", [])]
        return SearchResult(issues=issues, total=payload.get("total", len(issues)))

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str | None = None,
        issue_type: str = "Task",
        *,
        labels: list[str] | None = None,
        epic: str | None = None,
        parent: str | None = None,
        sprint_id: int | None = None,
        story_points: float | None = None,
        assignee_account_id: str | None = None,
    ) -> IssueCreateResult:
        """`epic` and `parent` are two different linking mechanisms, not
        interchangeable spellings of the same thing — Jira project type
        decides which one applies. `epic` sets the classic/company-managed
        "Epic Link" custom field (confirmed on `CF1`; needs `epic_link_field`
        configured, otherwise silently a no-op). `parent` sets the standard
        `parent` field, which is what team-managed projects use for
        Story->Epic linking (confirmed on `ADLC`, a `simplified: true`
        project — `customfield_10014` errors there with "cannot be set. It
        is not on the appropriate screen") *and* what every project type uses
        for Subtask->Story linking. Callers must pick the one that matches
        the target project/issue type.
        """
        fields: dict[str, Any] = {
            "project": {"key": project_key},
            "summary": summary,
            "issuetype": {"name": issue_type},
        }
        if description:
            fields["description"] = description
        if labels:
            fields["labels"] = labels
        if assignee_account_id:
            fields["assignee"] = {"accountId": assignee_account_id}
        if epic and self._config.epic_link_field:
            fields[self._config.epic_link_field] = epic
        if parent:
            fields["parent"] = {"key": parent}
        if sprint_id is not None and self._config.sprint_field:
            fields[self._config.sprint_field] = sprint_id
        if story_points is not None and self._config.story_points_field:
            fields[self._config.story_points_field] = story_points

        response = self._client.post("/rest/api/2/issue", json={"fields": fields})
        self._raise_for_status(response)
        payload = response.json()
        return IssueCreateResult(
            key=payload["key"],
            id=payload["id"],
            url=f"{self._config.base_url}/browse/{payload['key']}",
        )

    def update_issue(
        self, key: str, *, summary: str | None = None, description: str | None = None
    ) -> None:
        """PUT /issue/{key} with only the fields given — Jira's update endpoint
        only touches fields present in the payload, so omitted ones are left
        alone. At least one of summary/description must be given.
        """
        fields: dict[str, Any] = {}
        if summary is not None:
            fields["summary"] = summary
        if description is not None:
            fields["description"] = description
        response = self._client.put(f"/rest/api/2/issue/{key}", json={"fields": fields})
        self._raise_for_status(response)

    def add_comment(self, key: str, body: str) -> None:
        response = self._client.post(f"/rest/api/2/issue/{key}/comment", json={"body": body})
        self._raise_for_status(response)

    def create_remote_link(self, key: str, *, url: str, title: str) -> None:
        """A Jira "web link" (`/remotelink`), distinct from a comment — shows up
        in the issue's Links section. Call this only once the target `url`
        actually resolves (e.g. after a commit pointing at it is merged) —
        there's nothing stopping you from linking a URL that 404s, so don't.
        """
        response = self._client.post(
            f"/rest/api/2/issue/{key}/remotelink",
            json={"object": {"url": url, "title": title}},
        )
        self._raise_for_status(response)

    def list_transitions(self, key: str) -> list[Transition]:
        response = self._client.get(f"/rest/api/2/issue/{key}/transitions")
        self._raise_for_status(response)
        return [
            Transition(id=t["id"], name=t["name"]) for t in response.json().get("transitions", [])
        ]

    def transition_issue(self, key: str, transition_name: str) -> None:
        transitions = self.list_transitions(key)
        match = next((t for t in transitions if t.name.lower() == transition_name.lower()), None)
        if match is None:
            available = ", ".join(t.name for t in transitions) or "none"
            raise JiraError(
                404,
                f"No transition named {transition_name!r} for {key}. Available: {available}",
            )
        response = self._client.post(
            f"/rest/api/2/issue/{key}/transitions", json={"transition": {"id": match.id}}
        )
        self._raise_for_status(response)

    @staticmethod
    def _raise_for_status(response: httpx.Response) -> None:
        if response.is_success:
            return
        try:
            payload = response.json()
            message = "; ".join(payload.get("errorMessages", [])) or str(
                payload.get("errors", payload)
            )
        except ValueError:
            message = response.text
        raise JiraError(response.status_code, message)
