"""FastAPI server — Symphony §13.7's OPTIONAL HTTP Server Extension, adapted.

`GET /tools` / `POST /tools/{name}/call` expose the same tool registry the
CLI's `list-tools` / `call` commands use, over HTTP, so a remote agent or
orchestrator can drive Jira the same way a human at a terminal can.
`POST /runs/{id}/plan` and `POST /runs/{id}/implement` are the two-phase
interface from docs/spec.md — `/implement` is hard-gated on `state ==
plan_ready`, enforced in runs.py's RunStore, not just here.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError

from jira_agent_cli.agent_runner import AgentRunError, run_implement, run_plan
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import ConfigError, JiraConfig
from jira_agent_cli.prompts import render_implement_prompt, render_plan_prompt
from jira_agent_cli.runs import Run, RunState, RunStore, RunTransitionError
from jira_agent_cli.tools import TOOLS, call_tool
from jira_agent_cli.workspace import WorkspaceError, WorkspaceManager

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
RUNS_DOCS_ROOT = Path(os.environ.get("JIRA_AGENT_CLI_RUNS_DOCS_ROOT", REPO_ROOT / "docs" / "runs"))
WORKSPACE_ROOT = Path(os.environ.get("JIRA_AGENT_CLI_WORKSPACE_ROOT", REPO_ROOT / ".worktrees"))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        config = JiraConfig.from_env()
    except ConfigError as exc:
        raise RuntimeError(str(exc)) from exc
    app.state.jira_client = JiraClient(config)
    app.state.runs = RunStore()
    app.state.workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=REPO_ROOT)
    try:
        yield
    finally:
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


class ToolCallRequest(BaseModel):
    arguments: dict[str, Any] = {}


class CreateRunRequest(BaseModel):
    issue_key: str


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


@app.post("/runs/{run_id}/plan")
async def plan_run(
    run_id: str,
    runs: RunStore = Depends(get_run_store),
    client: JiraClient = Depends(get_jira_client),
    workspaces: WorkspaceManager = Depends(get_workspace_manager),
) -> Run:
    """Read-only: renders the plan prompt, runs one bounded permission_mode="plan"
    session, posts the result as a Jira comment + docs/runs/<key>/plan.md, and
    records the SDK session_id so /implement can resume the same thread. Never
    writes to the target repo — see docs/spec.md.
    """
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")

    try:
        issue = client.get_issue(run.issue_key)
    except JiraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    try:
        runs.mark_planning(run_id)
    except RunTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    try:
        workspace_path = workspaces.ensure(run.issue_key)
    except WorkspaceError as exc:
        runs.mark_failed(run_id, error=str(exc))
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    prompt = render_plan_prompt(issue)
    try:
        result = await run_plan(prompt, cwd=str(workspace_path))
    except AgentRunError as exc:
        runs.mark_failed(run_id, error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not result.session_id:
        runs.mark_failed(
            run_id, error="Agent run produced no session_id — cannot resume for /implement"
        )
        raise HTTPException(status_code=502, detail="Agent run produced no session_id")

    plan_dir = RUNS_DOCS_ROOT / run.issue_key
    plan_dir.mkdir(parents=True, exist_ok=True)
    (plan_dir / "plan.md").write_text(result.text, encoding="utf-8")

    try:
        client.add_comment(run.issue_key, f"[jira-agent-cli plan]\n\n{result.text}")
    except JiraError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    return runs.set_plan_ready(
        run_id, plan=result.text, session_id=result.session_id, workspace_path=str(workspace_path)
    )


@app.post("/runs/{run_id}/implement")
async def implement_run(run_id: str, runs: RunStore = Depends(get_run_store)) -> Run:
    """Hard-gated on state == plan_ready (enforced in RunStore, not just here).
    Resumes the Plan phase's session_id, full edit permissions, scoped to the
    same workspace. Never opens a PR or pushes — a human reviews the diff next.
    """
    run = runs.get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found")
    if run.state != RunState.PLAN_READY:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Run {run_id} is {run.state.value!r}, expected 'plan_ready' — "
                "plan not approved yet"
            ),
        )

    try:
        runs.mark_implementing(run_id)
    except RunTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    assert run.session_id is not None  # guaranteed by set_plan_ready
    assert run.workspace_path is not None

    prompt = render_implement_prompt(run.issue_key)
    try:
        result = await run_implement(
            prompt,
            cwd=run.workspace_path,
            resume_session_id=run.session_id,
        )
    except AgentRunError as exc:
        runs.mark_failed(run_id, error=str(exc))
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return runs.mark_completed(run_id, result=result.text)
