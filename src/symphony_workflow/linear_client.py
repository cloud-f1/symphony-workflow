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
live 2026-09-26, matches another internal tool's own working Linear integration
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
    """ "GAI-123" -> ("GAI", 123). Raises LinearError on anything that doesn't
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
        match = next((s for s in states if s.get("name", "").lower() == state_name.lower()), None)
        if match is None:
            available = ", ".join(s["name"] for s in states) or "none"
            raise LinearError(
                f"No workflow state named {state_name!r} for {key}'s team. Available: {available}"
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

    # ------------------------------------------------------------------
    # Plan-sync writes (ADLC-203). Not on the TrackerClient Protocol on purpose: the
    # Plan/Implement loop never creates issues, and JiraClient has its own shape for this.
    # ------------------------------------------------------------------

    def resolve_team_id(self) -> str:
        data = self._execute(
            """
            query Team($key: String!) {
                teams(filter: { key: { eq: $key } }, first: 1) { nodes { id key } }
            }
            """,
            variables={"key": self._config.team_key},
        )
        nodes = data.get("teams", {}).get("nodes", [])
        if not nodes:
            raise LinearError(f"No Linear team with key {self._config.team_key!r}")
        return nodes[0]["id"]

    def find_project(self, name: str) -> dict[str, Any] | None:
        """The project called `name` (exact, case-insensitive) or None. Never creates."""
        data = self._execute(
            """
            query Project($name: String!) {
                projects(filter: { name: { eqIgnoreCase: $name } }, first: 1) {
                    nodes { id name }
                }
            }
            """,
            variables={"name": name},
        )
        nodes = data.get("projects", {}).get("nodes", [])
        return nodes[0] if nodes else None

    def create_project(self, name: str, team_id: str) -> dict[str, Any]:
        data = self._execute(
            """
            mutation ProjectCreate($input: ProjectCreateInput!) {
                projectCreate(input: $input) { success project { id name } }
            }
            """,
            variables={"input": {"name": name, "teamIds": [team_id]}},
        )
        result = data.get("projectCreate", {})
        if not result.get("success") or not result.get("project"):
            raise LinearError(f"Failed to create project {name!r}")
        return result["project"]

    def list_project_milestones(self, project_id: str) -> list[dict[str, Any]]:
        data = self._execute(
            """
            query Milestones($id: String!) {
                project(id: $id) {
                    projectMilestones(first: 100) { nodes { id name description } }
                }
            }
            """,
            variables={"id": project_id},
        )
        return (data.get("project") or {}).get("projectMilestones", {}).get("nodes", [])

    def list_project_issues(self, project_id: str) -> list[dict[str, Any]]:
        """Every issue in the project (sub-issues included), with what the sink compares."""
        nodes: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            data = self._execute(
                """
                query ProjectIssues($id: String!, $after: String) {
                    project(id: $id) {
                        issues(first: 100, after: $after) {
                            nodes {
                                id identifier title description
                                state { id name type }
                                parent { id }
                                projectMilestone { id }
                            }
                            pageInfo { hasNextPage endCursor }
                        }
                    }
                }
                """,
                variables={"id": project_id, "after": cursor},
            )
            page = (data.get("project") or {}).get("issues", {})
            nodes.extend(page.get("nodes", []))
            info = page.get("pageInfo") or {}
            if not info.get("hasNextPage"):
                return nodes
            cursor = info.get("endCursor")

    def create_project_milestone(
        self, project_id: str, name: str, description: str
    ) -> dict[str, Any]:
        data = self._execute(
            """
            mutation MilestoneCreate($input: ProjectMilestoneCreateInput!) {
                projectMilestoneCreate(input: $input) {
                    success projectMilestone { id name description }
                }
            }
            """,
            variables={
                "input": {"projectId": project_id, "name": name, "description": description}
            },
        )
        result = data.get("projectMilestoneCreate", {})
        if not result.get("success") or not result.get("projectMilestone"):
            raise LinearError(f"Failed to create milestone {name!r}")
        return result["projectMilestone"]

    def update_project_milestone(self, milestone_id: str, name: str, description: str) -> None:
        data = self._execute(
            """
            mutation MilestoneUpdate($id: String!, $input: ProjectMilestoneUpdateInput!) {
                projectMilestoneUpdate(id: $id, input: $input) { success }
            }
            """,
            variables={"id": milestone_id, "input": {"name": name, "description": description}},
        )
        if not data.get("projectMilestoneUpdate", {}).get("success"):
            raise LinearError(f"Failed to update milestone {milestone_id}")

    def create_plan_issue(
        self,
        *,
        team_id: str,
        title: str,
        description: str,
        project_id: str | None = None,
        milestone_id: str | None = None,
        parent_id: str | None = None,
        state_id: str | None = None,
    ) -> dict[str, Any]:
        """One issue or, with `parent_id`, a sub-issue. Returns `{id, identifier}`."""
        fields = {
            "projectId": project_id,
            "projectMilestoneId": milestone_id,
            "parentId": parent_id,
            "stateId": state_id,
        }
        issue_input: dict[str, Any] = {
            "teamId": team_id,
            "title": title,
            "description": description,
        }
        issue_input.update({k: v for k, v in fields.items() if v})
        data = self._execute(
            """
            mutation IssueCreate($input: IssueCreateInput!) {
                issueCreate(input: $input) { success issue { id identifier } }
            }
            """,
            variables={"input": issue_input},
        )
        result = data.get("issueCreate", {})
        if not result.get("success") or not result.get("issue"):
            raise LinearError(f"Failed to create issue {title!r}")
        return result["issue"]

    def update_plan_issue(self, issue_id: str, *, title: str, description: str) -> None:
        """Title and description only; the state is left to the tracker (two-way sync owns it)."""
        data = self._execute(
            """
            mutation IssueUpdate($id: String!, $input: IssueUpdateInput!) {
                issueUpdate(id: $id, input: $input) { success }
            }
            """,
            variables={"id": issue_id, "input": {"title": title, "description": description}},
        )
        if not data.get("issueUpdate", {}).get("success"):
            raise LinearError(f"Failed to update issue {issue_id}")

    def team_workflow_states(self, team_id: str) -> list[dict[str, Any]]:
        return self._workflow_states(team_id)

    def set_issue_state(self, issue_id: str, state_id: str) -> None:
        """Move one issue to a workflow state by id (the plan-sync status push, ADLC-204)."""
        data = self._execute(
            """
            mutation IssueState($id: String!, $input: IssueUpdateInput!) {
                issueUpdate(id: $id, input: $input) { success }
            }
            """,
            variables={"id": issue_id, "input": {"stateId": state_id}},
        )
        if not data.get("issueUpdate", {}).get("success"):
            raise LinearError(f"Failed to set the state of issue {issue_id}")
