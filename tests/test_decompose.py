"""Unit tests for decompose.py (ADLC-46). Jira HTTP is mocked with respx (the
only Jira call decompose.py makes is `client.get_issue`, already covered by
test_client.py's own respx-mocked test — this file focuses on the
decompose-specific wiring); the Claude Agent SDK boundary is monkeypatched on
`decompose.run_plan`, same approach as test_agent_runner.py/test_cli.py.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
import respx

import jira_agent_cli.decompose as decompose_module
from jira_agent_cli.agent_runner import AgentRunResult
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import JiraConfig
from jira_agent_cli.decompose import (
    DEFINITION_OF_DONE_TEMPLATE,
    DecomposeError,
    Decomposition,
    Story,
    Task,
    execute_decompose,
    render_decomposition_markdown,
)

from .conftest import BASE_URL

STORIES_PAYLOAD = [
    {
        "title": "Add the widget model",
        "user_story": "As a user, I want a widget so that I can widge.",
        "acceptance_criteria": ["Widget model exists", "Widget model is typed"],
        "tasks": [
            {"title": "Add Widget pydantic model", "description": "In models.py"},
            {"title": "Add respx test", "description": "In test_client.py"},
        ],
    },
    {
        "title": "Expose widget over the API",
        "user_story": "As a user, I want to fetch widgets over HTTP.",
        "acceptance_criteria": ["GET /widgets works"],
        "tasks": [{"title": "Add endpoint", "description": "In api.py"}],
    },
]


class _FakeWorkspaceManager:
    """Stands in for WorkspaceManager -- no real `git worktree add` here."""

    def __init__(self) -> None:
        self.ensured: list[str] = []
        self.before_run_calls: list[str] = []
        self.after_run_calls: list[str] = []

    def ensure(self, issue_key: str, *, branch_slug: str | None = None) -> Path:
        self.ensured.append(issue_key)
        return Path("/tmp/fake-workspace") / issue_key

    def run_before_run(self, issue_key: str, workspace_path: Path) -> None:
        self.before_run_calls.append(issue_key)

    def run_after_run(self, issue_key: str, workspace_path: Path) -> None:
        self.after_run_calls.append(issue_key)


def _client() -> JiraClient:
    return JiraClient(JiraConfig(base_url=BASE_URL, email="bot@example.com", api_token="t"))


def _mock_get_epic(key: str = "ADLC-27") -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/{key}").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": key,
                "id": "1",
                "fields": {
                    "summary": "Symphony POC",
                    "status": {"name": "In Progress"},
                    "description": "Build the whole thing.",
                },
            },
        )
    )


async def _fake_run_plan_success(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
    return AgentRunResult(
        text="here is the decomposition",
        session_id="sess-1",
        cost_usd=0.02,
        num_turns=3,
        structured={"stories": STORIES_PAYLOAD},
    )


@pytest.mark.asyncio
@respx.mock
async def test_execute_decompose_writes_markdown_and_json(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_get_epic()
    monkeypatch.setattr(decompose_module, "run_plan", _fake_run_plan_success)
    workspaces = _FakeWorkspaceManager()

    with _client() as client:
        decomposition, md_path = await execute_decompose(
            "ADLC-27", client=client, workspaces=workspaces, runs_docs_root=tmp_path
        )

    assert isinstance(decomposition, Decomposition)
    assert decomposition.epic_key == "ADLC-27"
    assert len(decomposition.stories) == 2
    assert decomposition.definition_of_done == DEFINITION_OF_DONE_TEMPLATE
    assert workspaces.ensured == ["ADLC-27"]
    assert workspaces.before_run_calls == ["ADLC-27"]
    assert workspaces.after_run_calls == ["ADLC-27"]

    assert md_path == tmp_path / "ADLC-27" / "decomposition.md"
    assert md_path.exists()
    md_text = md_path.read_text(encoding="utf-8")
    assert "# Decomposition: ADLC-27" in md_text
    assert "## Story 1: Add the widget model" in md_text
    assert "## Story 2: Expose widget over the API" in md_text
    assert "## Definition of Done" in md_text
    assert DEFINITION_OF_DONE_TEMPLATE[0] in md_text

    json_path = tmp_path / "ADLC-27" / "decomposition.json"
    assert json_path.exists()
    saved = Decomposition.model_validate_json(json_path.read_text(encoding="utf-8"))
    assert saved == decomposition


@pytest.mark.asyncio
@respx.mock
async def test_execute_decompose_raises_on_missing_structured_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_get_epic()

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(
            text="no structured output", session_id="sess-1", cost_usd=0.0, num_turns=1
        )

    monkeypatch.setattr(decompose_module, "run_plan", fake_run_plan)
    workspaces = _FakeWorkspaceManager()

    with _client() as client:
        with pytest.raises(DecomposeError, match="did not produce structured"):
            await execute_decompose(
                "ADLC-27", client=client, workspaces=workspaces, runs_docs_root=tmp_path
            )


@pytest.mark.asyncio
@respx.mock
async def test_execute_decompose_raises_on_invalid_structured_output(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _mock_get_epic()

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        # Missing required "tasks" key on the one story.
        return AgentRunResult(
            text="bad shape",
            session_id="sess-1",
            cost_usd=0.0,
            num_turns=1,
            structured={
                "stories": [{"title": "oops", "user_story": "x", "acceptance_criteria": []}]
            },
        )

    monkeypatch.setattr(decompose_module, "run_plan", fake_run_plan)
    workspaces = _FakeWorkspaceManager()

    with _client() as client:
        with pytest.raises(DecomposeError, match="failed validation"):
            await execute_decompose(
                "ADLC-27", client=client, workspaces=workspaces, runs_docs_root=tmp_path
            )


@pytest.mark.asyncio
@respx.mock
async def test_execute_decompose_propagates_jira_error_for_bad_key(tmp_path: Path) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/NOPE-1").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
    )
    workspaces = _FakeWorkspaceManager()

    with _client() as client:
        with pytest.raises(JiraError):
            await execute_decompose(
                "NOPE-1", client=client, workspaces=workspaces, runs_docs_root=tmp_path
            )
    # Never even reached the agent run.
    assert workspaces.ensured == []


def test_run_plan_called_with_decompose_schema_and_workspace_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """execute_decompose is Plan-phase-shaped: it must call the same
    agent_runner.run_plan() seam ADLC-28 built, with PLAN_DECOMPOSE_SCHEMA and
    the issue's dedicated workspace as cwd -- never TARGET_REPO directly.
    """
    captured: dict[str, object] = {}

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        captured["cwd"] = cwd
        captured["output_schema"] = kwargs.get("output_schema")
        captured["prompt"] = prompt
        return AgentRunResult(
            text="ok",
            session_id="s",
            cost_usd=0.0,
            num_turns=1,
            structured={"stories": STORIES_PAYLOAD},
        )

    monkeypatch.setattr(decompose_module, "run_plan", fake_run_plan)
    workspaces = _FakeWorkspaceManager()

    with respx.mock:
        _mock_get_epic("ADLC-27")
        with _client() as client:
            asyncio.run(
                execute_decompose(
                    "ADLC-27", client=client, workspaces=workspaces, runs_docs_root=tmp_path
                )
            )

    assert captured["cwd"] == str(Path("/tmp/fake-workspace") / "ADLC-27")
    assert captured["output_schema"] is decompose_module.PLAN_DECOMPOSE_SCHEMA
    assert "ADLC-27" in captured["prompt"]


def test_render_decomposition_markdown_lists_tasks_and_definition_of_done() -> None:
    decomposition = Decomposition(
        epic_key="ADLC-27",
        stories=[
            Story(
                title="Story one",
                user_story="As a user, I want X.",
                acceptance_criteria=["Does X"],
                tasks=[Task(title="Do X", description="Implement X")],
            )
        ],
        definition_of_done=["Tests pass", "Lint clean"],
    )

    md = render_decomposition_markdown(decomposition)

    assert "## Story 1: Story one" in md
    assert "As a user, I want X." in md
    assert "- Does X" in md
    assert "**Do X** — Implement X" in md
    assert "- [ ] Tests pass" in md
    assert "- [ ] Lint clean" in md


def test_definition_of_done_template_is_not_empty_and_mentions_ruff_and_tests() -> None:
    # Sanity check on the "templated, not LLM-invented" design decision --
    # this list must actually reflect CLAUDE.md's real standards.
    joined = " ".join(DEFINITION_OF_DONE_TEMPLATE)
    assert len(DEFINITION_OF_DONE_TEMPLATE) >= 5
    assert "ruff" in joined
    assert "pytest" in joined
    assert "respx" in joined
