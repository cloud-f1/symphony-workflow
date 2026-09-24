"""Wraps the Claude Agent SDK — the coding-agent session runner for the Plan
and Implement phases. See docs/spec.md's "Why Claude Agent SDK" section for
why this is the SDK (not `claude -p`), and CLAUDE.md's "Claude Agent SDK
usage" section for the two settled decisions this module implements:
`permission_mode="plan"` for Plan, and `resume` to continue into Implement.

Every field referenced on `ClaudeAgentOptions` / `ResultMessage` /
`AssistantMessage` / `TextBlock` below was verified against the installed
`claude-agent-sdk` package (v0.2.159), not assumed from documentation alone.
This includes `ClaudeAgentOptions.output_format` / `ResultMessage.structured_output`
(ADLC-28) — see CLAUDE.md's "Claude Agent SDK usage" section for the settled
decision and docs/spec.md for the spike that confirmed structured output
survives Plan phase's exploratory multi-turn tool-use loop.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, Message, ResultMessage, TextBlock

from jira_agent_cli.prompts import PLAN_OUTPUT_SCHEMA

DEFAULT_PLAN_MAX_TURNS = 5
DEFAULT_IMPLEMENT_MAX_TURNS = 20
DEFAULT_MAX_BUDGET_USD = 2.0


class AgentRunError(RuntimeError):
    """Raised when a Claude Agent SDK run ends in error (ResultMessage.is_error)."""


@dataclass
class AgentRunResult:
    text: str
    session_id: str | None
    cost_usd: float | None
    num_turns: int | None
    structured: dict | None = None


async def _consume(stream: AsyncIterator[Message]) -> AgentRunResult:
    text_parts: list[str] = []
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None
    structured: dict | None = None

    async for message in stream:
        if isinstance(message, AssistantMessage):
            session_id = message.session_id or session_id
            for block in message.content:
                if isinstance(block, TextBlock):
                    text_parts.append(block.text)
        elif isinstance(message, ResultMessage):
            session_id = message.session_id or session_id
            cost_usd = message.total_cost_usd
            num_turns = message.num_turns
            structured = message.structured_output
            if message.is_error:
                raise AgentRunError(message.result or f"Agent run failed: {message.subtype}")

    return AgentRunResult(
        text="\n".join(text_parts),
        session_id=session_id,
        cost_usd=cost_usd,
        num_turns=num_turns,
        structured=structured,
    )


async def run_plan(
    prompt: str,
    *,
    cwd: str,
    model: str | None = None,
    max_turns: int = DEFAULT_PLAN_MAX_TURNS,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
) -> AgentRunResult:
    """Plan phase: `permission_mode="plan"` — explore without editing, no repo writes.

    Also requests structured output (ADLC-28) via `output_format`, matching
    `PLAN_OUTPUT_SCHEMA` — confirmed by spike to work cleanly alongside
    plan-mode's exploratory multi-turn tool-use loop.
    """
    options = ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        permission_mode="plan",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_format={"type": "json_schema", "schema": PLAN_OUTPUT_SCHEMA},
    )
    return await _consume(query(prompt=prompt, options=options))


async def run_implement(
    prompt: str,
    *,
    cwd: str,
    resume_session_id: str,
    model: str | None = None,
    max_turns: int = DEFAULT_IMPLEMENT_MAX_TURNS,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
) -> AgentRunResult:
    """Implement phase: resumes the Plan phase's session, full edit permissions."""
    options = ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        permission_mode="acceptEdits",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        resume=resume_session_id,
    )
    return await _consume(query(prompt=prompt, options=options))
