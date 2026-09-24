"""Command-line entrypoint for jira-agent-cli.

Three ways to drive it:
  - Friendly subcommands (`get`, `search`, `comment`, `transition`) for a human at a terminal.
  - `list-tools` / `call` for an AI agent: `list-tools` prints every tool's JSON schema,
    `call <name> '<json args>'` runs one by name. This is the integration surface meant for
    another orchestrator (Claude Code, Codex, a cron job) to shell out to.
  - `plan` / `implement` for driving the Plan/Implement loop without a running server:
    calls orchestrator.py in-process, the same shared logic api.py/poller.py use, just
    from a terminal instead of over HTTP. See ADLC-45.
  - `decompose` for breaking a Jira Epic into Stories/Tasks for human review: calls
    decompose.py in-process, reusing the Plan phase's agent wiring with a different
    structured-output schema. Read-only, no Jira writes — see ADLC-46.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from jira_agent_cli.agent_runner import AgentRunError
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import ConfigError, JiraConfig
from jira_agent_cli.decompose import DecomposeError, execute_decompose
from jira_agent_cli.orchestrator import (
    RUNS_DOCS_ROOT,
    TARGET_REPO,
    WORKSPACE_ROOT,
    execute_implement,
    execute_plan,
)
from jira_agent_cli.runs import Run, RunStore, RunTransitionError
from jira_agent_cli.tools import TOOLS, call_tool
from jira_agent_cli.workspace import WorkspaceError, WorkspaceManager

app = typer.Typer(
    help=(
        "Python CLI for Jira Cloud — plain subcommands plus a schema-described "
        "tool-call interface (`list-tools` / `call`) for AI agent orchestration."
    ),
    no_args_is_help=True,
)


def _client() -> JiraClient:
    try:
        return JiraClient(JiraConfig.from_env())
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


@app.command("list-tools")
def list_tools() -> None:
    """Print every available tool as a JSON schema an agent can read."""
    schemas = [tool.json_schema() for tool in TOOLS.values()]
    typer.echo(json.dumps(schemas, indent=2))


@app.command("call")
def call(
    tool_name: str = typer.Argument(..., help="Tool name, e.g. get_issue"),
    args_json: str = typer.Argument("{}", help="Tool arguments as a JSON object"),
) -> None:
    """Invoke one registered tool with JSON-encoded arguments."""
    try:
        raw_args = json.loads(args_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON arguments: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    with _client() as client:
        try:
            result = call_tool(client, tool_name, raw_args)
        except (KeyError, JiraError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command("get")
def get_issue(key: str) -> None:
    """Fetch a single issue."""
    with _client() as client:
        try:
            issue = client.get_issue(key)
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(issue.model_dump_json(indent=2))


@app.command("search")
def search_issues(jql: str, max_results: int = 50) -> None:
    """Search issues with JQL."""
    with _client() as client:
        try:
            result = client.search_issues(jql, max_results)
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    for issue in result.issues:
        typer.echo(f"{issue.key}\t{issue.status}\t{issue.summary}")
    typer.echo(f"\n{result.total} total")


@app.command("comment")
def add_comment(key: str, body: str) -> None:
    """Add a comment to an issue."""
    with _client() as client:
        try:
            client.add_comment(key, body)
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"Comment added to {key}")


@app.command("transition")
def transition_issue(key: str, transition_name: str) -> None:
    """Move an issue through a named workflow transition."""
    with _client() as client:
        try:
            client.transition_issue(key, transition_name)
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"{key} -> {transition_name}")


def _run_sidecar_path(issue_key: str) -> Path:
    return RUNS_DOCS_ROOT / issue_key / "run.json"


def _save_run(run: Run) -> None:
    # RunStore is in-memory and process-scoped (docs/spec.md) -- CLI mode is
    # a separate short-lived process per invocation, so `plan` and a later
    # `implement` can't share one. This sidecar, next to the plan.md this
    # same run already writes, is what lets `implement` resume it.
    path = _run_sidecar_path(run.issue_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(run.model_dump_json(indent=2), encoding="utf-8")


def _load_run(issue_key: str) -> Run:
    path = _run_sidecar_path(issue_key)
    if not path.exists():
        typer.echo(
            f"No saved run for {issue_key} at {path} -- run `jira-agent plan {issue_key}` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    return Run.model_validate_json(path.read_text(encoding="utf-8"))


@app.command("plan")
def plan(key: str) -> None:
    """Run the Plan phase for an issue in-process -- no server required."""
    with _client() as client:
        runs = RunStore()
        run = runs.create(key)
        workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=TARGET_REPO)
        try:
            updated = asyncio.run(
                execute_plan(run, runs=runs, client=client, workspaces=workspaces)
            )
        except (JiraError, WorkspaceError, AgentRunError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    _save_run(updated)
    typer.echo(f"Plan ready for {key} — see docs/runs/{key}/plan.md")
    typer.echo(f"Run state saved to {_run_sidecar_path(key)} for `jira-agent implement {key}`")


@app.command("implement")
def implement(key: str) -> None:
    """Run the Implement phase for an issue whose Plan is already approved
    (state plan_ready, saved by a prior `jira-agent plan` run)."""
    run = _load_run(key)
    with _client() as client:
        runs = RunStore()
        runs.load(run)
        workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=TARGET_REPO)
        try:
            updated = asyncio.run(
                execute_implement(run, runs=runs, client=client, workspaces=workspaces)
            )
        except (JiraError, RunTransitionError, AgentRunError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    _save_run(updated)
    typer.echo(f"Implement finished for {key} — state: {updated.state.value}")


@app.command("decompose")
def decompose(key: str) -> None:
    """Break a Jira Epic into Stories/Tasks for human review -- read-only, no
    Jira writes. Renders docs/runs/<key>/decomposition.md; turning a reviewed
    decomposition into real Jira Stories/Subtasks is ADLC-47, not this command.
    """
    with _client() as client:
        workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=TARGET_REPO)
        try:
            decomposition, md_path = asyncio.run(
                execute_decompose(
                    key,
                    client=client,
                    workspaces=workspaces,
                    runs_docs_root=RUNS_DOCS_ROOT,
                )
            )
        except (JiraError, WorkspaceError, AgentRunError, DecomposeError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(
        f"Decomposition ready for {key}: {len(decomposition.stories)} stories — see {md_path}"
    )


if __name__ == "__main__":
    app()
