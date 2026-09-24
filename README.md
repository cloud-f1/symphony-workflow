# jira-agent-cli

A Python CLI for the Jira Cloud REST API where every operation is also exposed as a
schema-described **tool call** — so an AI agent (Claude Code, Codex, a cron job) can shell out
to it the same way it would call a function, instead of an engineer running it by hand.

```bash
jira-agent list-tools                                    # every tool's JSON schema
jira-agent call get_issue '{"key": "CF1-1"}'              # agent-style invocation
jira-agent get CF1-1                                      # same thing, human-friendly
jira-agent search 'project = CF1 AND status != Done'
jira-agent comment CF1-1 "Looks good, merging."
jira-agent transition CF1-1 Done
```

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

⚠️ This client has only been exercised against mocked HTTP responses (`respx`) in this repo's
test suite, never against a live Jira instance. The search endpoint in particular
(`/rest/api/2/search`) has seen churn across Atlassian's API versions — re-verify it against
current docs before depending on it in production.

## Setup

Requires [`uv`](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync --extra dev
cp .env.example .env   # fill in JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
uv run jira-agent list-tools
```

Get a Jira API token: Atlassian account settings → Security → API tokens.

## Testing

```bash
uv run pytest              # all HTTP calls mocked via respx — no live Jira access, no network
uv run pytest --cov=jira_agent_cli
uv run ruff check .
```

## Project layout

```
src/jira_agent_cli/
  config.py   # env-var config (JiraConfig, ConfigError)
  models.py   # pydantic response models (Issue, SearchResult, Transition, ...)
  client.py   # JiraClient — typed HTTP wrapper (JiraError on non-2xx)
  tools.py    # Tool registry + call_tool() dispatcher — the agent-facing surface
  cli.py      # Typer app: friendly subcommands + list-tools/call
tests/        # pytest + respx, one test file per module above
```

## Adding a new tool

1. Add a pydantic args model to `tools.py`.
2. Add the client method to `client.py` if it doesn't exist yet (with a test in
   `test_client.py`).
3. Add a `_handler` function and register a `Tool(...)` entry in `TOOLS`.
4. Add coverage in `test_tools.py` (dispatch) and, if it deserves a human-friendly subcommand,
   `cli.py` + `test_cli.py`.

See [`CLAUDE.md`](CLAUDE.md) for the fuller set of conventions this repo follows.
