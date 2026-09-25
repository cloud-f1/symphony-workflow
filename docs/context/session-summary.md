# symphony-workflow — Session Summary
> **Load this to resume any session without re-explaining context.** Pattern borrowed
> from `ai-coding-nexjs-template`'s `docs/context/session-summary.md` (a real, proven
> sibling-repo convention) — adapted here since Jira (the `ADLC` board) is this repo's
> SSOT for epic/story *status*, not a local markdown file; this doc carries the
> narrative — what happened, what's next, what to remember — that a ticket list alone
> doesn't.

---

## Session close-out — 2026-09-25 (`ADLC-53`: repo renamed to symphony-workflow, v0.3.0 tagged)

**Executed `ADLC-53`**, the rename flagged in the previous close-out as needing explicit
human confirmation before touching anything. Alex confirmed both open decisions: new local
path is `~/Documents/git_saas/symphony-workflow` (not left at the old path), and the Python
package/console command are renamed too (`jira_agent_cli` → `symphony_workflow`, `jira-agent`
→ `symphony-workflow`) rather than deferred to a follow-up ticket.

**What changed, in order:**
1. Quiesced first — killed the running `uvicorn` server, removed 10 already-merged worktrees
   (`.worktrees/ADLC-{28,29,30,43,44,49,50,51,52,53,54}`) + their merged local branches, and
   unlocked/removed one stale worktree (`.claude/worktrees/agent-ad0c65d5e520a53c8`, PID
   confirmed dead) — an abandoned duplicate ADLC-50 attempt, unmerged, superseded by the
   branch that actually shipped. A full-tree `mv` with live linked worktrees still attached
   would have broken every one of them (their `.git/worktrees/*/gitdir` files store absolute
   paths).
2. Package rename on a branch first, while the GitHub repo was still named `jira-agent-cli`
   (so nothing broke mid-flight): `src/jira_agent_cli/` → `src/symphony_workflow/`, every
   import, `pyproject.toml` (`name`, `[project.scripts]`, hatch wheel `packages`), all
   docstrings/comments/Jira-comment-template strings in `cli.py`/`orchestrator.py`/
   `poller.py`/`decompose.py`/`sync_decomposition.py`/`tools.py`/`api.py`/`config.py`. 194
   tests + ruff clean throughout. One deliberate exception: `tests/test_workspace.py`'s
   regression test for the old `jira-agent-cli/ADLC-17` branch-naming bug keeps that literal
   string — it's quoting a real historical bug, not a live reference.
3. Plugin/skill layer, landed atomically: `.claude/skills/jira-agent-cli-ops/` →
   `.claude/skills/symphony-workflow-ops/` (and the `plugin/skills/` mirror copy — these two
   `SKILL.md` files were already hand-drifted from each other pre-rename; renamed both without
   reconciling that separate staleness), `.claude-plugin/marketplace.json`,
   `plugin/.claude-plugin/plugin.json` (incl. `homepage` URL), `plugin/hooks/hooks.json`, both
   `session-start-check-api.sh` copies (module path, log filename, status messages), and
   `.claude/settings.json`'s hardcoded absolute hook path (forward-referenced the not-yet-created
   new path, confirmed correct after the `mv` in step 4).
4. Version bumped to `0.3.0` (pyproject.toml + plugin.json, kept in sync per
   `test_plugin_version_matches_pyproject_version`), `CHANGELOG.md` entry added. Rename branch
   pushed, PR opened and merged into `main` on GitHub while the repo was still named
   `jira-agent-cli` — so the PR's own links stay valid pre-rename.
5. `gh repo rename symphony-workflow` on GitHub (auto-redirect keeps old
   `github.com/cloud-f1/jira-agent-cli` links resolving), then local
   `mv ~/Documents/git_saas/jira-agent-cli ~/Documents/git_saas/symphony-workflow` +
   `git remote set-url origin`, server restarted from the new path and `/health` re-verified.

**Deliberately left untouched (historical, not living references):** `docs/runs/**/*.md`
(per-ticket Plan-phase logs), `docs/plans/**/implement.md` (append-only execution logs),
`docs/plans/0001-symphony-poc/implement.md` specifically — these describe what was literally
true when written, under the name the repo had at the time. Only `intent.md`/`spec.md`
(forward-looking design docs) were swept. `CHANGELOG.md`'s pre-0.3.0 entries were left alone
for the same reason; a new 0.3.0 entry documents the rename itself.

**One follow-up surfaces for a later session**: the two `symphony-workflow-ops` `SKILL.md`
copies (`.claude/skills/` vs `plugin/skills/`) were already substantively different before
this rename (confirmed by diff) — the plugin copy is a hand-maintained mirror that's drifted.
Not fixed here (out of scope for a rename), but worth a dedicated reconciliation pass.

---

## Session close-out — 2026-09-24/25 (Epic 2 substantially complete, v0.2.0 tagged)

**Epic 2's full arc finished this session**, in order: interface API (`ADLC-32`) →
local md↔Jira linking (`ADLC-36`) → GitHub-for-Jira app connected (`ADLC-30`) → multi-repo
policy designed → a real live bug found and fixed (poller's `_seen` permanently stranding a
failed auto-Plan, `ADLC-44`'s own fix — bounded retry + failure-visible comments) →
`ADLC-44`'s Story (CLI control plane, decompose, sync-decomposition, docs — `ADLC-45`–`48`) →
richer-polling triage (`ADLC-29`) → wrap-up docs/memory pass (`ADLC-51`) → `ADLC-40`'s `NXJS`
project created via API → real portability validation against `ai-coding-nexjs-template`
(`ADLC-41`/`42`) which found a genuine security bug in that repo AND two real bugs in
jira-agent-cli itself (`ADLC-55`/`56`, both fixed) → version cut, `v0.2.0` tagged.

**`v0.2.0` is tagged and pushed.** See `CHANGELOG.md` for the full breakdown. `pyproject.toml`
and `plugin/.claude-plugin/plugin.json` versions kept in sync by `tests/test_version.py`.

**How this session actually ran, later half**: `/loop` (dynamic, self-pacing, user-approved) —
each wake checked the `ADLC` board, dispatched dev subagents (`Agent` tool,
`isolation: "worktree"`, capped at 4 concurrent per explicit user instruction — see
auto-memory), reviewed real diffs (not just subagent self-reports) before merging, escalated
instead of guessing on anything genuinely impactful. The loop has a real stop condition: it
stops itself (not polls forever) when nothing's ready/in-flight/awaiting-review and the reason
is a human-gated blocker. Ten PRs went through this pipeline this session (`#2`–`#11`), all reviewed and merged
individually, none auto-merged blind.

**Two tickets remain, both correctly untouched by the loop, both need you:**
- **`ADLC-53`** — rename `jira-agent-cli` → `symphony-workflow`. Deliberately NOT started
  autonomously (a real, hard-to-reverse, shared-system change — GitHub repo rename, this
  session's own local checkout path, a full internal-reference sweep). Needs your explicit
  go-ahead and a dedicated planning pass before any execution.
- **`ADLC-54`** — port the Claude Code plugin to `ai-rc-engineer-pm`. Prep confirmed safe (no
  skill-name collision), but the actual install-and-verify (`/plugin marketplace add`,
  `/plugin install`) needs a real interactive Claude Code session — no tool/subagent can drive
  Claude Code's own built-in slash commands. Run it yourself (or ask a fresh interactive
  session cd'd into `ai-rc-engineer-pm`) when ready.

**Findings worth remembering from this half of the session:**
- **Jira project creation works fine via `POST /rest/api/3/project`** — no UI needed, an
  account with global `ADMINISTER` permission can do it. Two traps: `projectTemplateKey`
  naming is misleading (`gh-simplified-kanban-classic` creates a classic project despite
  "simplified" in the name; `gh-simplified-agility-kanban` is the real team-managed one — verify
  via the response's `style`/`simplified` fields, not the key name). `DELETE` soft-deletes into
  Jira's Trash with a ~60-day retention — key *and* name stay reserved the whole time.
- **`AgentRunResult.text` can come back empty even when `result.structured` is fully
  populated** — happens when the model's final turn emits only the structured `output_format`
  result with no accompanying `TextBlock`. Confirmed live against a large real-world target
  repo, not a synthetic case. Fixed (`ADLC-56`), but worth remembering the SDK behavior itself
  if it resurfaces somewhere else structured output gets used.
- **`DEFAULT_PLAN_MAX_TURNS=5` is sized for this repo's own codebase, not real-world target
  repos** — confirmed failing deterministically against a 90-phase mature codebase, succeeding
  at `max_turns=15`. Now overridable (`ADLC-55`) without changing the shared default.
- **A ticket-tracking repo's own handover doc can go stale just like this one can** —
  `ai-coding-nexjs-template`'s `epic-progress.md` "Next Action" section flagged two issues
  (`make hook-test` failures, migration drift) that were both already fixed by the time this
  session checked live. Always verify a stale-doc-sourced lead before ticketing it as current.

## Earlier session — 2026-09-24 (Epic 2 mid-flight: ops hardening, CLI control plane, parallel dev pipeline)

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

**Update, same session, later**: all three dispatched PRs landed (`ADLC-49`, `ADLC-28`,
`ADLC-50` — merged). Found and fixed a real live bug immediately after: the poller's `_seen` set
blocked a failed auto-Plan from ever being retried, and posted no comment either — `ADLC-44`
(this session's own new Story) got silently stuck on `error_max_turns`. Fixed with bounded retry
(`JIRA_AGENT_CLI_POLL_MAX_PLAN_ATTEMPTS`, default 3) + failure-visible Jira comments
(`orchestrator._post_failure_comment`) + a "giving up" comment once retries are exhausted — live
re-verified: `ADLC-44` recovered and completed Plan on retry. Also shipped `ADLC-43`'s
`docs/plans/<NNNN>-<slug>/` convention (migrated the old top-level `docs/{intent,spec,plan}.md` to
`docs/plans/0001-symphony-poc/`) and used it for `ADLC-29`'s first real detailed spec
(`docs/plans/0002-richer-polling-triage/`).

**`/loop` turned on** (dynamic, self-pacing, user-approved) to keep this session autonomously
pulling and developing tickets between prompts — subagent+worktree+PR pipeline, review-then-merge
unless genuinely impactful (escalate instead of guessing), max 4 concurrent. Stop condition: if a
tick finds nothing ready/in-flight/awaiting-review and the reason is a human-gated blocker (not a
transient gap), the loop stops itself and pushes a notification rather than polling forever.

**`ADLC-44`'s whole Story shipped** (all four subtasks Done, merged): `ADLC-45` (CLI
plan/implement, direct commit), `ADLC-46` (`jira-agent decompose` — Epic→Stories/Tasks for human
review, DoD templated from `CLAUDE.md` not LLM-invented, PR #5), `ADLC-47` (`jira-agent
sync-decomposition` — reads reviewed `decomposition.json`, creates real Story/Subtask issues via
the `parent=` mechanism, PR #6), `ADLC-48` (docs, PR #7). `ADLC-29` (richer-polling triage) was
also dispatched — user explicitly approved building it despite its own ticket text saying to wait
for Epic 2's first wave, since that's taking a while; check `gh pr list` for its status.

**Pick up here next**: `ADLC-51` (quickstart doc + auto-memory refresh + CLAUDE.md consolidation
pass) was created for after this burst of work settles — now's close to the right time, once
`ADLC-29` lands too. Still waiting on the user for `ADLC-40` (dedicated Jira project for
`ai-coding-nexjs-template`) and confirmation before starting the
`ai-rc-engineer-pm` integration (no ticket exists for it yet).

---

## Earlier session — 2026-09-25 (Epic 1 closed, Epic 2 scoped)

**Epic 1** (`ADLC-2`, the Symphony-inspired POC — Phases 0–5) is **Done**. Full
history: `docs/plans/0001-symphony-poc/intent.md` (DoD review, all clauses ✅),
`docs/plans/0001-symphony-poc/spec.md` (design + Symphony alignment matrix),
`docs/plans/0001-symphony-poc/implement.md` (phase-by-phase build log),
`docs/runs/{ADLC-17,ADLC-24}/{plan,review}.md` (the two real live runs).

**Epic 2** (`ADLC-27`, "interface API, portability validation, Symphony
alignment") is open, first wave scoped:

| Story | What | Status |
|---|---|---|
| `ADLC-30` | Install + connect GitHub-for-Jira app to `cloud-f1` org | To Do — **manual, needs you**, not agent-doable |
| `ADLC-31` | Symphony alignment matrix in `docs/plans/0001-symphony-poc/spec.md` | Done |
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
  design**, not a corner we cut — reframed in `docs/plans/0001-symphony-poc/spec.md` after re-reading the
  spec post-hoc. Restart recovery = re-poll, not resume in-flight sessions, on
  purpose (Symphony's words, not just ours).
- **`acceptEdits` can't run Bash.** Implement writes a diff, can't self-verify.
  Always run `pytest`/`ruff` yourself after an Implement run.
- **`~/.claude/plans/*.md` (Claude Code's own plan-mode file) gets overwritten
  by the next unrelated plan-mode cycle.** This session's original POC roadmap
  plan was overwritten by the Epic-2-scoping plan — recoverable only because
  `docs/plans/0001-symphony-poc/intent.md` etc. already captured it before the overwrite. `ADLC-43`
  exists because of this exact problem — and has now shipped: this is the convention it built.

### Where things live

- **Ticket status** (what's Done/To Do/In Progress): the `ADLC` Jira board —
  this file doesn't duplicate it, just narrates around it.
- **Design history**: `docs/plans/0001-symphony-poc/{intent,spec,implement}.md`
  (`ADLC-43`'s convention — see `docs/plans/README.md` and `docs/plans/INDEX.md`).
- **Run-by-run record**: `docs/runs/{key}/{plan,review}.md`.
- **How to operate this repo day to day**: `.claude/skills/jira-agent-cli-ops/SKILL.md`.
- **Using this from another repo**: `docs/PORTABILITY.md`.
