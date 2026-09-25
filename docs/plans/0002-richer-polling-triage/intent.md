# Intent — ADLC-29: richer polling via an actual Claude Code turn

## Problem

`poller.py`'s `READY_JQL` is one blunt filter (`status = "To Do"`, excluding Epic/Subtask),
polled every 60s, blanket-dispatching `orchestrator.execute_plan` for every unseen ticket. That's
cheap and safe but dumb: it can't tell a well-formed ticket from a malformed one, can't
prioritize, can't spot near-duplicates, and (as of `ADLC-44`'s bounded-retry fix) will now retry
a genuinely bad ticket up to `SYMPHONY_WORKFLOW_POLL_MAX_PLAN_ATTEMPTS` times before giving up — which
is the right behavior for a *transient* failure, but wasteful for a ticket that's malformed in a
way a cheap classification step could have caught on attempt zero.

The ticket's own text flags the real risk itself: a full Claude Agent SDK turn per poll tick is
much more expensive than a JQL call, so "just call the SDK from the poll loop" is not a design —
this document is that design.

## Goals

- Classify newly-discovered tickets (plan / skip / flag-for-human) before committing to a full
  `execute_plan` run, without making every 60s poll tick an expensive agent turn.
- Preserve the load-bearing safety property this whole system is built around: the poller
  auto-Plans, never auto-Implements, and now (via triage) never even auto-*decides* anything
  Jira-visible without code applying that decision — the triage agent itself gets no Jira write
  tools, exactly like Plan/Implement never got merge/push access.
- Bound cost on two axes: cadence (triage runs far less often than the 60s poll) and batching
  (one triage turn classifies a whole batch of newly-discovered tickets, not one turn per ticket).

## Non-goals

- Reordering/prioritizing dispatch order — this design only classifies plan/skip/flag, it does
  not decide *which* ticket to work first. A separate concern if ever needed.
- Replacing the cheap JQL poll loop — it stays exactly as-is for liveness/dedupe; triage is an
  additional, slower-cadence step layered on top, not a replacement.
- Deciding what a Jira status transition for "flag" should be — this repo doesn't have live
  workflow-graph introspection today, so a flagged ticket gets a comment, not a status change,
  until that's built.

## Definition of done (for actually building this, not for this design doc)

- `SYMPHONY_WORKFLOW_TRIAGE_ENABLED` defaults `false` — this must not change today's behavior for
  anyone who hasn't opted in.
- A batch of newly-discovered tickets produces exactly one Claude Agent SDK turn, not one per
  ticket and not one per poll tick.
- The triage agent has no live Jira write tools (no `add_comment`/`transition_issue` inside its
  own tool access) — all Jira-visible side effects happen in code, after the agent returns a
  structured decision, matching how Plan/Implement already work.
- Tests cover: plan/skip/flag dispatch from a canned decision, malformed/out-of-batch decisions
  rejected, the feature-flag default-off path, and that a flagged ticket gets a comment not a
  status change.

See [`spec.md`](spec.md) for the design and [`implement.md`](implement.md) for the concrete build
plan once someone picks this up (**not yet** — this is explicitly deferred until Epic 2's first
wave, `ADLC-39`–`42`, ships; see `ADLC-29`'s own ticket text).
