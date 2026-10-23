"""Unit tests for the CLI entrypoint (Typer's CliRunner — no live Jira access).

`plan`/`implement` tests mock the same two points test_api.py does: Jira HTTP
via respx, and the Claude Agent SDK (`run_plan`/`run_implement`) monkeypatched
on `orchestrator` — where cli.py's commands actually call them from — plus a
fake WorkspaceManager so no real `git worktree add` runs against this repo.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import respx
from typer.testing import CliRunner

import symphony_workflow.cli as cli_module
import symphony_workflow.decompose as decompose_module
import symphony_workflow.orchestrator as orchestrator_module
from symphony_workflow.agent_runner import AgentRunResult
from symphony_workflow.cli import app
from symphony_workflow.decompose import Decomposition, Story, Task

from .conftest import BASE_URL

runner = CliRunner()


class _FakeWorkspaceManager:
    def __init__(self, *args: object, **kwargs: object) -> None:
        pass

    def ensure(self, issue_key: str, *, branch_slug: str | None = None) -> Path:
        return Path("/tmp/fake-workspace") / issue_key

    def run_before_run(self, issue_key: str, workspace_path: Path) -> None:
        pass

    def run_after_run(self, issue_key: str, workspace_path: Path) -> None:
        pass


def _mock_transition(key: str, name: str) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": name}]})
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(204)
    )


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


def test_jira_only_command_rejects_linear_tracker_setting(monkeypatch) -> None:
    # ADLC-106: real, deliberate behavior fix -- _jira_client() used to
    # construct a fresh JiraClient directly, silently ignoring
    # SYMPHONY_WORKFLOW_TRACKER entirely. Now it goes through
    # get_tracker_client() + require_jira(), so a Linear-configured
    # deployment gets a clear error instead of quietly using Jira anyway.
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRACKER", "linear")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", "GAI")
    # Valid Jira env vars are ALSO present -- the old behavior would have
    # happily used them; the fixed behavior must reject this command anyway.
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")

    result = runner.invoke(app, ["search", "project = ADLC"])

    assert result.exit_code == 1
    assert "Jira-only" in result.output


def test_jira_only_command_missing_env_vars_exits_with_helpful_message(monkeypatch) -> None:
    # ADLC-95: _jira_client()'s own ConfigError path -- distinct from
    # _client()'s (tested above via `get`). `search` is Jira-only regardless
    # of SYMPHONY_WORKFLOW_TRACKER, so this exercises _jira_client() directly.
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    result = runner.invoke(app, ["search", "project = ADLC"])
    assert result.exit_code == 1
    assert "Missing required env var" in result.output


def _set_jira_env(monkeypatch) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")


@respx.mock
def test_get_issue_happy_path(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "Do the thing", "status": {"name": "To Do"}},
            },
        )
    )
    result = runner.invoke(app, ["get", "ADLC-1"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["key"] == "ADLC-1"


@respx.mock
def test_get_issue_tracker_error_exits_nonzero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.get(f"{BASE_URL}/rest/api/2/issue/NOPE-1").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
    )
    result = runner.invoke(app, ["get", "NOPE-1"])
    assert result.exit_code == 1
    assert "not found" in result.output


@respx.mock
def test_search_issues_happy_path(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "issues": [
                    {
                        "key": "ADLC-1",
                        "id": "1",
                        "fields": {"summary": "Do the thing", "status": {"name": "To Do"}},
                    }
                ],
            },
        )
    )
    result = runner.invoke(app, ["search", "project = ADLC"])
    assert result.exit_code == 0, result.output
    assert "ADLC-1" in result.output
    assert "1 total" in result.output


@respx.mock
def test_search_issues_jira_error_exits_nonzero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(400, json={"errorMessages": ["bad jql"]})
    )
    result = runner.invoke(app, ["search", "not jql"])
    assert result.exit_code == 1
    assert "bad jql" in result.output


@respx.mock
def test_add_comment_happy_path(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    result = runner.invoke(app, ["comment", "ADLC-1", "Looks good"])
    assert result.exit_code == 0, result.output
    assert "Comment added to ADLC-1" in result.output


@respx.mock
def test_add_comment_tracker_error_exits_nonzero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.post(f"{BASE_URL}/rest/api/2/issue/NOPE-1/comment").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
    )
    result = runner.invoke(app, ["comment", "NOPE-1", "x"])
    assert result.exit_code == 1
    assert "not found" in result.output


@respx.mock
def test_transition_issue_happy_path(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    _mock_transition("ADLC-1", "Done")
    result = runner.invoke(app, ["transition", "ADLC-1", "done"])
    assert result.exit_code == 0, result.output
    assert "ADLC-1 -> done" in result.output


@respx.mock
def test_transition_issue_tracker_error_exits_nonzero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": "Done"}]})
    )
    result = runner.invoke(app, ["transition", "ADLC-1", "Cancelled"])
    assert result.exit_code == 1
    assert "No transition named" in result.output


@respx.mock
def test_call_invokes_registered_tool_successfully(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "Do the thing", "status": {"name": "To Do"}},
            },
        )
    )
    result = runner.invoke(app, ["call", "get_issue", '{"key": "ADLC-1"}'])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["key"] == "ADLC-1"


def test_call_unknown_tool_exits_nonzero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    result = runner.invoke(app, ["call", "not_a_real_tool", "{}"])
    assert result.exit_code == 1
    assert "Unknown tool" in result.output


@respx.mock
def test_call_jira_error_exits_nonzero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.get(f"{BASE_URL}/rest/api/2/issue/NOPE-1").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
    )
    result = runner.invoke(app, ["call", "get_issue", '{"key": "NOPE-1"}'])
    assert result.exit_code == 1
    assert "not found" in result.output


def test_no_args_shows_help() -> None:
    result = runner.invoke(app, [])
    assert "Python CLI for Jira Cloud" in result.output


@respx.mock
def test_plan_command_saves_run_sidecar_for_implement(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(orchestrator_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WorkspaceManager", _FakeWorkspaceManager)

    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "Do the thing", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-1", "In Review")

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="the plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)

    result = runner.invoke(app, ["plan", "ADLC-1"])

    assert result.exit_code == 0, result.output
    assert "Plan ready for ADLC-1" in result.output
    sidecar = tmp_path / "ADLC-1" / "run.json"
    assert sidecar.exists()
    saved = json.loads(sidecar.read_text())
    assert saved["state"] == "plan_ready"
    assert saved["session_id"] == "sess-1"


@respx.mock
def test_plan_command_max_turns_option_reaches_run_plan(monkeypatch, tmp_path: Path) -> None:
    """ADLC-55: `--max-turns` must reach `agent_runner.run_plan` unchanged."""
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(orchestrator_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WorkspaceManager", _FakeWorkspaceManager)

    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "Do the thing", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-1", "In Review")

    received: dict[str, object] = {}

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        received.update(kwargs)
        return AgentRunResult(text="the plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)

    result = runner.invoke(app, ["plan", "ADLC-1", "--max-turns", "15"])

    assert result.exit_code == 0, result.output
    assert received.get("max_turns") == 15


@respx.mock
def test_plan_command_no_max_turns_option_omits_override(monkeypatch, tmp_path: Path) -> None:
    """ADLC-55: without `--max-turns` (today's behavior), `run_plan` must not
    receive a `max_turns` kwarg at all.
    """
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(orchestrator_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WorkspaceManager", _FakeWorkspaceManager)

    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "Do the thing", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-1", "In Review")

    received_kwargs: dict[str, object] = {}

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        received_kwargs.update(kwargs)
        return AgentRunResult(text="the plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)

    result = runner.invoke(app, ["plan", "ADLC-1"])

    assert result.exit_code == 0, result.output
    assert "max_turns" not in received_kwargs


@respx.mock
def test_plan_command_tracker_error_exits_nonzero(monkeypatch, tmp_path: Path) -> None:
    # ADLC-95: execute_plan's very first call is client.get_issue -- a 404
    # there raises TrackerError before any workspace/agent work happens,
    # the cheapest real way to exercise cli.py's plan-command except branch.
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(orchestrator_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WorkspaceManager", _FakeWorkspaceManager)

    respx.get(f"{BASE_URL}/rest/api/2/issue/NOPE-1").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
    )

    result = runner.invoke(app, ["plan", "NOPE-1"])

    assert result.exit_code == 1
    assert "not found" in result.output


@respx.mock
def test_implement_command_resumes_from_saved_run(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)

    sidecar_dir = tmp_path / "ADLC-1"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "run.json").write_text(
        json.dumps(
            {
                "id": "run-1",
                "issue_key": "ADLC-1",
                "state": "plan_ready",
                "created_at": "2026-01-01T00:00:00Z",
                "plan": "the plan",
                "session_id": "sess-1",
                "workspace_path": "/tmp/fake-workspace/ADLC-1",
                "result": None,
                "error": None,
            }
        )
    )

    _mock_transition("ADLC-1", "In Progress")

    async def fake_run_implement(
        prompt: str, *, cwd: str, resume_session_id: str, **kwargs: object
    ) -> AgentRunResult:
        return AgentRunResult(text="done", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_implement", fake_run_implement)

    result = runner.invoke(app, ["implement", "ADLC-1"])

    assert result.exit_code == 0, result.output
    assert "state: completed" in result.output
    saved = json.loads((sidecar_dir / "run.json").read_text())
    assert saved["state"] == "completed"
    assert saved["result"] == "done"


def test_implement_command_without_saved_run_exits_with_helpful_message(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)

    result = runner.invoke(app, ["implement", "ADLC-99"])

    assert result.exit_code == 1
    assert "No saved run for ADLC-99" in result.output
    assert "symphony-workflow plan ADLC-99" in result.output


def test_implement_command_wrong_run_state_exits_nonzero(monkeypatch, tmp_path: Path) -> None:
    # ADLC-95: cli.py's implement except clause also catches RunTransitionError
    # -- the realistic one execute_implement actually raises (its own
    # TrackerError-raising calls are all best-effort/swallowed internally,
    # see orchestrator.execute_implement); a saved run not in plan_ready
    # triggers RunStore.mark_implementing's own guard.
    _set_jira_env(monkeypatch)
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)

    sidecar_dir = tmp_path / "ADLC-1"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "run.json").write_text(
        json.dumps(
            {
                "id": "run-1",
                "issue_key": "ADLC-1",
                "state": "created",
                "created_at": "2026-01-01T00:00:00Z",
                "plan": None,
                "session_id": None,
                "workspace_path": None,
                "result": None,
                "error": None,
            }
        )
    )

    result = runner.invoke(app, ["implement", "ADLC-1"])

    assert result.exit_code == 1
    assert "expected 'plan_ready'" in result.output


@respx.mock
def test_decompose_command_renders_decomposition_for_review(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WorkspaceManager", _FakeWorkspaceManager)

    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-27").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-27",
                "id": "1",
                "fields": {
                    "summary": "Symphony POC",
                    "status": {"name": "In Progress"},
                    "description": "Build the whole thing.",
                },
            },
        )
    )

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(
            text="decomposed",
            session_id="sess-1",
            cost_usd=0.02,
            num_turns=2,
            structured={
                "stories": [
                    {
                        "title": "Story A",
                        "user_story": "As a user, I want A.",
                        "acceptance_criteria": ["A works"],
                        "tasks": [{"title": "Build A", "description": "Do it"}],
                    }
                ]
            },
        )

    monkeypatch.setattr(decompose_module, "run_plan", fake_run_plan)

    result = runner.invoke(app, ["decompose", "ADLC-27"])

    assert result.exit_code == 0, result.output
    assert "Decomposition ready for ADLC-27: 1 stories" in result.output

    md_path = tmp_path / "ADLC-27" / "decomposition.md"
    assert md_path.exists()
    assert "## Story 1: Story A" in md_path.read_text()
    assert (tmp_path / "ADLC-27" / "decomposition.json").exists()

    # No Jira writes -- only the GET above should have been called.
    assert len(respx.calls) == 1


def test_decompose_command_reports_jira_error_and_exits_nonzero(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    monkeypatch.setattr(cli_module, "WorkspaceManager", _FakeWorkspaceManager)

    with respx.mock:
        respx.get(f"{BASE_URL}/rest/api/2/issue/NOPE-1").mock(
            return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
        )
        result = runner.invoke(app, ["decompose", "NOPE-1"])

    assert result.exit_code == 1


_SYNC_DECOMPOSITION = Decomposition(
    epic_key="ADLC-27",
    stories=[
        Story(
            title="Story A",
            user_story="As a user, I want A.",
            acceptance_criteria=["A works"],
            tasks=[Task(title="Build A", description="Do it")],
        )
    ],
    definition_of_done=["Tests pass"],
)


def _write_sync_decomposition(tmp_path: Path) -> None:
    out_dir = tmp_path / "ADLC-27"
    out_dir.mkdir(parents=True)
    (out_dir / "decomposition.json").write_text(
        _SYNC_DECOMPOSITION.model_dump_json(indent=2), encoding="utf-8"
    )


@respx.mock
def test_sync_decomposition_command_creates_story_and_subtask(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    _write_sync_decomposition(tmp_path)
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [], "isLast": True})
    )

    responses = iter(
        [
            httpx.Response(201, json={"key": "ADLC-101", "id": "101"}),
            httpx.Response(201, json={"key": "ADLC-102", "id": "102"}),
        ]
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(side_effect=lambda request: next(responses))

    result = runner.invoke(app, ["sync-decomposition", "ADLC-27"])

    assert result.exit_code == 0, result.output
    assert "Synced decomposition for ADLC-27 into project ADLC: 1 stories created" in result.output
    assert "Story ADLC-101: Story A" in result.output
    assert "Subtask ADLC-102: Build A" in result.output
    assert len(respx.calls) == 3  # children check + story + subtask

    story_call = json.loads(respx.calls[1].request.content)
    assert story_call["fields"]["parent"] == {"key": "ADLC-27"}
    task_call = json.loads(respx.calls[2].request.content)
    assert task_call["fields"]["parent"] == {"key": "ADLC-101"}


def test_sync_decomposition_command_without_decomposition_file_exits_with_helpful_message(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)

    result = runner.invoke(app, ["sync-decomposition", "ADLC-27"])

    assert result.exit_code == 1
    assert "symphony-workflow decompose ADLC-27" in result.output


@respx.mock
def test_sync_decomposition_command_honors_status_option(monkeypatch, tmp_path: Path) -> None:
    # ADLC-121: --status is the CLI-level escape hatch from the poller-race
    # window -- assert it actually reaches execute_sync_decomposition's
    # initial_status param and triggers a real transition call.
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    _write_sync_decomposition(tmp_path)
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [], "isLast": True})
    )

    responses = iter(
        [
            httpx.Response(201, json={"key": "ADLC-101", "id": "101"}),
            httpx.Response(201, json={"key": "ADLC-102", "id": "102"}),
        ]
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(side_effect=lambda request: next(responses))
    respx.get(url__regex=rf"{BASE_URL}/rest/api/2/issue/[^/]+/transitions$").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "31", "name": "Done"}]})
    )
    transition_route = respx.post(
        url__regex=rf"{BASE_URL}/rest/api/2/issue/[^/]+/transitions$"
    ).mock(return_value=httpx.Response(204))

    result = runner.invoke(app, ["sync-decomposition", "ADLC-27", "--status", "Done"])

    assert result.exit_code == 0, result.output
    assert transition_route.call_count == 2  # story + its one subtask


@respx.mock
def test_sync_decomposition_command_honors_project_option(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)
    _write_sync_decomposition(tmp_path)
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": [], "isLast": True})
    )

    responses = iter(
        [
            httpx.Response(201, json={"key": "OTHER-1", "id": "1"}),
            httpx.Response(201, json={"key": "OTHER-2", "id": "2"}),
        ]
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(side_effect=lambda request: next(responses))

    result = runner.invoke(app, ["sync-decomposition", "ADLC-27", "--project", "OTHER"])

    assert result.exit_code == 0, result.output
    assert "into project OTHER" in result.output
    story_call = json.loads(respx.calls[1].request.content)
    assert story_call["fields"]["project"] == {"key": "OTHER"}


def _hier(key: str, kind: str, level: int, status: str, parent: str | None = None) -> dict:
    fields: dict = {
        "summary": key,
        "status": {
            "name": status,
            "statusCategory": {"key": "done" if status == "Done" else "new"},
        },
        "issuetype": {"name": kind, "hierarchyLevel": level},
    }
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "id": key, "fields": fields}


def _mock_issue(payload: dict) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/{payload['key']}").mock(
        return_value=httpx.Response(200, json=payload)
    )


@respx.mock
def test_rollup_command_chain_and_json_output(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    sub = _hier("S-3", "Subtask", -1, "To Do", "ST-1")
    _mock_issue(sub)
    _mock_issue(_hier("ST-1", "Story", 0, "To Do", "EP-1"))
    _mock_issue(_hier("EP-1", "Epic", 1, "To Do"))
    children = {
        "ST-1": [sub, _hier("S-4", "Subtask", -1, "Done", "ST-1")],
        "EP-1": [_hier("ST-1", "Story", 0, "To Do", "EP-1")],
    }

    def search(request: httpx.Request) -> httpx.Response:
        jql = json.loads(request.content)["jql"]
        parent = jql.split("parent = ")[1].split(" ")[0]
        return httpx.Response(200, json={"issues": children[parent], "isLast": True})

    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(side_effect=search)
    for key in ("S-3", "ST-1", "EP-1"):
        _mock_transition(key, "Done")
    result = runner.invoke(app, ["rollup", "S-3", "--json"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert [t["key"] for t in payload["transitions"]] == ["S-3", "ST-1", "EP-1"]
    assert payload["stopped_at"] is None
    assert payload["dry_run"] is False


@respx.mock
def test_rollup_command_dry_run_does_not_post_transitions(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    _mock_issue(_hier("S-3", "Subtask", -1, "To Do", "ST-1"))
    _mock_issue(_hier("ST-1", "Story", 0, "To Do", "EP-1"))
    _mock_issue(_hier("EP-1", "Epic", 1, "To Do"))
    kids = {
        "ST-1": [_hier("S-3", "Subtask", -1, "To Do", "ST-1")],
        "EP-1": [_hier("ST-1", "Story", 0, "To Do", "EP-1")],
    }

    def search(request: httpx.Request) -> httpx.Response:
        parent = json.loads(request.content)["jql"].split("parent = ")[1].split(" ")[0]
        return httpx.Response(200, json={"issues": kids[parent], "isLast": True})

    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(side_effect=search)
    transition_post = respx.post(f"{BASE_URL}/rest/api/2/issue/S-3/transitions").mock(
        return_value=httpx.Response(204)
    )
    result = runner.invoke(app, ["rollup", "S-3", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would transition S-3: To Do -> Done" in result.output
    assert "would transition ST-1" in result.output
    assert "would transition EP-1" in result.output
    assert not transition_post.called


@respx.mock
def test_rollup_command_nothing_to_do_exits_zero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    _mock_issue(_hier("EP-1", "Epic", 1, "Done"))
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200, json={"issues": [_hier("ST-1", "Story", 0, "Done", "EP-1")], "isLast": True}
        )
    )
    result = runner.invoke(app, ["rollup", "EP-1"])
    assert result.exit_code == 0, result.output
    assert "nothing to do" in result.output


@respx.mock
def test_rollup_command_unknown_key_exits_2(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.get(f"{BASE_URL}/rest/api/2/issue/NOPE-1").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["Issue does not exist"]})
    )
    result = runner.invoke(app, ["rollup", "NOPE-1"])
    assert result.exit_code == 2
    assert "NOPE-1" in result.output


@respx.mock
def test_rollup_command_jira_error_exits_3_and_reports_partial_work(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    _mock_issue(_hier("S-3", "Subtask", -1, "To Do", "ST-1"))
    _mock_issue(_hier("ST-1", "Story", 0, "To Do", "EP-1"))
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={"issues": [_hier("S-3", "Subtask", -1, "To Do", "ST-1")], "isLast": True},
        )
    )
    _mock_transition("S-3", "Done")
    # the Story's own transition fails
    respx.get(f"{BASE_URL}/rest/api/2/issue/ST-1/transitions").mock(
        return_value=httpx.Response(500, json={"errorMessages": ["boom"]})
    )
    result = runner.invoke(app, ["rollup", "S-3"])
    assert result.exit_code == 3
    assert "transitioned S-3: To Do -> Done" in result.output
    assert "Roll-up aborted" in result.output


def test_rollup_command_requires_a_key() -> None:
    assert runner.invoke(app, ["rollup"]).exit_code == 2


def test_rollup_is_jira_only(monkeypatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRACKER", "linear")
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_x")
    monkeypatch.setenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", "GAI")
    result = runner.invoke(app, ["rollup", "GAI-1"])
    assert result.exit_code == 1
    assert "Jira" in result.output


# ---- audit / add-subtasks


def _audit_payload(key, kind, level, *, subtasks=None, assignee=None, parent=None, status="To Do"):
    fields = {
        "summary": f"{key} title",
        "status": {
            "name": status,
            "statusCategory": {"key": "done" if status == "Done" else "new"},
        },
        "issuetype": {"name": kind, "hierarchyLevel": level},
    }
    if subtasks is not None:
        fields["subtasks"] = [{"key": f"{key}-s{i}"} for i in range(subtasks)]
    if assignee:
        fields["assignee"] = {"displayName": assignee}
    if parent:
        fields["parent"] = {"key": parent}
    return {"key": key, "id": key, "fields": fields}


def _mock_audit_search(epics, stories, children=None):
    children = children or {}

    def search(request: httpx.Request) -> httpx.Response:
        jql = json.loads(request.content)["jql"]
        if "parent = " in jql:
            parent = jql.split("parent = ")[1].split(" ")[0]
            return httpx.Response(200, json={"issues": children.get(parent, []), "isLast": True})
        if "issuetype = Epic" in jql:
            return httpx.Response(200, json={"issues": epics, "isLast": True})
        return httpx.Response(200, json={"issues": stories, "isLast": True})

    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(side_effect=search)


@respx.mock
def test_audit_command_exits_1_and_lists_problems(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    _mock_audit_search(
        [_audit_payload("P-1", "Epic", 1)],
        [
            _audit_payload("P-2", "Story", 0, subtasks=0, parent="P-1"),
            _audit_payload("P-3", "Story", 0, subtasks=3, parent="P-1"),
        ],
        {"P-1": [_audit_payload("P-2", "Story", 0, parent="P-1")]},
    )
    result = runner.invoke(app, ["audit", "P"])
    assert result.exit_code == 1, result.output
    assert "P-2" in result.output and "no_subtasks" in result.output
    assert "P-3" not in result.output
    as_json = runner.invoke(app, ["audit", "P", "--json"])
    payload = json.loads(as_json.output)
    assert payload["ok"] is False and payload["errors"] == 1
    assert payload["findings"][0]["key"] == "P-2"


@respx.mock
def test_audit_command_exits_0_when_clean_and_assignee_me_uses_the_token_user(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": "acc", "displayName": "alex hsieh"})
    )
    _mock_audit_search(
        [_audit_payload("P-1", "Epic", 1, assignee="alex hsieh")],
        [_audit_payload("P-2", "Story", 0, subtasks=2, parent="P-1", assignee="alex hsieh")],
        {"P-1": [_audit_payload("P-2", "Story", 0, parent="P-1")]},
    )
    ok = runner.invoke(app, ["audit", "P", "--assignee-me"])
    assert ok.exit_code == 0, ok.output
    bad = runner.invoke(app, ["audit", "P", "--assignee", "someone else"])
    assert bad.exit_code == 1 and "wrong_assignee" in bad.output


@respx.mock
def test_add_subtasks_command_dry_run_creates_nothing(monkeypatch, tmp_path) -> None:
    _set_jira_env(monkeypatch)
    _mock_issue(_audit_payload("P-2", "Story", 0, subtasks=0, parent="P-1"))
    create = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        return_value=httpx.Response(201, json={})
    )
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"P-2": ["Add X", "Tests: unit"]}))
    result = runner.invoke(app, ["add-subtasks", str(plan), "--dry-run"])
    assert result.exit_code == 0, result.output
    assert "would create 2 subtask(s)" in result.output
    assert not create.called


@respx.mock
def test_add_subtasks_command_creates_and_rolls_up_a_done_story(monkeypatch, tmp_path) -> None:
    _set_jira_env(monkeypatch)
    _mock_issue(_audit_payload("P-2", "Story", 0, subtasks=0, parent="P-1", status="Done"))
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": "acc-9", "displayName": "alex hsieh"})
    )
    seq = iter(["P-101", "P-102"])
    bodies = []

    def create(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content)["fields"])
        key = next(seq)
        return httpx.Response(
            201, json={"id": "1", "key": key, "self": f"{BASE_URL}/rest/api/2/issue/1"}
        )

    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(side_effect=create)
    for key in ("P-101", "P-102"):
        _mock_transition(key, "Done")
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"P-2": ["Add X", "Tests: unit"]}))
    result = runner.invoke(app, ["add-subtasks", str(plan), "--assign-to-me"])
    assert result.exit_code == 0, result.output
    assert [b["parent"]["key"] for b in bodies] == ["P-2", "P-2"]
    assert all(b["assignee"] == {"accountId": "acc-9"} for b in bodies)
    assert all(b["issuetype"] == {"name": "Subtask"} for b in bodies)
    assert "Subtasks rolled to Done" in result.output


def test_add_subtasks_command_rejects_a_bad_plan_with_exit_2(monkeypatch, tmp_path) -> None:
    _set_jira_env(monkeypatch)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps({"P-2": []}))
    result = runner.invoke(app, ["add-subtasks", str(plan)])
    assert result.exit_code == 2 and "invalid plan" in result.output
    missing = runner.invoke(app, ["add-subtasks", str(tmp_path / "nope.json")])
    assert missing.exit_code == 2


def _run_implement_capturing_kwargs(monkeypatch, tmp_path: Path, extra_args: list[str]) -> dict:
    """Run `implement ADLC-1` against a saved plan_ready run and return the
    kwargs that reached `agent_runner.run_implement`."""
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)

    sidecar_dir = tmp_path / "ADLC-1"
    sidecar_dir.mkdir(parents=True)
    (sidecar_dir / "run.json").write_text(
        json.dumps(
            {
                "id": "run-1",
                "issue_key": "ADLC-1",
                "state": "plan_ready",
                "created_at": "2026-01-01T00:00:00Z",
                "plan": "the plan",
                "session_id": "sess-1",
                "workspace_path": "/tmp/fake-workspace/ADLC-1",
                "result": None,
                "error": None,
            }
        )
    )
    _mock_transition("ADLC-1", "In Progress")

    received: dict[str, object] = {}

    async def fake_run_implement(
        prompt: str, *, cwd: str, resume_session_id: str, **kwargs: object
    ) -> AgentRunResult:
        received.update(kwargs)
        return AgentRunResult(text="done", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_implement", fake_run_implement)

    result = runner.invoke(app, ["implement", "ADLC-1", *extra_args])

    assert result.exit_code == 0, result.output
    return received


@respx.mock
def test_implement_command_max_turns_and_budget_reach_run_implement(
    monkeypatch, tmp_path: Path
) -> None:
    """`--max-turns` and `--max-budget` must reach `agent_runner.run_implement`."""
    received = _run_implement_capturing_kwargs(
        monkeypatch, tmp_path, ["--max-turns", "60", "--max-budget", "8"]
    )

    assert received.get("max_turns") == 60
    assert received.get("max_budget_usd") == 8.0


@respx.mock
def test_implement_command_without_overrides_omits_them(monkeypatch, tmp_path: Path) -> None:
    """Without the options (today's behavior), `run_implement` must receive neither
    kwarg, so its own defaults apply."""
    received = _run_implement_capturing_kwargs(monkeypatch, tmp_path, [])

    assert "max_turns" not in received
    assert "max_budget_usd" not in received


def _jql_children(*issues: dict) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(200, json={"issues": list(issues), "isLast": True})
    )


def _env(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(cli_module, "RUNS_DOCS_ROOT", tmp_path)


@respx.mock
def test_diff_decomposition_exit_1_on_drift_and_json(monkeypatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path)
    _write_sync_decomposition(tmp_path)
    _jql_children()  # empty Epic: everything missing

    result = runner.invoke(app, ["diff-decomposition", "ADLC-27", "--json"])

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["drift"] is True and payload["counts"]["missing"] == 2


@respx.mock
def test_diff_decomposition_exit_2_without_file_and_3_on_jira_error(
    monkeypatch, tmp_path: Path
) -> None:
    _env(monkeypatch, tmp_path)
    assert runner.invoke(app, ["diff-decomposition", "ADLC-27"]).exit_code == 2

    _write_sync_decomposition(tmp_path)
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=httpx.Response(500))
    assert runner.invoke(app, ["diff-decomposition", "ADLC-27"]).exit_code == 3


@respx.mock
def test_sync_decomposition_refuses_epic_with_children(monkeypatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path)
    _write_sync_decomposition(tmp_path)
    _jql_children({"key": "ADLC-9", "fields": {"summary": "x"}})

    result = runner.invoke(app, ["sync-decomposition", "ADLC-27"])

    assert result.exit_code == 1
    assert "--update" in result.output


@respx.mock
def test_sync_update_dry_run_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path)
    _write_sync_decomposition(tmp_path)
    _jql_children()
    create = respx.post(f"{BASE_URL}/rest/api/2/issue").mock(return_value=httpx.Response(201))

    result = runner.invoke(app, ["sync-decomposition", "ADLC-27", "--update", "--dry-run"])

    assert result.exit_code == 0, result.output
    assert "Would: create story" in result.output
    assert create.call_count == 0


@respx.mock
def test_sync_update_creates_missing_and_persists_ids(monkeypatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path)
    _write_sync_decomposition(tmp_path)
    _jql_children()
    keys = iter(["ADLC-101", "ADLC-102"])
    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(
        side_effect=lambda request: httpx.Response(201, json={"key": next(keys), "id": "1"})
    )

    result = runner.invoke(app, ["sync-decomposition", "ADLC-27", "--update"])

    assert result.exit_code == 0, result.output
    saved = json.loads((tmp_path / "ADLC-27" / "decomposition.json").read_text())
    assert saved["stories"][0]["id"] == "s1" and saved["stories"][0]["tasks"][0]["id"] == "s1.t1"


def test_sync_dry_run_without_update_is_rejected(monkeypatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path)
    assert runner.invoke(app, ["sync-decomposition", "ADLC-27", "--dry-run"]).exit_code == 2


# ---- init-env / check-env (ADLC-189) ----


def _git_free_env(monkeypatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def test_init_env_creates_the_file_then_refuses_to_overwrite(monkeypatch, tmp_path: Path) -> None:
    _git_free_env(monkeypatch)
    env = tmp_path / ".env"

    first = runner.invoke(app, ["init-env", "--path", str(env)])
    assert first.exit_code == 0, first.output
    assert env.exists() and "check-env --verify" in first.output

    env.write_text("JIRA_API_TOKEN=keep-me\n")
    second = runner.invoke(app, ["init-env", "--path", str(env)])
    assert second.exit_code == 1
    assert "not overwriting" in second.output
    assert env.read_text() == "JIRA_API_TOKEN=keep-me\n"


def test_check_env_lists_missing_keys_and_exits_1(monkeypatch, tmp_path: Path) -> None:
    _git_free_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("JIRA_BASE_URL=https://x.example\nJIRA_EMAIL=\nJIRA_API_TOKEN=sekret-value\n")

    result = runner.invoke(app, ["check-env", "--path", str(env)])

    assert result.exit_code == 1
    assert "MISSING  JIRA_EMAIL" in result.output
    assert "sekret-value" not in result.output


def test_check_env_missing_file_points_at_init_env(monkeypatch, tmp_path: Path) -> None:
    _git_free_env(monkeypatch)
    result = runner.invoke(app, ["check-env", "--path", str(tmp_path / ".env")])
    assert result.exit_code == 1 and "init-env" in result.output


@respx.mock
def test_check_env_verify_exit_codes_and_json(monkeypatch, tmp_path: Path) -> None:
    _git_free_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text(f"JIRA_BASE_URL={BASE_URL}\nJIRA_EMAIL=a@b.c\nJIRA_API_TOKEN=sekret-value\n")
    route = respx.get(f"{BASE_URL}/rest/api/2/myself")

    route.mock(return_value=httpx.Response(200, json={"accountId": "a1", "displayName": "Bot"}))
    ok = runner.invoke(app, ["check-env", "--path", str(env), "--verify", "--json"])
    assert ok.exit_code == 0, ok.output
    payload = json.loads(ok.output)
    assert payload["ok"] and payload["verify"]["outcome"] == "ok"
    assert "sekret-value" not in ok.output

    route.mock(return_value=httpx.Response(401))
    bad = runner.invoke(app, ["check-env", "--path", str(env), "--verify"])
    assert bad.exit_code == 3
    assert "unauthorized" in bad.output and "sekret-value" not in bad.output


def test_check_env_verify_is_skipped_while_keys_are_missing(monkeypatch, tmp_path: Path) -> None:
    _git_free_env(monkeypatch)
    env = tmp_path / ".env"
    env.write_text("JIRA_BASE_URL=\n")
    with respx.mock(assert_all_called=False) as router:
        result = runner.invoke(app, ["check-env", "--path", str(env), "--verify"])
        assert not router.calls
    assert result.exit_code == 1 and "verify: skipped" in result.output


# ---- profile / import-plan / sync-plan (plan 0008, phase 1) ----


def _plan_project(tmp_path: Path) -> Path:
    from tests._athena import make_project

    return make_project(tmp_path)


def _no_creds(monkeypatch) -> None:
    for name in ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "LINEAR_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")


def test_profile_prints_two_recommendations_and_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    result = runner.invoke(app, ["profile", "--path", str(root)])
    assert result.exit_code == 0, result.output
    assert "Recommendation 1 (from usage)" in result.output
    assert "Recommendation 2 (refined from content)" in result.output
    assert not (root / "docs/context/plan-sync.json").exists()


def test_profile_json_is_machine_readable_and_has_no_secret(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    (root / ".env").write_text("LINEAR_API_KEY=sekret-value\n")
    result = runner.invoke(app, ["profile", "--path", str(root), "--json"])
    assert result.exit_code == 0 and "sekret-value" not in result.output
    payload = json.loads(result.output)
    assert payload["recommendation_1"]["config"]["tracker"] == "linear"
    assert payload["profile"]["tracker"]["linear_credentials"] is True


def test_profile_write_asks_first_and_refuses_to_overwrite(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    target = root / "docs/context/plan-sync.json"

    declined = runner.invoke(app, ["profile", "--path", str(root), "--write"], input="n\n")
    assert declined.exit_code == 1 and not target.exists()

    saved = runner.invoke(app, ["profile", "--path", str(root), "--write"], input="y\n")
    assert saved.exit_code == 0 and json.loads(target.read_text())["plan_source"] == "epics-md"

    again = runner.invoke(app, ["profile", "--path", str(root), "--write", "--yes"])
    assert again.exit_code == 1 and "already exists" in again.output
    forced = runner.invoke(
        app, ["profile", "--path", str(root), "--write", "--yes", "--force", "--use", "1"]
    )
    assert forced.exit_code == 0


def test_profile_rejects_a_bad_use_value(tmp_path: Path) -> None:
    assert runner.invoke(app, ["profile", "--path", str(tmp_path), "--use", "3"]).exit_code == 2


def test_import_plan_lists_the_pyramid_and_warnings(tmp_path: Path) -> None:
    root = _plan_project(tmp_path)
    result = runner.invoke(app, ["import-plan", "--path", str(root)])
    assert result.exit_code == 0 and "2 categor(ies), 3 task(s), 5 to-do(s)" in result.output
    assert "E4  Not in any phase" in result.output and "WARNING E4 no_todos" in result.output
    full = runner.invoke(app, ["import-plan", "--path", str(root), "--include-done", "--json"])
    assert json.loads(full.output)["counts"][0] == 3


def test_sync_plan_in_an_unconfigured_project_touches_nothing(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    before = sorted(p.name for p in (root / "docs/epics").iterdir())
    result = runner.invoke(app, ["sync-plan", "--path", str(root)])
    assert result.exit_code == 1
    assert "does not exist, so nothing was touched" in result.output
    assert "profile --write" in result.output
    assert sorted(p.name for p in (root / "docs/epics").iterdir()) == before


def test_sync_plan_writes_markdown_then_a_rerun_is_unchanged(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "markdown"]

    dry = runner.invoke(app, [*args, "--dry-run"])
    assert dry.exit_code == 0 and "Would update" in dry.output
    assert "sd:begin todo" not in (root / "docs/epics/e1-login.md").read_text()

    first = runner.invoke(app, args)
    # The epic files already exist, so they are updated in place and no index is invented.
    assert first.exit_code == 0 and "Updated" in first.output and "Created" not in first.output
    assert "sd-id: E1.d1" in (root / "docs/epics/e1-login.md").read_text()
    assert not (root / "docs/epics/EPIC_INDEX.md").exists()

    again = runner.invoke(app, args)
    assert again.exit_code == 0 and "Updated" not in again.output and "Created" not in again.output


def test_sync_plan_uses_the_saved_config(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    assert runner.invoke(app, ["profile", "--path", str(root), "--write", "--yes"]).exit_code == 0
    result = runner.invoke(app, ["sync-plan", "--path", str(root)])
    assert result.exit_code == 0, result.output  # tracker=markdown comes from the recommendation


def test_sync_plan_rejects_an_unknown_sink(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "trello"]
    result = runner.invoke(app, args)
    assert result.exit_code == 2 and "Unknown sink" in result.output


def test_sync_plan_to_linear_without_credentials_stops_cleanly(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    monkeypatch.delenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", raising=False)
    root = _plan_project(tmp_path)
    before = {p: p.read_text() for p in root.rglob("*.md")}
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "linear"]
    result = runner.invoke(app, args)
    assert result.exit_code == 2 and "LINEAR_API_KEY" in result.output
    assert {p: p.read_text() for p in root.rglob("*.md")} == before  # nothing was written


def test_sync_plan_from_a_decomposition(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    _write_sync_decomposition(tmp_path / "docs/runs")
    base = ["sync-plan", "--path", str(tmp_path), "--to", "markdown"]
    result = runner.invoke(app, [*base, "--from", "decomposition", "--epic", "ADLC-27"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "docs/epics/EPIC_INDEX.md").exists()
    missing = runner.invoke(app, [*base, "--from", "decomposition"])
    assert missing.exit_code == 2 and "--epic" in missing.output
    bad = runner.invoke(app, [*base, "--from", "nope"])
    assert bad.exit_code == 2


def test_an_invalid_config_stops_sync_plan(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    (tmp_path / "docs/context").mkdir(parents=True)
    (tmp_path / "docs/context/plan-sync.json").write_text('{"tracker": "trello"}')
    assert runner.invoke(app, ["sync-plan", "--path", str(tmp_path)]).exit_code == 2


def test_call_runs_local_tools_without_any_jira_credentials(monkeypatch, tmp_path: Path) -> None:
    _no_creds(monkeypatch)
    root = _plan_project(tmp_path)
    monkeypatch.chdir(root)  # the tools only accept a path inside the working directory
    result = runner.invoke(app, ["call", "profile_project", json.dumps({"path": str(root)})])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["profile"]["plan"]["epics"] == 4
    bad_args = json.dumps({"path": str(root), "include_done": "maybe"})
    bad = runner.invoke(app, ["call", "import_plan", bad_args])
    assert bad.exit_code == 1


# ---- split-todos and the To-Do sources in sync-plan (plan 0008, ADLC-201) ----


def _configured(tmp_path: Path, todo_source: str) -> Path:
    root = _plan_project(tmp_path)
    (root / "docs/context").mkdir(parents=True, exist_ok=True)
    (root / "docs/context/plan-sync.json").write_text(
        json.dumps({"tracker": "markdown", "plan_source": "epics-md", "todo_source": todo_source})
    )
    return root


def test_sync_plan_fills_the_five_steps_for_a_task_without_a_breakdown(tmp_path: Path) -> None:
    root = _configured(tmp_path, "steps")
    result = runner.invoke(app, ["sync-plan", "--path", str(root), "--include-done"])
    assert result.exit_code == 0 and "To-Dos for E4: from steps" in result.output
    text = (root / "docs/epics").glob("e4-*.md").__next__().read_text()
    assert "- [ ] Spec" in text and "- [ ] Merge" in text


def test_sync_plan_with_ai_split_but_no_proposal_says_what_to_run(tmp_path: Path) -> None:
    root = _configured(tmp_path, "ai-split")
    result = runner.invoke(app, ["sync-plan", "--path", str(root), "--include-done"])
    assert result.exit_code == 0 and "run `split-todos`" in result.output
    assert "from steps" not in result.output


def test_split_todos_writes_a_pending_proposal_and_approve_unlocks_it(
    monkeypatch, tmp_path: Path
) -> None:
    root = _configured(tmp_path, "ai-split")

    async def fake_split(task, *, cwd):
        return ["Build it", "Tests: it"]

    monkeypatch.setattr(cli_module, "split_with_agent", fake_split)
    made = runner.invoke(app, ["split-todos", "--path", str(root), "--include-done"])
    assert made.exit_code == 0, made.output
    saved = json.loads((root / "docs/context/todo-proposal.json").read_text())
    assert saved["approved"] is False and saved["tasks"]

    before = runner.invoke(app, ["sync-plan", "--path", str(root), "--include-done"])
    assert "from proposal" not in before.output  # not approved yet: nothing is used

    approved = runner.invoke(app, ["split-todos", "--path", str(root), "--approve"])
    assert approved.exit_code == 0 and "Approved" in approved.output
    after = runner.invoke(app, ["sync-plan", "--path", str(root), "--include-done"])
    assert "from proposal" in after.output


def test_split_todos_reports_an_agent_failure_with_exit_3(monkeypatch, tmp_path: Path) -> None:
    root = _configured(tmp_path, "ai-split")

    async def boom(task, *, cwd):
        raise cli_module.AgentRunError("no auth")

    monkeypatch.setattr(cli_module, "split_with_agent", boom)
    result = runner.invoke(app, ["split-todos", "--path", str(root), "--include-done"])
    assert result.exit_code == 3 and "no auth" in result.output
    assert not (root / "docs/context/todo-proposal.json").exists()


def test_split_todos_approve_without_a_proposal_is_a_clean_error(tmp_path: Path) -> None:
    root = _configured(tmp_path, "ai-split")
    result = runner.invoke(app, ["split-todos", "--path", str(root), "--approve"])
    assert result.exit_code == 2 and "split-todos" in result.output


# ---- sync-plan --to linear (plan 0008, ADLC-203) ----


class _FakeLinearCtx:
    """Stands in for LinearClient in the CLI: the context manager plus the sink's read calls."""

    created: list[str] = []

    def __init__(self, config) -> None:
        self.config = config

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def resolve_team_id(self):
        return "team-1"

    def find_project(self, name):
        return {"id": "p1", "name": name}

    def list_project_milestones(self, project_id):
        return []

    def list_project_issues(self, project_id):
        return []

    def team_workflow_states(self, team_id):
        return []

    def create_project_milestone(self, project_id, name, description):
        self.created.append(f"milestone:{name}")
        return {"id": "m1"}

    def create_plan_issue(self, **kwargs):
        self.created.append(f"issue:{kwargs['title']}")
        return {"id": f"i{len(self.created)}", "identifier": "GAI-1"}


def _linear_env(monkeypatch) -> None:
    _no_creds(monkeypatch)
    monkeypatch.setenv("LINEAR_API_KEY", "lin_api_test")
    monkeypatch.setenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", "GAI")
    monkeypatch.setattr(cli_module, "LinearClient", _FakeLinearCtx)
    _FakeLinearCtx.created = []


def test_sync_plan_to_linear_dry_run_writes_nothing(monkeypatch, tmp_path: Path) -> None:
    _linear_env(monkeypatch)
    root = _plan_project(tmp_path)
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "linear", "--dry-run"]
    result = runner.invoke(app, args)
    assert result.exit_code == 0, result.output
    assert "dry run" in result.output and "Would create" in result.output
    assert _FakeLinearCtx.created == [] and "lin_api_test" not in result.output


def test_sync_plan_to_linear_creates_through_the_client(monkeypatch, tmp_path: Path) -> None:
    _linear_env(monkeypatch)
    root = _plan_project(tmp_path)
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "linear"]
    result = runner.invoke(app, [*args, "--project", "Demo"])
    assert result.exit_code == 0, result.output
    assert "Linear project: Demo" in result.output
    assert any(c.startswith("milestone:") for c in _FakeLinearCtx.created)
    assert any(c.startswith("issue:") for c in _FakeLinearCtx.created)


def test_sync_plan_to_linear_reports_an_api_error_with_exit_3(monkeypatch, tmp_path: Path) -> None:
    _linear_env(monkeypatch)

    def boom(self):
        raise cli_module.LinearError("rate limited")

    monkeypatch.setattr(_FakeLinearCtx, "resolve_team_id", boom)
    root = _plan_project(tmp_path)
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "linear"]
    result = runner.invoke(app, args)
    assert result.exit_code == 3 and "rate limited" in result.output


# ---- sync-status (plan 0008, ADLC-204) ----

_STATES = [
    {"id": "s-todo", "name": "Todo", "type": "unstarted"},
    {"id": "s-done", "name": "Done", "type": "completed"},
]


class _StatusLinear(_FakeLinearCtx):
    cards: dict[str, str] = {}  # entry id -> "unstarted" | "completed"
    moves: list[tuple[str, str]] = []

    def list_project_issues(self, project_id):
        return [
            {"id": f"card-{k}", "description": f"sd-id: {k}",
             "state": {"name": "Done" if v == "completed" else "Todo", "type": v}}
            for k, v in self.cards.items()
        ]  # fmt: skip

    def team_workflow_states(self, team_id):
        return _STATES

    def set_issue_state(self, issue_id, state_id):
        self.moves.append((issue_id, state_id))


def _status_env(monkeypatch, cards: dict[str, str]) -> None:
    _linear_env(monkeypatch)
    _StatusLinear.cards, _StatusLinear.moves = cards, []
    monkeypatch.setattr(cli_module, "LinearClient", _StatusLinear)


def test_sync_status_pull_ticks_the_checkbox_and_saves_the_state(monkeypatch, tmp_path) -> None:
    _status_env(monkeypatch, {"E1.d1": "completed", "E1.d2": "unstarted", "E1.d3": "unstarted"})
    root = _plan_project(tmp_path)
    result = runner.invoke(app, ["sync-status", "--path", str(root), "--to", "linear"])
    assert result.exit_code == 0, result.output
    assert "pull" in result.output and "E1.d1" in result.output
    state = json.loads((root / "docs/context/sync-state.json").read_text())
    assert state["status"]["E1.d1"] == "done"
    text = next((root / "docs/epics").glob("e1-*.md")).read_text()
    assert "- [x]" in text.split("## To-Do")[1]  # the managed block now shows it ticked


def test_sync_status_dry_run_writes_and_moves_nothing(monkeypatch, tmp_path) -> None:
    _status_env(monkeypatch, {"E1.d1": "completed"})
    root = _plan_project(tmp_path)
    before = {p: p.read_text() for p in root.rglob("*") if p.is_file()}
    result = runner.invoke(app, ["sync-status", "--path", str(root), "--to", "linear", "--dry-run"])
    assert result.exit_code == 0 and "dry run" in result.output
    assert {p: p.read_text() for p in root.rglob("*") if p.is_file()} == before
    assert _StatusLinear.moves == []


def test_sync_status_reports_a_conflict_with_exit_1(monkeypatch, tmp_path) -> None:
    _status_env(monkeypatch, {"E1.d1": "completed"})
    root = _plan_project(tmp_path)
    (root / "docs/context").mkdir(parents=True, exist_ok=True)
    # last sync saw it in progress; the plan now says todo-or-done differently from Linear
    (root / "docs/context/sync-state.json").write_text(
        json.dumps({"status": {"E1.d1": "in_progress"}})
    )
    result = runner.invoke(app, ["sync-status", "--path", str(root), "--to", "linear"])
    assert result.exit_code == 1 and "conflict" in result.output
    saved = json.loads((root / "docs/context/sync-state.json").read_text())
    assert saved["status"]["E1.d1"] == "in_progress"  # the conflicted entry's base is untouched
    assert _StatusLinear.moves == []


def test_sync_status_needs_credentials_and_a_valid_direction(monkeypatch, tmp_path) -> None:
    _no_creds(monkeypatch)
    monkeypatch.delenv("SYMPHONY_WORKFLOW_LINEAR_TEAM", raising=False)
    root = _plan_project(tmp_path)
    missing = runner.invoke(app, ["sync-status", "--path", str(root), "--to", "linear"])
    assert missing.exit_code == 2 and "LINEAR_API_KEY" in missing.output
    bad = runner.invoke(
        app, ["sync-status", "--path", str(root), "--to", "linear", "--direction", "sideways"]
    )
    assert bad.exit_code == 2


# ---- sync-plan / sync-status --to jira (plan 0008, ADLC-275) ----


def _jira_env(monkeypatch):
    from tests.test_jira_sink import FakeJira

    _no_creds(monkeypatch)
    monkeypatch.setenv("JIRA_BASE_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "a@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "tok-not-printed")
    monkeypatch.delenv("SYMPHONY_WORKFLOW_JIRA_PROJECT", raising=False)
    fake = FakeJira()

    class Ctx:
        def __init__(self, config) -> None:
            pass

        def __enter__(self):
            return fake

        def __exit__(self, *exc) -> None:
            return None

    monkeypatch.setattr(cli_module, "JiraClient", Ctx)
    return fake


def test_sync_plan_to_jira_needs_a_project_key(monkeypatch, tmp_path: Path) -> None:
    _jira_env(monkeypatch)
    root = _plan_project(tmp_path)
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "jira"]
    result = runner.invoke(app, args)
    assert result.exit_code == 2 and "--project KEY" in result.output


def test_sync_plan_to_jira_dry_run_then_real_run_then_quiet_rerun(monkeypatch, tmp_path) -> None:
    fake = _jira_env(monkeypatch)
    root = _plan_project(tmp_path)
    args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "jira"]
    dry = runner.invoke(app, [*args, "--project", "ZZ", "--dry-run"])
    assert dry.exit_code == 0 and "Would create" in dry.output and not fake.issues
    assert not (root / "docs/context/sync-state.json").exists()
    assert "tok-not-printed" not in dry.output

    real = runner.invoke(app, [*args, "--project", "ZZ"])
    assert real.exit_code == 0 and "Created" in real.output and fake.issues
    state = json.loads((root / "docs/context/sync-state.json").read_text())
    assert state["keys"] and all(k.startswith("ZZ-") for k in state["keys"].values())

    fake.calls.clear()
    again = runner.invoke(app, [*args, "--project", "ZZ"])
    assert again.exit_code == 0 and "Created" not in again.output and fake.calls == []


def test_sync_plan_to_jira_reports_an_api_error_with_exit_3(monkeypatch, tmp_path) -> None:
    fake = _jira_env(monkeypatch)
    fake.fail_on = "Login page"
    root = _plan_project(tmp_path)
    args = [
        "sync-plan",
        "--path",
        str(root),
        "--from",
        "epics-md",
        "--to",
        "jira",
        "--project",
        "ZZ",
    ]
    result = runner.invoke(app, args)
    assert result.exit_code == 3 and "Jira error" in result.output


def test_sync_status_to_jira_pulls_a_change_made_in_jira(monkeypatch, tmp_path: Path) -> None:
    fake = _jira_env(monkeypatch)
    root = _plan_project(tmp_path)
    plan_args = ["sync-plan", "--path", str(root), "--from", "epics-md", "--to", "jira"]
    assert runner.invoke(app, [*plan_args, "--project", "ZZ"]).exit_code == 0
    keys = json.loads((root / "docs/context/sync-state.json").read_text())["keys"]
    fake.issues[keys["E1.d1"]]["cat"] = "done"
    result = runner.invoke(app, ["sync-status", "--path", str(root), "--to", "jira"])
    assert result.exit_code == 0 and "pull" in result.output and "E1.d1" in result.output
    state = json.loads((root / "docs/context/sync-state.json").read_text())
    assert state["status"]["E1.d1"] == "done" and state["keys"] == keys  # the map is kept


@respx.mock
def test_create_with_identity_label_reports_an_existing_card(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={
                "total": 1,
                "issues": [
                    {
                        "key": "ADLC-3",
                        "id": "3",
                        "fields": {"summary": "A card", "status": {"name": "To Do"}},
                    }
                ],
            },
        )
    )
    create = respx.post(f"{BASE_URL}/rest/api/2/issue")
    result = runner.invoke(app, ["create", "ADLC", "A card", "--identity-label", "fu-x"])
    assert result.exit_code == 0, result.output
    assert "ADLC-3 already exists" in result.output
    assert not create.called


@respx.mock
def test_create_with_two_cards_on_one_label_exits_nonzero(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    issue = {"id": "3", "fields": {"summary": "A card", "status": {"name": "To Do"}}}
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=httpx.Response(
            200,
            json={"total": 2, "issues": [{"key": "ADLC-3", **issue}, {"key": "ADLC-4", **issue}]},
        )
    )
    result = runner.invoke(app, ["create", "ADLC", "A card", "--identity-label", "fu-x"])
    assert result.exit_code == 1
    assert "more than one card" in result.output


@respx.mock
def test_update_adds_a_label_without_replacing(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    route = respx.put(f"{BASE_URL}/rest/api/2/issue/ADLC-3").mock(return_value=httpx.Response(204))
    result = runner.invoke(app, ["update", "ADLC-3", "--add-label", "fu-x"])
    assert result.exit_code == 0, result.output
    assert json.loads(route.calls.last.request.content)["update"] == {"labels": [{"add": "fu-x"}]}


def test_update_with_nothing_to_change_exits_2(monkeypatch) -> None:
    _set_jira_env(monkeypatch)
    result = runner.invoke(app, ["update", "ADLC-3"])
    assert result.exit_code == 2
    assert "Nothing to change" in result.output
