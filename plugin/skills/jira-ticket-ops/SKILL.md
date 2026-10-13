---
name: jira-ticket-ops
description: Audit, create, comment on, transition and roll up Jira tickets for any project through the symphony-workflow CLI and Jira REST, never a Jira MCP server. Use when asked to sync, audit, open or close tickets, or to keep a project's Epic -> Story -> Sub-task hierarchy honest. The project key is a parameter.
---

# Jira ticket ops (CLI + skill)

Use the `symphony-workflow` CLI for every Jira read and write. Fall back to raw Jira REST only for what the CLI has no command for (creating a project, searching with extra fields). Do not use a Jira MCP server for this work.

This skill does not install the CLI. Clone `cloud-f1/symphony-workflow`, run `uv sync --extra dev`, and run commands as `uv run --env-file .env symphony-workflow ...` from that checkout. If the CLI is missing, say so and stop.

## Credentials: one `.env` per project

Linking a project to Jira needs its own credentials, kept in that project's root, never in the plugin and never in git. The CLI has no `--env-file` of its own; the flag belongs to `uv run`, which loads the file into the environment.

Before the first Jira call in a project, check it. The command names missing keys and never prints a value:

```bash
uv run symphony-workflow check-env            # keys set? is .env gitignored?
uv run symphony-workflow check-env --verify   # plus one read-only call to Jira
```

Exit 0 is good, 1 means keys are missing or git would track the file, 3 means `--verify` failed (it says `unauthorized`, `jira-error` or `unreachable`, and which key to fix). Both commands read `.env` themselves, so no `--env-file` is needed; every other command still needs `uv run --env-file .env`. If the check is not good, stop and tell the user. Do not write a token yourself and do not ask for one in chat:

1. `uv run symphony-workflow init-env` creates `.env` with empty keys (owner-only permissions), adds it to `.gitignore` and refuses to overwrite an existing file.
2. The user fills in `JIRA_BASE_URL`, `JIRA_EMAIL` and `JIRA_API_TOKEN` themselves, in their editor or with the `!` prefix in the prompt.
3. Run `check-env --verify` again.

Never print, echo, `cat` or commit `.env`. Credentials from a different Jira site or project must not be reused: one project, one file.

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
| Plan files into a tracker | `sync-plan --to jira --project KEY --dry-run` (Epic / Story / Subtask, idempotent), then without `--dry-run`; `sync-status --to jira` for status; see the operations skill |
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
