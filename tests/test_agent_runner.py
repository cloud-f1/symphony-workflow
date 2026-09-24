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
from jira_agent_cli.agent_runner import AgentRunError, run_implement, run_plan, run_triage


def _fake_stream(
    *,
    text: str,
    session_id: str = "sess-123",
    is_error: bool = False,
    cost: float = 0.01,
    structured_output: dict | None = None,
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
            structured_output=structured_output,
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
async def test_run_plan_captures_structured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    structured = {"summary": "do the thing", "files_changed": ["a.py"], "risks": []}
    monkeypatch.setattr(
        agent_runner,
        "query",
        lambda prompt, options: _fake_stream(text="plan text", structured_output=structured),
    )
    result = await run_plan("plan this", cwd="/tmp/workspace")
    assert result.structured == structured


@pytest.mark.asyncio
async def test_run_plan_structured_defaults_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent_runner, "query", lambda prompt, options: _fake_stream(text="plan text")
    )
    result = await run_plan("plan this", cwd="/tmp/workspace")
    assert result.structured is None


@pytest.mark.asyncio
async def test_run_plan_passes_output_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_query(prompt: str, options: object) -> AsyncIterator[Message]:
        captured["options"] = options
        return _fake_stream(text="plan")

    monkeypatch.setattr(agent_runner, "query", fake_query)
    await run_plan("plan this", cwd="/tmp/workspace")
    assert captured["options"].output_format == {
        "type": "json_schema",
        "schema": agent_runner.PLAN_OUTPUT_SCHEMA,
    }


@pytest.mark.asyncio
async def test_run_plan_output_schema_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """ADLC-46: decompose.py reuses run_plan()'s Plan-phase wiring with a
    different structured-output schema via `output_schema=`.
    """
    captured: dict[str, object] = {}
    custom_schema = {"type": "object", "properties": {"stories": {"type": "array"}}}

    def fake_query(prompt: str, options: object) -> AsyncIterator[Message]:
        captured["options"] = options
        return _fake_stream(text="plan")

    monkeypatch.setattr(agent_runner, "query", fake_query)
    await run_plan("decompose this", cwd="/tmp/workspace", output_schema=custom_schema)
    assert captured["options"].output_format == {"type": "json_schema", "schema": custom_schema}
    assert captured["options"].permission_mode == "plan"


@pytest.mark.asyncio
async def test_run_implement_does_not_set_output_format(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def fake_query(prompt: str, options: object) -> AsyncIterator[Message]:
        captured["options"] = options
        return _fake_stream(text="implemented")

    monkeypatch.setattr(agent_runner, "query", fake_query)
    await run_implement("implement it", cwd="/tmp/workspace", resume_session_id="sess-abc")
    assert captured["options"].output_format is None


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


# --- run_triage (ADLC-29) -----------------------------------------------

_CANDIDATES = [
    {"key": "ADLC-1", "summary": "Fix the thing", "description": "Do X.", "status": "To Do"},
    {"key": "ADLC-2", "summary": "Also fix a thing", "description": "TBD", "status": "To Do"},
]


@pytest.mark.asyncio
async def test_run_triage_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    structured = {
        "decisions": [
            {"issue_key": "ADLC-1", "action": "plan", "reason": "well-formed"},
            {"issue_key": "ADLC-2", "action": "skip", "reason": "placeholder description"},
        ]
    }
    monkeypatch.setattr(
        agent_runner,
        "query",
        lambda prompt, options: _fake_stream(text="triaged", structured_output=structured),
    )
    decisions = await run_triage(_CANDIDATES, cwd="/tmp/workspace")
    assert [d.issue_key for d in decisions] == ["ADLC-1", "ADLC-2"]
    assert [d.action for d in decisions] == ["plan", "skip"]


@pytest.mark.asyncio
async def test_run_triage_uses_permission_mode_plan_and_triage_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def fake_query(prompt: str, options: object) -> AsyncIterator[Message]:
        captured["options"] = options
        return _fake_stream(text="triaged", structured_output={"decisions": []})

    monkeypatch.setattr(agent_runner, "query", fake_query)
    await run_triage(_CANDIDATES, cwd="/tmp/workspace")
    assert captured["options"].permission_mode == "plan"
    assert captured["options"].output_format == {
        "type": "json_schema",
        "schema": agent_runner.TRIAGE_OUTPUT_SCHEMA,
    }
    assert captured["options"].max_turns == agent_runner.DEFAULT_TRIAGE_MAX_TURNS
    assert captured["options"].max_budget_usd == agent_runner.DEFAULT_TRIAGE_MAX_BUDGET_USD


@pytest.mark.asyncio
async def test_run_triage_no_structured_output_returns_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(agent_runner, "query", lambda prompt, options: _fake_stream(text="triaged"))
    decisions = await run_triage(_CANDIDATES, cwd="/tmp/workspace")
    assert decisions == []


@pytest.mark.asyncio
async def test_run_triage_drops_malformed_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    structured = {
        "decisions": [
            {"issue_key": "ADLC-1", "action": "plan", "reason": "ok"},
            {"issue_key": "ADLC-2", "action": "not-a-real-action", "reason": "bad enum"},
            {"issue_key": "ADLC-2"},  # missing required fields
        ]
    }
    monkeypatch.setattr(
        agent_runner,
        "query",
        lambda prompt, options: _fake_stream(text="triaged", structured_output=structured),
    )
    decisions = await run_triage(_CANDIDATES, cwd="/tmp/workspace")
    assert [d.issue_key for d in decisions] == ["ADLC-1"]


@pytest.mark.asyncio
async def test_run_triage_drops_decision_outside_input_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    structured = {
        "decisions": [
            {"issue_key": "ADLC-1", "action": "plan", "reason": "ok"},
            {"issue_key": "ADLC-999", "action": "flag", "reason": "hallucinated key"},
        ]
    }
    monkeypatch.setattr(
        agent_runner,
        "query",
        lambda prompt, options: _fake_stream(text="triaged", structured_output=structured),
    )
    decisions = await run_triage(_CANDIDATES, cwd="/tmp/workspace")
    assert [d.issue_key for d in decisions] == ["ADLC-1"]


@pytest.mark.asyncio
async def test_run_triage_raises_agent_run_error_on_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        agent_runner,
        "query",
        lambda prompt, options: _fake_stream(text="oops", is_error=True),
    )
    with pytest.raises(AgentRunError, match="agent failed"):
        await run_triage(_CANDIDATES, cwd="/tmp/workspace")
