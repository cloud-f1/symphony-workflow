"""Unit tests for WorkspaceManager — uses a real temp git repo (fast, no
mocking git) so the actual `git worktree add` behavior is exercised.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from jira_agent_cli.workspace import (
    WorkspaceError,
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
