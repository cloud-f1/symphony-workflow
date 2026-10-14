"""The surface a person or an agent reads: --help, tool descriptions, skills, hooks (ADLC-261).

None of this is behaviour, which is why it rots: a command added without a help panel, a skill
copy edited on one side only, a hook still telling people to copy a file. These checks make that
fail a build instead of confusing someone later.
"""

from __future__ import annotations

import re
from pathlib import Path

from typer.main import get_command

from symphony_workflow.cli import app
from symphony_workflow.tools import TOOLS

REPO = Path(__file__).resolve().parent.parent
CANONICAL = REPO / ".claude/skills/symphony-workflow-ops/SKILL.md"
PACKAGED = REPO / "plugin/skills/symphony-workflow-ops/SKILL.md"


def _commands():
    return get_command(app).commands  # name -> click command


def test_every_command_is_in_a_help_panel_and_explains_itself() -> None:
    for name, command in _commands().items():
        assert command.rich_help_panel, f"{name} is not in a --help panel"
        assert len((command.help or "").strip()) >= 40, f"{name} has almost no help text"


def test_help_texts_do_not_refer_to_work_as_not_built_that_is_built() -> None:
    stale = [n for n, c in _commands().items() if "not built" in (c.help or "").lower()]
    assert stale == []


def test_every_tool_has_a_description_an_agent_can_act_on() -> None:
    thin = [t.name for t in TOOLS.values() if len(t.description) < 20]
    assert thin == []
    # the two gotchas that cost real time are in the descriptions an agent actually reads
    assert "parent" in TOOLS["create_issue"].description
    assert "wiki" in TOOLS["add_comment"].description.lower()


def _sections(path: Path) -> dict[str, str]:
    text = path.read_text(encoding="utf-8")
    parts = re.split(r"(?m)^## ", text)[1:]
    return {p.split("\n", 1)[0].strip(): p for p in parts}


# Sections where the plugin copy differs on purpose, each explained in the plugin copy itself:
# it has no session-start section (its own hook does that) and adds a note for other Jira sites.
INTENTIONAL = {"Quick start", "Branch naming — required, not optional"}


def test_the_two_skill_copies_have_the_same_sections_word_for_word() -> None:
    """The plugin copy is the canonical one plus a header note; the canonical one also carries the
    session-start section for this repo's own hook. Everything else must match exactly."""
    a, b = _sections(CANONICAL), _sections(PACKAGED)
    only_here = {"Session start — check before you start a server"}
    assert set(a) - set(b) <= only_here and set(b) <= set(a)
    drifted = [name for name in b if name not in INTENTIONAL and a[name] != b[name]]
    assert drifted == [], f"edit both copies: {drifted}"
    assert "intentional difference" in b["Quick start"]
    assert "different* Jira site" in b["Branch naming — required, not optional"]


def test_the_skills_describe_every_plan_sync_command() -> None:
    text = PACKAGED.read_text(encoding="utf-8")
    for command in ("profile", "import-plan", "sync-plan", "split-todos", "sync-status"):
        assert command in text, f"the operations skill does not mention {command}"
    assert "init-env" in text and "check-env" in text


def test_no_hook_or_skill_tells_people_to_copy_the_env_example() -> None:
    files = [
        REPO / ".claude/hooks/session-start-check-api.sh",
        REPO / "plugin/hooks/session-start-check-api.sh",
        CANONICAL,
        PACKAGED,
        REPO / "plugin/skills/jira-ticket-ops/SKILL.md",
    ]
    offenders = [
        f.name for f in files if re.search(r"cp \.env\.example|Copy \.env\.example", f.read_text())
    ]
    assert offenders == []
    assert "init-env" in (REPO / "plugin/hooks/session-start-check-api.sh").read_text()


def test_plugin_metadata_names_what_it_ships() -> None:
    import json

    meta = json.loads((REPO / "plugin/.claude-plugin/plugin.json").read_text())
    assert "plan sync" in meta["description"].lower() and "linear" in meta["keywords"]
    skills = {p.name for p in (REPO / "plugin/skills").iterdir() if p.is_dir()}
    assert skills == {"symphony-workflow-ops", "jira-ticket-ops", "shipping"}


def test_marketplace_describes_itself_and_matches_the_plugin() -> None:
    """`claude plugin validate --strict .` fails on a marketplace with no description; an entry that
    differs from plugin.json confuses people choosing what to install."""
    import json

    market = json.loads((REPO / ".claude-plugin/marketplace.json").read_text())
    meta = json.loads((REPO / "plugin/.claude-plugin/plugin.json").read_text())
    assert market.get("description", "").strip()
    assert [p["description"] for p in market["plugins"]] == [meta["description"]]


def test_the_readmes_say_how_to_install_the_plugin() -> None:
    line = "/plugin install symphony-workflow-ops --marketplace cloud-f1/symphony-workflow"
    for name in ("README.md", "README.zh-TW.md"):
        text = (REPO / name).read_text(encoding="utf-8")
        assert "/plugin marketplace add cloud-f1/symphony-workflow" in text and line in text


SHIPPING = REPO / ".claude/skills/shipping/SKILL.md"
SHIPPING_PACKAGED = REPO / "plugin/skills/shipping/SKILL.md"
GIT_COMMANDS = (
    "start",
    "pr",
    "land",
    "check-commit",
    "changelog-draft",
    "release",
    "release-backfill",
)


def test_the_shipping_skill_copies_are_identical_and_cover_every_git_command() -> None:
    text = SHIPPING_PACKAGED.read_text(encoding="utf-8")
    assert text == SHIPPING.read_text(encoding="utf-8")
    for command in GIT_COMMANDS:
        assert f"`{command}" in text, f"the shipping skill does not mention {command}"
    assert "JIRA_GIT_SETUP.md" in text


def test_the_operations_skill_points_at_shipping_and_the_release_command() -> None:
    text = PACKAGED.read_text(encoding="utf-8")
    assert "symphony-workflow release X.Y.Z" in text and "`shipping` skill" in text


def test_every_git_command_has_the_git_loop_help_panel() -> None:
    from typer.testing import CliRunner

    from symphony_workflow.cli import app

    out = CliRunner().invoke(app, ["--help"]).output
    panel = out[out.index("Git loop") :]
    for command in GIT_COMMANDS:
        assert command in panel, f"{command} is missing from the Git loop help panel"


def test_the_commit_hook_is_registered_and_the_setup_guide_exists() -> None:
    import json

    hooks = json.loads((REPO / "plugin/hooks/hooks.json").read_text())["hooks"]
    commands = [h["command"] for g in hooks["PreToolUse"] for h in g["hooks"]]
    assert any("commit-key-check.py" in c for c in commands)
    assert (REPO / "plugin/hooks/commit-key-check.py").exists()
    guide = (REPO / "docs/JIRA_GIT_SETUP.md").read_text(encoding="utf-8")
    assert "branch name" in guide and "dev-status" in guide
