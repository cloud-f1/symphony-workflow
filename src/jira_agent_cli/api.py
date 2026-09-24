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
Disable via `JIRA_AGENT_CLI_POLL_ENABLED=false` (tests do this).
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

from jira_agent_cli.agent_runner import AgentRunError
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import ConfigError, JiraConfig, triage_enabled, triage_interval_seconds
from jira_agent_cli.orchestrator import (
    TARGET_REPO,
    WORKSPACE_ROOT,
    execute_implement,
    execute_plan,
    sweep_terminal_workspaces,
)
from jira_agent_cli.poller import Poller
from jira_agent_cli.runs import Run, RunState, RunStore, RunTransitionError
from jira_agent_cli.tools import TOOLS, call_tool
from jira_agent_cli.workspace import WorkspaceError, WorkspaceManager


def _poll_enabled() -> bool:
    # Read at lifespan-startup time, not module-import time, so tests can
    # monkeypatch/setenv this per-test before their TestClient starts.
    return os.environ.get("JIRA_AGENT_CLI_POLL_ENABLED", "true").lower() != "false"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        config = JiraConfig.from_env()
    except ConfigError as exc:
        raise RuntimeError(str(exc)) from exc
    app.state.jira_client = JiraClient(config)
    app.state.runs = RunStore()
    app.state.workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=TARGET_REPO)

    # Symphony §8.6 startup sweep — opt-in (JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED,
    # default off), no-op and no Jira calls otherwise.
    sweep_terminal_workspaces(client=app.state.jira_client, workspaces=app.state.workspaces)

    poller = Poller(
        client=app.state.jira_client,
        runs=app.state.runs,
        workspaces=app.state.workspaces,
        interval_seconds=float(os.environ.get("JIRA_AGENT_CLI_POLL_INTERVAL_SECONDS", "60")),
        # ADLC-29 — default false via config.triage_enabled(), so this is a
        # no-op for anyone who hasn't opted in (docs/plans/0002-richer-
        # polling-triage/intent.md's definition of done).
        triage_enabled=triage_enabled(),
        triage_interval_seconds=triage_interval_seconds(),
    )
    app.state.poller = poller
    if _poll_enabled():
        poller.start()

    try:
        yield
    finally:
        await poller.stop()
        app.state.jira_client.close()


app = FastAPI(
    title="jira-agent-cli",
    description="Jira Cloud tool-call server — see docs/spec.md",
    lifespan=lifespan,
)


def get_jira_client(request: Request) -> JiraClient:
    return request.app.state.jira_client


def get_run_store(request: Request) -> RunStore:
    return request.app.state.runs


def get_workspace_manager(request: Request) -> WorkspaceManager:
    return request.app.state.workspaces


HEALTH_JIRA_TIMEOUT_SECONDS = 5.0


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    jira_reachable: bool
    poller_running: bool


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = {}


class CreateRunRequest(BaseModel):
    issue_key: str


@app.get("/health")
def health(request: Request, client: JiraClient = Depends(get_jira_client)) -> HealthResponse:
    """Liveness + Jira connectivity + poller status. Always 200 while the server is
    up; `jira_reachable` reflects a real `GET /rest/api/2/myself` call. A 401/403
    (bad credentials) also reports False. No user details are returned.
    """
    try:
        client.get_myself(timeout=HEALTH_JIRA_TIMEOUT_SECONDS)
        jira_reachable = True
    except (JiraError, httpx.TransportError):
        jira_reachable = False
    poller: Poller = request.app.state.poller
    return HealthResponse(jira_reachable=jira_reachable, poller_running=poller.is_running)


@app.get("/tools")
def list_tools() -> list[dict[str, Any]]:
    """Every registered tool's JSON schema — the same payload as `jira-agent list-tools`."""
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
def get_issue(key: str, client: JiraClient = Depends(get_jira_client)) -> dict[str, Any]:
    try:
        return client.get_issue(key).model_dump()
    except JiraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


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
    runs: RunStore = Depends(get_run_store),
    client: JiraClient = Depends(get_jira_client),
    workspaces: WorkspaceManager = Depends(get_workspace_manager),
) -> Run:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    try:
        return await execute_plan(run, runs=runs, client=client, workspaces=workspaces)
    except RunTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except WorkspaceError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except JiraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except AgentRunError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@app.post("/runs/{run_id}/implement")
async def implement_run(
    run_id: str,
    runs: RunStore = Depends(get_run_store),
    client: JiraClient = Depends(get_jira_client),
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
