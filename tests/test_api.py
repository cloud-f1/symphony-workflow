"""Unit tests for the FastAPI server — HTTP calls to Jira mocked via respx,
requests to the API itself go through Starlette's TestClient (no network),
and the Claude Agent SDK (`run_plan`/`run_implement`) is monkeypatched on
`orchestrator` — where api.py's endpoints and poller.py both actually call
them from — so no live agent session runs and no real git worktree gets
created against this repo. The background poller is disabled for these
tests (`JIRA_AGENT_CLI_POLL_ENABLED=false`) — see test_poller.py for that
in isolation.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

import jira_agent_cli.orchestrator as orchestrator_module
from jira_agent_cli.agent_runner import AgentRunError, AgentRunResult
from jira_agent_cli.api import app, get_workspace_manager

from .conftest import BASE_URL


class _FakeWorkspaceManager:
    """Avoids real `git worktree add` against this repo during tests. The
    hook/cleanup methods record calls (`self.calls`) so tests can assert
    orchestrator.py's hook wiring without re-testing hook dispatch itself —
    that's test_workspace.py's job. Cleanup is opt-in and off by default, so
    these tests never actually reach `remove()` unless a test enables it."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def ensure(self, issue_key: str, *, branch_slug: str | None = None) -> Path:
        return Path("/tmp/fake-workspace") / issue_key

    def run_before_run(self, issue_key: str, workspace_path: Path) -> None:
        self.calls.append("before_run")

    def run_after_run(self, issue_key: str, workspace_path: Path) -> None:
        self.calls.append("after_run")

    def run_before_remove(self, issue_key: str, workspace_path: Path) -> None:
        self.calls.append("before_remove")

    def remove(self, issue_key: str) -> bool:
        self.calls.append("remove")
        return False


def _mock_transition(key: str, name: str) -> None:
    """Mocks the GET+POST pair `JiraClient.transition_issue` makes."""
    respx.get(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": name}]})
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(204)
    )


@pytest.fixture
def fake_workspaces() -> _FakeWorkspaceManager:
    """A single shared instance for the test's whole client session, so tests
    can inspect `.calls` after making requests through `api_client`."""
    return _FakeWorkspaceManager()


@pytest.fixture
def api_client(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_workspaces: _FakeWorkspaceManager
):
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("JIRA_AGENT_CLI_POLL_ENABLED", "false")
    monkeypatch.setattr(orchestrator_module, "RUNS_DOCS_ROOT", tmp_path / "runs")
    app.dependency_overrides[get_workspace_manager] = lambda: fake_workspaces
    with TestClient(app) as client:
        yield client
    app.dependency_overrides.clear()


@respx.mock
def test_health_reports_jira_reachable(api_client: TestClient) -> None:
    route = respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": "abc", "displayName": "Bot"})
    )
    response = api_client.get("/health")
    assert route.called
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["jira_reachable"] is True
    assert body["poller_running"] is False  # disabled for this fixture


@pytest.mark.parametrize("status_code", [401, 503])
@respx.mock
def test_health_reports_unreachable_on_jira_error_status(
    api_client: TestClient, status_code: int
) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(status_code, json={"errorMessages": ["nope"]})
    )
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["jira_reachable"] is False


@pytest.mark.parametrize(
    "error", [httpx.ConnectError("connection refused"), httpx.ReadTimeout("timed out")]
)
@respx.mock
def test_health_reports_unreachable_on_transport_error(
    api_client: TestClient, error: httpx.TransportError
) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(side_effect=error)
    response = api_client.get("/health")
    assert response.status_code == 200
    assert response.json()["jira_reachable"] is False


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


def test_get_run_for_issue_finds_the_dispatched_run(api_client: TestClient) -> None:
    created = api_client.post("/runs", json={"issue_key": "ADLC-24"})
    run_id = created.json()["id"]

    found = api_client.get("/issues/ADLC-24/run")
    assert found.status_code == 200
    assert found.json()["id"] == run_id


def test_get_run_for_issue_404_when_no_run_exists(api_client: TestClient) -> None:
    response = api_client.get("/issues/ADLC-999/run")
    assert response.status_code == 404


def test_missing_jira_env_fails_app_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Missing required env var"), TestClient(app):
        pass


@respx.mock
def test_lifespan_sweeps_existing_workspaces_at_startup_when_cleanup_enabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Symphony §8.6 startup sweep — `sweep_terminal_workspaces` must actually
    run from `lifespan` when the opt-in env var is set, checking each existing
    workspace directory's live Jira status. (Removal mechanics themselves are
    covered by test_workspace.py/test_orchestrator.py; this just verifies the
    wiring fires.)
    """
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("JIRA_AGENT_CLI_POLL_ENABLED", "false")
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")

    workspace_root = tmp_path / "workspaces"
    (workspace_root / "ADLC-1").mkdir(parents=True)
    monkeypatch.setattr("jira_agent_cli.api.WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr("jira_agent_cli.api.TARGET_REPO", tmp_path)

    route = respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "X", "status": {"name": "In Progress"}},
            },
        )
    )

    with TestClient(app):
        pass

    assert route.called


@respx.mock
def test_lifespan_startup_sweep_is_a_noop_when_cleanup_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    monkeypatch.setenv("JIRA_AGENT_CLI_POLL_ENABLED", "false")
    monkeypatch.delenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", raising=False)

    workspace_root = tmp_path / "workspaces"
    (workspace_root / "ADLC-1").mkdir(parents=True)
    monkeypatch.setattr("jira_agent_cli.api.WORKSPACE_ROOT", workspace_root)
    monkeypatch.setattr("jira_agent_cli.api.TARGET_REPO", tmp_path)

    route = respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-1",
                "id": "1",
                "fields": {"summary": "X", "status": {"name": "In Progress"}},
            },
        )
    )

    with TestClient(app):
        pass

    assert not route.called


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
    _mock_transition("ADLC-1", "In Review")

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(
            text="Here is the plan.", session_id="sess-1", cost_usd=0.01, num_turns=1
        )

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]

    response = api_client.post(f"/runs/{run_id}/plan")
    assert response.status_code == 200
    body = response.json()
    assert body["state"] == "plan_ready"
    assert body["plan"] == "Here is the plan."
    assert body["session_id"] == "sess-1"
    assert comment_route.called


@respx.mock
def test_plan_endpoint_runs_before_and_after_run_workspace_hooks(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, fake_workspaces: _FakeWorkspaceManager
) -> None:
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

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]
    api_client.post(f"/runs/{run_id}/plan")

    # Symphony §9.4 — before_run must fire before the agent dispatch and
    # after_run after it, exactly once each.
    assert fake_workspaces.calls == ["before_run", "after_run"]


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

    monkeypatch.setattr(orchestrator_module, "run_plan", failing_run_plan)

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
    _mock_transition("ADLC-1", "In Review")
    _mock_transition("ADLC-1", "In Progress")

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

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)
    monkeypatch.setattr(orchestrator_module, "run_implement", fake_run_implement)

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
def test_implement_endpoint_runs_before_and_after_run_workspace_hooks(
    api_client: TestClient, monkeypatch: pytest.MonkeyPatch, fake_workspaces: _FakeWorkspaceManager
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
    _mock_transition("ADLC-1", "In Review")
    _mock_transition("ADLC-1", "In Progress")

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="the plan", session_id="sess-42", cost_usd=0.01, num_turns=1)

    async def fake_run_implement(
        prompt: str, *, cwd: str, resume_session_id: str, **kwargs: object
    ) -> AgentRunResult:
        return AgentRunResult(
            text="implemented it", session_id="sess-42", cost_usd=0.02, num_turns=3
        )

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)
    monkeypatch.setattr(orchestrator_module, "run_implement", fake_run_implement)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]
    api_client.post(f"/runs/{run_id}/plan")
    fake_workspaces.calls.clear()  # isolate Implement's own hook calls from Plan's

    api_client.post(f"/runs/{run_id}/implement")

    assert fake_workspaces.calls == ["before_run", "after_run"]


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
    _mock_transition("ADLC-1", "In Review")
    _mock_transition("ADLC-1", "In Progress")

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="the plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    async def fake_run_implement(
        prompt: str, *, cwd: str, resume_session_id: str, **kwargs: object
    ) -> AgentRunResult:
        return AgentRunResult(text="done", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)
    monkeypatch.setattr(orchestrator_module, "run_implement", fake_run_implement)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]
    api_client.post(f"/runs/{run_id}/plan")
    api_client.post(f"/runs/{run_id}/implement")

    second = api_client.post(f"/runs/{run_id}/implement")
    assert second.status_code == 409


def test_api_v1_state_empty_when_no_runs(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/state")
    assert response.status_code == 200
    body = response.json()
    assert body["counts"] == {"running": 0, "retrying": 0, "by_state": {}}
    assert body["running"] == []
    assert body["retrying"] == []
    assert body["poller_running"] is False  # disabled for this fixture


@respx.mock
def test_api_v1_state_counts_running_and_by_state(
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

    async def stuck_in_planning(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        raise AgentRunError("still working")

    monkeypatch.setattr(orchestrator_module, "run_plan", stuck_in_planning)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]
    api_client.post(f"/runs/{run_id}/plan")  # fails -> state becomes "failed"

    # Create a second run that stays "created" (never planned) to exercise by_state.
    api_client.post("/runs", json={"issue_key": "ADLC-2"})

    response = api_client.get("/api/v1/state")
    body = response.json()
    assert body["counts"]["running"] == 0  # "failed" and "created" aren't "running"
    assert body["counts"]["by_state"] == {"failed": 1, "created": 1}


def test_api_v1_issue_detail_404_when_no_run_exists(api_client: TestClient) -> None:
    response = api_client.get("/api/v1/issues/ADLC-999")
    assert response.status_code == 404


@respx.mock
def test_api_v1_issue_detail_matches_the_run(
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
    _mock_transition("ADLC-1", "In Review")

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="the plan", session_id="sess-7", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)

    created = api_client.post("/runs", json={"issue_key": "ADLC-1"})
    run_id = created.json()["id"]
    api_client.post(f"/runs/{run_id}/plan")

    response = api_client.get("/api/v1/issues/ADLC-1")
    assert response.status_code == 200
    body = response.json()
    assert body["issue_identifier"] == "ADLC-1"
    assert body["run_id"] == run_id
    assert body["status"] == "plan_ready"
    assert body["session_id"] == "sess-7"
    assert body["workspace"]["path"] == "/tmp/fake-workspace/ADLC-1"
