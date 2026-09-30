"""Unit tests for the poll-tick logic — `Poller.tick()` is called directly
(not via real asyncio timing/sleep) so these stay fast and deterministic.
HTTP mocked via respx; `execute_plan`'s SDK call mocked at the source
(`orchestrator.run_plan`), same style as test_agent_runner.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from symphony_workflow.agent_runner import AgentRunError, AgentRunResult
from symphony_workflow.client import JiraClient
from symphony_workflow.config import JiraConfig
from symphony_workflow.models import TriageDecision
from symphony_workflow.poller import Poller
from symphony_workflow.runs import RunState, RunStore

from .conftest import BASE_URL


class _FakeWorkspaceManager:
    def ensure(self, issue_key: str, *, branch_slug: str | None = None) -> Path:
        return Path("/tmp/fake-workspace") / issue_key

    def run_before_run(self, issue_key: str, workspace_path: Path) -> None:
        pass

    def run_after_run(self, issue_key: str, workspace_path: Path) -> None:
        pass

    def remove(self, issue_key: str) -> bool:
        return False


@pytest.fixture
def config() -> JiraConfig:
    return JiraConfig(base_url=BASE_URL, email="bot@example.com", api_token="token")


@pytest.fixture
def poller(config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Poller:
    # execute_plan writes docs/runs/<key>/plan.md via orchestrator.RUNS_DOCS_ROOT —
    # redirect it so poll-tick tests never touch this repo's real docs/runs/.
    monkeypatch.setattr("symphony_workflow.orchestrator.RUNS_DOCS_ROOT", tmp_path / "runs")
    return Poller(client=JiraClient(config), runs=RunStore(), workspaces=_FakeWorkspaceManager())


@pytest.fixture
def max_turns_poller(config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Poller:
    # ADLC-55: same as `poller` above but with an explicit plan_max_turns
    # override -- the opt-in path, used only by the max-turns-specific test.
    monkeypatch.setattr("symphony_workflow.orchestrator.RUNS_DOCS_ROOT", tmp_path / "runs")
    return Poller(
        client=JiraClient(config),
        runs=RunStore(),
        workspaces=_FakeWorkspaceManager(),
        plan_max_turns=15,
    )


@pytest.fixture
def max_budget_poller(config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Poller:
    # 2026-09-30: same shape as max_turns_poller above but for the dollar
    # budget ceiling override.
    monkeypatch.setattr("symphony_workflow.orchestrator.RUNS_DOCS_ROOT", tmp_path / "runs")
    return Poller(
        client=JiraClient(config),
        runs=RunStore(),
        workspaces=_FakeWorkspaceManager(),
        plan_max_budget_usd=10.0,
    )


@pytest.fixture
def triage_poller(config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Poller:
    # Same as `poller` above but with triage (ADLC-29) turned on -- the
    # opt-in path, used only by the triage-specific tests below.
    monkeypatch.setattr("symphony_workflow.orchestrator.RUNS_DOCS_ROOT", tmp_path / "runs")
    return Poller(
        client=JiraClient(config),
        runs=RunStore(),
        workspaces=_FakeWorkspaceManager(),
        triage_enabled=True,
    )


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

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

    dispatched = await poller.tick()
    assert len(dispatched) == 1
    assert dispatched[0].issue_key == "ADLC-99"
    assert dispatched[0].state == RunState.PLAN_READY


@pytest.mark.asyncio
@respx.mock
async def test_tick_no_max_turns_override_omits_kwarg(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADLC-55: today's behavior (no `plan_max_turns` passed to `Poller`) must
    not pass a `max_turns` kwarg to `run_plan` at all.
    """
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

    received_kwargs: dict[str, object] = {}

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        received_kwargs.update(kwargs)
        return AgentRunResult(text="auto-plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

    dispatched = await poller.tick()
    assert len(dispatched) == 1
    assert "max_turns" not in received_kwargs


@pytest.mark.asyncio
@respx.mock
async def test_tick_max_turns_override_reaches_run_plan(
    max_turns_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADLC-55: a `plan_max_turns` override passed to `Poller` must reach
    `run_plan` on auto-Plan dispatch.
    """
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

    received: dict[str, object] = {}

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        received.update(kwargs)
        return AgentRunResult(text="auto-plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

    dispatched = await max_turns_poller.tick()
    assert len(dispatched) == 1
    assert received.get("max_turns") == 15


@pytest.mark.asyncio
@respx.mock
async def test_tick_no_max_budget_override_omits_kwarg(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Today's behavior (no `plan_max_budget_usd` passed to `Poller`) must
    not pass a `max_budget_usd` kwarg to `run_plan` at all.
    """
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

    received_kwargs: dict[str, object] = {}

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        received_kwargs.update(kwargs)
        return AgentRunResult(text="auto-plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

    dispatched = await poller.tick()
    assert len(dispatched) == 1
    assert "max_budget_usd" not in received_kwargs


@pytest.mark.asyncio
@respx.mock
async def test_tick_max_budget_override_reaches_run_plan(
    max_budget_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `plan_max_budget_usd` override passed to `Poller` must reach
    `run_plan` on auto-Plan dispatch.
    """
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

    received: dict[str, object] = {}

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        received.update(kwargs)
        return AgentRunResult(text="auto-plan", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

    dispatched = await max_budget_poller.tick()
    assert len(dispatched) == 1
    assert received.get("max_budget_usd") == 10.0


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

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

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
    failure_comment_route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-97/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-98", "In Review")

    async def flaky_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        if "ADLC-97" in prompt:
            raise AgentRunError("boom")
        return AgentRunResult(text="ok", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", flaky_run_plan)

    dispatched = await poller.tick()
    # ADLC-97 failed and was logged, not raised; ADLC-98 still got planned.
    assert [r.issue_key for r in dispatched] == ["ADLC-98"]
    # The failure is now visible on the ticket, not silent (ADLC-44 finding).
    assert failure_comment_route.called
    assert "FAILED" in json.loads(failure_comment_route.calls.last.request.content)["body"]


@pytest.mark.asyncio
@respx.mock
async def test_failed_ticket_is_retried_on_a_later_tick(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Reproduces the ADLC-44 finding: a Plan failure used to strand a ticket
    # forever (permanently in `_seen`) with no further attempt. It should
    # instead be retried on a subsequent tick, not treated as done.
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-97"))
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-97").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-97",
                "id": "ADLC-97",
                "fields": {"summary": "X", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-97/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-97", "In Review")

    attempts = {"count": 0}

    async def flaky_then_ok(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise AgentRunError("boom")
        return AgentRunResult(text="ok", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", flaky_then_ok)

    first = await poller.tick()
    assert first == []  # failed, nothing dispatched yet
    assert attempts["count"] == 1

    second = await poller.tick()
    # Retried (not skipped as already-seen) and succeeded this time.
    assert [r.issue_key for r in second] == ["ADLC-97"]
    assert attempts["count"] == 2


@pytest.mark.asyncio
@respx.mock
async def test_ticket_stops_being_retried_after_max_attempts(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr("symphony_workflow.orchestrator.RUNS_DOCS_ROOT", tmp_path / "runs")
    poller = Poller(
        client=JiraClient(config),
        runs=RunStore(),
        workspaces=_FakeWorkspaceManager(),
        max_plan_attempts=2,
    )

    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-97"))
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-97").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-97",
                "id": "ADLC-97",
                "fields": {"summary": "X", "status": {"name": "To Do"}},
            },
        )
    )
    failure_route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-97/comment").mock(
        return_value=httpx.Response(201, json={})
    )

    async def always_fails(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        raise AgentRunError("boom")

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", always_fails)

    await poller.tick()  # attempt 1/2 — retried next tick
    assert "ADLC-97" not in poller._given_up
    await poller.tick()  # attempt 2/2 — exhausted, gives up
    assert "ADLC-97" in poller._given_up

    calls_after_giving_up = len(failure_route.calls)
    await poller.tick()  # would be attempt 3 if retried -- must not be
    assert len(failure_route.calls) == calls_after_giving_up

    # One of the comment posts (across the attempts + the final give-up
    # comment, same endpoint) says it gave up, not just "FAILED".
    bodies = [json.loads(c.request.content)["body"] for c in failure_route.calls]
    assert any("Gave up" in body for body in bodies)


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


# --- ADLC-29 triage cadence ------------------------------------------------


async def _fail_if_run_plan_called(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
    raise AssertionError("execute_plan/run_plan must not be called before triage decides 'plan'")


@pytest.mark.asyncio
@respx.mock
async def test_flag_off_by_default_dispatches_immediately_like_before(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The single most important regression to avoid: with the feature flag
    # off (poller's default), a newly-discovered ticket is dispatched on the
    # very first tick, exactly as before ADLC-29 -- it never touches the
    # triage queue at all.
    assert poller._triage_enabled is False

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

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

    dispatched = await poller.tick()

    assert [r.issue_key for r in dispatched] == ["ADLC-99"]
    assert poller._untriaged == {}  # never queued -- triage is entirely bypassed


@pytest.mark.asyncio
async def test_start_never_creates_a_triage_task_when_disabled(
    poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def noop_tick() -> list:
        return []

    monkeypatch.setattr(poller, "tick", noop_tick)

    poller.start()
    assert poller._triage_task is None
    await poller.stop()


@pytest.mark.asyncio
async def test_start_and_stop_manage_both_tasks_when_triage_enabled(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The triage loop runs on its own cadence, independent of the 60s poll
    # loop -- both tasks are created by start() and torn down by stop().
    async def noop_tick() -> list:
        return []

    async def noop_triage_tick() -> list:
        return []

    monkeypatch.setattr(triage_poller, "tick", noop_tick)
    monkeypatch.setattr(triage_poller, "triage_tick", noop_triage_tick)
    triage_poller._triage_interval = 0.01

    assert not triage_poller.is_running
    assert triage_poller._triage_task is None
    triage_poller.start()
    assert triage_poller.is_running
    assert triage_poller._triage_task is not None
    assert not triage_poller._triage_task.done()

    await triage_poller.stop()
    assert not triage_poller.is_running
    assert triage_poller._triage_task is None


@pytest.mark.asyncio
async def test_start_is_idempotent_for_the_triage_task_too(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def noop_tick() -> list:
        return []

    async def noop_triage_tick() -> list:
        return []

    monkeypatch.setattr(triage_poller, "tick", noop_tick)
    monkeypatch.setattr(triage_poller, "triage_tick", noop_triage_tick)

    triage_poller.start()
    task = triage_poller._triage_task
    triage_poller.start()  # second call must not replace the running task
    assert triage_poller._triage_task is task
    await triage_poller.stop()


@pytest.mark.asyncio
@respx.mock
async def test_tick_queues_new_ticket_for_triage_instead_of_dispatching(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-99"))
    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", _fail_if_run_plan_called)

    dispatched = await triage_poller.tick()

    assert dispatched == []
    assert set(triage_poller._untriaged) == {"ADLC-99"}
    assert triage_poller._untriaged["ADLC-99"].key == "ADLC-99"


@pytest.mark.asyncio
@respx.mock
async def test_tick_does_not_requeue_an_already_queued_ticket(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-99"))
    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", _fail_if_run_plan_called)

    await triage_poller.tick()
    await triage_poller.tick()

    assert len(triage_poller._untriaged) == 1


@pytest.mark.asyncio
@respx.mock
async def test_triage_tick_is_a_noop_when_queue_is_empty(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail_if_called(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        raise AssertionError("run_triage must not be called for an empty batch")

    monkeypatch.setattr("symphony_workflow.orchestrator.run_triage", fail_if_called)

    assert await triage_poller.triage_tick() == []


@pytest.mark.asyncio
@respx.mock
async def test_triage_plan_decision_then_next_regular_tick_dispatches(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-99"))
    await triage_poller.tick()
    assert "ADLC-99" in triage_poller._untriaged

    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        assert [c["key"] for c in candidates] == ["ADLC-99"]
        return [TriageDecision(issue_key="ADLC-99", action="plan", reason="well-formed")]

    monkeypatch.setattr("symphony_workflow.orchestrator.run_triage", fake_run_triage)

    triage_decisions = await triage_poller.triage_tick()
    assert [d.action for d in triage_decisions] == ["plan"]
    assert triage_poller._untriaged == {}  # drained

    # Second call, on a slower cadence, would be a no-op regardless -- assert
    # it explicitly so this doesn't silently regress into re-triaging.
    async def fail_if_called_again(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        raise AssertionError("must not re-triage an already-queued-and-drained batch")

    monkeypatch.setattr("symphony_workflow.orchestrator.run_triage", fail_if_called_again)
    assert await triage_poller.triage_tick() == []

    # Now the *regular* poll tick dispatches it -- exactly one Plan run, not
    # one per poll tick spent queued for triage.
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

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", fake_run_plan)

    dispatched = await triage_poller.tick()
    assert [r.issue_key for r in dispatched] == ["ADLC-99"]
    assert dispatched[0].state == RunState.PLAN_READY


@pytest.mark.asyncio
@respx.mock
async def test_triage_skip_decision_prevents_dispatch_forever(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-97"))
    await triage_poller.tick()

    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        return [TriageDecision(issue_key="ADLC-97", action="skip", reason="placeholder text")]

    monkeypatch.setattr("symphony_workflow.orchestrator.run_triage", fake_run_triage)
    await triage_poller.triage_tick()

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", _fail_if_run_plan_called)

    # Ticket is still "To Do" in Jira (skip doesn't transition anything), so
    # it keeps showing up in search results -- but must never be dispatched,
    # and must never re-enter the untriaged queue either.
    dispatched = await triage_poller.tick()
    assert dispatched == []
    assert triage_poller._untriaged == {}
    # Never became a Run -- so it never enters the bounded-retry accounting
    # (ADLC-44) either.
    assert triage_poller._runs.get_by_issue_key("ADLC-97") is None


@pytest.mark.asyncio
@respx.mock
async def test_triage_flag_decision_posts_comment_not_status_change_and_prevents_dispatch(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-96"))
    await triage_poller.tick()

    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        return [TriageDecision(issue_key="ADLC-96", action="flag", reason="ambiguous scope")]

    monkeypatch.setattr("symphony_workflow.orchestrator.run_triage", fake_run_triage)
    comment_route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-96/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    transitions_route = respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-96/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": []})
    )

    await triage_poller.triage_tick()

    assert comment_route.called
    body = json.loads(comment_route.calls.last.request.content)["body"]
    assert "ambiguous scope" in body
    assert not transitions_route.called  # no status transition -- comment-only by design

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", _fail_if_run_plan_called)
    dispatched = await triage_poller.tick()
    assert dispatched == []


@pytest.mark.asyncio
@respx.mock
async def test_triage_agent_failure_requeues_batch_for_next_cycle(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-95"))
    await triage_poller.tick()
    assert "ADLC-95" in triage_poller._untriaged

    async def always_fails(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        raise AgentRunError("error_max_turns")

    monkeypatch.setattr("symphony_workflow.orchestrator.run_triage", always_fails)

    decisions = await triage_poller.triage_tick()

    assert decisions == []
    # Re-queued, not dropped -- a failed triage batch gets another shot on
    # the next cycle instead of stranding the ticket forever.
    assert "ADLC-95" in triage_poller._untriaged


@pytest.mark.asyncio
@respx.mock
async def test_bounded_retry_path_is_unaffected_by_triage(
    triage_poller: Poller, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A ticket that already has a Run (e.g. it was triaged as "plan" and
    # dispatched, then its Plan failed) must keep using the existing
    # ADLC-44 bounded-retry bookkeeping without ever consulting TriageStore
    # again -- retries are not re-triaged.
    respx.post(f"{BASE_URL}/rest/api/3/search/jql").mock(return_value=_search_response("ADLC-94"))
    respx.get(f"{BASE_URL}/rest/api/2/issue/ADLC-94").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "ADLC-94",
                "id": "ADLC-94",
                "fields": {"summary": "X", "status": {"name": "To Do"}},
            },
        )
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-94/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-94", "In Review")

    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        return [TriageDecision(issue_key="ADLC-94", action="plan", reason="well-formed")]

    monkeypatch.setattr("symphony_workflow.orchestrator.run_triage", fake_run_triage)

    attempts = {"count": 0}

    async def flaky_then_ok(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise AgentRunError("boom")
        return AgentRunResult(text="ok", session_id="sess-1", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr("symphony_workflow.orchestrator.run_plan", flaky_then_ok)

    await triage_poller.tick()  # queues for triage
    await triage_poller.triage_tick()  # decides "plan"

    first = await triage_poller.tick()  # dispatches, fails
    assert first == []
    assert attempts["count"] == 1

    # A retry must not require -- or re-check -- a TriageStore entry; it's
    # already recorded and the run/retry path never looks at it again.
    second = await triage_poller.tick()
    assert [r.issue_key for r in second] == ["ADLC-94"]
    assert attempts["count"] == 2
