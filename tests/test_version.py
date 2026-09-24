"""pyproject.toml's version and plugin/.claude-plugin/plugin.json's version must stay in
sync (ADLC-52) -- the plugin is a repackaging of this same release, not an independently
versioned artifact, so a drift between the two is a real bug, not a stylistic nit.
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
