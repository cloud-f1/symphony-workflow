"""Run state — Symphony's §7.2 11-state run-attempt lifecycle, cut down to 5
states for this POC: just enough to gate the Implement phase behind an
approved Plan, not a full retry/reconciliation model. See docs/spec.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from symphony_workflow.models import TriageDecision


class RunState(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    PLAN_READY = "plan_ready"
    IMPLEMENTING = "implementing"
    COMPLETED = "completed"
    FAILED = "failed"


class Run(BaseModel):
    id: str
    issue_key: str
    state: RunState = RunState.CREATED
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    plan: str | None = None
    plan_structured: dict | None = None
    session_id: str | None = None
    workspace_path: str | None = None
    result: str | None = None
    error: str | None = None


class RunTransitionError(RuntimeError):
    """Raised when a Run is asked to transition from a state that forbids it —
    in particular, Implement requires state == plan_ready. That check is the
    human-review gate from docs/spec.md; it is enforced here, not just by
    convention in the API layer, so nothing can bypass it by calling
    RunStore directly.
    """


class RunStore:
    """In-memory run store.

    This is a deliberate POC simplification of Symphony's tracker/filesystem-
    driven restart recovery (§7.4): runs do not survive a process restart.
    Fine for a bounded, human-triggered POC run; not fine for anything meant
    to stay up unattended — see docs/intent.md's non-goals.
    """

    def __init__(self) -> None:
        self._runs: dict[str, Run] = {}

    def create(self, issue_key: str) -> Run:
        run = Run(id=str(uuid.uuid4()), issue_key=issue_key)
        self._runs[run.id] = run
        return run

    def load(self, run: Run) -> Run:
        """Register an already-built `Run` (e.g. rehydrated from the CLI's
        on-disk sidecar, see cli.py's `plan`/`implement` commands) into a
        fresh store, instead of `create()`'s always-new `created`-state Run.
        Lets a later state-mutating call (e.g. `mark_implementing`) find it
        via `_require` without going through the normal create->plan flow.
        """
        self._runs[run.id] = run
        return run

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)

    def get_by_issue_key(self, issue_key: str) -> Run | None:
        """Used by poller.py's idempotency check — one issue key gets at most one
        Run from auto-discovery, even across multiple poll ticks.
        """
        return next((r for r in self._runs.values() if r.issue_key == issue_key), None)

    def all(self) -> list[Run]:
        """Used by the Symphony §13.7-style interface API (`GET /api/v1/state`)."""
        return list(self._runs.values())

    def mark_planning(self, run_id: str) -> Run:
        run = self._require(run_id)
        self._require_state(run, RunState.CREATED)
        run.state = RunState.PLANNING
        return run

    def set_plan_ready(
        self,
        run_id: str,
        *,
        plan: str,
        session_id: str,
        workspace_path: str,
        plan_structured: dict | None = None,
    ) -> Run:
        run = self._require(run_id)
        self._require_state(run, RunState.PLANNING)
        run.state = RunState.PLAN_READY
        run.plan = plan
        run.plan_structured = plan_structured
        run.session_id = session_id
        run.workspace_path = workspace_path
        return run

    def mark_implementing(self, run_id: str) -> Run:
        run = self._require(run_id)
        self._require_state(run, RunState.PLAN_READY)
        run.state = RunState.IMPLEMENTING
        return run

    def mark_completed(self, run_id: str, *, result: str) -> Run:
        run = self._require(run_id)
        self._require_state(run, RunState.IMPLEMENTING)
        run.state = RunState.COMPLETED
        run.result = result
        return run

    def mark_failed(self, run_id: str, *, error: str) -> Run:
        run = self._require(run_id)
        run.state = RunState.FAILED
        run.error = error
        return run

    def reset_for_retry(self, run_id: str) -> Run:
        """Bring a `failed` Run back to `created` so it can be retried (Symphony
        §8.4-style bounded retry, see poller.py). Reuses the same Run rather
        than creating a new one, so `get_by_issue_key`'s "one issue key, one
        Run" invariant holds across retries -- a later lookup always finds the
        current attempt, not a stale failed one.
        """
        run = self._require(run_id)
        self._require_state(run, RunState.FAILED)
        run.state = RunState.CREATED
        run.error = None
        return run

    def _require(self, run_id: str) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise KeyError(f"Run {run_id!r} not found")
        return run

    @staticmethod
    def _require_state(run: Run, expected: RunState) -> None:
        if run.state != expected:
            raise RunTransitionError(
                f"Run {run.id} is {run.state.value!r}, expected {expected.value!r}"
            )


class TriageStore:
    """In-memory triage-decision store (ADLC-29, same POC in-memory
    simplification as `RunStore` above — see `RunStore`'s own docstring on
    why this doesn't survive a process restart).

    Records every decision (`plan`/`skip`/`flag`) so `poller.py`'s `tick()`
    can tell "not yet triaged" (queue it) apart from "already triaged"
    (dispatch or drop per the recorded action) purely by presence in this
    store — see docs/plans/0002-richer-polling-triage/spec.md's
    "Non-determinism & idempotency" risk, and `orchestrator.execute_triage`'s
    docstring for why `plan` is recorded too, not just `skip`/`flag`.
    """

    def __init__(self) -> None:
        self._decisions: dict[str, TriageDecision] = {}

    def record(self, decision: TriageDecision) -> None:
        self._decisions[decision.issue_key] = decision

    def get(self, issue_key: str) -> TriageDecision | None:
        return self._decisions.get(issue_key)

    def all(self) -> list[TriageDecision]:
        return list(self._decisions.values())
