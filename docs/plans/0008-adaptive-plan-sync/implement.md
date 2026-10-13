# 0008 — Adaptive plan sync: implementation log

> A log, not a plan written in advance: tick items and add dated entries as work ships. Nothing below is done yet.

## Ticket tree (3 Epics, 10 Stories, about 50 Subtasks; created only after the owner approves this list)

Epic A **Adaptive plan sync: AI and Athena plans to Jira, Linear or Markdown, two-way**

| Story | Subtasks (the last is always the tests) |
|---|---|
| S1 Neutral three-layer plan model | add Category / To-Do layers and ids with old files still loading; layer-size guardrails; marker helpers shared by all sinks; Tests |
| S2 Project profile and adaptive recommendation | profile scan (plan files, tracker key names, history, workflow); Recommendation 1 from usage; Recommendation 2 from content; `plan-sync.json` read/write and `profile --write`; `profile` CLI and tool; Tests |
| S3 `epics-md` source adapter | parse epic files, index and progress into the neutral plan; implementation phases vs acceptance criteria; tolerate both template variants; `import-plan` CLI and tool; Tests |
| S4 Layer-3 To-Do generation | pass implementation phases through; AI split behind the review gate; five-step fallback; 2-to-6 guardrail; Tests |
| S5 Markdown sink | render index, Task files, To-Do checklists; parse checkbox state back; idempotent re-run; `sync-plan --to markdown`; Tests |
| S6 Linear sink | `TrackerClient` create / update / children; ProjectMilestone and sub-issues; Linear status mapping; `sync-plan --to linear`; Tests |
| S7 Two-way status sync | status mapping tables; `--pull` and `--push`; conflict detection and reporting; `on-approve` hook decision; Tests |
| S8 Docs, skills and release | README and QUICKSTART; `CLAUDE.md`; plugin skills (both copies); CHANGELOG, version bump, release script check for `marketplace.json`; Tests of the docs examples where possible |

Epic B **Plan-sync adoption review (stage 2)** — one Story, opened only when Epic A is Done.

| Story | Subtasks |
|---|---|
| S9 Neighbour comparison | inventory each neighbouring project's current practice; run `profile` on each (read-only); table of current practice vs this workflow; pros and cons per project; discussion notes and the rollout decision |

Epic C **Skills, plugin and CLI refresh (stage 3, the very last step)** — one Story, opened only after Epic B is Done, so it can use
what the comparison and the shipped code taught us.

| Story | Subtasks |
|---|---|
| S10 Refresh every skill, plugin file and CLI capability | list every skill, plugin file and CLI command with what is stale or missing (inputs: the stage-2 table and this log); rewrite `symphony-workflow-ops` (both copies) and `jira-ticket-ops`; update plugin metadata and hook messages; improve CLI help, examples and `list-tools` descriptions; `claude plugin validate --strict`, version bump and release; Tests (help-text and registry checks, skill copies in sync) |

## Phases (build order)

- [ ] **Phase 0** — owner approves this folder; create Epic A / Epic B and the Stories and Subtasks above (`add-subtasks --dry-run` first).
- [ ] **Phase 1** — S1, S2, S3, S5 (no external system needed, and it gives a first working path: plan files to Markdown).
- [ ] **Phase 2** — S4 and S6 (Linear is the largest unknown: verify the GraphQL calls on a throwaway project first, as 0007 did for Jira).
- [ ] **Phase 3** — S7, then S8 and the release.
- [ ] **Phase 4 (stage 2)** — Epic B. Not started before everything above is done; the owner asked for this order.
- [ ] **Phase 5 (stage 3, the very last)** — Epic C. Only after stage 2 has been compared, discussed and the current development
  progress reviewed; the owner asked for this order.

## Verification plan

- Unit tests with fake clients and `respx`; no live tracker in the suite.
- Live, read-only first: `profile` and `check-env --verify` on a real project; then a **throwaway Epic or Project** in each tracker
  for create, update, status pull and push, never a consumer's live cards.
- Public-repo scan of every diff for local paths, credentials and consumer project names, as for 0.6.0 and 0.7.0.

## Log

- 2026-10-13 — plan written after a read-only survey of about 20 sibling projects; layer mapping, status ownership, Markdown
  scope, history backfill and the hybrid layer-3 source agreed with the owner. No code yet.
