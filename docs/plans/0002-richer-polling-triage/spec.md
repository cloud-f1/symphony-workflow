# Spec — ADLC-29: richer polling via an actual Claude Code turn

## Design

Keep the cheap JQL poll for liveness/dedupe exactly as-is (`poller.py`'s `tick()`, `READY_JQL`,
`_seen`/`_attempts`/`_given_up` bookkeeping — all unchanged, including the `ADLC-44` bounded-retry
fix). Add a **separate, throttled triage step** in front of `execute_plan`:

1. **Poll tick stays cheap.** Same JQL + dedupe/retry bookkeeping, but a newly-discovered issue
   (not yet seen, not given up) goes into a small in-memory "untriaged" queue instead of an
   immediate `execute_plan` call.
2. **A slower triage cadence** (new `JIRA_AGENT_CLI_TRIAGE_INTERVAL_SECONDS`, e.g. 900s —
   deliberately >> the 60s poll interval) drains that queue and, only if non-empty, makes **one**
   Claude Agent SDK turn for the whole batch — not one per ticket. The turn receives a compact
   JSON snapshot (key/summary/description/type/created) with **no live Jira tool access**, and
   returns structured decisions: `{issue_key, action: "plan" | "skip" | "flag", reason}` (using
   the same `output_format`/`json_schema` mechanism `ADLC-28` already proved works cleanly under
   `permission_mode="plan"`).
3. **Code applies the decision, not the agent.** `plan` → the existing `execute_plan` runs
   unchanged. `flag` → `orchestrator` posts a Jira comment via the existing `add_comment` tool
   (no status transition — this repo doesn't have live workflow-graph introspection, so guessing
   a target state is worse than not guessing). `skip` → recorded in a new small in-memory
   `TriageStore` (same POC pattern as `RunStore`) so it isn't re-reasoned-about every triage cycle
   while still `To Do`.
4. **Feature-flagged.** `JIRA_AGENT_CLI_TRIAGE_ENABLED` defaults `false`, preserving today's
   behavior until validated.

This bounds cost two ways: triage only fires on a slow cadence when there's new work, and it's one
budget-capped turn (`max_turns=2`, `max_budget_usd=0.5`, mirroring `agent_runner.run_plan`'s
existing pattern) per batch, not per tick or per ticket.

## Files that would change

- `poller.py` — split dispatch into enqueue vs. triage-driven action; add the triage cadence
  (a second, slower loop alongside the existing 60s one, or a counter-based skip inside the
  existing loop — implementer's call, see Risks).
- `orchestrator.py` — new `execute_triage(candidates, *, client, triage_store)`, shared by
  `poller.py` and any future API endpoint, same pattern as `execute_plan`/`execute_implement`.
- `agent_runner.py` — new `run_triage(...)`, a third thin wrapper around
  `claude_agent_sdk.query()`.
- `prompts.py` — new `TRIAGE_PROMPT` defining "malformed" and the required JSON output shape.
- `models.py` — new `TriageDecision` pydantic model (per this repo's pydantic-at-boundaries
  convention — a triage decision crosses the agent-output boundary, same reasoning as `Run`).
- New `TriageStore` (in-memory, alongside `runs.py`'s `RunStore`).
- `config.py` — worth centralizing the new triage env vars here rather than continuing the
  existing scattered pattern (`poller.py`/`api.py` currently read poll-related env vars directly)
  — judgment call for whoever builds this, not mandatory.
- Tests: `test_poller.py` (triage-path cases mirroring the existing `run_plan`
  monkeypatch/bounded-retry test style), `test_orchestrator.py::test_execute_triage`,
  `agent_runner.run_triage` malformed-JSON-fallback tests.

No `tools.py`/`client.py` changes needed — `add_comment`/`search_issues`/`get_issue` already exist
as both client methods and registered tools.

## Risks / open questions

- **Non-determinism & idempotency.** Reject decisions referencing issue keys outside the input
  batch (defense against the agent hallucinating or including something it wasn't asked about).
  `TriageStore` prevents re-triaging a `skip`-ped ticket every cycle.
- **Preserve "never auto-Implement."** Triage stays read-mostly and code-applied — the agent
  itself must not get `transition_issue`/`add_comment` tools directly, only the code path that
  consumes its structured decision does.
- **What "flag" does to ticket status is unresolved** — needs live workflow-graph introspection
  before wiring any transition. Left comment-only in this design, deliberately.
- **Prioritization scope.** This design only classifies plan/skip/flag, not reordering dispatch —
  decide separately if that's ever needed (see intent.md's Non-goals).
- **"via /loop, scoped" in the ticket's own text** could mean an external `/loop`-driven session
  rather than in-process — this design recommends staying in-process via `agent_runner.py`
  (consistent with `CLAUDE.md`'s "Never `claude -p`"), but confirm with whoever picks this up
  before building, since it changes the mechanism meaningfully.
- **Per the ticket: don't build before Epic 2's first wave ships** (`ADLC-39`–`42`, portability
  validation — still blocked on `ADLC-40` as of this writing).

See [`implement.md`](implement.md) for the concrete step-by-step build order and verification plan.
