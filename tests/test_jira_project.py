"""create_project: client methods, the tool, and the `create-project` command (ADLC-404).

Every HTTP call is mocked with respx; nothing here talks to a live Jira.
"""

from __future__ import annotations

import json

import httpx
import pytest
import respx
from pydantic import ValidationError
from typer.testing import CliRunner

from symphony_workflow.cli import app
from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.config import JiraConfig
from symphony_workflow.jira_project import (
    TEMPLATES,
    ProjectRefused,
    create_project,
    validate_key,
)
from symphony_workflow.tools import TOOLS, call_tool

from .conftest import BASE_URL

runner = CliRunner()
ME = "acc-me"
TEAM_TYPES = [
    {"name": "Epic", "subtask": False},
    {"name": "Subtask", "subtask": True},
    {"name": "Task", "subtask": False},
    {"name": "Story", "subtask": False},
]
CLASSIC_TYPES = [
    {"name": "Task", "subtask": False},
    {"name": "Sub-task", "subtask": True},
    {"name": "Epic", "subtask": False},
]


def _project(key: str, *, lead: str = ME, style: str = "next-gen", types=None) -> dict:
    return {
        "id": "10149",
        "key": key,
        "name": "Web App",
        "style": style,
        "simplified": style == "next-gen",
        "projectTypeKey": "software",
        "lead": {"accountId": lead, "displayName": "Owner"},
        "issueTypes": TEAM_TYPES if types is None else types,
    }


def _mock_myself() -> respx.Route:
    return respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": ME, "displayName": "Me"})
    )


def _mock_missing(key: str) -> respx.Route:
    return respx.get(f"{BASE_URL}/rest/api/3/project/{key}").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["No project could be found"]})
    )


# --- client ----------------------------------------------------------------------------------


@respx.mock
def test_get_project_parses_style_lead_and_issue_types(config: JiraConfig) -> None:
    route = respx.get(f"{BASE_URL}/rest/api/3/project/WEB").mock(
        return_value=httpx.Response(200, json=_project("WEB"))
    )
    with JiraClient(config) as client:
        project = client.get_project("WEB")
    assert route.calls.last.request.url.params["expand"] == "issueTypes,lead"
    assert (project.key, project.id, project.style, project.simplified) == (
        "WEB",
        "10149",
        "next-gen",
        True,
    )
    assert project.lead_account_id == ME
    assert [t.name for t in project.issue_types] == ["Epic", "Subtask", "Task", "Story"]
    assert project.subtask_type_names == ["Subtask"]


@respx.mock
def test_get_project_missing_raises_404(config: JiraConfig) -> None:
    _mock_missing("WEB")
    with JiraClient(config) as client, pytest.raises(JiraError) as exc:
        client.get_project("WEB")
    assert exc.value.status_code == 404


@respx.mock
def test_create_project_posts_the_v3_body(config: JiraConfig) -> None:
    route = respx.post(f"{BASE_URL}/rest/api/3/project").mock(
        return_value=httpx.Response(201, json={"id": 10150, "key": "WEB", "self": "x"})
    )
    with JiraClient(config) as client:
        created = client.create_project(
            "WEB",
            "Web App",
            lead_account_id=ME,
            project_template_key=TEMPLATES["team"],
            description="the web repo",
        )
    assert created == {"id": "10150", "key": "WEB", "url": f"{BASE_URL}/browse/WEB"}
    assert json.loads(route.calls.last.request.content) == {
        "key": "WEB",
        "name": "Web App",
        "projectTypeKey": "software",
        "projectTemplateKey": "com.pyxis.greenhopper.jira:gh-simplified-agility-scrum",
        "leadAccountId": ME,
        "assigneeType": "UNASSIGNED",
        "description": "the web repo",
    }


@respx.mock
def test_create_project_error_is_a_jira_error(config: JiraConfig) -> None:
    respx.post(f"{BASE_URL}/rest/api/3/project").mock(
        return_value=httpx.Response(400, json={"errors": {"projectKey": "already in use"}})
    )
    with JiraClient(config) as client, pytest.raises(JiraError, match="already in use"):
        client.create_project("WEB", "Web", lead_account_id=ME, project_template_key="t")


# --- shared logic ----------------------------------------------------------------------------


@pytest.mark.parametrize("key", ["AB", "ADLC", "WEB2", "A123456789"])
def test_valid_keys(key: str) -> None:
    assert validate_key(key) == key


@pytest.mark.parametrize("key", ["A", "adlc", "1ABC", "AB-C", "ABCDEFGHIJK", ""])
def test_invalid_keys(key: str) -> None:
    with pytest.raises(ValueError, match="invalid project key"):
        validate_key(key)


@respx.mock
def test_create_reads_back_and_warns_on_style_and_subtask_mismatch(config: JiraConfig) -> None:
    _mock_myself()
    get = respx.get(f"{BASE_URL}/rest/api/3/project/WEB")
    get.side_effect = [
        httpx.Response(404, json={"errorMessages": ["none"]}),
        httpx.Response(200, json=_project("WEB", style="classic", types=CLASSIC_TYPES)),
    ]
    respx.post(f"{BASE_URL}/rest/api/3/project").mock(
        return_value=httpx.Response(201, json={"id": "10150", "key": "WEB"})
    )
    with JiraClient(config) as client:
        result = create_project(client, "WEB", "Web App")
    assert result.action == "created" and result.subtask_types == ["Sub-task"]
    assert any("team-managed" in w and "classic" in w for w in result.warnings)
    assert any("subtask_type='Sub-task'" in w for w in result.warnings)


@respx.mock
def test_existing_project_led_by_someone_else_is_refused(config: JiraConfig) -> None:
    _mock_myself()
    respx.get(f"{BASE_URL}/rest/api/3/project/WEB").mock(
        return_value=httpx.Response(200, json=_project("WEB", lead="someone-else"))
    )
    post = respx.post(f"{BASE_URL}/rest/api/3/project")
    with JiraClient(config) as client, pytest.raises(ProjectRefused, match="already exists"):
        create_project(client, "WEB", "Web App")
    assert not post.called


# --- tool ------------------------------------------------------------------------------------


def test_create_project_tool_is_registered_with_a_validated_schema() -> None:
    schema = TOOLS["create_project"].json_schema()["input_schema"]
    assert schema["properties"]["key"]["pattern"] == "^[A-Z][A-Z0-9]{1,9}$"
    assert schema["properties"]["style"]["default"] == "team"
    assert set(schema["required"]) == {"key", "name"}


@pytest.mark.parametrize(
    "args",
    [
        {"key": "web", "name": "Web"},
        {"key": "W", "name": "Web"},
        {"key": "WEB", "name": ""},
        {"key": "WEB", "name": "Web", "style": "kanban"},
        {"name": "Web"},
    ],
)
def test_create_project_tool_rejects_bad_input(args: dict) -> None:
    with pytest.raises(ValidationError):
        call_tool(None, "create_project", args)  # type: ignore[arg-type]


@respx.mock
def test_create_project_tool_dry_run_posts_nothing(config: JiraConfig) -> None:
    _mock_myself()
    _mock_missing("WEB")
    post = respx.post(f"{BASE_URL}/rest/api/3/project")
    with JiraClient(config) as client:
        out = call_tool(
            client,
            "create_project",
            {"key": "WEB", "name": "Web", "style": "company", "dry_run": True},
        )
    assert out["action"] == "dry_run" and not post.called
    assert out["payload"]["projectTemplateKey"] == TEMPLATES["company"]
    assert out["payload"]["leadAccountId"] == ME


@respx.mock
def test_create_project_tool_maps_refusal_to_jira_error(config: JiraConfig) -> None:
    _mock_myself()
    respx.get(f"{BASE_URL}/rest/api/3/project/WEB").mock(
        return_value=httpx.Response(200, json=_project("WEB", lead="other"))
    )
    with JiraClient(config) as client, pytest.raises(JiraError) as exc:
        call_tool(client, "create_project", {"key": "WEB", "name": "Web"})
    assert exc.value.status_code == 409


# --- CLI -------------------------------------------------------------------------------------


@pytest.fixture
def jira_env(monkeypatch) -> None:
    monkeypatch.delenv("SYMPHONY_WORKFLOW_TRACKER", raising=False)
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret-token-value")


@respx.mock
def test_cli_dry_run_prints_the_payload_and_creates_nothing(jira_env) -> None:
    _mock_myself()
    _mock_missing("WEB")
    post = respx.post(f"{BASE_URL}/rest/api/3/project")
    result = runner.invoke(app, ["create-project", "WEB", "--name", "Web App", "--dry-run"])
    assert result.exit_code == 0, result.output
    assert not post.called
    assert "WEB is free" in result.output and "dry run" in result.output.lower()
    body = json.loads(result.output[result.output.index("{") :])
    assert body["projectTemplateKey"] == TEMPLATES["team"] and body["leadAccountId"] == ME
    assert "secret-token-value" not in result.output and "bot@example.com" not in result.output


@respx.mock
def test_cli_existing_project_you_lead_is_already_exists_exit_0(jira_env) -> None:
    _mock_myself()
    respx.get(f"{BASE_URL}/rest/api/3/project/WEB").mock(
        return_value=httpx.Response(200, json=_project("WEB"))
    )
    post = respx.post(f"{BASE_URL}/rest/api/3/project")
    result = runner.invoke(app, ["create-project", "WEB", "--name", "Web App"])
    assert result.exit_code == 0, result.output
    assert "WEB already exists" in result.output
    assert not post.called


@respx.mock
def test_cli_existing_project_of_someone_else_exits_1(jira_env) -> None:
    _mock_myself()
    respx.get(f"{BASE_URL}/rest/api/3/project/WEB").mock(
        return_value=httpx.Response(200, json=_project("WEB", lead="other"))
    )
    result = runner.invoke(app, ["create-project", "WEB", "--name", "Web App"])
    assert result.exit_code == 1
    assert "pick another key" in result.output


@respx.mock
def test_cli_creates_then_reads_back_issue_types(jira_env) -> None:
    _mock_myself()
    get = respx.get(f"{BASE_URL}/rest/api/3/project/WEB")
    get.side_effect = [
        httpx.Response(404, json={"errorMessages": ["none"]}),
        httpx.Response(200, json=_project("WEB")),
    ]
    post = respx.post(f"{BASE_URL}/rest/api/3/project").mock(
        return_value=httpx.Response(201, json={"id": "10150", "key": "WEB"})
    )
    result = runner.invoke(
        app, ["create-project", "WEB", "--name", "Web App", "--description", "repo"]
    )
    assert result.exit_code == 0, result.output
    assert post.called and json.loads(post.calls.last.request.content)["description"] == "repo"
    assert "WEB created" in result.output
    assert "Epic, Subtask, Task, Story" in result.output
    assert "WARNING" not in result.output


@respx.mock
def test_cli_company_project_warns_which_subtask_type_to_use(jira_env) -> None:
    _mock_myself()
    get = respx.get(f"{BASE_URL}/rest/api/3/project/OPS")
    get.side_effect = [
        httpx.Response(404, json={"errorMessages": ["none"]}),
        httpx.Response(200, json=_project("OPS", style="classic", types=CLASSIC_TYPES)),
    ]
    post = respx.post(f"{BASE_URL}/rest/api/3/project").mock(
        return_value=httpx.Response(201, json={"id": "10151", "key": "OPS"})
    )
    result = runner.invoke(app, ["create-project", "OPS", "--name", "Ops", "--style", "company"])
    assert result.exit_code == 0, result.output
    sent = json.loads(post.calls.last.request.content)
    assert sent["projectTemplateKey"] == TEMPLATES["company"]
    assert "WARNING" in result.output and "subtask_type='Sub-task'" in result.output
    assert "team-managed" not in result.output  # a classic project is what --style company asked


def test_cli_rejects_a_bad_key_or_style_before_any_call(jira_env) -> None:
    bad_key = runner.invoke(app, ["create-project", "web", "--name", "Web"])
    assert bad_key.exit_code == 1 and "invalid project key" in bad_key.output
    bad_style = runner.invoke(app, ["create-project", "WEB", "--name", "Web", "--style", "x"])
    assert bad_style.exit_code == 1 and "--style" in bad_style.output


@respx.mock
def test_cli_permission_error_exits_3_with_a_hint(jira_env) -> None:
    _mock_myself()
    _mock_missing("WEB")
    respx.post(f"{BASE_URL}/rest/api/3/project").mock(
        return_value=httpx.Response(403, json={"errorMessages": ["You cannot create projects"]})
    )
    result = runner.invoke(app, ["create-project", "WEB", "--name", "Web"])
    assert result.exit_code == 3
    assert "ADMINISTER" in result.output
