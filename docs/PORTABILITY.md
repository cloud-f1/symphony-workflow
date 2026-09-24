# Portability — using jira-agent-cli from another repo

jira-agent-cli started as a self-contained POC (plan/implement against its own
codebase, driven by its own `ADLC` Jira project). Two things were hardcoded to that
self-referential setup; both are now environment-configurable, so this can run as
**one central orchestrator service another repo points at itself**, rather than
something copied into every repo — closer to how Symphony itself is meant to run
(one binary, configured per target via its own policy file).

## What changed

| Before | Now | Env var |
|---|---|---|
| `orchestrator.py`'s `REPO_ROOT` doubled as both "where jira-agent-cli's source lives" and "the repo Plan/Implement work against" | Split into `REPO_ROOT` (unchanged meaning) and `TARGET_REPO` (the actual work target) | `JIRA_AGENT_CLI_TARGET_REPO` |
| `poller.py`'s `READY_JQL` hardcoded `project = ADLC` | Configurable | `JIRA_AGENT_CLI_JIRA_PROJECT` |

Both default to the original self-referential values, so nothing about this POC's
own setup changes unless you explicitly override them.

## Adopting it for another repo

1. Get jira-agent-cli onto the machine running the other repo's work — clone it, or
   `uv add git+https://github.com/cloud-f1/jira-agent-cli` as a dev dependency.
2. Create a Jira project for that repo's agent-driven work (same reasoning as
   `ADLC`: keep it separate from a general content/marketing backlog — see
   `docs/intent.md`'s Tracking section for why). Set:
   ```
   JIRA_AGENT_CLI_JIRA_PROJECT=<that project's key>
   ```
3. Point Plan/Implement at the actual codebase:
   ```
   JIRA_AGENT_CLI_TARGET_REPO=/path/to/the/other/repo
   ```
4. Give it its own workspace root, outside the target repo's own worktree
   conventions if it has any:
   ```
   JIRA_AGENT_CLI_WORKSPACE_ROOT=/path/to/the/other/repo/.worktrees
   ```
5. Usual Jira credentials (`JIRA_BASE_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`) — same as
   this repo's own `.env.example`.
6. Run it: `uv run uvicorn jira_agent_cli.api:app`.

That's the whole adoption path — no code fork needed for a different target repo or
Jira project.

## What is NOT generalized yet (deferred, not silently skipped)

The Plan/Implement **prompt templates** (`prompts.py`) are still jira-agent-cli's
own baked-in text — they don't know anything about a different repo's conventions
(its own `CLAUDE.md`, its own test framework, its own branch rules). Symphony's
actual design solves this with a per-repo `WORKFLOW.md` the orchestrator reads at
runtime; jira-agent-cli doesn't do that yet. For a repo whose conventions differ a
lot from this one, that's a real gap — the plan an agent writes will be shaped by
jira-agent-cli's own house style, not the target repo's.

**Future phase, if this gets adopted for real**: have `orchestrator.py` look for a
`WORKFLOW.md` in `TARGET_REPO` and use it instead of `prompts.py`'s hardcoded
templates when present, falling back to the current behavior when it's absent.
Not built now because nothing has actually needed it yet — build it when a second
repo's differences from this one's conventions make it worth the cost.

## Also worth knowing before adopting this elsewhere

- `RunStore` is in-memory only (see `docs/spec.md`) — every adopting instance
  inherits that limitation, not just this POC's.
- Branch names are `{ISSUE-KEY}-{slug}` (see `CLAUDE.md`'s Branch naming rule) —
  this only gets you Jira auto-linking if the target GitHub org/repo has the
  GitHub-for-Jira app installed and connected. Confirmed on `cloud-f1.atlassian.net`
  as of 2026-09-25 that it is **not** currently connected — `GET
  /rest/dev-status/1.0/issue/summary` returns `byInstanceType: {}` for every data
  type, not just branches. The naming convention is correct and necessary but not
  sufficient by itself; check the target Jira site's app connections too.
