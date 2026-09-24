"""Run state — Symphony's §7.2 11-state run-attempt lifecycle, cut down to 5
states for this POC: just enough to gate the Implement phase behind an
approved Plan, not a full retry/reconciliation model. See docs/spec.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from pydantic import BaseModel, Field


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
    error: str | None = None


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

    def get(self, run_id: str) -> Run | None:
        return self._runs.get(run_id)
