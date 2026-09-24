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

import jira_agent_cli.cli as cli_module
import jira_agent_cli.decompose as decompose_module
import jira_agent_cli.orchestrator as orchestrator_module
from jira_agent_cli.agent_runner import AgentRunResult
from jira_agent_cli.cli import app
from jira_agent_cli.decompose import Decomposition, Story, Task

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
    assert "jira-agent plan ADLC-99" in result.output


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
    assert "jira-agent decompose ADLC-27" in result.output


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
