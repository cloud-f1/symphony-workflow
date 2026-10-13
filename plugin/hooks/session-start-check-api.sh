#!/usr/bin/env bash
# SessionStart hook (plugin-packaged copy): check whether symphony-workflow's own
# API server (port 8811) is already running; start it if not. Mirrors this
# repo's own .claude/hooks/session-start-check-api.sh -- see docs/PORTABILITY.md's
# "Claude Code plugin" section for why this copy exists, and
# skills/symphony-workflow-ops/SKILL.md for the operational reference.
#
# Unlike the repo-local original, this script cannot assume it lives inside
# the symphony-workflow checkout -- a plugin installs to its own directory
# (${CLAUDE_PLUGIN_ROOT}), which has nothing to do with where the adopter
# cloned symphony-workflow. So it uses ${CLAUDE_PROJECT_DIR} (the session's
# project root, exported by Claude Code to every hook) as the repo location
# instead of deriving it from this script's own path, and verifies that
# directory actually looks like a symphony-workflow checkout before doing
# anything -- this hook is installed once per user/plugin-scope and will fire
# on SessionStart in *every* project, not just this one.
set -uo pipefail

# The hook reports through jq. Without it there is nothing safe to print, so stay quiet and do nothing
# (this hook is installed once per user and fires in every project).
command -v jq >/dev/null 2>&1 || exit 0

REPO_ROOT="${CLAUDE_PROJECT_DIR:-$PWD}"
HEALTH_URL="http://127.0.0.1:8811/health"
LOG_FILE="/tmp/symphony-workflow-api.log"

emit() {
  jq -n --arg msg "$1" '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":$msg}}'
}

if [ ! -f "$REPO_ROOT/pyproject.toml" ] || ! grep -q '^name = "symphony-workflow"' "$REPO_ROOT/pyproject.toml" 2>/dev/null; then
  # Not a symphony-workflow checkout -- stay quiet and do nothing. This keeps the
  # plugin safe to enable globally without it trying to manage a port 8811
  # server in unrelated projects.
  exit 0
fi

status="$(curl -s -m 3 "$HEALTH_URL" 2>/dev/null || true)"

if [ -n "$status" ]; then
  emit "symphony-workflow API server already running on :8811 -- $status"
  exit 0
fi

if [ ! -f "$REPO_ROOT/.env" ]; then
  emit "symphony-workflow API server is not running, and $REPO_ROOT/.env is missing -- cannot auto-start. Run `symphony-workflow init-env` there, fill in the empty keys, then `symphony-workflow check-env --verify`."
  exit 0
fi

( cd "$REPO_ROOT" && nohup uv run --env-file .env uvicorn symphony_workflow.api:app --port 8811 > "$LOG_FILE" 2>&1 & disown ) 2>/dev/null

sleep 3
status="$(curl -s -m 5 "$HEALTH_URL" 2>/dev/null || true)"

if [ -n "$status" ]; then
  emit "symphony-workflow API server was not running -- auto-started it on :8811. $status"
else
  emit "symphony-workflow API server was not running -- tried to auto-start it but it did not come up. Check $LOG_FILE and run manually: uv run --env-file .env uvicorn symphony_workflow.api:app --port 8811"
fi
