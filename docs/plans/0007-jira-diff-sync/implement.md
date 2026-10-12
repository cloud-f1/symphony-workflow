# 0007 — Jira drift detection and re-sync: implementation log

> A log, not a plan written in advance: tick items and add dated entries as work ships. Nothing below is done yet.

## Start here (for the session that picks this up)

1. Work from `origin/main` (0.5.2 or later). A long-lived local checkout may be behind — `git fetch` first, and use a fresh worktree under `.worktrees/`.
2. Read, in order: `CLAUDE.md` (Jira API gotchas, hard rules), `docs/JIRA_HIERARCHY.md`, this folder (`intent.md`, `spec.md`), `src/symphony_workflow/{sync_decomposition,decompose,hierarchy,rollup}.py`, `tests/test_sync_decomposition.py`.
3. Phase 0 below is mandatory before any code.
4. The person who handed this over holds the consumer-side context (real keys, a live multi-Story Epic to validate against, the adapter that will call these commands). Ask for it; it is intentionally not in this public repo.

## Phases

### Phase 0 — track the work (repo rule)
- [ ] Create the ADLC Epic for this plan; decompose it (`decompose` → review `decomposition.md` → `sync-decomposition`), or hand-write Stories matching the phases below. Link this folder.

### Phase 1 — identity and bookkeeping
- [ ] Optional `id` on `Story`/`Task` (backward compatible `model_validate` of old files); generate-and-write-back helper.
- [ ] Marker line in descriptions; `sync-state.json` read/write; `--adopt` matching (exact summary, then order) with a printed proposal and `--yes` to write.
- [ ] Tests: old file without ids still loads; ids are stable across re-runs; adopt matches/ambiguities.

### Phase 2 — `diff-decomposition` (read-only)
- [ ] Traversal via `list_children`/`get_issue`; per-entry classification as in `spec.md` §2; normalization of descriptions.
- [ ] CLI command + `--json`; exit codes 0/1/2/3; tool-registry entry if cheap.
- [ ] Tests with a fake client: ok, missing, summary-changed, description-changed, orphan, parent-differs, epic mismatch, empty Epic.

### Phase 3 — `sync-decomposition --update`
- [ ] create-missing → update-changed; `--dry-run`; `--status`; stop-at-first-error reporting; idempotent re-run test.
- [ ] Decide and document the behaviour of plain `sync-decomposition` on an Epic that already has children.

### Phase 4 — live validation (read-only first)
- [ ] Run `diff-decomposition` against a real Epic with several Stories/Subtasks (ask the person who handed this over for one). Expected findings to look for: an Epic whose own status lags its children; Story descriptions edited by hand after creation; a Subtask that exists only in Jira. Confirm description normalization does not report false drift on untouched cards.
- [ ] Prove `--update` on a **throwaway Epic in the ADLC project**, never on a consumer's live cards while developing.

### Phase 5 — docs and release
- [ ] README, `CLAUDE.md`, `docs/JIRA_HIERARCHY.md` (new "content drift" section), the skill copies (`symphony-workflow-ops` canonical + plugin copy, kept in sync by hand, and `jira-ticket-ops`), CHANGELOG `Unreleased`, version bump (minor), `docs/plans/INDEX.md` already lists this folder.
- [ ] Release per the repo's flow; note the new commands in the plugin skill so a session that installs the plugin sees them.

## Open questions for the owner
1. Should plain `sync-decomposition` refuse on an Epic that already has children (safer, but a behaviour change)?
2. Should the Epic be transitioned to In Progress when its first child starts, and if so in this tool or in each consumer?
3. Is per-Story `expected status` worth adding now, or only when a consumer asks?

## Log
- 2026-10-12 — plan written as a handover; no code yet.
