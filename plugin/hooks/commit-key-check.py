#!/usr/bin/env python3
"""PreToolUse hook: on a branch named for a ticket, a `git commit -m` subject must end with the
ticket key, so Jira keeps the commit on the card. Stays silent everywhere else.

Silent (exit 0) when: the command is not `git commit`, the message cannot be read (an editor
commit; use the commit-msg git hook for those), or the branch does not start with a ticket key.
Blocks (exit 2, message on stderr) when the subject breaks `type(scope): subject (KEY)`.
The pattern mirrors symphony_workflow.ship.COMMIT_RE; tests/test_ship_hook.py keeps them in step.
"""

import json
import re
import subprocess
import sys

TYPES = "feat|fix|docs|chore|refactor|test|perf|ci|build|revert"
GOOD = re.compile(rf"^(?:{TYPES})(?:\([\w./-]+\))?!?: .*\S \(([A-Z][A-Z0-9]+-\d+)\)(?: \(#\d+\))?$")
SKIP = ("Merge ", "Revert ", "fixup! ", "squash! ")
BRANCH_KEY = re.compile(r"^([A-Z][A-Z0-9]+-\d+)(?:-|$)")


def subject_of(command):
    heredoc = re.search(r"<<-?\s*['\"]?(\w+)['\"]?\n(.*?)\n", command, re.S)
    if heredoc:
        return heredoc.group(2).strip()
    quoted = re.search(r"""(?:-m|--message)[ =]+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""", command)
    if quoted:
        text = quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
        return text.strip().splitlines()[0] if text.strip() else ""
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    if not re.search(r"\bgit\b[^|;&]*\bcommit\b", command):
        return 0
    subject = subject_of(command)
    if not subject or subject.startswith(SKIP):
        return 0
    cwd = event.get("cwd") or "."
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=10, check=False,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return 0
    match = BRANCH_KEY.match(branch)
    if not match or GOOD.match(subject):
        return 0
    sys.stderr.write(
        f"Commit subject {subject!r} on branch {branch}: end it with the ticket key so Jira links "
        f"the commit, like `type(scope): subject ({match.group(1)})`. "
        f"Types: {TYPES.replace('|', ', ')}.\n"
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
