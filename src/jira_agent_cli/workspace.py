"""Per-issue workspace management — Symphony §9.5's safety invariants, adopted
as-is: every agent dispatch runs only inside a validated per-issue directory
under a single workspace root, never anywhere else on disk.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("jira_agent_cli.workspace")

_SANITIZE_RE = re.compile(r"[^A-Za-z0-9._-]")
_SLUG_RE = re.compile(r"[^a-z0-9]+")
MAX_SLUG_LENGTH = 60

# Symphony §9.4 lifecycle hook points -> the env var naming each one's shell
# command. This repo configures everything via `JIRA_AGENT_CLI_*` env vars
# rather than a WORKFLOW.md policy file (see docs/PORTABILITY.md); hooks
# follow that same convention instead of introducing a new config format.
HOOK_ENV_VARS: dict[str, str] = {
    "after_create": "JIRA_AGENT_CLI_HOOK_AFTER_CREATE",
    "before_run": "JIRA_AGENT_CLI_HOOK_BEFORE_RUN",
    "after_run": "JIRA_AGENT_CLI_HOOK_AFTER_RUN",
    "before_remove": "JIRA_AGENT_CLI_HOOK_BEFORE_REMOVE",
}
DEFAULT_HOOK_TIMEOUT_MS = 60000  # Symphony SPEC.md's own `hooks.timeout_ms` default.


class WorkspaceError(RuntimeError):
    """Raised when a workspace can't be created or fails a safety check."""


@dataclass(frozen=True)
class WorkspaceHooks:
    """One optional shell command per Symphony §9.4 lifecycle point. Each
    command (if set) runs with the workspace path and issue key exported as
    `JIRA_AGENT_CLI_WORKSPACE_PATH` / `JIRA_AGENT_CLI_ISSUE_KEY`, cwd'd into
    the workspace, under a timeout. A hook is best-effort: a non-zero exit,
    timeout, or launch failure is logged and swallowed, never raised — a
    broken hook command must not take down a Plan/Implement run.
    """

    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = DEFAULT_HOOK_TIMEOUT_MS

    @classmethod
    def from_env(cls) -> WorkspaceHooks:
        return cls(
            after_create=os.environ.get(HOOK_ENV_VARS["after_create"]) or None,
            before_run=os.environ.get(HOOK_ENV_VARS["before_run"]) or None,
            after_run=os.environ.get(HOOK_ENV_VARS["after_run"]) or None,
            before_remove=os.environ.get(HOOK_ENV_VARS["before_remove"]) or None,
            timeout_ms=int(
                os.environ.get("JIRA_AGENT_CLI_HOOK_TIMEOUT_MS", str(DEFAULT_HOOK_TIMEOUT_MS))
            ),
        )


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
    success), matching Symphony §9.1-9.2, unless terminal-status cleanup
    (§8.6, see `orchestrator.py`) removes one explicitly via `remove()`.
    """

    def __init__(
        self, root: Path, *, source_repo: Path, hooks: WorkspaceHooks | None = None
    ) -> None:
        self.root = root.resolve()
        self.source_repo = source_repo.resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.hooks = hooks if hooks is not None else WorkspaceHooks.from_env()

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
            self.run_after_create(issue_key, path)
        return path

    def remove(self, issue_key: str) -> bool:
        """Best-effort worktree removal (Symphony §8.6 terminal-status cleanup —
        see `orchestrator.cleanup_workspace_if_terminal`). Never forces: a
        worktree with uncommitted/untracked changes fails `git worktree remove`
        and is left alone (logged, not discarded) rather than silently losing
        work. Returns whether it was actually removed.
        """
        path = self.path_for(issue_key)
        if not path.exists():
            return False
        self._validate(path)
        self.run_before_remove(issue_key, path)
        try:
            result = subprocess.run(
                ["git", "worktree", "remove", str(path)],
                cwd=self.source_repo,
                capture_output=True,
                text=True,
            )
        except OSError:
            logger.warning("could not run git worktree remove for %s", issue_key, exc_info=True)
            return False
        if result.returncode != 0:
            logger.warning(
                "git worktree remove failed for %s (dirty worktree?): %s",
                issue_key,
                result.stderr.strip(),
            )
            return False
        logger.info("removed workspace for %s", issue_key)
        return True

    def run_after_create(self, issue_key: str, workspace_path: Path) -> None:
        self._run_hook(
            "after_create",
            self.hooks.after_create,
            issue_key=issue_key,
            workspace_path=workspace_path,
        )

    def run_before_run(self, issue_key: str, workspace_path: Path) -> None:
        self._run_hook(
            "before_run", self.hooks.before_run, issue_key=issue_key, workspace_path=workspace_path
        )

    def run_after_run(self, issue_key: str, workspace_path: Path) -> None:
        self._run_hook(
            "after_run", self.hooks.after_run, issue_key=issue_key, workspace_path=workspace_path
        )

    def run_before_remove(self, issue_key: str, workspace_path: Path) -> None:
        self._run_hook(
            "before_remove",
            self.hooks.before_remove,
            issue_key=issue_key,
            workspace_path=workspace_path,
        )

    def _run_hook(
        self, point: str, command: str | None, *, issue_key: str, workspace_path: Path
    ) -> None:
        if not command:
            return
        env = dict(os.environ)
        env["JIRA_AGENT_CLI_ISSUE_KEY"] = issue_key
        env["JIRA_AGENT_CLI_WORKSPACE_PATH"] = str(workspace_path)
        cwd = str(workspace_path) if workspace_path.exists() else None
        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=self.hooks.timeout_ms / 1000,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "workspace hook %r for %s timed out after %dms",
                point,
                issue_key,
                self.hooks.timeout_ms,
            )
            return
        except OSError:
            logger.warning(
                "workspace hook %r for %s failed to start", point, issue_key, exc_info=True
            )
            return
        if result.returncode != 0:
            logger.warning(
                "workspace hook %r for %s exited %d: %s",
                point,
                issue_key,
                result.returncode,
                result.stderr.strip(),
            )

    def _validate(self, path: Path) -> None:
        """Symphony Invariants 1/2: the workspace path must live inside the root."""
        resolved = path.resolve()
        if resolved == self.root or self.root not in resolved.parents:
            raise WorkspaceError(
                f"Workspace path {resolved} is not inside workspace root {self.root}"
            )
