"""HTTP client wrapper for the Jira Cloud REST API.

Uses API v2 throughout (plain-text `description`/comment bodies, not the v3
Atlassian Document Format) — this matches the pattern already proven against
`cloud-f1.atlassian.net` in this org's other tooling. If you point this at a
different Jira Cloud instance, re-verify the search endpoint against current
Atlassian docs before relying on it: Jira's JQL search API has changed more
than once and this client has only been exercised against mocked responses,
not a live server, in this repo.
"""

from __future__ import annotations

from typing import Any

import httpx

from jira_agent_cli.config import JiraConfig
from jira_agent_cli.models import Issue, IssueCreateResult, SearchResult, Transition


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

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def get_issue(self, key: str) -> Issue:
        response = self._client.get(f"/rest/api/2/issue/{key}")
        self._raise_for_status(response)
        return Issue.from_api(response.json())

    def search_issues(self, jql: str, max_results: int = 50) -> SearchResult:
        response = self._client.post(
            "/rest/api/2/search",
            json={
                "jql": jql,
                "maxResults": max_results,
                "fields": ["summary", "status", "assignee"],
            },
        )
        self._raise_for_status(response)
        payload = response.json()
        return SearchResult(
            issues=[Issue.from_api(item) for item in payload.get("issues", [])],
            total=payload.get("total", len(payload.get("issues", []))),
        )

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str | None = None,
        issue_type: str = "Task",
        *,
        labels: list[str] | None = None,
        epic: str | None = None,
        sprint_id: int | None = None,
        story_points: float | None = None,
        assignee_account_id: str | None = None,
    ) -> IssueCreateResult:
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

    def add_comment(self, key: str, body: str) -> None:
        response = self._client.post(f"/rest/api/2/issue/{key}/comment", json={"body": body})
        self._raise_for_status(response)

    def list_transitions(self, key: str) -> list[Transition]:
        response = self._client.get(f"/rest/api/2/issue/{key}/transitions")
        self._raise_for_status(response)
        return [
            Transition(id=t["id"], name=t["name"])
            for t in response.json().get("transitions", [])
        ]

    def transition_issue(self, key: str, transition_name: str) -> None:
        transitions = self.list_transitions(key)
        match = next(
            (t for t in transitions if t.name.lower() == transition_name.lower()), None
        )
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
