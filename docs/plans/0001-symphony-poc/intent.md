# Intent — what to build & why

## Context

We read OpenAI's [Symphony](https://github.com/openai/symphony) spec (`openai/symphony`,
Apache-2.0): tracker → per-issue workspace → coding-agent session → PR, with the tracker
exposing "provider-native tools" to the agent. Right after, we scaffolded
**`symphony-workflow`** — a Python CLI that already plays Symphony's "Issue Tracker Adapter"
role: every Jira op is both a typed client method and a schema-described tool
(`list-tools` / `call`), 22 tests, and live-verified against `cloud-f1.atlassian.net` (v2
issue CRUD works; v2 search is dead — HTTP 410 — fixed to use v3 `/search/jql`).

This POC asks whether the same Symphony shape — tracker → agent session → reviewed
change — is worth building further with **Claude Code** as the agent instead of Codex,
before investing in the expensive parts of the real spec (a 24/7 polling daemon,
retries, concurrency, reconciliation).

## What

A bounded proof-of-concept that takes **one real Jira ticket**, drives a Claude Code
agent through a **Plan phase** and then a separately-gated **Implement phase** against
it, over a small local **API server**, and produces the ADLC artifact chain (intent →
spec → plan → code+tests → review record → production signal) — run once, end-to-end,
human-supervised.

## Non-goals for this POC

- No 24/7 polling daemon, no multi-issue concurrency, no retry/backoff machinery, no
  reconciliation loop (Symphony §7–§8) — that's the expensive, low-learning part.
- No auto-merge / auto-deploy. A human reviews and merges, same as this org's existing
  worktree→PR discipline already does manually.
- No polling of the tracker for *candidate* work — the POC is pulled (human picks a
  ticket, calls the API), not pushed (daemon discovers work).

## The one house lesson this respects

The owner has a standing rule against unsupervised headless Claude spawns (`claude -p` /
war-room "Execute Next" was de-clawed after it ran unattended). Symphony's whole design
assumes an unattended polling daemon. This POC deliberately does **not** build that —
every agent session starts from an explicit, human-made API call, never a background loop.
See [`spec.md`](spec.md) for why this points at the Claude Agent SDK instead.

## Tracking

This POC is tracked in a dedicated Jira project — **ADLC** ("Agentic Dev Lifecycle"),
team-managed Kanban board, separate from CF1's content/marketing backlog:
https://cloud-f1.atlassian.net/jira/software/projects/ADLC/boards/74. Structure:
**Epic `ADLC-2`** ("symphony-workflow POC — Symphony-inspired Jira agent orchestration") →
one **Story per phase** → **Subtasks** per concrete work item underneath each Story.

| Phase | Story | Subtasks | Status |
|---|---|---|---|
| 0 — pick the real ticket + live-verify | `ADLC-3` | — | Done |
| 1 — API server | `ADLC-4` | — | Done (built under `CF1-286` before ADLC existed) |
| 2 — Plan/Implement interface | `ADLC-1` | `ADLC-5`/`6`/`7` | Done |
| 3 — Claude Code session runner | `ADLC-8` | `ADLC-9`/`10`/`11`/`12` | Done |
| 4 — end-to-end run | `ADLC-13` | `ADLC-14`/`15`/`16` | Done (real ticket: `ADLC-17`) |
| 5 — auto-discovery poller | `ADLC-18` | `ADLC-19`/`20`/`21`/`22`/`23` | Done (real ticket: `ADLC-24`) |

**Continuous polling revisited 2026-09-24** (originally deferred): resolved by
splitting discovery from code-writing — the poller auto-runs Plan only, never
Implement. Real daemon (background task in `api.py`, not `/loop`); ready signal =
`status = "To Do"` (board default). See `spec.md`'s Phase 5 section.

## Definition of done

Run `plan` then `implement` against one real CF1 ticket, get a plan a human approves,
get code+tests a human can review, and end up with a working example of every link in
the ADLC chain — including this intent/spec/plan trio itself.

### Reviewed 2026-09-25 — all clauses satisfied

| Clause | Status | Evidence |
|---|---|---|
| Plan run against a real ticket | ✅ | `ADLC-17`, `ADLC-24` |
| Human approves the plan before Implement | ✅ | Enforced in code (`RunTransitionError`), exercised for real on `ADLC-17` |
| Code+tests a human can review | ✅ | symphony-workflow PR #1, merged, 64→77 tests over the POC |
| Working ADLC artifact chain | ✅ | this trio + `docs/runs/{ADLC-17,ADLC-24}/{plan,review}.md` |
| intent/spec/plan trio itself | ✅ | kept current through every phase |

**The POC is done relative to its own stated DoD.** Two things stay explicitly *not*
done, by design rather than oversight — both already documented as findings in
`spec.md`, not new news: a persistent `RunStore` (Symphony §7.4-style restart
recovery) and a Bash-capable Implement phase that can self-verify its own output.

See [`spec.md`](spec.md) for the design, [`implement.md`](implement.md) for the phased
roadmap, [`PORTABILITY.md`](../../PORTABILITY.md) for using this outside symphony-workflow itself.
