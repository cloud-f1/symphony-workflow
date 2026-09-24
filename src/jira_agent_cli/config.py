"""Runtime configuration loaded from environment variables.

Only `JIRA_BASE_URL` / `JIRA_EMAIL` / `JIRA_API_TOKEN` are required. The three
`JIRA_FIELD_*` variables are optional custom-field IDs — Jira custom field IDs
are per-project, not global, so `create_issue` only sends them when set.
"""

from __future__ import annotations

import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised when required Jira configuration is missing."""


@dataclass(frozen=True)
class JiraConfig:
    base_url: str
    email: str
    api_token: str
    epic_link_field: str | None = None
    sprint_field: str | None = None
    story_points_field: str | None = None

    @classmethod
    def from_env(cls) -> JiraConfig:
        base_url = os.environ.get("JIRA_BASE_URL", "").rstrip("/")
        email = os.environ.get("JIRA_EMAIL", "")
        api_token = os.environ.get("JIRA_API_TOKEN", "")
        missing = [
            name
            for name, value in (
                ("JIRA_BASE_URL", base_url),
                ("JIRA_EMAIL", email),
                ("JIRA_API_TOKEN", api_token),
            )
            if not value
        ]
        if missing:
            raise ConfigError(f"Missing required env var(s): {', '.join(missing)}")
        return cls(
            base_url=base_url,
            email=email,
            api_token=api_token,
            epic_link_field=os.environ.get("JIRA_FIELD_EPIC_LINK") or None,
            sprint_field=os.environ.get("JIRA_FIELD_SPRINT") or None,
            story_points_field=os.environ.get("JIRA_FIELD_STORY_POINTS") or None,
        )
