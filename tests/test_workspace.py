"""Unit tests for WorkspaceManager — uses a real temp git repo (fast, no
mocking git) so the actual `git worktree add` behavior is exercised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jira_agent_cli.workspace import WorkspaceError, WorkspaceManager, sanitize_key


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
