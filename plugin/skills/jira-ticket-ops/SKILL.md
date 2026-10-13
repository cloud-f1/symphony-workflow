---
name: jira-ticket-ops
description: Audit, create, comment on, transition and roll up Jira tickets for any project through the symphony-workflow CLI and Jira REST, never a Jira MCP server. Use when asked to sync, audit, open or close tickets, or to keep a project's Epic -> Story -> Sub-task hierarchy honest. The project key is a parameter.
---

# Jira ticket ops (CLI + skill)

Use the `symphony-workflow` CLI for every Jira read and write. Fall back to raw Jira REST only for what the CLI has no command for (creating a project, searching with extra fields). Do not use a Jira MCP server for this work.

This skill does not install the CLI. Clone `cloud-f1/symphony-workflow`, run `uv sync --extra dev`, and run commands as `uv run --env-file .env symphony-workflow ...` from that checkout. `.env` holds `JIRA_BASE_URL`, `JIRA_EMAIL` and `JIRA_API_TOKEN`. Never print or copy them. If the CLI is missing, say so and stop.

## Pick the project key first

Every ticket belongs to the project of the repo you are working on. Take the key from the repo's own rules (for example `ADLC` for symphony-workflow, `PROJ` for symphony-walkman). If the repo names none, ask. Never file work in another project.

## Commands

| Need | Command |
|---|---|
| Read one ticket | `get KEY-12` |
| List or audit by JQL | `search 'project = KEY ORDER BY key ASC'` |
| Check the hierarchy | `audit KEY --open` (read-only; add `--assignee-me` to require an owner) |
| Add Sub-tasks to Stories | `add-subtasks plan.json --dry-run`, then again without it |
| Check Jira against a saved decomposition | `diff-decomposition EPIC-1` (read-only; exit 1 = drift), then `sync-decomposition EPIC-1 --update --dry-run` and without `--dry-run` |
| Comment | `comment KEY-12 "text"` |
| Move a ticket | `transition KEY-12 "In Progress"` |
| Close upward after a merge | `rollup KEY-12 --dry-run`, then without `--dry-run` |
| Anything else | `list-tools`, then `call <tool> '<json>'` (`create_issue` takes `parent`, `epic`, `labels`) |

`plan.json` for `add-subtasks` is `{ "KEY-12": ["Add X", "Wire Y", "Tests: ..."] }`.

## Rules

- Hierarchy is Epic -> Story -> Sub-task. Never build directly under an Epic; decompose first. Full rules: `docs/JIRA_HIERARCHY.md`.
- Create the Sub-tasks when you create the Story, and make the last one the tests.
- Branch, worktree and commit carry the Story key (`KEY-12-short-slug`) so Jira links them.
- Transition Done only after the pull request is merged (`gh pr view <branch> --json state`), then `rollup`.
- Ask the owner before a bulk create or bulk transition. Say exactly what ran and what Jira returned.

## Audit checklist

1. Count tickets by type and status.
2. Run `audit KEY --open` and read every error.
3. Check that no Done Story has an open Sub-task.
4. Report numbers. Fix only what was asked.
