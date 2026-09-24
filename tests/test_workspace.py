"""Unit tests for WorkspaceManager — uses a real temp git repo (fast, no
mocking git) so the actual `git worktree add`/`git worktree remove` behavior
is exercised. Lifecycle hooks (Symphony §9.4) are exercised with real shell
commands writing marker files, not mocked subprocess calls, for the same
reason.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import pytest

from jira_agent_cli.workspace import (
    WorkspaceError,
    WorkspaceHooks,
    WorkspaceManager,
    branch_name,
    sanitize_key,
    slugify,
)


@pytest.fixture
def source_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "source"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("hello")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def test_sanitize_key_replaces_invalid_characters() -> None:
    assert sanitize_key("CF1-123") == "CF1-123"
    assert sanitize_key("ADLC/weird key!") == "ADLC_weird_key_"


def test_ensure_creates_a_worktree(tmp_path: Path, source_repo: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    path = manager.ensure("ADLC-1")
    assert path.exists()
    assert (path / "README.md").exists()
    assert path == manager.root / "ADLC-1"


def test_ensure_is_idempotent(tmp_path: Path, source_repo: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    first = manager.ensure("ADLC-1")
    second = manager.ensure("ADLC-1")
    assert first == second


def test_ensure_sanitizes_the_issue_key(tmp_path: Path, source_repo: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    path = manager.ensure("weird/key")
    assert path.name == "weird_key"


def test_validate_rejects_a_path_outside_the_root(tmp_path: Path, source_repo: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    with pytest.raises(WorkspaceError):
        manager._validate(tmp_path / "elsewhere")


def test_ensure_raises_on_git_failure(tmp_path: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=tmp_path / "not-a-repo")
    with pytest.raises(WorkspaceError):
        manager.ensure("ADLC-1")


def test_slugify_lowercases_and_hyphenates() -> None:
    assert slugify("Add GET /health endpoint") == "add-get-health-endpoint"


def test_slugify_collapses_and_trims_punctuation() -> None:
    assert slugify("  weird---key!! ") == "weird-key"


def test_slugify_caps_length() -> None:
    long_text = "word " * 30
    assert len(slugify(long_text)) <= 60


def test_branch_name_starts_with_the_key_so_jira_auto_links() -> None:
    # Jira's Development-panel auto-linking requires the branch to START WITH
    # the issue key — confirmed missing on ADLC-17 (jira-agent-cli/ADLC-17)
    # before this fix; this is the regression test for that.
    name = branch_name("ADLC-24", "Poller live-verify: add a __version__ constant")
    assert name.startswith("ADLC-24-")
    assert name == "ADLC-24-poller-live-verify-add-a-version-constant"


def test_branch_name_falls_back_to_key_alone_without_a_summary() -> None:
    assert branch_name("ADLC-1") == "ADLC-1"
    assert branch_name("ADLC-1", "") == "ADLC-1"


def test_ensure_uses_branch_slug_for_the_actual_git_branch(
    tmp_path: Path, source_repo: Path
) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    path = manager.ensure("ADLC-24", branch_slug="Poller live-verify: add a __version__ constant")
    current_branch = subprocess.run(
        ["git", "branch", "--show-current"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()
    assert current_branch == "ADLC-24-poller-live-verify-add-a-version-constant"


# --- Lifecycle hooks (Symphony §9.4) ----------------------------------------


def test_hooks_from_env_reads_all_four_hook_points_and_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_HOOK_AFTER_CREATE", "echo created")
    monkeypatch.setenv("JIRA_AGENT_CLI_HOOK_BEFORE_RUN", "echo before")
    monkeypatch.setenv("JIRA_AGENT_CLI_HOOK_AFTER_RUN", "echo after")
    monkeypatch.setenv("JIRA_AGENT_CLI_HOOK_BEFORE_REMOVE", "echo removing")
    monkeypatch.setenv("JIRA_AGENT_CLI_HOOK_TIMEOUT_MS", "5000")

    hooks = WorkspaceHooks.from_env()

    assert hooks.after_create == "echo created"
    assert hooks.before_run == "echo before"
    assert hooks.after_run == "echo after"
    assert hooks.before_remove == "echo removing"
    assert hooks.timeout_ms == 5000


def test_hooks_from_env_defaults_to_all_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in (
        "JIRA_AGENT_CLI_HOOK_AFTER_CREATE",
        "JIRA_AGENT_CLI_HOOK_BEFORE_RUN",
        "JIRA_AGENT_CLI_HOOK_AFTER_RUN",
        "JIRA_AGENT_CLI_HOOK_BEFORE_REMOVE",
        "JIRA_AGENT_CLI_HOOK_TIMEOUT_MS",
    ):
        monkeypatch.delenv(var, raising=False)

    hooks = WorkspaceHooks.from_env()

    assert hooks == WorkspaceHooks()


def test_ensure_runs_after_create_hook_with_issue_key_and_path_env_vars(
    tmp_path: Path, source_repo: Path
) -> None:
    marker = tmp_path / "after-create-marker.txt"
    hooks = WorkspaceHooks(
        after_create=(
            f'echo "$JIRA_AGENT_CLI_ISSUE_KEY $JIRA_AGENT_CLI_WORKSPACE_PATH" > "{marker}"'
        )
    )
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo, hooks=hooks)

    path = manager.ensure("ADLC-1")

    assert marker.read_text().strip() == f"ADLC-1 {path}"


def test_ensure_does_not_rerun_after_create_hook_on_reuse(
    tmp_path: Path, source_repo: Path
) -> None:
    marker = tmp_path / "count.txt"
    hooks = WorkspaceHooks(after_create=f'echo x >> "{marker}"')
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo, hooks=hooks)

    manager.ensure("ADLC-1")
    manager.ensure("ADLC-1")  # reuse — must not re-fire after_create

    assert marker.read_text().count("x") == 1


def test_run_before_run_and_after_run_execute_configured_commands(
    tmp_path: Path, source_repo: Path
) -> None:
    before_marker = tmp_path / "before.txt"
    after_marker = tmp_path / "after.txt"
    hooks = WorkspaceHooks(
        before_run=f'touch "{before_marker}"', after_run=f'touch "{after_marker}"'
    )
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo, hooks=hooks)
    path = manager.ensure("ADLC-1")

    manager.run_before_run("ADLC-1", path)
    assert before_marker.exists()
    assert not after_marker.exists()

    manager.run_after_run("ADLC-1", path)
    assert after_marker.exists()


def test_hooks_are_no_ops_when_unconfigured(tmp_path: Path, source_repo: Path) -> None:
    manager = WorkspaceManager(
        tmp_path / "workspaces", source_repo=source_repo, hooks=WorkspaceHooks()
    )
    path = manager.ensure("ADLC-1")

    # None of these should raise or touch the filesystem.
    manager.run_before_run("ADLC-1", path)
    manager.run_after_run("ADLC-1", path)
    manager.run_before_remove("ADLC-1", path)


def test_hook_nonzero_exit_is_logged_not_raised(
    tmp_path: Path, source_repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    hooks = WorkspaceHooks(before_run="exit 1")
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo, hooks=hooks)
    path = manager.ensure("ADLC-1")

    with caplog.at_level(logging.WARNING, logger="jira_agent_cli.workspace"):
        manager.run_before_run("ADLC-1", path)  # must not raise

    assert "before_run" in caplog.text
    assert "ADLC-1" in caplog.text


def test_hook_timeout_is_logged_not_raised(
    tmp_path: Path, source_repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    hooks = WorkspaceHooks(before_run="sleep 5", timeout_ms=50)
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo, hooks=hooks)
    path = manager.ensure("ADLC-1")

    with caplog.at_level(logging.WARNING, logger="jira_agent_cli.workspace"):
        manager.run_before_run("ADLC-1", path)  # must not raise

    assert "timed out" in caplog.text


# --- Terminal-status removal (Symphony §8.6) --------------------------------


def test_remove_deletes_an_existing_worktree(tmp_path: Path, source_repo: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    path = manager.ensure("ADLC-1")
    assert path.exists()

    removed = manager.remove("ADLC-1")

    assert removed is True
    assert not path.exists()


def test_remove_returns_false_when_no_workspace_exists(tmp_path: Path, source_repo: Path) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    assert manager.remove("ADLC-999") is False


def test_remove_runs_before_remove_hook(tmp_path: Path, source_repo: Path) -> None:
    marker = tmp_path / "before-remove-marker.txt"
    hooks = WorkspaceHooks(before_remove=f'touch "{marker}"')
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo, hooks=hooks)
    manager.ensure("ADLC-1")

    manager.remove("ADLC-1")

    assert marker.exists()


def test_remove_fails_safe_on_a_dirty_worktree(
    tmp_path: Path, source_repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    manager = WorkspaceManager(tmp_path / "workspaces", source_repo=source_repo)
    path = manager.ensure("ADLC-1")
    (path / "untracked.txt").write_text("uncommitted work")

    with caplog.at_level(logging.WARNING, logger="jira_agent_cli.workspace"):
        removed = manager.remove("ADLC-1")

    # `git worktree remove` (no --force) refuses when there are untracked
    # files — cleanup must fail safe, not silently discard the work.
    assert removed is False
    assert path.exists()
