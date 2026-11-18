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


HEREDOC_OPEN = re.compile(r"<<(-?)\s*['\"]?(\w+)['\"]?")
# A real `git commit` starts a command: line start, after ; & | ( or $( , optionally behind
# VAR=value assignments and `git -C dir` / `git -c k=v`. Text inside quotes or a heredoc body never
# matches, so a Python string or a note that merely mentions "git commit" is left alone.
GIT_COMMIT = re.compile(r"(?:^|[;&|(\n]|\$\()\s*(?:\w+=\S*\s+)*git(?:\s+-[Cc]\s+\S+)*\s+commit\b")
# The -m flag must sit in the same command segment; its quoted value may span lines.
MESSAGE = re.compile(r"""[^;&|\n]*?(?:-m|--message)[ =]+(?:"((?:[^"\\]|\\.)*)"|'([^']*)')""")
FILE_STDIN = re.compile(r"[^;&|\n]*?(?:-F|--file)[ =]+-(?:\s|$)[^;&|\n]*?__HEREDOC_(\d+)__")
PLACEHOLDER = re.compile(r"__HEREDOC_(\d+)__")


def strip_heredocs(command):
    """Replace every heredoc body (and its end line) with a placeholder; return (text, bodies)."""
    bodies, out, pos = [], [], 0
    while True:
        m = HEREDOC_OPEN.search(command, pos)
        line_end = command.find("\n", m.end()) if m else -1
        if not m or line_end == -1:
            out.append(command[pos:])
            return "".join(out), bodies
        dash, tag = m.group(1), m.group(2)
        body, cursor = [], line_end + 1
        while cursor <= len(command):
            nl = command.find("\n", cursor)
            ln = command[cursor:] if nl == -1 else command[cursor:nl]
            cursor = len(command) + 1 if nl == -1 else nl + 1
            if (ln.strip() if dash else ln) == tag:
                break
            body.append(ln)
        out.append(
            command[pos : m.start()] + f"__HEREDOC_{len(bodies)}__" + command[m.end() : line_end]
        )
        bodies.append("\n".join(body))
        pos = min(cursor - 1, len(command))  # keep the newline after the end tag as a separator


def first_line(text):
    text = text.strip()
    return text.splitlines()[0].strip() if text else ""


def subject_of(command):
    """Subject of the real `git commit` in the command, or None when it cannot be read."""
    text, bodies = strip_heredocs(command)
    commit = GIT_COMMIT.search(text)
    if not commit:
        return None
    after = text[commit.end() :]
    quoted = MESSAGE.match(after)
    if quoted:
        value = quoted.group(1) if quoted.group(1) is not None else quoted.group(2)
        hd = PLACEHOLDER.search(value)  # -m "$(cat <<'EOF' ... EOF)"
        return first_line(bodies[int(hd.group(1))]) if hd else first_line(value)
    stdin = FILE_STDIN.match(after)  # git commit -F - <<'EOF' ... EOF
    if stdin:
        return first_line(bodies[int(stdin.group(1))])
    return None


def main():
    try:
        event = json.load(sys.stdin)
    except ValueError:
        return 0
    command = (event.get("tool_input") or {}).get("command", "")
    subject = subject_of(command)
    if not subject or subject.startswith(SKIP):
        return 0
    cwd = event.get("cwd") or "."
    try:
        branch = subprocess.run(
            ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
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
