"""Per-project Jira credential setup: create, check and verify a project's `.env` (ADLC-189).

A project that links to Jira keeps its own credentials in its own root, gitignored. This module
backs `symphony-workflow init-env` / `check-env`. It never returns, logs or prints a value from the
file: reports name keys only, so the output is safe to paste or to hand to an agent.

Why not `JiraConfig.from_env()`: `check-env` must judge a file without exporting it into the
process, and say *which* key is missing rather than stop at the first one.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.config import JiraConfig

REQUIRED_KEYS = ("JIRA_BASE_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")
VERIFY_TIMEOUT_SECONDS = 10.0

TEMPLATE = """\
# Jira credentials for this project. Gitignored: never commit a real token.
# Create a token at https://id.atlassian.com/manage-profile/security/api-tokens
JIRA_BASE_URL=
JIRA_EMAIL=
JIRA_API_TOKEN=
"""


class EnvExistsError(FileExistsError):
    """`init-env` refuses to overwrite a file that is already there."""


def parse_env(text: str) -> dict[str, str]:
    """`KEY=value` lines (an optional `export ` prefix, `#` comments, one pair of quotes)."""
    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _is_gitignored(path: Path) -> bool | None:
    """True / False from `git check-ignore`; None when this is not a git repo (or git is absent)."""
    try:
        proc = subprocess.run(
            ["git", "-C", str(path.resolve().parent), "check-ignore", "-q", path.name],
            capture_output=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(proc.returncode)


@dataclass
class EnvReport:
    path: str
    exists: bool
    present: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)  # absent or empty
    gitignored: bool | None = None  # None: not a git repo

    @property
    def ok(self) -> bool:
        return self.exists and not self.missing and self.gitignored is not False

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "exists": self.exists,
            "ok": self.ok,
            "present": self.present,
            "missing": self.missing,
            "gitignored": self.gitignored,
        }


def check_env(path: Path) -> EnvReport:
    """Which required keys are set, and whether git ignores the file. Values are never read out."""
    if not path.is_file():
        return EnvReport(str(path), False, missing=list(REQUIRED_KEYS))
    values = parse_env(path.read_text(encoding="utf-8"))
    present = [k for k in REQUIRED_KEYS if values.get(k)]
    missing = [k for k in REQUIRED_KEYS if not values.get(k)]
    return EnvReport(str(path), True, present, missing, _is_gitignored(path))


@dataclass
class InitResult:
    path: str
    added_to_gitignore: bool


def init_env(path: Path) -> InitResult:
    """Create `path` from the template (owner-only permissions) and make sure git ignores it.
    Raises `EnvExistsError` instead of overwriting."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise EnvExistsError(str(path)) from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(TEMPLATE)
    added = False
    if _is_gitignored(path) is False:
        ignore = path.resolve().parent / ".gitignore"
        existing = ignore.read_text(encoding="utf-8") if ignore.exists() else ""
        prefix = "" if not existing or existing.endswith("\n") else "\n"
        ignore.write_text(f"{existing}{prefix}{path.name}\n", encoding="utf-8")
        added = True
    return InitResult(str(path), added)


@dataclass
class VerifyResult:
    ok: bool
    outcome: str  # ok | incomplete | unauthorized | jira-error | unreachable
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "outcome": self.outcome, "detail": self.detail}


def verify_env(path: Path) -> VerifyResult:
    """One read-only call (`/myself`) with the file's credentials. Tells a bad URL, a bad
    token and a working link apart, so the user knows which line to fix."""
    report = check_env(path)
    if report.missing:
        return VerifyResult(False, "incomplete", f"missing or empty: {', '.join(report.missing)}")
    values = parse_env(path.read_text(encoding="utf-8"))
    config = JiraConfig(
        base_url=values["JIRA_BASE_URL"].rstrip("/"),
        email=values["JIRA_EMAIL"],
        api_token=values["JIRA_API_TOKEN"],
    )
    try:
        with JiraClient(config) as client:
            user = client.get_myself(timeout=VERIFY_TIMEOUT_SECONDS)
    except JiraError as exc:
        if exc.status_code in (401, 403):
            detail = f"HTTP {exc.status_code}: check JIRA_EMAIL and JIRA_API_TOKEN"
            return VerifyResult(False, "unauthorized", detail)
        return VerifyResult(False, "jira-error", f"HTTP {exc.status_code}: check JIRA_BASE_URL")
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return VerifyResult(
            False, "unreachable", f"{type(exc).__name__}: check JIRA_BASE_URL and the network"
        )
    return VerifyResult(True, "ok", f"authenticated as {user.display_name}")
