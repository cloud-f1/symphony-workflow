"""Unit tests for per-project credential setup (envsetup.py, ADLC-189).

Files live in `tmp_path`; Jira is mocked with respx. A real `git init` backs the gitignore cases,
with the machine's global git config switched off so the result does not depend on who runs it.
"""

from __future__ import annotations

import stat
import subprocess
from pathlib import Path

import httpx
import pytest
import respx

from symphony_workflow.envsetup import (
    REQUIRED_KEYS,
    EnvExistsError,
    check_env,
    init_env,
    parse_env,
    verify_env,
)

from .conftest import BASE_URL

SECRET = "tok-DO-NOT-LEAK-123"


@pytest.fixture(autouse=True)
def _hermetic_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def _git_init(path: Path) -> None:
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _write(path: Path, base_url: str = BASE_URL, token: str = SECRET) -> None:
    path.write_text(
        f"JIRA_BASE_URL={base_url}\nJIRA_EMAIL=me@example.com\nJIRA_API_TOKEN={token}\n",
        encoding="utf-8",
    )


def test_parse_env_handles_comments_export_and_quotes() -> None:
    lines = ["# note", 'export JIRA_EMAIL="me@example.com"', "JIRA_API_TOKEN='abc'", "JUNK"]
    text = "\n".join([*lines, "JIRA_BASE_URL="])
    assert parse_env(text) == {
        "JIRA_EMAIL": "me@example.com",
        "JIRA_API_TOKEN": "abc",
        "JIRA_BASE_URL": "",
    }


def test_check_env_missing_file_reports_every_key() -> None:
    report = check_env(Path("/nonexistent-dir/.env"))
    assert not report.exists and not report.ok
    assert report.missing == list(REQUIRED_KEYS)


def test_check_env_names_empty_keys_and_never_returns_values(tmp_path: Path) -> None:
    _git_init(tmp_path)
    env = tmp_path / ".env"
    env.write_text(f"JIRA_BASE_URL={BASE_URL}\nJIRA_EMAIL=\nJIRA_API_TOKEN={SECRET}\n")
    report = check_env(env)
    assert report.present == ["JIRA_BASE_URL", "JIRA_API_TOKEN"]
    assert report.missing == ["JIRA_EMAIL"]
    assert SECRET not in str(report.as_dict())


def test_check_env_flags_a_file_git_would_track(tmp_path: Path) -> None:
    _git_init(tmp_path)
    env = tmp_path / ".env"
    _write(env)
    assert check_env(env).gitignored is False and not check_env(env).ok
    (tmp_path / ".gitignore").write_text(".env\n")
    assert check_env(env).gitignored is True and check_env(env).ok


def test_check_env_outside_a_git_repo_does_not_fail_the_check(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _write(env)
    report = check_env(env)
    assert report.gitignored is None and report.ok


def test_init_env_creates_owner_only_template_and_updates_gitignore(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text("node_modules")  # no trailing newline
    result = init_env(tmp_path / ".env")
    assert result.added_to_gitignore
    assert (tmp_path / ".gitignore").read_text() == "node_modules\n.env\n"
    assert stat.S_IMODE((tmp_path / ".env").stat().st_mode) == 0o600
    assert check_env(tmp_path / ".env").missing == list(REQUIRED_KEYS)


def test_init_env_does_not_touch_gitignore_when_already_ignored(tmp_path: Path) -> None:
    _git_init(tmp_path)
    (tmp_path / ".gitignore").write_text(".env\n")
    assert init_env(tmp_path / ".env").added_to_gitignore is False
    assert (tmp_path / ".gitignore").read_text() == ".env\n"


def test_init_env_refuses_to_overwrite(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _write(env)
    with pytest.raises(EnvExistsError):
        init_env(env)
    assert SECRET in env.read_text()  # untouched


def test_verify_incomplete_makes_no_request(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("JIRA_BASE_URL=\n")
    with respx.mock(assert_all_called=False) as router:
        result = verify_env(env)
        assert not router.calls
    assert (result.ok, result.outcome) == (False, "incomplete")


@respx.mock
def test_verify_ok_reports_the_user_and_sends_basic_auth(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _write(env)
    route = respx.get(f"{BASE_URL}/rest/api/2/myself").mock(
        return_value=httpx.Response(200, json={"accountId": "a1", "displayName": "Jira Bot"})
    )
    result = verify_env(env)
    assert route.called and route.calls.last.request.method == "GET"
    assert (result.ok, result.outcome) == (True, "ok")
    assert "Jira Bot" in result.detail and SECRET not in result.detail


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_verify_unauthorized_points_at_email_and_token(tmp_path: Path, status: int) -> None:
    env = tmp_path / ".env"
    _write(env)
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(return_value=httpx.Response(status))
    result = verify_env(env)
    assert (result.ok, result.outcome) == (False, "unauthorized")
    assert "JIRA_API_TOKEN" in result.detail and SECRET not in result.detail


@respx.mock
def test_verify_other_http_error_points_at_the_url(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _write(env)
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(return_value=httpx.Response(404))
    result = verify_env(env)
    assert (result.ok, result.outcome) == (False, "jira-error")
    assert "JIRA_BASE_URL" in result.detail


@respx.mock
def test_verify_unreachable_host_is_reported_not_raised(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    _write(env)
    respx.get(f"{BASE_URL}/rest/api/2/myself").mock(side_effect=httpx.ConnectError("no route"))
    result = verify_env(env)
    assert (result.ok, result.outcome) == (False, "unreachable")
