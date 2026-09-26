#!/usr/bin/env bash
# scripts/release.sh <version> -- makes the git-tag step of a release
# unskippable (ADLC-101). v0.3.0 and v0.4.0's CHANGELOG entries said
# "tagged" but `git tag -l` disagreed -- a three-step manual process
# (bump pyproject.toml + plugin.json together, write the CHANGELOG entry,
# `git tag -a` + push) silently dropped the tag step twice.
#
# Design: run this AFTER the version-bump PR (pyproject.toml +
# plugin/.claude-plugin/plugin.json + the CHANGELOG.md entry, bundled into
# the same feature commit -- this repo's own established pattern, see
# ADLC-53/57/86/87/88/95/121's release commits) is already merged to main.
# This script does NOT bump the version files or create a commit itself --
# it verifies the merged bump is real and consistent, then tags. Tagging
# BEFORE the bump is committed would tag the wrong commit; making this
# script also create its own bump commit would introduce a second commit
# shape this repo doesn't otherwise use. Verify-then-tag matches what was
# actually done by hand for v0.4.1/0.4.2/0.4.3, just without the "forgot
# to tag" failure mode.
#
# Usage:
#   scripts/release.sh 0.5.0
#
# Exits non-zero (and does NOT tag) if:
#   - pyproject.toml's version isn't already exactly <version>
#   - plugin/.claude-plugin/plugin.json's version isn't already exactly <version>
#   - CHANGELOG.md has no "## <version>" heading yet
#   - `uv run pytest tests/test_version.py` fails
#   - a git tag v<version> already exists
#
# Never runs `git push` itself -- prints the exact commands instead,
# matching this repo's existing confirm-before-pushing posture elsewhere.

set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: scripts/release.sh <version>  (e.g. scripts/release.sh 0.5.0)" >&2
  echo "Run this AFTER the version-bump PR (pyproject.toml + plugin.json +" >&2
  echo "CHANGELOG.md, in one commit) is already merged to main." >&2
  exit 1
fi

VERSION="$1"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

fail() {
  echo "ERROR: $1" >&2
  exit 1
}

echo "==> Checking pyproject.toml's version is already ${VERSION}"
PYPROJECT_VERSION="$(python3 -c '
import re
content = open("pyproject.toml").read()
m = re.search(r"(?m)^version = \"([^\"]*)\"", content)
print(m.group(1) if m else "")
')"
[ "$PYPROJECT_VERSION" = "$VERSION" ] || fail "pyproject.toml's version is '${PYPROJECT_VERSION}', expected '${VERSION}'. Bump it (and plugin.json, and CHANGELOG.md), merge that PR, then re-run this script."

echo "==> Checking plugin.json's version is already ${VERSION}"
PLUGIN_VERSION="$(python3 -c '
import json
print(json.load(open("plugin/.claude-plugin/plugin.json")).get("version", ""))
')"
[ "$PLUGIN_VERSION" = "$VERSION" ] || fail "plugin/.claude-plugin/plugin.json's version is '${PLUGIN_VERSION}', expected '${VERSION}'."

echo "==> Checking CHANGELOG.md has a '## ${VERSION}' heading"
grep -q "^## ${VERSION}" CHANGELOG.md || fail "CHANGELOG.md has no '## ${VERSION}' heading yet. Write the changelog entry, merge it, then re-run this script."

echo "==> Checking v${VERSION} isn't already tagged"
if git rev-parse "v${VERSION}" >/dev/null 2>&1; then
  fail "Tag v${VERSION} already exists."
fi

echo "==> Running the version-sync test"
uv run pytest tests/test_version.py -q || fail "tests/test_version.py failed -- pyproject.toml and plugin.json disagree, or something else broke. Not tagging."

echo "==> Creating annotated tag v${VERSION}"
git tag -a "v${VERSION}" -m "v${VERSION}"

cat <<EOF

Tagged v${VERSION} locally. Push it:

  git push origin v${VERSION}

(If main itself hasn't been pushed yet: git push origin main first.)
EOF
