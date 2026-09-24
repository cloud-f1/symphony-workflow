---
jira_url: https://cloud-f1.atlassian.net/browse/ADLC-30
---

## Plan: ADLC-30 — Install + connect GitHub for Jira app to cloud-f1 org

### Context
Confirmed 2026-09-25: `GET /rest/dev-status/1.0/issue/summary` against `cloud-f1.atlassian.net` returns `byInstanceType: {}` for every data type (branch/PR/build/review) — no GitHub-for-Jira app is connected at all. Branch naming was already fixed in `workspace.py`'s `branch_name()` (branches start with `{ISSUE-KEY}-{slug}`), but that alone can't populate Jira's Development panel — the connector app must exist and be authorized first. This finding is already recorded in four places (`CLAUDE.md`, `.claude/skills/jira-agent-cli-ops/SKILL.md`, `docs/PORTABILITY.md`, `docs/context/session-summary.md`), so this ticket is about closing the gap, not discovering it.

**This ticket has no code path.** It's an org-admin browser action against Atlassian Marketplace + GitHub's app-authorization flow — nothing reachable via `client.py`/tool calls. No new `client.py` method, `tools.py` entry, or CLI command is warranted; there's no Jira REST endpoint being wrapped, and `/rest/dev-status/1.0/issue/summary` is already reachable ad hoc for verification without promoting it to a full tool.

### Manual steps (org-admin, browser — not agent-executable)
1. Log into `cloud-f1.atlassian.net` as a site/org admin.
2. Atlassian Marketplace → install "GitHub for Jira" (Atlassian's own first-party app — verify it's the `atlassian/jira-github` listing, not a similarly-named third-party integration).
3. Authorize it against the `cloud-f1` GitHub org (requires GitHub org-owner-level OAuth consent) and select target repo(s), at minimum `jira-agent-cli`.
4. Confirm the repo shows connected in the app's Jira admin config screen.

### Follow-up steps (agent-doable, only after the manual step)
1. **Live-verify**: re-run `GET /rest/dev-status/1.0/issue/summary` against a ticket with a correctly-named branch (`ADLC-24` already qualifies) and confirm `byInstanceType` is now non-empty for `branch`. This is the actual acceptance check.
2. **Update `CLAUDE.md`** — Branch naming section's "not connected" paragraph → confirmed-connected note with new date.
3. **Update `.claude/skills/jira-agent-cli-ops/SKILL.md`** — same caveat (~line 57-58).
4. **Update `docs/PORTABILITY.md`** — same caveat (~line 68-70); note this is per-Jira-site/per-GitHub-org and won't propagate to other adopters automatically.
5. **Update `docs/context/session-summary.md`** — flip the `ADLC-30` row to Done, fold the finding bullet into a dated closure note.
6. No `tests/` changes — no new client method/tool is added, and no test should depend on live Jira connectivity per `CLAUDE.md`'s testing rules.

**Files touched (docs only, post-manual-step):** `CLAUDE.md`, `.claude/skills/jira-agent-cli-ops/SKILL.md`, `docs/PORTABILITY.md`, `docs/context/session-summary.md`. No changes to `src/jira_agent_cli/**` or `tests/**`.

### Risks / open questions
- **Access**: needs someone with both Jira site-admin *and* GitHub org-owner rights on `cloud-f1` — may block on a second person if the assignee only has one.
- **Repo scope**: decide up front whether to authorize "all repos" or just `jira-agent-cli` — narrowing later means revisiting the GitHub app config.
- **Marketplace app identity**: must install Atlassian's own listing, not a similarly-named third-party GitHub/Jira integration, or dev-status data may not populate correctly.
- **Verification ticket**: use `ADLC-24` (already has a correctly-named branch) rather than creating a throwaway branch just to test.
- **No rollback risk**: purely additive/config-only on Atlassian/GitHub's side; nothing in this repo's code depends on the app being connected.

---

**Jira comment summary:** This ticket has no code implementation — it's a one-time manual step where a `cloud-f1` org-admin (with both Jira site-admin and GitHub org-owner access) installs Atlassian's "GitHub for Jira" app from the Marketplace and authorizes it against the `cloud-f1` GitHub org, scoped to at least the `jira-agent-cli` repo. Branch naming (`workspace.py`'s `branch_name()`) is already correct and doesn't need to change. Once installed, the only agent-doable follow-up is verifying `GET /rest/dev-status/1.0/issue/summary` now returns non-empty `byInstanceType` data for a correctly-named branch (e.g. `ADLC-24`), then updating four docs that currently record the "not connected" finding (`CLAUDE.md`, `.claude/skills/jira-agent-cli-ops/SKILL.md`, `docs/PORTABILITY.md`, `docs/context/session-summary.md`) to reflect the fix and its confirmation date. Main risk is access: this needs someone with both Jira and GitHub org admin rights, and the org should decide up front whether to scope the app to this repo or the whole org.