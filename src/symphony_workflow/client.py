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

from typing import Any

import httpx

from symphony_workflow.config import JiraConfig
from symphony_workflow.models import (
    CurrentUser,
    Issue,
    IssueCreateResult,
    SearchResult,
    Transition,
)


class JiraError(RuntimeError):
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
