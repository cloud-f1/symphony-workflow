# Implement — ADLC-29: richer polling via an actual Claude Code turn

**Status: not started.** This is a build plan for whoever picks up `ADLC-29`, written ahead of
time per `spec.md`'s design — not a log of work already done (unlike
`docs/plans/0001-symphony-poc/implement.md`, which is a real build log). Per the ticket itself,
don't start this before Epic 2's first wave (`ADLC-39`–`42`) ships.

## Step-by-step build order

1. **`models.py`**: add `TriageDecision` (pydantic) — `issue_key: str`, `action: Literal["plan",
   "skip", "flag"]`, `reason: str`.
2. **`prompts.py`**: add `TRIAGE_PROMPT` — takes a JSON batch of candidate issues, defines what
   "malformed" means (empty/placeholder description, duplicate-looking summary, etc.), specifies
   the required output shape matching `TriageDecision` (as a list).
3. **`agent_runner.py`**: add `run_triage(candidates: list[dict], ...) -> list[TriageDecision]`,
   thin wrapper around `claude_agent_sdk.query()` with `permission_mode="plan"` (read-only, no
   tool access to Jira), `output_format` set to a JSON-schema array of `TriageDecision`,
   `max_turns=2`, `max_budget_usd=0.5`. Reject/log (don't crash) any decision referencing an issue
   key outside the input batch.
4. **New `TriageStore`** (alongside `runs.py`'s `RunStore`, same in-memory POC pattern) — tracks
   which issue keys have been triaged and to what decision, so a `skip` isn't re-reasoned-about
   every cycle.
5. **`orchestrator.py`**: add `execute_triage(candidates, *, client, triage_store) ->
   list[TriageDecision]` — calls `run_triage`, then applies each decision: `plan` triggers nothing
   here (the poller's normal `execute_plan` path handles it next), `flag` posts a Jira comment via
   `client.add_comment`, `skip` records in `TriageStore`.
6. **`poller.py`**: add the triage cadence. Two viable approaches — pick one and document why:
   (a) a second, slower `asyncio` loop alongside the existing 60s one; (b) a tick-counter inside
   the existing loop that only runs triage every Nth tick. Route newly-discovered, not-yet-seen
   issues through triage before `execute_plan` instead of dispatching them immediately. Gate the
   whole thing behind `JIRA_AGENT_CLI_TRIAGE_ENABLED` (default `false`).
7. **`config.py`** (optional but recommended): centralize `JIRA_AGENT_CLI_TRIAGE_ENABLED`,
   `JIRA_AGENT_CLI_TRIAGE_INTERVAL_SECONDS` here rather than reading them ad hoc in `poller.py`.

## Tests

- `test_agent_runner.py`: `run_triage` happy path (canned decisions matching schema), malformed
  JSON / out-of-schema output handling, an issue-key-outside-batch decision is rejected/dropped.
- `test_orchestrator.py::test_execute_triage`: `plan` decision is a no-op here (poller handles
  it), `flag` posts a comment (respx-mocked, assert body/endpoint), `skip` records in
  `TriageStore` and isn't re-flagged on a second call.
- `test_poller.py`: triage cadence fires independently of the 60s poll cadence; feature-flag-off
  path behaves identically to today (no behavior change when `JIRA_AGENT_CLI_TRIAGE_ENABLED` is
  unset); a `plan`-decided ticket flows into the existing `execute_plan` path unchanged.
- `test_config.py` (if `config.py` gains the new env vars): default-off, parses interval as float.

## Manual verification (once implemented)

1. `uv run pytest tests/test_poller.py tests/test_orchestrator.py tests/test_agent_runner.py -k
   triage` — new tests pass in isolation.
2. `uv run pytest --cov=jira_agent_cli` — confirm zero regression with
   `JIRA_AGENT_CLI_TRIAGE_ENABLED=false` (the default).
3. Live: create a scratch ticket with an empty/placeholder description, enable triage, confirm it
   gets a `flag` comment rather than a silently-created `Run` and a wasted `execute_plan` attempt.
4. Live: confirm a well-formed ticket still reaches `execute_plan` and Plan runs exactly once
   (not once per poll tick while queued for triage).
