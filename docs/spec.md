# Spec — design & approach

See [`intent.md`](intent.md) for why this exists.

## Component mapping (Symphony → this POC)

| Symphony component | This POC | Status |
|---|---|---|
| Issue Tracker Adapter | `jira_agent_cli.tools` / `.client` | ✅ already built, live-verified |
| `WORKFLOW.md` (policy-as-code) | new `docs/WORKFLOW.md` | new, small |
| Agent Runner (Codex app-server) | Claude Agent SDK session, not `claude -p` | new |
| Orchestrator (poll/dispatch/retry) | **out of scope** — a human calls the API once per ticket | intentionally cut |
| Workspace Manager + §9.5 safety invariants | per-issue git worktree under `.worktrees/<key>/`, cwd validated | new, adopted as-is (cheap, high value) |
| §13.7 OPTIONAL HTTP Server Extension | new FastAPI app | new — this *is* the "API server" ask |
| Human Review gate (Step 3 in Symphony's example workflow) | a hard state-machine gate between `/plan` and `/implement` | new — this *is* the "interface" ask |

## Why Claude Agent SDK, not `claude -p`

Symphony's Agent Runner (§10) assumes a persistent app-server subprocess with
thread/turn semantics it can stream and supervise turn-by-turn. `claude -p` is the
closest single-shot analog, but this org's own memory already recorded the failure mode:
headless Claude spawned without supervision (war-room's "Execute Next") ran unattended
and had to be de-clawed. The **Claude Agent SDK** (`claude-agent-sdk` on PyPI, confirmed
installed — v0.2.159, `pip install claude-agent-sdk`) gives the same turn-by-turn control
Symphony wants from an app-server, while keeping every session start bound to an
explicit API call this spec defines, never a background poll.

**Confirmed API surface** (verified live against `code.claude.com/docs/en/agent-sdk`,
2026-09-24 — this section replaces earlier speculation):

```python
async def query(
    *, prompt: str, options: ClaudeAgentOptions | None = None
) -> AsyncIterator[Message]
```

`ClaudeAgentOptions` fields this POC uses: `model`, `cwd` (the per-issue worktree),
`permission_mode`, `max_turns`, `resume` (session id, for Implement to continue the
Plan phase's thread), `can_use_tool` (a permission callback for finer control than
`permission_mode` alone gives).

**The key find:** `permission_mode="plan"` is a **built-in** SDK mode — "explore
without editing" — that maps directly onto the read-only Plan-phase requirement below,
better than the custom tool-budget split this spec originally proposed. Implement uses
`permission_mode="acceptEdits"` (or `"default"`) instead. The five modes:
`default` / `acceptEdits` / `plan` / `dontAsk` / `bypassPermissions` / `auto`
(model-classifier-approved).

Custom tools (e.g. exposing `jira_agent_cli.tools`'s registry to the agent directly)
register via the `@tool` decorator + `create_sdk_mcp_server(...)` (in-process MCP
server) — deferred for this POC; the orchestrator fetches the issue and injects it into
the prompt instead, which is simpler and enough for Phase 2/3.

**Implement-phase permission gap — found live, 2026-09-24 (`ADLC-17`,
[review](runs/ADLC-17/review.md)):** `permission_mode="acceptEdits"` allows
Write/Edit tool calls but **not** Bash. The Implement phase can write a correct diff
but cannot run tests/lint/the Jira CLI itself to verify it — it can only report
honestly that it didn't. This POC's human-review step covers that gap manually; a
non-POC version needs either a narrow `can_use_tool` Bash allowlist (`pytest`,
`ruff check`) or a separate verification pass after Implement, not an assumption
that `acceptEdits` implies "can finish the job."

**Host environment inheritance — also found live:** a Claude Agent SDK session
inherits the full host Claude Code environment (skills, configured MCP servers)
unless explicitly restricted via `mcp_servers`/`setting_sources`. Fine for this POC
(same trust boundary as the operator); would need locking down before running
anywhere less trusted.

**Structured output (`output_format`) — spiked 2026-09-24 (`ADLC-28`):** `ClaudeAgentOptions`
also has `output_format: dict[str, Any] | None`, e.g. `{"type": "json_schema", "schema":
{...}}`; the matching result lands on `ResultMessage.structured_output: Any = None`. The
open question going in was whether a forced output schema plays well with Plan phase's
exploratory multi-turn tool-use loop (`permission_mode="plan"`, several Read/Write/ToolSearch
turns before a final answer) — that interaction, not basic SDK support, was the unconfirmed
part. A throwaway spike (prompt shaped like `PLAN_PROMPT_TEMPLATE`, a 3-field schema:
`summary`/`files_changed`/`risks`) confirmed it works cleanly: the agent ran its normal
multi-turn explore loop (Read, Read, Write, ToolSearch tool calls observed), then emitted a
final structured-output turn, and `ResultMessage.structured_output` came back as a `dict`
matching the schema exactly, with `is_error: False`. On the strength of that, `run_plan()`
in `agent_runner.py` now always sets `output_format={"type": "json_schema", "schema":
PLAN_OUTPUT_SCHEMA}` (`prompts.py`), and `AgentRunResult.structured` / `Run.plan_structured`
carry it alongside (not instead of) the existing free-text `result.text` / `Run.plan` that
`orchestrator.py` still writes to `plan.md` and the Jira comment. `run_implement()` does not
set `output_format` — Implement stays free-text. This ticket only plumbs the field through;
using `plan_structured` to actually change what's rendered/posted, or growing the schema into
a full Epic/Story/Task decomposition, is `ADLC-46`'s scope, not this one's.

## The two-phase interface

```
POST /runs                {issue_key}         → creates a Run (state: created)
POST /runs/{id}/plan                          → state: planning → plan_ready
                                                 (permission_mode="plan" — read-only;
                                                  writes plan to a Jira comment +
                                                  docs/runs/<key>/plan.md; touches NO
                                                  repo files; records the SDK session_id)
[ human reads the plan, approves out-of-band ]
POST /runs/{id}/implement                     → REQUIRES state == plan_ready (hard gate)
                                                 state: implementing → completed|failed
                                                 (permission_mode="acceptEdits", resumes
                                                  the plan phase's session_id, scoped to
                                                  .worktrees/<key>/; writes code+tests)
GET  /runs/{id}                               → current state + artifacts produced
GET  /tools                                   → jira-agent-cli's existing TOOLS registry
POST /tools/{name}/call                       → direct tool-call passthrough (agent-callable)
```

Run states are a 5-value subset of Symphony's 11-state lifecycle (§7.2) — just enough to
enforce the human gate, not a full retry/reconciliation model.

## Workspace safety (adopted from §9.5 as-is)

- Workspace root: `.worktrees/` (gitignored, same convention this org's `task-workflow.md`
  already uses).
- Per-issue path: `.worktrees/<sanitized-key>/`, created via `git worktree add`.
- Before any agent dispatch: validate `cwd == workspace_path` and `workspace_path` is
  inside the workspace root — Symphony's Invariant 1/2, directly portable, cheap insurance.

## What's reused vs new

- **Reused unchanged:** `client.py`, `tools.py`, `config.py`, `models.py`, `cli.py` — the
  whole existing tracker-adapter layer.
- **New:** `src/jira_agent_cli/api.py` (FastAPI), `src/jira_agent_cli/agent_runner.py`
  (Claude Agent SDK wrapper + workspace manager), `src/jira_agent_cli/runs.py` (the small
  state machine, in-memory store — no DB for a POC), `docs/WORKFLOW.md` (prompt template
  + phase policy, Symphony-style but simplified).

## Phase 5 — auto-discovery poller (decided + built + live-verified 2026-09-24)

Deferred earlier in this POC ("no polling daemon" was a stated non-goal); revisited
once Phases 0–4 proved the Plan/Implement loop works. Resolved the apparent tension
by splitting *discovery* from *code-writing*: automating "notice a new ticket and
write a plan" is safe (read-only, bounded cost); automating "write a plan → write
code with nobody watching" is not — that's exactly the failure mode `feedback_no_claude_p_in_repo`
warns about, and `ADLC-17` proved why (`acceptEdits` can't self-verify).

- **Mechanism**: a background `asyncio` task in `api.py`'s `lifespan` (Symphony §8's
  actual poll-tick pattern), not `/loop` — a real daemon independent of any open
  terminal. `orchestrator.py` was extracted so the poller and the HTTP endpoint call
  the exact same Plan/Implement logic and can't drift.
- **Ready signal**: `status = "To Do"` on the ADLC board (board default, not a
  label) — decided over a label because the dedicated ADLC project (not CF1) already
  is the filter; see `docs/intent.md`'s Tracking table.
- **Scope**: auto-creates a `Run` + auto-runs `/plan` only. `/implement` is
  unchanged — still a deliberate `POST` a human makes.
- **`GET /issues/{key}/run`**: added because the poller creates Runs internally;
  nothing returned their id to a human until this lookup endpoint existed.
- **Live-verified** (`ADLC-24`, [review](runs/ADLC-24/review.md)): a fresh "To Do"
  ticket was auto-discovered, auto-planned, and transitioned to "In Review" with
  zero manual API calls — confirmed against real Jira state directly, not just the
  app's own response.
- **Finding**: `RunStore`'s in-memory-only design was hit in practice — restarting
  the server lost the `session_id` a poller-created Run needed for `/implement`.
  **Reframed 2026-09-25**: this isn't a gap to close. Symphony §14.3 states the
  same in-memory design is *intentional*: *"restart recovery means the service
  can resume useful operation by polling tracker state and reusing preserved
  workspaces... It does not mean retry timers, running sessions, or live worker
  state survive process restart."* Our finding confirms Symphony's own spec is
  right about what breaks on restart, not that our POC cut a corner Symphony
  would have avoided. See the alignment matrix below.

## Symphony alignment matrix (2026-09-25)

What we adopted vs. deferred from `SPEC.md`, and where re-reading it after
building something ourselves changed how we'd frame it (not just "gaps" — some
sections turned out to already agree with what we'd built for other reasons).

| Symphony section | Status | Note |
|---|---|---|
| §4 Core Domain Model | Adopted (subset) | `Issue`/`SearchResult` models; no `blockers`/labels normalization yet |
| §5 `WORKFLOW.md` policy file | Deferred | `prompts.py` is still jira-agent-cli's own hardcoded templates, not a per-repo policy file — see `docs/PORTABILITY.md` |
| §7 Orchestration State Machine | Adopted (5-state subset) | `RunState` — just enough to gate Implement, not Symphony's full 11-state lifecycle |
| §8 Polling, Scheduling, Reconciliation | Adopted (poll only) | `poller.py` — no concurrency control (§8.3), no retry/backoff (§8.4), no reconciliation (§8.5); one ticket, one Run, no re-check loop |
| §8.6 Workspace Cleanup | Adopted (opt-in) | `orchestrator.cleanup_workspace_if_terminal`/`sweep_terminal_workspaces` — startup sweep (`api.py`'s `lifespan`) + on-transition check in `execute_plan`/`execute_implement`'s failure/success paths; off by default (`JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED`), checks *live* Jira status against `JIRA_AGENT_CLI_TERMINAL_STATUSES` (default `Done`), never force-removes a dirty worktree. No periodic re-sweep in `poller.py`'s tick — startup sweep + the two orchestrator hook points were judged sufficient for this POC; a future ticket could add one if "eventually" needs to mean "within a poll interval" rather than "next restart" |
| §9.4 Workspace Lifecycle Hooks | Adopted (minimal) | `workspace.py`'s `WorkspaceHooks`/`WorkspaceManager.run_*` — one optional shell command per hook point (`after_create`/`before_run`/`after_run`/`before_remove`), configured via `JIRA_AGENT_CLI_HOOK_*` env vars (this repo's existing convention, not a WORKFLOW.md policy file — see §5's row below), workspace path + issue key passed as env vars, `hooks.timeout_ms` default 60000 matching SPEC.md. Best-effort: a failing/timed-out hook is logged, never raised |
| §9.5 Workspace Safety Invariants | **Adopted as-is** | `workspace.py`'s `_validate` — cwd-inside-root + sanitized keys, taken directly |
| §10 Agent Runner Protocol (Codex app-server) | **Not applicable** | We use the Claude Agent SDK, a different protocol entirely — `agent_runner.py` is our own contract, not §10's |
| §11 Issue Tracker Integration Contract | Adopted (Jira only) | `tools.py`'s registry plays the adapter role; no multi-tracker abstraction |
| §13.3/13.7 Runtime Snapshot / HTTP API | Partially adopted | `GET /health` exists; `GET /api/v1/state` / `GET /api/v1/issues/{key}` are Epic 2's first wave (`ADLC-32`) |
| §14.1–14.2 Failure Model | Adopted (partial) | Per-run failure → `mark_failed`; no exponential backoff/retry queue (§14.2's "Worker failures: convert to retries") |
| §14.3 Partial State Recovery | **Already agreed** | See the Finding above — same intentional in-memory design, discovered independently before re-reading this section |
| §15 Security and Operational Safety | Adopted (partial) | Workspace safety yes; no documented sandbox/approval posture beyond `permission_mode` |

See [`plan.md`](plan.md) for the phased build order.
