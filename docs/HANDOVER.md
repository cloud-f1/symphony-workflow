# Handover (state at v0.10.0, 2026-10-14)

Read this first when you pick the project up. The README is the guidance (two languages), `docs/QUICKSTART.md` the walkthrough;
this file is the state of the work and where the next session starts.

## What it is

A Python CLI for Jira Cloud and Linear where every Jira operation is also a schema-described tool call, so an agent can shell out
to it. Three things on top of that:

- the Plan/Implement loop against Jira or Linear (a person always starts Implement);
- **plan sync** (plan `docs/plans/0008-adaptive-plan-sync/`): a plan kept as epics-as-markdown files goes to Markdown, Linear or Jira
  and its To-Do status is kept in step both ways (`profile`, `import-plan`, `sync-plan`, `split-todos`, `sync-status`);
- **the git half of a ticket** (plan `docs/plans/0009-git-to-jira-loop/`): `start`, `pr`, `land`, `check-commit`, `changelog-draft`, `release`
  and `release-backfill`. A ticket key in the branch name is what makes Jira link a branch, its commits and its PR to a card; these
  commands make that name, keep the key on every step, clean up after the merge and publish the release with its note;
- a Claude Code plugin with three skills (`symphony-workflow-ops`, `jira-ticket-ops`, `shipping`) and two hooks (the server health check and a
  commit hook that keeps the ticket key on commits made on a ticket branch).

## Links

| What | Where |
|---|---|
| This repo (public, MIT) | <https://github.com/cloud-f1/symphony-workflow> |
| Claude Code plugin in this repo | `plugin/` (`symphony-workflow-ops`), listed by `.claude-plugin/marketplace.json` |
| Companion repo (public, MIT) | <https://github.com/cloud-f1/symphony-walkman> (a status board that reads Jira through this CLI; its marketplace also installs this plugin) |
| Releases | every tag has a GitHub Release (v0.10.0 is Latest). Release only after the merge: `symphony-workflow release X.Y.Z` (the gate in `scripts/release.sh`, then tag, push the tag, GitHub Release from the CHANGELOG; safe to re-run). It checks `pyproject.toml`, `plugin.json`, `marketplace.json` and the CHANGELOG heading. Steps: the Releasing section of the `symphony-workflow-ops` skill |
| Jira | project **ADLC**; hierarchy rules in `docs/JIRA_HIERARCHY.md`. Epic ADLC-27 (v2) is closed with everything under it Done and `audit ADLC --open` is clean: start new work under a new Epic |
| Install the CLI | `uv tool install git+https://github.com/cloud-f1/symphony-workflow` (add `@vX.Y.Z` to pin) |
| Install the plugin | `/plugin marketplace add cloud-f1/symphony-workflow` then `/plugin install symphony-workflow-ops@symphony-workflow-marketplace` |

## What was verified for real, not only mocked

- The Linear sink, the Jira sink and `sync-status` for both were run against the real services on throwaway objects (a Linear
  project, a Jira tree) and deleted afterwards. That found bugs mocks could not (Linear puts a blank line before lists, so every
  Task looked changed; a Task moved while a To-Do was in conflict; a freely worded tests item was duplicated; a Jira key map naming
  someone else's ticket was trusted). Do the same for any change to a sink: run it once for real, with a cleanup that only deletes
  what the run created.
- `split-todos` ran against a real model.
- The git loop: a throwaway ticket went through `start`, a commit and `pr --draft`, and the card's Development panel showed 1 PR, 1 branch
  and 1 repository about 30 seconds later (the GitHub app syncs asynchronously). `land` refused an open and a closed PR and changed nothing,
  then ran its merged path on real merged PRs; `release 0.10.0` and `release-backfill` (16 historical Releases) ran for real. The ticket,
  branches and worktree the check created were removed; closed PR #58 stays (GitHub cannot delete PRs). Not done: the Jira admin settings
  for smart commits and PR-merged automation (`docs/JIRA_GIT_SETUP.md` says how).
- The README snapshots (`docs/snapshots/`) are captured output, see the README section in `CLAUDE.md`.

## Open items

| Ticket | What |
|---|---|
| README and CI | `README.md` / `README.zh-TW.md` lead with the plugin and the skills, show the real cases as images (`scripts/render_snapshots.py`) and carry badges; `.github/workflows/ci.yml` runs ruff and pytest on 3.11 and 3.13. The skill description was widened after a real agent run showed it never triggered on a plan request: re-run `docs/snapshots/09-agent.txt`'s capture (see the README section in `CLAUDE.md`) after any change to it. GitHub's Latest release is still v0.5.3 (only tags are cut); cut GitHub Releases from the CHANGELOG if you want that page current. |
| Plugin install | Verified for real on 2026-10-14 in an isolated config (`CLAUDE_CONFIG_DIR=<scratch>`, so your own Claude config is untouched): marketplace add, install, both skills, the hook silent in a non-symphony-workflow project. Not exercised: the refresh after a version bump, and an install into another owner checkout (ADLC-327). |
| Companion board | `symphony-walkman` pins this CLI at `@v0.5.2`; bump it after each release. It reads three things: `search`, `docs/runs/<KEY>/run.json` and branch names starting with a Jira key. |
| Ideas, not committed | Read credentials from `~/.config/symphony-workflow/.env` when no variables are set (`init-env` writes the project `.env` only; the companion board already reads the user-level path); make `claude-agent-sdk` optional (about 95 MB for read-only commands); `ruff format` would change about 10 files; `sync-status` does not write the progress matrix back (five step columns vs one tracker status, a decision, reopen ADLC-242 to change it); `on-approve` push mode (only `manual` exists); two plans must not sync into one Jira project (ids are per plan). |

Another session may already have a worktree under `.worktrees/`; run `git worktree list` before you start and ask before deleting any.
`land` removes the worktree of a merged ticket itself.

## Gotchas

- The plugin cache and a repo checkout can disagree: an installed plugin only updates when `version` changes.
- Credentials: the CLI reads `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (and `LINEAR_API_KEY`, `SYMPHONY_WORKFLOW_LINEAR_TEAM` for Linear) from the environment (`uv run --env-file .env ...`; the flag is `uv`'s, not the CLI's). `init-env` creates the file, `check-env [--verify]` reports it without printing values. Never print them or ask for a token in chat.
- `gh pr merge` was refused by the auto-mode classifier ("Merge Without Review") for PRs nobody reviewed, and later worked once the user asked explicitly. Verify, report, and if it is refused let the user run the merge. Confirm with `gh pr view <n> --json state,mergeCommit`.
- A user hook (`git-guard`) blocks any Bash command line that contains both a force-push flag and the word `main`, even in a heredoc: run the push as its own command.
- A squash-merged stack needs `git rebase --onto origin/main <old tip> <branch>` for each branch above the one that merged.
- Jira: a JQL child list lags a few seconds behind a create, and v3 search returns descriptions as ADF (re-read through v2). The Jira sink keeps an id-to-key map in `docs/context/sync-state.json` for that and trusts a key only when it is in the target project and carries the entry's own `sd-id:` marker.
- Public repo: keep consumer repo names, other companies' Jira keys and local paths out of code, tests, docs and snapshots (git history keeps what is already there).

## How to continue

1. Epic → Story → Sub-task in ADLC first, then `symphony-workflow start ADLC-<n>`: a worktree under `.worktrees/` off a fresh `origin/main`, branch `ADLC-<n>-<slug>`.
2. `uv run pytest` and `uv run ruff check .`; for plugin changes `claude plugin validate --strict plugin` and `claude plugin validate --strict .` (the marketplace; they are different checks).
3. Commit with `type(scope): subject (ADLC-<n>)`, `pr ADLC-<n>`, merge, `land ADLC-<n>`; for a release bump the three versions and the CHANGELOG in the PR, then `release X.Y.Z` after the merge; close tickets bottom-up with `rollup` once the merge is confirmed.
