# Portability — using symphony-workflow from another repo

symphony-workflow started as a self-contained POC (plan/implement against its own
codebase, driven by its own `ADLC` Jira project). Two things were hardcoded to that
self-referential setup; both are now environment-configurable, so this can run as
**one central orchestrator service another repo points at itself**, rather than
something copied into every repo — closer to how Symphony itself is meant to run
(one binary, configured per target via its own policy file).

This document is written in terms of Jira (`JIRA_PROJECT`, `poller.py`'s JQL) since that's
still the default and most-adopted backend. Everything below applies equally to a
Linear-backed instance (`SYMPHONY_WORKFLOW_TRACKER=linear`, `ADLC-57`) with `SYMPHONY_WORKFLOW_JIRA_PROJECT`
replaced by `SYMPHONY_WORKFLOW_LINEAR_TEAM` — see
[docs/plans/0005-linear-tracker-backend/intent.md](plans/0005-linear-tracker-backend/intent.md).
The one thing that does **not** carry over: the Claude Code plugin's `session-start-check-api.sh`
hook and the CLI's `search`/`call`/`decompose`/`sync-decomposition` commands stay Jira-only
regardless of this doc's guidance.

## What changed

| Before | Now | Env var |
|---|---|---|
| `orchestrator.py`'s `REPO_ROOT` doubled as both "where symphony-workflow's source lives" and "the repo Plan/Implement work against" | Split into `REPO_ROOT` (unchanged meaning) and `TARGET_REPO` (the actual work target) | `SYMPHONY_WORKFLOW_TARGET_REPO` |
| `poller.py`'s `READY_JQL` hardcoded `project = ADLC` | Configurable | `SYMPHONY_WORKFLOW_JIRA_PROJECT` |

Both default to the original self-referential values, so nothing about this POC's
own setup changes unless you explicitly override them.

## Adopting it for another repo

1. Get symphony-workflow onto the machine running the other repo's work — clone it, or
   `uv add git+https://github.com/cloud-f1/symphony-workflow` as a dev dependency.
2. Create a Jira project for that repo's agent-driven work (same reasoning as
   `ADLC`: keep it separate from a general content/marketing backlog — see
   `docs/plans/0001-symphony-poc/intent.md`'s Tracking section for why). Set:
   ```
   SYMPHONY_WORKFLOW_JIRA_PROJECT=<that project's key>
   ```
3. Point Plan/Implement at the actual codebase:
   ```
   SYMPHONY_WORKFLOW_TARGET_REPO=/path/to/the/other/repo
   ```
4. Give it its own workspace root, outside the target repo's own worktree
   conventions if it has any:
   ```
   SYMPHONY_WORKFLOW_WORKSPACE_ROOT=/path/to/the/other/repo/.worktrees
   ```
5. Usual Jira credentials (`JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`) — same as
   this repo's own `.env.example`.
6. Run it: `uv run uvicorn symphony_workflow.api:app`.

That's the whole adoption path — no code fork needed for a different target repo or
Jira project.

## Multi-repo policy (decided 2026-09-24, don't re-litigate without updating this section)

As more repos adopt this, two questions come up. Both are settled — the reasoning
is in `docs/context/session-summary.md`'s handoff notes if you want the full
back-and-forth.

**1. Dedicated Jira project per repo, not Epic/tag inside one shared project.**
`poller.py`'s `READY_JQL` already hardcodes single-project polling
(`project = {JIRA_PROJECT} AND status = "To Do" ...`) and `orchestrator.py` takes
one `TARGET_REPO` per process — there's no per-issue repo routing anywhere.
Project-per-repo costs nothing (it's what already exists); routing by
Epic/label inside one shared project would need real new engineering (JQL by
label instead of project, per-issue repo resolution) and overloads "Epic" —
this repo already uses Epic to mean *phase of work*, not *which repo*.

**2. One symphony-workflow instance per repo (own port, own `.env`), not one shared
multi-tenant poller.** `WorkspaceManager` validates one workspace root per
instance, `RunStore` is in-memory per-process, `TARGET_REPO`/`JIRA_PROJECT` are
both single env vars. Running N repos today already means N processes — that's
this doc's own adoption path, not a limitation to fix. A shared multi-tenant
poller would need the poller to sweep multiple JQLs and route each issue to the
right `TARGET_REPO`/workspace — real rearchitecture, and it raises blast radius
(one shared loop's bug now affects every repo at once). Don't build it
preemptively; revisit only if running several small processes becomes the
actual operational pain.

**Instance registry** (repo → Jira project → port → poller):

| Repo | Jira project | Port | Poller enabled? |
|---|---|---|---|
| `symphony-workflow` (this repo, self-referential) | `ADLC` | 8811 | Yes — the owning instance for `ADLC` |
| `ai-coding-nexjs-template` | `NXJS` (dedicated, `ADLC-40`, Done — team-managed Kanban, matching `ADLC`'s own style) | 8812 (planned) | Yes, once `ADLC-41` stands up the instance |
| `course-checkin` | `CKIN` (dedicated, team-managed Kanban) | **8813 — actually running** (confirmed live 2026-09-29, `.env.course-checkin`) | No — `SYMPHONY_WORKFLOW_POLL_ENABLED=false`; Jira sync done manually per-epic via the tool-call API, not the poller |
| `ai-rc-engineer-pm` | `RCPM` (dedicated, created 2026-09-29 via this doc's playbook — team-managed Kanban `gh-simplified-agility-kanban`, matching `ADLC`/`NXJS`'s style; `RCPM-1` Epic = Phase 79 with 6 Stories E328–E333, all `Done`, PR remote-links attached) | ~~8813~~ **reassign before starting — collides with `course-checkin`'s real instance above, this doc's own table had both claim it** (planned, not yet started) | Not yet started — `.env.ai-rc-engineer-pm` has `SYMPHONY_WORKFLOW_POLL_ENABLED=false`; flip once someone deliberately runs the server for this repo, on a free port |
| `ai-workmate-salon` | `SALON` (dedicated, created 2026-09-29 — team-managed Kanban `gh-simplified-agility-kanban`, matching `CKIN`'s style; verified `simplified: true` / `style: next-gen` before use, per this doc's own template-key trap warning) | 8814 — actually running (confirmed live 2026-09-29, `.env.ai-workmate-salon`) | No — same manual-sync posture as `course-checkin` |

### Sharing one Jira project across repos (interim/exception case)

The default is a dedicated project per repo — `ADLC-40` did this for
`ai-coding-nexjs-template` (`NXJS`, created via `POST /rest/api/3/project`; see `CLAUDE.md`'s
"Jira API gotchas" for the template-key trap hit along the way). For a *new* repo before its own
dedicated project exists, a portability test may temporarily reuse `ADLC` (or any existing
project) as `SYMPHONY_WORKFLOW_JIRA_PROJECT` for a *second* instance pointed at a different
`TARGET_REPO`. That's safe only with both of these, because exactly one poller may ever run
against a given Jira project:

1. **The non-owning instance must start with `SYMPHONY_WORKFLOW_POLL_ENABLED=false`**
   (confirmed real, `api.py:43`, `os.environ.get("SYMPHONY_WORKFLOW_POLL_ENABLED",
   "true")`). Without this, its poller auto-discovers *any* "To Do" ticket in
   the shared project — including the owning repo's own real backlog — and
   wrongly Plans it against the wrong `TARGET_REPO`. Drive it only through the
   manual flow: `POST /runs {issue_key}` → `POST /runs/{id}/plan`.
2. **Any tracking ticket created for the non-owning repo's test should be a
   Subtask.** `poller.py`'s `READY_JQL` already excludes
   `issuetype not in (Epic, Subtask)` — a Subtask is invisible to the owning
   instance's poller by construction, so the owning instance (which keeps
   running, poller on) never picks it up and tries to Plan it against its own
   `TARGET_REPO` by mistake. Zero code change; this is an existing exclusion,
   repurposed.

This interim arrangement should be short-lived per repo — once a dedicated
project exists, point that repo's instance at it and this workaround no longer
applies to it.

## What is NOT generalized yet (deferred, not silently skipped)

The Plan/Implement **prompt templates** (`prompts.py`) are still symphony-workflow's
own baked-in text — they don't know anything about a different repo's conventions
(its own `CLAUDE.md`, its own test framework, its own branch rules). Symphony's
actual design solves this with a per-repo `WORKFLOW.md` the orchestrator reads at
runtime; symphony-workflow doesn't do that yet. For a repo whose conventions differ a
lot from this one, that's a real gap — the plan an agent writes will be shaped by
symphony-workflow's own house style, not the target repo's.

**Future phase, if this gets adopted for real**: have `orchestrator.py` look for a
`WORKFLOW.md` in `TARGET_REPO` and use it instead of `prompts.py`'s hardcoded
templates when present, falling back to the current behavior when it's absent.
Not built now because nothing has actually needed it yet — build it when a second
repo's differences from this one's conventions make it worth the cost.

## Also worth knowing before adopting this elsewhere

- `RunStore` is in-memory only (see `docs/plans/0001-symphony-poc/spec.md`) — every adopting instance
  inherits that limitation, not just this POC's.
- Branch names are `{ISSUE-KEY}-{slug}` (see `CLAUDE.md`'s Branch naming rule) —
  this only gets you Jira auto-linking if the target GitHub org/repo has the
  GitHub-for-Jira app installed and connected. On `cloud-f1.atlassian.net` this
  is now true as of 2026-09-24 (`ADLC-30`, Done) — `repository.byInstanceType`
  is populated where it used to be `{}` for every data type. **Branch-level
  linking specifically is still unverified**: nothing has pushed a
  correctly-named branch to GitHub since the app connected, so
  `branch.byInstanceType` hasn't been exercised end-to-end. On a different
  Jira site, check its app connection the same way before assuming a
  correctly-named branch alone is enough.

## Claude Code plugin (skill + hook only)

`plugin/` at this repo's root packages the `symphony-workflow-ops` skill and the
`SessionStart` API-health-check hook as an installable [Claude Code
plugin](https://code.claude.com/docs/en/plugins-reference), so an adopter gets
both auto-wired into any Claude Code session without hand-editing
`.claude/settings.json` or copying skill files around. `.claude-plugin/marketplace.json`
at the repo root makes this repo itself a (private, single-plugin) marketplace.

### What the plugin gives you

- **The `symphony-workflow-ops` skill**, namespaced `/symphony-workflow-ops:symphony-workflow-ops`
  once installed — the same operational reference as this repo's own
  `.claude/skills/symphony-workflow-ops/SKILL.md` (kept in sync by hand; there's no
  build step that generates one from the other, so a change to one needs the
  same edit in the other until that's automated).
- **The `SessionStart` hook** (`plugin/hooks/session-start-check-api.sh`,
  wired via `plugin/hooks/hooks.json`) — checks whether the symphony-workflow API
  server is already running on `:8811` and auto-starts it if not, using
  `${CLAUDE_PROJECT_DIR}` (not `${CLAUDE_PLUGIN_ROOT}`, which is the plugin's
  own install directory) to find the symphony-workflow checkout. Because a
  plugin's hooks fire on every Claude Code session it's enabled for — not just
  sessions opened in this repo — the script first checks that
  `$CLAUDE_PROJECT_DIR/pyproject.toml` actually names `symphony-workflow` and
  exits silently otherwise, so it's safe to enable globally.

### What the plugin does NOT give you

- **The CLI, the FastAPI server, the poller, or the orchestrator.** Claude Code
  plugins cannot install Python dependencies (no `uv sync`/`pip install` hook
  in the plugin lifecycle) — the actual `symphony_workflow` package stays a normal
  git-cloned dependency. Installing the plugin without also cloning this repo
  and running `uv sync --extra dev` gets you a skill that describes a server
  that isn't there and a hook that will (silently) never find anything to
  start.
- Anything from the rest of this document — `TARGET_REPO`/`JIRA_PROJECT`
  configuration, credentials, the prompt templates — none of that is
  plugin-packaged; it's still `.env` and a real checkout.

### Installing it

```
# 1. Clone and set up the actual CLI/server (still required, plugin or not):
git clone https://github.com/cloud-f1/symphony-workflow
cd symphony-workflow && uv sync --extra dev && cp .env.example .env   # fill in Jira creds

# 2. Add this repo as a plugin marketplace, then install from it, from inside
#    a Claude Code session (either a private GitHub remote or a local path
#    both work — the marketplace add supports either):
/plugin marketplace add cloud-f1/symphony-workflow
/plugin install symphony-workflow-ops
# or, working from a local clone instead of GitHub:
/plugin marketplace add /path/to/symphony-workflow
/plugin install symphony-workflow-ops
```

### Not verified live

An isolated worktree with no interactive Claude Code session can't run
`/plugin marketplace add` / `/plugin install` itself — that round trip
(marketplace resolution, plugin install, skill namespacing showing up as
`/symphony-workflow-ops:symphony-workflow-ops`, the hook actually firing on a real
`SessionStart`) is a documented follow-up to verify by hand in an interactive
session. What *is* verified: `plugin/.claude-plugin/plugin.json`,
`.claude-plugin/marketplace.json`, and `plugin/hooks/hooks.json` are all valid
JSON (`jq -e .`) with the fields the schema requires, and
`plugin/hooks/session-start-check-api.sh` passes `bash -n`.
