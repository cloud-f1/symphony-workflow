"""pyproject.toml's version, plugin/.claude-plugin/plugin.json's version and the plugin entry in
.claude-plugin/marketplace.json must stay in sync (ADLC-52, ADLC-250) -- the plugin is a
repackaging of this same release, not an independently versioned artifact, so a drift is a real
bug, not a stylistic nit. Claude Code only refreshes an installed plugin when `version` changes,
so a marketplace entry left behind means users keep the old plugin.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _pyproject_version() -> str:
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text())
    return data["project"]["version"]


def _plugin_version() -> str:
    data = json.loads((REPO_ROOT / "plugin" / ".claude-plugin" / "plugin.json").read_text())
    return data["version"]


def test_plugin_version_matches_pyproject_version() -> None:
    assert _plugin_version() == _pyproject_version()


def _marketplace_versions() -> list[str]:
    data = json.loads((REPO_ROOT / ".claude-plugin" / "marketplace.json").read_text())
    return [entry["version"] for entry in data["plugins"]]


def test_marketplace_versions_match_pyproject_version() -> None:
    versions = _marketplace_versions()
    assert versions, "marketplace.json lists no plugin: nothing to check is a failure, not a pass"
    assert set(versions) == {_pyproject_version()}
