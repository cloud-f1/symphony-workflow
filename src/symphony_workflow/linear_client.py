"""HTTP client wrapper for the Linear GraphQL API — implements the same
TrackerClient surface as client.py's JiraClient (ADLC-57), so
orchestrator.py/poller.py can drive either backend without knowing which one
they're talking to.

Linear has no REST API for issue CRUD — everything goes through one GraphQL
endpoint. Two shape differences from Jira that show up throughout this file:

- An issue's human-readable identifier ("TEAM-123") is not itself a lookup
  key the API accepts directly — it's `{team.key}-{number}`, so get_issue()
  splits it and filters by both.
- Mutations (commentCreate, issueUpdate) take the issue's internal UUID, not
  its identifier — every write path resolves the issue first.

Auth header is the raw API key with no "Bearer " prefix — confirmed against
a real `lin_api_...` personal key (Linear's API docs specify this; verified
live 2026-09-26, matches content-asset-system's own working `gaia linear`
client, which this file does not depend on or reuse).
"""

from __future__ import annotations

from typing import Any

import httpx

from symphony_workflow.config import LinearConfig
from symphony_workflow.models import CurrentUser, Issue, SearchResult
from symphony_workflow.tracker import TrackerError

LINEAR_API_URL = "https://api.linear.app/graphql"

# Shared field selection for a single issue node — used by both get_issue()
# and search_ready_tickets() so the two return identically-shaped Issue
# objects regardless of which query found them.
_ISSUE_FIELDS = """
    id
    identifier
    number
    title
    description
    state { id name type }
    assignee { name }
    team { id key }
    parent { id }
"""


class LinearError(TrackerError):
    """Raised when the Linear API returns a non-2xx response or a GraphQL
    `errors` array, mirroring JiraError's shape (a single string message) —
    orchestrator.py/poller.py catch TrackerError, not this specifically, so
    this doesn't need JiraError's status_code field.
    """


def _split_identifier(key: str) -> tuple[str, int]:
    """"GAI-123" -> ("GAI", 123). Raises LinearError on anything that doesn't
    match — a bare int-less key is almost always a copy-paste of a Jira key
    into a Linear-backed instance by mistake, so the error says so.
    """
    team_key, _, number_part = key.rpartition("-")
    if not team_key or not number_part.isdigit():
        raise LinearError(
            f"{key!r} doesn't look like a Linear identifier (expected TEAM-123, "
            "e.g. 'GAI-5') — check SYMPHONY_WORKFLOW_TRACKER is set correctly "
            "for this issue"
        )
    return team_key.upper(), int(number_part)


class LinearClient:
    """Thin, testable wrapper around the Linear GraphQL API — the
    TrackerClient surface only (ADLC-57's explicit scope). No create_issue,
    no arbitrary-query search, no Epic/parent linking — those are genuinely
    Jira-shaped and stay JiraClient-only. See
    docs/plans/0005-linear-tracker-backend/intent.md's non-goals.
    """

    def __init__(self, config: LinearConfig, *, client: httpx.Client | None = None) -> None:
        self._config = config
        self._client = client or httpx.Client(
            base_url=LINEAR_API_URL,
            headers={"Content-Type": "application/json", "Authorization": config.api_key},
            timeout=30.0,
        )
        # Cached lazily — every issue_url() call would otherwise cost a
        # get_myself() round-trip. Cleared never: an org's urlKey doesn't
        # change for the lifetime of one client instance.
        self._org_url_key: str | None = None

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> LinearClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Core GraphQL executor
    # ------------------------------------------------------------------

    def _execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        kwargs: dict[str, Any] = {} if timeout is None else {"timeout": timeout}
        payload: dict[str, Any] = {"query": query}
        if variables:
            payload["variables"] = variables
        response = self._client.post("", json=payload, **kwargs)
        if response.status_code >= 400:
            raise LinearError(f"Linear API error {response.status_code}: {response.text}")
        body = response.json()
        if "errors" in body:
            messages = "; ".join(e.get("message", "Unknown error") for e in body["errors"])
            raise LinearError(messages)
        return body.get("data", {})

    # ------------------------------------------------------------------
    # TrackerClient surface
    # ------------------------------------------------------------------

    def get_myself(self, *, timeout: float | None = None) -> CurrentUser:
        data = self._execute(
            """
            query Viewer {
                viewer {
                    id
                    name
                    email
                    organization { urlKey }
                }
            }
            """,
            timeout=timeout,
        )
        viewer = data.get("viewer", {})
        org = viewer.get("organization") or {}
        if org.get("urlKey"):
            self._org_url_key = org["urlKey"]
        return CurrentUser.from_linear(viewer)

    def _resolve_org_url_key(self) -> str:
        if self._org_url_key is None:
            self.get_myself()
        if self._org_url_key is None:
            raise LinearError("Could not resolve organization urlKey from Linear")
        return self._org_url_key

    def issue_url(self, key: str) -> str:
        return f"https://linear.app/{self._resolve_org_url_key()}/issue/{key}"

    def get_issue(self, key: str) -> Issue:
        team_key, number = _split_identifier(key)
        data = self._execute(
            f"""
            query IssueByIdentifier($teamKey: String!, $number: Float!) {{
                issues(
                    filter: {{ team: {{ key: {{ eq: $teamKey }} }}, number: {{ eq: $number }} }}
                    first: 1
                ) {{
                    nodes {{ {_ISSUE_FIELDS} }}
                }}
            }}
            """,
            variables={"teamKey": team_key, "number": number},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        if not nodes:
            raise LinearError(f"Issue not found: {key}")
        return Issue.from_linear(nodes[0])

    def _resolve_issue_ids(self, key: str) -> tuple[str, str]:
        """(issue UUID, team UUID) — every write path needs the issue's
        internal id, and transition_issue additionally needs its team's id
        to look up that team's workflow states. One extra round-trip per
        write call; no cache, since a Run's handful of calls per issue over
        the life of a Plan/Implement pass doesn't justify one (unlike
        JiraClient, which doesn't need this resolution step at all — Jira's
        REST endpoints take the human-readable key directly).
        """
        team_key, number = _split_identifier(key)
        data = self._execute(
            """
            query IssueIds($teamKey: String!, $number: Float!) {
                issues(
                    filter: { team: { key: { eq: $teamKey } }, number: { eq: $number } }
                    first: 1
                ) {
                    nodes { id team { id } }
                }
            }
            """,
            variables={"teamKey": team_key, "number": number},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        if not nodes:
            raise LinearError(f"Issue not found: {key}")
        return nodes[0]["id"], nodes[0]["team"]["id"]

    def add_comment(self, key: str, body: str) -> None:
        issue_id, _team_id = self._resolve_issue_ids(key)
        data = self._execute(
            """
            mutation CommentCreate($input: CommentCreateInput!) {
                commentCreate(input: $input) { success }
            }
            """,
            variables={"input": {"issueId": issue_id, "body": body}},
        )
        if not data.get("commentCreate", {}).get("success"):
            raise LinearError(f"Failed to add comment to {key}")

    def _workflow_states(self, team_id: str) -> list[dict[str, Any]]:
        data = self._execute(
            """
            query WorkflowStates($teamId: ID!) {
                workflowStates(filter: { team: { id: { eq: $teamId } } }, first: 50) {
                    nodes { id name type }
                }
            }
            """,
            variables={"teamId": team_id},
        )
        return data.get("workflowStates", {}).get("nodes", [])

    def transition_issue(self, key: str, state_name: str) -> None:
        issue_id, team_id = self._resolve_issue_ids(key)
        states = self._workflow_states(team_id)
        match = next(
            (s for s in states if s.get("name", "").lower() == state_name.lower()), None
        )
        if match is None:
            available = ", ".join(s["name"] for s in states) or "none"
            raise LinearError(
                f"No workflow state named {state_name!r} for {key}'s team. "
                f"Available: {available}"
            )
        data = self._execute(
            """
            mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
                issueUpdate(id: $id, input: $input) { success }
            }
            """,
            variables={"id": issue_id, "input": {"stateId": match["id"]}},
        )
        if not data.get("issueUpdate", {}).get("success"):
            raise LinearError(f"Failed to transition {key} to {state_name!r}")

    def search_ready_tickets(self, *, max_results: int = 50) -> SearchResult:
        """TrackerClient's backend-agnostic "what should the poller auto-Plan
        next" query. Linear equivalent of Jira's JQL `status = "To Do" AND
        issuetype not in (Epic, Subtask)`: state.type "unstarted" (Linear's
        workflow-state category, not a specific state name — matches any
        team's "Todo"/"Backlog"-named state that's categorized unstarted) AND
        parent is null. Linear has no Epic issue type at all (a Linear
        "Project" is a separate object, not an Issue), so "not an Epic" is
        vacuous here; "not a Subtask" is expressed as "has no parent issue"
        instead, since Linear's sub-issue relationship is the closest analog.
        """
        data = self._execute(
            f"""
            query ReadyTickets($teamKey: String!, $first: Int!) {{
                issues(
                    filter: {{
                        team: {{ key: {{ eq: $teamKey }} }}
                        state: {{ type: {{ eq: "unstarted" }} }}
                        parent: {{ null: true }}
                    }}
                    orderBy: createdAt
                    first: $first
                ) {{
                    nodes {{ {_ISSUE_FIELDS} }}
                }}
            }}
            """,
            variables={"teamKey": self._config.team_key, "first": max_results},
        )
        nodes = data.get("issues", {}).get("nodes", [])
        issues = [Issue.from_linear(node) for node in nodes]
        return SearchResult(issues=issues, total=len(issues))
