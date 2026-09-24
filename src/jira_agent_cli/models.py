"""Pydantic models for Jira API request/response payloads."""

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
