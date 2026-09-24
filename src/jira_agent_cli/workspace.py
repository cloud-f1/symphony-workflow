"""Per-issue workspace management — Symphony §9.5's safety invariants, adopted
as-is: every agent dispatch runs only inside a validated per-issue directory
under a single workspace root, never anywhere else on disk.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
MAX_SLUG_LENGTH = 60


class WorkspaceError(RuntimeError):
    """Raised when a workspace can't be created or fails a safety check."""


def sanitize_key(issue_key: str) -> str:
    """Symphony Invariant 3: only [A-Za-z0-9._-] in workspace directory names."""
    return _SANITIZE_RE.sub("_", issue_key)


def slugify(text: str, *, max_length: int = MAX_SLUG_LENGTH) -> str:
    """Lowercase, non-alnum -> `-`, collapsed and trimmed. Used to build branch
    names of the form `{ISSUE-KEY}-{slug}` — see CLAUDE.md's Branch naming rule
    for why the branch must *start with* the key (Jira's Development-panel
    auto-linking depends on it, confirmed missing on ADLC-17 before this fix).
    """
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:max_length].rstrip("-")


def branch_name(issue_key: str, summary: str | None = None) -> str:
    key = sanitize_key(issue_key)
    if not summary:
        return key
    slug = slugify(summary)
    return f"{key}-{slug}" if slug else key


class WorkspaceManager:
    """Creates and validates git worktrees under one root — one per issue key.

    Workspaces persist across runs for the same issue (not auto-deleted on
    success), matching Symphony §9.1-9.2. This POC does not implement
    `after_create`/`before_run` hooks (§9.4) — out of scope, see docs/plan.md.
    """

    def __init__(self, root: Path, *, source_repo: Path) -> None:
        self.root = root.resolve()
        self.source_repo = source_repo.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def path_for(self, issue_key: str) -> Path:
        return self.root / sanitize_key(issue_key)

    def ensure(self, issue_key: str, *, branch_slug: str | None = None) -> Path:
        """Create (or reuse) the worktree for `issue_key`. Returns its validated
        path. `branch_slug` (typically the issue summary) becomes part of the
        branch name so Jira auto-links it — see `branch_name()`.
        """
        path = self.path_for(issue_key)
        self._validate(path)
        if not path.exists():
            branch = branch_name(issue_key, branch_slug)
            try:
                result = subprocess.run(
                    ["git", "worktree", "add", "-b", branch, str(path), "HEAD"],
                    cwd=self.source_repo,
                    capture_output=True,
                    text=True,
                )
            except OSError as exc:
                raise WorkspaceError(
                    f"could not run git for {issue_key!r} (source repo {self.source_repo}): {exc}"
                ) from exc
            if result.returncode != 0:
                raise WorkspaceError(
                    f"git worktree add failed for {issue_key!r}: {result.stderr.strip()}"
                )
        return path

    def _validate(self, path: Path) -> None:
        """Symphony Invariants 1/2: the workspace path must live inside the root."""
        resolved = path.resolve()
        if resolved == self.root or self.root not in resolved.parents:
            raise WorkspaceError(
                f"Workspace path {resolved} is not inside workspace root {self.root}"
            )
