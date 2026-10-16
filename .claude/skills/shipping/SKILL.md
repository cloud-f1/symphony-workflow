---
name: shipping
description: >
  Use when a ticket's code goes from idea to released: start work on a ticket, commit, open a
  pull request, merge and clean up, cut a release with a tag, changelog and GitHub Release.
  Trigger on: "start ADLC-12", "make a branch for this ticket", "open a PR for this ticket",
  "I merged it, clean up", "land this", "release 0.4.0", "tag a release", "write the changelog",
  "release notes", "GitHub releases are behind the tags". Jira links a branch, its commits and its
  PR to a card because the ticket key is in the branch name, so this skill makes that name and
  keeps the key on every step. Read the ticket first; never push, merge or publish without the
  user's go-ahead.
---

# Shipping a ticket: branch, commit, PR, merge, release

One loop, five commands (`symphony-workflow <command> --help` has the detail):

| Step | Command | What it does |
|---|---|---|
| 1 | `start <KEY>` | Branch `{KEY}-{slug}` and a worktree from a **fresh `origin/main`**, card to In Progress |
| 2 | commit | Subject `type(scope): subject (KEY)`; `check-commit` checks one |
| 3 | `pr <KEY>` | Push, open a PR whose title and body carry the key, card to In Review |
| 4 | `land <KEY>` | After the merge: verify on GitHub, update main, delete the remote branch and worktree, card Done and rolled up |
| 5 | `release X.Y.Z` | `scripts/release.sh` gate and tag, push the tag, GitHub Release from the CHANGELOG |

## Why the branch name is the whole integration

Jira's GitHub app puts a branch, its commits and its pull request on a card when it finds the
ticket key in the **branch name**. Nothing else needs configuring, which is why `start` builds the
name from the ticket and refuses a Done or missing ticket. The key in the commit subject and the PR
title is the backup for work done outside the branch. Optional Jira settings (smart commits, the
Create branch button, moving a card when a PR merges) are in `docs/JIRA_GIT_SETUP.md`.

## Rules that are easy to get wrong

- **Start from `origin/main`, not a stale local branch.** `start` fetches first and says which commit
  it branched from.
- **Never force.** `land` leaves a worktree with uncommitted changes alone and says so (exit 1).
- **`land` trusts GitHub, not an exit code.** It reads the PR's state and merge commit, and checks the
  merge commit is on `origin/main`. An open PR changes nothing.
- **A deleted remote branch drops the card's branch entry, not the PR.** The PR and commits stay on
  the card; that is expected, keep the PR as the record.
- **Squash merges leave the local branch looking unmerged.** `land` deletes it because the PR is
  verified merged; do not use `git branch --merged` as the test.
- **A release needs a merged version bump first.** Bump `pyproject.toml`, `plugin.json`,
  `.claude-plugin/marketplace.json` and the CHANGELOG in one PR, merge it, then `release`.
  `changelog-draft X.Y.Z` writes the section from the commits; edit it, do not paste it blind.
- **`release` is safe to re-run** (tag, push and Release each skip when done). `release-backfill
  --dry-run` lists tags that have no GitHub Release; creating them is public, so ask first.
- **`start`, `pr`, `land`, `release` and `release-backfill` are CLI commands, not `call` tools.** They change git, GitHub
  and the tracker, and `call` has no confirmation or dry run. Only the read-only `check_commit_subject` and
  `changelog_draft` are tools.
- **A card already in the target status is left alone.** A Jira automation may have closed it when the PR merged; `land`
  then says "already Done" and still rolls the parents up.
- **`--no-transition`** on `start`, `pr` and `land` leaves the card alone when its status should not move.
- In a Jira project, the card moves only through the project's own transitions; if one is missing the
  command reports it and finishes the git side.

## A good session

1. Read the ticket (`get KEY`), `start KEY`, work in the printed worktree.
2. Commit small, each subject ending in `(KEY)`; the plugin hook blocks a `git commit -m` on a ticket
   branch that forgets it.
3. `pr KEY`, wait for CI, ask the user to merge (or merge when they have said to).
4. `land KEY`, then confirm `git status` is clean on `main` and the card is Done.
5. Release only when asked: bump, merge, `release X.Y.Z`, check the Release page.
