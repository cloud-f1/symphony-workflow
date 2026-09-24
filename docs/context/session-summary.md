# jira-agent-cli — Session Summary
> **Load this to resume any session without re-explaining context.** Pattern borrowed
> from `ai-coding-nexjs-template`'s `docs/context/session-summary.md` (a real, proven
> sibling-repo convention) — adapted here since Jira (the `ADLC` board) is this repo's
> SSOT for epic/story *status*, not a local markdown file; this doc carries the
> narrative — what happened, what's next, what to remember — that a ticket list alone
> doesn't.

---

## Latest session — 2026-09-24 (Epic 2 mid-flight: ops hardening, CLI control plane, parallel dev pipeline)

**GitHub-for-Jira app connected** (`ADLC-30`, Done) — `repository.byInstanceType` now populated
(previously `{}` for every data type). Branch-level linking itself still unverified — nothing has
pushed a correctly-named branch to GitHub since the app connected; next real test is doing that.

**Multi-repo policy decided and documented** (`docs/PORTABILITY.md`'s new section): dedicated
Jira project per repo, one jira-agent-cli instance per repo (own port/`.env`) — not Epic/tag
routing or a shared multi-tenant poller. Interim exception (reusing `ADLC` before a dedicated
project exists) requires `JIRA_AGENT_CLI_POLL_ENABLED=false` on the non-owning instance +
tracking tickets created as Subtasks (invisible to the owning poller's `READY_JQL`).

**Real bugs found and fixed, live-verified against `cloud-f1.atlassian.net`:**
- Jira comments were garbled (raw GFM `#`/`##`/`###` collide with Jira wiki-markup's nested
  numbered-list syntax) — `orchestrator.jira_safe_comment_body()` now sanitizes before posting.
- `create_issue`'s `epic` param silently doesn't work against `ADLC` (a team-managed project) —
  added `parent` param (`client.py`), which is what team-managed epic-linking and all
  Subtask→Story linking actually use. See `CLAUDE.md`'s new "Jira API gotchas" section.
- No `update_issue` tool existed at all — added (needed to retitle `ADLC-28`).

**CLI-native control plane landed** (`ADLC-45`, Done): `jira-agent plan <key>` /
`jira-agent implement <key>` call `orchestrator.py` in-process, no server/HTTP needed. Run state
persists to `docs/runs/<key>/run.json` so `implement` can resume across separate CLI invocations
despite `RunStore` being in-memory/process-scoped (`RunStore.load()` added for this).

**Symphony spec re-checked directly against `SPEC.md`'s §18.1 conformance checklist** (not just
the existing alignment matrix) — found two real untracked gaps: workspace lifecycle hooks (§9.4)
and startup/terminal workspace cleanup (§8.6). Logged `ADLC-49`. Also researched packaging the ops
skill + `SessionStart` hook as a Claude Code plugin — verdict: hybrid only, the actual
CLI/server/poller can't be plugin-installed (no Python dependency auto-install in the plugin
system). Logged `ADLC-50`.

**`ADLC-28` retitled and reset to To Do** — original title assumed the wrong SDK field name
(`output_config`, corrected to `output_format`); now explicitly scoped as the structured-output
foundation `ADLC-46` (under the new `ADLC-44` Story: CLI control + Epic/Story/Task decomposition
with DoD/Acceptance Criteria) will build on.

**Parallel worktree-subagent pipeline dispatched** (in flight at end of session): `ADLC-49`,
`ADLC-50`, `ADLC-28` — three independent tickets, each a fresh Claude Code agent in an isolated
git worktree (`Agent` tool, `isolation: "worktree"`), implementing + testing + opening a PR each.
**Human (or this session) reviews and merges — not auto-merged.** Max 4 concurrent subagents (a
hard user-set cap, see auto-memory). Check `gh pr list` for what landed; these may still be open
if resuming shortly after this was written.

**`SessionStart` hook now surfaces ready tickets, not just server health**
(`.claude/hooks/session-start-check-api.sh`) — queries `ADLC` for `status = "To Do"` (excluding
Epic/Subtask) via the v3 search endpoint and injects the list as session-start context. Fires once
per session start, not a recurring loop — `/loop` or a scheduled cloud agent would be needed for
genuine periodic autonomous pulling, not yet set up.

**Pick up here next**: check whether the three dispatched subagents' PRs are ready
(`gh pr list --state open`), review + merge them, run the full suite on `main` after each merge
("make main branch perfect" per the user's own framing). Still waiting on the user for `ADLC-40`
(dedicated Jira project for `ai-coding-nexjs-template`) and confirmation before starting the
`ai-rc-engineer-pm` integration (no ticket exists for it yet).

---

## Earlier session — 2026-09-25 (Epic 1 closed, Epic 2 scoped)

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

- **Branch naming was broken, now fixed; the GitHub-for-Jira app is now
  connected too (`ADLC-30`, Done, 2026-09-24).** Branches correctly start with
  the issue key (`workspace.py`'s `branch_name()`), and `repository.byInstanceType`
  is now populated (was `{}` for every data type before). **Branch-level linking
  itself is still unverified** — no correctly-named branch has actually been
  pushed to GitHub since the app connected (Plan-phase worktrees never commit;
  this repo's history is direct-to-main, not pushed feature branches), so
  `branch.byInstanceType` hasn't been exercised end-to-end yet. Next real test:
  push one correctly-named branch and re-check.
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
