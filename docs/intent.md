# Intent — what to build & why

## Context

We read OpenAI's [Symphony](https://github.com/openai/symphony) spec (`openai/symphony`,
Apache-2.0): tracker → per-issue workspace → coding-agent session → PR, with the tracker
exposing "provider-native tools" to the agent. Right after, we scaffolded
**`jira-agent-cli`** — a Python CLI that already plays Symphony's "Issue Tracker Adapter"
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

CAS has a standing rule against unsupervised headless Claude spawns (`claude -p` /
war-room "Execute Next" was de-clawed after it ran unattended). Symphony's whole design
assumes an unattended polling daemon. This POC deliberately does **not** build that —
every agent session starts from an explicit, human-made API call, never a background loop.
See [`spec.md`](spec.md) for why this points at the Claude Agent SDK instead.

## Tracking

This POC is tracked in a dedicated Jira project — **ADLC** ("Agentic Dev Lifecycle"),
team-managed Kanban board, separate from CF1's content/marketing backlog:
https://cloud-f1.atlassian.net/jira/software/projects/ADLC/boards/74. Phase 1 was built
and closed under `CF1-286` before ADLC existed; `ADLC-1` (Phase 2) is the first ticket on
the new board. Continuous polling (`/loop`) was considered and explicitly deferred —
Phase 2 gets driven by hand a few times first so we can see real plan quality before
automating ticket discovery.

## Definition of done

Run `plan` then `implement` against one real CF1 ticket, get a plan a human approves,
get code+tests a human can review, and end up with a working example of every link in
the ADLC chain — including this intent/spec/plan trio itself.

See [`spec.md`](spec.md) for the design, [`plan.md`](plan.md) for the phased roadmap.
