"""Unit tests for the tool-call dispatch layer — the agent-facing integration surface."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from symphony_workflow.models import CurrentUser, Issue, IssueCreateResult
from symphony_workflow.tools import TOOLS, call_tool


def test_tools_registry_has_expected_names() -> None:
    assert set(TOOLS) == {
        "get_issue",
        "search_issues",
        "create_issue",
        "update_issue",
        "add_comment",
        "transition_issue",
        "get_myself",
        "create_remote_link",
    }


def test_every_tool_produces_a_valid_json_schema() -> None:
    for tool in TOOLS.values():
        schema = tool.json_schema()
        assert schema["name"] == tool.name
        assert schema["description"]
        assert "properties" in schema["input_schema"]


def test_call_tool_dispatches_get_issue() -> None:
    client = MagicMock()
    client.get_issue.return_value = Issue(key="CF1-1", id="1", summary="Test", status="Todo")
    result = call_tool(client, "get_issue", {"key": "CF1-1"})
    client.get_issue.assert_called_once_with("CF1-1")
    assert result["key"] == "CF1-1"


def test_call_tool_dispatches_get_myself() -> None:
    client = MagicMock()
    client.get_myself.return_value = CurrentUser(account_id="abc", display_name="Bot")
    result = call_tool(client, "get_myself", {})
    client.get_myself.assert_called_once_with()
    assert result == {
        "account_id": "abc",
        "display_name": "Bot",
        "email_address": None,
        "active": True,
    }


def test_call_tool_dispatches_create_issue_with_defaults() -> None:
    client = MagicMock()
    client.create_issue.return_value = IssueCreateResult(
        key="CF1-9", id="9", url="https://x/browse/CF1-9"
    )
    result = call_tool(client, "create_issue", {"project_key": "CF1", "summary": "New"})
    client.create_issue.assert_called_once_with(
        "CF1",
        "New",
        None,
        "Task",
        labels=None,
        epic=None,
        parent=None,
        sprint_id=None,
        story_points=None,
        assignee_account_id=None,
    )
    assert result["key"] == "CF1-9"


def test_call_tool_dispatches_create_issue_with_parent() -> None:
    client = MagicMock()
    client.create_issue.return_value = IssueCreateResult(
        key="ADLC-50", id="50", url="https://x/browse/ADLC-50"
    )
    result = call_tool(
        client,
        "create_issue",
        {
            "project_key": "ADLC",
            "summary": "A subtask",
            "issue_type": "Subtask",
            "parent": "ADLC-45",
        },
    )
    client.create_issue.assert_called_once_with(
        "ADLC",
        "A subtask",
        None,
        "Subtask",
        labels=None,
        epic=None,
        parent="ADLC-45",
        sprint_id=None,
        story_points=None,
        assignee_account_id=None,
    )
    assert result["key"] == "ADLC-50"


def test_call_tool_dispatches_update_issue() -> None:
    client = MagicMock()
    result = call_tool(client, "update_issue", {"key": "ADLC-28", "summary": "New title"})
    client.update_issue.assert_called_once_with("ADLC-28", summary="New title", description=None)
    assert result == {"ok": True, "key": "ADLC-28"}


def test_call_tool_dispatches_transition_issue() -> None:
    client = MagicMock()
    result = call_tool(client, "transition_issue", {"key": "CF1-1", "transition_name": "Done"})
    client.transition_issue.assert_called_once_with("CF1-1", "Done")
    assert result == {"ok": True, "key": "CF1-1", "transition": "Done"}


def test_call_tool_dispatches_create_remote_link() -> None:
    client = MagicMock()
    result = call_tool(
        client,
        "create_remote_link",
        {
            "key": "ADLC-17",
            "url": "https://github.com/x/y/blob/main/docs/runs/ADLC-17/review.md",
            "title": "review.md",
        },
    )
    client.create_remote_link.assert_called_once_with(
        "ADLC-17",
        url="https://github.com/x/y/blob/main/docs/runs/ADLC-17/review.md",
        title="review.md",
    )
    assert result == {
        "ok": True,
        "key": "ADLC-17",
        "url": "https://github.com/x/y/blob/main/docs/runs/ADLC-17/review.md",
    }


def test_call_tool_raises_on_unknown_tool_name() -> None:
    client = MagicMock()
    with pytest.raises(KeyError, match="Unknown tool"):
        call_tool(client, "delete_everything", {})


def test_call_tool_validates_args_against_schema() -> None:
    client = MagicMock()
    with pytest.raises(ValidationError):
        call_tool(client, "get_issue", {})  # missing required "key"
    client.get_issue.assert_not_called()
