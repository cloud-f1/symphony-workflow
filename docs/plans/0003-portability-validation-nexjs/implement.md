# Implement/Record — ADLC-41/42: portability validation against ai-coding-nexjs-template

A build/run log, not a forward-looking plan — this documents what was actually done and found.

## Setup (ADLC-41)

Dedicated Jira project `NXJS` (`ADLC-40`, team-managed Kanban matching `ADLC`'s own style — see
`CLAUDE.md`'s "Jira API gotchas" for the project-creation trap hit getting there). A one-off
`.env.nxjs` (gitignored, `.env.*` pattern) with `JIRA_AGENT_CLI_JIRA_PROJECT=NXJS`,
`JIRA_AGENT_CLI_TARGET_REPO=/Users/cloud-f1/Documents/git_saas/ai-coding-nexjs-template`,
`JIRA_AGENT_CLI_WORKSPACE_ROOT` pointed at that repo's own `.worktrees` — no full server stood
up; `uv run --env-file .env.nxjs jira-agent plan <key>` is sufficient for one ticket, matching
`docs/PORTABILITY.md`'s adoption steps. The existing `ADLC`-project instance on port 8811 was
never touched or restarted.

Test ticket `NXJS-1`: "Audit `next-app/lib/rate-limit.ts` for correctness" — a genuine, bounded,
read-only exploratory task (not a fabricated/pre-known bug), matching `ai-coding-nexjs-template`'s
own established pattern of code-review-driven phases (its `docs/context/session-summary.md`'s
Phase 89/90). Two candidate leads from that repo's own `docs/context/epic-progress.md` ("Next
Action" section) were checked live first and found stale/already-fixed (`make hook-test` is 9/9
green, not "5/9 紅"; migration file count matches the journal, no drift) — worth noting that repo's
own handover doc can go stale too, the same lesson `ADLC-44`'s own retry-loop bug taught about
this repo.

## What happened

`jira-agent plan NXJS-1` failed twice with `error_max_turns` at the default
`DEFAULT_PLAN_MAX_TURNS=5` — `ai-coding-nexjs-template` is a large, mature codebase (90 phases)
needing more exploration than jira-agent-cli's own smaller repo typically does. A one-off direct
call to `agent_runner.run_plan(..., max_turns=15, max_budget_usd=3.0)` succeeded and produced a
genuinely strong result (see `docs/runs/NXJS-1/plan.md` for the full report): a real, exploitable
TOCTOU race in `actions/auth.ts::loginAction` (concurrent login requests bypass the stated rate
limit because the check and the record straddle a DB lookup + bcrypt compare), plus two smaller
findings (a doc-comment overclaim in `sweep()`, a stale wiring table). The plan correctly read and
cross-referenced that repo's own prior epics (E355/E360/E370), its own test file
(`lib/rate-limit.test.ts`), and its own docs (`docs/deployment/rate-limiting.md`) — not fabricated,
not generic, not shaped by jira-agent-cli's own codebase.

## Judgment (ADLC-42): does the plan read the target repo's own conventions?

**Yes, clearly.** The plan's *content* is 100% specific to `ai-coding-nexjs-template` — real line
numbers, real epic cross-references only that repo's own history would know about, real citations
of its own test/docs files. This directly answers ADLC-42's core question in the positive.

**Nuance on `docs/PORTABILITY.md`'s pre-existing worry** ("the plan an agent writes will be shaped
by jira-agent-cli's own house style, not the target repo's"): confirmed *partially* true but less
of a problem than feared. The plan's **structure** (Context → What's correct → Findings → Risks →
Verification) does come from `prompts.PLAN_PROMPT_TEMPLATE`'s own generic shape, not something
`ai-coding-nexjs-template` asked for — but a consistent structural shell around genuinely
repo-specific content isn't a real defect for a read-only audit-style ticket; it's arguably a
feature (predictable review shape). The `WORKFLOW.md` gap `PORTABILITY.md` already flags stays
real for anything needing target-repo-specific *process* (that repo's own `/athena:*` pipeline,
its QA-before-commit ordering, its own branch-naming rules) rather than just *content quality* —
this test didn't exercise Implement, where process divergence would matter more. Still deferred,
now with a concrete data point instead of a hypothetical one.

## Two real bugs this surfaced (not hypothetical — reproduced)

1. **`DEFAULT_PLAN_MAX_TURNS=5` (`agent_runner.py`) is tuned for jira-agent-cli's own repo size,
   not real-world target repos.** Confirmed: failed deterministically at 5 turns against
   `ai-coding-nexjs-template` twice, succeeded at 15. No CLI/API surface currently exposes an
   override — `execute_plan()`'s signature has no `max_turns` passthrough at all. See `ADLC-55`.
2. **`AgentRunResult.text` can be empty even when `result.structured` is fully populated** — the
   NXJS-1 session's final turn returned only structured JSON with no accompanying `TextBlock`, so
   `_consume()`'s `text_parts` stayed `[]`. `execute_plan()` unconditionally writes `result.text`
   to `plan.md` and posts it as the Jira comment — **this would have silently produced a blank
   plan.md and a blank Jira comment** had this run gone through the normal `execute_plan()` path
   instead of a one-off direct `run_plan()` call. Recovered by hand this time only because
   `result.structured['summary']` had a decent paste-ready summary and the full report was still
   sitting in Claude Code's own `~/.claude/plans/*.md` scratch file. See `ADLC-56`.

## Files touched

None in `jira-agent-cli`'s own codebase by this run itself (read-only ticket) — `docs/runs/NXJS-1/
plan.md` and this folder are the only new files, plus `.env.nxjs` (gitignored, not committed).

## Verification

`NXJS-1` moved to "In Review" in Jira with the findings posted as a (wiki-markup-safe) comment.
`ADLC-41`/`ADLC-42` transitioned to Done on the strength of this real run, not a simulated one.
