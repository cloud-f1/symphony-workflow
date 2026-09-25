# Spec — design & approach

See [`intent.md`](intent.md) for why this exists.

## Component mapping (Symphony → this POC)

| Symphony component | This POC | Status |
|---|---|---|
| Issue Tracker Adapter | `symphony_workflow.tools` / `.client` | ✅ already built, live-verified |
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

Custom tools (e.g. exposing `symphony_workflow.tools`'s registry to the agent directly)
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
GET  /tools                                   → symphony-workflow's existing TOOLS registry
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
- **New:** `src/symphony_workflow/api.py` (FastAPI), `src/symphony_workflow/agent_runner.py`
  (Claude Agent SDK wrapper + workspace manager), `src/symphony_workflow/runs.py` (the small
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
  is the filter; see `intent.md`'s Tracking table.
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

## CLI control plane + decomposition (ADLC-45/46/47, 2026-09)

A fourth way to drive a ticket, alongside `POST /runs/.../plan|implement`, the poller, and
`symphony-workflow call`: `cli.py`'s `plan`/`implement`/`decompose`/`sync-decomposition` commands
run the same orchestration logic in-process, no `uvicorn` server required.

- **`symphony-workflow plan/implement <key>`** (ADLC-45) call `orchestrator.execute_plan`/
  `execute_implement` directly — the exact functions `api.py`'s HTTP handlers and
  `poller.py` already share, so this doesn't fork the Plan/Implement logic a third way.
  The one wrinkle: `RunStore` is in-memory and process-scoped (§14.3 above), and the CLI
  is a separate short-lived process per invocation, so `plan` can't hand its `Run` to a
  later `implement` call the way the long-lived API process can. `cli.py` works around
  this with a small sidecar — `plan` writes `Run.model_dump_json()` to
  `docs/runs/<key>/run.json` next to the `plan.md` it already renders, and `implement`
  reloads it via `RunStore.load`. This is a CLI-process concern only, not a change to
  `RunStore`/`runs.py` itself.
- **`symphony-workflow decompose <key>`** (ADLC-46) breaks a Jira Epic into Stories, each with a
  user story, acceptance criteria, and Tasks, for human review before any Jira issue
  exists. Deliberately **not** routed through `runs.Run`/`RunStore` — that state machine
  models one issue's Plan → Implement lifecycle, which a one-shot Epic decomposition has
  no equivalent of. Instead it's Plan-phase-shaped: `agent_runner.run_plan()`'s
  `permission_mode="plan"` (read-only exploration) + `output_format` wiring, the same
  mechanism `execute_plan()` uses, just with a new `prompts.PLAN_DECOMPOSE_SCHEMA` in
  place of `PLAN_OUTPUT_SCHEMA` — the direct next use of the `output_format` foundation
  ADLC-28 landed (see above). No repo writes, no Jira writes; renders
  `docs/runs/<key>/decomposition.md` (human review) and `decomposition.json` (the
  structured seam `sync-decomposition` reads). A `definition_of_done` checklist is
  templated in `decompose.py` from this repo's own CLAUDE.md standards, not left to the
  LLM to invent per run.
- **`symphony-workflow sync-decomposition <key>`** (ADLC-47) reads an already-reviewed
  `decomposition.json` — never the Markdown, and never re-running the decompose agent —
  and creates the real issues: one Story per decomposed story (`parent=<epic-key>`) then
  one Subtask per that Story's tasks (`parent=<story-key>`), both via the existing
  `client.create_issue(..., parent=...)` path (the standard-`parent`-field mechanism
  team-managed projects like `ADLC` use, per the "Jira API gotchas" section) — no new
  Jira operation, no client.py/tools.py change. Created Stories land in Jira's default
  "To Do" state untouched, so the poller/manual Plan+Implement flow picks them up
  unchanged; created Subtasks are excluded from the poller's `READY_JQL` like every other
  Subtask this repo creates for its own tracking, the same way ADLC-44's own subtasks are.

## Symphony alignment matrix (2026-09-25)

What we adopted vs. deferred from `SPEC.md`, and where re-reading it after
building something ourselves changed how we'd frame it (not just "gaps" — some
sections turned out to already agree with what we'd built for other reasons).

| Symphony section | Status | Note |
|---|---|---|
| §4 Core Domain Model | Adopted (subset) | `Issue`/`SearchResult` models; no `blockers`/labels normalization yet |
| §5 `WORKFLOW.md` policy file | Deferred | `prompts.py` is still symphony-workflow's own hardcoded templates, not a per-repo policy file — see `docs/PORTABILITY.md` |
| §7 Orchestration State Machine | Adopted (5-state subset) | `RunState` — just enough to gate Implement, not Symphony's full 11-state lifecycle |
| §8 Polling, Scheduling, Reconciliation | Adopted (poll + bounded retry) | `poller.py` — no concurrency control (§8.3); §8.4 partially adopted 2026-09-24: a failed auto-Plan is retried up to `SYMPHONY_WORKFLOW_POLL_MAX_PLAN_ATTEMPTS` (default 3) at the normal poll cadence (crude backoff, not exponential), then gives up with a Jira comment — found live (`ADLC-44` hit `error_max_turns` and was silently stuck forever before this); no reconciliation (§8.5); one ticket, one Run per issue key (reused across retries via `RunStore.reset_for_retry`, not one-Run-per-attempt) |
| §8.6 Workspace Cleanup | Adopted (opt-in) | `orchestrator.cleanup_workspace_if_terminal`/`sweep_terminal_workspaces` — startup sweep (`api.py`'s `lifespan`) + on-transition check in `execute_plan`/`execute_implement`'s failure/success paths; off by default (`SYMPHONY_WORKFLOW_WORKSPACE_CLEANUP_ENABLED`), checks *live* Jira status against `SYMPHONY_WORKFLOW_TERMINAL_STATUSES` (default `Done`), never force-removes a dirty worktree. No periodic re-sweep in `poller.py`'s tick — startup sweep + the two orchestrator hook points were judged sufficient for this POC; a future ticket could add one if "eventually" needs to mean "within a poll interval" rather than "next restart" |
| §9.4 Workspace Lifecycle Hooks | Adopted (minimal) | `workspace.py`'s `WorkspaceHooks`/`WorkspaceManager.run_*` — one optional shell command per hook point (`after_create`/`before_run`/`after_run`/`before_remove`), configured via `SYMPHONY_WORKFLOW_HOOK_*` env vars (this repo's existing convention, not a WORKFLOW.md policy file — see §5's row below), workspace path + issue key passed as env vars, `hooks.timeout_ms` default 60000 matching SPEC.md. Best-effort: a failing/timed-out hook is logged, never raised |
| §9.5 Workspace Safety Invariants | **Adopted as-is** | `workspace.py`'s `_validate` — cwd-inside-root + sanitized keys, taken directly |
| §10 Agent Runner Protocol (Codex app-server) | **Not applicable** | We use the Claude Agent SDK, a different protocol entirely — `agent_runner.py` is our own contract, not §10's |
| §11 Issue Tracker Integration Contract | Adopted (Jira only) | `tools.py`'s registry plays the adapter role; no multi-tracker abstraction |
| §13.3/13.7 Runtime Snapshot / HTTP API | Partially adopted | `GET /health` exists; `GET /api/v1/state` / `GET /api/v1/issues/{key}` are Epic 2's first wave (`ADLC-32`) |
| §14.1–14.2 Failure Model | Adopted (partial) | Per-run failure → `mark_failed`; no exponential backoff/retry queue (§14.2's "Worker failures: convert to retries") |
| §14.3 Partial State Recovery | **Already agreed** | See the Finding above — same intentional in-memory design, discovered independently before re-reading this section |
| §15 Security and Operational Safety | Adopted (partial) | Workspace safety yes; no documented sandbox/approval posture beyond `permission_mode` |
| §13.7 HTTP API Extension, CLI variant | Adopted (new, ADLC-45/46/47) | `symphony-workflow plan/implement/decompose/sync-decomposition` — the CLI control plane above; a fourth interface onto `orchestrator.py`/`agent_runner.py`, not a parallel implementation of the Plan/Implement gate |
| §4 Core Domain Model, Epic decomposition | Adopted (new, ADLC-46/47) | No Symphony section models Epic→Story/Task decomposition directly; `decompose.py`'s `Decomposition`/`Story`/`Task` are new, deliberately outside `RunState` — see the CLI control plane section above |

See [`implement.md`](implement.md) for the phased build order.
