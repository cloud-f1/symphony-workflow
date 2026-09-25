"""Shared Plan/Implement business logic — used by both the HTTP endpoints in
api.py (which translate these exceptions to HTTPException) and poller.py
(which catches and logs them instead of crashing the poll loop). Splitting
this out means the poller and the API can never drift on what "running a
plan" actually does.
"""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from symphony_workflow.agent_runner import (
    AgentRunError,
    AgentRunResult,
    run_implement,
    run_plan,
    run_triage,
)
from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.models import Issue, TriageDecision
from symphony_workflow.prompts import render_implement_prompt, render_plan_prompt
from symphony_workflow.runs import Run, RunState, RunStore, RunTransitionError, TriageStore
from symphony_workflow.workspace import WorkspaceError, WorkspaceManager

logger = logging.getLogger("symphony_workflow.orchestrator")

# REPO_ROOT: where symphony-workflow's own source lives (docs/runs, .worktrees
# default here). TARGET_REPO: the repo Plan/Implement actually work against —
# defaults to REPO_ROOT (this POC plans/implements against itself) but is the
# one override a repo *adopting* symphony-workflow as its orchestrator needs to
# set. See docs/PORTABILITY.md.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TARGET_REPO = Path(os.environ.get("SYMPHONY_WORKFLOW_TARGET_REPO", REPO_ROOT))
RUNS_DOCS_ROOT = Path(
    os.environ.get("SYMPHONY_WORKFLOW_RUNS_DOCS_ROOT", REPO_ROOT / "docs" / "runs")
)
WORKSPACE_ROOT = Path(os.environ.get("SYMPHONY_WORKFLOW_WORKSPACE_ROOT", REPO_ROOT / ".worktrees"))

# Symphony §8.6 terminal-status workspace cleanup. Off by default: removing a
# workspace automatically is a real behavior change (an adopter might still
# want to inspect a finished run's diff on disk), so it's an explicit opt-in
# rather than a surprise. `SYMPHONY_WORKFLOW_TERMINAL_STATUSES` is a comma-
# separated list of Jira status *names* (not IDs — matches this repo's
# existing "match names, not IDs" pattern elsewhere, e.g. transition_issue).
WORKSPACE_CLEANUP_ENV = "SYMPHONY_WORKFLOW_WORKSPACE_CLEANUP_ENABLED"
DEFAULT_TERMINAL_STATUSES = "Done"


def _cleanup_enabled() -> bool:
    # Read at call time, not import time, so tests can toggle this per-test via
    # monkeypatch/setenv — same reasoning as api.py's `_poll_enabled()`.
    return os.environ.get(WORKSPACE_CLEANUP_ENV, "false").lower() == "true"


def _terminal_statuses() -> frozenset[str]:
    raw = os.environ.get("SYMPHONY_WORKFLOW_TERMINAL_STATUSES", DEFAULT_TERMINAL_STATUSES)
    return frozenset(status.strip() for status in raw.split(",") if status.strip())


def cleanup_workspace_if_terminal(
    issue_key: str, *, client: JiraClient, workspaces: WorkspaceManager
) -> bool:
    """Remove `issue_key`'s workspace if its *live* Jira status is terminal
    (Symphony §8.6). Opt-in (see WORKSPACE_CLEANUP_ENV) and best-effort — never
    raises, since cleanup must never take down a Run, a poll tick, or server
    startup. Deliberately checks live Jira status rather than Run state: a Run
    reaching completed/failed doesn't mean the ticket itself has reached Done
    — that's a separate, human-driven transition this repo doesn't automate.
    """
    if not _cleanup_enabled():
        return False
    try:
        issue = client.get_issue(issue_key)
    except JiraError:
        logger.warning("cleanup: could not fetch status for %s", issue_key, exc_info=True)
        return False
    if issue.status not in _terminal_statuses():
        return False
    logger.info("cleanup: %s is %r (terminal) — removing its workspace", issue_key, issue.status)
    return workspaces.remove(issue_key)


def sweep_terminal_workspaces(*, client: JiraClient, workspaces: WorkspaceManager) -> list[str]:
    """Startup sweep (Symphony §8.6): check every existing workspace directory
    against live Jira status and remove the ones whose issue is terminal.
    Called from `api.py`'s `lifespan`. Opt-in via the same env var as
    `cleanup_workspace_if_terminal` — returns immediately (no Jira calls) when
    disabled, so it's cheap to call unconditionally at startup.
    """
    if not _cleanup_enabled() or not workspaces.root.exists():
        return []
    removed = []
    for path in sorted(workspaces.root.iterdir()):
        if not path.is_dir():
            continue
        if cleanup_workspace_if_terminal(path.name, client=client, workspaces=workspaces):
            removed.append(path.name)
    return removed


def render_run_doc(client: JiraClient, issue_key: str, body: str) -> str:
    """Prepends a `jira_url` frontmatter line — the local-md-to-Jira half of
    linking (see CLAUDE.md's "Local md <-> Jira" section). The other half
    (Jira -> local, a remotelink pointing back at the merged GitHub file) can't
    happen here: the file doesn't exist on GitHub until it's committed and
    pushed, well after this function returns — see the `create_remote_link`
    tool, called separately once that's true.
    """
    jira_url = f"{client.base_url}/browse/{issue_key}"
    return f"---\njira_url: {jira_url}\n---\n\n{body}"


_MARKDOWN_ATX_HEADER = re.compile(r"^#{1,6}[ \t]+(.*)$", re.MULTILINE)


def jira_safe_comment_body(text: str) -> str:
    """Jira's v2 `add_comment` body is rendered through wiki markup, not
    GitHub Markdown: a leading `#`/`##`/`###` there means nested
    ORDERED-LIST depth (1 / a / i), not a heading. Posting the agent's raw
    GFM plan (which uses `##`/`###` headers) verbatim collides with that and
    renders as a garbled outline instead of readable prose — confirmed live
    on ADLC-28/ADLC-30's plan comments. `**bold**` renders fine as-is, so
    ATX headers are converted to bold lines rather than stripped bare.
    plan.md is unaffected — it's real GFM, rendered on GitHub.
    """
    return _MARKDOWN_ATX_HEADER.sub(r"**\1**", text)


def _post_failure_comment(client: JiraClient, issue_key: str, phase: str, error: str) -> None:
    """Best-effort visibility for a failed Plan/Implement run — without this,
    a failure was previously silent: the ticket just sat at its current
    status with no indication anything was attempted (confirmed live,
    ADLC-44's auto-Plan hit error_max_turns and left no trace on the ticket).
    Never raises -- a failed comment post must not mask the original error.
    """
    try:
        client.add_comment(
            issue_key,
            jira_safe_comment_body(f"[symphony-workflow {phase}] FAILED: {error}"),
        )
    except JiraError:
        logger.warning("could not post failure comment for %s", issue_key, exc_info=True)


def _render_plan_text(result: AgentRunResult) -> str:
    """ADLC-56: `result.text` is normally the Plan phase's free-text
    explanation, but a session's final turn can end up emitting only the
    structured `output_format` result with no accompanying `TextBlock`
    (confirmed live, see `docs/plans/0003-portability-validation-nexjs/
    implement.md`'s incident record and `docs/runs/NXJS-1/plan.md`) --
    `agent_runner._consume()` then leaves `text_parts` empty and
    `result.text` comes back `""`. Silently writing/posting that empty
    string as plan.md / the Jira comment would throw away a fully populated
    `result.structured` for nothing, so fall back to rendering the
    structured fields when text is empty.

    Deliberately a fallback-when-empty, not an always-append: the ticket's
    own framing ("silently blank") is about the empty-text case specifically
    -- when `result.text` is already non-empty it's returned as-is,
    unaugmented. Always appending structured fields onto an already-useful
    render is a plausible nice-to-have but a different, broader change (a
    render-format redesign) than the bug being fixed here.

    Raises `AgentRunError` when both `result.text` and `result.structured`
    are empty/absent -- a Run reaching `plan_ready` with truly no plan
    content to show is worse than silently succeeding with nothing, and this
    repo's conventions favor never swallowing that kind of failure quietly
    (see CLAUDE.md's "never a bare `except Exception: pass`" rule; this is
    the same spirit applied to a silent-empty-success instead of a silent
    exception).
    """
    if result.text:
        return result.text

    structured = result.structured or {}
    lines: list[str] = []

    summary = structured.get("summary")
    if summary:
        lines.append(str(summary))

    files_changed = structured.get("files_changed")
    if files_changed:
        if lines:
            lines.append("")
        lines.append("Files changed:")
        lines.extend(f"- {f}" for f in files_changed)

    risks = structured.get("risks")
    if risks:
        if lines:
            lines.append("")
        lines.append("Risks:")
        lines.extend(f"- {r}" for r in risks)

    rendered = "\n".join(lines).strip()
    if rendered:
        return rendered

    raise AgentRunError("Agent run produced neither text nor usable structured output")


async def execute_plan(
    run: Run,
    *,
    runs: RunStore,
    client: JiraClient,
    workspaces: WorkspaceManager,
    max_turns: int | None = None,
) -> Run:
    """Read-only: no repo writes. Raises JiraError / RunTransitionError /
    WorkspaceError / AgentRunError on failure — the run is marked failed
    first wherever the failure happened after a state change, so GET
    /runs/{id} always reflects what actually happened.

    `max_turns` (ADLC-55) optionally overrides `agent_runner.run_plan`'s own
    `DEFAULT_PLAN_MAX_TURNS` for this one run — e.g. a large real-world
    target repo (confirmed live against `ai-coding-nexjs-template`,
    docs/plans/0003-portability-validation-nexjs/implement.md) can need more
    than the default 5 turns to produce a real plan. `None` (the default)
    means "don't override" — `run_plan` falls back to its own default,
    exactly today's behavior, so every existing caller is unaffected unless
    it explicitly opts in.
    """
    issue = client.get_issue(run.issue_key)
    runs.mark_planning(run.id)

    try:
        workspace_path = workspaces.ensure(run.issue_key, branch_slug=issue.summary)
    except WorkspaceError as exc:
        runs.mark_failed(run.id, error=str(exc))
        raise

    prompt = render_plan_prompt(issue)
    workspaces.run_before_run(run.issue_key, workspace_path)
    run_plan_kwargs: dict[str, int] = {}
    if max_turns is not None:
        run_plan_kwargs["max_turns"] = max_turns
    try:
        result = await run_plan(prompt, cwd=str(workspace_path), **run_plan_kwargs)
    except AgentRunError as exc:
        runs.mark_failed(run.id, error=str(exc))
        _post_failure_comment(client, run.issue_key, "plan", str(exc))
        cleanup_workspace_if_terminal(run.issue_key, client=client, workspaces=workspaces)
        raise
    finally:
        workspaces.run_after_run(run.issue_key, workspace_path)

    if not result.session_id:
        error = "Agent run produced no session_id — cannot resume for /implement"
        runs.mark_failed(run.id, error=error)
        _post_failure_comment(client, run.issue_key, "plan", error)
        raise AgentRunError(error)

    try:
        plan_text = _render_plan_text(result)
    except AgentRunError as exc:
        runs.mark_failed(run.id, error=str(exc))
        _post_failure_comment(client, run.issue_key, "plan", str(exc))
        raise

    plan_dir = RUNS_DOCS_ROOT / run.issue_key
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "plan.md").write_text(
        render_run_doc(client, run.issue_key, plan_text), encoding="utf-8"
    )

    client.add_comment(
        run.issue_key, jira_safe_comment_body(f"[symphony-workflow plan]\n\n{plan_text}")
    )

    updated = runs.set_plan_ready(
        run.id,
        plan=plan_text,
        session_id=result.session_id,
        workspace_path=str(workspace_path),
        plan_structured=result.structured,
    )

    # Best-effort — a failed status transition shouldn't undo a successful plan.
    try:
        client.transition_issue(run.issue_key, "In Review")
    except JiraError:
        logger.warning("could not transition %s to 'In Review'", run.issue_key, exc_info=True)

    return updated


async def execute_implement(
    run: Run, *, runs: RunStore, client: JiraClient, workspaces: WorkspaceManager
) -> Run:
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
    workspace_path = Path(run.workspace_path)

    prompt = render_implement_prompt(run.issue_key)
    workspaces.run_before_run(run.issue_key, workspace_path)
    try:
        result = await run_implement(
            prompt, cwd=run.workspace_path, resume_session_id=run.session_id
        )
    except AgentRunError as exc:
        runs.mark_failed(run.id, error=str(exc))
        _post_failure_comment(client, run.issue_key, "implement", str(exc))
        cleanup_workspace_if_terminal(run.issue_key, client=client, workspaces=workspaces)
        raise
    finally:
        workspaces.run_after_run(run.issue_key, workspace_path)

    updated = runs.mark_completed(run.id, result=result.text)

    try:
        client.transition_issue(run.issue_key, "In Progress")
    except JiraError:
        logger.warning("could not transition %s to 'In Progress'", run.issue_key, exc_info=True)

    # Best-effort — e.g. the ticket was already Done before Implement ran, or
    # got closed out-of-band mid-run. The common case (a human reviews the
    # diff and transitions the ticket later) is caught by the startup sweep.
    cleanup_workspace_if_terminal(run.issue_key, client=client, workspaces=workspaces)

    return updated


async def execute_triage(
    candidates: list[Issue], *, client: JiraClient, triage_store: TriageStore
) -> list[TriageDecision]:
    """ADLC-29: one triage agent turn for a whole batch of newly-discovered
    candidate issues, then code (not the agent) applies each decision:

    - `plan` — no Jira-visible side effect. The poller's normal `execute_plan`
      dispatch path picks it up on its next tick, unchanged (see poller.py's
      `tick()`).
    - `flag` — posts a Jira comment via `client.add_comment` (best-effort,
      same swallow-and-log pattern as `_post_failure_comment`). No status
      transition — this repo doesn't have live workflow-graph introspection
      (docs/plans/0002-richer-polling-triage/intent.md's non-goals).
    - `skip` — no Jira-visible side effect either; the ticket is just never
      dispatched. Never becomes a `Run`, so it never enters `poller.py`'s
      bounded-retry accounting (ADLC-44) either.

    Every decision (including `plan`) is recorded in `triage_store` — not
    just `flag`/`skip` — so `poller.py`'s `tick()` can tell "not yet triaged"
    (queue it) apart from "triaged as plan, safe to dispatch now" purely by
    presence in the store; without recording `plan` too, a plan-decided
    ticket would look identical to a brand-new, never-queued one on the very
    next tick and get re-queued for triage forever instead of dispatched.

    The agent itself never gets `add_comment`/`transition_issue` tools — only
    this function, after the agent returns, applies anything Jira-visible.
    Empty `candidates` is a no-op (no agent turn) so an empty triage cycle
    costs nothing. Propagates `AgentRunError` from `run_triage` (a genuine
    agent-run failure, e.g. budget/turn limit) — the caller (poller.py's
    `triage_tick`) decides how to handle that, same split as
    `execute_plan`/`execute_implement` raising instead of swallowing.
    """
    if not candidates:
        return []

    payload = [
        {
            "key": issue.key,
            "summary": issue.summary,
            "description": issue.description or "",
            "status": issue.status,
        }
        for issue in candidates
    ]
    decisions = await run_triage(payload, cwd=str(TARGET_REPO))

    for decision in decisions:
        if decision.action == "flag":
            try:
                client.add_comment(
                    decision.issue_key,
                    jira_safe_comment_body(
                        f"[symphony-workflow triage] Flagged for human review before "
                        f"auto-Plan: {decision.reason}"
                    ),
                )
            except JiraError:
                logger.warning(
                    "triage: could not post flag comment for %s", decision.issue_key, exc_info=True
                )
        triage_store.record(decision)

    return decisions
