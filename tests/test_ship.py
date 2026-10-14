"""start / pr / land / commit check, on real throwaway git repos with a fake gh (ADLC-336)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from symphony_workflow.ship import (
    ShipError,
    check_commit_subject,
    land,
    main_checkout,
    open_pr,
    run_cmd,
    start,
)

_ENV = {
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@example.invalid",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@example.invalid",
}


def git(cwd: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env={**os.environ, **_ENV},
    )
    return done.stdout.strip()


def commit(cwd: Path, name: str, message: str = "chore: x (ADLC-1)") -> str:
    (cwd / name).write_text(name)
    git(cwd, "add", name)
    git(cwd, "commit", "-q", "-m", message)
    return git(cwd, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _isolated_git(monkeypatch: pytest.MonkeyPatch) -> None:
    for key, value in _ENV.items():
        monkeypatch.setenv(key, value)


@pytest.fixture
def repos(tmp_path: Path) -> SimpleNamespace:
    origin = tmp_path / "origin.git"
    git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    work = tmp_path / "work"
    git(tmp_path, "clone", "-q", str(origin), str(work))
    git(work, "checkout", "-q", "-b", "main")
    commit(work, "a.txt", "chore: seed (ADLC-1)")
    git(work, "push", "-q", "-u", "origin", "main")
    return SimpleNamespace(origin=origin, work=work, tmp=tmp_path)


class FakeTracker:
    def __init__(self, status: str = "To Do", summary: str = "Add the start command") -> None:
        self.issue = SimpleNamespace(key="ADLC-9", status=status, summary=summary)
        self.moves: list[tuple[str, str]] = []

    def get_issue(self, key: str) -> SimpleNamespace:
        if key != "ADLC-9":
            raise RuntimeError("not found")
        return self.issue

    def transition_issue(self, key: str, name: str) -> None:
        self.moves.append((key, name))
        self.issue.status = name


class FakeGh:
    """Real git, scripted gh. `prs` is what `gh pr list/view` returns; creates are recorded."""

    def __init__(self, prs: list[dict] | None = None, create_url: str = "https://x/pull/1") -> None:
        self.prs = prs or []
        self.create_url = create_url
        self.gh_calls: list[list[str]] = []

    def __call__(self, cmd, cwd):
        if cmd[0] != "gh":
            return run_cmd(cmd, cwd)
        self.gh_calls.append(list(cmd))
        if cmd[1:3] == ["pr", "create"]:
            return subprocess.CompletedProcess(cmd, 0, self.create_url + "\n", "")
        if cmd[1:3] in (["pr", "list"], ["pr", "view"]):
            payload = self.prs[0] if cmd[2] == "view" and self.prs else self.prs
            return subprocess.CompletedProcess(cmd, 0, json.dumps(payload), "")
        return subprocess.CompletedProcess(cmd, 1, "", "unexpected gh call")


# --- commit convention -------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject",
    [
        "feat(ship): add start (ADLC-337)",
        "fix: stop the loop (ADLC-9)",
        "docs(readme)!: rewrite (ADLC-262) (#52)",
        "Merge pull request #5 from x/y",
        'Revert "feat: x (ADLC-1)"',
    ],
)
def test_good_subjects_pass(subject: str) -> None:
    assert check_commit_subject(subject) == []


def test_subject_without_key_or_type_says_what_to_fix() -> None:
    problems = check_commit_subject("added a thing")
    assert any("type(scope)" in p for p in problems)
    assert any("ticket key" in p for p in problems)


def test_subject_missing_only_the_key() -> None:
    assert check_commit_subject("fix: stop the loop") == [
        "end with the ticket key in brackets, like `(ADLC-12)`"
    ]


# --- start -------------------------------------------------------------------------------------


def test_start_branches_from_origin_main_not_local_head(repos) -> None:
    commit(repos.work, "local-only.txt")  # local main is now ahead of origin/main
    origin_main = git(repos.work, "rev-parse", "origin/main")
    tracker = FakeTracker()
    result = start("ADLC-9", repo=repos.work, tracker=tracker)
    assert result.branch == "ADLC-9-add-the-start-command"
    assert result.path == repos.work / ".worktrees" / result.branch
    assert git(result.path, "rev-parse", "HEAD") == origin_main
    assert not (result.path / "local-only.txt").exists()
    assert tracker.moves == [("ADLC-9", "In Progress")]
    assert result.transitioned


def test_start_twice_reuses_the_worktree(repos) -> None:
    tracker = FakeTracker()
    first = start("ADLC-9", repo=repos.work, tracker=tracker)
    second = start("ADLC-9", repo=repos.work, tracker=tracker)
    assert second.reused and second.path == first.path
    assert tracker.moves == [("ADLC-9", "In Progress")]  # the second start finds it In Progress


def test_start_refuses_a_done_ticket_and_creates_nothing(repos) -> None:
    with pytest.raises(ShipError, match="already Done"):
        start("ADLC-9", repo=repos.work, tracker=FakeTracker(status="Done"))
    assert not (repos.work / ".worktrees").exists()


def test_start_refuses_a_missing_ticket_and_a_bad_key(repos) -> None:
    with pytest.raises(ShipError, match="cannot read"):
        start("ADLC-404", repo=repos.work, tracker=FakeTracker())
    with pytest.raises(ShipError, match="not a ticket key"):
        start("nope", repo=repos.work, tracker=FakeTracker())


def test_start_no_transition_leaves_the_card(repos) -> None:
    tracker = FakeTracker()
    result = start("ADLC-9", repo=repos.work, tracker=tracker, transition=False)
    assert tracker.moves == [] and not result.transitioned


def test_start_reports_but_survives_a_failed_transition(repos) -> None:
    class Failing(FakeTracker):
        def transition_issue(self, key: str, name: str) -> None:
            raise RuntimeError("no such transition")

    result = start("ADLC-9", repo=repos.work, tracker=Failing())
    assert result.path.exists() and any("could not move" in n for n in result.notes)


def test_main_checkout_from_inside_a_worktree(repos) -> None:
    result = start("ADLC-9", repo=repos.work, tracker=FakeTracker())
    assert main_checkout(result.path).resolve() == repos.work.resolve()


# --- pr ----------------------------------------------------------------------------------------


def _worktree_with_commit(repos, tracker: FakeTracker) -> Path:
    result = start("ADLC-9", repo=repos.work, tracker=tracker)
    commit(result.path, "feature.txt", "feat(ship): add start (ADLC-9)")
    return result.path


def test_pr_title_and_body_carry_the_key_and_card_moves_to_review(repos) -> None:
    tracker = FakeTracker()
    path = _worktree_with_commit(repos, tracker)
    gh = FakeGh()
    result = open_pr(
        "ADLC-9", cwd=path, tracker=tracker, issue_url="https://jira.example/browse/ADLC-9", run=gh
    )
    create = next(c for c in gh.gh_calls if c[1:3] == ["pr", "create"])
    assert create[create.index("--title") + 1] == "feat(ship): add start (ADLC-9)"
    assert "ADLC-9" in create[create.index("--body") + 1]
    assert result.url == "https://x/pull/1"
    assert tracker.moves[-1] == ("ADLC-9", "In Review")
    assert "ADLC-9-add-the-start-command" in git(repos.origin, "branch", "--list")


def test_pr_refuses_a_branch_that_would_not_link(repos) -> None:
    git(repos.work, "checkout", "-q", "-b", "random-branch")
    with pytest.raises(ShipError, match="does not start with ADLC-9"):
        open_pr("ADLC-9", cwd=repos.work, tracker=FakeTracker(), run=FakeGh())


def test_pr_refuses_a_title_without_the_key(repos) -> None:
    tracker = FakeTracker()
    path = _worktree_with_commit(repos, tracker)
    with pytest.raises(ShipError, match="breaks the convention"):
        open_pr("ADLC-9", cwd=path, tracker=tracker, title="add the thing", run=FakeGh())


# --- land --------------------------------------------------------------------------------------


def _merged(repos, tracker: FakeTracker) -> tuple[Path, str, FakeGh]:
    """A pushed branch whose PR 'merged' as a squash commit on origin/main."""
    path = _worktree_with_commit(repos, tracker)
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    git(path, "push", "-q", "origin", branch)
    squash = commit(repos.work, "squash.txt", "feat(ship): add start (ADLC-9) (#1)")
    git(repos.work, "push", "-q", "origin", "main")
    git(
        repos.work, "reset", "-q", "--hard", "HEAD~1"
    )  # local main is behind origin, as after a merge
    gh = FakeGh(
        prs=[
            {"number": 1, "state": "MERGED", "mergeCommit": {"oid": squash}, "headRefName": branch}
        ]
    )
    return path, branch, gh


def test_land_cleans_everything_and_closes_the_card(repos) -> None:
    tracker = FakeTracker()
    path, branch, gh = _merged(repos, tracker)
    rolled: list[str] = []
    result = land("ADLC-9", repo=repos.work, tracker=tracker, run=gh, rollup_fn=rolled.append)
    assert result.pr_number == 1 and result.kept == []
    assert not path.exists()
    assert git(repos.work, "branch", "--list", branch) == ""
    assert git(repos.origin, "branch", "--list", branch) == ""
    assert (repos.work / "squash.txt").exists()  # main was fast-forwarded
    assert tracker.moves[-1] == ("ADLC-9", "Done") and rolled == ["ADLC-9"]


def test_land_refuses_an_open_pr_and_changes_nothing(repos) -> None:
    tracker = FakeTracker()
    path = _worktree_with_commit(repos, tracker)
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    git(path, "push", "-q", "origin", branch)
    gh = FakeGh(prs=[{"number": 1, "state": "OPEN", "mergeCommit": None, "headRefName": branch}])
    with pytest.raises(ShipError, match="not merged"):
        land("ADLC-9", repo=repos.work, tracker=tracker, run=gh)
    assert path.exists() and git(repos.origin, "branch", "--list", branch)
    assert ("ADLC-9", "Done") not in tracker.moves


def test_land_refuses_when_there_is_no_pr(repos) -> None:
    tracker = FakeTracker()
    _worktree_with_commit(repos, tracker)
    with pytest.raises(ShipError, match="no PR found"):
        land("ADLC-9", repo=repos.work, tracker=tracker, run=FakeGh(prs=[]))


def test_land_refuses_a_merge_commit_not_on_origin_main(repos) -> None:
    tracker = FakeTracker()
    path = _worktree_with_commit(repos, tracker)
    branch = git(path, "rev-parse", "--abbrev-ref", "HEAD")
    unpushed = git(path, "rev-parse", "HEAD")  # on the branch only, never reached main
    gh = FakeGh(
        prs=[
            {
                "number": 1,
                "state": "MERGED",
                "mergeCommit": {"oid": unpushed},
                "headRefName": branch,
            }
        ]
    )
    with pytest.raises(ShipError, match="not on origin/main"):
        land("ADLC-9", repo=repos.work, tracker=tracker, run=gh)
    assert path.exists()


def test_land_keeps_a_dirty_worktree_and_its_branch(repos) -> None:
    tracker = FakeTracker()
    path, branch, gh = _merged(repos, tracker)
    (path / "unsaved.txt").write_text("work in progress")
    result = land("ADLC-9", repo=repos.work, tracker=tracker, run=gh)
    assert path.exists() and (path / "unsaved.txt").exists()
    assert git(repos.work, "branch", "--list", branch)
    assert any("uncommitted" in k for k in result.kept)
    assert (
        git(repos.origin, "branch", "--list", branch) == ""
    )  # the merged remote branch still goes


def test_land_when_the_remote_branch_is_already_gone(repos) -> None:
    tracker = FakeTracker()
    path, branch, gh = _merged(repos, tracker)
    git(repos.work, "push", "-q", "origin", "--delete", branch)  # GitHub's auto-delete
    result = land("ADLC-9", repo=repos.work, tracker=tracker, run=gh)
    assert any("already gone" in s for s in result.steps) and not path.exists()


def test_land_no_transition_and_keep_remote(repos) -> None:
    tracker = FakeTracker()
    path, branch, gh = _merged(repos, tracker)
    land("ADLC-9", repo=repos.work, tracker=tracker, run=gh, transition=False, keep_remote=True)
    assert ("ADLC-9", "Done") not in tracker.moves
    assert git(repos.origin, "branch", "--list", branch)


def test_land_reports_a_failed_card_move_without_undoing_the_git_work(repos) -> None:
    class Failing(FakeTracker):
        def transition_issue(self, key: str, name: str) -> None:
            if name == "Done":
                raise RuntimeError("no Done transition")

    tracker = Failing()
    path, branch, gh = _merged(repos, tracker)
    result = land("ADLC-9", repo=repos.work, tracker=tracker, run=gh)
    assert not path.exists() and any("could not move" in k for k in result.kept)
