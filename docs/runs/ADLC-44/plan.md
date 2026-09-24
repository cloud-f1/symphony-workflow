---
jira_url: https://cloud-f1.atlassian.net/browse/ADLC-44
---

## Implementation Plan — ADLC-44

### Blocking precondition found during research
This worktree's branch (`9717d06`) is an **ancestor of `main`**, not a descendant. Two things the ticket's "Approach" section assumes already exist are **missing here**:
- ADLC-28's structured-output plumbing (`ClaudeAgentOptions.output_format`, `PLAN_OUTPUT_SCHEMA`, `AgentRunResult.structured`) — on `main` at `1bf8c3f`, absent in this worktree.
- The `parent=` param on `client.create_issue`/`tools.CreateIssueArgs` for team-managed Story→Epic / Subtask→Story linking — on `main` at `38b94b1`, absent here.

Merge/rebase `main` into this branch **before** starting Part 2, or that work will duplicate/conflict with what already landed upstream.

### Part 1 — CLI-native `plan` / `implement`
**Files:** `cli.py`, `runs.py`, `tests/test_cli.py`, `tests/test_runs.py`

- `Run` in `runs.py` is already a pydantic `BaseModel`, so add a thin persistence shim, not a RunStore rewrite: `save_run_sidecar(run)` / `load_run_sidecar(issue_key)` writing `docs/runs/<key>/run.json` (same directory `execute_plan` already writes `plan.md` into — no collision), plus `RunStore.restore(run)` to reinsert a deserialized `Run`.
- `jira-agent plan <KEY>`: build `JiraClient` via the existing `_client()` helper, construct `RunStore()`/`WorkspaceManager()` locally (same as `poller.py`), `run = runs.create(key)`, `asyncio.run(execute_plan(run, runs=runs, client=client, workspaces=workspaces))`, then `save_run_sidecar(run)`.
- `jira-agent implement <KEY>`: `load_run_sidecar`, restore into a fresh `RunStore`, `asyncio.run(execute_implement(...))`, save again.
- All existing Typer commands are sync; this is the first place `asyncio.run(...)` gets introduced into `cli.py`.
- This sidecar is intentionally narrow — CLI-only handoff of `session_id`/`workspace_path` across two process invocations, not the general RunStore-persistence fix CLAUDE.md flags as real scope.

### Part 2 — `decompose` / `sync-decomposition`
**Files:** `prompts.py`, `models.py`, `agent_runner.py`, `orchestrator.py`, `cli.py`, tests

- `models.py`: add `Task`, `Story(title, user_story, acceptance_criteria[], tasks[])`, `DecompositionResult(stories[])` — plain pydantic, no `from_api` needed.
- `prompts.py`: add `DECOMPOSE_OUTPUT_SCHEMA` + `DECOMPOSE_PROMPT_TEMPLATE` + `render_decompose_prompt()`. **Definition of Done is deliberately not part of the LLM schema** — add a hand-authored `DEFAULT_DEFINITION_OF_DONE` constant sourced from CLAUDE.md's "three non-negotiables"/"Code style" sections, merged in by the orchestrator, not generated.
- `agent_runner.py`: reuse ADLC-28's structured-output plumbing after the merge (parameterize `run_plan` or add a thin `run_decompose`) rather than duplicating it.
- `orchestrator.py`: `execute_decompose(issue_key, *, client) -> DecompositionResult` — fetch issue, render prompt, run read-only (`permission_mode="plan"`), write `docs/runs/<key>/decomposition.md` (human-reviewable) + `decomposition.json` sidecar. Open question: likely doesn't need a `WorkspaceManager` worktree at all since it produces no code.
- CLI `decompose <KEY>` writes the files; `sync-decomposition <KEY>` reads the JSON and creates Story issues with `client.create_issue(..., issue_type="Story", parent=<KEY>)` then Subtasks with `parent=<created_story_key>` — the team-managed `parent=` field, not `epic=`/`customfield_10014`. Track `created_key` per story/task in the sidecar so a partial failure can resume without duplicate creation.
- Assumption to confirm: `<KEY>` is an existing Epic being decomposed into Stories underneath it.

### Risks / open questions
1. **Blocking:** sync branch with `main` before Part 2.
2. No file locking on the JSON sidecars — fine for single-operator POC use.
3. Does decompose need a git workspace at all — probably not.
4. Does `sync-decomposition` need an `--epic` override, or is `<KEY>` always the Epic.
5. Partial-failure/resume behavior in `sync-decomposition` needs explicit test coverage.
6. Out of scope unless clarified: should `plan`/`implement` auto-transition ticket status — neither `api.py` nor `poller.py` does this today, recommend leaving manual.
7. Update `docs/spec.md` (post-merge path: `docs/plans/0001-symphony-poc/spec.md`) to document both new CLI surfaces and the sidecar mechanism.

### Verification
`uv run pytest` (all HTTP mocked via `respx`, SDK calls monkeypatched, no live calls), `uv run ruff check . && uv run ruff format --check .`, plus a manual two-invocation smoke test (`jira-agent plan` then a separate `jira-agent implement`) to confirm the sidecar round-trips `session_id`, and a `jira-agent decompose ADLC-2` dry run to eyeball rendered markdown before ever running `sync-decomposition`.

---

**Jira comment summary:** This ticket has two parts — CLI-native `plan`/`implement` commands that call `orchestrator.py` in-process with Run state persisted to a `docs/runs/<key>/run.json` sidecar (a thin save/restore shim around the already-pydantic `Run` model, not a RunStore rewrite), and a `decompose`/`sync-decomposition` CLI pair that uses the Claude Agent SDK's structured-output mode to turn a ticket into an Epic→Story→Subtask backlog with LLM-generated per-story Acceptance Criteria and a hand-templated (not LLM-invented) Definition of Done sourced from CLAUDE.md's own standards, rendered to markdown for human review before `sync-decomposition` creates any real Jira issues using the team-managed `parent=` linking. The one hard blocker found during planning: this worktree's branch is stale relative to `main` and is missing both prerequisites the ticket assumes exist — ADLC-28's structured-output plumbing and the `parent=` field fix — so merging/rebasing `main` in first is required before any of the above can be built without conflicting with work that already landed upstream.