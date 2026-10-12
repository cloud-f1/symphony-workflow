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
from pathlib import Path

import typer

from symphony_workflow.agent_runner import AgentRunError
from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.config import ConfigError
from symphony_workflow.decompose import DecomposeError, assign_ids, execute_decompose
from symphony_workflow.drift import DiffReport, apply_update, diff_decomposition
from symphony_workflow.hierarchy import (
    AddSubtasksAborted,
    AddSubtasksResult,
    AuditResult,
    PlanError,
    add_subtasks,
    audit,
)
from symphony_workflow.orchestrator import (
    RUNS_DOCS_ROOT,
    TARGET_REPO,
    WORKSPACE_ROOT,
    execute_implement,
    execute_plan,
)
from symphony_workflow.rollup import RollupAborted, RollupNotFound, RollupResult, rollup
from symphony_workflow.runs import Run, RunStore, RunTransitionError
from symphony_workflow.sync_decomposition import (
    SyncDecompositionError,
    execute_sync_decomposition,
    load_decomposition,
    save_decomposition,
)
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
            report = diff_decomposition(key, decomposition, client=client, adopt=adopt)
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
        what = f"update {','.join(action.fields)}" if action.action == "update" else "create"
        typer.echo(f"  {label}: {what} {action.level} {action.key or ''} {action.summary}".rstrip())


if __name__ == "__main__":
    app()
