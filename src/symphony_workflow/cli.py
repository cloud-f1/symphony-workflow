"""Command-line entrypoint for symphony-workflow.

Three ways to drive it:
  - Friendly subcommands (`get`, `search`, `comment`, `transition`) for a human at a terminal.
    `get`/`comment`/`transition` work against either tracker backend
    (`SYMPHONY_WORKFLOW_TRACKER=jira|linear`, see tracker.py). `search` (raw JQL) is
    Jira-only — Linear has no JQL equivalent (ADLC-57).
  - `list-tools` / `call` for an AI agent: `list-tools` prints every tool's JSON schema,
    `call <name> '<json args>'` runs one by name. This is the integration surface meant for
    another orchestrator (Claude Code, Codex, a cron job) to shell out to. Jira-only —
    `tools.py`'s field set (Epic/parent linking, sprint/story-points) is Jira-shaped
    (ADLC-57's non-goals).
  - `plan` / `implement` for driving the Plan/Implement loop without a running server:
    calls orchestrator.py in-process, the same shared logic api.py/poller.py use, just
    from a terminal instead of over HTTP. See ADLC-45. Tracker-agnostic (ADLC-57).
  - `decompose` for breaking a Jira Epic into Stories/Tasks for human review: calls
    decompose.py in-process, reusing the Plan phase's agent wiring with a different
    structured-output schema. Read-only, no Jira writes — see ADLC-46. Jira-only.
  - `sync-decomposition` for turning an already-reviewed decomposition.json into real
    Jira Story/Subtask issues: calls sync_decomposition.py in-process. Does not re-run
    the decompose agent — see ADLC-47. Jira-only.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from symphony_workflow.agent_runner import AgentRunError
from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.config import ConfigError
from symphony_workflow.decompose import DecomposeError, execute_decompose
from symphony_workflow.orchestrator import (
    RUNS_DOCS_ROOT,
    TARGET_REPO,
    WORKSPACE_ROOT,
    execute_implement,
    execute_plan,
)
from symphony_workflow.runs import Run, RunStore, RunTransitionError
from symphony_workflow.sync_decomposition import SyncDecompositionError, execute_sync_decomposition
from symphony_workflow.tools import TOOLS, call_tool
from symphony_workflow.tracker import ConfigError as TrackerConfigError
from symphony_workflow.tracker import TrackerClient, TrackerError, get_tracker_client, require_jira
from symphony_workflow.workspace import WorkspaceError, WorkspaceManager

app = typer.Typer(
    help=(
        "Python CLI for Jira Cloud and Linear — plain subcommands plus a schema-described "
        "tool-call interface (`list-tools` / `call`) for AI agent orchestration."
    ),
    no_args_is_help=True,
)


def _client() -> TrackerClient:
    """Backs the tracker-agnostic commands (get/comment/transition/plan/implement) --
    selects Jira or Linear per SYMPHONY_WORKFLOW_TRACKER. See _jira_client() for the
    Jira-only commands (search/call/decompose/sync-decomposition).
    """
    try:
        return get_tracker_client()
    except (ConfigError, TrackerConfigError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=1) from exc


def _jira_client() -> JiraClient:
    """Backs the commands that are genuinely Jira-only regardless of
    SYMPHONY_WORKFLOW_TRACKER -- raw-JQL search, the AI-agent tool-call surface,
    decompose/sync-decomposition. See tracker.py's TrackerClient docstring / ADLC-57's
    non-goals for why these don't have a Linear equivalent yet.

    Goes through get_tracker_client() + require_jira() (ADLC-106), not a
    direct JiraClient(JiraConfig.from_env()) construction -- so this now
    honors SYMPHONY_WORKFLOW_TRACKER too. A real, deliberate behavior fix:
    previously SYMPHONY_WORKFLOW_TRACKER=linear was silently ignored by
    these commands (they'd still try Jira, using whatever JIRA_* env vars
    happened to be set); now they fail with a clear "Jira-only" message
    instead. No change for the common case (tracker unset/jira).
    """
    try:
        return require_jira(get_tracker_client(), context="This command")
    except (ConfigError, TrackerConfigError) as exc:
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
    """Invoke one registered tool with JSON-encoded arguments. Jira-only -- see
    tools.py's docstring / ADLC-57's non-goals."""
    try:
        raw_args = json.loads(args_json)
    except json.JSONDecodeError as exc:
        typer.echo(f"Invalid JSON arguments: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    with _jira_client() as client:
        try:
            result = call_tool(client, tool_name, raw_args)
        except (KeyError, JiraError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, default=str))


@app.command("get")
def get_issue(key: str) -> None:
    """Fetch a single issue. Works against either tracker backend."""
    with _client() as client:
        try:
            issue = client.get_issue(key)
        except TrackerError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(issue.model_dump_json(indent=2))


@app.command("search")
def search_issues(jql: str, max_results: int = 50) -> None:
    """Search issues with JQL. Jira-only -- Linear has no JQL equivalent (ADLC-57)."""
    with _jira_client() as client:
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
    """Add a comment to an issue. Works against either tracker backend."""
    with _client() as client:
        try:
            client.add_comment(key, body)
        except TrackerError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(f"Comment added to {key}")


@app.command("transition")
def transition_issue(key: str, transition_name: str) -> None:
    """Move an issue through a named workflow transition/state. Works against
    either tracker backend."""
    with _client() as client:
        try:
            client.transition_issue(key, transition_name)
        except TrackerError as exc:
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
            f"No saved run for {issue_key} at {path} -- "
            f"run `symphony-workflow plan {issue_key}` first.",
            err=True,
        )
        raise typer.Exit(code=1)
    return Run.model_validate_json(path.read_text(encoding="utf-8"))


@app.command("plan")
def plan(
    key: str,
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help=(
            "Override the Plan phase's agent turn budget "
            "(default: agent_runner.DEFAULT_PLAN_MAX_TURNS). Raise this for a "
            "large target repo that hits error_max_turns at the default (ADLC-55)."
        ),
    ),
) -> None:
    """Run the Plan phase for an issue in-process -- no server required."""
    with _client() as client:
        runs = RunStore()
        run = runs.create(key)
        workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=TARGET_REPO)
        try:
            updated = asyncio.run(
                execute_plan(
                    run, runs=runs, client=client, workspaces=workspaces, max_turns=max_turns
                )
            )
        except (TrackerError, WorkspaceError, AgentRunError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    _save_run(updated)
    typer.echo(f"Plan ready for {key} — see docs/runs/{key}/plan.md")
    typer.echo(
        f"Run state saved to {_run_sidecar_path(key)} for `symphony-workflow implement {key}`"
    )


@app.command("implement")
def implement(key: str) -> None:
    """Run the Implement phase for an issue whose Plan is already approved
    (state plan_ready, saved by a prior `symphony-workflow plan` run). Works
    against either tracker backend."""
    run = _load_run(key)
    with _client() as client:
        runs = RunStore()
        runs.load(run)
        workspaces = WorkspaceManager(WORKSPACE_ROOT, source_repo=TARGET_REPO)
        try:
            updated = asyncio.run(
                execute_implement(run, runs=runs, client=client, workspaces=workspaces)
            )
        except (TrackerError, RunTransitionError, AgentRunError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    _save_run(updated)
    typer.echo(f"Implement finished for {key} — state: {updated.state.value}")


@app.command("decompose")
def decompose(key: str) -> None:
    """Break a Jira Epic into Stories/Tasks for human review -- read-only, no
    Jira writes. Renders docs/runs/<key>/decomposition.md; turning a reviewed
    decomposition into real Jira Stories/Subtasks is ADLC-47, not this command.
    Jira-only (ADLC-57's non-goals).
    """
    with _jira_client() as client:
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


@app.command("sync-decomposition")
def sync_decomposition(
    key: str,
    project: str | None = typer.Option(
        None,
        "--project",
        help=(
            "Target project key for the created issues. Defaults to the epic key's own "
            "project (the prefix before the dash, e.g. 'ADLC' for 'ADLC-27')."
        ),
    ),
    status: str | None = typer.Option(
        None,
        "--status",
        help=(
            "Transition each Story/Subtask to this status immediately after creating it, "
            "instead of leaving the default 'To Do' for the poller to auto-Plan (ADLC-121). "
            "Use this when retroactively documenting already-shipped work as real Jira "
            "issues (this repo's own use case, e.g. --status Done) -- leave unset for a "
            "real forward-looking decomposition you actually want the poller to pick up."
        ),
    ),
) -> None:
    """Materialize an already-reviewed docs/runs/<key>/decomposition.json as real
    Jira issues: one Story per decomposed story (parent=<key>) plus one Subtask per
    story's tasks (parent=<story-key>). Does NOT re-run `symphony-workflow decompose` — run
    that first and review decomposition.md. See ADLC-47. Jira-only (ADLC-57's
    non-goals).
    """
    with _jira_client() as client:
        try:
            result = execute_sync_decomposition(
                key,
                client=client,
                runs_docs_root=RUNS_DOCS_ROOT,
                project_key=project,
                initial_status=status,
            )
        except (JiraError, SyncDecompositionError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc

    typer.echo(
        f"Synced decomposition for {result.epic_key} into project {result.project_key}: "
        f"{len(result.stories)} stories created"
    )
    for story in result.stories:
        typer.echo(f"  Story {story.key}: {story.title}")
        typer.echo(f"    {story.url}")
        for task in story.tasks:
            typer.echo(f"    Subtask {task.key}: {task.title}")
            typer.echo(f"      {task.url}")


if __name__ == "__main__":
    app()
