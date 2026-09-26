"""Unit tests for config.py — JiraConfig.from_env() plus the ADLC-29 triage
env var helpers (JiraConfig itself already has behavioral coverage via
test_cli.py's `test_missing_env_vars_exit_with_helpful_message`; this file is
scoped to config.py's own free functions).
"""

from __future__ import annotations

import pytest

from symphony_workflow.config import (
    DEFAULT_TRIAGE_INTERVAL_SECONDS,
    ConfigError,
    LinearConfig,
    poll_plan_max_turns,
    triage_enabled,
    triage_interval_seconds,
)


@pytest.fixture(autouse=True)
def _clear_triage_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SYMPHONY_WORKFLOW_TRIAGE_ENABLED", raising=False)
    monkeypatch.delenv("SYMPHONY_WORKFLOW_TRIAGE_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("SYMPHONY_WORKFLOW_POLL_PLAN_MAX_TURNS", raising=False)


def test_triage_enabled_defaults_false() -> None:
    # Definition of done (docs/plans/0002-richer-polling-triage/intent.md):
    # must not change today's behavior for anyone who hasn't opted in.
    assert triage_enabled() is False


def test_triage_enabled_true_when_set(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRIAGE_ENABLED", "true")
    assert triage_enabled() is True


def test_triage_enabled_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRIAGE_ENABLED", "TRUE")
    assert triage_enabled() is True


def test_triage_enabled_false_for_other_values(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRIAGE_ENABLED", "yes")
    assert triage_enabled() is False


def test_triage_interval_seconds_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    assert triage_interval_seconds() == DEFAULT_TRIAGE_INTERVAL_SECONDS


def test_triage_interval_seconds_parses_as_float(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRIAGE_INTERVAL_SECONDS", "120")
    assert triage_interval_seconds() == 120.0
    assert isinstance(triage_interval_seconds(), float)


def test_poll_plan_max_turns_defaults_to_none() -> None:
    # ADLC-55 definition of done: unset must preserve today's behavior, i.e.
    # execute_plan/run_plan fall back to agent_runner.DEFAULT_PLAN_MAX_TURNS.
    assert poll_plan_max_turns() is None


def test_poll_plan_max_turns_parses_as_int(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_POLL_PLAN_MAX_TURNS", "15")
    assert poll_plan_max_turns() == 15
    assert isinstance(poll_plan_max_turns(), int)


def test_linear_config_from_env_reads_required_vars(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_abc")
    monkeypatch.setenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", "gai")
    config = LinearConfig.from_env()
    assert config.api_key == "lin_api_abc"
    # Team key is upper-cased -- Linear team keys are conventionally uppercase
    # (e.g. "GAI"), and a lowercase env var value shouldn't silently produce a
    # different, non-matching key.
    assert config.team_key == "GAI"


def test_linear_config_from_env_missing_vars_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    monkeypatch.delenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", raising=False)
    with pytest.raises(ConfigError, match="LINEAR_API_KEY"):
        LinearConfig.from_env()
