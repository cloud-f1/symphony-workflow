---
jira_url: https://cloud-f1.atlassian.net/browse/ADLC-51
---

Both exploration agents (docs survey and auto-memory survey) are running in the background. I'll continue once they report back.
Waiting for the two background exploration agents (docs structure survey, auto-memory survey) to finish before drafting the plan.
Investigation complete. Findings for ADLC-51 planning:

**1. `docs/` tree**
```
docs/PORTABILITY.md
docs/WORKFLOW.md
docs/context/session-summary.md
docs/plans/README.md
docs/plans/INDEX.md
docs/plans/0001-symphony-poc/{intent,spec,implement}.md
docs/runs/{ADLC-17,ADLC-24}/{plan,review}.md
docs/runs/{ADLC-28,ADLC-29,ADLC-30,ADLC-43,ADLC-50}/plan.md
```
`docs/context/` has only `session-summary.md` (no other files). No `docs/runs/ADLC-51/` yet.

**2. `docs/PORTABILITY.md`** — covers: what changed to make jira-agent-cli portable (`TARGET_REPO`/`JIRA_PROJECT` env vars), step-by-step adoption path for another repo, multi-repo policy (dedicated Jira project per repo, one instance per repo) with an instance registry table, an interim "shared project" exception procedure, what's NOT generalized yet (prompt templates still hardcoded, no per-repo `WORKFLOW.md` support), other portability caveats (in-memory `RunStore`, branch-linking verification status), and a full section on the Claude Code plugin (`jira-agent-cli-ops` skill + `SessionStart` hook), including install steps and what's not yet live-verified.

**3. `.claude/skills/jira-agent-cli-ops/SKILL.md`** — the short operational reference (explicitly says not to duplicate `docs/{intent,spec,plan}.md`). Covers: checking `/health` before starting a server, quick-start commands (`uv sync`, `.env`, `uv run --env-file .env uvicorn ...`), the three ways to drive a ticket (manual Plan/Implement, poller auto-discovery, CLI passthrough), branch-naming rule, safety invariants (Plan/Implement human gate, workspace isolation, never `claude -p`, poller never auto-Implements), known limitations (in-memory RunStore, `acceptEdits` can't run Bash, per-worktree `uv sync`), and a pointer to `docs/PORTABILITY.md`.

**4. `README.md`** (root) — covers: what the tool is/why a tool-call layer, background/provenance, a **Setup** section (`uv sync --extra dev`, `cp .env.example .env`, `uv run jira-agent list-tools`, how to get a Jira API token), **Testing** section (`uv run pytest`, coverage, ruff), project layout, and "Adding a new tool" steps. No mention of starting the API server/uvicorn — that's only in the SKILL.md and PORTABILITY.md, a possible gap for a quickstart doc to bridge.

**5. `docs/plans/README.md`** — documents the `docs/plans/<NNNN>-<slug>/` convention (intent/spec/implement per planning cycle), numbering rules, when to create a new folder, and history (only one cycle so far: `0001-symphony-poc`).

`0001-symphony-poc/intent.md` "Tracking" section: POC tracked in Jira project `ADLC`, Epic `ADLC-2` → one Story per Phase 0–5 → Subtasks. All six phases show **Done**. DoD reviewed 2026-09-25, all clauses ✅ satisfied — POC formally done.

**6. `docs/context/session-summary.md`** — two dated session entries (newest first: 2026-09-24, then 2026-09-25 oddly out of chronological order as headers). Latest session covers GitHub-for-Jira app connection, multi-repo policy, bugs fixed, CLI control plane (`ADLC-45`), Symphony conformance gaps (`ADLC-49`, `ADLC-50`), parallel worktree pipeline in flight, and a "Pick up here next" section. Ends with a "Where things live" index pointing to plans/runs/SKILL.md/PORTABILITY.md.

**7. No existing quickstart/tutorial/getting-started doc found anywhere in the repo** (searched for QUICKSTART, TUTORIAL, GETTING_STARTED, walkthrough filenames — zero hits). ADLC-51's quickstart doc would be genuinely new, not a refresh; README.md + SKILL.md together currently cover the ground it'd need to consolidate (setup+testing vs. operate-the-server+drive-a-ticket).
All 7 files read (1 index + 6 memory notes, matching the count MEMORY.md references). Note: none are actually named `feedback_*`/`reference_*` in a way that mismatches — all 6 individual files are referenced correctly in MEMORY.md, no orphans or missing links found.

**MEMORY.md** — Index file listing all 6 memory notes with one-line descriptions and relative links. Current/accurate: links all resolve to existing files, last modified timestamp (22:37) is the most recent of the set.

**feedback_subagent_concurrency_cap.md** — Hard cap of max 4 concurrent subagents, set explicitly by the user during a batch of parallel worktree-isolated dev agents, no reasoning given beyond the cap itself. Looks current: it's a durable operational rule with no session-specific/expiring content, though it does forward-reference a `[[project-jira-agent-cli-multirepo]]` link that does not correspond to any file in this directory (likely a broken/orphaned wikilink to a note that was never created or was pruned) — worth flagging as a minor internal inconsistency.

**feedback_hooks_over_docs.md** — Prefer building a `SessionStart` hook over just documenting a repeating session-start check (e.g. API server status, surfacing ready tickets), with the caveat that hooks fire once per session and don't support ongoing polling. Current/accurate: general, durable preference; cites a concrete example still relevant (`.claude/hooks/session-start-check-api.sh` in jira-agent-cli).

**feedback_momentum_over_reconfirmation.md** — Once the user confirms an overall approach, keep executing without re-confirming each step, while still using judgment on genuinely new/risky decisions. Current/accurate: durable behavioral preference, cross-references the concurrency-cap note coherently (not redundant, complementary).

**feedback_pr_review_and_merge_gate.md** — Auto-merge subagent-produced PRs after verifying diff/tests/lint, only escalating to the user for genuinely impactful changes (auth/secrets, behavior changes, irreversible actions, unresolved design choices). Current/accurate: refines/extends momentum-over-reconfirmation with specifics; not redundant, it's a natural follow-on from the same session (same `originSessionId`) rather than a duplicate.

**feedback_test_and_hook_over_memory_for_recurring_errors.md** — When a bug class could recur, codify prevention as a test (pytest for Python, pipe-tests/hooks for shell) rather than relying on a memory note; motivating example was a Jira v2 endpoint bug in the session-start hook. Potentially slightly stale in its concrete example: references `session-start-check-api.sh` using "the retired Jira v2 search endpoint" as the motivating bug — this specific bug was presumably already fixed within that same session (per the file's own account), so the general lesson remains valid but the example ticket/bug itself is resolved, not an open issue. All 5 feedback files share the same `originSessionId` (5726b07c...) and similar modified timestamps (14:28–14:37 today), suggesting they were all distilled from one single working session — worth confirming they're not overlapping fragments of the same conversation rather than independently durable lessons.

**reference_jira_agent_cli_setup.md** — Points to `docs/context/session-summary.md` as the real handover doc, `.claude/skills/jira-agent-cli-ops/SKILL.md` for ops, and describes the user's multi-repo `git_saas` layout (jira-agent-cli/ADLC, ai-coding-nexjs-template, ai-rc-engineer-pm) plus Jira instance details. Mostly current, but self-flags a freshness caveat: "ai-rc-engineer-pm ... no Jira integration as of 2026-09-24" — dated today, so likely accurate now but explicitly time-boxed and will go stale if that project's Jira integration changes; also worth noting it explicitly defers to the repo's own `session-summary.md` for ticket status rather than duplicating it, which is a good practice but means this note alone can't confirm current ticket state.
I've completed the plan for ADLC-51 and written it to the plan file. Summary:

**Plan: ADLC-51 wrap-up (quickstart doc + memory refresh + CLAUDE.md pass)**

Confirmed unblocked — `docs/plans/0001-symphony-poc`'s Tracking table shows all POC phases Done (DoD reviewed 2026-09-25), so the work this ticket waits on has landed.

1. **Quickstart doc (new gap, confirmed)** — Add `docs/QUICKSTART.md`: a thin, linear walkthrough (uv sync → .env → `list-tools` sanity check → start API server → Plan→Implement on a real ticket → where output lands). README.md currently stops before "start the server"; SKILL.md has that but is explicitly reference material, not a walkthrough. New doc links out to README/SKILL.md/PORTABILITY.md rather than duplicating them; wired in from both README.md's top and CLAUDE.md's doc-links paragraph.

2. **Auto-memory refresh** — Investigated all 6 notes + index; most are accurate and non-redundant (5 of 6 share one origin session but are complementary, not overlapping). Two concrete, targeted fixes rather than a rewrite: a dangling wikilink in `feedback_subagent_concurrency_cap.md`, and an already-resolved bug presented as a live example in `feedback_test_and_hook_over_memory_for_recurring_errors.md`. One note (`reference_jira_agent_cli_setup.md`) has a time-boxed claim flagged for a future recheck rather than edited now.

3. **CLAUDE.md consolidation** — Reorg this session's incrementally-added sections (Jira gotchas, branch-naming/dev-status, portability pointers, docs/plans/ convention) for ordering/redundancy, preserving every non-negotiable verbatim in substance. Verified via before/after grep for load-bearing phrases (e.g. "Don't wire Implement into the poller").

Open question flagged in the plan: whether README.md's Setup section should be replaced by or kept alongside the new QUICKSTART.md pointer — plan assumes "kept alongside" as the lower-risk default.

Full plan with file list and verification steps is at `/Users/cloud-f1/.claude/plans/you-are-looking-at-snappy-bumblebee.md`. The `ExitPlanMode` tool isn't available in this environment, so this message serves as the final plan delivery — no files were edited and no side-effecting commands were run.