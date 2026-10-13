# Handover (state at v0.9.2, 2026-10-13)

Read this first when you pick the project up. The README is the guidance (two languages), `docs/QUICKSTART.md` the walkthrough;
this file is the state of the work and where the next session starts.

## What it is

A Python CLI for Jira Cloud and Linear where every Jira operation is also a schema-described tool call, so an agent can shell out
to it. Three things on top of that:

- the Plan/Implement loop against Jira or Linear (a person always starts Implement);
- **plan sync** (plan `docs/plans/0008-adaptive-plan-sync/`): a plan kept as epics-as-markdown files goes to Markdown, Linear or Jira
  and its To-Do status is kept in step both ways (`profile`, `import-plan`, `sync-plan`, `split-todos`, `sync-status`);
- a Claude Code plugin with two skills (`symphony-workflow-ops`, `jira-ticket-ops`).

## Links

| What | Where |
|---|---|
| This repo (public, MIT) | <https://github.com/cloud-f1/symphony-workflow> |
| Claude Code plugin in this repo | `plugin/` (`symphony-workflow-ops`), listed by `.claude-plugin/marketplace.json` |
| Companion repo (public, MIT) | <https://github.com/cloud-f1/symphony-walkman> (a status board that reads Jira through this CLI; its marketplace also installs this plugin) |
| Releases | tags `v0.5.0` … `v0.9.2`. Release only after the merge: `scripts/release.sh X.Y.Z`, then `git push origin vX.Y.Z`. It checks `pyproject.toml`, `plugin.json`, `marketplace.json` and the CHANGELOG heading. Steps: the Releasing section of the `symphony-workflow-ops` skill |
| Jira | project **ADLC**; hierarchy rules in `docs/JIRA_HIERARCHY.md` |
| Install the CLI | `uv tool install git+https://github.com/cloud-f1/symphony-workflow` (add `@vX.Y.Z` to pin) |
| Install the plugin | `/plugin marketplace add cloud-f1/symphony-workflow` then `/plugin install symphony-workflow-ops@symphony-workflow-marketplace` |

## What was verified for real, not only mocked

- The Linear sink, the Jira sink and `sync-status` for both were run against the real services on throwaway objects (a Linear
  project, a Jira tree) and deleted afterwards. That found bugs mocks could not (Linear puts a blank line before lists, so every
  Task looked changed; a Task moved while a To-Do was in conflict; a freely worded tests item was duplicated; a Jira key map naming
  someone else's ticket was trusted). Do the same for any change to a sink: run it once for real, with a cleanup that only deletes
  what the run created.
- `split-todos` ran against a real model.
- The README snapshots (`docs/snapshots/`) are captured output, see the README section in `CLAUDE.md`.

## Open items

| Ticket | What |
|---|---|
| ADLC-54 | In Review and has no Sub-tasks (the only `audit ADLC --open` error). Needs the owner's plan, then `add-subtasks`. |
| ADLC-186 | This file. Done once it is merged. |
| Companion board | `symphony-walkman` pins this CLI at `@v0.5.2`; bump it after each release. It reads three things: `search`, `docs/runs/<KEY>/run.json` and branch names starting with a Jira key. |
| Ideas, not committed | Read credentials from `~/.config/symphony-workflow/.env` when no variables are set (`init-env` writes the project `.env` only; the companion board already reads the user-level path); make `claude-agent-sdk` optional (about 95 MB for read-only commands); `ruff format` would change about 10 files; `sync-status` does not write the progress matrix back (five step columns vs one tracker status, a decision, reopen ADLC-242 to change it); `on-approve` push mode (only `manual` exists); two plans must not sync into one Jira project (ids are per plan). |

Another session may already have a worktree under `.worktrees/` (several merged ones are still there, none is needed); run
`git worktree list` before you start and ask before deleting any.

## Gotchas

- The plugin cache and a repo checkout can disagree: an installed plugin only updates when `version` changes.
- Credentials: the CLI reads `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` (and `LINEAR_API_KEY`, `SYMPHONY_WORKFLOW_LINEAR_TEAM` for Linear) from the environment (`uv run --env-file .env ...`; the flag is `uv`'s, not the CLI's). `init-env` creates the file, `check-env [--verify]` reports it without printing values. Never print them or ask for a token in chat.
- `gh pr merge` was refused by the auto-mode classifier ("Merge Without Review") for PRs nobody reviewed, and later worked once the user asked explicitly. Verify, report, and if it is refused let the user run the merge. Confirm with `gh pr view <n> --json state,mergeCommit`.
- A user hook (`git-guard`) blocks any Bash command line that contains both a force-push flag and the word `main`, even in a heredoc: run the push as its own command.
- A squash-merged stack needs `git rebase --onto origin/main <old tip> <branch>` for each branch above the one that merged.
- Jira: a JQL child list lags a few seconds behind a create, and v3 search returns descriptions as ADF (re-read through v2). The Jira sink keeps an id-to-key map in `docs/context/sync-state.json` for that and trusts a key only when it is in the target project and carries the entry's own `sd-id:` marker.
- Public repo: keep consumer repo names, other companies' Jira keys and local paths out of code, tests, docs and snapshots (git history keeps what is already there).

## How to continue

1. Fresh worktree under `.worktrees/` off `origin/main`, branch `ADLC-<n>-<slug>`, with Epic → Story → Sub-task in ADLC first.
2. `uv run pytest` and `uv run ruff check .`; for plugin changes `claude plugin validate --strict plugin`.
3. PR, merge, bump version and release, then close tickets bottom-up with `rollup` once the merge is confirmed.
