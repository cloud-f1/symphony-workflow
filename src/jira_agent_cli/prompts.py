"""Renders the Plan/Implement prompt templates documented in
docs/WORKFLOW.md. Deliberately plain `str.format()`, not Jinja2 — this
POC's placeholders are flat fields, no conditionals/loops needed (see
docs/plan.md). If that changes, add Jinja2 then, not preemptively.
"""

from __future__ import annotations

from jira_agent_cli.models import Issue

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


def render_plan_prompt(issue: Issue) -> str:
    return PLAN_PROMPT_TEMPLATE.format(
        key=issue.key,
        summary=issue.summary,
        status=issue.status,
        description=issue.description or "No description provided.",
    )


def render_implement_prompt(issue_key: str) -> str:
    return IMPLEMENT_PROMPT_TEMPLATE.format(key=issue_key)
