"""Command-line entrypoint for jira-agent-cli.

Two ways to drive it:
  - Friendly subcommands (`get`, `search`, `comment`, `transition`) for a human at a terminal.
  - `list-tools` / `call` for an AI agent: `list-tools` prints every tool's JSON schema,
    `call <name> '<json args>'` runs one by name. This is the integration surface meant for
    another orchestrator (Claude Code, Codex, a cron job) to shell out to.
"""

from __future__ import annotations

import json

import typer

from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import ConfigError, JiraConfig
from jira_agent_cli.tools import TOOLS, call_tool

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


if __name__ == "__main__":
    app()
