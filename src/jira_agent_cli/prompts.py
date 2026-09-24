"""Renders the Plan/Implement prompt templates documented in
docs/WORKFLOW.md. Deliberately plain `str.format()`, not Jinja2 — this
POC's placeholders are flat fields, no conditionals/loops needed (see
docs/plan.md). If that changes, add Jinja2 then, not preemptively.
"""

from __future__ import annotations

import json

from jira_agent_cli.models import Issue

# Structured output schema for the Plan phase (ADLC-28) — foundation for
# ADLC-46's Epic/Story/Task decomposition schema, deliberately minimal here.
# Confirmed via a throwaway spike (see docs/spec.md) that
# `output_format={"type": "json_schema", "schema": PLAN_OUTPUT_SCHEMA}`
# survives permission_mode="plan"'s exploratory multi-turn tool-use loop and
# lands on ResultMessage.structured_output matching this shape.
PLAN_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "files_changed": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["summary", "files_changed", "risks"],
}

# Structured output schema for the decompose command (ADLC-46) — reuses
# agent_runner.run_plan()'s same Plan-phase wiring (permission_mode="plan" +
# output_format/json_schema) with this schema in place of PLAN_OUTPUT_SCHEMA.
# Deliberately does NOT include `definition_of_done`: that list is templated
# from CLAUDE.md's own testing/lint/style standards in decompose.py
# (DEFINITION_OF_DONE_TEMPLATE), not invented by the LLM per run — an explicit
# design decision, see decompose.py's module docstring.
PLAN_DECOMPOSE_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "user_story": {"type": "string"},
                    "acceptance_criteria": {"type": "array", "items": {"type": "string"}},
                    "tasks": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "title": {"type": "string"},
                                "description": {"type": "string"},
                            },
                            "required": ["title", "description"],
                        },
                    },
                },
                "required": ["title", "user_story", "acceptance_criteria", "tasks"],
            },
        },
    },
    "required": ["stories"],
}

# Structured output schema for the triage step (ADLC-29,
# docs/plans/0002-richer-polling-triage/spec.md) — reuses
# agent_runner.run_plan()'s Plan-phase wiring (ADLC-28/ADLC-46's seam) with
# this schema in place of PLAN_OUTPUT_SCHEMA. One triage turn classifies a
# whole batch of candidate issues, so the shape is a list of decisions, not a
# single one.
TRIAGE_OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "decisions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "issue_key": {"type": "string"},
                    "action": {"type": "string", "enum": ["plan", "skip", "flag"]},
                    "reason": {"type": "string"},
                },
                "required": ["issue_key", "action", "reason"],
            },
        },
    },
    "required": ["decisions"],
}

TRIAGE_PROMPT_TEMPLATE = """You are triaging a batch of newly-discovered Jira tickets before \
they are handed to an automated Plan phase. This is an unattended orchestration session \
running in permission_mode="plan" with no Jira tool access at all — you cannot read live \
Jira, add a comment, or transition any ticket. The only information you have about these \
tickets is the JSON batch below; do not invent or reference any issue key that is not in it.

For each ticket in the batch, decide exactly one action:

- "plan": the ticket is well-formed enough for an automated Plan phase to attempt — it has a
  concrete summary and a description that says what should actually change.
- "skip": the ticket is malformed in a way no amount of planning fixes on its own — e.g. an
  empty or placeholder description ("TBD", "N/A", a single word, just the summary repeated),
  or a summary that's a near-duplicate of another ticket already in this same batch.
- "flag": the ticket might be legitimate but needs a human's judgment before an agent should
  attempt it — e.g. it's ambiguous, unusually high-risk, or conflicts with another ticket in
  this batch.

Candidate tickets:
{candidates_json}

Return exactly one decision per ticket above (the same set of issue keys, no more and no
fewer), each with a short one-sentence reason."""

PLAN_PROMPT_TEMPLATE = """You are looking at Jira ticket {key}: "{summary}" (status: {status}).

Description:
{description}

This is an unattended orchestration session running in permission_mode="plan" — you
cannot edit files or run commands with side effects, only read and explore.

Write a short, concrete implementation plan for this ticket: what files would change,
what the approach is, and any risks or open questions. Do not write code. End with a
one-paragraph summary suitable for posting as a Jira comment."""

IMPLEMENT_PROMPT_TEMPLATE = """Continue from the plan you wrote for {key} in the previous turn
(same session, resumed). Implement it now: make the file changes, and write or update
tests for anything you change. Stop when you're done — do not open a PR or push; a
human reviews the diff next."""

DECOMPOSE_PROMPT_TEMPLATE = """You are looking at Jira Epic {key}: "{summary}" (status: {status}).

Description:
{description}

This is an unattended orchestration session running in permission_mode="plan" — you
cannot edit files or run commands with side effects, only read and explore this
repository to ground your decomposition in its actual structure and conventions.

Break this Epic down into Stories. For each Story, write a concrete user story
("As a ..., I want ..., so that ..."), Acceptance Criteria grounded in the Epic's
actual content (not generic boilerplate), and the concrete Tasks needed to implement
it. Keep each Story scoped to something independently shippable. Do not write code,
and do not invent a Definition of Done — that is templated separately from this
repo's own engineering standards, not something you should decide."""


def render_plan_prompt(issue: Issue) -> str:
    return PLAN_PROMPT_TEMPLATE.format(
        key=issue.key,
        summary=issue.summary,
        status=issue.status,
        description=issue.description or "No description provided.",
    )


def render_implement_prompt(issue_key: str) -> str:
    return IMPLEMENT_PROMPT_TEMPLATE.format(key=issue_key)


def render_decompose_prompt(issue: Issue) -> str:
    return DECOMPOSE_PROMPT_TEMPLATE.format(
        key=issue.key,
        summary=issue.summary,
        status=issue.status,
        description=issue.description or "No description provided.",
    )


def render_triage_prompt(candidates: list[dict]) -> str:
    """`candidates` is the compact per-issue snapshot built by
    `orchestrator.execute_triage` (key/summary/description/status) — see that
    module for why `type`/`created` from the original design doc aren't
    included (`Issue` doesn't carry them today, and adding that would mean a
    client.py/tools.py change this design doc says isn't needed).
    """
    return TRIAGE_PROMPT_TEMPLATE.format(candidates_json=json.dumps(candidates, indent=2))
