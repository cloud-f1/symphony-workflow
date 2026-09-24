"""Unit tests for the CLI entrypoint (Typer's CliRunner — no live Jira access)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from jira_agent_cli.cli import app

runner = CliRunner()


def test_list_tools_prints_all_registered_tool_schemas() -> None:
    result = runner.invoke(app, ["list-tools"])
    assert result.exit_code == 0
    schemas = json.loads(result.stdout)
    names = {schema["name"] for schema in schemas}
    assert {"get_issue", "create_issue", "search_issues"} <= names


def test_call_rejects_invalid_json_args() -> None:
    result = runner.invoke(app, ["call", "get_issue", "{not json"])
    assert result.exit_code == 1
    assert "Invalid JSON" in result.output


def test_missing_env_vars_exit_with_helpful_message(monkeypatch) -> None:
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    result = runner.invoke(app, ["get", "CF1-1"])
    assert result.exit_code == 1
    assert "Missing required env var" in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Python CLI for Jira Cloud" in result.output
