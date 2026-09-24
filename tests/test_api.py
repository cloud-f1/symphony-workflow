"""Unit tests for the FastAPI server — HTTP calls to Jira mocked via respx,
requests to the API itself go through Starlette's TestClient (no network).
"""

from __future__ import annotations

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from jira_agent_cli.api import app

from .conftest import BASE_URL


@pytest.fixture
def api_client(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIRA_BASE_URL", BASE_URL)
    monkeypatch.setenv("JIRA_EMAIL", "bot@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "token")
    with TestClient(app) as client:
        yield client


def test_list_tools_returns_every_registered_schema(api_client: TestClient) -> None:
    response = api_client.get("/tools")
    assert response.status_code == 200
    names = {tool["name"] for tool in response.json()}
    assert {"get_issue", "create_issue", "search_issues"} <= names


@respx.mock
def test_call_tool_dispatches_get_issue(api_client: TestClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CF1-1",
                "id": "1",
                "fields": {"summary": "Test", "status": {"name": "Todo"}},
            },
        )
    )
    response = api_client.post("/tools/get_issue/call", json={"arguments": {"key": "CF1-1"}})
    assert response.status_code == 200
    assert response.json()["key"] == "CF1-1"


def test_call_tool_unknown_name_returns_404(api_client: TestClient) -> None:
    response = api_client.post("/tools/delete_everything/call", json={"arguments": {}})
    assert response.status_code == 404


def test_call_tool_invalid_arguments_returns_422(api_client: TestClient) -> None:
    response = api_client.post("/tools/get_issue/call", json={"arguments": {}})
    assert response.status_code == 422


@respx.mock
def test_call_tool_jira_error_maps_to_matching_status(api_client: TestClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-404").mock(
        return_value=httpx.Response(404, json={"errorMessages": ["not found"]})
    )
    response = api_client.post(
        "/tools/get_issue/call", json={"arguments": {"key": "CF1-404"}}
    )
    assert response.status_code == 404


@respx.mock
def test_get_issue_endpoint(api_client: TestClient) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/CF1-1").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": "CF1-1",
                "id": "1",
                "fields": {"summary": "Test", "status": {"name": "Todo"}},
            },
        )
    )
    response = api_client.get("/issues/CF1-1")
    assert response.status_code == 200
    assert response.json()["summary"] == "Test"


def test_create_and_fetch_run_roundtrip(api_client: TestClient) -> None:
    created = api_client.post("/runs", json={"issue_key": "CF1-286"})
    assert created.status_code == 201
    run_id = created.json()["id"]
    assert created.json()["state"] == "created"

    fetched = api_client.get(f"/runs/{run_id}")
    assert fetched.status_code == 200
    assert fetched.json()["issue_key"] == "CF1-286"


def test_get_unknown_run_returns_404(api_client: TestClient) -> None:
    response = api_client.get("/runs/does-not-exist")
    assert response.status_code == 404


def test_missing_jira_env_fails_app_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIRA_BASE_URL", raising=False)
    monkeypatch.delenv("JIRA_EMAIL", raising=False)
    monkeypatch.delenv("JIRA_API_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Missing required env var"), TestClient(app):
        pass
