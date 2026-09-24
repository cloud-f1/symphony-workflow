"""Unit tests for the poll-tick logic — `Poller.tick()` is called directly
(not via real asyncio timing/sleep) so these stay fast and deterministic.
HTTP mocked via respx; `execute_plan`'s SDK call mocked at the source
(`orchestrator.run_plan`), same style as test_agent_runner.py.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import respx

from jira_agent_cli.agent_runner import AgentRunError, AgentRunResult
from jira_agent_cli.client import JiraClient
from jira_agent_cli.config import JiraConfig
from jira_agent_cli.poller import Poller
from jira_agent_cli.runs import RunState, RunStore

from .conftest import BASE_URL


class _FakeWorkspaceManager:
    def ensure(self, issue_key: str, *, branch_slug: str | None = None) -> Path:
        return Path("/tmp/fake-workspace") / issue_key


@pytest.fixture
def config() -> JiraConfig:
    return JiraConfig(base_url=BASE_URL, email="bot@example.com", api_token="token")


@pytest.fixture
def poller(config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Poller:
    # execute_plan writes docs/runs/<key>/plan.md via orchestrator.RUNS_DOCS_ROOT —
    # redirect it so poll-tick tests never touch this repo's real docs/runs/.
    monkeypatch.setattr("jira_agent_cli.orchestrator.RUNS_DOCS_ROOT", tmp_path / "runs")
    return Poller(client=JiraClient(config), runs=RunStore(), workspaces=_FakeWorkspaceManager())


def _search_response(*keys: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "total": len(keys),
            "issues": [
                {
                    "key": key,
                    "id": key,
                    "fields": {"summary": f"Ticket {key}", "status": {"name": "To Do"}},
                }
                for key in keys
            ],
        },
    )


def _mock_transition(key: str, name: str) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": name}]})
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(204)
    )


@pytest.mark.asyncio
@respx.mock
async def test_tick_dispatches_plan_for_a_new_ticket(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-99"))
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-99").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-99",
                "id": "1",
                "fields": {"summary": "New work", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-99/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-99", "In Review")

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="auto-plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_plan", fake_run_plan)

    dispatched = await poller.tick()
    assert len(dispatched) == 1
    assert dispatched[0].issue_key == "ADLC-99"
    assert dispatched[0].state == RunState.PLAN_READY


@pytest.mark.asyncio
@respx.mock
async def test_tick_skips_a_ticket_already_dispatched(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-99"))
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-99").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-99",
                "id": "1",
                "fields": {"summary": "New work", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-99/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-99", "In Review")

    call_count = 0

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        nonlocal call_count
        call_count += 1
        return AgentRunResult(text="auto-plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_plan", fake_run_plan)

    first = await poller.tick()
    second = await poller.tick()

    assert len(first) == 1
    assert len(second) == 0  # already seen -> not re-dispatched
    assert call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_tick_logs_and_continues_when_one_ticket_fails(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(
        return_value=_search_response("ADLC-97", "ADLC-98")
    )
    for key in ("ADLC-97", "ADLC-98"):
        respx.get(f"{BASE_URL}/rest/api/2/issue/{key}").mock(
            return_value=httpx.Response(
                200,
                json={
                    "key": key,
                    "id": key,
                    "fields": {"summary": "X", "status": {"name": "To Do"}},
                },
            )
        )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-98/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-98", "In Review")

    async def flaky_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        if "ADLC-97" in prompt:
            raise AgentRunError("boom")
        return AgentRunResult(text="ok", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_plan", flaky_run_plan)

    dispatched = await poller.tick()
    # ADLC-97 failed and was logged, not raised; ADLC-98 still got planned.
    assert [r.issue_key for r in dispatched] == ["ADLC-98"]


@pytest.mark.asyncio
async def test_start_and_stop_manage_the_background_task(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    # tick() itself is exercised above — here we only verify lifecycle state,
    # so replace it with a no-op to avoid a real network call inside _loop().
    async def noop_tick() -> list:
        return []

    monkeypatch.setattr(poller, "tick", noop_tick)

    assert not poller.is_running
    poller.start()
    assert poller.is_running
    await poller.stop()
    assert not poller.is_running


@pytest.mark.asyncio
async def test_start_is_idempotent(poller: Poller, monkeypatch: pytest.MonkeyPatch) -> None:
    async def noop_tick() -> list:
        return []

    monkeypatch.setattr(poller, "tick", noop_tick)

    poller.start()
    task = poller._task
    poller.start()  # second call must not replace the running task
    assert poller._task is task
    await poller.stop()
