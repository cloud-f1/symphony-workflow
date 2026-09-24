---
jira_url: https://cloud-f1.atlassian.net/browse/ADLC-29
---

I'll wait for both exploration agents to finish before continuing with the design phase.
I'll wait for the two background exploration agents to finish naturally (harness will notify me) rather than polling.
## Summary: current poller data flow

`JQL search (READY_JQL)` → `for issue in result.issues` → skip if seen/existing Run → `RunStore.create` → `execute_plan(run)` (Jira fetch → workspace ensure → Claude Agent SDK `run_plan` in `permission_mode="plan"` → write plan.md → add Jira comment → transition to "In Review").

### 1. `src/jira_agent_cli/poller.py` (full file, 123 lines)
- **JQL** (line 43-46): `project = {JIRA_PROJECT} AND status = "To Do" AND issuetype not in (Epic, Subtask) ORDER BY created ASC`. `JIRA_PROJECT` from env `JIRA_AGENT_CLI_JIRA_PROJECT` (default `"ADLC"`).
- **Interval**: `DEFAULT_POLL_INTERVAL_SECONDS = 60.0` (line 34), passed into `Poller.__init__` as `interval_seconds`; `_loop()` (line 90-96) does `await self.tick(); await asyncio.sleep(self._interval)` in an infinite `while True`. Only `JiraError` from `tick()` is caught at the loop level (others would propagate — but `tick()` itself already narrows failures per-ticket).
- **`tick()`** (line 98-122): calls `client.search_issues(READY_JQL, max_results=25)`, then for each issue: dedupe via in-memory `self._seen: set[str]` (line 68) **and** `self._runs.get_by_issue_key(issue.key)`; if new, `runs.create(issue.key)` then `await execute_plan(run, runs=..., client=..., workspaces=...)` wrapped in `try/except _PLAN_FAILURE_TYPES` (JiraError, RunTransitionError, WorkspaceError, AgentRunError) — logs and continues, never crashes the tick.
- **State tracked between polls**: only the in-memory `_seen` set (belt-and-suspenders on top of `RunStore`, which is also in-memory — see runs.py). Nothing persists across process restart.
- Never calls `execute_implement` — Implement is explicitly out of scope for the poller (see module docstring, lines 1-13): "auto-discovers and auto-Plans new tickets, but never auto-Implements."

### 2. `src/jira_agent_cli/orchestrator.py`
- `execute_plan(run, *, runs, client, workspaces) -> Run` (line 45-95): fetches issue, `runs.mark_planning`, `workspaces.ensure(issue_key, branch_slug=summary)` (creates a git worktree), renders plan prompt, calls `agent_runner.run_plan` (a Claude Agent SDK turn), writes `docs/runs/<key>/plan.md`, posts a Jira comment, `runs.set_plan_ready(...)`, best-effort `client.transition_issue(key, "In Review")`. Read-only re: repo (plan mode doesn't edit).
- `execute_implement(run, *, runs, client) -> Run` (line 98-129): gated on `run.state == PLAN_READY`; resumes the Plan's session via `run_implement(..., resume_session_id=run.session_id)` with `acceptEdits`; not called by poller at all.

### 3. `src/jira_agent_cli/agent_runner.py`
- Wraps `claude_agent_sdk.query()`. `run_plan` (line 61-77) uses `ClaudeAgentOptions(cwd, model, permission_mode="plan", max_turns=5, max_budget_usd=2.0)`. `run_implement` (line 80-98) uses `permission_mode="acceptEdits"`, `max_turns=20`, `resume=resume_session_id` (continues Plan's session). `_consume()` (line 37-58) drains the async stream, collects text/session_id/cost/turns, raises `AgentRunError` on `ResultMessage.is_error`.

### 4. `src/jira_agent_cli/config.py`
Only Jira auth env vars: `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (required), plus optional `JIRA_FIELD_EPIC_LINK`/`JIRA_FIELD_SPRINT`/`JIRA_FIELD_STORY_POINTS`. No poller-specific config here — poller/orchestrator env vars (`JIRA_AGENT_CLI_JIRA_PROJECT`, `JIRA_AGENT_CLI_TARGET_REPO`, `JIRA_AGENT_CLI_RUNS_DOCS_ROOT`, `JIRA_AGENT_CLI_WORKSPACE_ROOT`) live directly in poller.py (line 39) and orchestrator.py (lines 28-30) via `os.environ.get`, not in this dataclass.

### 5. `src/jira_agent_cli/runs.py`
`RunStore` is **in-memory only** (dict `self._runs`, line 55), explicitly called out (line 46-52) as not surviving a process restart — a POC simplification. `Run` fields (line 24-33): `id`, `issue_key`, `state` (5-state enum: created/planning/plan_ready/implementing/completed/failed), `created_at`, `plan`, `session_id`, `workspace_path`, `result`, `error`.

### 6. `src/jira_agent_cli/tools.py`
Yes — `search_issues` tool (line 122-127) wraps `client.search_issues(jql, max_results)`, directly reusable by a reasoning poll turn. Full tool list: `get_issue` (fetch by key), `search_issues` (JQL search), `create_issue` (new issue), `add_comment`, `transition_issue` (workflow transition by name), `get_myself` (auth check), `create_remote_link` (web link to external URL, e.g. merged plan.md).

### 7. `tests/test_poller.py` (192 lines)
Tests `Poller.tick()` directly (no real asyncio timing), respx-mocked HTTP, `orchestrator.run_plan` monkeypatched. Covers: (a) dispatches Plan for one new ticket and reaches `PLAN_READY`; (b) skips a ticket already dispatched on second tick (idempotency via `_seen`); (c) one ticket's `run_plan` raising `AgentRunError` doesn't block the other ticket in the same batch; (d) `start()`/`stop()` lifecycle manage the background task; (e) `start()` is idempotent (second call doesn't replace running task). New reasoning-turn tests would need to cover: triage decisions (which tickets get planned vs skipped/flagged), malformed-ticket detection, and how the reasoning turn's own tool calls (e.g. `search_issues`) get exercised/mocked instead of a single fixed JQL search.
First exploration agent finished. Waiting on the second (design docs / cost concerns) before drafting the plan.
I'll wait for the second background agent (docs/spec.md, docs/intent.md, docs/plan.md, api.py, skill file) to complete before proceeding to the design phase.
I have everything needed. Report below.

## Findings for ADLC-29 planning

**1. docs/spec.md — Phase 5 (lines 109–163)**
- Line 109: `## Phase 5 — auto-discovery poller (decided + built + live-verified 2026-09-24)`
- Design intent (111–116): "Deferred earlier in this POC... Resolved the apparent tension by splitting *discovery* from *code-writing*: automating 'notice a new ticket and write a plan' is safe (read-only, bounded cost); automating 'write a plan → write code with nobody watching' is not."
- Mechanism (118–121): background `asyncio` task in `api.py`'s `lifespan`, not `/loop`; logic lives in `orchestrator.py` so poller and HTTP endpoint can't drift.
- Ready signal (122–124): plain `status = "To Do"` on the ADLC board — this is exactly the mechanism ADLC-29 proposes replacing.
- Safety property (125–126): "Scope: auto-creates a `Run` + auto-runs `/plan` only. `/implement` is unchanged — still a deliberate `POST` a human makes." Alignment table row (154): "`poller.py` — no concurrency control (§8.3), no retry/backoff (§8.4), no reconciliation (§8.5); one ticket, one Run, no re-check loop."
- No existing cost-concern text in spec.md itself for *richer* polling — that language lives in the session-summary doc (below).

**2. docs/intent.md**
- Tracking table (lines 51–58): Phase 5 row: `5 — auto-discovery poller | ADLC-18 | ADLC-19/20/21/22/23 | Done (real ticket: ADLC-24)`.
- Lines 60–63: "Continuous polling revisited 2026-09-24... resolved by splitting discovery from code-writing — the poller auto-runs Plan only, never Implement."
- Non-goals section (26–33) originally excluded polling entirely ("No 24/7 polling daemon... that's the expensive, low-learning part"), later reversed for Plan-only in Phase 5.
- No explicit Epic 2/POC cost-per-tick language in intent.md — that's in the session-summary file, not here.

**3. docs/plan.md** — stops at Phase 4 (never updated for Phase 5/Epic 2); no explicit "richer polling" roadmap item or phase-number placeholder for ADLC-29 here. The actual roadmap tracking has moved to `docs/context/session-summary.md`.

**4. docs/context/session-summary.md (the real source for Epic 2 status/cost context)** — this is where ADLC-29 is explicitly named:
- Line 29: `| ADLC-28/ADLC-29 | Backlog, unsized: structured plan output, richer (Claude-Code-turn) polling | Tracked, not scoped |`
- Epic 2 (`ADLC-27`) table (18–29) shows ADLC-32 (Interface API) Done, ADLC-39 (portability validation) is next up, ADLC-43 (plans convention) To Do. ADLC-29 sits below all of these as unscoped backlog — confirming it's "the clearest next-big-feature candidate once Epic 2's first wave ships."
- No pre-existing cost/budget number is documented anywhere in the repo for Agent SDK calls — the "cost per tick goes way up" framing appears to originate in the ticket itself, not in prior docs.

**5. api.py endpoints** (`src/jira_agent_cli/api.py`):
- `GET /health` (109) — Jira connectivity + poller running status
- `GET /tools` (124), `POST /tools/{name}/call` (130) — tool registry passthrough
- `GET /issues/{key}` (144)
- `POST /runs` (152), `GET /runs/{run_id}` (157), `GET /issues/{key}/run` (165) — lookup by issue key
- `POST /runs/{run_id}/plan` (176) — read-only agent turn
- `POST /runs/{run_id}/implement` (198) — gated on `plan_ready`
- `GET /api/v1/state` (258), `GET /api/v1/issues/{key}` (278) — Epic 2 "interface API" (ADLC-32)
- `_poll_enabled()` (40–43) reads `JIRA_AGENT_CLI_POLL_ENABLED` (default `"true"`); `lifespan` (47) wires `interval_seconds=os.environ.get("JIRA_AGENT_CLI_POLL_INTERVAL_SECONDS", "60")`. No existing endpoint to change poll behavior/JQL at runtime — a richer-poll feature would need a new toggle/config surface here.

**6. .claude/skills/jira-agent-cli-ops/SKILL.md**
- Start: `uv run uvicorn jira_agent_cli.api:app --port 8811`; `GET /health` reports poller status.
- Line 36–40: "Auto-discovery (the poller): any ticket with `status = 'To Do'`... gets auto-Planned... **It never auto-Implements.** That's not a missing feature, it's the whole point... don't 'complete' it by wiring Implement into the poller."
- Line 73–74 (Safety invariants): "The poller auto-Plans, never auto-Implements... this is the load-bearing safety property of Phase 5, not an oversight to 'fix.'" — directly relevant guardrail ADLC-29 must preserve even with richer reasoning.

**7. docs/PORTABILITY.md** — env vars a new feature must respect: `JIRA_AGENT_CLI_TARGET_REPO` (line 14), `JIRA_AGENT_CLI_JIRA_PROJECT` (line 15, replaces `poller.py`'s hardcoded `project = ADLC`), `JIRA_AGENT_CLI_WORKSPACE_ROOT` (37). Confirmed in `poller.py` itself: `JIRA_PROJECT = os.environ.get("JIRA_AGENT_CLI_JIRA_PROJECT", "ADLC")` and `READY_JQL` built from it, excluding Epic/Subtask issue types.

**8. config.py / test_config.py** — `src/jira_agent_cli/config.py` only handles `JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`/custom-field env vars; no poll-related config lives there. `tests/test_config.py` does not exist in this repo (only `tests/test_poller.py`). Poll-tuning env vars (`JIRA_AGENT_CLI_POLL_ENABLED`, `JIRA_AGENT_CLI_POLL_INTERVAL_SECONDS`) are read directly in `api.py`, not centralized in `config.py` — worth noting since ADLC-29 will likely add more such env vars and may want to consolidate them into `config.py` instead of following the existing scattered pattern.
I've completed the design pass for ADLC-29. The full plan is written to the plan file; here it is inline as well.

## ADLC-29 — Richer polling via an actual Claude Code turn

### Context
`poller.py` currently discovers work with one blunt JQL filter (`status = "To Do"`, `poller.py:43-46`) polled every 60s, and blanket-dispatches `orchestrator.execute_plan` for every unseen ticket (`poller.py:98-122`). That's cheap and safe but dumb — it can't tell a well-formed ticket from a placeholder, can't prioritize, and can't spot duplicates. ADLC-29 wants a reasoning step instead, but flags the real risk itself: a full Claude Agent SDK turn is much more expensive than a JQL call, so "just call the SDK from the poll loop" isn't a design — this is that design pass, meant as the Jira comment/attached artifact for whoever picks this up (after Epic 2's first wave, per the ticket).

### Recommended approach
Keep the cheap JQL poll for liveness/dedupe exactly as-is. Add a **separate, throttled triage step**:

1. **Poll tick stays cheap** — same JQL + `_seen`/`RunStore` dedupe, but new issues go into a small in-memory "untriaged" queue instead of an immediate `execute_plan` call.
2. **A slower triage cadence** (new `JIRA_AGENT_CLI_TRIAGE_INTERVAL_SECONDS`, e.g. 900s, deliberately >> poll interval) drains that queue and, only if non-empty, makes **one** Claude Agent SDK turn for the whole batch — not one per ticket. The turn gets a compact JSON snapshot (key/summary/description/type/created), no live Jira tool access, and returns structured decisions: `{issue_key, action: "plan"|"skip"|"flag", reason}`.
3. **Code applies the decision, not the agent** — `plan` → existing `execute_plan` runs unchanged; `flag` → orchestrator posts a Jira comment via the existing `add_comment` tool (no status transition — the board's workflow graph isn't known well enough to guess a target state); `skip` → recorded in a new small in-memory `TriageStore` (same POC pattern as `RunStore`) so it isn't re-reasoned-about every cycle while still `To Do`.
4. **Feature-flagged**: `JIRA_AGENT_CLI_TRIAGE_ENABLED` defaults `false`, preserving today's behavior until validated.

This bounds cost two ways: triage only fires on a slow cadence when there's new work, and it's one budget-capped turn (`max_turns=2`, `max_budget_usd=0.5`, mirroring `agent_runner.run_plan`'s existing pattern) per batch, not per tick or per ticket.

### Files that would change
- `poller.py` — split dispatch into enqueue vs. triage-driven action; add the triage cadence.
- `orchestrator.py` — new `execute_triage(candidates, *, client, triage_store)`, shared by poller and any future API endpoint (same pattern as `execute_plan`/`execute_implement`).
- `agent_runner.py` — new `run_triage(...)`, a third thin wrapper around `claude_agent_sdk.query()`.
- `prompts.py` — new `TRIAGE_PROMPT` defining "malformed" and the required JSON output shape.
- `models.py` — new `TriageDecision` pydantic model.
- New `TriageStore` (in-memory, alongside `runs.py`'s `RunStore`).
- `config.py` — good opportunity to stop scattering poll-related env vars across `poller.py`/`api.py` and centralize the new ones here (judgment call, not mandatory).
- Tests: `test_poller.py` (triage-path cases mirroring existing `run_plan` monkeypatching), `test_orchestrator.py::test_execute_triage`, `agent_runner.run_triage` malformed-JSON-fallback tests.

No `tools.py`/`client.py` changes needed — `add_comment`/`search_issues`/`get_issue` already exist as both client methods and registered tools.

### Risks / open questions
- **Non-determinism & idempotency**: reject decisions referencing issue keys outside the input batch; `TriageStore` prevents re-triaging.
- **Preserve "never auto-Implement"**: triage stays read-mostly and code-applied — the agent itself must not get `transition_issue`/`add_comment` tools directly.
- **What "flag" does to ticket status is unresolved** — needs live workflow-graph introspection before wiring any transition; left comment-only here.
- **Prioritization scope**: this plan only classifies plan/skip/flag, not reordering dispatch — decide separately if needed.
- **"via /loop, scoped" in the ticket text** could mean an external `/loop`-driven session rather than in-process. Recommendation is to stay in-process via `agent_runner.py` (consistent with CLAUDE.md's "Never `claude -p`"), but this should be confirmed with whoever files the build subtask.
- Per the ticket, don't build before Epic 2's first wave ships.

### Verification (once implemented)
- `uv run pytest tests/test_poller.py tests/test_orchestrator.py -k triage` (respx-mocked, `run_triage` monkeypatched with canned decisions).
- `uv run pytest --cov=jira_agent_cli` to confirm no regression when `JIRA_AGENT_CLI_TRIAGE_ENABLED=false`.
- Manual: enable triage against a scratch ticket with an empty description and confirm it gets a comment, not a silently-created Run.

---

**Jira comment summary:** Investigated ADLC-29 and wrote a design (no code yet, per the ticket's own "needs a design pass" note). Recommendation: don't turn every 60s poll tick into a Claude Agent SDK turn — keep the existing cheap JQL discovery loop as-is, and add a separate, much-slower-cadence "triage" step (new `execute_triage` in `orchestrator.py`, a new `run_triage` wrapper in `agent_runner.py`, feature-flagged via `JIRA_AGENT_CLI_TRIAGE_ENABLED`) that makes exactly one Claude turn per batch of newly-discovered tickets, with no live Jira tool access, to classify each as plan/skip/flag; the poller's existing code (not the agent) then applies that decision via `execute_plan` or a Jira comment. This preserves the poller's core safety property (auto-Plan only, never auto-Implement, side effects driven by code not by an unattended agent's own tool calls) while bounding cost to one small, budget-capped turn per batch rather than one per tick or per ticket. Open questions before implementation: what a "flag" should actually do to the ticket's status (needs live workflow introspection), whether prioritization should reorder dispatch (not just classify), and confirming in-process `agent_runner` is the right mechanism vs. the ticket's literal "(via /loop)" phrasing. Per the ticket, actual build-out should wait until Epic 2's first wave ships.