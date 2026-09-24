"""Unit tests for the FastAPI server — HTTP calls to Jira mocked via respx,
requests to the API itself go through Starlette's TestClient (no network),
and the Claude Agent SDK (`run_plan`/`run_implement`) is monkeypatched so no
live agent session runs and no real git worktree gets created against this
repo — see test_agent_runner.py / test_workspace.py for those in isolation.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import jira_agent_cli.api as api_module
from jira_agent_cli.agent_runner import AgentRunError, AgentRunResult
from jira_agent_cli.api import app, get_workspace_manager

from .conftest import BASE_URL


class _FakeWorkspaceManager:
    """Avoids real `git worktree add` against this repo during tests."""

    def ensure(self, issue_key: str) -> Path:
        return Path("/tmp/fake-workspace") / issue_key


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setattr(api_module, "RUNS_DOCS_ROOT", tmp_path / "runs")
    app.dependency_overrides[get_workspace_manager] = lambda: _FakeWorkspaceManager()
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


def test_list_tools_returns_every_registered_schema(api_client: TestClient) -> None:
    response = api_client.get("/tools")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()}
    assert {"get_issue", "create_issue", "search_issues"} <= names


@respx.mock
def test_call_tool_dispatches_get_issue(api_client: TestClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CF1-1",
                "id": "1",
                "fields": {"summary": "Test", "status": {"name": "Todo"}},
            },
        )
    )
    response = api_client.post("/tools/get_issue/call", json={"arguments": {"key": "CF1-1"}})
    assert response.status_code == 200
    assert response.json()["key"] == "CF1-1"


def test_call_tool_unknown_name_returns_404(api_client: TestClient) -> None:
    response = api_client.post("/tools/delete_everything/call", json={"arguments": {}})
    assert response.status_code == 404


def test_call_tool_invalid_arguments_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/tools/get_issue/call", json={"arguments": {}})
    assert response.status_code == 422


@respx.mock
def test_call_tool_jira_error_maps_to_matching_status(api_client: TestClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-404").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
    )
    response = api_client.post("/tools/get_issue/call", json={"arguments": {"key": "CF1-404"}})
    assert response.status_code == 404


@respx.mock
def test_get_issue_endpoint(api_client: TestClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CF1-1",
                "id": "1",
                "fields": {"summary": "Test", "status": {"name": "Todo"}},
            },
        )
    )
    response = api_client.get("/issues/CF1-1")
    assert response.status_code == 200
    assert response.json()["summary"] == "Test"


def test_create_and_fetch_run_roundtrip(api_client: TestClient) -> None:
    created = api_client.post("/runs", json={"issue_key": "CF1-286"})
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["state"] == "created"

    fetched = api_client.get(f"/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["issue_key"] == "CF1-286"


def test_get_unknown_run_returns_404(api_client: TestClient) -> None:
    response = api_client.get("/runs/does-not-exist")
    assert response.status_code == 404


def test_missing_jira_env_fails_app_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Missing required env var"), TestClient(app):
        pass


@respx.mock
def test_plan_endpoint_happy_path(api_client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
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
    comment_route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(
            text="Here is the plan.", session_id="sess-1", cost_usd=0.01, num_turns=1
        )

    monkeypatch.setattr(api_module, "run_plan", fake_run_plan)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]

    response = api_client.post(f"/runs/{run_id}/plan")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "plan_ready"
    assert body["plan"] == "Here is the plan."
    assert body["session_id"] == "sess-1"
    assert comment_route.called


def test_plan_endpoint_unknown_run_returns_404(api_client: TestClient) -> None:
    response = api_client.post("/runs/does-not-exist/plan")
    assert response.status_code == 404


@respx.mock
def test_plan_endpoint_agent_error_marks_run_failed(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "X", "status": {"name": "To Do"}},
            },
        )
    )

    async def failing_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        raise AgentRunError("agent blew up")

    monkeypatch.setattr(api_module, "run_plan", failing_run_plan)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]

    response = api_client.post(f"/runs/{run_id}/plan")
    assert response.status_code == 502

    fetched = api_client.get(f"/runs/{run_id}")
    assert fetched.json()["state"] == "failed"
    assert "agent blew up" in fetched.json()["error"]


def test_implement_endpoint_requires_plan_ready_state(api_client: TestClient) -> None:
    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]

    response = api_client.post(f"/runs/{run_id}/implement")
    assert response.status_code == 409


def test_implement_endpoint_unknown_run_returns_404(api_client: TestClient) -> None:
    response = api_client.post("/runs/does-not-exist/implement")
    assert response.status_code == 404


@respx.mock
def test_implement_endpoint_happy_path_resumes_plan_session(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "X", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="the plan", session_id="sess-42", cost_usd=0.01, num_turns=1)

    captured: dict[str, object] = {}

    async def fake_run_implement(
        prompt: str, *, cwd: str, resume_session_id: str, **kwargs: object
    ) -> AgentRunResult:
        captured["cwd"] = cwd
        captured["resume_session_id"] = resume_session_id
        return AgentRunResult(
            text="implemented it", session_id="sess-42", cost_usd=0.02, num_turns=3
        )

    monkeypatch.setattr(api_module, "run_plan", fake_run_plan)
    monkeypatch.setattr(api_module, "run_implement", fake_run_implement)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]
    api_client.post(f"/runs/{run_id}/plan")

    response = api_client.post(f"/runs/{run_id}/implement")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "completed"
    assert body["result"] == "implemented it"
    assert captured["resume_session_id"] == "sess-42"
    assert "ADLC-1" in captured["cwd"]


@respx.mock
def test_implement_endpoint_cannot_run_twice(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "X", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-1/comment").mock(
        return_value=httpx.Response(201, json={})
    )

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="the plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    async def fake_run_implement(
        prompt: str, *, cwd: str, resume_session_id: str, **kwargs: object
    ) -> AgentRunResult:
        return AgentRunResult(text="done", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(api_module, "run_plan", fake_run_plan)
    monkeypatch.setattr(api_module, "run_implement", fake_run_implement)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]
    api_client.post(f"/runs/{run_id}/plan")
    api_client.post(f"/runs/{run_id}/implement")

    second = api_client.post(f"/runs/{run_id}/implement")
    assert second.status_code == 409
