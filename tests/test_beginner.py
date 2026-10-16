"""What a newcomer sees first: --version and the plain-language next steps (ADLC-375)."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from symphony_workflow.cli import app
from symphony_workflow.project_profile import next_steps, profile_project

from ._athena import make_project

runner = CliRunner()


def test_version_flag_prints_the_package_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.startswith("symphony-workflow ") and result.output.split()[-1][0].isdigit()


def test_an_empty_folder_is_told_what_a_plan_file_looks_like(tmp_path: Path) -> None:
    text = "\n".join(next_steps(profile_project(tmp_path)))
    assert "docs/epics/e1-login.md" in text and "# E1: Login page" in text
    assert "Acceptance Criteria" in text and "Your first plan file" in text


def test_a_project_with_plans_gets_three_numbered_commands(tmp_path: Path) -> None:
    make_project(tmp_path)
    steps = next_steps(profile_project(tmp_path))
    commands = [s.strip() for s in steps if s.strip().startswith("symphony-workflow")]
    assert commands == [
        "symphony-workflow import-plan",
        "symphony-workflow sync-plan --from epics-md --to markdown --dry-run",
        "symphony-workflow profile --write",
    ]


def test_the_commands_carry_the_path_only_when_it_is_not_the_current_folder(tmp_path: Path) -> None:
    make_project(tmp_path)
    profile = profile_project(tmp_path)
    assert "--path ../x" in "\n".join(next_steps(profile, path="../x"))
    assert "--path" not in "\n".join(next_steps(profile, path="."))


def test_a_saved_config_changes_the_advice_to_a_dry_run(tmp_path: Path) -> None:
    make_project(tmp_path)
    steps = next_steps(profile_project(tmp_path), config_exists=True)
    assert any("sync-plan --dry-run" in s for s in steps) and not any("--write" in s for s in steps)


def test_profile_prints_the_next_block_after_the_recommendations(tmp_path: Path) -> None:
    make_project(tmp_path)
    result = runner.invoke(app, ["profile", "--path", str(tmp_path)])
    assert result.exit_code == 0
    assert result.output.index("Recommendation 2") < result.output.index("Next:")
    assert "symphony-workflow import-plan" in result.output


def test_profile_json_is_unchanged_by_the_next_block(tmp_path: Path) -> None:
    import json

    make_project(tmp_path)
    data = json.loads(runner.invoke(app, ["profile", "--path", str(tmp_path), "--json"]).output)
    assert set(data) == {"profile", "recommendation_1", "recommendation_2"}


# --- the READMEs a newcomer reads ------------------------------------------------------------

REPO = Path(__file__).resolve().parent.parent


def test_every_command_is_in_the_cheat_sheet_of_both_readmes() -> None:
    from typer.main import get_command

    names = set(get_command(app).commands)
    for readme in ("README.md", "README.zh-TW.md"):
        text = (REPO / readme).read_text(encoding="utf-8")
        missing = sorted(n for n in names if f"`{n}`" not in text)
        assert missing == [], f"{readme} does not list: {missing}"


def test_the_first_plan_file_in_the_readme_is_one_the_tool_reads(tmp_path: Path) -> None:
    from symphony_workflow.epics_md import load_epics_md

    shown = (REPO / "docs/snapshots/10-first-plan.txt").read_text(encoding="utf-8")
    body = shown.split("$ cat docs/epics/e1-login.md\n", 1)[1].split("\n$ ", 1)[0]
    (tmp_path / "docs/epics").mkdir(parents=True)
    (tmp_path / "docs/epics/e1-login.md").write_text(body, encoding="utf-8")
    plan = load_epics_md(tmp_path)
    tasks = [t for c in plan.categories for t in c.tasks]
    assert [(t.id, t.title) for t in tasks] == [("E1", "Login page")]


def test_the_readmes_tell_a_newcomer_what_to_install_and_how_to_check_it() -> None:
    for readme, phrase in (("README.md", "Before you start"), ("README.zh-TW.md", "開始之前")):
        text = (REPO / readme).read_text(encoding="utf-8")
        assert phrase in text and "symphony-workflow --version" in text and "`uv`" in text


def test_every_local_tool_is_in_the_tool_table_of_both_readmes() -> None:
    from symphony_workflow.tools import LOCAL_TOOLS

    for readme in ("README.md", "README.zh-TW.md"):
        text = (REPO / readme).read_text(encoding="utf-8")
        missing = sorted(t for t in LOCAL_TOOLS if f"| `{t}` |" not in text)
        assert missing == [], f"{readme} does not list the tools: {missing}"


def test_the_readmes_say_the_commands_that_change_git_are_not_tools() -> None:
    for readme in ("README.md", "README.zh-TW.md"):
        text = (REPO / readme).read_text(encoding="utf-8")
        assert "`release-backfill`) are deliberately **not** tools" in text or "刻意不是" in text
