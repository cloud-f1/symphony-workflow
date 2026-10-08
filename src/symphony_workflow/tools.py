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
from typing import Any

from pydantic import BaseModel

from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.rollup import RollupAborted, RollupNotFound, rollup


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


class UpdateIssueArgs(BaseModel):
    key: str
    summary: str | None = None
    description: str | None = None


class AddCommentArgs(BaseModel):
    key: str
    body: str


class TransitionIssueArgs(BaseModel):
    key: str
    transition_name: str


class RollupIssueArgs(BaseModel):
    key: str
    dry_run: bool = False


class GetMyselfArgs(BaseModel):
    """No arguments — returns the user the configured API token authenticates as."""


class CreateRemoteLinkArgs(BaseModel):
    key: str
    url: str
    title: str


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    args_model: type[BaseModel]
    handler: Callable[[JiraClient, Any], Any]

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
    )
    return result.model_dump()


def _update_issue(client: JiraClient, args: UpdateIssueArgs) -> dict[str, Any]:
    client.update_issue(args.key, summary=args.summary, description=args.description)
    return {"ok": True, "key": args.key}


def _add_comment(client: JiraClient, args: AddCommentArgs) -> dict[str, Any]:
    client.add_comment(args.key, args.body)
    return {"ok": True, "key": args.key}


def _transition_issue(client: JiraClient, args: TransitionIssueArgs) -> dict[str, Any]:
    client.transition_issue(args.key, args.transition_name)
    return {"ok": True, "key": args.key, "transition": args.transition_name}


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
        Tool("create_issue", "Create a new Jira issue.", CreateIssueArgs, _create_issue),
        Tool(
            "update_issue",
            "Update an existing Jira issue's summary and/or description.",
            UpdateIssueArgs,
            _update_issue,
        ),
        Tool("add_comment", "Add a comment to an issue.", AddCommentArgs, _add_comment),
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
            "get_myself",
            "Return the Jira user the configured API token authenticates as.",
            GetMyselfArgs,
            _get_myself,
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


def call_tool(client: JiraClient, name: str, raw_args: dict[str, Any]) -> Any:
    """Validate `raw_args` against the named tool's schema and run it."""
    tool = TOOLS.get(name)
    if tool is None:
        available = ", ".join(sorted(TOOLS)) or "none"
        raise KeyError(f"Unknown tool {name!r}. Available: {available}")
    parsed = tool.args_model.model_validate(raw_args)
    return tool.handler(client, parsed)
