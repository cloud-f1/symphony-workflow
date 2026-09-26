# Changelog

All notable changes to `symphony-workflow` (formerly `jira-agent-cli`) are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/), and versions follow
[semver](https://semver.org/). ADLC ticket numbers are cited for traceability — see the `ADLC`
Jira board (`cloud-f1.atlassian.net`) for full history.

## 0.4.3 — 2026-09-26

**Closed `cli.py`'s test coverage gap** (`ADLC-95`) — measured 70% (only happy paths tested)
vs. 90%+ everywhere else. Added a failure-path test for every command's `except`/`typer.Exit`
branch (`get`/`comment`/`transition`/`plan`/`implement` raising `TrackerError`; `search`/`call`
raising `JiraError`; `_client()`/`_jira_client()` raising `ConfigError`), plus previously-untested
happy paths (`search`, `comment`, `transition`, `call` had zero coverage at all). `cli.py` now at
99% (only the untestable `if __name__ == "__main__"` guard remains).

Also added: explicit `TrackerClient` Protocol-conformance assertions (`test_tracker.py` —
`isinstance(JiraClient(...), TrackerClient)` / `LinearClient`, catching a future signature drift
instead of only failing silently at call time), and a dedicated `test_models.py` (previously
100%-covered only incidentally through other tests' fixtures — now explicit edge-case + `from_api`/
`from_linear` parity coverage).

248 tests total (31 new), overall coverage 92%→96%, `ruff check .` clean.

## 0.4.2 — 2026-09-26

**`sync-decomposition --status` (`ADLC-121`)** — closes a real poller-race window hit twice this
session (`ADLC-64`, `ADLC-89`): `sync-decomposition` intentionally creates new Stories in Jira's
default "To Do" so the poller auto-Plans them — correct for a genuine forward-looking
decomposition, but it raced a batch status-fix-up pass when used to retroactively document
already-shipped work, twice flipping a Story to "In Review" via a real auto-Plan run. New optional
`--status <name>` (`execute_sync_decomposition(..., initial_status=...)`) transitions each
Story/Subtask immediately after creating it, inside the same loop iteration — tightening the race
window from "create all N, then transition all N" down to one synchronous call per issue. Default
behavior (no flag) is unchanged. 3 new tests (`test_sync_decomposition.py` ×2, `test_cli.py` ×1),
217 total, `ruff check .` clean.

## 0.4.1 — 2026-09-26

**Corrected the recommended Linear structure mapping** (`ADLC-87`) — the initial live demo built
while validating `ADLC-57`'s write path used the wrong mapping (Linear Team = repo, Linear
Project = Epic), which doesn't scale: Linear plans cap the number of Teams (2, in the account
this was tested against), so "one Team per repo" can't extend past a couple of repos. Corrected,
confirmed against Linear's real GraphQL schema: **Project = repo** (not capped the way Teams
are), **`ProjectMilestone` = Epic** (a real Linear object for exactly this, previously unused),
**Issue = Story**, **sub-issue = Task**. README.md documents this mapping in full, in both
English and Traditional Chinese. No source code changes — documentation + a live Linear
workspace correction only (the existing demo project was renamed and its 27 issues reassigned
onto a real Milestone in place, not duplicated).

## 0.4.0 — 2026-09-26

**Linear as an alternative tracker backend** (`ADLC-57`) — the Plan/Implement run-loop
(`get`/`comment`/`transition`/`plan`/`implement`, plus their HTTP equivalents) now works against
Linear as well as Jira, selected via `SYMPHONY_WORKFLOW_TRACKER=jira|linear` (default `jira`,
no behavior change for existing deployments that don't set it).

- New `tracker.py`: a `TrackerClient` Protocol covering exactly the run-loop's real surface
  (`get_myself`/`get_issue`/`add_comment`/`transition_issue`/`search_ready_tickets`/`issue_url`),
  plus `TrackerError` (a new common base — `JiraError` now subclasses it) and
  `get_tracker_client()`, the one place the env var is read.
- New `linear_client.py`: `LinearClient`, a GraphQL wrapper implementing that surface. Confirmed
  live (read-only) against a real Linear team, not just mocked tests.
- New `LinearConfig` (`config.py`): `LINEAR_API_KEY` (same env var name as content-asset-system's
  own `gaia linear` tooling) + `SYMPHONY_WORKFLOW_LINEAR_TEAM`.
- **Two breaking-but-honest renames**: `docs/runs/<key>/plan.md`'s frontmatter key `jira_url:` →
  `tracker_url:` (a Linear-backed run's plan claiming `jira_url` would be wrong, not just
  imprecise); `GET /health`'s `jira_reachable` field → `tracker_reachable`. Historical
  `docs/runs/**` files keep their original `jira_url:` unchanged.
- **Explicitly NOT ported to Linear** (by design — see
  `docs/plans/0005-linear-tracker-backend/intent.md`'s non-goals): the AI-agent tool-call layer
  (`tools.py` — Epic/parent linking, sprint/story-points fields don't exist on Linear), raw-JQL
  `search`, and `decompose`/`sync-decomposition`. `api.py`'s Jira-only endpoints (`/tools`,
  `/tools/{name}/call`) now return `501` when the configured backend isn't Jira, instead of
  silently misbehaving.
- 20 new tests (`test_linear_client.py`, `test_tracker.py`, plus `LinearConfig` cases in
  `test_config.py`) — 214 total, all passing, `ruff check .` clean.

## 0.3.0 — 2026-09-25

**Renamed `jira-agent-cli` → `symphony-workflow`** (`ADLC-53`) — repo, Python package
(`jira_agent_cli` → `symphony_workflow`), console command (`jira-agent` →
`symphony-workflow`), and Claude Code plugin/skill (`jira-agent-cli-ops` →
`symphony-workflow-ops`), all in one change to avoid a broken interim state. Breaking change
for anyone with the old command/import in muscle memory or scripts. GitHub's rename redirect
keeps `github.com/cloud-f1/jira-agent-cli` resolving to the new URL, so existing links (e.g.
in past Jira comments) still work. Local checkout moved to
`~/Documents/git_saas/symphony-workflow`. Historical docs (`docs/runs/**`, `docs/plans/**/
implement.md`) were deliberately left referencing the old name — they're point-in-time logs of
what actually happened, not living references.

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
