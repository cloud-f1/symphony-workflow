# Plan — implementation roadmap

See [`intent.md`](intent.md) for why, [`spec.md`](spec.md) for the design this follows.

**Phase 0 — Pick the real ticket**
Use `jira_agent_cli.tools.search_issues` / `get_issue` (already fixed to the live v3
endpoint) to pick one small, real CF1 ticket as the POC's driving example — or create a
purpose-made throwaway CF1 ticket if nothing suitable exists. This grounds every later
phase in a real payload instead of fixtures.

**Phase 1 — API server**
- `src/jira_agent_cli/api.py`: `GET /tools`, `POST /tools/{name}/call`, `GET
  /issues/{key}`, `POST /runs`, `GET /runs/{id}` — everything except the two run-phase
  endpoints is a thin FastAPI wrapper over the existing `tools.py`.
- `tests/test_api.py` using FastAPI's `TestClient` + `respx` — same "no live Jira in
  tests" rule the existing suite already follows.
- Add `fastapi` + `uvicorn` to `pyproject.toml` deps.

**Phase 2 — Plan/Implement interface**
- `runs.py`: the 5-state Run model (`created → planning → plan_ready → implementing →
  completed|failed`), in-memory dict store.
- `POST /runs/{id}/plan`: renders `docs/WORKFLOW.md`'s prompt template with the issue's
  fields, runs one bounded Agent SDK turn scoped to **read-only** tools, posts the
  resulting plan via `add_comment` (existing tool, unchanged) and saves
  `docs/runs/<key>/plan.md` locally.
- `POST /runs/{id}/implement`: hard-gated on `state == plan_ready`; runs a second bounded
  session inside `.worktrees/<key>/` with the approved plan as context, full tool budget,
  writes code + tests; ends with a summary (files changed, test result) — stops there,
  no auto-PR.

**Phase 3 — Claude Code session runner**
- `agent_runner.py`: wraps `claude-agent-sdk`'s `ClaudeSDKClient` (not `claude -p`);
  owns workspace create/validate (the §9.5 invariants) and the tool-budget split between
  Plan (read-only) and Implement (full) phases.

**Phase 4 — End-to-end run against the real ticket**
Run `plan` then `implement` against the Phase-0 ticket, watch it, and capture the
resulting artifacts as `docs/runs/<key>/{plan,review}.md` — the last two links in the
ADLC chain (code+tests and review record already exist as the PR itself; add a one-line
production-signal note manually since there's no real deploy for a POC ticket).

## Advice

1. **Skip Symphony's poller/retry/reconciliation entirely for the POC.** It's the most
   expensive, least-informative 80% of the spec — a pulled, two-call API teaches you
   whether the agent's plans are good and whether the human gate actually gets respected,
   for a fraction of the build.
2. **Don't fork a third repo.** `jira-agent-cli` already is the tracker/tool-call layer
   Symphony calls the adapter — extend it in place.
3. **The Claude Agent SDK, never `claude -p`, is the one adaptation that actually
   matters** — it's the direct fix for this org's own recorded failure mode, not just a
   style preference.
4. **Keep the Plan phase read-only.** Symphony doesn't enforce this (it trusts the
   workflow prompt), but a hard tool-budget split is cheap and turns the human-review gate
   from "please don't write code yet" into something the runtime actually enforces.
5. Once Phase 4 runs for real, this doc trio plus the run's own artifacts becomes a
   working demo of the ADLC diagram — worth pointing at directly if this gets pitched
   further.

## Verification

- `uv run pytest` stays green after each phase (mocked HTTP only, per existing
  convention) — no live Jira or live Claude session in the test suite itself.
- Phase 4 is the one deliberately-live step: run it manually, watch the API responses and
  the Jira comment/ticket state, and read the generated `plan.md`/diff before calling it
  done.
