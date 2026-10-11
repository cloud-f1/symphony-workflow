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
    assert len(respx.calls) == 2

    story_call = json.loads(respx.calls[0].request.content)
    assert story_call["fields"]["parent"] == {"key": "ADLC-27"}
    task_call = json.loads(respx.calls[1].request.content)
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
    story_call = json.loads(respx.calls[0].request.content)
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
