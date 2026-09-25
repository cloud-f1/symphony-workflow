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

from symphony_workflow.agent_runner import AgentRunError
from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.config import DEFAULT_TRIAGE_INTERVAL_SECONDS
from symphony_workflow.models import Issue, TriageDecision
from symphony_workflow.orchestrator import execute_plan, execute_triage, jira_safe_comment_body
from symphony_workflow.runs import Run, RunState, RunStore, RunTransitionError, TriageStore
from symphony_workflow.workspace import WorkspaceError, WorkspaceManager

# Errors execute_plan can raise per-ticket — caught individually below so an
# unexpected exception type (a real bug) still surfaces loudly instead of
# being swallowed as if it were just "this ticket's plan failed".
_PLAN_FAILURE_TYPES = (JiraError, RunTransitionError, WorkspaceError, AgentRunError)

# execute_triage can raise AgentRunError (a genuine agent-run failure, e.g.
# error_max_turns/budget exceeded) -- caught in triage_tick() below so one
# bad triage batch doesn't crash the triage loop, same reasoning as
# _PLAN_FAILURE_TYPES above.
_TRIAGE_FAILURE_TYPES = (AgentRunError,)

logger = logging.getLogger("symphony_workflow.poller")

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
JIRA_PROJECT = os.environ.get("SYMPHONY_WORKFLOW_JIRA_PROJECT", "ADLC")

# Epic/Subtask excluded: you can't "implement" an Epic, and a Subtask's parent
# Story is the actual unit of work in this POC's model.
READY_JQL = (
    f'project = {JIRA_PROJECT} AND status = "To Do" '
    "AND issuetype not in (Epic, Subtask) ORDER BY created ASC"
)


class Poller:
    """Owns the background asyncio task(s). `start()`/`stop()` are called
    from api.py's lifespan; `_seen` is an in-memory idempotency guard on top
    of `RunStore.get_by_issue_key` (belt and suspenders — see docs/plans/
    0001-symphony-poc/spec.md's note on RunStore not surviving a process
    restart) for a ticket whose Plan *succeeded* but whose best-effort status
    transition to "In Review" failed -- without it, a successful Plan would
    get silently re-run forever. `_attempts`/`_given_up` are a separate,
    bounded-retry guard for a ticket whose Plan *failed*.

    ADLC-29 triage (docs/plans/0002-richer-polling-triage/spec.md): a second,
    slower `asyncio` loop (`_triage_loop`/`triage_tick`), independent of the
    60s poll loop's `_loop`/`tick` -- chosen over a tick-counter inside the
    existing loop because the two cadences are genuinely unrelated concerns
    (liveness/dedupe vs. a budget-capped batch agent turn) and a second loop
    keeps `tick()`'s existing behavior and tests completely untouched when
    triage is disabled, rather than threading a counter/conditional through
    it. Gated behind `triage_enabled` (default `false`, see config.py) --
    when off, `start()`/`stop()` never create or touch `_triage_task` at all,
    so this is a no-op addition for anyone who hasn't opted in.
    """

    def __init__(
        self,
        *,
        client: JiraClient,
        runs: RunStore,
        workspaces: WorkspaceManager,
        interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
        max_plan_attempts: int = DEFAULT_MAX_PLAN_ATTEMPTS,
        triage_enabled: bool = False,
        triage_interval_seconds: float = DEFAULT_TRIAGE_INTERVAL_SECONDS,
        triage_store: TriageStore | None = None,
        plan_max_turns: int | None = None,
    ) -> None:
        self._client = client
        self._runs = runs
        self._workspaces = workspaces
        self._interval = interval_seconds
        self._max_plan_attempts = max_plan_attempts
        # ADLC-55: optional override for auto-Plan's agent turn budget --
        # None (the default, see config.poll_plan_max_turns()) preserves
        # today's behavior (agent_runner.DEFAULT_PLAN_MAX_TURNS).
        self._plan_max_turns = plan_max_turns
        self._seen: set[str] = set()
        self._attempts: dict[str, int] = {}
        self._given_up: set[str] = set()
        self._task: asyncio.Task[None] | None = None

        self._triage_enabled = triage_enabled
        self._triage_interval = triage_interval_seconds
        self._triage_store = triage_store if triage_store is not None else TriageStore()
        # Newly-discovered, not-yet-triaged issues, queued by tick() and
        # drained by triage_tick() -- a dict (not a list/set) so re-queueing
        # the same issue on a later tick before it's been drained is a no-op,
        # and so the latest-seen Issue snapshot is what's used.
        self._untriaged: dict[str, Issue] = {}
        self._triage_task: asyncio.Task[None] | None = None

    @property
    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
        if self._triage_enabled and self._triage_task is None:
            self._triage_task = asyncio.create_task(self._triage_loop())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        if self._triage_task is not None:
            self._triage_task.cancel()
            try:
                await self._triage_task
            except asyncio.CancelledError:
                pass
            self._triage_task = None

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except JiraError:
                logger.warning("poll tick: search_issues failed", exc_info=True)
            await asyncio.sleep(self._interval)

    async def _triage_loop(self) -> None:
        while True:
            await asyncio.sleep(self._triage_interval)
            await self.triage_tick()

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
                if self._triage_enabled:
                    decision = self._triage_store.get(issue.key)
                    if decision is None:
                        # Not yet triaged -- queue for the next triage cycle
                        # instead of dispatching Plan immediately (ADLC-29).
                        # `setdefault` so a ticket already queued from an
                        # earlier tick isn't re-added / doesn't reset ordering.
                        self._untriaged.setdefault(issue.key, issue)
                        continue
                    if decision.action != "plan":
                        # "skip"/"flag" -- already fully handled by
                        # execute_triage (comment posted / recorded in
                        # TriageStore). Never becomes a Run, so it never
                        # enters the bounded-retry accounting below either.
                        continue
                    # decision.action == "plan" -- falls through to the same
                    # dispatch path as when triage is disabled.
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
                        run,
                        runs=self._runs,
                        client=self._client,
                        workspaces=self._workspaces,
                        max_turns=self._plan_max_turns,
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

    async def triage_tick(self) -> list[TriageDecision]:
        """One triage cycle: drain the untriaged queue (if non-empty) into a
        single `execute_triage` batch call -- one Claude Agent SDK turn for
        the whole queue, not one per ticket. Public (not `_triage_tick`) for
        the same reason `tick()` is: tests call it directly instead of
        waiting on real asyncio timing. No-op (no agent turn, no Jira calls)
        when the queue is empty, which is the common case at this cadence.
        """
        if not self._untriaged:
            return []

        batch = list(self._untriaged.values())
        self._untriaged.clear()
        try:
            return await execute_triage(batch, client=self._client, triage_store=self._triage_store)
        except _TRIAGE_FAILURE_TYPES:
            logger.warning(
                "triage tick: execute_triage failed for a batch of %d", len(batch), exc_info=True
            )
            # Requeue so this batch is retried on the next triage cycle
            # instead of silently lost -- `setdefault` in case a tick()
            # queued a newer snapshot for one of these keys in the meantime.
            for issue in batch:
                self._untriaged.setdefault(issue.key, issue)
            return []

    def _post_give_up_comment(self, issue_key: str, attempts: int) -> None:
        # Best-effort, same reasoning as orchestrator._post_failure_comment --
        # a failed comment post here must not crash the poll loop.
        try:
            self._client.add_comment(
                issue_key,
                jira_safe_comment_body(
                    f"[symphony-workflow plan] Gave up after {attempts} failed auto-Plan "
                    "attempts. Not retrying automatically anymore -- needs a human look "
                    "(check the failure comments above), or POST /runs to retry manually."
                ),
            )
        except JiraError:
            logger.warning("could not post give-up comment for %s", issue_key, exc_info=True)
