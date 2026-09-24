"""FastAPI server — Symphony §13.7's OPTIONAL HTTP Server Extension, adapted.

`GET /tools` / `POST /tools/{name}/call` expose the same tool registry the
CLI's `list-tools` / `call` commands use, over HTTP, so a remote agent or
orchestrator can drive Jira the same way a human at a terminal can.
`POST /runs` / `GET /runs/{id}` are the start of the Plan/Implement run
interface — Phase 1 only creates and reads a Run; the `/plan` and
`/implement` transition endpoints are Phase 2 (see docs/plan.md).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ValidationError

from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import ConfigError, JiraConfig
from jira_agent_cli.runs import Run, RunStore
from jira_agent_cli.tools import TOOLS, call_tool


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        config = JiraConfig.from_env()
    except ConfigError as exc:
        raise RuntimeError(str(exc)) from exc
    app.state.jira_client = JiraClient(config)
    app.state.runs = RunStore()
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
