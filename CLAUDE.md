# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

> **Auto-loaded every session. Keep concise — essential rules only.**
> Ticket status → the `ADLC` Jira board | Last state → [docs/context/session-summary.md](docs/context/session-summary.md)
> (pattern borrowed from `ai-coding-nexjs-template`'s own proven session-handover convention)

## Project Overview

**jira-agent-cli** is now two things, not one: a Python CLI for the Jira Cloud REST API where
every operation is also a schema-described tool call, **and** a Symphony-inspired orchestration
service (FastAPI server + background poller) that drives a Jira ticket through a Plan/Implement
loop via the Claude Agent SDK. Both are first-class — the CLI didn't stay "just a CLI." See
[README.md](README.md) for the CLI pitch and confirmed CF1 field IDs; see
[docs/plans/0001-symphony-poc/intent.md](docs/plans/0001-symphony-poc/intent.md) /
[docs/plans/0001-symphony-poc/spec.md](docs/plans/0001-symphony-poc/spec.md) /
[docs/plans/0001-symphony-poc/implement.md](docs/plans/0001-symphony-poc/implement.md)
for the orchestration service's why/design/roadmap (read those three before touching `api.py`,
`orchestrator.py`, `poller.py`, `runs.py`, or `agent_runner.py`) — the general convention behind
this `docs/plans/<NNNN>-<slug>/` layout is `docs/plans/README.md` (`ADLC-43`); see
[docs/PORTABILITY.md](docs/PORTABILITY.md) for running this against a *different* repo/Jira
project. **New to this repo? Load the `jira-agent-cli-ops` skill first**
(`.claude/skills/jira-agent-cli-ops/SKILL.md`) — the short operational reference this file's
sibling docs don't repeat. Setting this up for the first time as a human (not a Claude Code
session)? [docs/QUICKSTART.md](docs/QUICKSTART.md) is a linear walkthrough, not reference material.

This is a standalone package under `~/Documents/git_saas/`, a private repo in the `cloud-f1`
GitHub org. It is not part of `content-asset-system` — don't assume its rules apply here beyond
what this file states.

## Jira tracking for this repo's own work

Work on this repo is tracked in Jira project **ADLC** (`cloud-f1.atlassian.net`, Kanban board
74), not CF1 — CF1 is content/marketing work, ADLC is agent-driven dev work. Structure:
**Epic `ADLC-2`** → one **Story per POC phase** → **Subtasks** per concrete work item. When
you pick up a subtask, transition it as you go (`jira-agent transition <key> "In Progress"` /
`"Done"`) — this repo's own agent-tool CLI is the thing doing the tracking, so use it. See
[docs/plans/0001-symphony-poc/intent.md](docs/plans/0001-symphony-poc/intent.md)'s Tracking
table for the current phase → ticket mapping.

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
`list-tools` — see the `jira-agent-cli-ops` skill):

- **`jira-agent plan/implement <key>`** (ADLC-45) — calls `orchestrator.execute_plan`/
  `execute_implement`, the same functions `api.py`/`poller.py` use. `RunStore` is
  per-process here, so `plan` persists its `Run` to `docs/runs/<key>/run.json` for a
  later `implement` invocation to reload.
- **`jira-agent decompose <key>`** (ADLC-46) — breaks a Jira Epic into Stories/Tasks for
  human review. Reuses the Plan phase's `permission_mode="plan"` + `output_format` wiring
  (`prompts.PLAN_DECOMPOSE_SCHEMA` in place of `PLAN_OUTPUT_SCHEMA`) — read-only, no Jira
  writes. Renders `docs/runs/<key>/decomposition.{md,json}`.
- **`jira-agent sync-decomposition <key>`** (ADLC-47) — materializes an already-reviewed
  `decomposition.json` as real Jira Story (`parent=<epic-key>`)/Subtask
  (`parent=<story-key>`) issues via the existing `create_issue` path. Does not re-run the
  decompose agent.

See `docs/plans/0001-symphony-poc/spec.md`'s "CLI control plane" section for design detail.

## Branch naming — required, not optional

Every workspace's branch is `{ISSUE-KEY}-{slug-of-summary}` (`workspace.py`'s `branch_name()`),
e.g. `ADLC-24-poller-live-verify-add-a-version-constant`. **Must start with the issue key** —
that's what Jira's Development-panel auto-linking depends on (confirmed missing on `ADLC-17`
before this was fixed, when the branch was `jira-agent-cli/ADLC-17`). A human doing manual dev
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

1. **Python codebase, CLI type.** The public interface is the `jira-agent` console script
   (Typer), not a library someone imports into unrelated code — though `JiraClient` is a normal
   typed class and importable when that's genuinely what's needed. Any new capability gets a
   subcommand or a `call`-able tool, not a side script.
2. **Jira API is integrated as tool calls, not just an HTTP client.** Every Jira operation must
   be reachable two ways: `client.py` (typed Python method) AND a registered `Tool` in
   `tools.py` with a pydantic input model, exposed via `jira-agent list-tools` / `call`. Adding
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

## Architecture

```
src/jira_agent_cli/
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
  decompose.py    # Epic -> Stories/Tasks, read-only, reuses run_plan()'s output_format wiring
  sync_decomposition.py # decomposition.json -> real Jira Story/Subtask issues
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
(`content-asset-system`'s `jira-cloud-f1` skill) already uses v2 successfully against
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
uv run pytest --cov=jira_agent_cli
uv run ruff check .              # lint
uv run ruff format .             # format
uv run jira-agent list-tools     # sanity-check the tool registry
```

## Secrets

Real credentials live only in a local, gitignored `.env` (copy from `.env.example`). Never
commit a token, and never let a test read `.env` — `test_cli.py`'s
`test_missing_env_vars_exit_with_helpful_message` explicitly clears the relevant env vars via
`monkeypatch` so the suite stays independent of whatever's in the developer's shell.

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
  (`JIRA_AGENT_CLI_TARGET_REPO`, `JIRA_AGENT_CLI_JIRA_PROJECT`, ...), because this is meant to
  work against any Jira Cloud project and any target repo — see `docs/PORTABILITY.md`.
- Don't reach for the v3 API / ADF format without a documented reason — it changes every model
  and client method to be structured instead of plain text.
- Don't assume a correctly-named branch alone gets Jira auto-linking — see the Branch naming
  section above for the current status of the GitHub-for-Jira app connection this depends on.
