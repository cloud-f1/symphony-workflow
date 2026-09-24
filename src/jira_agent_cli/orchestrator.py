"""Shared Plan/Implement business logic — used by both the HTTP endpoints in
api.py (which translate these exceptions to HTTPException) and poller.py
(which catches and logs them instead of crashing the poll loop). Splitting
this out means the poller and the API can never drift on what "running a
plan" actually does.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from jira_agent_cli.agent_runner import AgentRunError, run_implement, run_plan
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.prompts import render_implement_prompt, render_plan_prompt
from jira_agent_cli.runs import Run, RunState, RunStore, RunTransitionError
from jira_agent_cli.workspace import WorkspaceError, WorkspaceManager

logger = logging.getLogger("jira_agent_cli.orchestrator")

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DOCS_ROOT = Path(os.environ.get("JIRA_AGENT_CLI_RUNS_DOCS_ROOT", REPO_ROOT / "docs" / "runs"))
WORKSPACE_ROOT = Path(os.environ.get("JIRA_AGENT_CLI_WORKSPACE_ROOT", REPO_ROOT / ".worktrees"))


async def execute_plan(
    run: Run, *, runs: RunStore, client: JiraClient, workspaces: WorkspaceManager
) -> Run:
    """Read-only: no repo writes. Raises JiraError / RunTransitionError /
    WorkspaceError / AgentRunError on failure — the run is marked failed
    first wherever the failure happened after a state change, so GET
    /runs/{id} always reflects what actually happened.
    """
    issue = client.get_issue(run.issue_key)
    runs.mark_planning(run.id)

    try:
        workspace_path = workspaces.ensure(run.issue_key)
    except WorkspaceError as exc:
        runs.mark_failed(run.id, error=str(exc))
        raise

    prompt = render_plan_prompt(issue)
    try:
        result = await run_plan(prompt, cwd=str(workspace_path))
    except AgentRunError as exc:
        runs.mark_failed(run.id, error=str(exc))
        raise

    if not result.session_id:
        error = "Agent run produced no session_id — cannot resume for /implement"
        runs.mark_failed(run.id, error=error)
        raise AgentRunError(error)

    plan_dir = RUNS_DOCS_ROOT / run.issue_key
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "plan.md").write_text(result.text, encoding="utf-8")

    client.add_comment(run.issue_key, f"[jira-agent-cli plan]\n\n{result.text}")

    updated = runs.set_plan_ready(
        run.id,
        plan=result.text,
        session_id=result.session_id,
        workspace_path=str(workspace_path),
    )

    # Best-effort — a failed status transition shouldn't undo a successful plan.
    try:
        client.transition_issue(run.issue_key, "In Review")
    except JiraError:
        logger.warning("could not transition %s to 'In Review'", run.issue_key, exc_info=True)

    return updated


async def execute_implement(run: Run, *, runs: RunStore, client: JiraClient) -> Run:
    """Hard-gated on state == plan_ready (RunTransitionError otherwise, raised
    by mark_implementing — the same check RunStore enforces everywhere else).
    Never opens a PR or pushes; a human reviews the diff next.
    """
    if run.state != RunState.PLAN_READY:
        raise RunTransitionError(
            f"Run {run.id} is {run.state.value!r}, expected 'plan_ready' — plan not approved yet"
        )

    runs.mark_implementing(run.id)

    assert run.session_id is not None  # guaranteed by set_plan_ready
    assert run.workspace_path is not None

    prompt = render_implement_prompt(run.issue_key)
    try:
        result = await run_implement(
            prompt, cwd=run.workspace_path, resume_session_id=run.session_id
        )
    except AgentRunError as exc:
        runs.mark_failed(run.id, error=str(exc))
        raise

    updated = runs.mark_completed(run.id, result=result.text)

    try:
        client.transition_issue(run.issue_key, "In Progress")
    except JiraError:
        logger.warning("could not transition %s to 'In Progress'", run.issue_key, exc_info=True)

    return updated
