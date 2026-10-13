# symphony-workflow

[English](README.md) · [繁體中文](README.zh-TW.md)

Keep your plan as Markdown files, put it into **Linear** or **Jira** when you want a board, and keep the
status in step both ways. Underneath, every Jira operation is also a schema-described tool call, so an AI
agent can drive tickets the same way an engineer would.

Nothing here deletes anything, and every command that writes has a `--dry-run`.

## Who it is for

- **You keep your plan in Markdown** (a `docs/epics/e1-...md` file per epic, the way an Athena-style
  workflow does) and want it on a Linear or Jira board without retyping it, and without the two drifting apart.
- **You run Jira tickets with an AI agent** (Claude Code, Codex, a cron job) and want it to read, comment,
  move and plan tickets through named tools instead of raw HTTP.
- **You are fixing a Jira project** whose Stories have no Subtasks, or whose parents never close.

It is not a Jira client library for unrelated code, and it does not edit your code: the Implement phase is
always started by a person.

## Five-minute start

**1. Install.** No clone needed:

```bash
uv tool install git+https://github.com/cloud-f1/symphony-workflow   # keeps a `symphony-workflow` command
uvx --from git+https://github.com/cloud-f1/symphony-workflow symphony-workflow --help   # or try it once
```

Add `@vX.Y.Z` to the URL to pin a release (see the tags). To work on the code instead: `git clone`, then
`uv sync --extra dev` and prefix commands with `uv run`.

**Using Claude Code?** Install the plugin (two skills and a SessionStart hook; it does not install the CLI above):

```text
/plugin marketplace add cloud-f1/symphony-workflow
/plugin install symphony-workflow-ops --marketplace cloud-f1/symphony-workflow
```

**2. Look at a project, change nothing.** From a project that has `docs/epics/*.md`:

```bash
symphony-workflow profile --path .
```

**3. Credentials, only when you need a tracker.** Markdown-only use needs none. For Jira or Linear, keep them
in the project's own gitignored `.env`, never in a chat:

```bash
symphony-workflow init-env          # creates .env with empty keys and gitignores it
symphony-workflow check-env --verify # names missing keys, one read-only call, never prints a value
```

## Which setup do I need?

| You have | Run | It needs |
|---|---|---|
| Plan files, no tracker | `profile`, then `sync-plan --to markdown` | nothing |
| Plan files, tasks with no To-Dos | `split-todos`, review, `split-todos --approve` | Claude credentials the Claude Agent SDK can use |
| Plan files, you want a Linear board | `sync-plan --to linear --project "Name"` | `LINEAR_API_KEY`, `SYMPHONY_WORKFLOW_LINEAR_TEAM` |
| Plan files, you want a Jira board | `sync-plan --to jira --project KEY` | the three Jira keys in `.env` |
| A board you want kept in step | `sync-status --to linear` or `--to jira` | the same as the sink |
| Jira tickets and an agent to work them | the Plan/Implement loop, see `docs/QUICKSTART.md` | Jira keys and Claude credentials |
| A Jira hierarchy that rotted | `audit`, `add-subtasks`, `rollup`, see Reference | the three Jira keys |

When unsure, start with `profile`: it prints two recommendations, the first from how the project is used,
the second refined from what the plans contain, and saves nothing unless you pass `--write`.

## Use cases

Every snapshot below is real output, captured from a throwaway project (and, for Linear and Jira, a
throwaway board that was deleted afterwards). The full text is in `docs/snapshots/`.

### 1. Set up credentials without pasting a token into a chat

You are about to use Jira or Linear from a new project. `init-env` writes the file, `check-env` tells you
what is missing and never prints a value.

<!-- snapshot: docs/snapshots/01-setup.txt -->
```text
$ symphony-workflow init-env
Created .env (owner-only permissions).
Added .env to .gitignore.
Next: fill in JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN, then run `symphony-workflow check-env --verify`.

$ symphony-workflow check-env
.env: found
  MISSING  JIRA_BASE_URL
  MISSING  JIRA_EMAIL
  MISSING  JIRA_API_TOKEN
(exit 1)

# you paste the base URL and your e-mail, forget the token:
$ symphony-workflow check-env
.env: found
  ok       JIRA_BASE_URL
  ok       JIRA_EMAIL
  MISSING  JIRA_API_TOKEN
(exit 1)
```

Next: fill the empty keys in an editor, then `symphony-workflow check-env --verify`.

### 2. See how a project already works

Before choosing anything, let the tool read the project: its plan files, which credential *names* exist,
issue keys in git history, and the shape of the plan.

<!-- snapshot: docs/snapshots/02-profile.txt -->
```text
$ symphony-workflow profile --path .
Project: .
Plan files: 4 epic file(s); index=False progress=True strategy-log=False; plan folders=0; athena commands=False
Tracker: jira credentials=False linear credentials=False SYMPHONY_WORKFLOW_TRACKER=unset; issue keys in history: none
Content: 2 phase(s) (1 not complete), 4 epic(s), tasks per phase median=1.5, acceptance items median=1.0, epics with a ready-made To-Do breakdown=75%
Recommendation 1 (from usage): tracker=markdown plan_source=epics-md todo_source=hybrid push=manual record=markdown include_done=false
  - tracker: no tracker evidence in this project -> use Markdown files
  - tracker: LINEAR_API_KEY is set in the shell, shared by every project, but not in this project's .env -> not enough to pick Linear; pass --to linear if you want it
  - plan source: 4 epic file(s) under docs/epics -> read them with `epics-md`
Recommendation 2 (refined from content): tracker=markdown plan_source=epics-md todo_source=breakdown push=manual record=markdown include_done=false
  - To-Do source: 75% of epics have a ready-made To-Do breakdown -> use each epic's own breakdown (to-do list, phases or story headings) as To-Dos
```

`import-plan` then shows the plan as three layers (category, task, to-do) and warns when the shape is off:

<!-- snapshot: docs/snapshots/03-import-plan.txt -->
```text
$ symphony-workflow import-plan --path .
plan: 2 categor(ies), 3 task(s), 5 to-do(s)
  phase-2  Phase 2  [in_progress]
    E1  Login page  [in_progress]  (3 to-dos)
    E2  Reports  [todo]  (2 to-dos)
  unphased  Unphased  [todo]
    E4  Not in any phase  [todo]  (0 to-dos)
  WARNING unphased one_task: a Category with one Task is really a Task
  WARNING E4 no_todos: no To-Dos yet (layer-3 generation fills them in)
```

A category with one task is really a task, and a task with no To-Dos has nothing for a board to track yet.
Cases 3 and 4 fix the second one.

### 3. Keep the plan as Markdown, no tracker

No board, but you want a task list and an index you can commit. `sync-plan --to markdown` writes
Athena-compatible files, only inside its own managed blocks, so your hand-written text stays.

<!-- snapshot: docs/snapshots/04-markdown.txt -->
```text
$ symphony-workflow sync-plan --path . --from epics-md --to markdown --dry-run
  To-Dos for E4: from steps
  Would update docs/epics/e1-login.md
  Would update docs/epics/e2-reports.md
  Would update docs/epics/e4-loose.md
  Would create docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task

$ symphony-workflow sync-plan --path . --from epics-md --to markdown
  To-Dos for E4: from steps
  Updated      docs/epics/e1-login.md
  Updated      docs/epics/e2-reports.md
  Updated      docs/epics/e4-loose.md
  Created      docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task

# run it again: nothing to do
$ symphony-workflow sync-plan --path . --from epics-md --to markdown
  Unchanged    docs/epics/e1-login.md
  Unchanged    docs/epics/e2-reports.md
  Unchanged    docs/epics/e4-loose.md
  Unchanged    docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task
```

The dry run told you what would change, the real run did it, and the third run found nothing to do.

### 4. Let an agent propose the To-Dos, you approve them

A task with no breakdown gets five fixed steps (Spec, Implement, Tests and QA, Commit, Merge) by default.
For something more specific, one read-only agent turn writes a proposal you can edit. Nothing reaches a
tracker until you approve it.

<!-- snapshot: docs/snapshots/05-todos.txt -->
```text
$ symphony-workflow split-todos --path .
Proposed To-Dos for 1 Task(s): docs/context/todo-proposal.md
Review docs/context/todo-proposal.json, then run `symphony-workflow split-todos --approve`.

# docs/context/todo-proposal.json is what you review and edit:
{
  "approved": false,
  "tasks": {
    "E4": [
      "Clarify and document the scope/requirements for this task (since it's currently in no phase and criteria is unspecified)",
      "Implement the core change needed to satisfy the requirement",
      "Update related docs/config and wire the change into the existing system",
      "Write and run tests covering the acceptance criteria, confirm they pass"
    ]
  }
}

$ symphony-workflow split-todos --path . --approve
Approved 1 Task(s) in docs/context/todo-proposal.json.

$ symphony-workflow sync-plan --path . --from epics-md --to markdown
  To-Dos for E4: from proposal
  Updated      docs/epics/e1-login.md
  Updated      docs/epics/e2-reports.md
  Updated      docs/epics/e4-loose.md
  Created      docs/epics/EPIC_INDEX.md
  WARNING unphased one_task: a Category with one Task is really a Task
```

The proposal is a file: change the wording, drop an item, then approve. Until you do, `sync-plan` waits
instead of writing the five steps.

### 5. Push the plan to Linear

Categories become milestones, tasks become issues, To-Dos become sub-issues, all inside one project. The
dry run reads Linear and writes nothing.

<!-- snapshot: docs/snapshots/06-linear.txt -->
```text
$ symphony-workflow sync-plan --path . --from epics-md --to linear --project "Demo plan" --dry-run
  To-Dos for E4: from steps
Linear project: Demo plan (dry run, nothing written)
  Would create milestone Phase 2
  Would create issue     Login page
  Would create sub-issue Build the form
  Would create sub-issue Wire the API
  Would create sub-issue Tests
  Would create issue     Reports
  Would create sub-issue Phase 1 — Data layer
  Would create sub-issue Phase 2 — UI
  Would create milestone Unphased
  Would create issue     Not in any phase
  Would create sub-issue Spec
  Would create sub-issue Implement
  Would create sub-issue Tests and QA
  Would create sub-issue Commit
  Would create sub-issue Merge
  WARNING unphased one_task: a Category with one Task is really a Task

$ symphony-workflow sync-plan --path . --from epics-md --to linear --project "Demo plan"
  To-Dos for E4: from steps
...
```

The second run, the one after it, finds every object by an `sd-id:` line it put in each description:

<!-- snapshot: docs/snapshots/06-linear.txt -->
```text
# run it again: nothing to create or update
$ symphony-workflow sync-plan --path . --from epics-md --to linear --project "Demo plan"
  To-Dos for E4: from steps
Linear project: Demo plan
  Unchanged    milestone Phase 2
  Unchanged    issue     Login page
  Unchanged    sub-issue Build the form
  Unchanged    sub-issue Wire the API
  Unchanged    sub-issue Tests
  Unchanged    issue     Reports
  Unchanged    sub-issue Phase 1 — Data layer
  Unchanged    sub-issue Phase 2 — UI
  Unchanged    milestone Unphased
  Unchanged    issue     Not in any phase
  Unchanged    sub-issue Spec
  Unchanged    sub-issue Implement
  Unchanged    sub-issue Tests and QA
  Unchanged    sub-issue Commit
  Unchanged    sub-issue Merge
  WARNING unphased one_task: a Category with one Task is really a Task
```

Use a fresh `--project` name the first time: an existing project's hand-made issues have no `sd-id:` line, so
they would be duplicated, not adopted.

### 6. Keep status in step, both ways

A change on one side moves to the other. A change on both sides is a conflict: it is reported and left alone.

<!-- snapshot: docs/snapshots/07-status.txt -->
```text
# in Linear someone moves 'Build the form' to Done; you pull it:
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  pull      to-do  E1.d1  plan=todo tracker=done (last sync: todo)
  1 pull

# you tick 'Wire the API' in docs/epics/e1-login.md; push it:
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  push      to-do  E1.d2  plan=done tracker=todo (last sync: todo)
  1 push

# nothing left to do:
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  nothing to do

# both sides change 'Tests' differently: a conflict, left alone
$ symphony-workflow sync-status --path . --to linear --project "Demo plan"
Linear project: Demo plan
  conflict  to-do  E1.d3  plan=done tracker=in_progress (last sync: todo)
  1 conflict
Conflicts were left as they are: fix one side, then run this again.
(exit 1)
```

Pulling ticks the checkbox in your Markdown file; pushing moves the card. The progress matrix of an
Athena-style project is not written back: it has five step columns and a board has one status.

### 7. Push the plan to Jira

Categories become Epics, tasks become Stories, To-Dos become Subtasks. The keys it created are saved in
`docs/context/sync-state.json` (commit that file), because Jira's child lists lag a few seconds behind a create.

<!-- snapshot: docs/snapshots/08-jira.txt -->
```text
$ symphony-workflow sync-plan --path . --from epics-md --to jira --project ADLC --dry-run
  To-Dos for E4: from steps
Jira project: ADLC (dry run, nothing written)
  Would create epic     Phase 2
  Would create story    Login page
  Would create subtask  Build the form
  Would create subtask  Wire the API
  Would create subtask  Tests
  Would create story    Reports
  Would create subtask  Phase 1 — Data layer
  Would create subtask  Phase 2 — UI
  Would create epic     Unphased
  Would create story    Not in any phase
  Would create subtask  Spec
  Would create subtask  Implement
  Would create subtask  Tests and QA
  Would create subtask  Commit
  Would create subtask  Merge
  WARNING unphased one_task: a Category with one Task is really a Task
...
```

Running it again finds the cards by marker and the saved keys, so nothing is created twice.

## What it will never do

- **Delete anything**, in any direction. A card or file that exists on one side only is reported.
- **Overwrite your text.** Markdown files are only touched inside `<!-- sd:begin ... -->` blocks.
- **Print a credential.** Keys live in the project's `.env`; checks name what is missing, never the value.
- **Trust a file it did not write.** A `docs/context/sync-state.json` that names someone else's ticket is
  ignored; git runs with repository-supplied commands switched off; plan files behind a symlink are refused.
- **Start Implement on its own.** The background poller plans; a person starts Implement.

Two things to know: ids are unique per plan, so two plans must not sync into one Jira project, and the Linear
and Jira sinks are idempotent only for objects they created themselves.

## Reference

The sections below are the original reference for the tool-call layer, the tracker backends, the Jira hierarchy
commands and the project layout. The Plan/Implement loop is walked through in `docs/QUICKSTART.md`, the state of
the project is in `docs/HANDOVER.md`.

### Tool calls

```bash
symphony-workflow list-tools                                    # every tool's JSON schema
symphony-workflow call get_issue '{"key": "CF1-1"}'              # agent-style invocation
symphony-workflow get CF1-1                                      # same thing, human-friendly
symphony-workflow search 'project = CF1 AND status != Done'
symphony-workflow comment CF1-1 "Looks good, merging."
symphony-workflow transition CF1-1 Done
symphony-workflow rollup CF1-7 --dry-run                         # Subtask done -> close Story/Epic when all children are
```

### Why a tool-call layer, not just a client

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

### Background

Cloud-F1 already had working, hand-rolled Jira REST helpers scattered across
skill files in a private content repo (`urllib`-based scripts pasted at the top of one-off Python files). This repo consolidates that
proven pattern — Jira Cloud API **v2**, Basic auth via `email:api_token`, plain-text
description/comment bodies (not the v3 Atlassian Document Format), fetch-transitions-then-match-
by-name for status changes — into a real installable package with tests, instead of copy-pasted
snippets. If you're touching CF1 tickets from another repo, keep using the MCP tools /
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

### Keeping Jira in step with a spec: `diff-decomposition` and `sync-decomposition --update`

`sync-decomposition` creates cards once. When the spec (`docs/runs/<epic>/decomposition.json`) changes later:

```bash
symphony-workflow diff-decomposition ADLC-12 [--adopt] [--json]   # read-only; exit 0 in sync, 1 drift, 2 bad input, 3 Jira error
symphony-workflow sync-decomposition ADLC-12 --update --dry-run   # create missing + update changed; never deletes; idempotent
```

Cards are matched to entries by a stable `id` (written to the JSON) and a trailing `sd-id: <id>` line in the
Jira description, so a rename updates the card instead of duplicating it. `--adopt` matches older cards by exact
summary and asks before writing; a Subtask moved to another Story shows as `parent-differs` and `--update` moves it back.
Status, comments and links are not drift. Jira-only. See `docs/JIRA_HIERARCHY.md`.

### Keeping the hierarchy honest: `audit` and `add-subtasks`

The Epic -> Story -> Subtask rules are in [docs/JIRA_HIERARCHY.md](docs/JIRA_HIERARCHY.md). Two commands
keep a project's *existing* cards true to them (`decompose` / `sync-decomposition` only help for an Epic you
have not built yet):

```bash
symphony-workflow audit ADLC --open --assignee-me     # Stories with no Subtasks, Stories under no Epic, Epics with no Stories, Done Stories with open Subtasks, unassigned cards; exit 1 on any error
symphony-workflow add-subtasks plan.json --dry-run    # {"ADLC-12": ["Add X", "Tests: unit"], ...}; idempotent; a Done Story's new Subtasks go Done too
```

Also the `audit_hierarchy` and `add_subtasks` tools. Read-only `audit`; `add-subtasks` writes and has `--dry-run`.

### Rolling a finished Subtask up the tree

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

### Tracker backends

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

#### Recommended Linear structure: mapping Jira's Project → Epic → Story → Task

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

### Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync --extra dev
uv run symphony-workflow init-env   # creates .env (gitignored) with empty keys
uv run symphony-workflow check-env --verify   # after you filled JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
uv run symphony-workflow list-tools
```

Get a Jira API token: Atlassian account settings → Security → API tokens.

### Testing

```bash
uv run pytest              # all HTTP calls mocked via respx — no live Jira access, no network
uv run pytest --cov=symphony_workflow
uv run ruff check .
```

### Releasing

Bump `version` in `pyproject.toml`, `plugin/.claude-plugin/plugin.json` and
`.claude-plugin/marketplace.json` (and the package's line in `uv.lock`) to the same new value, add a
`## <version>` entry to `CHANGELOG.md`, and land them in one PR (this repo's existing pattern — see any
past release commit). Once that PR is merged to `main`:

```bash
scripts/release.sh <version>   # e.g. scripts/release.sh 0.5.0
git push origin v<version>     # the script tags locally but never pushes itself
```

`scripts/release.sh` exists because `v0.3.0`/`v0.4.0`'s CHANGELOG entries said "tagged" but
`git tag -l` disagreed — a manual multi-step process silently dropped the tag step, twice. The
script verifies (not bumps) `pyproject.toml`/`plugin.json`/`marketplace.json`/`CHANGELOG.md` are already consistent
for `<version>`, runs `tests/test_version.py`, and only then creates the annotated tag — it exits
non-zero, tagging nothing, if any check fails or `v<version>` already exists.

### Project layout

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

### Adding a new tool

1. Add a pydantic args model to `tools.py`.
2. Add the client method to `client.py` if it doesn't exist yet (with a test in
   `test_client.py`).
3. Add a `_handler` function and register a `Tool(...)` entry in `TOOLS`.
4. Add coverage in `test_tools.py` (dispatch) and, if it deserves a human-friendly subcommand,
   `cli.py` + `test_cli.py`.

See [`CLAUDE.md`](CLAUDE.md) for the fuller set of conventions this repo follows.

### Adding a new tracker backend

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
