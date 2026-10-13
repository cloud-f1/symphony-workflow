"""Unit tests for the project profile and adaptive recommendation (project_profile.py, ADLC-199).

Projects are built in `tmp_path`; the machine's git config and credential environment are switched
off so the result never depends on who runs the suite.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from symphony_workflow.project_profile import (
    PlanSyncConfig,
    load_config,
    profile_project,
    recommend,
    recommend_from_usage,
    refine_from_content,
    save_config,
)

from ._athena import make_project

SECRET = "tok-DO-NOT-LEAK-999"


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", "/dev/null")
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", "/dev/null")
    for name in (
        "JIRA_BASE_URL",
        "JIRA_EMAIL",
        "JIRA_API_TOKEN",
        "LINEAR_API_KEY",
        "SYMPHONY_WORKFLOW_TRACKER",
    ):
        monkeypatch.delenv(name, raising=False)


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        check=True,
        capture_output=True,
    )


def _repo_with_history(root: Path, subjects: list[str], branch: str | None = None) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    for subject in subjects:
        _git(root, "commit", "-q", "--allow-empty", "-m", subject)
    if branch:
        _git(root, "branch", branch)


def _jira_env(root: Path, token: str = SECRET) -> None:
    (root / ".env").write_text(
        f"JIRA_BASE_URL=https://x.example\nJIRA_EMAIL=a@b.c\nJIRA_API_TOKEN={token}\n",
        encoding="utf-8",
    )


def test_profile_reads_plan_files_and_content_shape(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    profile = profile_project(root)
    assert profile.plan.epics == 4 and profile.plan.has_progress and not profile.plan.has_index
    assert (profile.content.phases, profile.content.open_phases) == (2, 1)
    assert profile.content.tasks_per_category_median == 1.5
    assert profile.content.acceptance_median == 1  # every sample epic has one or two items
    assert (
        profile.content.ready_todo_share == 0.75
    )  # E1 stories, E2 phases, E3 checklist; E4 has none


def test_an_empty_project_has_nothing_to_report(tmp_path: Path) -> None:
    profile = profile_project(tmp_path)
    assert profile.plan.epics == 0 and profile.content.phases == 0
    assert profile.content.acceptance_median is None and profile.tracker.key_prefixes == {}


def test_issue_keys_come_from_branches_and_commit_subjects(tmp_path: Path) -> None:
    _repo_with_history(
        tmp_path, ["feat: add x (PROJ-12)", "PROJ-13 fix y", "chore: nothing"], "ABC-7-thing"
    )
    assert profile_project(tmp_path).tracker.key_prefixes == {"PROJ": 2, "ABC": 1}


def test_credentials_are_reported_as_names_never_values(tmp_path: Path) -> None:
    _jira_env(tmp_path)
    profile = profile_project(tmp_path)
    assert profile.tracker.jira_credentials is True
    assert SECRET not in json.dumps(profile.as_dict())
    first, refined = recommend(profile)
    assert SECRET not in json.dumps([first.as_dict(), refined.as_dict()])


def test_partial_jira_credentials_do_not_count(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(
        "JIRA_BASE_URL=https://x.example\nJIRA_EMAIL=\n", encoding="utf-8"
    )
    assert profile_project(tmp_path).tracker.jira_credentials is False


def test_recommendation_one_picks_jira_from_project_credentials_and_history(tmp_path: Path) -> None:
    _repo_with_history(tmp_path, ["feat: x (PROJ-1)"])
    _jira_env(tmp_path)
    rec = recommend_from_usage(profile_project(tmp_path))
    assert rec.config.tracker == "jira"
    assert any("PROJ" in n.evidence for n in rec.notes)


def test_issue_keys_without_credentials_say_to_run_init_env(tmp_path: Path) -> None:
    _repo_with_history(tmp_path, ["feat: x (PROJ-1)"])
    rec = recommend_from_usage(profile_project(tmp_path))
    assert rec.config.tracker == "markdown"
    assert any("init-env" in n.advice for n in rec.notes)


def test_a_linear_key_in_the_shell_is_not_enough_to_pick_linear(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LINEAR_API_KEY", SECRET)
    rec = recommend_from_usage(profile_project(tmp_path))
    assert rec.config.tracker == "markdown"
    assert any("shell" in n.evidence for n in rec.notes) and SECRET not in json.dumps(rec.as_dict())


def test_a_linear_key_in_the_project_env_picks_linear(tmp_path: Path) -> None:
    (tmp_path / ".env").write_text(f"LINEAR_API_KEY={SECRET}\n", encoding="utf-8")
    assert recommend_from_usage(profile_project(tmp_path)).config.tracker == "linear"


def test_an_explicit_tracker_setting_wins(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SYMPHONY_WORKFLOW_TRACKER", "linear")
    assert recommend_from_usage(profile_project(tmp_path)).config.tracker == "linear"


def test_plan_source_follows_whether_epic_files_exist(tmp_path: Path) -> None:
    assert recommend_from_usage(profile_project(tmp_path)).config.plan_source == "decomposition"
    assert (
        recommend_from_usage(profile_project(make_project(tmp_path / "p"))).config.plan_source
        == "epics-md"
    )


def test_no_evidence_at_all_means_markdown(tmp_path: Path) -> None:
    rec = recommend_from_usage(profile_project(tmp_path))
    assert rec.config.tracker == "markdown" and rec.config.push == "manual"
    assert rec.config.include_done is False


def test_recommendation_two_uses_the_breakdown_when_many_epics_have_one(tmp_path: Path) -> None:
    profile = profile_project(make_project(tmp_path))
    refined = refine_from_content(profile, recommend_from_usage(profile).config)
    assert refined.config.todo_source == "breakdown"
    assert any(n.topic == "To-Do source" for n in refined.notes)


def test_recommendation_two_stays_hybrid_when_few_epics_have_one(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    for name in ("e1-login.md", "e2-reports.md", "e3-old.md"):
        (root / "docs/epics" / name).write_text(
            f"# E{name[1]} — x\n\n## Acceptance Criteria\n- [ ] a\n", encoding="utf-8"
        )
    profile = profile_project(root)
    refined = refine_from_content(profile, recommend_from_usage(profile).config)
    assert refined.config.todo_source == "hybrid"


def test_recommendation_two_flags_a_large_completed_history(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    rows = "".join(f"| Phase {n} | E{n} | ✅ Complete |\n" for n in range(10, 40))
    path = root / "docs/context/epic-progress.md"
    path.write_text(
        path.read_text().replace("| Phase 1 | E3 | ✅ Complete |", rows.rstrip("\n")),
        encoding="utf-8",
    )
    refined = refine_from_content(profile_project(root), PlanSyncConfig())
    assert any(n.topic == "history" for n in refined.notes)


def test_config_round_trip_and_defaults(tmp_path: Path) -> None:
    assert load_config(tmp_path) is None
    path = save_config(tmp_path, PlanSyncConfig(tracker="jira", todo_source="breakdown"))
    assert path == tmp_path / "docs/context/plan-sync.json"
    loaded = load_config(tmp_path)
    assert loaded is not None and loaded.tracker == "jira" and loaded.todo_source == "breakdown"
    assert loaded.push == "manual" and loaded.include_done is False


def test_an_invalid_config_is_a_clear_error(tmp_path: Path) -> None:
    (tmp_path / "docs/context").mkdir(parents=True)
    (tmp_path / "docs/context/plan-sync.json").write_text('{"tracker": "trello"}', encoding="utf-8")
    with pytest.raises(ValueError, match="not a valid plan-sync config"):
        load_config(tmp_path)
