# 0010 — Idempotent card creation (handover, not started)

Status: **handover**. Nothing is built. Whoever picks this up: read this file, then `spec.md`, then follow "First steps".

## Why

A consumer project creates many cards from one piece of work (a batch of fixes and findings). Doing it one `create_issue` call at a time
went wrong in three ways in a single session:

1. **A duplicate.** The card already existed, nobody searched first, and a second one was created (then deleted by hand).
2. **Wrong state.** A card was created In Progress when it should have been To Do, and nothing checked it afterwards.
3. **Missing cards.** Some fixes never got a card at all, because there was no list to compare against.

The consumer fixed it locally with a manifest plus a small script. It works and is tested, but the same three guarantees belong in the
tool every consumer already installs, so each project does not rebuild them.

## What the CLI lacks today

| Need | Today |
|---|---|
| Rerunning a create must not create a second card | `create_issue` never looks for an existing card |
| The result must be checked, not assumed | no read-back after create or update |
| A card made by hand must be adoptable | `update_issue` only changes summary, description and parent: it cannot add a label |

## Goals

- A rerun of the same batch creates **zero** new cards.
- Every card the tool creates or changes is read back; a mismatch is a non-zero exit, not a warning.
- An existing card can be adopted (given the identity label) without creating anything.
- Dry-run is the default. Writing needs an explicit `--apply`.

## Non-goals

- Linear. `tools.py` and these commands stay Jira-only, like `decompose` (see plan 0005).
- Changing the default behaviour of `create_issue`. Everything here is opt-in.
- Deduplicating by summary text. Summaries are edited; a stable label is the identity.
- Reopening a Done card. Ever.

## Definition of done

- Unit tests for every rule in `spec.md`, plus a mutation check: remove each guard once and see a test fail, then restore it.
- `symphony-workflow audit <PROJECT> --open` is clean before and after.
- The `jira-ticket-ops` skill, the README (both languages) and `CHANGELOG.md` describe the new behaviour.
- Released only through the existing gate (`scripts/release.sh`), after the merge.

## First steps (this repo's own rules apply)

1. `git fetch origin && git status`; report the baseline commit you work from.
2. Read `CLAUDE.md` and load the `symphony-workflow-ops` skill.
3. File an ADLC Epic for this plan. **Never build directly under an Epic**: run `symphony-workflow decompose <epic>`, review
   `decomposition.md`, then `sync-decomposition`. Subtasks for the pieces in `spec.md` are the natural split.
4. Use `symphony-workflow start` so the branch and worktree carry the ticket key. Every commit subject keeps the key.
5. Add a row to `docs/plans/INDEX.md` and keep `implement.md` here as the build log.
