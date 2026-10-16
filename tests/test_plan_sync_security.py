"""Security regression tests for plan sync (ADLC-270), one per finding of the security review.

Everything happens in `tmp_path`; the "attacks" only touch marker files in a throwaway directory.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from symphony_workflow.envsetup import check_env, init_env
from symphony_workflow.epics_md import scan_epics
from symphony_workflow.markdown_sink import read_state, write_markdown
from symphony_workflow.plan import Category, Plan, Task, Todo
from symphony_workflow.project_profile import profile_project
from symphony_workflow.safepath import UnsafePathError
from symphony_workflow.tools import call_tool


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")


def _plan() -> Plan:
    task = Task(id="c1.t1", title="Sign in", todos=[Todo(id="c1.t1.d1", title="Tests: unit")])
    return Plan(categories=[Category(id="c1", title="One", tasks=[task])])


# ---- 1. git must not run commands from an untrusted repository's config ----


def _evil_repo(root: Path, marker: Path) -> Path:
    repo = root / "evil"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@e.c",
         "commit", "-q", "--allow-empty", "-m", "feat: x (PROJ-1)"],
        check=True,
    )  # fmt: skip
    # The classic trap: core.fsmonitor names a command that git runs while reading the index.
    subprocess.run(
        ["git", "-C", str(repo), "config", "core.fsmonitor", f"touch {marker}; echo"], check=True
    )
    return repo


def test_profile_does_not_run_commands_from_the_repo_config(tmp_path: Path) -> None:
    marker = tmp_path / "PWNED-profile"
    profile = profile_project(_evil_repo(tmp_path, marker))
    assert not marker.exists(), "git ran core.fsmonitor from an untrusted .git/config"
    assert profile.tracker.key_prefixes == {"PROJ": 1}  # the history is still read


def test_profile_does_not_run_gpg_program_from_the_repo_config(tmp_path: Path) -> None:
    # log.showSignature makes `git log` verify a signed commit, and gpg.program names the verifier.
    marker = tmp_path / "PWNED-gpg"
    repo = _evil_repo(tmp_path, tmp_path / "unused")
    script = tmp_path / "evil-gpg.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    script.chmod(0o755)
    tree = subprocess.run(
        ["git", "-C", str(repo), "write-tree"], check=True, capture_output=True, text=True
    ).stdout.strip()
    raw = (
        f"tree {tree}\nauthor t <t@e.c> 1 +0000\ncommitter t <t@e.c> 1 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n \n x\n -----END PGP SIGNATURE-----\n\n"
        "feat: signed (PROJ-2)\n"
    )
    commit = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=raw, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    subprocess.run(["git", "-C", str(repo), "reset", "-q", "--soft", commit], check=True)
    for key, value in (("log.showSignature", "true"), ("gpg.program", str(script))):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    profile = profile_project(repo)
    assert not marker.exists(), "git ran gpg.program from an untrusted .git/config"
    assert profile.tracker.key_prefixes == {"PROJ": 1}  # the signed commit is still read


def test_check_env_and_init_env_do_not_run_commands_from_the_repo_config(tmp_path: Path) -> None:
    marker = tmp_path / "PWNED-env"
    repo = _evil_repo(tmp_path, marker)
    check_env(repo / ".env")
    init_env(repo / ".env")
    assert not marker.exists(), "git ran core.fsmonitor from an untrusted .git/config"


# ---- 2. the agent-facing tools stay inside the working directory ----


def test_tools_refuse_a_path_outside_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, outside = tmp_path / "proj", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    monkeypatch.chdir(project)
    for tool, extra in (
        ("profile_project", {}),
        ("import_plan", {}),
        ("changelog_draft", {"version": "1.0.0"}),
    ):
        with pytest.raises(ValueError, match="inside the working directory"):
            call_tool(None, tool, {"path": str(outside), **extra})
        with pytest.raises(ValueError, match="inside the working directory"):
            call_tool(None, tool, {"path": "..", **extra})


def test_tools_refuse_a_symlink_that_leads_outside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project, outside = tmp_path / "proj", tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    (project / "link").symlink_to(outside)
    monkeypatch.chdir(project)
    with pytest.raises(ValueError, match="inside the working directory"):
        call_tool(None, "profile_project", {"path": "link"})


def test_tools_still_work_inside_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "sub").mkdir()
    monkeypatch.chdir(tmp_path)
    assert call_tool(None, "profile_project", {"path": "."})["profile"]["plan"]["epics"] == 0
    assert call_tool(None, "import_plan", {"path": "sub"})["counts"] == [0, 0, 0]


# ---- 3. plan files are never read or written through a symlink ----


def test_write_refuses_a_symlinked_epics_directory(tmp_path: Path) -> None:
    project, outside = tmp_path / "proj", tmp_path / "outside"
    (project / "docs").mkdir(parents=True)
    outside.mkdir()
    (project / "docs/epics").symlink_to(outside)
    with pytest.raises(UnsafePathError):
        write_markdown(_plan(), project)
    assert list(outside.iterdir()) == []


def test_write_refuses_a_symlinked_task_file(tmp_path: Path) -> None:
    project, outside = tmp_path / "proj", tmp_path / "outside"
    (project / "docs/epics").mkdir(parents=True)
    outside.mkdir()
    victim = outside / "victim.md"
    victim.write_text("keep me\n")
    (project / "docs/epics/c1-t1-sign-in.md").symlink_to(victim)
    with pytest.raises(UnsafePathError):
        write_markdown(_plan(), project)
    assert victim.read_text() == "keep me\n"


def test_write_refuses_a_symlinked_index(tmp_path: Path) -> None:
    project, outside = tmp_path / "proj", tmp_path / "outside"
    (project / "docs/epics").mkdir(parents=True)
    outside.mkdir()
    victim = outside / "index.md"
    victim.write_text("keep me\n")
    (project / "docs/epics/EPIC_INDEX.md").symlink_to(victim)
    with pytest.raises(UnsafePathError):
        write_markdown(_plan(), project)
    assert victim.read_text() == "keep me\n"


def test_reading_skips_symlinked_epic_files_and_progress(tmp_path: Path) -> None:
    project, outside = tmp_path / "proj", tmp_path / "outside"
    (project / "docs/epics").mkdir(parents=True)
    (project / "docs/context").mkdir(parents=True)
    outside.mkdir()
    secret = outside / "secret.md"
    secret.write_text("# E1 — Stolen title\n\n## Acceptance Criteria\n- [ ] leaked\n")
    (project / "docs/epics/e1-secret.md").symlink_to(secret)
    (project / "docs/context/epic-progress.md").symlink_to(secret)
    assert scan_epics(project) == []
    assert profile_project(project).content.phases == 0


def test_read_state_ignores_a_symlinked_file(tmp_path: Path) -> None:
    project, outside = tmp_path / "proj", tmp_path / "outside"
    (project / "docs/epics").mkdir(parents=True)
    outside.mkdir()
    fake = outside / "fake.md"
    fake.write_text("- [x] Tests: unit <!-- sd-id: c1.t1.d1 -->\n")
    (project / "docs/epics/c1-t1-sign-in.md").symlink_to(fake)
    plan = _plan()
    assert read_state(plan, project) == []
    assert plan.categories[0].tasks[0].todos[0].status == "todo"


def test_changelog_draft_does_not_run_gpg_program_from_the_repo_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The tool reads `git log` in a directory the agent names: log.showSignature plus gpg.program
    # must not run a program from that repository's own config (ADLC-393).
    marker = tmp_path / "PWNED-changelog"
    repo = _evil_repo(tmp_path, tmp_path / "unused")
    script = tmp_path / "evil-gpg.sh"
    script.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    script.chmod(0o755)
    tree = subprocess.run(
        ["git", "-C", str(repo), "write-tree"], check=True, capture_output=True, text=True
    ).stdout.strip()
    raw = (
        f"tree {tree}\nauthor t <t@e.c> 1 +0000\ncommitter t <t@e.c> 1 +0000\n"
        "gpgsig -----BEGIN PGP SIGNATURE-----\n \n x\n -----END PGP SIGNATURE-----\n\n"
        "feat: signed (PROJ-2)\n"
    )
    commit = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-t", "commit", "-w", "--stdin"],
        input=raw, check=True, capture_output=True, text=True,
    ).stdout.strip()  # fmt: skip
    subprocess.run(["git", "-C", str(repo), "reset", "-q", "--soft", commit], check=True)
    for key, value in (("log.showSignature", "true"), ("gpg.program", str(script))):
        subprocess.run(["git", "-C", str(repo), "config", key, value], check=True)
    monkeypatch.chdir(tmp_path)  # the tool only reads inside the working directory
    result = call_tool(None, "changelog_draft", {"version": "1.0.0", "path": "evil"})
    assert not marker.exists(), "git ran gpg.program from an untrusted .git/config"
    assert "signed (PROJ-2)" in result["markdown"]  # the signed commit is still read
