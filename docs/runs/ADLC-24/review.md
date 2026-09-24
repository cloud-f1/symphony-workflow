# Review — ADLC-24 (Phase 5 poller live-verify)

**Ticket:** [ADLC-24](https://cloud-f1.atlassian.net/browse/ADLC-24), linked under
Epic ADLC-2, labeled `poller-live-verify` (not a bug fix, not QA — a synthetic demo
ticket purpose-built to prove Phase 5's auto-discovery live, same role ADLC-17 played
for Phase 4).

## What happened, unattended

1. Created `ADLC-24` (status: To Do) via `create_issue`.
2. Started the API server with a 10s poll interval (`JIRA_AGENT_CLI_POLL_INTERVAL_SECONDS=10`
   — default is 60s).
3. **Zero manual API calls after that.** Within one tick, the poller: found `ADLC-24`
   via `READY_JQL`, created a `Run`, ran a real bounded `permission_mode="plan"`
   session, posted the plan as a Jira comment, saved `plan.md`, and transitioned the
   ticket `To Do` → `In Review`.
4. Verified via `GET /issues/ADLC-24` directly against Jira (not the API server) —
   status and comment count confirmed independently of the app's own state.

## Plan quality — good, same bar as ADLC-17

Reasoned about the actual current state (`__init__.py` empty, `pyproject.toml`'s
version), applied `CLAUDE.md`'s "every change needs a test" rule unprompted, proposed
a drift-detection test (compare `__version__` against `pyproject.toml` via
`tomllib`), and correctly scoped out unrelated additions (no `--version` CLI flag,
no tool-registry entry) with reasoning for why.

## Finding — RunStore doesn't survive a restart (now hit in practice, not just theory)

I restarted the server (for an unrelated reason — switching to a fixed poll interval)
before calling `/implement` on `ADLC-24`. The in-memory `Run` the poller created —
including the `session_id` needed to resume for Implement — was gone. `GET
/issues/ADLC-24/run` (added specifically to look this up) correctly returned 404.

This was already documented as a known POC limitation (`runs.py`: "fine for a
bounded, human-triggered POC run; not fine for anything meant to stay up
unattended"). Confirms it's a real constraint, not a hypothetical one: **the ticket's
Jira state (`In Review`, plan comment) outlived the process, but the ability to
resume that exact agent session did not.** A non-POC version needs either a
persistent Run store (matching Symphony's tracker/filesystem-driven recovery, §7.4)
or a policy for what happens to a ticket whose Run was lost — re-plan from scratch
is the obvious answer, and would work today (it just wasn't exercised here, to avoid
spending real cost re-demonstrating what ADLC-17 already proved end-to-end).

## Also found (fixed same session)

**Real test-isolation bug**: the first version of `test_poller.py` didn't override
`orchestrator.RUNS_DOCS_ROOT`, so running the test suite wrote real files into this
repo's `docs/runs/ADLC-98/` and `ADLC-99/` (the poller tests' fixture issue keys).
Caught via `git status` after a normal test run, not by a failing test — worth
remembering: a passing suite doesn't mean a clean one. Fixed by redirecting
`RUNS_DOCS_ROOT` to `tmp_path` in the `poller` fixture, matching how `test_api.py`
already did it.

## Production signal

No real deploy. Closest signal: `GET /issues/{key}/run` (added because this run
surfaced the gap), 71/71 tests passing, `ruff` clean, and — the actual point of this
ticket — independently-verified Jira state proving the unattended discovery→Plan
path works without a human calling any endpoint.
