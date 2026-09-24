"""Wraps the Claude Agent SDK — the coding-agent session runner for the Plan
and Implement phases. See docs/spec.md's "Why Claude Agent SDK" section for
why this is the SDK (not `claude -p`), and CLAUDE.md's "Claude Agent SDK
usage" section for the two settled decisions this module implements:
`permission_mode="plan"` for Plan, and `resume` to continue into Implement.

Every field referenced on `ClaudeAgentOptions` / `ResultMessage` /
`AssistantMessage` / `TextBlock` below was verified against the installed
`claude-agent-sdk` package (v0.2.159), not assumed from documentation alone.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, Message, ResultMessage, TextBlock

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


async def _consume(stream: AsyncIterator[Message]) -> AgentRunResult:
    text_parts: list[str] = []
    session_id: str | None = None
    cost_usd: float | None = None
    num_turns: int | None = None

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
            if message.is_error:
                raise AgentRunError(message.result or f"Agent run failed: {message.subtype}")

    return AgentRunResult(
        text="\n".join(text_parts), session_id=session_id, cost_usd=cost_usd, num_turns=num_turns
    )


async def run_plan(
    prompt: str,
    *,
    cwd: str,
    model: str | None = None,
    max_turns: int = DEFAULT_PLAN_MAX_TURNS,
    max_budget_usd: float = DEFAULT_MAX_BUDGET_USD,
) -> AgentRunResult:
    """Plan phase: `permission_mode="plan"` — explore without editing, no repo writes."""
    options = ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        permission_mode="plan",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
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
