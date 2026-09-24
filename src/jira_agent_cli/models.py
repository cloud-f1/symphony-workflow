"""Pydantic models for Jira API request/response payloads."""

from __future__ import annotations

from typing import Any

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
