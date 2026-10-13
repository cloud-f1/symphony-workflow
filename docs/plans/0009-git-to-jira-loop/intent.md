# 0009 — The git half of a ticket (ADLC-336)

## Why

A ticket's code left the tool at the first `git checkout`. Branches were made by hand (and from a
stale local `HEAD` when the worktree manager made them), commit subjects had no rule, merged
branches piled up (23 on the remote after one cycle), and GitHub's "Latest release" sat three
minors behind the tags because `release.sh` tagged and nothing created the Release.

## What Jira really needs

Verified on the live ADLC project: with the GitHub for Jira app connected, a ticket key in a
**branch name** puts the branch, its commits and its PR on the card. The key in a commit subject or
PR title links on its own too. A merged PR stays on the card; the branch entry goes when the remote
branch is deleted. `ADLC` is team-managed, so the "pull request merged" workflow trigger does not
exist there (Automation rules do). Smart commits and the Create branch button are optional.

## Scope

`start`, `pr`, `land`, `check-commit`, `changelog-draft`, `release`, `release-backfill`, a plugin
hook and a `shipping` skill, `docs/JIRA_GIT_SETUP.md`. Not in scope: configuring Jira (an admin does
that, the guide says how), auto-merging, GitLab (the key-in-branch-name rule is the same, `gh` is not).

## Tickets

Epic ADLC-336; Stories ADLC-337 start, 338 land, 339 commit and PR convention, 340 release,
341 plugin, 342 verify the loop for real.
