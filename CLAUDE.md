# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

> **Auto-loaded every session. Keep concise — essential rules only.**
> Ticket status → the `ADLC` Jira board | Last state → [docs/context/session-summary.md](docs/context/session-summary.md)
> (pattern borrowed from `example-nextjs-app`'s own proven session-handover convention)

## Project Overview

**symphony-workflow** is now two things, not one: a Python CLI for the Jira Cloud REST API where
every operation is also a schema-described tool call, **and** a Symphony-inspired orchestration
service (FastAPI server + background poller) that drives a ticket through a Plan/Implement
loop via the Claude Agent SDK. Both are first-class — the CLI didn't stay "just a CLI." The
Plan/Implement run-loop also works against **Linear**, selected via
`SYMPHONY_WORKFLOW_TRACKER=jira|linear` (`ADLC-57`, `tracker.py`'s `TrackerClient` Protocol) — the
tool-call layer (`tools.py`) and `decompose`/`sync-decomposition` stay Jira-only; see
[docs/plans/0005-linear-tracker-backend/intent.md](docs/plans/0005-linear-tracker-backend/intent.md)
for why. See [README.md](README.md) for the CLI pitch and confirmed CF1 field IDs; see
[docs/plans/0001-symphony-poc/intent.md](docs/plans/0001-symphony-poc/intent.md) /
[docs/plans/0001-symphony-poc/spec.md](docs/plans/0001-symphony-poc/spec.md) /
[docs/plans/0001-symphony-poc/implement.md](docs/plans/0001-symphony-poc/implement.md)
for the orchestration service's why/design/roadmap (read those three before touching `api.py`,
`orchestrator.py`, `poller.py`, `runs.py`, or `agent_runner.py`) — the general convention behind
this `docs/plans/<NNNN>-<slug>/` layout is `docs/plans/README.md` (`ADLC-43`); see
[docs/PORTABILITY.md](docs/PORTABILITY.md) for running this against a *different* repo/Jira
project. **New to this repo? Load the `symphony-workflow-ops` skill first**
(`.claude/skills/symphony-workflow-ops/SKILL.md`) — the short operational reference this file's
sibling docs don't repeat. Setting this up for the first time as a human (not a Claude Code
session)? [docs/QUICKSTART.md](docs/QUICKSTART.md) is a linear walkthrough, not reference material.

This is a standalone package: a public (MIT) repo in the `cloud-f1` GitHub org, usually cloned next to
your other repos. It is not part of any other repo of the org — don't assume its rules apply here beyond
what this file states. Because it is public, keep client and personal names, local paths and
credentials out of code, comments, docs and `docs/runs/` (git history keeps what was committed before).

## Jira tracking for this repo's own work

Work on this repo is tracked in Jira project **ADLC** (`cloud-f1.atlassian.net`, Kanban board
74), not CF1 — CF1 is content/marketing work, ADLC is agent-driven dev work. Structure:
**Epic** → **Story** (one per meaningful chunk of the epic's work) → **Subtask** per concrete
work item. When you pick up a subtask, transition it as you go
(`symphony-workflow transition <key> "In Progress"` / `"Done"`) — this repo's own agent-tool CLI
is the thing doing the tracking, so use it. See
[docs/plans/0001-symphony-poc/intent.md](docs/plans/0001-symphony-poc/intent.md)'s Tracking
table for `ADLC-2`'s original phase → ticket mapping.

**Hard rule (Alex, 2026-09-26): never build directly under an Epic ticket.** Every Epic gets
decomposed into Stories (and Subtasks as needed) *before or while* the work happens —
`symphony-workflow decompose <epic-key>` (read-only, writes `docs/runs/<epic>/decomposition.md`
for review) then `symphony-workflow sync-decomposition <epic-key>` (materializes the reviewed
decomposition as real Story/Subtask issues, `parent=<epic-key>`) exist exactly for this — see
`ADLC-46`/`47`. Work happens against the Stories/Subtasks, each transitioned as it completes; the
Epic itself only transitions to Done once its children are. **Real incident this rule fixes**:
`ADLC-57` (issue type Epic) was built end-to-end and shipped with zero Story/Subtask children —
caught only after the fact, then retroactively decomposed once this was noticed (see
`docs/plans/0005-linear-tracker-backend/implement.md`'s closing entry). `ADLC-53`/`ADLC-58` were
Story/Task, not Epics, so this specific rule didn't apply to them, but the same instinct — do the
work directly under one ticket instead of breaking it down first — is what to watch for. Don't
repeat this — decompose first.

**Stories without Subtasks are the same mistake one level down** (one consumer: 33 of 73; another:
all 26). Run `symphony-workflow audit <PROJECT> --open` before and after a batch of work; fix with
`symphony-workflow add-subtasks <plan.json>`. The rules (naming, size, done-when, roll-up, one owner) are
in [docs/JIRA_HIERARCHY.md](docs/JIRA_HIERARCHY.md).

## Claude Agent SDK usage (agent_runner.py)

Confirmed 2026-09-24 against `code.claude.com/docs/en/agent-sdk` — `pip install
claude-agent-sdk` (installed, v0.2.159+). Entry point: `async def query(*, prompt,
options: ClaudeAgentOptions) -> AsyncIterator[Message]`. Three settled decisions, don't
re-litigate without updating docs/plans/0001-symphony-poc/spec.md first:

- **Plan phase uses `permission_mode="plan"`** (a built-in SDK mode — explore without
  editing), not a hand-rolled read-only tool allowlist. This is simpler and safer than
  what was originally speculated.
- **Implement resumes the Plan phase's session** via `options.resume=<session_id>`
  rather than starting fresh — the agent keeps the context of the plan it already wrote.
- **Plan phase also sets `ClaudeAgentOptions.output_format={"type": "json_schema",
  "schema": PLAN_OUTPUT_SCHEMA}`** (ADLC-28) — structured output lands on
  `ResultMessage.structured_output` and is captured as `AgentRunResult.structured` /
  `Run.plan_structured`, alongside (not instead of) the existing free-text `result.text`
  / `Run.plan`. Confirmed by a throwaway spike that this survives plan-mode's
  exploratory multi-turn tool-use loop cleanly — see `docs/plans/0001-symphony-poc/spec.md`.
  Implement phase stays free-text (no `output_format`); this is the minimal foundation
  `ADLC-46`'s Epic/Story/Task decomposition schema builds on, not that feature itself. `ADLC-29`'s
  triage step reuses this same seam rather than introducing a fourth settled decision.
- Never `claude -p`. See the "Why Claude Agent SDK" section of `docs/plans/0001-symphony-poc/spec.md`.

## CLI control plane (`plan`/`implement`/`decompose`/`sync-decomposition`)

`cli.py` can also drive a ticket without the FastAPI server, calling the same shared logic
in-process (a fourth way to drive a ticket, alongside the API, the poller, and `call`/
`list-tools` — see the `symphony-workflow-ops` skill):

- **`symphony-workflow plan/implement <key>`** (ADLC-45) — calls `orchestrator.execute_plan`/
  `execute_implement`, the same functions `api.py`/`poller.py` use. `RunStore` is
  per-process here, so `plan` persists its `Run` to `docs/runs/<key>/run.json` for a
  later `implement` invocation to reload.
- **`symphony-workflow decompose <key>`** (ADLC-46) — breaks a Jira Epic into Stories/Tasks for
  human review. Reuses the Plan phase's `permission_mode="plan"` + `output_format` wiring
  (`prompts.PLAN_DECOMPOSE_SCHEMA` in place of `PLAN_OUTPUT_SCHEMA`) — read-only, no Jira
  writes. Renders `docs/runs/<key>/decomposition.{md,json}`.
- **`symphony-workflow sync-decomposition <key>`** (ADLC-47) — materializes an already-reviewed
  `decomposition.json` as real Jira Story (`parent=<epic-key>`)/Subtask
  (`parent=<story-key>`) issues via the existing `create_issue` path. Does not re-run the
  decompose agent. Refuses an Epic that already has children.
- **`symphony-workflow diff-decomposition <key>`** / **`sync-decomposition <key> --update`** (ADLC-146) —
  read-only content-drift report (exit 0/1/2/3) and its idempotent apply step; cards are tied to spec entries
  by `id` + a `sd-id:` description marker (`drift.py`). Never deletes. See `docs/JIRA_HIERARCHY.md`.
- **`symphony-workflow profile`** / **`import-plan`** / **`sync-plan`** (plan 0008, ADLC-197) — local, tracker-free:
  `profile` reads how a project works and recommends a setup (two recommendations; `--write` saves
  `docs/context/plan-sync.json` after confirming), `import-plan` reads epics-as-markdown files into the neutral plan,
  `sync-plan --to markdown` writes it back as Markdown (idempotent, only managed blocks, never deletes). With no
  config `sync-plan` prints the recommendation and exits 1 without touching anything. `--to linear` pushes it to Linear and **`sync-status --to linear`** syncs To-Do status both ways, conflicts reported not resolved (both mock-tested; ADLC-239 is the live check), a Jira sink comes later. **`split-todos [--approve]`** (ADLC-201) proposes To-Dos for Tasks with none behind a review file; `sync-plan` uses only an approved one, else the five steps. `profile_project` / `import_plan` are also tools and `call` runs them without Jira credentials.

See `docs/plans/0001-symphony-poc/spec.md`'s "CLI control plane" section for design detail.

## Branch naming — required, not optional

Every workspace's branch is `{ISSUE-KEY}-{slug-of-summary}` (`workspace.py`'s `branch_name()`),
e.g. `ADLC-24-poller-live-verify-add-a-version-constant`. **Must start with the issue key** —
that's what Jira's Development-panel auto-linking depends on (confirmed missing on `ADLC-17`
before this was fixed, when the branch was `symphony-workflow/ADLC-17`). A human doing manual dev
work on a ticket outside the Plan/Implement path should use the same pattern by hand:
`git checkout -b ADLC-24-poller-live-verify-add-a-version-constant`.

**Branch naming alone was not sufficient — a connected app was also required, and now is.**
Confirmed 2026-09-24: the GitHub-for-Jira app is installed and connected (`ADLC-30`, Done) —
`GET /rest/dev-status/1.0/issue/summary` now shows `repository.byInstanceType` populated
(previously `{}` for every data type, confirmed missing as of 2026-09-25 before this was fixed).
**Still unverified**: `branch.byInstanceType` — no correctly-named branch has actually been
*pushed* to GitHub since the app connected (Plan-phase worktrees never commit, and this repo's
history is direct-to-main commits, not pushed feature branches), so full end-to-end
Development-panel linking hasn't been exercised yet. Don't assume it works until a real pushed
branch confirms it.

## The three non-negotiables

1. **Python codebase, CLI type.** The public interface is the `symphony-workflow` console script
   (Typer), not a library someone imports into unrelated code — though `JiraClient` is a normal
   typed class and importable when that's genuinely what's needed. Any new capability gets a
   subcommand or a `call`-able tool, not a side script.
2. **Jira API is integrated as tool calls, not just an HTTP client.** Every Jira operation must
   be reachable two ways: `client.py` (typed Python method) AND a registered `Tool` in
   `tools.py` with a pydantic input model, exposed via `symphony-workflow list-tools` / `call`. Adding
   a client method without a matching tool entry is an incomplete change.
3. **Unit tests are required, not optional.** No PR-worthy change ships without tests. HTTP
   calls are mocked with `respx` — nothing in this repo talks to a live Jira instance. If you
   add a client method, add a `respx`-mocked test for it in the same change.

## Jira API gotchas — confirmed against live `cloud-f1.atlassian.net`, don't relearn these

- **`epic` vs `parent` on `create_issue` are two different linking mechanisms, not
  interchangeable.** `ADLC` is a **team-managed** project (`"simplified": true`) — Story→Epic and
  Subtask→Story linking both use the standard `parent` field (`{"key": "ADLC-27"}`).
  `CF1` is classic/company-managed and uses the "Epic Link" custom field instead
  (`epic_link_field`, e.g. `customfield_10014` — confirmed correct for `CF1` only). Passing
  `epic="ADLC-27"` against `ADLC` fails with `"cannot be set. It is not on the appropriate
  screen"`; use `parent="ADLC-27"` there instead. `client.py`'s `create_issue` docstring has the
  full explanation.
- **Jira's `add_comment` body renders through wiki markup, not GitHub Markdown.** A leading
  `#`/`##`/`###` there means nested *numbered-list depth* (1 / a / i), not a heading — GFM ATX
  headers collide and render as a garbled outline (confirmed live on `ADLC-28`/`ADLC-30`'s plan
  comments before the fix). `orchestrator.py`'s `jira_safe_comment_body()` converts them to bold
  lines before posting; route any new comment-posting code through it too, don't post agent/plan
  text to a comment verbatim.
- **`/rest/api/2/search` is retired; use `/rest/api/3/search/jql` (POST, JSON body, not GET+query
  params).** `client.py`'s `search_issues` already does this correctly — but anything posting
  JQL queries *outside* the Python client (e.g. a shell hook) has to get this right independently;
  re-discovered the hard way writing `.claude/hooks/session-start-check-api.sh`.
- **Project creation (`POST /rest/api/3/project`) works fine via API — it does NOT require the
  Jira UI** — confirmed live creating `EXMP` (`ADLC-40`), an account with global `ADMINISTER`
  permission (check with `GET /rest/api/3/mypermissions?permissions=ADMINISTER`) can do this.
  Two traps: (1) `projectTemplateKey` naming is misleading —
  `com.pyxis.greenhopper.jira:gh-simplified-kanban-classic` creates a **classic**
  (company-managed) project despite "simplified" in the name (the word "classic" in the key is
  the real signal); the correct **team-managed** (next-gen, `"simplified": true`, matching
  `ADLC`'s own style) Kanban template is
  `com.pyxis.greenhopper.jira:gh-simplified-agility-kanban` — verify by checking the response's
  `style`/`simplified` fields, don't trust the template key name alone. (2) `DELETE
  /rest/api/3/project/{key}` soft-deletes into Jira's Trash with a ~60-day retention — the key
  *and* name stay reserved the whole time (`GET /rest/api/3/project/search?status=deleted` lists
  them), so recovering from a wrong-template creation needs a different key/name for the retry,
  not just a delete-and-recreate with the same ones.

## Architecture

```
src/symphony_workflow/
  config.py       # JiraConfig.from_env() — required: JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
                  # optional: JIRA_FIELD_EPIC_LINK / JIRA_FIELD_SPRINT / JIRA_FIELD_STORY_POINTS
  models.py       # pydantic response models (Issue, SearchResult, IssueCreateResult, Transition)
  client.py       # JiraClient — typed HTTP wrapper over Jira REST API v2, raises JiraError
  tools.py        # Tool dataclass + TOOLS registry + call_tool() — the agent-facing surface
  cli.py          # Typer app — human subcommands (get/search/...) + list-tools/call
  workspace.py    # WorkspaceManager — per-issue git worktree, §9.5 safety invariants, branch_name()
  agent_runner.py # wraps claude_agent_sdk.query() — the Plan/Implement session runner
  prompts.py      # Plan/Implement prompt templates (docs/WORKFLOW.md is the policy summary)
  runs.py         # Run/RunState/RunStore — the Plan/Implement human-gate state machine
  orchestrator.py # execute_plan/execute_implement — shared by api.py AND poller.py
  rollup.py       # Subtask -> Story -> Epic roll-up: all children Done -> parent Done (forward-only)
  decompose.py    # Epic -> Stories/Tasks, read-only, reuses run_plan()'s output_format wiring
  sync_decomposition.py # decomposition.json -> real Jira Story/Subtask issues
  drift.py        # diff-decomposition + sync --update: ids, sd-id markers, content drift vs Jira
  envsetup.py     # init-env / check-env: per-project .env creation + key report, never prints values
  plan.py         # neutral Category -> Task -> To-Do plan, ids, pyramid guardrails (plan 0008)
  epics_md.py     # source adapter: an epics-as-markdown project's files -> the neutral plan
  project_profile.py # `profile`: read how a project works, recommend a plan-sync setup (names only, no secrets)
  markdown_sink.py   # neutral plan -> Athena-compatible .md files (managed blocks only), checkbox state back
  status_sync.py  # sync-status: three-way status compare (last sync / plan / Linear), pull + push, conflicts reported
  linear_sink.py  # neutral plan -> Linear project/milestones/issues/sub-issues, sd-id idempotent, never deletes
  todo_source.py  # layer-3 To-Dos: approved agent proposal (split-todos), five workflow steps, 2-to-6 guardrail
  safepath.py     # run_git (repo-config command execution off), is_safe (no symlinks out of the project), scoped_path
  poller.py       # background asyncio task — auto-discovers + auto-Plans, never auto-Implements.
                  # Optional triage cadence (ADLC-29, off by default): a slower, batched agent
                  # turn classifies new tickets plan/skip/flag before the normal dispatch path
  api.py          # FastAPI app — HTTP surface over tools.py + orchestrator.py
tests/            # pytest + respx, one file per module above
```

Two entry points, one shared core: `cli.py` and `api.py` both go through `tools.py` →
`client.py` → Jira REST API v2; `api.py`'s `/plan`/`/implement` and `poller.py` both go through
`orchestrator.py` so they can't drift on what "running a plan" means. Never call `httpx`
directly from `cli.py`, `tools.py`, or `api.py` — go through `JiraClient`.

**Why API v2, not v3:** v2 takes plain-text `description`/comment bodies; v3 requires the
Atlassian Document Format (nested `content`/`paragraph` JSON). This org's other Jira tooling
(an internal `jira-cloud-f1` skill) already uses v2 successfully against
`cloud-f1.atlassian.net` — match that proven pattern rather than reintroducing ADF here.

## Adding a new tool (the standard change shape)

1. **Model** — add a pydantic args model to `tools.py` if the operation needs new input shape.
2. **Client** — add the method to `client.py`, calling the right REST endpoint, raising
   `JiraError` via `_raise_for_status` on failure. Add a `respx`-mocked test in
   `test_client.py` that asserts both the request sent (method/path/body) and the response
   parsing.
3. **Tool** — write a `_handler` function and register a `Tool(name, description, args_model,
   handler)` entry in `TOOLS`. Add a dispatch test in `test_tools.py` using a `MagicMock`
   client (don't re-test HTTP behavior here — that's `test_client.py`'s job).
4. **CLI** *(only if a human needs a friendly subcommand — not every tool needs one)* — add a
   Typer command in `cli.py`, add a test in `test_cli.py`.

Keep the three test files scoped to their layer: `test_client.py` owns HTTP correctness,
`test_tools.py` owns schema validation + dispatch, `test_cli.py` owns argument parsing and exit
codes.

## Code style

- **`uv`** for all package management — never `pip`. `uv sync --extra dev` installs everything.
- **Python 3.11+.** Type hints everywhere; pydantic models for anything crossing a boundary
  (HTTP response, tool input).
- **Explicit exception handling** — never a bare `except Exception: pass`. `JiraError` carries
  `status_code`; catch it specifically, not `Exception`.
- Follow `ruff` defaults (`pyproject.toml` → `[tool.ruff]`, line length 100).
- New client methods that read or write real Jira data must never run against a live instance
  in tests — mock with `respx`.

## Shell commands

```bash
uv sync --extra dev              # install deps (base + dev extras)
uv run pytest                    # run tests (mocked HTTP only)
uv run pytest --cov=symphony_workflow
uv run ruff check .              # lint
uv run ruff format .             # format
uv run symphony-workflow list-tools     # sanity-check the tool registry
```

## Secrets

Real credentials live only in a local, gitignored `.env` (`symphony-workflow init-env` creates it from a
template and gitignores it; `check-env [--verify]` reports which keys are missing without printing a
value — `envsetup.py`, ADLC-189). Never commit a token, and never let a test read `.env` — `test_cli.py`'s
`test_missing_env_vars_exit_with_helpful_message` explicitly clears the relevant env vars via
`monkeypatch` so the suite stays independent of whatever's in the developer's shell.

## Inspecting a directory you do not control

Plan sync reads and writes a project directory that can be someone else's (a cloned repo, a path an agent passes to a tool). Run git only through `safepath.run_git` (a repo's own `.git/config` can set `core.fsmonitor` to a command, and `git check-ignore` runs it), check every plan file with `safepath.is_safe` / `check_inside` before reading or writing it (no symlinks below the root, resolved path inside it), and give agent-facing tools `safepath.scoped_path` for any path argument. Regression tests: `tests/test_plan_sync_security.py` (ADLC-270).

## What NOT to do here

- **Don't wire Implement into the poller.** `poller.py` auto-discovers and auto-Plans on
  purpose, never auto-Implements — that split is the load-bearing safety property of Phase 5
  (see `docs/plans/0001-symphony-poc/spec.md`), not a missing feature. `acceptEdits` can't
  self-verify (documented finding, `docs/runs/ADLC-17/review.md`); an unattended Plan→Implement
  chain would ship unreviewed code with nobody watching. The optional triage step (`ADLC-29`)
  doesn't change this — it only ever decides plan/skip/flag, code applies the decision, and the
  triage agent itself gets no Jira write tools, same as Plan/Implement.
- **Don't add a database or persistent queue casually.** `RunStore` being in-memory-only is a
  known, documented POC limitation (`docs/plans/0001-symphony-poc/spec.md`, confirmed in practice
  on `ADLC-24`) — fixing it is real scope (Symphony §7.4-style recovery), not a quick add. If
  it's ever tackled, update
  `docs/plans/0001-symphony-poc/spec.md` first.
- Don't hardcode `cloud-f1.atlassian.net`, CF1's custom field IDs, `ADLC`, or a specific target
  repo path into `client.py`/`orchestrator.py`/`poller.py` — they belong in `.env` / env vars
  (`SYMPHONY_WORKFLOW_TARGET_REPO`, `SYMPHONY_WORKFLOW_JIRA_PROJECT`, ...), because this is meant to
  work against any Jira Cloud project and any target repo — see `docs/PORTABILITY.md`.
- Don't reach for the v3 API / ADF format without a documented reason — it changes every model
  and client method to be structured instead of plain text.
- Don't assume a correctly-named branch alone gets Jira auto-linking — see the Branch naming
  section above for the current status of the GitHub-for-Jira app connection this depends on.
