"""Pydantic models for tracker API request/response payloads.

Shared by both backends (ADLC-57) -- `Issue`/`SearchResult`/`CurrentUser` are
generic enough that Jira's REST shape and Linear's GraphQL shape both parse
into the same fields (`Issue.key` is Jira's `key` / Linear's `identifier`,
both human-readable "PREFIX-123"; `Issue.id` is each API's own internal id).
`IssueCreateResult`/`Transition` stay Jira-only -- Linear's create/transition
paths don't go through these types (see client.py vs linear_client.py).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class Issue(BaseModel):
    key: str
    id: str
    summary: str
    status: str
    assignee: str | None = None
    description: str | None = None

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> Issue:
        fields = payload.get("fields", {}) or {}
        assignee = fields.get("assignee")
        status = fields.get("status") or {}
        return cls(
            key=payload["key"],
            id=payload["id"],
            summary=fields.get("summary", ""),
            status=status.get("name", ""),
            assignee=assignee.get("displayName") if assignee else None,
            # v2 API returns description as plain text (not ADF) — see client.py.
            description=fields.get("description") or None,
        )

    @classmethod
    def from_linear(cls, payload: dict[str, Any]) -> Issue:
        """`payload` is one node from a Linear `issue`/`issues` GraphQL query
        (see linear_client.py's ISSUE_FIELDS) -- `identifier` is the
        human-readable "TEAM-123" (Jira's `key` equivalent), `id` is Linear's
        internal UUID (Jira's `id` equivalent, also internal/numeric there).
        """
        state = payload.get("state") or {}
        assignee = payload.get("assignee")
        return cls(
            key=payload["identifier"],
            id=payload["id"],
            summary=payload.get("title", ""),
            status=state.get("name", ""),
            assignee=assignee.get("name") if assignee else None,
            description=payload.get("description") or None,
        )


class HierarchyIssue(BaseModel):
    """One issue as the roll-up logic sees it (rollup.py): where it sits in the
    Epic -> Story -> Subtask tree and whether it is done. Jira-only -- Linear has no
    `parent`-field hierarchy of this shape.

    `status_category` is Jira's own `statusCategory.key` ("new" / "indeterminate" /
    "done") -- the workflow-independent way to ask "is it done", unlike the free-form
    `status` name. `hierarchy_level` is `issuetype.hierarchyLevel` (-1 subtask, 0 story/
    task, 1 epic) when Jira returns it, else None (rollup.py then falls back to the
    issue type's name).
    """

    key: str
    summary: str = ""
    status: str = ""
    status_category: str | None = None
    issue_type: str = ""
    hierarchy_level: int | None = None
    parent_key: str | None = None

    @property
    def is_done(self) -> bool:
        if self.status_category is not None:
            return self.status_category == "done"
        return self.status.strip().lower() == "done"

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> HierarchyIssue:
        fields = payload.get("fields", {}) or {}
        status = fields.get("status") or {}
        category = status.get("statusCategory") or {}
        issue_type = fields.get("issuetype") or {}
        parent = fields.get("parent") or {}
        level = issue_type.get("hierarchyLevel")
        return cls(
            key=payload["key"],
            summary=fields.get("summary", "") or "",
            status=status.get("name", "") or "",
            status_category=category.get("key"),
            issue_type=issue_type.get("name", "") or "",
            hierarchy_level=level if isinstance(level, int) else None,
            parent_key=parent.get("key") if parent else None,
        )


class SearchResult(BaseModel):
    issues: list[Issue]
    total: int


class IssueCreateResult(BaseModel):
    key: str
    id: str
    url: str


class Transition(BaseModel):
    id: str
    name: str


class TriageDecision(BaseModel):
    """One triage agent decision for a single candidate issue (ADLC-29,
    docs/plans/0002-richer-polling-triage/spec.md). Crosses the agent-output
    boundary (structured output from `agent_runner.run_triage`), same
    reasoning as `Run`/`decompose.Decomposition` for why this is a pydantic
    model rather than a plain dict: it's validated once here, then trusted by
    `orchestrator.execute_triage` and `poller.py` without re-checking shape.
    """

    issue_key: str
    action: Literal["plan", "skip", "flag"]
    reason: str


class CurrentUser(BaseModel):
    """The authenticated user, from `GET /rest/api/2/myself`."""

    account_id: str
    display_name: str
    email_address: str | None = None
    active: bool = True

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> CurrentUser:
        return cls(
            account_id=payload["accountId"],
            display_name=payload.get("displayName", ""),
            # Jira Cloud omits emailAddress when the user's profile visibility hides it.
            email_address=payload.get("emailAddress") or None,
            active=payload.get("active", True),
        )

    @classmethod
    def from_linear(cls, payload: dict[str, Any]) -> CurrentUser:
        """`payload` is Linear's `viewer` GraphQL query result. Linear has no
        `active` concept for the viewer (you can't authenticate as a
        deactivated user) -- always True, matching Jira's own default.
        """
        return cls(
            account_id=payload["id"],
            display_name=payload.get("name", ""),
            email_address=payload.get("email") or None,
            active=True,
        )
