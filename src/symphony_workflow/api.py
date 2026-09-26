"""FastAPI server — Symphony §13.7's OPTIONAL HTTP Server Extension, adapted.

`GET /tools` / `POST /tools/{name}/call` expose the same tool registry the
CLI's `list-tools` / `call` commands use, over HTTP, so a remote agent or
orchestrator can drive Jira the same way a human at a terminal can.
`POST /runs/{id}/plan` and `POST /runs/{id}/implement` are the two-phase
interface from docs/spec.md — `/implement` is hard-gated on `state ==
plan_ready`, enforced in runs.py's RunStore, not just here. Both endpoints
are thin wrappers over orchestrator.py, which the background poller
(poller.py) also calls — so the two dispatch paths can never drift.

Auto-discovery (Phase 5, docs/spec.md): a background poll-tick started in
`lifespan` auto-creates a Run and auto-runs Plan for new "To Do" tickets.
It never auto-runs Implement — that stays a deliberate POST a human makes.
Disable via `SYMPHONY_WORKFLOW_POLL_ENABLED=false` (tests do this).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError

from symphony_workflow.agent_runner import AgentRunError
from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.config import ConfigError as EnvConfigError
from symphony_workflow.config import (
    poll_plan_max_turns,
    triage_enabled,
    triage_interval_seconds,
)
from symphony_workflow.orchestrator import (
    TARGET_REPO,
    WORKSPACE_ROOT,
    execute_implement,
    execute_plan,
    sweep_terminal_workspaces,
)
from symphony_workflow.poller import Poller
from symphony_workflow.runs import Run, RunState, RunStore, RunTransitionError
from symphony_workflow.tools import TOOLS, call_tool
from symphony_workflow.tracker import ConfigError as BackendConfigError
from symphony_workflow.tracker import TrackerClient, TrackerError, get_tracker_client
from symphony_workflow.workspace import WorkspaceError, WorkspaceManager


def _poll_enabled() -> bool:
    # Read at lifespan-startup time, not module-import time, so tests can
    # monkeypatch/setenv this per-test before their TestClient starts.
    return os.environ.get("SYMPHONY_WORKFLOW_POLL_ENABLED", "true").lower() != "false"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        client = get_tracker_client()
    except (EnvConfigError, BackendConfigError) as exc:
        raise RuntimeError(str(exc)) from exc
    app.state.tracker_client = client
    app.state.runs = RunStore()
    app.state.workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=TARGET_REPO)

    # Symphony §8.6 startup sweep — opt-in (SYMPHONY_WORKFLOW_WORKSPACE_CLEANUP_ENABLED,
    # default off), no-op and no tracker calls otherwise.
    sweep_terminal_workspaces(client=app.state.tracker_client, workspaces=app.state.workspaces)

    poller = Poller(
        client=app.state.tracker_client,
        runs=app.state.runs,
        workspaces=app.state.workspaces,
        interval_seconds=float(os.environ.get("SYMPHONY_WORKFLOW_POLL_INTERVAL_SECONDS", "60")),
        # ADLC-29 — default false via config.triage_enabled(), so this is a
        # no-op for anyone who hasn't opted in (docs/plans/0002-richer-
        # polling-triage/intent.md's definition of done).
        triage_enabled=triage_enabled(),
        triage_interval_seconds=triage_interval_seconds(),
        # ADLC-55 — unset by default (config.poll_plan_max_turns() returns
        # None), so this is a no-op for anyone who hasn't opted in.
        plan_max_turns=poll_plan_max_turns(),
    )
    app.state.poller = poller
    if _poll_enabled():
        poller.start()

    try:
        yield
    finally:
        await poller.stop()
        app.state.tracker_client.close()


app = FastAPI(
    title="symphony-workflow",
    description="Jira Cloud + Linear tool-call server — see docs/spec.md",
    lifespan=lifespan,
)


def get_tracker(request: Request) -> TrackerClient:
    return request.app.state.tracker_client


def get_jira_client(request: Request) -> JiraClient:
    """Dependency for the Jira-only endpoints (/tools, /tools/{name}/call --
    tools.py's field set is genuinely Jira-shaped, ADLC-57's non-goals). 501,
    not a silent pass-through of the wrong client type, when the configured
    backend isn't Jira.
    """
    client = request.app.state.tracker_client
    if not isinstance(client, JiraClient):
        raise HTTPException(
            status_code=501,
            detail=(
                "This endpoint is Jira-only; the configured SYMPHONY_WORKFLOW_TRACKER "
                "backend is not Jira."
            ),
        )
    return client


def get_run_store(request: Request) -> RunStore:
    return request.app.state.runs


def get_workspace_manager(request: Request) -> WorkspaceManager:
    return request.app.state.workspaces


HEALTH_TRACKER_TIMEOUT_SECONDS = 5.0


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    tracker_reachable: bool
    poller_running: bool


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = {}


class CreateRunRequest(BaseModel):
    issue_key: str


class PlanRunRequest(BaseModel):
    """Optional body for `POST /runs/{id}/plan` (ADLC-55). `max_turns` unset
    (the default) preserves today's behavior — `execute_plan` falls through
    to `agent_runner.DEFAULT_PLAN_MAX_TURNS`. Every existing caller that
    posts no body (or an empty `{}`) is unaffected.
    """

    max_turns: int | None = None


@app.get("/health")
def health(request: Request, client: TrackerClient = Depends(get_tracker)) -> HealthResponse:
    """Liveness + tracker connectivity + poller status. Always 200 while the server is
    up; `tracker_reachable` reflects a real connectivity call to whichever backend is
    configured (Jira `GET /rest/api/2/myself` or Linear's `viewer` query). A 401/403
    (bad credentials) also reports False. No user details are returned.
    """
    try:
        client.get_myself(timeout=HEALTH_TRACKER_TIMEOUT_SECONDS)
        tracker_reachable = True
    except (TrackerError, httpx.TransportError):
        tracker_reachable = False
    poller: Poller = request.app.state.poller
    return HealthResponse(tracker_reachable=tracker_reachable, poller_running=poller.is_running)


@app.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    """Every registered tool's JSON schema — the same payload as `symphony-workflow
    list-tools`. Jira-only (ADLC-57's non-goals) -- the schemas themselves don't change
    based on the configured backend, but calling one via /tools/{name}/call does 501
    when the backend isn't Jira.
    """
    return [tool.json_schema() for tool in TOOLS.values()]


@app.post("/tools/{name}/call")
def call_tool_endpoint(
    name: str, body: ToolCallRequest, client: JiraClient = Depends(get_jira_client)
) -> Any:
    try:
        return call_tool(client, name, body.arguments)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    except JiraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@app.get("/issues/{key}")
def get_issue(key: str, client: TrackerClient = Depends(get_tracker)) -> dict[str, Any]:
    try:
        return client.get_issue(key).model_dump()
    except TrackerError as exc:
        raise HTTPException(status_code=getattr(exc, "status_code", 502), detail=str(exc)) from exc


@app.post("/runs", status_code=201)
def create_run(body: CreateRunRequest, runs: RunStore = Depends(get_run_store)) -> Run:
    return runs.create(body.issue_key)


@app.get("/runs/{run_id}")
def get_run(run_id: str, runs: RunStore = Depends(get_run_store)) -> Run:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    return run


@app.get("/issues/{key}/run")
def get_run_for_issue(key: str, runs: RunStore = Depends(get_run_store)) -> Run:
    """Find the Run for an issue key — needed to discover the run_id a poller-
    dispatched Run got (auto-Plan creates the Run internally; nothing returns
    its id to the caller until this endpoint)."""
    run = runs.get_by_issue_key(key)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run found for issue {key!r}")
    return run


@app.post("/runs/{run_id}/plan")
async def plan_run(
    run_id: str,
    body: PlanRunRequest | None = None,
    runs: RunStore = Depends(get_run_store),
    client: TrackerClient = Depends(get_tracker),
    workspaces: WorkspaceManager = Depends(get_workspace_manager),
) -> Run:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    max_turns = body.max_turns if body is not None else None
    try:
        return await execute_plan(
            run, runs=runs, client=client, workspaces=workspaces, max_turns=max_turns
        )
    except RunTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except TrackerError as exc:
        raise HTTPException(status_code=getattr(exc, "status_code", 502), detail=str(exc)) from exc
    except AgentRunError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/runs/{run_id}/implement")
async def implement_run(
    run_id: str,
    runs: RunStore = Depends(get_run_store),
    client: TrackerClient = Depends(get_tracker),
    workspaces: WorkspaceManager = Depends(get_workspace_manager),
) -> Run:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    try:
        return await execute_implement(run, runs=runs, client=client, workspaces=workspaces)
    except RunTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AgentRunError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


# --- Symphony §13.7.2 JSON REST API (GET /api/v1/*) -----------------------
#
# An observability/control surface distinct from /runs/*: /runs/* is the
# thing that DOES Plan/Implement, /api/v1/* is a read-only view over
# RunStore matching Symphony's suggested shape (adapted to our 5-state Run
# model — no retry queue yet, so `retrying` stays empty but is kept in the
# shape for forward compatibility per §13.7.2's "MAY add fields" allowance).
# POST /api/v1/refresh is intentionally not implemented — no value without
# the poller's tick being externally triggerable (see docs/spec.md).

RUNNING_STATES = (RunState.PLANNING, RunState.IMPLEMENTING)


class RunCounts(BaseModel):
    running: int
    retrying: int
    by_state: dict[str, int]


class StateResponse(BaseModel):
    generated_at: datetime
    counts: RunCounts
    running: list[Run]
    retrying: list[Run] = []
    poller_running: bool


class WorkspaceInfo(BaseModel):
    path: str | None = None


class IssueDetailResponse(BaseModel):
    issue_identifier: str
    run_id: str | None = None
    status: str | None = None
    workspace: WorkspaceInfo
    session_id: str | None = None
    plan: str | None = None
    result: str | None = None
    last_error: str | None = None
    created_at: datetime | None = None


@app.get("/api/v1/state")
def api_state(request: Request, runs: RunStore = Depends(get_run_store)) -> StateResponse:
    """Symphony §13.7.2's `GET /api/v1/state` — a summary view of current runtime
    state, distinct from `GET /health` (liveness) and `GET /runs/{id}` (one run).
    """
    all_runs = runs.all()
    running = [r for r in all_runs if r.state in RUNNING_STATES]
    by_state: dict[str, int] = {}
    for run in all_runs:
        by_state[run.state.value] = by_state.get(run.state.value, 0) + 1
    poller: Poller = request.app.state.poller
    return StateResponse(
        generated_at=datetime.now(UTC),
        counts=RunCounts(running=len(running), retrying=0, by_state=by_state),
        running=running,
        retrying=[],
        poller_running=poller.is_running,
    )


@app.get("/api/v1/issues/{key}")
def api_issue_detail(key: str, runs: RunStore = Depends(get_run_store)) -> IssueDetailResponse:
    """Symphony §13.7.2's `GET /api/v1/<issue_identifier>` — per-ticket runtime
    detail for debugging. Distinct from the plain `GET /issues/{key}/run`: this
    nests fields to match Symphony's suggested shape (`workspace.path`, ...)
    rather than returning the flat `Run` model. 404 if no Run exists for the
    key yet, matching Symphony's suggested "issue unknown to in-memory state"
    behavior — not a Jira-side 404 (this never calls Jira).
    """
    run = runs.get_by_issue_key(key)
    if run is None:
        raise HTTPException(status_code=404, detail=f"No run found for issue {key!r}")
    return IssueDetailResponse(
        issue_identifier=key,
        run_id=run.id,
        status=run.state.value,
        workspace=WorkspaceInfo(path=run.workspace_path),
        session_id=run.session_id,
        plan=run.plan,
        result=run.result,
        last_error=run.error,
        created_at=run.created_at,
    )
