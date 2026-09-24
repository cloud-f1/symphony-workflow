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


# ADLC-29 triage env vars (docs/plans/0002-richer-polling-triage/spec.md).
# Centralized here per that design's implement.md step 7 rather than read ad
# hoc in poller.py/api.py — plain module-level functions (not JiraConfig
# fields) since these are poller behavior toggles, not Jira credentials, and
# `poller.py`/`orchestrator.py` already have their own precedent for this
# shape (e.g. orchestrator._cleanup_enabled()). Read at call time, not import
# time, so tests can monkeypatch/setenv per-test.
TRIAGE_ENABLED_ENV = "JIRA_AGENT_CLI_TRIAGE_ENABLED"
TRIAGE_INTERVAL_ENV = "JIRA_AGENT_CLI_TRIAGE_INTERVAL_SECONDS"

# Deliberately >> the 60s poll interval (spec.md's design) -- triage is a
# slow-cadence, budget-capped batch step, not a per-tick one.
DEFAULT_TRIAGE_INTERVAL_SECONDS = 900.0


def triage_enabled() -> bool:
    """Default `false` -- must not change today's poller behavior for anyone
    who hasn't opted in (definition of done, docs/plans/0002-richer-polling-
    triage/intent.md).
    """
    return os.environ.get(TRIAGE_ENABLED_ENV, "false").lower() == "true"


def triage_interval_seconds() -> float:
    raw = os.environ.get(TRIAGE_INTERVAL_ENV)
    if not raw:
        return DEFAULT_TRIAGE_INTERVAL_SECONDS
    return float(raw)
