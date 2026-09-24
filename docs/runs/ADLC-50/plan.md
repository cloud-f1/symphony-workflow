---
jira_url: https://cloud-f1.atlassian.net/browse/ADLC-50
---

Exploration agent is running in the background to gather details on the skill directory, hook wiring, existing plugin scaffolding, and portability docs. I'll continue once it reports back.
# ADLC-50 Investigation Findings

**Note first:** the worktree (`.worktrees/ADLC-50`, branch `ADLC-50-backlog-...`, HEAD `38b94b1`) is behind `main` (HEAD `c04d03c`). `.claude/settings.json` and `.claude/hooks/` were added in a later main commit (`3c50a61`) that this branch doesn't have — that's why they're invisible in the worktree. Items 2 below come from the main checkout at `/Users/cloud-f1/Documents/git_saas/jira-agent-cli/.claude/`.

**1. `.claude/skills/jira-agent-cli-ops/`**
Only one file exists: `.claude/skills/jira-agent-cli-ops/SKILL.md`. Frontmatter:
```yaml
name: jira-agent-cli-ops
description: >
  Use when operating jira-agent-cli itself — starting the API server, driving a
  ticket through Plan then Implement, reading poller status, or picking up a
  ticket by hand. Trigger on: "run jira-agent-cli", "plan this ticket", "start
  the poller", "jira-agent-cli server", "ADLC ticket", or any request to drive
  a Jira ticket through this repo's Plan/Implement loop...
```
Body sections: Quick start (`uv sync`, `.env` setup, `uvicorn ... --port 8811`), "The three ways to drive a ticket" (manual API calls, auto-discovery poller, CLI passthrough), Branch naming rule, Safety invariants (Plan/Implement gate in `RunStore`, workspace isolation, "Never `claude -p`", poller never auto-Implements), Known limitations (`RunStore` in-memory, `acceptEdits` can't run Bash, per-worktree `uv sync`), and a Portability section pointing to `docs/PORTABILITY.md`.

**2. SessionStart hook (main checkout, not this worktree)**
- `.claude/settings.json`:
```json
{"hooks":{"SessionStart":[{"hooks":[{"type":"command","command":"/Users/cloud-f1/Documents/git_saas/jira-agent-cli/.claude/hooks/session-start-check-api.sh","timeout":15,"statusMessage":"Checking jira-agent-cli API server..."}]}]}}
```
No matcher restriction — fires on every session start. Command path is **absolute**, hardcoded to this machine.
- `.claude/hooks/session-start-check-api.sh`: checks `GET http://127.0.0.1:8811/health`; auto-starts `uv run --env-file .env uvicorn jira_agent_cli.api:app --port 8811` (nohup/disown, logs to `/tmp/jira-agent-api.log`) if not running; then queries Jira (v3 `/search/jql`, project=ADLC, status="To Do", excluding Epic/Subtask) and emits a `SessionStart` `hookSpecificOutput.additionalContext` JSON blob via `jq` summarizing server status + ready tickets. Both committed to git (`git ls-files .claude/` confirms, commit `3c50a61`).

**3. Existing plugin scaffolding**
None found repo-wide: no `.claude-plugin/`, `plugin.json`, or `marketplace.json` anywhere (searched both the worktree and main checkout).

**4. `docs/PORTABILITY.md`**
Describes adopting jira-agent-cli's *orchestrator* (CLI/API/poller) for another repo via env vars (`JIRA_AGENT_CLI_TARGET_REPO`, `JIRA_AGENT_CLI_JIRA_PROJECT`, `JIRA_AGENT_CLI_WORKSPACE_ROOT`) — clone/`uv add` it, point env at the target repo, run `uvicorn`. It's entirely about the *server/orchestrator*, not the Claude Code skill+hook layer — it never mentions `.claude/`, meaning today there's no documented path for another repo to pick up the ops skill/hook without manually copying `.claude/skills/jira-agent-cli-ops/SKILL.md` and `.claude/hooks/session-start-check-api.sh` + editing the absolute path in `settings.json`. That copy-paste friction is what ADLC-50 targets.

**5. `docs/intent.md` Tracking table**
Phases 0–5 all "Done" (Epic `ADLC-2`, POC closed 2026-09-25). ADLC-50 isn't in this table (it's backlog under a later Epic 2 wave, not part of the original phase table) — for its Epic 2 context see item 6 below.

**6. Prior ADLC-50 research**
No dedicated `docs/research/` file exists. The write-up lives inline in `docs/context/session-summary.md` (lines 36–41): confirms research happened same-day and gives the verdict — **"hybrid only, the actual CLI/server/poller can't be plugin-installed (no Python dependency auto-install in the plugin system)"** — i.e., only the skill + SessionStart hook are plugin-packageable, not the Python backend.

**7. Top-level structure**
`.claude/` (skills/ only, in this worktree), `.env.example`, `CLAUDE.md`, `README.md`, `docs/` (`context/`, `runs/`, `intent.md`, `spec.md`, `plan.md`, `PORTABILITY.md`, `WORKFLOW.md`), `src/jira_agent_cli/`, `tests/` (9 test files), `pyproject.toml`, `uv.lock`.
Plan-design agent is running in the background to work out the file-level plugin structure and open questions. I'll write the final plan once it reports back.
## Implementation Plan — ADLC-50: Package ops skill + SessionStart hook as a Claude Code plugin

### 1. New file tree

Per Claude Code plugin conventions, plugin manifest lives in `.claude-plugin/plugin.json` at the plugin root, but `skills/` and `hooks/` live as **siblings** of `.claude-plugin/`, not nested inside it. Repo-root plugin layout:

```
jira-agent-cli/
├── .claude-plugin/
│   └── plugin.json
├── skills/
│   └── jira-agent-cli-ops/
│       └── SKILL.md
├── hooks/
│   ├── hooks.json
│   └── session-start-check-api.sh
```

`plugin.json`:
```json
{
  "name": "jira-agent-cli-ops",
  "version": "0.1.0",
  "description": "Ops skill + SessionStart health-check/ready-ticket hook for jira-agent-cli",
  "author": {"name": "cloud-f1"}
}
```
No explicit `skills`/`hooks` pointer fields needed — Claude Code auto-discovers `skills/*/SKILL.md` and `hooks/hooks.json` by convention relative to plugin root. Keep it minimal; don't hand-list paths that convention already resolves.

`hooks/hooks.json`:
```json
{
  "hooks": {
    "SessionStart": [
      {"hooks": [{"type": "command", "command": "${CLAUDE_PLUGIN_ROOT}/hooks/session-start-check-api.sh", "timeout": 15, "statusMessage": "Checking jira-agent-cli API server..."}]}
    ]
  }
}
```
This fixes the absolute-path portability bug for free, as noted in the ticket.

### 2. Relocating skill/hook

- `git mv .claude/skills/jira-agent-cli-ops/SKILL.md skills/jira-agent-cli-ops/SKILL.md` — content unchanged (frontmatter `name`/`description` still valid).
- `git mv .claude/hooks/session-start-check-api.sh hooks/session-start-check-api.sh`, edit only the log path if it referenced repo-relative paths elsewhere (check for `$(dirname "$0")`-relative assumptions inside the script that assumed `.claude/hooks/`).
- Retire `.claude/settings.json`'s hook entry (superseded by `hooks/hooks.json`); no plugin equivalent needed for the settings.json hook block once plugin ships.

### 3. Keep, remove, or fallback for `.claude/`

Recommend **remove**, not dual-maintain. Rationale: this repo is itself the primary consumer (dogfooding), and two copies of the same skill/hook drift silently (explicitly flagged as a risk below). Once the plugin works for this repo's own session, delete `.claude/skills/` and `.claude/hooks/`, and strip the `SessionStart` block from `.claude/settings.json` (or delete the file if nothing else lives there — check first). If `.claude/settings.json` has other keys (permissions, etc.), keep the file, remove only the hook entry.

### 4. Marketplace distribution

Same-repo marketplace (simplest for a single-org private repo, no new repo needed): add `.claude-plugin/marketplace.json` at repo root (sibling to `plugin.json`, both under `.claude-plugin/`):
```json
{
  "name": "jira-agent-cli-marketplace",
  "owner": {"name": "cloud-f1"},
  "plugins": [
    {"name": "jira-agent-cli-ops", "source": "./", "description": "..."}
  ]
}
```
Adopters run `/plugin marketplace add cloud-f1/jira-agent-cli` (GitHub shorthand works for private repos the user has access to; Claude Code auths via existing `gh`/git credentials) then `/plugin install jira-agent-cli-ops@jira-agent-cli-marketplace`. No git remote/visibility changes required — private repo access is sufficient, no need to make it public. Don't create a separate marketplace repo; that's unwarranted overhead for one plugin.

### 5. Hardcoded path + ADLC JQL — recommendation

**Absolute hook path**: in scope, fixed automatically by `${CLAUDE_PLUGIN_ROOT}` substitution — no extra work beyond the move.

**`project=ADLC` JQL**: **out of scope for this ticket**, file as a follow-up. Reasoning: `docs/PORTABILITY.md` already solved this exact problem for the orchestrator via `JIRA_AGENT_CLI_JIRA_PROJECT` env var — the hook script should read the same env var (sourcing `.env` or inheriting the shell env) instead of a literal `ADLC` string, but wiring that through is a behavior change to the hook script's logic, not a packaging change. Bundling it here conflates two concerns and risks scope creep on a ticket whose stated deliverable is "package, don't redesign." Open a follow-up ticket (e.g. ADLC-51) referencing `docs/PORTABILITY.md`'s existing pattern.

### 6. Doc updates

- `docs/PORTABILITY.md`: new `## Using the Claude Code plugin` section — marketplace add/install commands, note that plugin covers skill+hook only, CLI/server still needs manual `uv sync`.
- `docs/intent.md`: add a Tracking table row under Epic `ADLC-2` for ADLC-50 alongside existing milestones (line ~58 area).
- `docs/context/session-summary.md`: handover note — plugin shipped, `.claude/` removed, where marketplace.json lives.
- `README.md`: near existing skill mention (line 37/43) add a line pointing at plugin install instead of hand-copying `.claude/skills/shared/...`.

### 7. Verification plan (no pytest)

1. From a scratch second repo (or a throwaway clone), run `/plugin marketplace add cloud-f1/jira-agent-cli` then `/plugin install jira-agent-cli-ops@jira-agent-cli-marketplace` in a fresh Claude Code session.
2. Confirm `/plugin list` shows it enabled; confirm skill appears namespaced (invoke via a trigger phrase like "run jira-agent-cli" and confirm it loads `jira-agent-cli-ops:jira-agent-cli-ops` context, or check `/skills` listing).
3. Start a new session in that scratch repo; confirm `SessionStart` hook fires (statusMessage visible), check hook's resolved command path via `claude --debug` or hook log output to confirm `${CLAUDE_PLUGIN_ROOT}` resolved to the plugin's installed cache dir, not a hardcoded path.
4. Kill any local port-8811 server first, confirm hook auto-starts it and `/tmp/jira-agent-api.log` shows expected uvicorn boot lines.
5. Uninstall (`/plugin uninstall`), confirm hook/skill disappear cleanly.

### 8. Risks / open questions

- Plugin `version` in `plugin.json` vs. package version in `pyproject.toml` — no automated sync; document manually bump both on release, or defer versioning rigor until adoption beyond this repo.
- Transition drift: mitigate by deleting `.claude/` copies same-PR as adding the plugin (per §3), not phasing.
- Single-repo marketplace vs. dedicated marketplace repo: revisit only if a second plugin or external (non-cloud-f1) consumer appears; not needed now.
- Confirm target Claude Code version supports plugins/`${CLAUDE_PLUGIN_ROOT}` in this org's rollout before relying on it as the only distribution path.

### Critical Files for Implementation
- /Users/cloud-f1/Documents/git_saas/jira-agent-cli/.worktrees/ADLC-50/.claude/skills/jira-agent-cli-ops/SKILL.md
- /Users/cloud-f1/Documents/git_saas/jira-agent-cli/.worktrees/ADLC-50/.claude/hooks/session-start-check-api.sh (not present in this worktree; exists on `main` at commit `3c50a61`)
- /Users/cloud-f1/Documents/git_saas/jira-agent-cli/.worktrees/ADLC-50/.claude/settings.json (not present in this worktree; exists on `main`)
- /Users/cloud-f1/Documents/git_saas/jira-agent-cli/.worktrees/ADLC-50/docs/PORTABILITY.md
- /Users/cloud-f1/Documents/git_saas/jira-agent-cli/.worktrees/ADLC-50/docs/intent.md
- /Users/cloud-f1/Documents/git_saas/jira-agent-cli/.worktrees/ADLC-50/README.md
I've written the full plan to the plan file. `ExitPlanMode` isn't available in this unattended session (no interactive user to approve it), so per the ticket's actual instructions, here's the plan and the closing summary.

## Plan: ADLC-50 — package ops skill + SessionStart hook as a Claude Code plugin

**Approach:** Ship a same-repo plugin (no new marketplace repo). Relocate the existing skill and hook out of `.claude/` into plugin-convention paths at repo root, sibling to a new `.claude-plugin/` manifest. Delete the `.claude/` copies in the same change rather than keeping a parallel fallback (avoids silent drift; this repo is its own first consumer).

**New files:**
```
.claude-plugin/plugin.json        # name/version/description/author; auto-discovers skills/hooks
.claude-plugin/marketplace.json   # single-entry marketplace pointing source: "./"
skills/jira-agent-cli-ops/SKILL.md          # git mv from .claude/skills/jira-agent-cli-ops/
hooks/hooks.json                            # SessionStart → "${CLAUDE_PLUGIN_ROOT}/hooks/session-start-check-api.sh"
hooks/session-start-check-api.sh            # git mv from .claude/hooks/, audit for path assumptions
```

**Changed files:** `.claude/settings.json` (drop the `SessionStart` block — superseded), `docs/PORTABILITY.md` (new plugin-install section), `docs/intent.md` (Tracking table row), `docs/context/session-summary.md` (handover note), `README.md` (point at plugin install, not hand-copy).

**Out of scope:** generalizing the hook's hardcoded `project=ADLC` JQL filter — separate ticket if wanted; this ticket is packaging, not redesign.

**Key risks:** this worktree branch (`38b94b1`) is behind `main` (has `.claude/settings.json`/`.claude/hooks/` since commit `3c50a61`) — rebase before implementing; plugin `version` won't auto-sync with `pyproject.toml`'s version; confirm the org's Claude Code version actually supports plugins/`${CLAUDE_PLUGIN_ROOT}` before committing to this as the only distribution path.

**Verification:** no pytest applies (this is Claude Code harness behavior, not Python logic) — manual pass from a scratch second repo: `/plugin marketplace add` + `/plugin install`, confirm skill triggers namespaced, confirm `SessionStart` hook fires with `${CLAUDE_PLUGIN_ROOT}` resolved correctly (not hardcoded), confirm auto-start of the API server still works, then `/plugin uninstall` cleanly removes both.

---

**Jira comment summary:** This ticket's spike work is scoped to relocating the existing `jira-agent-cli-ops` skill and `SessionStart` hook out of `.claude/` into a same-repo Claude Code plugin (`.claude-plugin/plugin.json` + `marketplace.json`, with `skills/jira-agent-cli-ops/SKILL.md` and `hooks/hooks.json` + `hooks/session-start-check-api.sh` at repo root), distributed via `/plugin marketplace add cloud-f1/jira-agent-cli` — no new repo needed since this is a single-org private plugin. The move also fixes, for free, the hook's existing hardcoded-absolute-path bug via `${CLAUDE_PLUGIN_ROOT}` substitution. The Python CLI/server/poller stays a normal git-cloned dependency (confirmed unfixable limitation: no Python dependency auto-install in the plugin system), and `.claude/`'s copies get deleted in the same change rather than kept as a parallel fallback, to avoid drift. Out of scope: generalizing the hook's hardcoded `project=ADLC` JQL filter — file separately if wanted. Docs to update: `docs/PORTABILITY.md`, `docs/intent.md`, `docs/context/session-summary.md`, `README.md`. No pytest coverage applies; verification is a manual install/trigger/uninstall pass from a scratch second repo. One prerequisite: this worktree branch is behind `main`, which already added `.claude/settings.json`/`.claude/hooks/` at commit `3c50a61` — rebase before implementing.