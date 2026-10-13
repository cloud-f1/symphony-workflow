# Changelog

All notable changes to `symphony-workflow` (formerly `jira-agent-cli`) are documented here.
Format loosely follows [Keep a Changelog](https://keepachangelog.com/), and versions follow
[semver](https://semver.org/). ADLC ticket numbers are cited for traceability — see the `ADLC`
Jira board (`cloud-f1.atlassian.net`) for full history.

## Unreleased

**Plugin install path** (ADLC-54): `.claude-plugin/marketplace.json` now has a description, so `claude plugin validate --strict .` passes (it failed; only `plugin/` had been validated), and its plugin entry matches `plugin.json`; both READMEs say how to install the plugin from Claude Code (`/plugin marketplace add cloud-f1/symphony-workflow`, `/plugin install symphony-workflow-ops --marketplace cloud-f1/symphony-workflow`). Two tests in `tests/test_surface.py` keep both true.

**README rewritten, guidance first, in two languages** (ADLC-262): `README.md` and `README.zh-TW.md` now open with who the tool is for, a five-minute start, a which-setup-do-I-need table and seven use cases, each with a real snapshot from `docs/snapshots/` (a throwaway project, a real model for the To-Do proposal, and a throwaway Linear project and Jira tree that were deleted afterwards); the former README is kept under `## Reference`. `tests/test_readme.py` keeps the two languages the same shape, every snapshot block a verbatim excerpt of its file, every command shown real, and nothing private (a credential, a local path) in the guidance or the snapshots.

## 0.9.2 — 2026-10-13

**Refresh of everything a person or an agent reads** (plan 0008 stage 3, ADLC-208): `symphony-workflow --help` groups the commands in panels (Tickets, Plan and implement, Plan sync, Setup, Agent tool calls); `sync-plan`'s help describes all three sinks and its exit codes; the `create_issue` and `add_comment` tool descriptions carry the two gotchas that cost real time (`parent` vs `epic`, wiki markup instead of Markdown); the SessionStart hooks, both skills and `docs/PORTABILITY.md` say `init-env` / `check-env` instead of `cp .env.example`; two docstrings that still called `sync-decomposition` unbuilt are fixed; the plugin description and keywords name what it ships. New `tests/test_surface.py` fails when a command is missing from a help panel, a tool description is thin, the two skill copies drift (sections that differ on purpose are listed and explained), or a hook points at the old setup; it caught existing drift between the copies on its first run, both intentional.

## 0.9.1 — 2026-10-13

**Security fix in the Jira sink** (found by an automated review of the 0.9.0 commit, reproduced first): the id-to-key map in `docs/context/sync-state.json` is a file in the project directory, which may be someone else's, and 0.9.0 trusted it blindly, so a map naming another project's ticket (or a ticket in the same project that this plan did not create) made `sync-plan --to jira` overwrite its summary and description and `sync-status --to jira` move it. A key is now used only when it is in the target project and the card carries this entry's own `sd-id:` marker; otherwise it is dropped and the entry is found by marker or created. `JiraStatusBackend` takes `project_key` and applies the same check; `sync-status --to jira` takes it from `--project`, `SYMPHONY_WORKFLOW_JIRA_PROJECT`, or the keys already in the map. Ids are unique per plan, so two plans must not sync into one Jira project. Re-verified live on a throwaway tree.

## 0.9.0 — 2026-10-13

**Jira sink** (plan 0008, ADLC-275): `sync-plan --to jira --project KEY [--dry-run]` writes a plan as Jira Epics (Categories), Stories (Tasks) and Subtasks (To-Dos), idempotent through the same `sd-id:` marker, never deleting, an existing card keeping the status Jira has. The issue keys it made are saved in `docs/context/sync-state.json` (`keys`) because a JQL child list lags a few seconds behind a create; cards made before that map existed are adopted by marker through the v2 issue endpoint (v3 search returns ADF). `sync-status --to jira` runs the same three-way status sync against those cards; `sync_status_with` now takes a backend, with `LinearStatusBackend` and `JiraStatusBackend`. Run live on a throwaway tree in a real project (create, re-run, pull, push, conflict) and cleaned up afterwards. Also in this release: a project with no phase table is grouped in eights, and `no_tests_todo` only warns on generated To-Dos (stage-2 review of 18 projects). New module `jira_sink.py`.

**Plan sync, from the stage-2 review of 18 real projects** (ADLC-206): a project without a phase table is no longer one giant Category (it had 40 to 210 Tasks); `import-plan` / `sync-plan` group it in eights in numeric epic order (`unphased`, `unphased-2`, ... so ids stay put when epics are appended; up to eight stay one `Unphased`). The `no_tests_todo` warning now only applies to generated To-Dos (a proposal), not to an author's own checklist, phases or story headings; Tasks carry `todo_origin` for that. Measured on the real data: `too_many_tasks` 13 to 1, `no_tests_todo` 254 to 0, categories 487 to 578 (1 : 3.4 to 1 : 2.9 against a target of 1 : 3).

## 0.8.1 — 2026-10-13

Found by running `split-todos` against a real model: an agent that words its tests item freely ("Write and run tests ...") no longer gets a second, duplicate tests To-Do appended (any item starting a word with `test` counts, `latest` does not), and `check_plan` accepts the same wording. Also, with `todo_source` `hybrid`, `sync-plan` no longer writes the five fallback steps for a Task that has a proposal waiting for review: they would have been read as the author's own breakdown and the approved proposal never used. The live agent split (ADLC-226) is now verified end to end.

## 0.8.0 — 2026-10-13

**Plan sync** (plan `docs/plans/0008-adaptive-plan-sync/`, ADLC-197), summarised: `profile`, `import-plan`, `sync-plan --to markdown|linear`, `split-todos`, `sync-status`, and the git/path hardening behind them. The Linear sink and `sync-status` were also run against a real Linear workspace on a throwaway project (removed afterwards), which found and fixed two bugs the mocks could not show: Linear normalises Markdown (a blank line before a list), which made every Task look changed on a re-run, and a Task was pushed forward while one of its To-Dos was in conflict. A Linear `duplicate` state now counts as done. Also: `scripts/release.sh` and `tests/test_version.py` now check `marketplace.json` (ADLC-249/250); both operations skill copies and the QUICKSTART describe plan sync.

**Two-way status sync** (plan 0008, ADLC-204): `sync-status --to linear [--direction both|push|pull] [--project] [--dry-run]`. Each To-Do is compared three ways (its status at the last sync in `docs/context/sync-state.json`, in the plan's checkboxes, in Linear): a change on one side is carried to the other (pull ticks the checkbox, push moves the card), a change on both is a conflict that is reported, left alone, and exits 1. A Task follows the roll-up of its To-Dos and is only ever pushed forward. A To-Do with no card is reported as an orphan, nothing is created or deleted. Linear state names can be mapped to plan statuses with `status_map` in `docs/context/plan-sync.json`; a team state called Blocked counts as blocked. The progress matrix is deliberately not written back (it has five step columns, the tracker one status). Mock-tested only, like the Linear sink. New module `status_sync.py`.

**Linear sink** (plan 0008, ADLC-203): `sync-plan --to linear [--project NAME] [--dry-run]` pushes the plan as a Linear Project with one ProjectMilestone per Category, one issue per Task and one sub-issue per To-Do. Idempotent through an `sd-id:` line in each description, so a re-run updates in place; it never deletes, and an existing issue keeps the state Linear has (the two-way rule comes with ADLC-204). New issues get a workflow state from the plan status (a team's own `Blocked` state when it has one). `LinearClient` gained the write methods (kept off the shared `TrackerClient` Protocol, the Plan/Implement loop never creates issues). **Tested against mocks only**; the GraphQL field names have not yet been run against a real workspace (ADLC-239). New module `linear_sink.py`.

**To-Do sources** (plan 0008, ADLC-201): `sync-plan` now fills Tasks that have no breakdown of their own, per `todo_source` in `docs/context/plan-sync.json`: an approved agent proposal, then the five workflow steps (Spec, Implement, Tests and QA, Commit, Merge). New `split-todos` runs one read-only agent turn per such Task and writes `docs/context/todo-proposal.{json,md}` for review; `split-todos --approve` unlocks it, and nothing reaches a tracker before that. Proposals are held to the 2-to-6 guardrail (last To-Do is the tests); an author's own breakdown outside it is passed through with a warning. New module `todo_source.py`.

**Security hardening of plan sync** (ADLC-270, from an automated review of the phase-1 commit; each finding reproduced first): git now runs through `safepath.run_git` with `core.fsmonitor`, `core.hooksPath` and the pager disabled, so a repository's own `.git/config` can no longer make `check-env`, `init-env` or `profile` run a command (`git check-ignore` executed `core.fsmonitor`); signature verification is switched off too (`log.showSignature`, `gpg.program`), because a repo that set both made `profile`'s `git log` run its script on a signed commit (present in 0.7.0's `profile` too, which was released without it); plan files are never read or written through a symlink or outside the project root, so a `docs/epics` symlink can no longer make `sync-plan` write elsewhere; the `profile_project` and `import_plan` tools only accept a path inside the working directory.

**Plan sync, phase 1** (plan `docs/plans/0008-adaptive-plan-sync/`, ADLC-197): `symphony-workflow profile [--path] [--json] [--write]` reads how a project already works (plan files, tracker credential key *names*, issue keys in git history, the shape of the plan) and prints two recommendations, the second refined from content; `--write` saves `docs/context/plan-sync.json` after asking. `import-plan` reads an epics-as-markdown project into a neutral Category / Task / To-Do plan with pyramid guardrail warnings. `sync-plan --to markdown [--from epics-md|decomposition] [--dry-run]` writes it as Markdown in `docs/epics/` (idempotent, only managed `<!-- sd:begin -->` blocks, never deletes); in an unconfigured project it prints the recommendation and stops. New tools `profile_project` and `import_plan`; `call` runs them without Jira credentials. New modules `plan.py`, `epics_md.py`, `project_profile.py`, `markdown_sink.py`.

## 0.7.0 — 2026-10-13

**`symphony-workflow init-env [--path]`** and **`check-env [--path] [--verify] [--json]`** (ADLC-189): per-project Jira
credential setup. `init-env` creates `.env` from a template with empty keys (owner-only permissions), adds it to
`.gitignore` when git would otherwise track it, and refuses to overwrite. `check-env` names missing or empty
keys and whether the file is gitignored, and never prints a value; `--verify` makes one read-only call (`/myself`)
and tells `unauthorized` (401/403), `jira-error` (bad URL) and `unreachable` apart. Exit 0 good, 1 keys missing or
file not ignored, 3 `--verify` failed. The plugin's `jira-ticket-ops` skill now tells a session to run these instead
of reading `.env`, and never to ask for a token in chat. No new tool entry: the connectivity check is the existing
`get_myself` tool.

## 0.6.0 — 2026-10-12

**`symphony-workflow diff-decomposition <epic> [--adopt] [--json]`** and **`sync-decomposition <epic> --update
[--dry-run] [--adopt] [--yes]`** (ADLC-146): read-only content-drift detection between `decomposition.json` and the
live Epic/Stories/Subtasks, and an idempotent apply step (create missing, update changed, never delete).
`Story`/`Task` gain an optional stable `id` (old files still load; ids are written back on first sync); descriptions
carry a trailing `sd-id:` marker, mirrored in `docs/runs/<epic>/sync-state.json` (id -> key cache). A Subtask moved to another Story is reported as `parent-differs` and fixed by `--update`. New tool `diff_decomposition`. `update_issue` gains `parent` (re-parent). Behaviour change: plain
`sync-decomposition` now refuses an Epic that already has children (use `--update`). Found live: v3 search returns
descriptions as ADF, so descriptions are read through the v2 issue endpoint. Plan: `docs/plans/0007-jira-diff-sync/`.

## 0.5.3 — 2026-10-12

Public-repo audit, second pass: consumer repo names, their Jira project keys, other internal tools and local
paths are replaced by neutral placeholders across code, tests, docs and `docs/runs/` (the 0.5.2 pass missed
`docs/PORTABILITY.md`'s instance registry, tests, the hierarchy docs and run artifacts). The session-start hook
command in `.claude/settings.json` now uses `$CLAUDE_PROJECT_DIR` instead of an absolute home path, and
`.gitignore` keeps `docs/runs/<non-ADLC key>/` local. Also: fixed a `ruff` E501 in `tests/test_poller.py`, and
added the 0007 jira-diff-sync handover plan (docs only, PR #33). Git history keeps what was committed earlier.

## 0.5.2 — 2026-10-11

Public-repo audit (ADLC-135): an external repo and its ticket keys are no longer named in code comments, docstrings, tests or this file (git history keeps what was committed earlier); CLAUDE.md and the README background no longer say "private" or point at a personal path; the plugin's SessionStart hook now exits quietly when `jq` is missing instead of failing in every project. Also released here: the `implement --max-turns / --max-budget` overrides below.

`symphony-workflow implement <key> [--max-turns N] [--max-budget USD]` (PR #27): the Implement phase had a
fixed 20-turn cap that ended a real run (an external repo's ticket) with `error_max_turns`; 60 turns and $8
completed it. The overrides are threaded through `execute_implement` into `run_implement`; with neither
option the defaults apply exactly as before.

## 0.5.1 — 2026-10-11

New plugin skill **`jira-ticket-ops`** (`plugin/skills/jira-ticket-ops/`): the CLI + skill way to audit, create,
transition and roll up Jira tickets for any project, with the project key as a parameter (first user:
a consumer repo with its own Jira project). Chosen over a Jira MCP server because the CLI already exposes a
schema-described tool interface, and a skill costs almost nothing in context until it is used. The plugin copy
of `symphony-workflow-ops` is re-synced with the canonical skill: it had missed the `rollup` and `audit` /
`add-subtasks` paragraphs added in 0.5.0 (ADLC-122). License changed from Proprietary to MIT ahead of making the repo public.

## 0.5.0 — 2026-10-11

**`symphony-workflow audit <PROJECT> [--open] [--assignee NAME | --assignee-me] [--json]`** and
**`symphony-workflow add-subtasks <plan.json> [--dry-run] [--assign-to-me] [--json]`** (tools
`audit_hierarchy`, `add_subtasks`) — keep an existing project's Epic -> Story -> Subtask hierarchy
honest. `audit` is read-only and exits 1 on: a Story-level issue (Story/Task/Bug) with no Subtasks or no
Epic, an Epic with no Stories, a Done Story with open Subtasks, and (opt-in) a card not assigned to a
named person; one Subtask, or more than 8, is a warning. `add-subtasks` adds Subtasks to Stories from a
plan file, idempotently (a Story that already has Subtasks is skipped), rolls a Done Story's new Subtasks
to Done, validates the whole plan before creating anything, reports partial work if Jira fails, and tries
`Sub-task` if `Subtask` is rejected. New `JiraClient.search_hierarchy()` (paged) and `HierarchyIssue`
fields `assignee` / `subtask_count`; `get_hierarchy_issue` now also requests them. New
[docs/JIRA_HIERARCHY.md](docs/JIRA_HIERARCHY.md) states the rules (naming, size, done-when, roll-up,
ownership).

Why: one consumer repo found 33 of 73 Stories with no Subtasks (the board showed no progress), and
another had 36 cards with no Subtasks and no assignee; `decompose` only covers an Epic not yet
built. The commands are ported from a consumer repo's `jira-add-subtasks.py`, which stays as that repo's own
copy. 24 new tests (322 total); `ruff check` clean on the new code. No version bump.

**`symphony-workflow rollup <key> [--dry-run] [--json]`** and the `rollup_issue` tool — close out
a finished Subtask's Story and Epic once *all* their children are Done: Subtask → Done, then the
Story if every Subtask is Done, then the Epic if every Story is Done. Forward-only, idempotent,
stops at the first level that isn't fully done; also accepts a Story or Epic key. Exit codes `0`
ok / `2` not found / `3` Jira error (partial work is always reported). Jira-only (needs the
`parent` field). New `JiraClient.get_hierarchy_issue()` / `list_children()` (paged through
`nextPageToken`) and `models.HierarchyIssue`; "is it done" uses Jira's `statusCategory`, not the
free-form status name, and keys just transitioned are remembered so Jira's eventually
consistent search can't make a finished Subtask look unfinished.

Requested by a consumer project, whose own Story/Epic bookkeeping was being closed
by hand. 39 new tests, 298 total, `rollup.py` 100% covered, `ruff check` clean on the new code.
No version bump — cutting a release is a separate decision.

## 0.4.5 — 2026-09-26

**Consolidated the duplicated Jira-only-backend guard** (`ADLC-106`) — `cli.py`'s Jira-only
commands and `api.py`'s `/tools` endpoints each independently asserted "this client must be
Jira," worded slightly differently. New `tracker.require_jira(client, *, context)` is the one
shared assertion both now use.

Confirmed first (`ADLC-107`) that the two guards weren't actually identical: `cli.py`'s
`_jira_client()` *constructed* a fresh `JiraClient` directly from `JiraConfig.from_env()`,
completely ignoring `SYMPHONY_WORKFLOW_TRACKER`; `api.py`'s `get_jira_client()` *checked* the
server's one already-selected shared client. **Real, deliberate behavior fix along the way**:
`_jira_client()` now goes through `get_tracker_client()` first (honoring the env var) before
asserting Jira-ness — a deployment with `SYMPHONY_WORKFLOW_TRACKER=linear` now gets a clear
"Jira-only" error from `search`/`call`/`decompose`/`sync-decomposition` instead of silently using
leftover `JIRA_*` env vars anyway. No change for the common case (tracker unset/`jira`).

Also closed a real, pre-existing test gap found while touching this code: `api.py`'s `/tools` 501
path had zero test coverage since it was built in `ADLC-57` — now covered.

4 new tests, 252 total, `ruff check .` clean.

## 0.4.4 — 2026-09-26

**`scripts/release.sh`** (`ADLC-101`) — makes the git-tag step of a release unskippable.
`v0.3.0`/`v0.4.0`'s CHANGELOG entries said "tagged" but `git tag -l` disagreed; a three-step
manual process (bump `pyproject.toml`+`plugin.json` together, write the CHANGELOG entry, `git tag
-a`+push) silently dropped the tag step, twice. Run it after a version-bump PR is already merged
to `main`: it verifies `pyproject.toml`/`plugin.json`/`CHANGELOG.md` already agree on `<version>`,
runs `tests/test_version.py`, and only then creates the annotated tag (never pushes — prints the
command instead). `README.md` gains a "Releasing" section. **This very release (`v0.4.4`) is the
script's own end-to-end validation** — used for real rather than a throwaway version, per
`ADLC-105`.

**Retroactive-tag decision** (`ADLC-104`): **not** retroactively tagging `v0.3.0` (commit
`888f713`) or `v0.4.0` (commit `3dec254`). Both were superseded within the same day they shipped
(`v0.4.1` landed hours after `v0.4.0`), this is still a POC repo with no external consumers
pinning to those versions, and `scripts/release.sh` now prevents the underlying failure from
recurring for every release going forward — tagging two dead, already-superseded historical
points has no real value proportional to the small risk of tagging the wrong commit by hand.
Recorded here, not silently dropped.

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
  own Linear tooling) + `SYMPHONY_WORKFLOW_LINEAR_TEAM`.
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
- Portability validated against a real second repo — created the `EXMP` Jira project and ran a
  live Plan against `example-nextjs-app` (`ADLC-39`–`42`).
- `docs/plans/<NNNN>-<slug>/` planning-cycle convention, replacing overwrite-prone top-level
  `docs/{intent,spec,plan}.md` files (`ADLC-43`).
- Quickstart walkthrough + `CLAUDE.md` consolidation pass (`ADLC-51`).

## 0.1.0 — initial release

Scaffolded `jira-agent-cli`: the Jira Cloud REST API v2 client + tool-call registry + Typer
CLI, and Epic 1's Symphony-inspired orchestration service (FastAPI server, Plan/Implement loop
via the Claude Agent SDK, and the auto-discovery poller — Phases 0–5, `ADLC-2`).
