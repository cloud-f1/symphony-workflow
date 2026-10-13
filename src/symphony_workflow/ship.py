"""The git half of a ticket's life: start (branch + worktree), pr, land (verified merge + cleanup).

Jira links a branch, its commits and its PR to a card because the ticket key is in the branch
name, so `start` makes that name and nothing else has to be configured. `land` closes the loop
by reading GitHub (state and merge commit, not an exit code), fast-forwarding main, deleting the
remote branch and the worktree, and moving the card to Done. git and gh run through one `Runner`
so tests use a real throwaway repository with a fake gh. ADLC-336.
"""

from __future__ import annotations

import json
import re
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from symphony_workflow.workspace import branch_name

Runner = Callable[[Sequence[str], Path], "subprocess.CompletedProcess[str]"]

KEY_RE = re.compile(r"^[A-Z][A-Z0-9]+-\d+$")
DONE_STATUSES = frozenset({"done", "closed", "resolved", "cancelled", "canceled"})
IN_PROGRESS = "In Progress"
IN_REVIEW = "In Review"
DONE = "Done"

COMMIT_TYPES = ("feat", "fix", "docs", "chore", "refactor", "test", "perf", "ci", "build", "revert")
# `type(scope)!: subject (KEY)` with an optional squash-merge suffix ` (#12)` on the end
_TYPES = "|".join(COMMIT_TYPES)
COMMIT_RE = re.compile(
    rf"^(?:{_TYPES})(?:\([\w./-]+\))?!?: .*\S \(([A-Z][A-Z0-9]+-\d+)\)(?: \(#\d+\))?$"
)
SKIPPED_SUBJECT_PREFIXES = ("Merge ", "Revert ", "fixup! ", "squash! ")


class ShipError(RuntimeError):
    """A step cannot run, or would be unsafe to run. The message says what to do."""


class Tracker(Protocol):
    def get_issue(self, key: str) -> Any: ...
    def transition_issue(self, key: str, transition_name: str) -> None: ...


def run_cmd(cmd: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            list(cmd), cwd=cwd, capture_output=True, text=True, timeout=120, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ShipError(f"could not run {cmd[0]}: {exc}") from exc


def _git(run: Runner, repo: Path, *args: str, ok: bool = False) -> str:
    result = run(["git", *args], repo)
    if result.returncode != 0 and not ok:
        raise ShipError(
            f"git {' '.join(args)} failed: {result.stderr.strip() or result.stdout.strip()}"
        )
    return result.stdout.strip() if result.returncode == 0 else ""


def main_checkout(cwd: Path, run: Runner = run_cmd) -> Path:
    """The main checkout's root even when `cwd` is inside one of its worktrees."""
    common = _git(run, cwd, "rev-parse", "--path-format=absolute", "--git-common-dir")
    path = Path(common)
    return path.parent if path.name == ".git" else path


def _check_key(key: str) -> None:
    if not KEY_RE.match(key):
        raise ShipError(f"{key!r} is not a ticket key like ADLC-12")


# --- commit convention -------------------------------------------------------------------------


def check_commit_subject(subject: str) -> list[str]:
    """Problems with a commit subject, empty when it is fine. Merge and revert commits pass."""
    subject = subject.strip()
    if subject.startswith(SKIPPED_SUBJECT_PREFIXES):
        return []
    if COMMIT_RE.match(subject):
        return []
    problems = []
    if not re.match(rf"^(?:{'|'.join(COMMIT_TYPES)})(?:\([\w./-]+\))?!?: ", subject):
        problems.append(
            f"start with `type(scope): ` where type is one of {', '.join(COMMIT_TYPES)}"
        )
    if not re.search(r"\([A-Z][A-Z0-9]+-\d+\)(?: \(#\d+\))?$", subject):
        problems.append("end with the ticket key in brackets, like `(ADLC-12)`")
    return problems or ["does not match `type(scope): subject (KEY)`"]


# --- start -------------------------------------------------------------------------------------


@dataclass
class StartResult:
    key: str
    branch: str
    path: Path
    base_sha: str
    reused: bool = False
    transitioned: bool = False
    notes: list[str] = field(default_factory=list)


def _worktrees(run: Runner, repo: Path) -> dict[str, Path]:
    """branch name -> worktree path, from `git worktree list --porcelain`."""
    out = _git(run, repo, "worktree", "list", "--porcelain")
    found: dict[str, Path] = {}
    path: Path | None = None
    for line in out.splitlines():
        if line.startswith("worktree "):
            path = Path(line[len("worktree ") :])
        elif line.startswith("branch refs/heads/") and path is not None:
            found[line[len("branch refs/heads/") :]] = path
    return found


def start(
    key: str,
    *,
    repo: Path,
    tracker: Tracker,
    worktrees_dir: Path | None = None,
    base: str = "origin/main",
    transition: bool = True,
    fetch: bool = True,
    run: Runner = run_cmd,
) -> StartResult:
    """Branch `{KEY}-{slug}` and a worktree for it, from a fresh `base`; card to In Progress."""
    _check_key(key)
    try:
        issue = tracker.get_issue(key)
    except Exception as exc:  # noqa: BLE001 - any tracker failure means we cannot start
        raise ShipError(f"cannot read {key}: {exc}") from exc
    if str(issue.status).strip().lower() in DONE_STATUSES:
        raise ShipError(f"{key} is already {issue.status}; reopen it or open a new ticket")
    branch = branch_name(key, issue.summary)
    if fetch:
        _git(run, repo, "fetch", "origin", "--prune")
    base_sha = _git(run, repo, "rev-parse", "--verify", f"{base}^{{commit}}")
    existing = _worktrees(run, repo)
    notes: list[str] = []
    reused = branch in existing
    if reused:
        path = existing[branch]
        notes.append(f"reused the worktree at {path}")
    else:
        path = (worktrees_dir or repo / ".worktrees") / branch
        has_branch = bool(_git(run, repo, "branch", "--list", branch))
        if has_branch:
            _git(run, repo, "worktree", "add", str(path), branch)
            notes.append(f"branch {branch} already existed; checked it out")
        else:
            _git(run, repo, "worktree", "add", "-b", branch, str(path), base)
    transitioned = False
    if transition and str(issue.status).strip().lower() != IN_PROGRESS.lower():
        try:
            tracker.transition_issue(key, IN_PROGRESS)
            transitioned = True
        except Exception as exc:  # noqa: BLE001 - the branch is made; report, do not undo it
            notes.append(f"could not move {key} to {IN_PROGRESS}: {exc}")
    return StartResult(key, branch, path, base_sha, reused, transitioned, notes)


# --- pr ----------------------------------------------------------------------------------------


@dataclass
class PrResult:
    key: str
    branch: str
    url: str
    title: str
    transitioned: bool = False
    notes: list[str] = field(default_factory=list)


def _branch_in(run: Runner, cwd: Path) -> str:
    return _git(run, cwd, "rev-parse", "--abbrev-ref", "HEAD")


def open_pr(
    key: str,
    *,
    cwd: Path,
    tracker: Tracker,
    title: str | None = None,
    body: str | None = None,
    issue_url: str | None = None,
    draft: bool = False,
    transition: bool = True,
    run: Runner = run_cmd,
) -> PrResult:
    """Push the branch and open a PR whose title and body carry the key; card to In Review."""
    _check_key(key)
    branch = _branch_in(run, cwd)
    if not (branch == key or branch.startswith(f"{key}-")):
        raise ShipError(f"branch {branch!r} does not start with {key}; Jira would not link the PR")
    title = title or _git(run, cwd, "log", "-1", "--format=%s")
    if check_commit_subject(title):
        raise ShipError(
            f"PR title {title!r} breaks the convention: {'; '.join(check_commit_subject(title))}"
        )
    text = body if body is not None else ""
    link = f"Jira: {issue_url}" if issue_url else f"Jira: {key}"
    if key not in text:
        text = f"{text}\n\n{link}".strip()
    _git(run, cwd, "push", "-u", "origin", branch)
    cmd = ["gh", "pr", "create", "--head", branch, "--title", title, "--body", text]
    if draft:
        cmd.append("--draft")
    result = run(cmd, cwd)
    if result.returncode != 0:
        raise ShipError(f"gh pr create failed: {result.stderr.strip()}")
    url = result.stdout.strip().splitlines()[-1] if result.stdout.strip() else ""
    notes: list[str] = []
    transitioned = False
    if transition:
        try:
            tracker.transition_issue(key, IN_REVIEW)
            transitioned = True
        except Exception as exc:  # noqa: BLE001 - the PR is open; report, do not fail
            notes.append(f"could not move {key} to {IN_REVIEW}: {exc}")
    return PrResult(key, branch, url, title, transitioned, notes)


# --- land --------------------------------------------------------------------------------------


@dataclass
class LandResult:
    key: str
    branch: str
    pr_number: int
    merge_commit: str
    steps: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    transitioned: bool = False


def _find_branch(run: Runner, repo: Path, key: str) -> str:
    candidates = [b for b in _worktrees(run, repo) if b == key or b.startswith(f"{key}-")]
    if not candidates:
        out = _git(run, repo, "branch", "--list", f"{key}-*", f"{key}", ok=True)
        candidates = [
            line.strip().lstrip("*+ ").strip() for line in out.splitlines() if line.strip()
        ]
    if not candidates:
        out = _git(run, repo, "ls-remote", "--heads", "origin", f"{key}-*", ok=True)
        candidates = [ln.split("refs/heads/")[-1] for ln in out.splitlines() if "refs/heads/" in ln]
    if len(candidates) != 1:
        found = ", ".join(candidates) or "none"
        raise ShipError(f"expected one branch for {key}, found {found}; pass --branch")
    return candidates[0]


def _merged_pr(run: Runner, repo: Path, branch: str, number: int | None) -> dict[str, Any]:
    if number is not None:
        cmd = ["gh", "pr", "view", str(number), "--json", "number,state,mergeCommit,headRefName"]
    else:
        cmd = [
            "gh",
            "pr",
            "list",
            "--head",
            branch,
            "--state",
            "all",
            "--json",
            "number,state,mergeCommit,headRefName",
        ]
    result = run(cmd, repo)
    if result.returncode != 0:
        raise ShipError(f"gh could not read the PR: {result.stderr.strip()}")
    try:
        data = json.loads(result.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise ShipError(f"gh returned something that is not JSON: {result.stdout[:80]!r}") from exc
    prs = [data] if isinstance(data, dict) else data
    if not prs:
        raise ShipError(f"no PR found for branch {branch}; open one with `pr` first")
    merged = [p for p in prs if p.get("state") == "MERGED"]
    if not merged:
        states = ", ".join(f"#{p.get('number')} {p.get('state')}" for p in prs)
        raise ShipError(f"the PR is not merged ({states}); nothing was changed")
    pr = max(merged, key=lambda p: p.get("number", 0))
    commit = (pr.get("mergeCommit") or {}).get("oid", "")
    if not commit:
        raise ShipError(f"PR #{pr.get('number')} is merged but GitHub reports no merge commit")
    return pr


def land(
    key: str,
    *,
    repo: Path,
    tracker: Tracker,
    branch: str | None = None,
    pr_number: int | None = None,
    transition: bool = True,
    keep_remote: bool = False,
    rollup_fn: Callable[[str], Any] | None = None,
    run: Runner = run_cmd,
) -> LandResult:
    """Close the loop after a PR is merged. Verifies first, changes nothing if it cannot."""
    _check_key(key)
    branch = branch or _find_branch(run, repo, key)
    pr = _merged_pr(run, repo, branch, pr_number)
    commit = pr["mergeCommit"]["oid"]
    result = LandResult(key, branch, int(pr["number"]), commit)
    _git(run, repo, "fetch", "origin", "--prune")
    if run(["git", "merge-base", "--is-ancestor", commit, "origin/main"], repo).returncode != 0:
        raise ShipError(f"merge commit {commit[:7]} is not on origin/main yet; fetch again later")
    result.steps.append(f"verified PR #{result.pr_number} merged as {commit[:7]} on origin/main")
    # bring the main checkout up to date, only when that cannot lose anything
    current = _branch_in(run, repo)
    dirty = bool(_git(run, repo, "status", "--porcelain", "--untracked-files=no"))
    if current == "main" and not dirty:
        _git(run, repo, "merge", "--ff-only", "origin/main")
        result.steps.append("fast-forwarded local main")
    else:
        result.kept.append(f"local main not updated (on {current!r}, dirty={dirty})")
    # remote branch
    if not keep_remote:
        on_remote = bool(_git(run, repo, "ls-remote", "--heads", "origin", branch, ok=True))
        if on_remote:
            _git(run, repo, "push", "origin", "--delete", branch)
            result.steps.append(f"deleted remote branch {branch}")
        else:
            result.steps.append(f"remote branch {branch} was already gone")
    # worktree and local branch: never force, a dirty worktree keeps its work
    worktree = _worktrees(run, repo).get(branch)
    removed_worktree = True
    if worktree is not None:
        dirty_wt = bool(_git(run, worktree, "status", "--porcelain", ok=True))
        if dirty_wt:
            removed_worktree = False
            result.kept.append(f"worktree {worktree} has uncommitted changes; left alone")
        else:
            _git(run, repo, "worktree", "remove", str(worktree))
            result.steps.append(f"removed worktree {worktree}")
    if removed_worktree and _git(run, repo, "branch", "--list", branch):
        _git(run, repo, "branch", "-D", branch)
        result.steps.append(f"deleted local branch {branch} (its PR is merged)")
    elif not removed_worktree:
        result.kept.append(f"local branch {branch} kept with its worktree")
    if transition:
        try:
            tracker.transition_issue(key, DONE)
            if rollup_fn is not None:
                rollup_fn(key)
            result.transitioned = True
            result.steps.append(f"moved {key} to {DONE}")
        except Exception as exc:  # noqa: BLE001 - git side is done; report the card separately
            result.kept.append(f"could not move {key} to {DONE}: {exc}")
    return result
