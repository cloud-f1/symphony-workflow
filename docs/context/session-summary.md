# jira-agent-cli — Session Summary
> **Load this to resume any session without re-explaining context.** Pattern borrowed
> from `ai-coding-nexjs-template`'s `docs/context/session-summary.md` (a real, proven
> sibling-repo convention) — adapted here since Jira (the `ADLC` board) is this repo's
> SSOT for epic/story *status*, not a local markdown file; this doc carries the
> narrative — what happened, what's next, what to remember — that a ticket list alone
> doesn't.

---

## Latest session — 2026-09-25 (Epic 1 closed, Epic 2 scoped)

**Epic 1** (`ADLC-2`, the Symphony-inspired POC — Phases 0–5) is **Done**. Full
history: `docs/intent.md` (DoD review, all clauses ✅), `docs/spec.md` (design +
Symphony alignment matrix), `docs/plan.md` (phase-by-phase build log),
`docs/runs/{ADLC-17,ADLC-24}/{plan,review}.md` (the two real live runs).

**Epic 2** (`ADLC-27`, "interface API, portability validation, Symphony
alignment") is open, first wave scoped:

| Story | What | Status |
|---|---|---|
| `ADLC-30` | Install + connect GitHub-for-Jira app to `cloud-f1` org | To Do — **manual, needs you**, not agent-doable |
| `ADLC-31` | Symphony alignment matrix in `docs/spec.md` | Done |
| `ADLC-32` | Interface API — `GET /api/v1/state`, `GET /api/v1/issues/{key}` | **Done** — live-verified |
| `ADLC-36` | Local md ↔ Jira ticket linking (remotelink + frontmatter) | **Done** — real links created on `ADLC-17`/`ADLC-24` |
| `ADLC-39` | Portability validation against a real second repo (`ai-coding-nexjs-template`) | To Do — **pick up here next session** |
| `ADLC-43` | `docs/plans/<NNNN>-<slug>/` convention (this session's own overwritten-plan problem) | To Do — designed, not built |
| `ADLC-28`/`ADLC-29` | Backlog, unsized: structured plan output, richer (Claude-Code-turn) polling | Tracked, not scoped |

**Pick up here next session**: `ADLC-39` (portability validation) — its own
subtasks (`ADLC-40`/`41`/`42`) are the concrete steps: create a dedicated Jira
project for `ai-coding-nexjs-template`'s agent-driven work (same reasoning as
`ADLC` vs `CF1`), point a jira-agent-cli instance's env at it
(`JIRA_AGENT_CLI_TARGET_REPO=~/Documents/git_saas/ai-coding-nexjs-template`,
`JIRA_AGENT_CLI_JIRA_PROJECT=<that project's key>` — see `docs/PORTABILITY.md`
for the full adoption steps), run one real Plan against one real ticket there,
and judge whether the plan actually reads *that* repo's `CLAUDE.md`/conventions
— expected to surface the prompt-templates gap `docs/PORTABILITY.md` already
flags as deferred. This session deliberately left it unbuilt — it's a real,
well-defined next action a fresh session should be able to execute correctly
from these docs alone, not something left unfinished by accident.

### Findings worth remembering (don't rediscover)

- **Branch naming was broken, now fixed, but insufficient alone.** Branches now
  correctly start with the issue key (`workspace.py`'s `branch_name()`) — but
  `cloud-f1.atlassian.net` has **no GitHub-for-Jira app connected at all**
  (`GET /rest/dev-status/1.0/issue/summary` → `byInstanceType: {}` for every data
  type). `ADLC-30` tracks getting that installed; nothing in code can fix it.
- **`RunStore`'s in-memory design matches Symphony's own §14.3 intentional
  design**, not a corner we cut — reframed in `docs/spec.md` after re-reading the
  spec post-hoc. Restart recovery = re-poll, not resume in-flight sessions, on
  purpose (Symphony's words, not just ours).
- **`acceptEdits` can't run Bash.** Implement writes a diff, can't self-verify.
  Always run `pytest`/`ruff` yourself after an Implement run.
- **`~/.claude/plans/*.md` (Claude Code's own plan-mode file) gets overwritten
  by the next unrelated plan-mode cycle.** This session's original POC roadmap
  plan was overwritten by the Epic-2-scoping plan — recoverable only because
  `docs/intent.md` etc. already captured it before the overwrite. `ADLC-43`
  exists because of this exact problem.

### Where things live

- **Ticket status** (what's Done/To Do/In Progress): the `ADLC` Jira board —
  this file doesn't duplicate it, just narrates around it.
- **Design history**: `docs/{intent,spec,plan}.md` (will move to
  `docs/plans/0001-symphony-poc/` once `ADLC-43` ships — not yet).
- **Run-by-run record**: `docs/runs/{key}/{plan,review}.md`.
- **How to operate this repo day to day**: `.claude/skills/jira-agent-cli-ops/SKILL.md`.
- **Using this from another repo**: `docs/PORTABILITY.md`.
