"""CLI wiring for the git loop: exit codes and output (ADLC-336). Logic is in test_ship.py."""

from __future__ import annotations

from typer.testing import CliRunner

from symphony_workflow import cli
from symphony_workflow.cli import app
from tests.test_ship import FakeTracker, _isolated_git, commit, git, repos  # noqa: F401

runner = CliRunner()


def test_check_commit_ok_and_not_ok() -> None:
    assert runner.invoke(app, ["check-commit", "fix: stop it (ADLC-9)"]).exit_code == 0
    bad = runner.invoke(app, ["check-commit", "stop it"])
    assert bad.exit_code == 1 and "ticket key" in bad.output


def test_check_commit_reads_a_message_file_and_skips_comments(tmp_path) -> None:
    good = tmp_path / "MSG"
    good.write_text("# Please enter a message\nfeat(x): add y (ADLC-9)\n\nbody\n")
    assert runner.invoke(app, ["check-commit", str(good)]).exit_code == 0
    bad = tmp_path / "MSG2"
    bad.write_text("# comment\nadd y\n")
    assert runner.invoke(app, ["check-commit", str(bad)]).exit_code == 1


def test_changelog_draft_prints_a_section(repos, monkeypatch) -> None:  # noqa: F811
    monkeypatch.chdir(repos.work)
    commit(repos.work, "n", "feat(ship): add start (ADLC-337)")
    result = runner.invoke(app, ["changelog-draft", "0.2.0"])
    assert result.exit_code == 0 and "## 0.2.0" in result.output and "add start" in result.output
    assert runner.invoke(app, ["changelog-draft", "v0.2"]).exit_code == 1


class _Ctx:
    def __init__(self, tracker: FakeTracker) -> None:
        self.tracker = tracker

    def __enter__(self) -> FakeTracker:
        return self.tracker

    def __exit__(self, *exc: object) -> None:
        return None


def test_start_command_prints_branch_and_worktree(repos, monkeypatch) -> None:  # noqa: F811
    monkeypatch.chdir(repos.work)
    tracker = FakeTracker()
    monkeypatch.setattr(cli, "_client", lambda: _Ctx(tracker))
    result = runner.invoke(app, ["start", "ADLC-9"])
    assert result.exit_code == 0, result.output
    assert "branch ADLC-9-add-the-start-command" in result.output and "In Progress" in result.output


def test_start_command_exits_1_for_a_done_ticket(repos, monkeypatch) -> None:  # noqa: F811
    monkeypatch.chdir(repos.work)
    monkeypatch.setattr(cli, "_client", lambda: _Ctx(FakeTracker(status="Done")))
    result = runner.invoke(app, ["start", "ADLC-9"])
    assert result.exit_code == 1 and "already Done" in result.output
