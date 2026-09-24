"""Unit tests for orchestrator.py helpers not already covered indirectly via
test_api.py (execute_plan/execute_implement) and test_poller.py.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from jira_agent_cli.client import JiraError
from jira_agent_cli.orchestrator import (
    cleanup_workspace_if_terminal,
    jira_safe_comment_body,
    render_run_doc,
    sweep_terminal_workspaces,
)


@pytest.fixture(autouse=True)
def _clear_cleanup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # cleanup_workspace_if_terminal/sweep_terminal_workspaces both read env
    # vars at call time (see orchestrator._cleanup_enabled) — start every test
    # from the documented off-by-default state, same reasoning as
    # test_cli.py's env-clearing fixture.
    monkeypatch.delenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", raising=False)
    monkeypatch.delenv("JIRA_AGENT_CLI_TERMINAL_STATUSES", raising=False)


def _issue(status: str) -> MagicMock:
    issue = MagicMock()
    issue.status = status
    return issue


def test_cleanup_disabled_by_default_never_calls_jira() -> None:
    client = MagicMock()
    workspaces = MagicMock()

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is False
    client.get_issue.assert_not_called()
    workspaces.remove.assert_not_called()


def test_cleanup_removes_workspace_when_status_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    client.get_issue.return_value = _issue("Done")
    workspaces = MagicMock()
    workspaces.remove.return_value = True

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is True
    workspaces.remove.assert_called_once_with("ADLC-1")


def test_cleanup_leaves_non_terminal_workspace_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    client.get_issue.return_value = _issue("In Progress")
    workspaces = MagicMock()

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is False
    workspaces.remove.assert_not_called()


def test_cleanup_respects_custom_terminal_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("JIRA_AGENT_CLI_TERMINAL_STATUSES", "Done, Won't Do")
    client = MagicMock()
    client.get_issue.return_value = _issue("Won't Do")
    workspaces = MagicMock()
    workspaces.remove.return_value = True

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is True


def test_cleanup_swallows_jira_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    client.get_issue.side_effect = JiraError(500, "boom")
    workspaces = MagicMock()

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is False
    workspaces.remove.assert_not_called()


def test_sweep_disabled_by_default_does_nothing(tmp_path: Path) -> None:
    (tmp_path / "ADLC-1").mkdir()
    client = MagicMock()
    workspaces = MagicMock()
    workspaces.root = tmp_path

    removed = sweep_terminal_workspaces(client=client, workspaces=workspaces)

    assert removed == []
    client.get_issue.assert_not_called()


def test_sweep_removes_only_terminal_issues_and_skips_non_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    (tmp_path / "ADLC-1").mkdir()
    (tmp_path / "ADLC-2").mkdir()
    (tmp_path / "not-a-workspace.txt").write_text("stray file")

    client = MagicMock()
    statuses = {"ADLC-1": "Done", "ADLC-2": "In Progress"}
    client.get_issue.side_effect = lambda key: _issue(statuses[key])

    workspaces = MagicMock()
    workspaces.root = tmp_path
    workspaces.remove.return_value = True

    removed = sweep_terminal_workspaces(client=client, workspaces=workspaces)

    assert removed == ["ADLC-1"]
    workspaces.remove.assert_called_once_with("ADLC-1")


def test_sweep_returns_empty_list_when_workspace_root_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    workspaces = MagicMock()
    workspaces.root = tmp_path / "does-not-exist"

    assert sweep_terminal_workspaces(client=client, workspaces=workspaces) == []


def test_render_run_doc_prepends_jira_url_frontmatter() -> None:
    client = MagicMock()
    client.base_url = "https://cloud-f1.atlassian.net"

    doc = render_run_doc(client, "ADLC-17", "Here is the plan.")

    assert doc == (
        "---\njira_url: https://cloud-f1.atlassian.net/browse/ADLC-17\n---\n\nHere is the plan."
    )


def test_render_run_doc_frontmatter_is_parseable_yaml_prefix() -> None:
    client = MagicMock()
    client.base_url = "https://cloud-f1.atlassian.net"

    doc = render_run_doc(client, "ADLC-24", "body text")
    lines = doc.splitlines()

    assert lines[0] == "---"
    assert lines[1] == "jira_url: https://cloud-f1.atlassian.net/browse/ADLC-24"
    assert lines[2] == "---"


def test_jira_safe_comment_body_converts_atx_headers_to_bold() -> None:
    # Reproduces the garbled ADLC-28/ADLC-30 comment: Jira's wiki-markup
    # renderer treats a leading '#'/'##'/'###' as nested ordered-list depth,
    # not a heading, so raw GFM headers must become bold lines instead.
    text = "## Plan: ADLC-30\n\n### Context\nSome body text.\n#### Deep\nmore"

    result = jira_safe_comment_body(text)

    assert result == ("**Plan: ADLC-30**\n\n**Context**\nSome body text.\n**Deep**\nmore")
    assert "#" not in result


def test_jira_safe_comment_body_leaves_non_header_text_untouched() -> None:
    text = "**already bold** and `inline code` and a normal line\n- a bullet"

    result = jira_safe_comment_body(text)

    assert result == text
