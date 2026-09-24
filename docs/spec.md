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

See [`plan.md`](plan.md) for the phased build order.
