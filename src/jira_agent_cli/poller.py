"""Background poll-tick — Symphony §8's actual orchestrator pattern, scoped
down per docs/plans/0001-symphony-poc/intent.md's non-goals: auto-discovers
and auto-Plans new tickets, but never auto-Implements. Implement stays a
deliberate call a human makes via POST /runs/{id}/implement — see
docs/plans/0001-symphony-poc/spec.md's "Auto-discovery" section for why (the
ADLC-17 finding: acceptEdits can't self-verify, so an unattended
Plan->Implement chain would ship unverified code with nobody watching).

Decided 2026-09-24: this runs as a background task inside the API server's
own process (not /loop) — a real daemon independent of any open terminal.
Ready signal: any ADLC ticket with status "To Do" (board default, not a
label) — see docs/plans/0001-symphony-poc/intent.md's Tracking table for the
decision log.
"""

from __future__ import annotations

import asyncio
import logging
import os

from jira_agent_cli.agent_runner import AgentRunError
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.orchestrator import execute_plan, jira_safe_comment_body
from jira_agent_cli.runs import Run, RunState, RunStore, RunTransitionError
from jira_agent_cli.workspace import WorkspaceError, WorkspaceManager

# Errors execute_plan can raise per-ticket — caught individually below so an
# unexpected exception type (a real bug) still surfaces loudly instead of
# being swallowed as if it were just "this ticket's plan failed".
_PLAN_FAILURE_TYPES = (JiraError, RunTransitionError, WorkspaceError, AgentRunError)

logger = logging.getLogger("jira_agent_cli.poller")

DEFAULT_POLL_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_RESULTS = 25

# Symphony §8.4-style bounded retry: a failed auto-Plan used to be a dead
# end (the ticket sat at "To Do" forever, silently, with no comment and no
# further attempts -- confirmed live, ADLC-44 hit error_max_turns and left
# no trace). A fixed number of retries at the normal poll cadence (crude
# backoff, not exponential -- good enough for this POC) plus a "giving up"
# comment closes that loop without hammering a ticket that fails
# deterministically forever.
DEFAULT_MAX_PLAN_ATTEMPTS = 3

# Overridable per adopting repo/project — see docs/PORTABILITY.md. Defaults to
# ADLC (this POC's own board) for backward compatibility.
JIRA_PROJECT = os.environ.get("JIRA_AGENT_CLI_JIRA_PROJECT", "ADLC")

# Epic/Subtask excluded: you can't "implement" an Epic, and a Subtask's parent
# Story is the actual unit of work in this POC's model.
READY_JQL = (
    f'project = {JIRA_PROJECT} AND status = "To Do" '
    "AND issuetype not in (Epic, Subtask) ORDER BY created ASC"
)


class Poller:
    """Owns the background asyncio task. `start()`/`stop()` are called from
    api.py's lifespan; `_seen` is an in-memory idempotency guard on top of
    `RunStore.get_by_issue_key` (belt and suspenders — see docs/plans/
    0001-symphony-poc/spec.md's note on RunStore not surviving a process
    restart) for a ticket whose Plan *succeeded* but whose best-effort status
    transition to "In Review" failed -- without it, a successful Plan would
    get silently re-run forever. `_attempts`/`_given_up` are a separate,
    bounded-retry guard for a ticket whose Plan *failed*.
    """

    def __init__(
        self,
        *,
        client: JiraClient,
        runs: RunStore,
        workspaces: WorkspaceManager,
        interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_plan_attempts: int = DEFAULT_MAX_PLAN_ATTEMPTS,
    ) -> None:
        self._client = client
        self._runs = runs
        self._workspaces = workspaces
        self._interval = interval_seconds
        self._max_plan_attempts = max_plan_attempts
        self._seen: set[str] = set()
        self._attempts: dict[str, int] = {}
        self._given_up: set[str] = set()
        self._task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass
        self._task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except JiraError:
                logger.warning("poll tick: search_issues failed", exc_info=True)
            await asyncio.sleep(self._interval)

    async def tick(self) -> list[Run]:
        """One poll cycle: find ready issues, dispatch Plan for the new ones.
        Public (not `_tick`) so tests can call it directly instead of waiting
        on real asyncio timing.
        """
        result = self._client.search_issues(READY_JQL, max_results=DEFAULT_MAX_RESULTS)

        dispatched: list[Run] = []
        for issue in result.issues:
            if issue.key in self._seen or issue.key in self._given_up:
                continue

            existing = self._runs.get_by_issue_key(issue.key)
            if existing is None:
                run = self._runs.create(issue.key)
            elif existing.state == RunState.FAILED:
                if self._attempts.get(issue.key, 0) >= self._max_plan_attempts:
                    # Already retried the max number of times on an earlier
                    # tick and should have been added to _given_up then --
                    # belt and suspenders in case that branch was skipped.
                    self._given_up.add(issue.key)
                    continue
                run = self._runs.reset_for_retry(existing.id)
            else:
                # In-flight from another path (e.g. a manual /runs POST) --
                # not this poller's dispatch to make, leave it alone.
                continue

            logger.info(
                "poll: auto-planning %s (attempt %d/%d)",
                issue.key,
                self._attempts.get(issue.key, 0) + 1,
                self._max_plan_attempts,
            )
            try:
                dispatched.append(
                    await execute_plan(
                        run, runs=self._runs, client=self._client, workspaces=self._workspaces
                    )
                )
                self._seen.add(issue.key)
            except _PLAN_FAILURE_TYPES:
                logger.warning("poll: execute_plan failed for %s", issue.key, exc_info=True)
                attempts = self._attempts.get(issue.key, 0) + 1
                self._attempts[issue.key] = attempts
                if attempts >= self._max_plan_attempts:
                    self._given_up.add(issue.key)
                    self._post_give_up_comment(issue.key, attempts)

        return dispatched

    def _post_give_up_comment(self, issue_key: str, attempts: int) -> None:
        # Best-effort, same reasoning as orchestrator._post_failure_comment --
        # a failed comment post here must not crash the poll loop.
        try:
            self._client.add_comment(
                issue_key,
                jira_safe_comment_body(
                    f"[jira-agent-cli plan] Gave up after {attempts} failed auto-Plan "
                    "attempts. Not retrying automatically anymore -- needs a human look "
                    "(check the failure comments above), or POST /runs to retry manually."
                ),
            )
        except JiraError:
            logger.warning("could not post give-up comment for %s", issue_key, exc_info=True)
