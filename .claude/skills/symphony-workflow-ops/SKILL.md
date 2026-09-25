---
name: symphony-workflow-ops
description: >
  Use when operating symphony-workflow itself — starting the API server, driving a
  ticket through Plan then Implement, reading poller status, or picking up a
  ticket by hand. Trigger on: "run symphony-workflow", "plan this ticket", "start
  the poller", "symphony-workflow server", "ADLC ticket", or any request to drive
  a Jira ticket through this repo's Plan/Implement loop. For the why/design
  behind these mechanics, read docs/{intent,spec,plan}.md — this skill is the
  short operational reference, not the design doc.
---

# symphony-workflow operations

**Read this before running anything in this repo for the first time.** For the
*why* (Symphony, the ADLC decision log, findings from real runs) see
`docs/{intent,spec,plan}.md` — this file stays short on purpose and doesn't
duplicate them.

## Session start — check before you start a server

Before assuming the API server is down (and definitely before launching a new
one), check first: `curl -s http://127.0.0.1:8811/health`. A prior session may
have left it running in the background; starting a second instance on the same
port just errors, and starting one against a different `.env`/target repo while
another is already polling is a real way to get two pollers double-dispatching
Plans. If `/health` doesn't respond, then start it as below.

## Quick start

```bash
uv sync --extra dev
cp .env.example .env   # fill in JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
uv run --env-file .env uvicorn symphony_workflow.api:app --port 8811
```

`GET /health` reports Jira connectivity and whether the poller is running.
Note: `config.py` reads `os.environ` directly, no `python-dotenv` — plain `uv
run symphony-workflow ...` will NOT pick up `.env` and fails with "Missing required
env var(s)". Either pass `--env-file .env` to `uv run` (confirmed working,
uv 0.12.3+) or export the three `JIRA_*` vars into the shell yourself.

## The four ways to drive a ticket

1. **Manual, one ticket**: `POST /runs {issue_key}` → `POST /runs/{id}/plan` →
   **read the plan, it's a real judgment call, not a formality** → `POST
   /runs/{id}/implement`. `GET /issues/{key}/run` finds a Run's id from the
   issue key (needed after auto-discovery, since nothing else returns it).
2. **Auto-discovery (the poller)**: any ticket with `status = "To Do"` on the
   configured Jira project gets auto-Planned — no manual `POST /runs` needed.
   **It never auto-Implements.** That's not a missing feature, it's the whole
   point (see `docs/plans/0001-symphony-poc/spec.md`'s Phase 5 section for why) — don't "complete" it
   by wiring Implement into the poller.
3. **CLI tool-call passthrough**: `symphony-workflow call <tool> '<json args>'` or
   `symphony-workflow list-tools` — the same registry the API's `/tools` exposes, for
   scripting outside the server.
4. **CLI-native control plane** (no server needed): `symphony-workflow plan <key>` /
   `symphony-workflow implement <key>` run the same Plan/Implement loop as way 1
   in-process, via `orchestrator.py` (ADLC-45) — `RunStore` is per-process here, so
   `plan` saves its `Run` to `docs/runs/<key>/run.json` for a later `implement` to
   reload. `symphony-workflow decompose <key>` (ADLC-46) breaks a Jira Epic into
   Stories/Tasks for human review — read-only, no Jira writes, renders
   `docs/runs/<key>/decomposition.{md,json}`. `symphony-workflow sync-decomposition <key>`
   (ADLC-47) turns an already-reviewed `decomposition.json` into real Jira
   Story/Subtask issues; it does not re-run the decompose agent. See
   `docs/plans/0001-symphony-poc/spec.md`'s "CLI control plane" section for design detail.

## Branch naming — required, not optional

Every workspace's branch is `{ISSUE-KEY}-{slug-of-summary}` (built by
`workspace.py`'s `branch_name()`), e.g.
`ADLC-24-poller-live-verify-add-a-version-constant`. **The branch must start
with the issue key** — Jira's Development-panel auto-linking depends on it.
If you're doing manual dev work on a ticket outside the automated Plan/Implement
path, use the same pattern by hand:
```bash
git checkout -b ADLC-24-poller-live-verify-add-a-version-constant
```
Confirmed 2026-09-24: the GitHub-for-Jira app is now installed and connected
(`ADLC-30`, Done) — `repository.byInstanceType` is populated where it used to be
`{}`. **Branch-level linking is still unverified**, though: nothing has actually
pushed a correctly-named branch to GitHub since the app connected (Plan-phase
worktrees never commit), so `branch.byInstanceType` hasn't been exercised
end-to-end yet. Don't assume the Development panel populates until a real push
confirms it.

## Safety invariants — don't "helpfully" remove these

- **The Plan/Implement human gate is enforced in `RunStore`** (`RunTransitionError`),
  not just in the API layer — Implement is unreachable without a `plan_ready`
  Run, on purpose.
- **Workspace isolation**: every agent dispatch runs only inside a validated
  per-issue git worktree under one root (`workspace.py`'s `_validate`) —
  Symphony §9.5's invariants, adopted deliberately.
- **Never `claude -p`.** This repo's Agent SDK usage exists specifically to
  avoid that failure mode — see `docs/plans/0001-symphony-poc/spec.md`'s "Why Claude Agent SDK" section
  and CAS's `feedback_no_claude_p_in_repo` memory.
- **The poller auto-Plans, never auto-Implements.** See point 2 above — this
  is the load-bearing safety property of Phase 5, not an oversight to "fix."

## Known limitations — work around, don't rediscover

- **`RunStore` is in-memory only.** A poller-created Run's `session_id` does
  not survive a server restart — confirmed in practice (`docs/runs/ADLC-24/review.md`),
  not just theoretical. If you restart the server mid-flow, a `plan_ready`
  Run's Implement path is gone; the ticket's Jira state (comment, status)
  persists, the ability to resume that exact agent session does not.
- **`permission_mode="acceptEdits"` can't run Bash.** Implement writes a diff,
  it cannot run its own tests/lint or transition the ticket itself — it will
  say so honestly in its result text rather than claim false verification.
  **Always run `uv run pytest` + `uv run ruff check .` yourself** in the
  worktree before trusting an Implement result.
- **Each new worktree needs its own `uv sync --extra dev`** before its tests
  will run — worktrees don't share a venv with the main checkout.

## Portability

This repo can act as the orchestrator for a *different* repo/Jira project — see
`docs/PORTABILITY.md` for the two env vars that matter
(`SYMPHONY_WORKFLOW_TARGET_REPO`, `SYMPHONY_WORKFLOW_JIRA_PROJECT`) and what's still
hardcoded (the prompt templates — Symphony's per-repo `WORKFLOW.md` model isn't
implemented yet).
