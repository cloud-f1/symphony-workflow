"""Tool-call layer: exposes Jira operations as discrete, schema-described
functions an AI agent can invoke by name with JSON arguments — the same
shape as an Anthropic/OpenAI function-calling tool definition. This is the
layer other agent orchestration (e.g. `symphony-workflow call <tool> '<json>'`)
should target instead of importing JiraClient methods directly, because it
gives every tool a name, a description, and a validated input schema.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, Field

from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.drift import diff_decomposition
from symphony_workflow.epics_md import load_epics_md
from symphony_workflow.hierarchy import AddSubtasksAborted, PlanError, add_subtasks, audit
from symphony_workflow.jira_project import PROJECT_KEY_RE, ProjectRefused, create_project
from symphony_workflow.plan import check_plan
from symphony_workflow.project_profile import profile_project, recommend
from symphony_workflow.releasing import draft_changelog
from symphony_workflow.rollup import RollupAborted, RollupNotFound, rollup
from symphony_workflow.safepath import scoped_path
from symphony_workflow.ship import ShipError, check_commit_subject, hardened_run
from symphony_workflow.sync_decomposition import (
    SyncDecompositionError,
    load_decomposition,
    load_state,
)


class GetIssueArgs(BaseModel):
    key: str


class SearchIssuesArgs(BaseModel):
    jql: str
    max_results: int = 50


class CreateIssueArgs(BaseModel):
    project_key: str
    summary: str
    issue_type: str = "Task"
    description: str | None = None
    labels: list[str] | None = None
    epic: str | None = None
    parent: str | None = None
    sprint_id: int | None = None
    story_points: float | None = None
    assignee_account_id: str | None = None
    # Idempotent create (plan 0010): search the project for this label first; none -> create with
    # the label and read it back, one -> create nothing and return it with existing=true, two or
    # more -> error. Without it, create_issue behaves as before.
    identity_label: str | None = None


class UpdateIssueArgs(BaseModel):
    key: str
    summary: str | None = None
    description: str | None = None
    parent: str | None = None
    # Labels to add; existing labels are kept (plan 0010). No whitespace in a label.
    labels_add: list[str] | None = None


class AddCommentArgs(BaseModel):
    key: str
    body: str


class TransitionIssueArgs(BaseModel):
    key: str
    transition_name: str


class RollupIssueArgs(BaseModel):
    key: str
    dry_run: bool = False


class AuditHierarchyArgs(BaseModel):
    project: str
    open_only: bool = False
    assignee: str | None = None


class DiffDecompositionArgs(BaseModel):
    epic_key: str
    adopt: bool = False


class ProfileProjectArgs(BaseModel):
    path: str = "."


class ImportPlanArgs(BaseModel):
    path: str = "."
    include_done: bool = False


class CheckCommitSubjectArgs(BaseModel):
    subject: str


class ChangelogDraftArgs(BaseModel):
    version: str
    since: str | None = None
    path: str = "."


class AddSubtasksArgs(BaseModel):
    plan: dict[str, list[str]]
    dry_run: bool = False
    assign_to_me: bool = False


class GetMyselfArgs(BaseModel):
    """No arguments — returns the user the configured API token authenticates as."""


class CreateProjectArgs(BaseModel):
    key: str = Field(pattern=PROJECT_KEY_RE.pattern, description="Project key, e.g. WEB")
    name: str = Field(min_length=1, max_length=80)
    style: Literal["team", "company"] = "team"
    template: str | None = Field(
        None, description="projectTemplateKey; default is the scrum template for `style`"
    )
    description: str | None = None
    lead_account_id: str | None = Field(
        None, description="Project lead; default is the token's own account"
    )
    dry_run: bool = False


class CreateRemoteLinkArgs(BaseModel):
    key: str
    url: str
    title: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[Any, Any], Any]

    def json_schema(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.args_model.model_json_schema(),
        }


def _get_issue(client: JiraClient, args: GetIssueArgs) -> dict[str, Any]:
    return client.get_issue(args.key).model_dump()


def _search_issues(client: JiraClient, args: SearchIssuesArgs) -> dict[str, Any]:
    return client.search_issues(args.jql, args.max_results).model_dump()


def _create_issue(client: JiraClient, args: CreateIssueArgs) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if args.identity_label is not None:  # only passed when asked, so default calls are unchanged
        extra["identity_label"] = args.identity_label
    result = client.create_issue(
        args.project_key,
        args.summary,
        args.description,
        args.issue_type,
        labels=args.labels,
        epic=args.epic,
        parent=args.parent,
        sprint_id=args.sprint_id,
        story_points=args.story_points,
        assignee_account_id=args.assignee_account_id,
        **extra,
    )
    return result.model_dump()


def _update_issue(client: JiraClient, args: UpdateIssueArgs) -> dict[str, Any]:
    extra: dict[str, Any] = {}
    if args.labels_add:
        extra["labels_add"] = args.labels_add
    client.update_issue(
        args.key,
        summary=args.summary,
        description=args.description,
        parent=args.parent,
        **extra,
    )
    return {"ok": True, "key": args.key}


def _add_comment(client: JiraClient, args: AddCommentArgs) -> dict[str, Any]:
    client.add_comment(args.key, args.body)
    return {"ok": True, "key": args.key}


def _transition_issue(client: JiraClient, args: TransitionIssueArgs) -> dict[str, Any]:
    client.transition_issue(args.key, args.transition_name)
    return {"ok": True, "key": args.key, "transition": args.transition_name}


def _audit_hierarchy(client: JiraClient, args: AuditHierarchyArgs) -> dict[str, Any]:
    return audit(client, args.project, open_only=args.open_only, assignee=args.assignee).as_dict()


def _diff_decomposition(client: JiraClient, args: DiffDecompositionArgs) -> dict[str, Any]:
    from symphony_workflow.orchestrator import (
        RUNS_DOCS_ROOT,
    )  # lazy: orchestrator imports tools' peers

    try:
        decomposition = load_decomposition(args.epic_key, RUNS_DOCS_ROOT)
    except SyncDecompositionError as exc:
        raise JiraError(400, str(exc)) from exc
    known = load_state(RUNS_DOCS_ROOT / args.epic_key / "sync-state.json")
    return diff_decomposition(
        args.epic_key, decomposition, client=client, adopt=args.adopt, known=known
    ).as_dict()


def _add_subtasks(client: JiraClient, args: AddSubtasksArgs) -> dict[str, Any]:
    account = client.get_myself().account_id if args.assign_to_me else None
    try:
        return add_subtasks(
            client, args.plan, dry_run=args.dry_run, assignee_account_id=account
        ).as_dict()
    except PlanError as exc:
        raise JiraError(400, f"invalid plan: {exc}") from exc
    except AddSubtasksAborted as exc:
        made = ", ".join(c for s in exc.result.stories for c in s.created) or "nothing"
        raise JiraError(
            exc.cause.status_code, f"{exc.cause}; created before the failure: {made}"
        ) from exc


def _rollup_issue(client: JiraClient, args: RollupIssueArgs) -> dict[str, Any]:
    try:
        return rollup(client, args.key, dry_run=args.dry_run).as_dict()
    except RollupNotFound as exc:
        raise JiraError(404, str(exc)) from exc
    except RollupAborted as exc:
        done = ", ".join(f"{t.key} -> {t.to}" for t in exc.result.transitions) or "nothing"
        raise JiraError(
            exc.cause.status_code, f"{exc.cause}; transitioned before the failure: {done}"
        ) from exc


def _get_myself(client: JiraClient, args: GetMyselfArgs) -> dict[str, Any]:
    return client.get_myself().model_dump()


def _profile_project(_client: None, args: ProfileProjectArgs) -> dict[str, Any]:
    profile = profile_project(scoped_path(args.path))
    first, refined = recommend(profile)
    return {
        "profile": profile.as_dict(),
        "recommendation_1": first.as_dict(),
        "recommendation_2": refined.as_dict(),
    }


def _import_plan(_client: None, args: ImportPlanArgs) -> dict[str, Any]:
    plan = load_epics_md(scoped_path(args.path), include_done=args.include_done)
    return {
        "plan": plan.model_dump(),
        "counts": list(plan.counts()),
        "findings": [f.as_dict() for f in check_plan(plan)],
    }


def _check_commit_subject(_client: None, args: CheckCommitSubjectArgs) -> dict[str, Any]:
    problems = check_commit_subject(args.subject)
    return {"ok": not problems, "problems": problems}


def _changelog_draft(_client: None, args: ChangelogDraftArgs) -> dict[str, Any]:
    try:
        markdown = draft_changelog(
            args.version, repo=scoped_path(args.path), since=args.since, run=hardened_run
        )
    except ShipError as exc:
        raise ValueError(str(exc)) from exc
    return {"version": args.version, "markdown": markdown}


def _create_project(client: JiraClient, args: CreateProjectArgs) -> dict[str, Any]:
    try:
        return create_project(
            client,
            args.key,
            args.name,
            style=args.style,
            template=args.template,
            description=args.description,
            lead_account_id=args.lead_account_id,
            dry_run=args.dry_run,
        ).as_dict()
    except ProjectRefused as exc:
        raise JiraError(409, str(exc)) from exc


def _create_remote_link(client: JiraClient, args: CreateRemoteLinkArgs) -> dict[str, Any]:
    client.create_remote_link(args.key, url=args.url, title=args.title)
    return {"ok": True, "key": args.key, "url": args.url}


TOOLS: dict[str, Tool] = {
    tool.name: tool
    for tool in (
        Tool("get_issue", "Fetch a single Jira issue by key.", GetIssueArgs, _get_issue),
        Tool(
            "search_issues",
            "Search issues with a JQL query.",
            SearchIssuesArgs,
            _search_issues,
        ),
        Tool(
            "create_issue",
            "Create a new Jira issue. Link it with `parent` (an Epic's key for a Story, a Story's "
            "key for a Subtask), the field team-managed projects use; `epic` is the classic Epic "
            "Link field and fails there with 'not on the appropriate screen'.",
            CreateIssueArgs,
            _create_issue,
        ),
        Tool(
            "update_issue",
            "Update an existing Jira issue's summary, description and/or parent (re-parent), "
            "or add labels with `labels_add` (existing labels are kept).",
            UpdateIssueArgs,
            _update_issue,
        ),
        Tool(
            "add_comment",
            "Add a comment to an issue. Jira renders the body as wiki markup, not Markdown: a "
            "leading # is a numbered list, not a heading, so use bold lines instead.",
            AddCommentArgs,
            _add_comment,
        ),
        Tool(
            "transition_issue",
            "Move an issue through a named workflow transition (e.g. 'In Progress', 'Done').",
            TransitionIssueArgs,
            _transition_issue,
        ),
        Tool(
            "rollup_issue",
            "Mark a finished Subtask Done, then roll up: if all of its Story's Subtasks are "
            "Done, mark the Story Done; if all of the Epic's Stories are Done, mark the Epic "
            "Done. Also accepts a Story or Epic key (re-evaluates it and upward). Forward-only, "
            "idempotent; dry_run=true reports what would change without transitioning.",
            RollupIssueArgs,
            _rollup_issue,
        ),
        Tool(
            "diff_decomposition",
            "Read-only: compare docs/runs/<epic_key>/decomposition.json with the live Epic -> "
            "Story -> Subtask tree and report drift (missing, summary-changed, "
            "description-changed, parent-differs, orphan). Status/comments/links are not drift. "
            "`adopt` matches cards without an id marker by exact summary.",
            DiffDecompositionArgs,
            _diff_decomposition,
        ),
        Tool(
            "audit_hierarchy",
            "Check a Jira project against the Epic -> Story -> Subtask rules and report problems "
            "without changing anything: Stories with no Subtasks, Stories under no Epic, "
            "Epics with no Stories, Done Stories with open Subtasks, and (if `assignee` is given, "
            "a display name) cards not assigned to that person. `open_only` skips Done Stories.",
            AuditHierarchyArgs,
            _audit_hierarchy,
        ),
        Tool(
            "add_subtasks",
            "Add Subtasks under existing Stories from a plan {story key: [subtask summaries]}. "
            "Idempotent: a Story that already has Subtasks is skipped; a Done Story gets its new "
            "Subtasks rolled to Done. dry_run=true creates nothing; assign_to_me=true assigns the "
            "new Subtasks to the token's user.",
            AddSubtasksArgs,
            _add_subtasks,
        ),
        Tool(
            "get_myself",
            "Return the Jira user the configured API token authenticates as.",
            GetMyselfArgs,
            _get_myself,
        ),
        Tool(
            "profile_project",
            "Local and read-only (no Jira needed): read how a project already works -- plan "
            "files, tracker credential key NAMES (never values), issue-key history, content "
            "shape -- and return the "
            "profile plus two recommendations (from usage, then refined from content).",
            ProfileProjectArgs,
            _profile_project,
        ),
        Tool(
            "import_plan",
            "Local and read-only (no Jira needed): read a project's epics-as-markdown files "
            "into the neutral Category / Task / To-Do plan and report pyramid guardrail "
            "findings. Completed phases are "
            "skipped unless include_done=true.",
            ImportPlanArgs,
            _import_plan,
        ),
        Tool(
            "check_commit_subject",
            "Local and read-only (no Jira needed): check a commit subject is "
            "`type(scope): subject (KEY)`; returns ok and the problems to fix. The commands that "
            "change git (start, pr, land, release) are deliberately CLI-only, not tools.",
            CheckCommitSubjectArgs,
            _check_commit_subject,
        ),
        Tool(
            "changelog_draft",
            "Local and read-only (no Jira needed): draft a CHANGELOG section for `version` from "
            "the commit subjects since the last tag (or `since`), grouped by type, for the "
            "repository at `path` (inside the working directory). Returns markdown to edit; "
            "writes nothing.",
            ChangelogDraftArgs,
            _changelog_draft,
        ),
        Tool(
            "create_project",
            "Create a Jira project (one per repo). Idempotent: a project with this key that the "
            "token's user leads is returned as action='exists'; one led by someone else is "
            "refused. style 'team' (team-managed, default) or 'company'. Reads the project back "
            "and warns when it has no 'Subtask' issue type. dry_run=true returns the request "
            "body and creates nothing. Needs Jira ADMINISTER permission.",
            CreateProjectArgs,
            _create_project,
        ),
        Tool(
            "create_remote_link",
            "Add a Jira 'web link' (Links section, distinct from a comment) pointing an "
            "issue at an external URL — e.g. a merged docs/runs/<key>/plan.md on GitHub. "
            "Only call this once the target URL actually resolves.",
            CreateRemoteLinkArgs,
            _create_remote_link,
        ),
    )
}


# Tools that only touch local files: `call` runs them without a Jira client (so a project with no
# tracker can use them).
LOCAL_TOOLS = frozenset(
    {"profile_project", "import_plan", "check_commit_subject", "changelog_draft"}
)


def call_tool(client: JiraClient | None, name: str, raw_args: dict[str, Any]) -> Any:
    """Validate `raw_args` against the named tool's schema and run it. `client` may be None for
    LOCAL_TOOLS."""
    tool = TOOLS.get(name)
    if tool is None:
        available = ", ".join(sorted(TOOLS)) or "none"
        raise KeyError(f"Unknown tool {name!r}. Available: {available}")
    parsed = tool.args_model.model_validate(raw_args)
    return tool.handler(client, parsed)
