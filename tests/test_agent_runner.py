"""Unit tests for agent_runner.py. `claude_agent_sdk.query` is monkeypatched
with a fake async generator yielding real SDK message dataclasses — no live
Claude Agent SDK session runs in this test suite (that costs real money and
would make tests flaky/slow; see docs/spec.md's live-run caveat).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from claude_agent_sdk.types import AssistantMessage, Message, ResultMessage, TextBlock

from jira_agent_cli import agent_runner
from jira_agent_cli.agent_runner import AgentRunError, run_implement, run_plan


def _fake_stream(
    *, text: str, session_id: str = "sess-123", is_error: bool = False, cost: float = 0.01
) -> AsyncIterator[Message]:
    async def gen() -> AsyncIterator[Message]:
        yield AssistantMessage(
            content=[TextBlock(text=text)],
            model="claude-opus-5",
            session_id=session_id,
        )
        yield ResultMessage(
            subtype="success" if not is_error else "error",
            duration_ms=1000,
            duration_api_ms=800,
            is_error=is_error,
            num_turns=1,
            session_id=session_id,
            total_cost_usd=cost,
            result="ok" if not is_error else "agent failed",
        )

    return gen()


@pytest.mark.asyncio
async def test_run_plan_collects_text_and_session_id(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_runner,
        "query",
        lambda prompt, options: _fake_stream(text="Here is the plan."),
    )
    result = await run_plan("plan this", cwd="/tmp/workspace")
    assert result.text == "Here is the plan."
    assert result.session_id == "sess-123"
    assert result.cost_usd == 0.01
    assert result.num_turns == 1


@pytest.mark.asyncio
async def test_run_plan_uses_permission_mode_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_query(prompt: str, options: object) -> AsyncIterator[Message]:
        captured["options"] = options
        return _fake_stream(text="plan")

    monkeypatch.setattr(agent_runner, "query", fake_query)
    await run_plan("plan this", cwd="/tmp/workspace")
    assert captured["options"].permission_mode == "plan"


@pytest.mark.asyncio
async def test_run_implement_resumes_the_plan_session(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_query(prompt: str, options: object) -> AsyncIterator[Message]:
        captured["options"] = options
        return _fake_stream(text="implemented")

    monkeypatch.setattr(agent_runner, "query", fake_query)
    result = await run_implement("implement it", cwd="/tmp/workspace", resume_session_id="sess-abc")
    assert result.text == "implemented"
    assert captured["options"].resume == "sess-abc"
    assert captured["options"].permission_mode == "acceptEdits"


@pytest.mark.asyncio
async def test_agent_run_error_raised_on_is_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_runner,
        "query",
        lambda prompt, options: _fake_stream(text="oops", is_error=True),
    )
    with pytest.raises(AgentRunError, match="agent failed"):
        await run_plan("plan this", cwd="/tmp/workspace")
