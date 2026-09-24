# CLAUDE.md

Guidance for Claude Code (claude.ai/code) working in this repository.

## Project Overview

**jira-agent-cli** — a Python CLI for the Jira Cloud REST API where every operation is also
exposed as a schema-described tool call, so an AI agent can invoke it by name the same way it
would call a function. See [README.md](README.md) for the full pitch, background, and
confirmed CF1 field IDs.

This is a standalone package under `~/Documents/git_saas/`, a private repo in the `cloud-f1`
GitHub org. It is not part of `content-asset-system` — don't assume its rules apply here beyond
what this file states.

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

## Architecture

```
src/jira_agent_cli/
  config.py   # JiraConfig.from_env() — required: JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
              # optional: JIRA_FIELD_EPIC_LINK / JIRA_FIELD_SPRINT / JIRA_FIELD_STORY_POINTS
  models.py   # pydantic response models (Issue, SearchResult, IssueCreateResult, Transition)
  client.py   # JiraClient — typed HTTP wrapper over Jira REST API v2, raises JiraError
  tools.py    # Tool dataclass + TOOLS registry + call_tool() — the agent-facing surface
  cli.py      # Typer app — human subcommands (get/search/comment/transition) + list-tools/call
tests/        # pytest + respx, one file per module (test_client / test_tools / test_cli)
```

Data flows one direction: `cli.py` → `tools.py` → `client.py` → Jira REST API v2. Never call
`httpx` directly from `cli.py` or `tools.py` — go through `JiraClient`.

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

- Don't add a web server, database, or queue — this is a CLI, not a service. If a long-running
  daemon that polls Jira and dispatches agents is ever wanted, that's a different project (see
  [openai/symphony](https://github.com/openai/symphony) for that shape of problem — this repo
  intentionally stays at "one command, one Jira call").
- Don't hardcode `cloud-f1.atlassian.net` or CF1's custom field IDs into `client.py` — they
  belong in `.env` / `JiraConfig`, because this client is meant to work against any Jira Cloud
  project, not just CF1.
- Don't reach for the v3 API / ADF format without a documented reason — it changes every model
  and client method to be structured instead of plain text.
