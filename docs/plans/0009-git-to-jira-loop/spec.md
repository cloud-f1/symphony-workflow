# 0009 — Design

- `ship.py` runs git and gh through one `Runner`, so tests use a real throwaway repo with a local
  bare origin and a scripted gh. Mutation checks (branch from `HEAD`, accept an open PR, force the
  worktree removal) each fail a test.
- **start** fetches, branches from `origin/main`, reuses an existing worktree, moves the card unless
  it is already In Progress. **land** order: verify the PR merged, verify the merge commit is on
  `origin/main`, fast-forward `main` (only when it is checked out, clean), delete the remote branch
  (skip when already gone), remove the worktree (never forced), delete the local branch, move the card.
  Any refusal happens before anything is changed.
- **Commit subject**: `type(scope): subject (KEY)` with an optional squash suffix; merge, revert,
  fixup subjects pass. The plugin hook mirrors the pattern in a stdlib script (a test keeps the two
  in agreement) and acts only on a branch that starts with a ticket key, so projects that do not use
  tickets never see it.
- **release** wraps `scripts/release.sh` (still the gate), then pushes the tag and creates the Release
  with the CHANGELOG section as the note; every step skips when done. **release-backfill** marks only
  the newest tag Latest.
- The tracker is the existing `TrackerClient`; roll-up happens only for Jira.

## Verified for real (2026-10-14)

A throwaway ticket went through `start` (branch from `origin/main`, card In Progress, second run
reuses the worktree), a commit and `pr --draft`; the card's Development panel then showed 1 PR, 1
branch, 1 repository after about 30 seconds. `land` refused an open and a closed PR and changed
nothing; its merged path ran against the really merged PR #38 (verified, main fast-forwarded,
remote branch already gone). gh's JSON field names were read from a real merged PR. The ticket,
branches and worktree were removed afterwards; the closed throwaway PR remains on GitHub.

## Follow-ups (ADLC-393)

`start`, `pr`, `land`, `release` and `release-backfill` stay CLI-only: `call` has no confirmation or dry
run and can be driven unattended by any agent. The two read-only pieces are tools, `check_commit_subject`
and `changelog_draft`, the second through `ship.hardened_run` (git via `safepath.run_git`) because its
path is an agent's argument; without it a repository's `gpg.program` ran, shown by a test. A card
already in the target status is skipped, because the Jira "pull request merged" automation can close it
first; `land` still rolls up, and a failed roll-up is reported without failing the git side.

## Decisions taken

Deleting the remote branch after merge drops the card's branch entry but keeps the PR; the PR is the
record. `--no-transition` on `start`, `pr` and `land`. Backfilling the historical Releases was
approved by the user.
