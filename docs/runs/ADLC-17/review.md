# Review — ADLC-17 (first real end-to-end Phase 4 run)

**Ticket:** [ADLC-17](https://cloud-f1.atlassian.net/browse/ADLC-17) — add `GET /health`
**PR:** [cloud-f1/jira-agent-cli#1](https://github.com/cloud-f1/jira-agent-cli/pull/1) (squash-merged, `6ecd7bb`)
**Run:** `POST /runs {issue_key: ADLC-17}` → `POST /runs/{id}/plan` → human review → `POST /runs/{id}/implement`

## Plan phase — verdict: good

Real Claude Agent SDK session, `permission_mode="plan"`. Produced a concrete,
codebase-accurate plan (correct files, correctly cited the "every client method is
also a tool" convention from `CLAUDE.md`, correctly flagged real risks — timeout
handling, `httpx` leaking into the API layer, bad-credentials-looks-like-outage).
Cost: ~$0.13 for a trivial single-turn call earlier in dev; this real plan run was not
separately metered but stayed under the $2 `max_budget_usd` ceiling. See
[`plan.md`](plan.md).

## Implement phase — verdict: good code, but a real permission gap

Resumed the Plan session (`resume=<session_id>`), `permission_mode="acceptEdits"`.
Wrote 7 files, 179 insertions, matching the plan exactly. **Human-verified after the
fact: 64/64 tests passing, `ruff check` clean, code matches repo conventions.**

**Finding — document this, don't just note it in passing:** `acceptEdits` allows
file Write/Edit tool calls but does **not** allow Bash. The agent could not run
`pytest`/`ruff`, could not transition the Jira ticket via `jira-agent transition`
(both would need Bash), and — importantly — **said so honestly** instead of claiming
verification it hadn't done ("none of it has been run yet... Nothing has been
committed or pushed"). That honesty is exactly the behavior an unattended,
unsupervised phase needs.

**What this means for the design:** Implement as currently configured produces a
diff, not a verified diff. A real (non-POC) version needs one of:
- `can_use_tool` allowing a specific, narrow Bash allowlist (`uv run pytest`,
  `uv run ruff check .`) rather than blanket `acceptEdits`, or
- A separate verification step after Implement (CI, or a bounded follow-up session
  with Bash access) before a PR is opened — this POC's human review step filled that
  gap manually.

This is arguably the single most useful thing this POC surfaced: the gap between
"the agent wrote code" and "the agent verified the code" is real and needs an
explicit design decision, not an assumption.

## Also observed

- The nested session behaves like a full Claude Code instance — it wrote its own
  plan file under `~/.claude/plans/` and referenced an unrelated MCP connector
  (Atlassian Rovo) it apparently has configured on this host, then correctly
  reasoned it wasn't needed. Confirms the SDK inherits the full host environment
  unless explicitly restricted (`mcp_servers`, `setting_sources`) — worth locking
  down before this pattern runs anywhere less trusted than a POC.
- Workspace isolation worked exactly as designed: `.worktrees/ADLC-17/` was created,
  scoped, and the diff landed only there — nothing touched the main working tree
  until the human-initiated merge.

## Production signal

No real deployment for a POC ticket — the "production signal" here is this review
plus the merged PR and the passing test suite on `main`. The closest analog: PR #1
is merged, `main`'s test count went from 54 to 64, and `GET /health` is live in the
codebase for any future manual smoke test against a running server.
