# Handover (state at v0.5.3, 2026-10-13)

Read this first when you pick the project up. The README is the pitch, `docs/QUICKSTART.md` the walkthrough; this file is the
state of the work and where the next session starts.

## What it is

A Python CLI for the Jira Cloud REST API where every operation is also a schema-described tool call, so an agent can shell out
to it. It also runs the Plan/Implement loop against Jira or Linear, and ships a Claude Code plugin with two skills.

## Links

| What | Where |
|---|---|
| This repo (public, MIT) | <https://github.com/cloud-f1/symphony-workflow> |
| Claude Code plugin in this repo | `plugin/` (`symphony-workflow-ops`, skills `symphony-workflow-ops` and `jira-ticket-ops`), listed by `.claude-plugin/marketplace.json` |
| Companion repo (public, MIT) | <https://github.com/cloud-f1/symphony-walkman> (a status board that reads Jira through this CLI; its marketplace also installs this plugin) |
| Releases | tags `v0.5.0` … `v0.5.3`; run `scripts/release.sh X.Y.Z` after the merge, never before (it tags the current commit) |
| Jira | project **ADLC**, Epic ADLC-27; hierarchy rules in `docs/JIRA_HIERARCHY.md` |
| Install the CLI | `uvx --from git+https://github.com/cloud-f1/symphony-workflow@v0.5.3 symphony-workflow --help` |
| Install the plugin | `/plugin marketplace add cloud-f1/symphony-workflow` then `/plugin install symphony-workflow-ops@symphony-workflow-marketplace` |

## Open items

| Ticket | What |
|---|---|
| ADLC-146 | Jira drift detection and re-sync for decompositions. Plan and log: `docs/plans/0007-jira-diff-sync/` (start at its "Start here"). Phase 0 (Epic) is not done. |
| ADLC-142 | `audit` reports an inflated Story count when Done issues are included (101 reported, 19 exist). Failing test first. |
| ADLC-54 | In Review; the port of the plugin to a consumer repo. |
| Ideas, not committed | Read credentials from `~/.config/symphony-workflow/.env` when no variables are set; make `claude-agent-sdk` optional (about 95 MB for read-only commands); `ruff format` would change 13 files. |

Another session may already have a worktree for ADLC-142 or ADLC-146 under `.worktrees/`; run `git worktree list` before you start.

## Gotchas

- The plugin cache and a repo checkout can disagree: an installed plugin only updates when `version` changes.
- Credentials: the CLI reads `JIRA_BASE_URL`, `JIRA_EMAIL`, `JIRA_API_TOKEN` from the environment or `--env-file`. Never print them.
- `gh pr merge --delete-branch` prints errors inside a worktree but works; confirm with `gh pr view <n> --json state,mergeCommit`.
- Public repo: keep consumer repo names, other companies' Jira keys and local paths out of code, tests and docs (git history keeps what is already there).

## How to continue

1. Fresh worktree under `.worktrees/` off `origin/main`, branch `ADLC-<n>-<slug>`, with Epic → Story → Sub-task in ADLC first.
2. `uv run pytest` and `uv run ruff check .`; for plugin changes `claude plugin validate --strict plugin`.
3. PR, merge, bump version and release, then close tickets bottom-up with `rollup` once the merge is confirmed.
