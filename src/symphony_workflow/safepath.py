"""Path and git safety for code that inspects a project directory it does not control (ADLC-270).

A project directory can be someone else's: a cloned repository, a path an agent passes to a tool.
Three rules:

* `check_inside` / `is_safe`: a plan file is read or written only when no part of its path below
  the project root is a symlink and it resolves inside the root. Otherwise
  `docs/epics -> /somewhere/else` turns a sync into a write outside the project, and a symlinked
  epic file turns an import into a read of any file.
* `scoped_path`: the agent-facing tools only accept a path inside the current working directory.
* `run_git`: git runs with the settings a repository could use to execute commands switched off.
  A repo's own `.git/config` can set `core.fsmonitor` to a command that git runs while it reads
  the index (`git check-ignore` does), and `core.hooksPath` / `core.pager` name more.
  Command-line `-c` settings win over the repository's.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class UnsafePathError(ValueError):
    """A path leads outside the project, or through a symlink."""


def is_safe(root: Path, path: Path) -> bool:
    """True when `path` (built as `root / ...`) has no symlink below `root` and resolves inside."""
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            return False
    return path.resolve().is_relative_to(root.resolve())


def check_inside(root: Path, path: Path) -> None:
    if not is_safe(root, path):
        raise UnsafePathError(
            f"{path} is a symlink or leads outside the project {root}; refusing it"
        )


def scoped_path(raw: str) -> Path:
    """`raw` resolved, and required to be the working directory or inside it (symlinks followed)."""
    resolved = Path(raw).resolve()
    if not resolved.is_relative_to(Path.cwd().resolve()):
        raise ValueError(f"path must be inside the working directory: {raw}")
    return resolved


_GIT_HARDENING = (
    "--no-pager",
    "-c", "core.fsmonitor=false",
    "-c", "core.hooksPath=/dev/null",
    "-c", "core.pager=cat",
    "-c", "core.untrackedCache=false",
)  # fmt: skip


def run_git(root: Path, *args: str, timeout: float = 15) -> subprocess.CompletedProcess[str] | None:
    """`git -C root <args>` with command-running repo settings disabled; None if git cannot run."""
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    try:
        return subprocess.run(
            ["git", *_GIT_HARDENING, "-C", str(root), *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
