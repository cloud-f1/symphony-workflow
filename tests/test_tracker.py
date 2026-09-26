"""Unit tests for tracker.py's get_tracker_client() -- the one place
SYMPHONY_WORKFLOW_TRACKER is read (ADLC-57).
"""

from __future__ import annotations

import pytest

from symphony_workflow.client import JiraClient
from symphony_workflow.linear_client import LinearClient
from symphony_workflow.tracker import ConfigError, get_tracker_client


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", "GAI")


def test_defaults_to_jira_when_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYMPHONY_WORKFLOW_TRACKER", raising=False)
    with get_tracker_client() as client:
        assert isinstance(client, JiraClient)


def test_explicit_jira(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRACKER", "jira")
    with get_tracker_client() as client:
        assert isinstance(client, JiraClient)


def test_explicit_linear(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRACKER", "linear")
    with get_tracker_client() as client:
        assert isinstance(client, LinearClient)


def test_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRACKER", "LINEAR")
    with get_tracker_client() as client:
        assert isinstance(client, LinearClient)


def test_unknown_backend_raises_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRACKER", "shortcut")
    with pytest.raises(ConfigError, match="Unknown SYMPHONY_WORKFLOW_TRACKER"):
        get_tracker_client()
