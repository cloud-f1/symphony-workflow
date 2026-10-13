"""The plugin commit-key hook, run as Claude Code runs it: JSON in, exit code out (ADLC-336)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from symphony_workflow.ship import check_commit_subject
from tests.test_ship import _isolated_git, git, repos  # noqa: F401

HOOK = Path(__file__).resolve().parent.parent / "plugin/hooks/commit-key-check.py"


def run_hook(command: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    event = {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": str(cwd)}
    return subprocess.run(
        [sys.executable, "-I", str(HOOK)], input=json.dumps(event), capture_output=True, text=True
    )


def on_branch(repos, name: str) -> Path:  # noqa: F811
    git(repos.work, "checkout", "-q", "-b", name)
    return repos.work


def test_blocks_a_subject_without_the_key_on_a_ticket_branch(repos) -> None:  # noqa: F811
    cwd = on_branch(repos, "ADLC-9-add-thing")
    done = run_hook('git commit -m "feat: add thing"', cwd)
    assert done.returncode == 2 and "ADLC-9" in done.stderr


def test_allows_a_correct_subject(repos) -> None:  # noqa: F811
    cwd = on_branch(repos, "ADLC-9-add-thing")
    assert run_hook('git commit -m "feat(x): add thing (ADLC-9)"', cwd).returncode == 0


def test_reads_a_heredoc_message(repos) -> None:  # noqa: F811
    cwd = on_branch(repos, "ADLC-9-add-thing")
    cmd = "git commit -m \"$(cat <<'EOF'\nfeat: add thing\n\nbody\nEOF\n)\""
    assert run_hook(cmd, cwd).returncode == 2
    ok = "git commit -m \"$(cat <<'EOF'\nfeat: add thing (ADLC-9)\n\nbody\nEOF\n)\""
    assert run_hook(ok, cwd).returncode == 0


def test_silent_on_a_branch_that_is_not_a_ticket(repos) -> None:  # noqa: F811
    assert run_hook('git commit -m "whatever"', repos.work).returncode == 0  # main


def test_silent_for_other_commands_and_unreadable_messages(repos) -> None:  # noqa: F811
    cwd = on_branch(repos, "ADLC-9-add-thing")
    assert run_hook("git status", cwd).returncode == 0
    assert run_hook("git commit", cwd).returncode == 0  # an editor commit: the git hook's job
    assert run_hook("echo hi", cwd).returncode == 0


def test_silent_for_merge_and_revert_subjects(repos) -> None:  # noqa: F811
    cwd = on_branch(repos, "ADLC-9-add-thing")
    assert run_hook('git commit -m "Merge branch x"', cwd).returncode == 0


def test_garbage_input_does_not_block() -> None:
    done = subprocess.run(
        [sys.executable, "-I", str(HOOK)], input="not json", capture_output=True, text=True
    )
    assert done.returncode == 0


@pytest.mark.parametrize(
    "subject",
    [
        "feat(ship): add start (ADLC-337)",
        "fix: x (ADLC-9)",
        "docs!: y (ADLC-1) (#5)",
        "feat: no key",
        "added a thing",
        "feat(x) missing colon (ADLC-1)",
        "chore: trailing space  (ADLC-1)",
    ],
)
def test_hook_and_cli_agree(subject: str, repos) -> None:  # noqa: F811
    cwd = on_branch(repos, "ADLC-9-add-thing")
    blocked = run_hook(f"git commit -m '{subject}'", cwd).returncode == 2
    assert blocked == bool(check_commit_subject(subject))
