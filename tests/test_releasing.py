"""CHANGELOG draft, release notes, publish and backfill (ADLC-336)."""

from __future__ import annotations

import subprocess
from datetime import date
from pathlib import Path

import pytest

from symphony_workflow.releasing import (
    backfill,
    draft_changelog,
    extract_notes,
    missing_releases,
    publish,
)
from symphony_workflow.ship import ShipError, run_cmd
from tests.test_ship import _isolated_git, commit, git, repos  # noqa: F401  (fixtures)

CHANGELOG = """# Changelog

## Unreleased

## 0.2.0 — 2026-10-14

### Features

- the new thing (ADLC-2)

## 0.1.0 — 2026-10-01

first
"""


class ReleaseGh:
    """Real git; gh answers from `releases` and records creates."""

    def __init__(self, releases: list[str] | None = None) -> None:
        self.releases = list(releases or [])
        self.created: list[list[str]] = []

    def __call__(self, cmd, cwd):
        if cmd[0] != "gh":
            return run_cmd(cmd, cwd)
        if cmd[1:3] == ["release", "view"]:
            return subprocess.CompletedProcess(cmd, 0 if cmd[3] in self.releases else 1, "", "")
        if cmd[1:3] == ["release", "list"]:
            return subprocess.CompletedProcess(cmd, 0, "\n".join(self.releases), "")
        if cmd[1:3] == ["release", "create"]:
            self.created.append(list(cmd))
            self.releases.append(cmd[3])
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return subprocess.CompletedProcess(cmd, 1, "", "unexpected")


def _tagged(repos, tag: str) -> None:  # noqa: F811
    git(repos.work, "tag", "-a", tag, "-m", tag)
    git(repos.work, "push", "-q", "origin", tag)


def test_draft_groups_by_type_and_keeps_scope_and_key(repos) -> None:  # noqa: F811
    _tagged(repos, "v0.1.0")
    commit(repos.work, "a", "feat(ship): add start (ADLC-337)")
    commit(repos.work, "b", "fix: stop the loop (ADLC-338)")
    commit(repos.work, "c", "docs(readme): rewrite (ADLC-262) (#52)")
    commit(repos.work, "d", "something unconventional")
    text = draft_changelog("0.2.0", repo=repos.work, today=date(2026, 10, 14))
    assert text.startswith("## 0.2.0 — 2026-10-14")
    assert "### Features\n\n- **ship**: add start (ADLC-337)" in text
    assert "### Fixes\n\n- stop the loop (ADLC-338)" in text
    assert "### Documentation" in text and "### Other\n\n- something unconventional" in text
    assert text.index("Features") < text.index("Fixes") < text.index("Documentation")


def test_draft_with_nothing_new_says_so(repos) -> None:  # noqa: F811
    _tagged(repos, "v0.1.0")
    assert "No changes since the last tag." in draft_changelog("0.2.0", repo=repos.work)


def test_draft_rejects_a_bad_version(repos) -> None:  # noqa: F811
    with pytest.raises(ShipError, match="not a version"):
        draft_changelog("v0.2.0", repo=repos.work)


def test_extract_notes_takes_one_section() -> None:
    assert extract_notes(CHANGELOG, "0.2.0") == "### Features\n\n- the new thing (ADLC-2)"
    assert extract_notes(CHANGELOG, "0.1.0") == "first"
    assert extract_notes(CHANGELOG, "9.9.9") == ""
    assert extract_notes(CHANGELOG, "0.2") == ""  # a prefix is not a match


def _release_repo(repos, gate: str) -> Path:  # noqa: F811
    (repos.work / "CHANGELOG.md").write_text(CHANGELOG)
    script = repos.work / "gate.sh"
    script.write_text(gate)
    git(repos.work, "add", "-A")
    git(repos.work, "commit", "-q", "-m", "chore: release 0.2.0 (ADLC-1)")
    git(repos.work, "push", "-q", "origin", "main")
    return script


def test_publish_tags_pushes_and_creates_the_release_with_the_changelog_note(repos) -> None:  # noqa: F811
    _release_repo(repos, '#!/bin/bash\ngit tag -a "v$1" -m "v$1"\n')
    gh = ReleaseGh()
    result = publish("0.2.0", repo=repos.work, script="gate.sh", run=gh)
    assert git(repos.origin, "tag", "--list") == "v0.2.0"
    create = gh.created[0]
    assert (
        create[3] == "v0.2.0" and "- the new thing (ADLC-2)" in create[create.index("--notes") + 1]
    )
    assert "--latest" in create
    assert any("created GitHub Release" in s for s in result.steps)


def test_publish_is_safe_to_rerun(repos) -> None:  # noqa: F811
    _release_repo(repos, '#!/bin/bash\ngit tag -a "v$1" -m "v$1"\n')
    gh = ReleaseGh()
    publish("0.2.0", repo=repos.work, script="gate.sh", run=gh)
    again = publish("0.2.0", repo=repos.work, script="gate.sh", run=gh)
    assert len(gh.created) == 1 and any("already exists" in s for s in again.steps)


def test_publish_stops_when_the_gate_fails_and_tags_nothing(repos) -> None:  # noqa: F811
    _release_repo(repos, "#!/bin/bash\necho 'versions disagree' >&2\nexit 1\n")
    gh = ReleaseGh()
    with pytest.raises(ShipError, match="versions disagree"):
        publish("0.2.0", repo=repos.work, script="gate.sh", run=gh)
    assert git(repos.work, "tag", "--list") == "" and gh.created == []


def test_publish_refuses_a_changelog_section_with_no_body(repos) -> None:  # noqa: F811
    _release_repo(repos, '#!/bin/bash\ngit tag -a "v$1" -m "v$1"\n')
    (repos.work / "CHANGELOG.md").write_text("# Changelog\n\n## 0.2.0 — 2026-10-14\n\n## 0.1.0\n")
    with pytest.raises(ShipError, match="no body"):
        publish("0.2.0", repo=repos.work, script="gate.sh", run=ReleaseGh())


def test_backfill_creates_only_missing_releases_and_only_the_newest_is_latest(repos) -> None:  # noqa: F811
    (repos.work / "CHANGELOG.md").write_text(CHANGELOG)
    for tag in ("v0.1.0", "v0.2.0", "v0.10.0"):
        commit(repos.work, tag)
        _tagged(repos, tag)
    gh = ReleaseGh(releases=["v0.2.0"])
    assert missing_releases(repos.work, gh) == ["v0.1.0", "v0.10.0"]  # numeric order, not text
    done = backfill(repo=repos.work, dry_run=False, run=gh)
    assert done == ["v0.1.0", "v0.10.0"]
    flags = {c[3]: ("--latest" in c, "--latest=false" in c) for c in gh.created}
    assert flags["v0.10.0"] == (True, False) and flags["v0.1.0"] == (False, True)
    assert "first" in gh.created[0][gh.created[0].index("--notes") + 1]  # from the CHANGELOG


def test_backfill_dry_run_creates_nothing(repos) -> None:  # noqa: F811
    (repos.work / "CHANGELOG.md").write_text(CHANGELOG)
    _tagged(repos, "v0.1.0")
    gh = ReleaseGh()
    assert backfill(repo=repos.work, dry_run=True, run=gh) == ["v0.1.0"] and gh.created == []


@pytest.mark.parametrize("since", ["--output=PWNED", "-n1", "a..b", "HEAD@{1}", "v0.1.0 x"])
def test_draft_rejects_a_since_that_git_would_read_as_an_option_or_a_range(
    repos,  # noqa: F811
    since: str,
) -> None:
    # `git log --output=<file>` writes there, so a `since` from an agent must be a plain ref name.
    _tagged(repos, "v0.1.0")
    with pytest.raises(ShipError, match="not a tag, branch or commit"):
        draft_changelog("0.2.0", repo=repos.work, since=since)
    assert not list(repos.work.glob("PWNED*"))


def test_draft_rejects_a_since_that_is_not_a_commit(repos) -> None:  # noqa: F811
    with pytest.raises(ShipError, match="not a commit in this repository"):
        draft_changelog("0.2.0", repo=repos.work, since="no-such-tag")


def test_draft_accepts_a_tag_a_branch_and_a_short_sha(repos) -> None:  # noqa: F811
    _tagged(repos, "v0.1.0")
    git(repos.work, "branch", "release-base")  # a branch that sits on the tagged commit
    commit(repos.work, "n", "feat: new (ADLC-2)")
    short = git(repos.work, "rev-parse", "--short", "v0.1.0")
    for since in ("v0.1.0", "release-base", short):
        assert "new (ADLC-2)" in draft_changelog("0.2.0", repo=repos.work, since=since)
