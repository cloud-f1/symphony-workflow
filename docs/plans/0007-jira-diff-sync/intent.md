# 0007 — Jira drift detection and re-sync for decompositions: intent

Status: **handed over, not started** (written 2026-10-12 by a session in a consumer repo; implement in this repo).

## Problem

`decompose` / `sync-decomposition` turn a reviewed `decomposition.json` into Jira Stories and Subtasks, **once**.
After that nothing keeps the two sides aligned:

- `sync-decomposition` never updates an existing card; running it again would create duplicates. It does not
  record which Jira key belongs to which decomposition entry (`SyncResult` is printed, not stored).
- `audit` checks the **structure** (Story without Subtasks, orphan Story, Done Story with open Subtasks, …); it
  does not compare a card's **content** with the spec it came from.
- `add-subtasks` only adds Subtasks to a Story that has none.
- `rollup` closes work upward; nothing moves a status or a changed scope back into Jira.

Real consequences seen in a consumer repo whose epics are markdown files (specs are the source of truth, Jira is a
mirror) when it used this tool for a batch of five Stories / forty Subtasks:

1. The Epic stayed *To Do* while every child was already *In Progress* (nothing transitions the Epic on the way down).
2. After the spec changed, Story descriptions and Subtask lists in Jira silently went stale; a second run said
   "already mapped — skipped". The only way to correct a card was hand-calling `update_issue` and writing the change in a log.
3. A Subtask was added to Jira by hand for a gap found during review; the spec file did not know about it, so a
   later comparison would have to treat it as drift rather than as an error.
4. Every consumer repo ends up writing its own adapter (key map file, create/start/close script, a private copy of the
   roll-up logic) because the shared layer stops at "create".

## Goals

1. `symphony-workflow diff-decomposition <epic-key>` — **read-only**: compare `docs/runs/<epic>/decomposition.json`
   with the live Epic → Stories → Subtasks and print what differs (missing cards, changed summary/description,
   cards that exist only in Jira, statuses that disagree with the optional expected status).
2. `symphony-workflow sync-decomposition <epic-key> --update [--dry-run]` — apply the safe part of that diff:
   create what is missing, update summary/description that changed. Idempotent; re-running changes nothing.
3. A stable identity between a decomposition entry and its Jira key, so renames in the spec update the card instead of
   creating a duplicate (see `spec.md`).
4. Output and exit codes usable by a consumer's CI / session hook (`--json`; exit 0 clean, 1 drift, 2 bad input, 3 Jira error).
5. Keep the consumer side thin: a consumer converts *its* spec format into `decomposition.json`, nothing more.

## Non-goals

- **Jira → repo** sync. The spec file stays the single source of truth; edits made in Jira are reported as drift, not imported.
- Deleting or re-parenting issues (the Jira tools here have no such operation; removed entries are flagged, never deleted).
- Changing the decompose agent, the poller, the Plan/Implement loop, or Linear support (these commands stay Jira-only).
- Replacing `audit`: structural rules stay there; this adds *content* comparison against a spec.

## Definition of done

- [ ] ADLC Epic for this work exists and is decomposed (repo rule: never build directly under an Epic); this plan folder is linked from it.
- [ ] `diff-decomposition` and `sync-decomposition --update` implemented with unit tests against a fake client (existing patterns in `tests/test_sync_decomposition.py`, `tests/test_rollup.py`); ruff + pytest green; coverage not lower.
- [ ] A live read-only `diff-decomposition` run against a real multi-Story Epic gives the expected findings (list in `implement.md`), and a throwaway Epic proves the `--update` path end to end.
- [ ] README, `CLAUDE.md`, `docs/JIRA_HIERARCHY.md`, the ops skill copies (canonical `.claude/skills/...` and `plugin/skills/...`, kept in sync by hand, plus `jira-ticket-ops`), CHANGELOG `Unreleased`, and the version bump (new commands ⇒ minor) updated.
- [ ] A consumer-side adapter contract is documented here (how a consumer produces `decomposition.json`); the consumer's own migration is tracked in the consumer's repo, not here.
