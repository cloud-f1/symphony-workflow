#!/usr/bin/env bash
# SessionStart hook: check whether jira-agent-cli's own API server (port 8811)
# is already running (start it if not), then surface ready-to-pick-up ADLC
# tickets so a new session doesn't have to be told to go look. See
# .claude/skills/jira-agent-cli-ops/SKILL.md ("Session start — check before
# you start a server"). Fires once per session start -- this is NOT a
# recurring loop; for actual periodic autonomous pulling use /loop or a
# scheduled cloud agent instead.
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
HEALTH_URL="http://127.0.0.1:8811/health"
LOG_FILE="/tmp/jira-agent-api.log"

emit() {
  jq -n --arg msg "$1" '{"hookSpecificOutput":{"hookEventName":"SessionStart","additionalContext":$msg}}'
}

ready_tickets_summary() {
  # Best-effort -- a Jira lookup failure here must never block session start,
  # so every failure mode falls through to an empty string.
  [ -f "$REPO_ROOT/.env" ] || return 0
  ( set -a && . "$REPO_ROOT/.env" && set +a
    [ -n "${JIRA_BASE_URL:-}" ] && [ -n "${JIRA_EMAIL:-}" ] && [ -n "${JIRA_API_TOKEN:-}" ] || exit 0
    # v2 /search is retired (confirmed elsewhere in this repo, see client.py's
    # search_issues) -- v3 /search/jql is a POST with a JSON body, not GET+query.
    jql='project = ADLC AND status = "To Do" AND issuetype not in (Epic, Subtask) ORDER BY created ASC'
    payload="$(jq -n --arg jql "$jql" '{jql: $jql, fields: ["summary"], maxResults: 10}')"
    body="$(curl -s -m 5 -u "$JIRA_EMAIL:$JIRA_API_TOKEN" -X POST -H "Content-Type: application/json" \
      -d "$payload" "$JIRA_BASE_URL/rest/api/3/search/jql")"
    [ -n "$body" ] || exit 0
    echo "$body" | jq -r '.issues[]? | "\(.key): \(.fields.summary)"' 2>/dev/null
  ) 2>/dev/null
}

status="$(curl -s -m 3 "$HEALTH_URL" 2>/dev/null || true)"

if [ -z "$status" ]; then
  if [ ! -f "$REPO_ROOT/.env" ]; then
    emit "jira-agent-cli API server is not running, and $REPO_ROOT/.env is missing -- cannot auto-start. Copy .env.example to .env and fill in Jira credentials first."
    exit 0
  fi
  ( cd "$REPO_ROOT" && nohup uv run --env-file .env uvicorn jira_agent_cli.api:app --port 8811 > "$LOG_FILE" 2>&1 & disown ) 2>/dev/null
  sleep 3
  status="$(curl -s -m 5 "$HEALTH_URL" 2>/dev/null || true)"
fi

if [ -z "$status" ]; then
  emit "jira-agent-cli API server was not running -- tried to auto-start it but it did not come up. Check $LOG_FILE and run manually: uv run --env-file .env uvicorn jira_agent_cli.api:app --port 8811"
  exit 0
fi

tickets="$(ready_tickets_summary)"
if [ -n "$tickets" ]; then
  msg="jira-agent-cli API server: $status
Ready to pick up (ADLC, status=To Do, not Epic/Subtask):
$tickets"
else
  msg="jira-agent-cli API server: $status
No ADLC tickets currently in To Do (or the ready-ticket lookup failed) -- check docs/context/session-summary.md for what's next."
fi
emit "$msg"
