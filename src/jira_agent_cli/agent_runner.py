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

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from claude_agent_sdk import ClaudeAgentOptions, query
from claude_agent_sdk.types import AssistantMessage, Message, ResultMessage, TextBlock
from pydantic import ValidationError

from jira_agent_cli.models import TriageDecision
from jira_agent_cli.prompts import PLAN_OUTPUT_SCHEMA, TRIAGE_OUTPUT_SCHEMA, render_triage_prompt

logger = logging.getLogger("jira_agent_cli.agent_runner")

DEFAULT_PLAN_MAX_TURNS = 5
DEFAULT_IMPLEMENT_MAX_TURNS = 20
DEFAULT_MAX_BUDGET_USD = 2.0

# ADLC-29: deliberately much cheaper than Plan/Implement's budget — triage is
# a single cheap classification turn over a batch, not exploratory work. See
# docs/plans/0002-richer-polling-triage/spec.md.
DEFAULT_TRIAGE_MAX_TURNS = 2
DEFAULT_TRIAGE_MAX_BUDGET_USD = 0.5


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
    output_schema: dict | None = None,
) -> AgentRunResult:
    """Plan phase: `permission_mode="plan"` — explore without editing, no repo writes.

    Also requests structured output (ADLC-28) via `output_format`, defaulting to
    `PLAN_OUTPUT_SCHEMA` — confirmed by spike to work cleanly alongside plan-mode's
    exploratory multi-turn tool-use loop. Pass `output_schema` to reuse this same
    Plan-phase wiring for a different structured-output shape without duplicating
    it — e.g. decompose.py passes `prompts.PLAN_DECOMPOSE_SCHEMA` (ADLC-46), the
    first caller of this seam.
    """
    options = ClaudeAgentOptions(
        cwd=cwd,
        model=model,
        permission_mode="plan",
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_format={"type": "json_schema", "schema": output_schema or PLAN_OUTPUT_SCHEMA},
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


async def run_triage(
    candidates: list[dict],
    *,
    cwd: str,
    model: str | None = None,
    max_turns: int = DEFAULT_TRIAGE_MAX_TURNS,
    max_budget_usd: float = DEFAULT_TRIAGE_MAX_BUDGET_USD,
) -> list[TriageDecision]:
    """Triage step (ADLC-29): one Plan-phase-shaped turn classifying a whole
    batch of candidate issues as plan/skip/flag. Reuses `run_plan()`'s
    `output_schema=` seam (ADLC-46) rather than hand-rolling new
    `ClaudeAgentOptions` wiring — same `permission_mode="plan"`, same
    structured-output mechanism, just `TRIAGE_OUTPUT_SCHEMA` in place of
    `PLAN_OUTPUT_SCHEMA`/`PLAN_DECOMPOSE_SCHEMA`. No Jira tools are ever
    registered on the SDK `query()` call in this module (no `mcp_servers`),
    so "no live Jira tool access" already holds here exactly like it does for
    Plan/Implement — nothing extra to restrict.

    `candidates` is a list of plain dicts (already-rendered issue snapshots,
    see `orchestrator.execute_triage`), not `Issue` objects — the agent only
    ever sees the compact JSON this function renders into the prompt.

    Never raises on a malformed/partial structured-output shape or an
    out-of-batch issue key — those decisions are dropped (logged) rather
    than crashing the triage cycle; only a genuine agent-run failure
    (`AgentRunError`, e.g. `error_max_turns`/budget exceeded) propagates,
    same as `run_plan`/`run_implement`.
    """
    prompt = render_triage_prompt(candidates)
    result = await run_plan(
        prompt,
        cwd=cwd,
        model=model,
        max_turns=max_turns,
        max_budget_usd=max_budget_usd,
        output_schema=TRIAGE_OUTPUT_SCHEMA,
    )

    valid_keys = {c.get("key") for c in candidates if c.get("key")}
    decisions_raw = (result.structured or {}).get("decisions") or []
    if not isinstance(decisions_raw, list):
        logger.warning("triage: structured output 'decisions' was not a list, dropping batch")
        return []

    decisions: list[TriageDecision] = []
    for raw in decisions_raw:
        try:
            decision = TriageDecision.model_validate(raw)
        except ValidationError:
            logger.warning("triage: dropping malformed decision %r", raw, exc_info=True)
            continue
        if decision.issue_key not in valid_keys:
            logger.warning(
                "triage: dropping decision for %r — not in the input batch", decision.issue_key
            )
            continue
        decisions.append(decision)

    return decisions
