# Changelog

All notable changes to `jira-agent-cli` are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/), and versions follow [semver](https://semver.org/).
ADLC ticket numbers are cited for traceability — see the `ADLC` Jira board
(`cloud-f1.atlassian.net`) for full history.

## 0.2.0 — 2026-09-25

Epic 2 (`ADLC-27`) shipped: a CLI-native control plane, Epic/Story/Task decomposition, a
Claude Code plugin, and a round of reliability fixes found live-running the poller. All
additions are backward-compatible with the existing CLI/API surface — no breaking changes.

### CLI control plane
- `jira-agent plan <key>` / `jira-agent implement <key>` drive a ticket through the
  Plan/Implement loop in-process, without needing the FastAPI server running. Run state
  persists to `docs/runs/<key>/run.json` so a later `implement` invocation (a separate process)
  can resume it despite `RunStore` being in-memory/process-scoped (`ADLC-45`).
- Documentation for the CLI control plane + decomposition feature (`ADLC-48`).

### Epic/Story/Task decomposition
- `jira-agent decompose <key>` breaks a Jira Epic into Stories/Tasks for human review, with
  Definition of Done / Acceptance Criteria templated from `CLAUDE.md` conventions rather than
  LLM-invented. Read-only — reuses the Plan phase's `permission_mode="plan"` wiring, no Jira
  writes (`ADLC-46`).
- `jira-agent sync-decomposition <key>` materializes an already-reviewed `decomposition.json`
  into real Jira Story (`parent=<epic>`) / Subtask (`parent=<story>`) issues via the existing
  `create_issue` path (`ADLC-47`).
- Structured-output plumbing in `agent_runner.py` (`ClaudeAgentOptions.output_format` /
  `ResultMessage.structured_output`) — the foundation `ADLC-46` built on (`ADLC-28`).
- Fix: fall back to a structured-plan render when `result.text` comes back empty, so a Plan run
  still produces a readable `plan.md` (`ADLC-56`).
- Optional `max_turns` override for the Plan phase (`ADLC-55`).

### Interface API & Jira linking
- `GET /api/v1/state`, `GET /api/v1/issues/{key}` — a read-only interface API over the
  orchestration state (`ADLC-32`).
- Local markdown ↔ Jira ticket linking (remotelink + frontmatter), so `docs/runs/<key>/*.md`
  and the Jira issue reference each other (`ADLC-36`).
- `parent=` param on `create_issue`/`CreateIssueArgs`, alongside the existing `epic=` param —
  team-managed projects (like `ADLC`) reject the classic Epic Link custom field and require
  `parent` instead. New `update_issue` client method + tool (there was previously no way to
  edit an existing issue's summary/description at all).

### Plugin packaging
- Packaged the `jira-agent-cli-ops` skill + `SessionStart` hook (server health check + ready-
  ticket surfacing) as an installable Claude Code plugin (`plugin/.claude-plugin/plugin.json`).
  Hybrid packaging only — the CLI/server/poller itself still requires `uv sync --extra dev` in
  a cloned checkout, since the plugin system has no Python dependency auto-install (`ADLC-50`).

### Reliability fixes
- Bounded retry (`JIRA_AGENT_CLI_POLL_MAX_PLAN_ATTEMPTS`, default 3) + failure-visible Jira
  comments for auto-Plan: a failed Plan no longer strands a ticket silently at "To Do" forever
  — found live when `ADLC-44` hit `error_max_turns` and was never retried or commented on
  (`ADLC-44`).
- Optional throttled batch triage step (`JIRA_AGENT_CLI_TRIAGE_ENABLED`, default off) that
  classifies newly-discovered tickets as plan/skip/flag via one budget-capped agent turn per
  batch, ahead of the poller's normal auto-Plan dispatch (`ADLC-29`).
- Workspace lifecycle hooks (`before_run`/`after_run`/`after_create`/`before_remove` via
  `JIRA_AGENT_CLI_HOOK_*`) and terminal-status workspace cleanup, off by default
  (`JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED`) (`ADLC-49`).
- Fix: sanitize Jira comment bodies against wiki-markup header collision — a leading `#`/`##`
  in a GFM plan comment was rendering as a garbled nested numbered list under Jira's v2 wiki
  markup, not a heading.
- Fix: branch names must start with the issue key (`{ISSUE-KEY}-{slug}`) for Jira's
  Development-panel auto-linking to work at all.

### Multi-repo portability & process
- Multi-repo policy confirmed and documented: one dedicated Jira project + one jira-agent-cli
  instance per target repo, not Epic/tag routing or a shared multi-tenant poller
  (`docs/PORTABILITY.md`).
- Portability validated against a real second repo — created the `NXJS` Jira project and ran a
  live Plan against `ai-coding-nexjs-template` (`ADLC-39`–`42`).
- `docs/plans/<NNNN>-<slug>/` planning-cycle convention, replacing overwrite-prone top-level
  `docs/{intent,spec,plan}.md` files (`ADLC-43`).
- Quickstart walkthrough + `CLAUDE.md` consolidation pass (`ADLC-51`).

## 0.1.0 — initial release

Scaffolded `jira-agent-cli`: the Jira Cloud REST API v2 client + tool-call registry + Typer
CLI, and Epic 1's Symphony-inspired orchestration service (FastAPI server, Plan/Implement loop
via the Claude Agent SDK, and the auto-discovery poller — Phases 0–5, `ADLC-2`).
