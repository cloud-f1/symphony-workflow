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
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError

from jira_agent_cli.agent_runner import AgentRunError
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import ConfigError, JiraConfig
from jira_agent_cli.orchestrator import REPO_ROOT, WORKSPACE_ROOT, execute_implement, execute_plan
from jira_agent_cli.poller import Poller
from jira_agent_cli.runs import Run, RunStore, RunTransitionError
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
    app.state.workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=REPO_ROOT)

    poller = Poller(
        client=app.state.jira_client,
        runs=app.state.runs,
        workspaces=app.state.workspaces,
        interval_seconds=float(os.environ.get("JIRA_AGENT_CLI_POLL_INTERVAL_SECONDS", "60")),
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
) -> Run:
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    try:
        return await execute_implement(run, runs=runs, client=client)
    except RunTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except AgentRunError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
