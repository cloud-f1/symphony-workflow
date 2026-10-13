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
  - `rollup` for closing out a finished Subtask's Story and Epic once all their children are
    Done (forward-only, idempotent, `--dry-run`). Jira-only (needs the `parent` field).
  - `audit` / `add-subtasks` for keeping an existing project's Epic -> Story -> Subtask hierarchy
    honest: `audit` lists Stories with no Subtasks, Stories under no Epic, Epics with no Stories,
    Done Stories with open Subtasks and (optionally) unassigned cards; `add-subtasks` adds
    Subtasks to existing Stories from a plan file, idempotently. See docs/JIRA_HIERARCHY.md.
    Jira-only.
  - `sync-decomposition` for turning an already-reviewed decomposition.json into real
    Jira Story/Subtask issues: calls sync_decomposition.py in-process. Does not re-run
    the decompose agent — see ADLC-47. Jira-only.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path

import typer

from symphony_workflow.agent_runner import AgentRunError
from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.config import ConfigError, JiraConfig, LinearConfig
from symphony_workflow.decompose import DecomposeError, assign_ids, execute_decompose
from symphony_workflow.drift import DiffReport, apply_update, diff_decomposition
from symphony_workflow.envsetup import EnvExistsError, check_env, init_env, verify_env
from symphony_workflow.epics_md import load_epics_md
from symphony_workflow.hierarchy import (
    AddSubtasksAborted,
    AddSubtasksResult,
    AuditResult,
    PlanError,
    add_subtasks,
    audit,
)
from symphony_workflow.jira_sink import JiraStatusBackend, sync_plan_to_jira
from symphony_workflow.linear_client import LinearClient, LinearError
from symphony_workflow.linear_sink import sync_plan_to_linear
from symphony_workflow.markdown_sink import write_markdown
from symphony_workflow.orchestrator import (
    RUNS_DOCS_ROOT,
    TARGET_REPO,
    WORKSPACE_ROOT,
    execute_implement,
    execute_plan,
)
from symphony_workflow.plan import Plan, assign_plan_ids, check_plan, from_decomposition
from symphony_workflow.project_profile import (
    CONFIG_PATH,
    PlanSyncConfig,
    Profile,
    Recommendation,
    load_config,
    profile_project,
    recommend,
    save_config,
)
from symphony_workflow.rollup import RollupAborted, RollupNotFound, RollupResult, rollup
from symphony_workflow.runs import Run, RunStore, RunTransitionError
from symphony_workflow.safepath import UnsafePathError
from symphony_workflow.status_sync import (
    LinearStatusBackend,
    sync_status_with,
)
from symphony_workflow.status_sync import load_state as load_sync_state
from symphony_workflow.status_sync import save_state as save_sync_state
from symphony_workflow.sync_decomposition import (
    SyncDecompositionError,
    execute_sync_decomposition,
    load_decomposition,
    load_state,
    save_decomposition,
)
from symphony_workflow.todo_source import (
    PROPOSAL_JSON,
    approve_proposal,
    fill_todos,
    load_proposal,
    propose,
    save_proposal,
    split_with_agent,
)
from symphony_workflow.tools import LOCAL_TOOLS, TOOLS, call_tool
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

    if tool_name in LOCAL_TOOLS:  # local-file tools need no tracker credentials
        try:
            result = call_tool(None, tool_name, raw_args)
        except (KeyError, ValueError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
        typer.echo(json.dumps(result, indent=2, default=str))
        return
    with _jira_client() as client:
        try:
            result = call_tool(client, tool_name, raw_args)
        except (KeyError, JiraError) as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=1) from exc
    typer.echo(json.dumps(result, indent=2, default=str))


def _print_rollup(result: RollupResult, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return
    verb = "would transition" if result.dry_run else "transitioned"
    for t in result.transitions:
        typer.echo(f"{verb} {t.key}: {t.from_status} -> {t.to}")
    if result.stopped_at:
        typer.echo(f"stopped at {result.stopped_at['key']}: {result.stopped_at['reason']}")
    elif not result.transitions:
        typer.echo(f"nothing to do for {result.key} (already rolled up)")


def _print_audit(result: AuditResult, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return
    typer.echo(
        f"{result.project}: {result.epics} epic(s), {result.stories} story-level issue(s), "
        f"{len(result.errors)} error(s), {len(result.findings) - len(result.errors)} warning(s)"
    )
    for f in result.findings:
        typer.echo(
            f"  {f.severity.upper():7} {f.key} [{f.status}] {f.rule}: {f.detail}"
            f"  | {f.summary[:60]}"
        )


@app.command("audit")
def audit_command(
    project: str = typer.Argument(..., help="Jira project key, e.g. ADLC"),
    open_only: bool = typer.Option(False, "--open", help="Skip Stories that are already Done"),
    assignee: str | None = typer.Option(
        None, "--assignee", help="Also require every Epic/Story to be assigned to this display name"
    ),
    assignee_me: bool = typer.Option(
        False, "--assignee-me", help="Same as --assignee, using the token's own user"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Check a project against the three-layer rules (docs/JIRA_HIERARCHY.md). Writes nothing.

    Errors: a Story with no Subtasks, a Story under no Epic, an Epic with no Stories, a Done Story
    with open Subtasks, and (with --assignee) a card not assigned to that person. Warnings: one
    Subtask, or more than 8. Jira-only.

    Exit codes: 0 no errors, 1 at least one error, 3 Jira API error.
    """
    with _jira_client() as client:
        try:
            who = client.get_myself().display_name if assignee_me else assignee
            result = audit(client, project, open_only=open_only, assignee=who)
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=3) from exc
    _print_audit(result, as_json=as_json)
    if not result.ok:
        raise typer.Exit(code=1)


def _print_add_subtasks(result: AddSubtasksResult, *, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(result.as_dict(), indent=2))
        return
    verb = "would create" if result.dry_run else "created"
    for s in result.stories:
        if s.action == "skipped":
            typer.echo(f"{s.key}: skipped ({s.reason})")
            continue
        tail = " (Story is Done: Subtasks rolled to Done)" if s.done else ""
        typer.echo(f"{s.key}: {verb} {len(s.created)} subtask(s){tail}")
        for line in s.created:
            typer.echo(f"    {line}")
        for warning in s.warnings:
            typer.echo(f"    warning: {warning}")


@app.command("add-subtasks")
def add_subtasks_command(
    plan_file: Path = typer.Argument(  # noqa: B008
        ..., help='JSON file: {"PROJ-12": ["Add X", "Tests: unit"], ...}'
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would be created; create nothing"
    ),
    assign_to_me: bool = typer.Option(
        False, "--assign-to-me", help="Assign the new Subtasks to the token's own user"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Add Subtasks under existing Stories from a plan file. Idempotent: a Story that already has
    Subtasks is skipped; a Story that is Done gets its new Subtasks transitioned to Done (roll-up).
    Plan problems are rejected before anything is created. Jira-only.

    Exit codes: 0 ok, 2 invalid plan file, 3 Jira API error (anything already created is printed).
    """
    try:
        plan = json.loads(plan_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        typer.echo(f"cannot read plan file: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    with _jira_client() as client:
        try:
            account = client.get_myself().account_id if assign_to_me else None
            result = add_subtasks(client, plan, dry_run=dry_run, assignee_account_id=account)
        except PlanError as exc:
            typer.echo(f"invalid plan: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        except AddSubtasksAborted as exc:
            _print_add_subtasks(exc.result, as_json=as_json)
            typer.echo(f"add-subtasks aborted: {exc.cause}", err=True)
            raise typer.Exit(code=3) from exc
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=3) from exc
    _print_add_subtasks(result, as_json=as_json)


@app.command("rollup")
def rollup_command(
    key: str = typer.Argument(..., help="The Subtask just completed (a Story or Epic also works)"),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="Report what would change; transition nothing"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Mark a finished Subtask Done, then roll up: all of the Story's Subtasks Done -> Story
    Done; all of the Epic's Stories Done -> Epic Done. Forward-only, stops at the first level
    that is not fully done, safe to re-run. Given a Story/Epic key it re-evaluates that issue
    and upward. Jira-only -- needs the `parent` field (Linear has no equivalent).

    Exit codes: 0 ok (including "nothing to do"), 2 key not found/invalid, 3 Jira API error
    (anything already transitioned is printed, never hidden).
    """
    with _jira_client() as client:
        try:
            result = rollup(client, key, dry_run=dry_run)
        except RollupNotFound as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
        except RollupAborted as exc:
            _print_rollup(exc.result, as_json=as_json)
            typer.echo(f"Roll-up aborted: {exc.cause}", err=True)
            raise typer.Exit(code=3) from exc
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=3) from exc
    _print_rollup(result, as_json=as_json)


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
    max_budget_usd: float | None = typer.Option(
        None,
        "--max-budget",
        help=(
            "Override the Plan phase's dollar budget ceiling "
            "(default: agent_runner.DEFAULT_MAX_BUDGET_USD, $2.00). Raising "
            "--max-turns alone doesn't touch this second, independent wall -- "
            "a target repo whose tickets need real investigation before a "
            "Plan is possible can hit error_max_budget_usd right after "
            "clearing error_max_turns (confirmed live 2026-09-30 against "
            "an external repo's ticket)."
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
                    run,
                    runs=runs,
                    client=client,
                    workspaces=workspaces,
                    max_turns=max_turns,
                    max_budget_usd=max_budget_usd,
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
def implement(
    key: str,
    max_turns: int | None = typer.Option(
        None,
        "--max-turns",
        help=(
            "Override the Implement phase's agent turn budget "
            "(default: agent_runner.DEFAULT_IMPLEMENT_MAX_TURNS, 20). Raise "
            "this for a ticket whose Implement is real investigation work, "
            "not a small code change (confirmed live 2026-10-01 against "
            "an external repo's ticket)."
        ),
    ),
    max_budget_usd: float | None = typer.Option(
        None,
        "--max-budget",
        help=(
            "Override the Implement phase's dollar budget ceiling "
            "(default: agent_runner.DEFAULT_MAX_BUDGET_USD, $2.00) -- same "
            "independent-wall caveat as `plan --max-budget`."
        ),
    ),
) -> None:
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
                execute_implement(
                    run,
                    runs=runs,
                    client=client,
                    workspaces=workspaces,
                    max_turns=max_turns,
                    max_budget_usd=max_budget_usd,
                )
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
    update: bool = typer.Option(
        False,
        "--update",
        help=(
            "Re-sync an Epic that already has children (ADLC-146): create what is missing, "
            "update summary/description that changed, never delete. Idempotent."
        ),
    ),
    dry_run: bool = typer.Option(
        False, "--dry-run", help="With --update: print the plan, write nothing."
    ),
    adopt: bool = typer.Option(
        False,
        "--adopt",
        help=(
            "With --update: match cards created before ids existed by exact summary. "
            "Prints the proposal and asks before writing unless --yes."
        ),
    ),
    yes: bool = typer.Option(False, "--yes", help="With --adopt: don't ask for confirmation."),
) -> None:
    """Materialize an already-reviewed docs/runs/<key>/decomposition.json as real
    Jira issues: one Story per decomposed story (parent=<key>) plus one Subtask per
    story's tasks (parent=<story-key>). Does NOT re-run `symphony-workflow decompose` — run
    that first and review decomposition.md. See ADLC-47. Jira-only (ADLC-57's
    non-goals). On an Epic that already has children, plain sync refuses; use --update.
    """
    if update:
        _sync_update(key, project, status, dry_run, adopt, yes)
        return
    if dry_run or adopt or yes:
        typer.echo("--dry-run/--adopt/--yes only apply together with --update.", err=True)
        raise typer.Exit(code=2)
    with _jira_client() as client:
        try:
            result = execute_sync_decomposition(
                key,
                client=client,
                runs_docs_root=RUNS_DOCS_ROOT,
                project_key=project,
                initial_status=status,
                refuse_if_children=True,
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


def _print_diff(report: DiffReport) -> None:
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(report.counts().items()))
    typer.echo(f"{report.epic_key}: {'DRIFT' if report.drift else 'in sync'} ({counts or 'empty'})")
    for item in report.items:
        if item.kind == "ok":
            continue
        where = item.key or "(not in Jira)"
        extra = f" -- {item.detail}" if item.detail else ""
        if item.expected or item.actual:
            extra += f" [{item.actual or ''} -> {item.expected or ''}]"
        typer.echo(f"  {item.kind:<20} {item.level:<8} {item.id or '-':<8} {where}{extra}")


@app.command("diff-decomposition")
def diff_decomposition_cmd(
    key: str,
    adopt: bool = typer.Option(
        False, "--adopt", help="Match cards without an id marker by exact summary."
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output."),
) -> None:
    """Read-only: compare docs/runs/<key>/decomposition.json with the live Epic ->
    Story -> Subtask tree and report what differs (missing, summary/description changed,
    orphans). Status, comments and links are not drift. Exit codes: 0 in sync, 1 drift,
    2 invalid decomposition, 3 Jira error. Jira-only. See ADLC-146.
    """
    try:
        decomposition = load_decomposition(key, RUNS_DOCS_ROOT)
    except SyncDecompositionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    with _jira_client() as client:
        try:
            report = diff_decomposition(
                key,
                decomposition,
                client=client,
                adopt=adopt,
                known=load_state(RUNS_DOCS_ROOT / key / "sync-state.json"),
            )
        except JiraError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=3) from exc
    if as_json:
        typer.echo(json.dumps(report.as_dict(), indent=2))
    else:
        _print_diff(report)
    raise typer.Exit(code=1 if report.drift else 0)


def _sync_update(
    key: str, project: str | None, status: str | None, dry_run: bool, adopt: bool, yes: bool
) -> None:
    try:
        decomposition = load_decomposition(key, RUNS_DOCS_ROOT)
    except SyncDecompositionError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    with _jira_client() as client:
        try:
            if adopt and not yes:
                preview = diff_decomposition(key, decomposition, client=client, adopt=True)
                typer.echo("Proposed adoption (cards matched by exact summary):")
                _print_diff(preview)
                if not dry_run and not typer.confirm("Write ids and apply?"):
                    raise typer.Exit(code=1)
            state_path = RUNS_DOCS_ROOT / key / "sync-state.json"
            ids_added = assign_ids(decomposition)
            if ids_added and not dry_run:
                save_decomposition(key, decomposition, RUNS_DOCS_ROOT)
            actions = apply_update(
                key,
                decomposition,
                client=client,
                project_key=project,
                initial_status=status,
                adopt=adopt,
                dry_run=dry_run,
                known=load_state(state_path),
                state_path=state_path,
            )
        except JiraError as exc:
            typer.echo(f"Jira error: {exc}", err=True)
            typer.echo(
                "Stopped at the first error; re-run --update to resume (it is idempotent).",
                err=True,
            )
            raise typer.Exit(code=3) from exc
    label = "Would" if dry_run else "Did"
    if not actions:
        typer.echo(f"{key}: nothing to do -- Jira already matches the decomposition.")
    for action in actions:
        what = (
            "create" if action.action == "create" else f"{action.action} {','.join(action.fields)}"
        )
        typer.echo(f"  {label}: {what} {action.level} {action.key or ''} {action.summary}".rstrip())


_ROOT_OPTION = typer.Option(Path("."), "--path", help="The project root")


def _print_recommendation(title: str, rec: Recommendation) -> None:
    c = rec.config
    typer.echo(
        f"{title}: tracker={c.tracker} plan_source={c.plan_source} todo_source={c.todo_source} "
        f"push={c.push} record={c.record} include_done={str(c.include_done).lower()}"
    )
    for note in rec.notes:
        typer.echo(f"  - {note.topic}: {note.evidence} -> {note.advice}")


def _print_profile(profile: Profile) -> None:
    p, t, c = profile.plan, profile.tracker, profile.content
    typer.echo(f"Project: {profile.root}")
    typer.echo(
        f"Plan files: {p.epics} epic file(s); index={p.has_index} progress={p.has_progress} "
        f"strategy-log={p.has_strategy_log}; plan folders={p.plan_folders}; "
        f"athena commands={p.athena_commands}"
    )
    keys = ", ".join(f"{k} ({v})" for k, v in t.key_prefixes.items()) or "none"
    typer.echo(
        f"Tracker: jira credentials={t.jira_credentials} linear credentials={t.linear_credentials} "
        f"SYMPHONY_WORKFLOW_TRACKER={t.tracker_env or 'unset'}; issue keys in history: {keys}"
    )
    typer.echo(
        f"Content: {c.phases} phase(s) ({c.open_phases} not complete), {c.epics} epic(s), "
        f"tasks per phase median={c.tasks_per_category_median}, "
        f"acceptance items median={c.acceptance_median}, "
        f"epics with a ready-made To-Do breakdown={c.ready_todo_share:.0%}"
    )


@app.command("profile")
def profile_command(
    path: Path = _ROOT_OPTION,
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
    write: bool = typer.Option(
        False, "--write", help=f"Save a recommendation to {CONFIG_PATH} (asks first)"
    ),
    use: int = typer.Option(2, "--use", help="Which recommendation to save with --write: 1 or 2"),
    yes: bool = typer.Option(False, "--yes", help="Do not ask for confirmation with --write"),
    force: bool = typer.Option(False, "--force", help="Overwrite an existing config with --write"),
) -> None:
    """Read how this project already works and recommend a plan-sync setup (ADLC-199). Read-only.

    Prints what it found (plan files, tracker credential key NAMES, issue keys in history, the shape
    of the
    plan), Recommendation 1 from usage and Recommendation 2 refined from content. Never prints a
    credential
    value. With --write the chosen recommendation is saved, after confirmation, for `sync-plan`.

    Exit codes: 0 ok, 1 --write refused (a config exists, or the answer was no), 2 bad arguments.
    """
    if use not in (1, 2):
        typer.echo("--use must be 1 or 2", err=True)
        raise typer.Exit(code=2)
    profile = profile_project(path)
    first, refined = recommend(profile)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "profile": profile.as_dict(),
                    "recommendation_1": first.as_dict(),
                    "recommendation_2": refined.as_dict(),
                },
                indent=2,
            )
        )
    else:
        _print_profile(profile)
        _print_recommendation("Recommendation 1 (from usage)", first)
        _print_recommendation("Recommendation 2 (refined from content)", refined)
    if not write:
        return
    chosen = first if use == 1 else refined
    target = path / CONFIG_PATH
    if target.exists() and not force:
        typer.echo(f"{target} already exists; pass --force to overwrite.", err=True)
        raise typer.Exit(code=1)
    if not yes and not typer.confirm(f"Save recommendation {use} to {target}?", default=False):
        typer.echo("Nothing written.", err=True)
        raise typer.Exit(code=1)
    typer.echo(f"Saved {save_config(path, chosen.config)}")


def _print_plan(plan: Plan) -> None:
    categories, tasks, todos = plan.counts()
    typer.echo(
        f"{plan.name or 'plan'}: {categories} categor(ies), {tasks} task(s), {todos} to-do(s)"
    )
    for category in plan.categories:
        typer.echo(f"  {category.id}  {category.title}  [{category.status}]")
        for task in category.tasks:
            typer.echo(f"    {task.id}  {task.title}  [{task.status}]  ({len(task.todos)} to-dos)")


@app.command("import-plan")
def import_plan_command(
    path: Path = _ROOT_OPTION,
    include_done: bool = typer.Option(False, "--include-done", help="Also import completed phases"),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Read the project's epics-as-markdown files into the neutral Category / Task / To-Do plan
    (ADLC-200).

    Read-only. Prints the plan and any pyramid guardrail warnings. Completed phases are skipped
    unless
    --include-done is given. Writes nothing.
    """
    plan = load_epics_md(path, include_done=include_done)
    findings = check_plan(plan)
    if as_json:
        typer.echo(
            json.dumps(
                {
                    "plan": plan.model_dump(),
                    "counts": list(plan.counts()),
                    "findings": [f.as_dict() for f in findings],
                },
                indent=2,
            )
        )
        return
    _print_plan(plan)
    for f in findings:
        typer.echo(f"  WARNING {f.where} {f.rule}: {f.detail}")


@app.command("sync-plan")
def sync_plan_command(
    path: Path = _ROOT_OPTION,
    source: str | None = typer.Option(
        None, "--from", help="epics-md or decomposition (default: the config)"
    ),
    epic: str | None = typer.Option(None, "--epic", help="Epic key, for --from decomposition"),
    to: str | None = typer.Option(None, "--to", help="markdown, linear or jira"),
    project: str | None = typer.Option(
        None,
        "--project",
        help="Linear: the project name (default: the plan's name). Jira: the project KEY.",
    ),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change, write nothing"),
    include_done: bool = typer.Option(False, "--include-done", help="Also sync completed phases"),
) -> None:
    """Sync a plan into Markdown files in the project's docs/ (ADLC-202). Idempotent; never deletes.

    Settings come from docs/context/plan-sync.json. With no config and no --from/--to nothing is
    touched:
    the recommendation is printed and the command stops, so run `profile --write` first (or pass
    both flags).
    Only the managed blocks of a file are rewritten; hand-written sections stay as they are.

    Exit codes: 0 ok, 1 not configured, 2 bad input or a sink that is not built yet.
    """
    try:
        config = load_config(path)
    except ValueError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    if config is None and not (source and to):
        first, refined = recommend(profile_project(path))
        typer.echo(f"{path / CONFIG_PATH} does not exist, so nothing was touched. Recommendation:")
        _print_recommendation("Recommendation 2 (refined from content)", refined)
        typer.echo(
            "Save it with `symphony-workflow profile --write`, or pass --from and --to.", err=True
        )
        raise typer.Exit(code=1)
    config = config or PlanSyncConfig()
    source = source or config.plan_source
    to = to or config.tracker
    if to not in ("markdown", "linear", "jira"):
        typer.echo(f"Unknown sink {to!r}: use markdown, linear or jira.", err=True)
        raise typer.Exit(code=2)
    if source == "epics-md":
        plan = load_epics_md(path, include_done=include_done or config.include_done)
    elif source == "decomposition":
        if not epic:
            typer.echo("--from decomposition needs --epic KEY", err=True)
            raise typer.Exit(code=2)
        try:
            plan = from_decomposition(load_decomposition(epic, path / "docs/runs"))
        except SyncDecompositionError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
    else:
        typer.echo(f"Unknown source {source!r}: use epics-md or decomposition", err=True)
        raise typer.Exit(code=2)
    try:
        proposal = load_proposal(path)
    except (UnsafePathError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    assign_plan_ids(plan)
    origins = fill_todos(plan, config.todo_source, proposal)
    for task_id, origin in origins.items():
        typer.echo(f"  To-Dos for {task_id}: from {origin}")
    if to == "linear":
        _sync_plan_to_linear(plan, project or plan.name or path.resolve().name, dry_run)
        return
    if to == "jira":
        _sync_plan_to_jira(plan, path, project, dry_run)
        return
    try:
        changes = write_markdown(plan, path, dry_run=dry_run)
    except UnsafePathError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    label = {
        "create": "Would create" if dry_run else "Created",
        "update": "Would update" if dry_run else "Updated",
    }
    for change in changes:
        typer.echo(f"  {label.get(change.action, 'Unchanged'):12} {change.path}")
    for f in check_plan(plan):
        typer.echo(f"  WARNING {f.where} {f.rule}: {f.detail}")
    if config.todo_source in ("hybrid", "ai-split") and any(
        f.rule == "no_todos" for f in check_plan(plan)
    ):
        typer.echo("  Some Tasks have no To-Dos: run `split-todos`, review, then `--approve`.")


def _sync_plan_to_jira(plan: Plan, path: Path, project: str | None, dry_run: bool) -> None:
    project_key = project or os.environ.get("SYMPHONY_WORKFLOW_JIRA_PROJECT", "")
    if not project_key:
        typer.echo("--to jira needs --project KEY (or SYMPHONY_WORKFLOW_JIRA_PROJECT).", err=True)
        raise typer.Exit(code=2)
    try:
        jira_config = JiraConfig.from_env()
        state = load_sync_state(path)
    except (ConfigError, UnsafePathError, ValueError) as exc:
        typer.echo(f"{exc} (Jira credentials: this project's .env, see `check-env`)", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Jira project: {project_key}{' (dry run, nothing written)' if dry_run else ''}")
    try:
        with JiraClient(jira_config) as client:
            changes, keys = sync_plan_to_jira(
                plan, client, project_key=project_key, keys=state.keys, dry_run=dry_run
            )
    except JiraError as exc:
        typer.echo(f"Jira error: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    label = {"create": "Would create" if dry_run else "Created",
             "update": "Would update" if dry_run else "Updated"}  # fmt: skip
    for change in changes:
        key = f" {change.key}" if change.key else ""
        action = label.get(change.action, "Unchanged")
        typer.echo(f"  {action:12} {change.kind:8} {change.title}{key}")
    if not dry_run:
        state.keys = keys
        save_sync_state(path, state)
    for f in check_plan(plan):
        typer.echo(f"  WARNING {f.where} {f.rule}: {f.detail}")


def _sync_plan_to_linear(plan: Plan, project: str, dry_run: bool) -> None:
    try:
        config = LinearConfig.from_env()
    except ConfigError as exc:
        typer.echo(f"{exc} (set them in this project's .env; see `check-env`)", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"Linear project: {project}{' (dry run, nothing written)' if dry_run else ''}")
    try:
        with LinearClient(config) as client:
            changes = sync_plan_to_linear(plan, client, project_name=project, dry_run=dry_run)
    except LinearError as exc:
        typer.echo(f"Linear error: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    label = {"create": "Would create" if dry_run else "Created",
             "update": "Would update" if dry_run else "Updated"}  # fmt: skip
    for change in changes:
        typer.echo(f"  {label.get(change.action, 'Unchanged'):12} {change.kind:9} {change.title}")
    for f in check_plan(plan):
        typer.echo(f"  WARNING {f.where} {f.rule}: {f.detail}")


@app.command("sync-status")
def sync_status_command(
    path: Path = _ROOT_OPTION,
    to: str | None = typer.Option(None, "--to", help="linear or jira"),
    direction: str = typer.Option("both", "--direction", help="both, push or pull"),
    project: str | None = typer.Option(None, "--project", help="Linear project name"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Show what would change, write nothing"),
    include_done: bool = typer.Option(False, "--include-done", help="Also cover completed phases"),
) -> None:
    """Two-way status sync between the plan's checkboxes and Linear (ADLC-204).

    Each To-Do is compared three ways (status at the last sync, in the plan, in Linear): a change on
    one side moves to the other (pull writes the checkboxes, push moves the card), a change on both
    sides is a conflict that is reported and left alone. Tasks follow the roll-up of their To-Dos,
    forward only. Nothing is deleted. Exit codes: 0 ok, 1 conflicts, 2 bad input, 3 Linear failed.
    """
    try:
        config = load_config(path) or PlanSyncConfig()
        base = load_sync_state(path)
        proposal = load_proposal(path)
    except (UnsafePathError, ValueError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    to = to or config.tracker
    if to not in ("linear", "jira") or direction not in ("both", "push", "pull"):
        typer.echo(
            "sync-status needs --to linear or jira and --direction both, push or pull.", err=True
        )
        raise typer.Exit(code=2)
    plan = load_epics_md(path, include_done=include_done or config.include_done)
    assign_plan_ids(plan)
    fill_todos(plan, config.todo_source, proposal)
    try:
        if to == "linear":
            name = project or plan.name or path.resolve().name
            linear_config = LinearConfig.from_env()
            typer.echo(f"Linear project: {name}{' (dry run, nothing written)' if dry_run else ''}")
            with LinearClient(linear_config) as linear:
                report = sync_status_with(
                    plan,
                    LinearStatusBackend(linear, name, config.status_map),
                    base=base,
                    direction=direction,  # type: ignore[arg-type]
                    dry_run=dry_run,
                )
        else:
            jira_config = JiraConfig.from_env()
            jira_project = project or os.environ.get("SYMPHONY_WORKFLOW_JIRA_PROJECT", "")
            if not jira_project:
                prefixes = {k.rsplit("-", 1)[0] for k in base.keys.values()}
                jira_project = prefixes.pop() if len(prefixes) == 1 else ""
            if not jira_project:
                typer.echo(
                    "--to jira needs --project KEY (or SYMPHONY_WORKFLOW_JIRA_PROJECT).", err=True
                )
                raise typer.Exit(code=2)
            typer.echo(f"Jira (cards from the last sync-plan){' (dry run)' if dry_run else ''}")
            with JiraClient(jira_config) as jira:
                report = sync_status_with(
                    plan,
                    JiraStatusBackend(jira, base.keys, config.status_map, project_key=jira_project),
                    base=base,
                    direction=direction,  # type: ignore[arg-type]
                    dry_run=dry_run,
                )
    except ConfigError as exc:
        typer.echo(f"{exc} (set them in this project's .env; see `check-env`)", err=True)
        raise typer.Exit(code=2) from exc
    except (LinearError, JiraError) as exc:
        typer.echo(f"{to.capitalize()} error: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    for change in report.changes:
        if change.action == "unchanged":
            continue
        typer.echo(
            f"  {change.action:9} {change.kind:6} {change.id}  "
            f"plan={change.plan} tracker={change.tracker} (last sync: {change.base})"
        )
    counts = {a: sum(1 for c in report.changes if c.action == a) for a in
              ("pull", "push", "conflict", "orphan", "skipped", "no_state")}  # fmt: skip
    summary = ", ".join(f"{n} {a}" for a, n in counts.items() if n)
    typer.echo(f"  {summary or 'nothing to do'}")
    if not dry_run:
        try:
            if counts["pull"]:
                write_markdown(plan, path)
            save_sync_state(path, report.new_state)
        except UnsafePathError as exc:
            typer.echo(str(exc), err=True)
            raise typer.Exit(code=2) from exc
    if report.conflicts:
        typer.echo("Conflicts were left as they are: fix one side, then run this again.", err=True)
        raise typer.Exit(code=1)


@app.command("split-todos")
def split_todos_command(
    path: Path = _ROOT_OPTION,
    approve: bool = typer.Option(False, "--approve", help="Approve the reviewed proposal"),
    include_done: bool = typer.Option(False, "--include-done", help="Also cover completed phases"),
) -> None:
    """Propose To-Dos for Tasks that have none, behind a human review gate (ADLC-201).

    Runs one read-only agent turn per Task and writes docs/context/todo-proposal.{json,md}. Edit
    the JSON, then run again with --approve; `sync-plan` only uses an approved proposal, so nothing
    reaches a tracker before a human has looked. Exit codes: 0 ok, 1 nothing to do, 2 bad input,
    3 the agent failed.
    """
    plan = load_epics_md(path, include_done=include_done)
    assign_plan_ids(plan)
    try:
        if approve:
            proposal = approve_proposal(path, plan)
            typer.echo(f"Approved {len(proposal.tasks)} Task(s) in {path / PROPOSAL_JSON}.")
            return
        proposal = asyncio.run(propose(plan, lambda t: split_with_agent(t, cwd=str(path))))
        if not proposal.tasks:
            typer.echo("Every Task already has To-Dos; nothing to propose.")
            raise typer.Exit(code=1)
        json_path, md_path = save_proposal(path, proposal, plan)
    except (UnsafePathError, ValueError, FileNotFoundError) as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(code=2) from exc
    except AgentRunError as exc:
        typer.echo(f"The agent failed: {exc}", err=True)
        raise typer.Exit(code=3) from exc
    typer.echo(f"Proposed To-Dos for {len(proposal.tasks)} Task(s): {md_path}")
    typer.echo(f"Review {json_path}, then run `symphony-workflow split-todos --approve`.")


_ENV_PATH_OPTION = typer.Option(Path(".env"), "--path", help="The credentials file to use")


@app.command("init-env")
def init_env_command(path: Path = _ENV_PATH_OPTION) -> None:
    """Create this project's `.env` from a template and make sure git ignores it (ADLC-189).

    Writes only empty keys: fill in JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN yourself, in an
    editor, never in a chat. Refuses to overwrite an existing file.

    Exit codes: 0 created, 1 the file already exists.
    """
    try:
        result = init_env(path)
    except EnvExistsError as exc:
        typer.echo(
            f"{path} already exists; not overwriting. Run `symphony-workflow check-env`.", err=True
        )
        raise typer.Exit(code=1) from exc
    typer.echo(f"Created {result.path} (owner-only permissions).")
    if result.added_to_gitignore:
        typer.echo(f"Added {path.name} to .gitignore.")
    typer.echo(
        "Next: fill in JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN, then run "
        "`symphony-workflow check-env --verify`."
    )


@app.command("check-env")
def check_env_command(
    path: Path = _ENV_PATH_OPTION,
    verify: bool = typer.Option(
        False, "--verify", help="Also make one read-only call to Jira with these credentials"
    ),
    as_json: bool = typer.Option(False, "--json", help="Machine-readable output"),
) -> None:
    """Report which Jira keys are set in `.env` and whether git ignores it. Never prints a value.

    With --verify, one read-only call tells a bad URL, a bad token and a working link apart.

    Exit codes: 0 all good, 1 keys missing or the file not ignored, 3 --verify failed.
    """
    report = check_env(path)
    verification = verify_env(path) if verify and report.ok else None
    if as_json:
        payload = report.as_dict()
        if verification:
            payload["verify"] = verification.as_dict()
        typer.echo(json.dumps(payload, indent=2))
    else:
        typer.echo(f"{report.path}: {'found' if report.exists else 'NOT FOUND'}")
        for key in report.present:
            typer.echo(f"  ok       {key}")
        for key in report.missing:
            typer.echo(f"  MISSING  {key}")
        if report.exists and report.gitignored is False:
            typer.echo("  NOT-IGNORED: git would track this file; add it to .gitignore")
        if verification:
            typer.echo(f"  verify: {verification.outcome}: {verification.detail}")
        elif verify:
            typer.echo("  verify: skipped until the problems above are fixed")
        if not report.exists:
            typer.echo("Create it with `symphony-workflow init-env`.")
    if not report.ok:
        raise typer.Exit(code=1)
    if verification and not verification.ok:
        raise typer.Exit(code=3)


if __name__ == "__main__":
    app()
