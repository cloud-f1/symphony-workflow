"""Create a Jira project for a newly adopted repo (one Jira project per repo, docs/PORTABILITY.md).

Shared by the `create_project` tool and the `create-project` command. Jira-only.

What it does, in order:

1. Check the key is free (`GET /rest/api/3/project/{key}`; 404 = free). An existing project led
   by the token's own user is reported as `exists` (idempotent re-run); one led by someone else is
   refused.
2. Resolve the body: the template for the chosen style, the token's own account as lead.
   `dry_run` stops here and returns the body.
3. `POST /rest/api/3/project`, then read the project back and check two things the template name
   cannot promise: the style is the one asked for, and there is a sub-task type named `Subtask`
   (what `sync-plan --to jira` creates; company-managed projects call it `Sub-task`).
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.models import Project

# Jira's default key rule: an uppercase letter, then uppercase letters or digits, 2 to 10 long.
PROJECT_KEY_RE = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")

Style = Literal["team", "company"]

# Scrum templates, both confirmed by `style` on the created project. The kanban variants are
# `gh-simplified-agility-kanban` (team) and `gh-simplified-kanban-classic` (company): "simplified"
# in a key does not mean team-managed, "classic" does mean company-managed.
TEMPLATES: dict[str, str] = {
    "team": "com.pyxis.greenhopper.jira:gh-simplified-agility-scrum",
    "company": "com.pyxis.greenhopper.jira:gh-simplified-scrum-classic",
}
_EXPECTED_STYLE = {"team": "next-gen", "company": "classic"}
SUBTASK_TYPE = "Subtask"  # jira_sink.sync_plan_to_jira's default `subtask_type`

Action = Literal["created", "exists", "dry_run"]


class ProjectRefused(Exception):
    """The key is taken by a project the token's user does not lead."""


@dataclass
class CreateProjectResult:
    key: str
    action: Action
    payload: dict[str, Any] = field(default_factory=dict)
    id: str | None = None
    url: str | None = None
    name: str | None = None
    style: str | None = None
    issue_types: list[str] = field(default_factory=list)
    subtask_types: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_key(key: str) -> str:
    if not PROJECT_KEY_RE.match(key):
        raise ValueError(
            f"invalid project key {key!r}: an uppercase letter, then 1 to 9 uppercase letters or "
            "digits (e.g. ADLC, CFT, WEB2)"
        )
    return key


def subtask_warnings(project: Project) -> list[str]:
    """A warning when the project has no sub-task type named `Subtask`, naming the one to use."""
    names = project.subtask_type_names
    if SUBTASK_TYPE in names:
        return []
    if names:
        return [
            f"{project.key} has no issue type named {SUBTASK_TYPE!r}; its sub-task type is "
            f"{names[0]!r}. Use subtask_type={names[0]!r} with sync_plan_to_jira (sync-plan "
            f"--to jira creates {SUBTASK_TYPE!r}); add-subtasks tries both spellings."
        ]
    return [
        f"{project.key} has no sub-task issue type at all; add one in the project settings "
        f"(named {SUBTASK_TYPE!r}) before sync-plan --to jira or add-subtasks."
    ]


def _fill(result: CreateProjectResult, project: Project, base_url: str) -> None:
    result.id = project.id
    result.name = project.name
    result.style = project.style
    result.url = f"{base_url}/browse/{project.key}"
    result.issue_types = [t.name for t in project.issue_types]
    result.subtask_types = project.subtask_type_names


def _lookup(client: JiraClient, key: str) -> Project | None:
    try:
        return client.get_project(key)
    except JiraError as exc:
        if exc.status_code == 404:
            return None
        raise


def create_project(
    client: JiraClient,
    key: str,
    name: str,
    *,
    style: Style = "team",
    template: str | None = None,
    description: str | None = None,
    lead_account_id: str | None = None,
    dry_run: bool = False,
) -> CreateProjectResult:
    """See the module docstring. Raises ProjectRefused, ValueError (bad key/style) or JiraError."""
    validate_key(key)
    if style not in TEMPLATES:
        raise ValueError(f"style must be 'team' or 'company', not {style!r}")
    me = client.get_myself().account_id
    lead = lead_account_id or me

    existing = _lookup(client, key)
    if existing is not None:
        if existing.lead_account_id not in (me, lead):
            who = existing.lead_display_name or "another user"
            raise ProjectRefused(
                f"project {key} already exists ({existing.name!r}, led by {who}); pick another key"
            )
        result = CreateProjectResult(key=key, action="exists")
        _fill(result, existing, client.base_url)
        result.warnings = subtask_warnings(existing)
        return result

    payload = JiraClient.project_payload(
        key,
        name,
        lead_account_id=lead,
        project_template_key=template or TEMPLATES[style],
        description=description,
    )
    if dry_run:
        return CreateProjectResult(key=key, action="dry_run", payload=payload, name=name)

    created = client.create_project(
        key,
        name,
        lead_account_id=lead,
        project_template_key=payload["projectTemplateKey"],
        description=description,
    )
    result = CreateProjectResult(
        key=created["key"], action="created", payload=payload, id=created["id"]
    )
    project = client.get_project(created["key"])
    _fill(result, project, client.base_url)
    expected = _EXPECTED_STYLE[style]
    if template is None and project.style and project.style != expected:
        result.warnings.append(
            f"asked for a {style}-managed project but Jira made style {project.style!r}; "
            "a deleted project keeps its key for about 60 days, so a retry needs a new key"
        )
    result.warnings.extend(subtask_warnings(project))
    return result
