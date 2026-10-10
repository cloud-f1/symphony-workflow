# symphony-workflow

A Python CLI for the Jira Cloud REST API where every operation is also exposed as a
schema-described **tool call** — so an AI agent (Claude Code, Codex, a cron job) can shell out
to it the same way it would call a function, instead of an engineer running it by hand. The
Plan/Implement run-loop (`get`/`comment`/`transition`/`plan`/`implement`) also works against
**Linear** as a second tracker backend (`SYMPHONY_WORKFLOW_TRACKER=jira|linear`, `ADLC-57`) — see
"Tracker backends" below. The tool-call layer itself (`tools.py`) stays Jira-only.

```bash
symphony-workflow list-tools                                    # every tool's JSON schema
symphony-workflow call get_issue '{"key": "CF1-1"}'              # agent-style invocation
symphony-workflow get CF1-1                                      # same thing, human-friendly
symphony-workflow search 'project = CF1 AND status != Done'
symphony-workflow comment CF1-1 "Looks good, merging."
symphony-workflow transition CF1-1 Done
symphony-workflow rollup CF1-7 --dry-run                         # Subtask done -> close Story/Epic when all children are
```

**New here?** `docs/QUICKSTART.md` is a linear walkthrough from `uv sync` to driving a real
ticket through Plan/Implement — this README is the pitch, not a tutorial.

## Why a tool-call layer, not just a client

Most Jira wrappers are just an HTTP client — fine for a script, awkward for an agent that needs
to (a) discover what it can do and (b) call it with untrusted/LLM-generated arguments. This repo
separates the two:

- **`client.py`** — a normal typed Python client (`JiraClient`). Import it directly if you're
  writing Python.
- **`tools.py`** — a registry of named tools, each with a pydantic input model. `list-tools`
  dumps `[{name, description, input_schema}, ...]` (the same shape as an Anthropic/OpenAI
  function-calling tool definition); `call <name> '<json>'` validates the JSON against that
  tool's schema before touching the network, so a malformed agent call fails fast with a clear
  error instead of hitting Jira with garbage.

This mirrors the "tracker adapter with provider-native tools" pattern from OpenAI's
[Symphony](https://github.com/openai/symphony) orchestration spec — Symphony has agents call
tracker operations as named tools rather than importing a client library.

## Background

Cloud-F1 already has working, hand-rolled Jira REST helpers scattered across
`content-asset-system`'s `.claude/skills/shared/jira-cloud-f1/` and `jira-awoo/` skill files
(`urllib`-based scripts pasted at the top of one-off Python files). This repo consolidates that
proven pattern — Jira Cloud API **v2**, Basic auth via `email:api_token`, plain-text
description/comment bodies (not the v3 Atlassian Document Format), fetch-transitions-then-match-
by-name for status changes — into a real installable package with tests, instead of copy-pasted
snippets. If you're touching CF1 tickets from `content-asset-system`, keep using the MCP tools /
skill scripts there for now; this repo is the standalone, agent-callable version.

**Confirmed CF1 custom field IDs** (as of 2026-06-04, verified via `detect_project_fields`):
Epic Link = `customfield_10014`, Sprint = `customfield_10020`, Story Points = `customfield_10031`.
These are per-project, not global — re-verify before pointing this at a different Jira project.
Wire them via `.env` (see `.env.example`); `create_issue` only sends them when configured.

⚠️ Verified live against `cloud-f1.atlassian.net` on 2026-09-24: v2 issue CRUD works;
v2's classic `/rest/api/2/search` is now HTTP 410 (retired), so `search_issues` uses v3's
`/rest/api/3/search/jql` instead — the one place this client crosses API versions. Unit
tests still only exercise mocked HTTP (`respx`); re-verify against a different Jira instance
before trusting either endpoint there.

## Keeping the hierarchy honest: `audit` and `add-subtasks`

The Epic -> Story -> Subtask rules are in [docs/JIRA_HIERARCHY.md](docs/JIRA_HIERARCHY.md). Two commands
keep a project's *existing* cards true to them (`decompose` / `sync-decomposition` only help for an Epic you
have not built yet):

```bash
symphony-workflow audit ADLC --open --assignee-me     # Stories with no Subtasks, Stories under no Epic, Epics with no Stories, Done Stories with open Subtasks, unassigned cards; exit 1 on any error
symphony-workflow add-subtasks plan.json --dry-run    # {"ADLC-12": ["Add X", "Tests: unit"], ...}; idempotent; a Done Story's new Subtasks go Done too
```

Also the `audit_hierarchy` and `add_subtasks` tools. Read-only `audit`; `add-subtasks` writes and has `--dry-run`.

## Rolling a finished Subtask up the tree

`symphony-workflow rollup <subtask-key>` (also the `rollup_issue` tool) marks the Subtask Done,
then marks its Story Done if *all* the Story's Subtasks are Done, then the Epic Done if *all* the
Epic's Stories are Done. Forward-only (never reopens anything), stops at the first level that
isn't fully done, safe to re-run, and `--dry-run` shows what it would do without transitioning.
It also takes a Story or Epic key (re-evaluates that issue and upward).

- `--json` prints `{"key", "dry_run", "transitions": [{"key","from","to"}], "stopped_at": {"key","reason"}|null}`.
- Exit codes: `0` ok (including "nothing to do"), `2` key not found/invalid, `3` Jira API error
  (anything already transitioned is printed first, never hidden).
- A Story/Epic with no children is judged on its own status and never closed by the roll-up.
- Children are found with `parent = <key>` (how team-managed projects link Subtask→Story→Epic),
  "done" means Jira's `statusCategory` is done, and Jira-only (Linear has no such hierarchy).

## Tracker backends

`get`/`comment`/`transition`/`plan`/`implement` (and the HTTP equivalents in `api.py`) work
against either backend, picked via one env var:

```bash
SYMPHONY_WORKFLOW_TRACKER=jira      # default — JIRA_BASE_URL/JIRA_EMAIL/JIRA_API_TOKEN
SYMPHONY_WORKFLOW_TRACKER=linear    # LINEAR_API_KEY + SYMPHONY_WORKFLOW_LINEAR_TEAM
```

**Not** tracker-agnostic: `search` (raw JQL — Linear has no equivalent), `call`/`list-tools`
(`tools.py`'s field set is Jira-shaped: Epic/parent linking, sprint/story-points), `rollup`
(needs the `parent` field), and `decompose`/`sync-decomposition`. These stay Jira-only regardless of `SYMPHONY_WORKFLOW_TRACKER`
— see `docs/plans/0005-linear-tracker-backend/intent.md`'s non-goals for why porting them isn't
just more of the same work (Linear has no Epic issue type, no JQL, a different field model
entirely).

### Recommended Linear structure: mapping Jira's Project → Epic → Story → Task

Jira gives you this hierarchy for free: one **Project** per repo, **Epic**s inside it, **Story**/
**Task** issues inside each Epic. Linear has no `issuetype` field at all, so the naive mapping
(one Linear **Team** per repo, one Linear **Project** per Epic) breaks down fast — Linear plans
cap the number of **Team**s (often just 2), so "one Team per repo" can't scale past a couple of
repos. The mapping that actually works, confirmed against Linear's real GraphQL schema:

| Jira | Linear | Why |
|---|---|---|
| (org) | Workspace | 1:1 |
| **Project** (= one repo) | **Project** (not Team) | Projects aren't capped like Teams are — this is what actually scales to N repos |
| **Epic** | **`ProjectMilestone`** (inside that Project) | `ProjectMilestone.project` scopes it to one Project; `issues` lists everything under it |
| **Story** | **Issue**, with `projectMilestoneId` set | `IssueCreateInput`/`IssueUpdateInput` both take `projectMilestoneId` directly |
| **Task** | **sub-issue** (an Issue with `parentId` set to the Story's issue id) | Linear's only hierarchy primitive below Issue — no separate "Task" type exists |

Keep using one shared Team (or your existing one) across every repo; give each repo its own
Project; use Milestones inside that Project for Epics. This is exactly the 3-layer Epic → Story →
Task breakdown Jira gives natively, reconstructed from Linear's actual primitives instead of
guessed at.

**繁體中文版**：Jira 天生就有這個層級：每個 repo 一個 **Project**，裡面有 **Epic**，Epic 裡面
再有 **Story**/**Task**。Linear 完全沒有 `issuetype` 這個欄位，所以直覺的對應方式（每個 repo
對一個 Linear **Team**，每個 Epic 對一個 Linear **Project**）很快就會出問題——Linear 的方案
通常會限制 **Team** 的數量（常見是 2 個），所以「一個 repo 一個 Team」沒辦法擴展到超過兩個
repo。真正可行、且已對照 Linear 實際 GraphQL schema 確認過的對應方式：

| Jira | Linear | 為什麼 |
|---|---|---|
| （組織層級） | Workspace | 一對一 |
| **Project**（＝一個 repo） | **Project**（不是 Team） | Project 不像 Team 會被方案上限卡住——這才是真正能撐到 N 個 repo 的做法 |
| **Epic** | **`ProjectMilestone`**（在該 Project 底下） | `ProjectMilestone.project` 把它限定在單一 Project；`issues` 會列出底下所有工作項目 |
| **Story** | **Issue**，設定 `projectMilestoneId` | `IssueCreateInput`／`IssueUpdateInput` 都直接支援 `projectMilestoneId` 欄位 |
| **Task** | **sub-issue**（把某個 Issue 的 `parentId` 指向 Story 那個 Issue） | Linear 在 Issue 之下唯一的階層機制——沒有獨立的「Task」類型 |

做法：維持共用同一個 Team（沿用現有的就好），每個 repo 各自開一個 Project，Epic 則用該
Project 底下的 Milestone 來表達。這樣就重建出 Jira 原生的 Epic → Story → Task 三層結構——
不是憑空猜測，而是照著 Linear 實際的物件模型組出來的。

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync --extra dev
cp .env.example .env   # fill in JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
uv run symphony-workflow list-tools
```

Get a Jira API token: Atlassian account settings → Security → API tokens.

## Testing

```bash
uv run pytest              # all HTTP calls mocked via respx — no live Jira access, no network
uv run pytest --cov=symphony_workflow
uv run ruff check .
```

## Releasing

Bump `pyproject.toml`'s `version` and `plugin/.claude-plugin/plugin.json`'s `version` to the same
new value, add a `## <version>` entry to `CHANGELOG.md`, and land all three in one PR (this
repo's existing pattern — see any past release commit). Once that PR is merged to `main`:

```bash
scripts/release.sh <version>   # e.g. scripts/release.sh 0.5.0
git push origin v<version>     # the script tags locally but never pushes itself
```

`scripts/release.sh` exists because `v0.3.0`/`v0.4.0`'s CHANGELOG entries said "tagged" but
`git tag -l` disagreed — a manual multi-step process silently dropped the tag step, twice. The
script verifies (not bumps) `pyproject.toml`/`plugin.json`/`CHANGELOG.md` are already consistent
for `<version>`, runs `tests/test_version.py`, and only then creates the annotated tag — it exits
non-zero, tagging nothing, if any check fails or `v<version>` already exists.

## Project layout

```
src/symphony_workflow/
  config.py        # env-var config (JiraConfig, LinearConfig, ConfigError)
  models.py        # pydantic response models (Issue, SearchResult, Transition, ...)
  tracker.py       # TrackerClient Protocol + get_tracker_client() backend selector (ADLC-57)
  client.py        # JiraClient — typed HTTP wrapper (JiraError on non-2xx)
  linear_client.py # LinearClient — GraphQL wrapper, TrackerClient surface only (ADLC-57)
  tools.py         # Tool registry + call_tool() dispatcher — the agent-facing surface, Jira-only
  rollup.py        # Subtask -> Story -> Epic roll-up (Jira-only), behind `rollup` / the rollup_issue tool
  hierarchy.py     # audit a project's Epic -> Story -> Subtask shape + add Subtasks to Stories (Jira-only), behind `audit` / `add-subtasks`
  cli.py           # Typer app: friendly subcommands + list-tools/call
tests/             # pytest + respx, one test file per module above
```

## Adding a new tool

1. Add a pydantic args model to `tools.py`.
2. Add the client method to `client.py` if it doesn't exist yet (with a test in
   `test_client.py`).
3. Add a `_handler` function and register a `Tool(...)` entry in `TOOLS`.
4. Add coverage in `test_tools.py` (dispatch) and, if it deserves a human-friendly subcommand,
   `cli.py` + `test_cli.py`.

See [`CLAUDE.md`](CLAUDE.md) for the fuller set of conventions this repo follows.

## Adding a new tracker backend

`LinearClient` (`ADLC-57`) is the one worked example — read it alongside `client.py` before
starting a third backend.

1. Implement `tracker.TrackerClient`'s Protocol surface (`get_myself`/`get_issue`/`add_comment`/
   `transition_issue`/`search_ready_tickets`/`issue_url`/`close`/context-manager) in a new
   `<backend>_client.py` — structural typing, no inheritance needed (`JiraClient` satisfies the
   Protocol without ever importing it).
2. Add a `<Backend>Config` to `config.py` (`.from_env()`, same pattern as `JiraConfig`/
   `LinearConfig`).
3. Add a branch to `tracker.get_tracker_client()`'s `if`/`elif` chain, importing the new backend's
   module and config lazily inside that branch — not at module level (keeps `tracker.py` a leaf
   that never requires a backend's dependencies unless it's the one actually selected).
4. Add `test_<backend>_client.py` (mock the backend's own HTTP shape — `respx` either way, REST or
   GraphQL) and a `TrackerClient` Protocol-conformance assertion in `test_tracker.py`
   (`isinstance(<Backend>Client(config), TrackerClient)`).
5. Update the tables in this README, `CLAUDE.md`, and `.claude/skills/symphony-workflow-ops/
   SKILL.md` (**both** copies — see that skill's own note on keeping them in sync) — same edit in
   three places, or the docs drift the way `ADLC-89` found and fixed.

**Stays Jira-only regardless of how many backends exist** (`ADLC-57`'s non-goals — don't try to
port these to a new backend without a real, separate design pass): `tools.py`'s AI-agent tool-call
surface (`create_issue`'s Epic/parent-linking, sprint/story-points fields are Jira-shaped),
`decompose.py`/`sync_decomposition.py` (Epic→Story→Subtask has no clean equivalent on a backend
without an Epic issue type), and `cli.py`'s `search` command (raw JQL has no universal
equivalent). See `docs/plans/0005-linear-tracker-backend/intent.md` for the fuller reasoning the
first time this boundary was drawn.
