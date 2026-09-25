---
jira_url: https://cloud-f1.atlassian.net/browse/ADLC-53
---

## ADLC-53 — Implementation Plan: Rename jira-agent-cli → symphony-workflow

**This is scoping only — no files were changed.** Per the ticket's explicit gate, this plan-phase pass does not execute the rename; it identifies what a `docs/plans/0003-repo-rename/` cycle needs to decide and touch.

### Two decisions that need explicit human confirmation before any execution

1. **New local checkout path** — recommend `~/Documents/git_saas/symphony-workflow`. Requires stopping the running API server/poller first, since this session's own processes/worktrees point at the current path.
2. **Whether the Python package `jira_agent_cli` also gets renamed** — recommend **no, not in this ticket**. `jira-agent-cli` (repo/docs name) has 32 references; `jira_agent_cli` (package/module) has 44, including every import, `pyproject.toml`'s `[project.scripts]` entry, and the installed `jira-agent` console command. Bigger, separate blast radius — recommend a follow-up ticket if wanted, possibly via `decompose`/`sync-decomposition` into a Story with Subtasks.

### Recommended approach (once confirmed)

1. GitHub repo rename (`gh repo rename` or UI) — auto-redirect mitigates but doesn't eliminate breakage of links already posted in this session's Jira comments.
2. Local checkout `mv` + `git remote set-url` + server restart + `/health` check.
3. Sweep of `CLAUDE.md`, `docs/PORTABILITY.md`, forward-looking sections of `docs/plans/**` (leave historical `implement.md` logs as-is), `.claude-plugin/marketplace.json`, `plugin/.claude-plugin/plugin.json` (including its `homepage` URL), and likely `.claude/skills/jira-agent-cli-ops/` itself (its trigger phrases name the old repo) — all in one commit to avoid a broken interim state.
4. Leave `src/jira_agent_cli/`, `pyproject.toml`'s `name`, and all imports untouched (per decision #2).

### Key risks

- This session's own worktrees/server hold references to the old path — must be quiesced before the `mv`.
- Jira comments already posted this session with old GitHub links become redirect-dependent, not dead.
- Skill-directory rename must land atomically with marketplace/plugin.json edits, or the skill becomes undiscoverable mid-change.

Full detail written to the plan file.