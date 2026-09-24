"""Background poll-tick — Symphony §8's actual orchestrator pattern, scoped
down per docs/intent.md's non-goals: auto-discovers and auto-Plans new
tickets, but never auto-Implements. Implement stays a deliberate call a
human makes via POST /runs/{id}/implement — see docs/spec.md's "Auto-
discovery" section for why (the ADLC-17 finding: acceptEdits can't
self-verify, so an unattended Plan->Implement chain would ship unverified
code with nobody watching).

Decided 2026-09-24: this runs as a background task inside the API server's
own process (not /loop) — a real daemon independent of any open terminal.
Ready signal: any ADLC ticket with status "To Do" (board default, not a
label) — see docs/intent.md's Tracking table for the decision log.
"""

from __future__ import annotations

import asyncio
import logging

from jira_agent_cli.agent_runner import AgentRunError
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.orchestrator import execute_plan
from jira_agent_cli.runs import Run, RunStore, RunTransitionError
from jira_agent_cli.workspace import WorkspaceError, WorkspaceManager

# Errors execute_plan can raise per-ticket — caught individually below so an
# unexpected exception type (a real bug) still surfaces loudly instead of
# being swallowed as if it were just "this ticket's plan failed".
_PLAN_FAILURE_TYPES = (JiraError, RunTransitionError, WorkspaceError, AgentRunError)

logger = logging.getLogger("jira_agent_cli.poller")

DEFAULT_POLL_INTERVAL_SECONDS = 60.0
DEFAULT_MAX_RESULTS = 25

# Epic/Subtask excluded: you can't "implement" an Epic, and a Subtask's parent
# Story is the actual unit of work in this POC's model.
READY_JQL = (
    'project = ADLC AND status = "To Do" AND issuetype not in (Epic, Subtask) ORDER BY created ASC'
)


class Poller:
    """Owns the background asyncio task. `start()`/`stop()` are called from
    api.py's lifespan; `_seen` is an in-memory idempotency guard on top of
    `RunStore.get_by_issue_key` (belt and suspenders — see docs/plan.md's
    note on RunStore not surviving a process restart).
    """

    def __init__(
        self,
        *,
        client: JiraClient,
        runs: RunStore,
        workspaces: WorkspaceManager,
        interval_seconds: float = DEFAULT_POLL_INTERVAL_SECONDS,
    ) -> None:
        self._client = client
        self._runs = runs
        self._workspaces = workspaces
        self._interval = interval_seconds
        self._seen: set[str] = set()
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
            if issue.key in self._seen or self._runs.get_by_issue_key(issue.key) is not None:
                self._seen.add(issue.key)
                continue
            self._seen.add(issue.key)
            logger.info("poll: auto-planning %s", issue.key)
            run = self._runs.create(issue.key)
            try:
                dispatched.append(
                    await execute_plan(
                        run, runs=self._runs, client=self._client, workspaces=self._workspaces
                    )
                )
            except _PLAN_FAILURE_TYPES:
                logger.warning("poll: execute_plan failed for %s", issue.key, exc_info=True)

        return dispatched
