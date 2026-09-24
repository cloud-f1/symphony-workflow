---
jira_url: https://cloud-f1.atlassian.net/browse/ADLC-28
---

Still running — waiting for the notification rather than polling further.
There's real structured-output support in the installed SDK. Let me pull the exact context around these fields.
Good, confirmed. `ClaudeAgentOptions.output_format` (dict, `{"type": "json_schema", "schema": {...}}`) and `ResultMessage.structured_output` exist. Now let's check the CLI-side extraction code and then look at runs.py / orchestrator.py / docs / tests.
Now let's look at orchestrator.py, docs, and tests.
No existing mention of ADLC-28 or structured output plans in docs. No existing string-parsing of plan output today — it's used as raw text (`result.text`) directly. Now check tests.
I have everything needed. Here's the report.

## Findings for ADLC-28: Structured plan output via `output_config`/`output_format`

**1. `src/jira_agent_cli/agent_runner.py`**
- Calls `claude_agent_sdk.query(prompt=prompt, options=options)` (lines 61-98), builds `ClaudeAgentOptions` with `cwd`, `model`, `permission_mode`, `max_turns`, `max_budget_usd`, plus `resume` for Implement. No `output_format` set today.
- `_consume()` (lines 37-58) only reads `AssistantMessage.content` `TextBlock`s (joined into `text`) and `ResultMessage.{session_id, total_cost_usd, num_turns, is_error/result}`. It never reads `ResultMessage.structured_output`.
- `AgentRunResult` dataclass (lines 29-34) has only `text, session_id, cost_usd, num_turns` — no field for structured data yet.
- Installed SDK (`claude-agent-sdk==0.2.159`, confirmed via `.venv/lib/python3.11/site-packages/claude_agent_sdk`, matches `pyproject.toml`'s `>=0.2.159` pin) **does support this**:
  - `ClaudeAgentOptions.output_format: dict[str, Any] | None` (types.py:2364-2369): `{"type": "json_schema", "schema": {...}}`, "matches the Messages API structure."
  - `ResultMessage.structured_output: Any = None` (types.py:1353), populated from `data.get("structured_output")` in `_internal/message_parser.py:322`.
  - CLI-arg wiring in `_internal/transport/subprocess_cli.py:776-786`: when `output_format.type == "json_schema"`, it does `cmd.extend(["--json-schema", json.dumps(schema)])`.
  - Note the naming: it's `output_format` on `ClaudeAgentOptions`, not `output_config` (the ticket title's term) — `output_config` only appears in docstrings for the unrelated `task_budget` beta feature (types.py:2412) and in a `types.py:93` docstring, not as an actual options field. Worth flagging in the plan since the ticket title may need reconciling with actual SDK naming.

**2. `src/jira_agent_cli/prompts.py`**
- `PLAN_PROMPT_TEMPLATE` (lines 11-21) is pure free text: asks for "a short, concrete implementation plan... what files would change, what the approach is, and any risks or open questions... End with a one-paragraph summary suitable for posting as a Jira comment." No structured sections requested today; `render_plan_prompt()` (29-35) just does `str.format()` with `key/summary/status/description`.

**3. `runs.py` / `orchestrator.py`**
- No string-parsing exists today — `result.text` (raw joined text) is used verbatim everywhere: written to `docs/runs/<key>/plan.md` via `render_run_doc()` (orchestrator.py:33-42, called at line ~78), posted as-is to `client.add_comment(run.issue_key, f"[jira-agent-cli plan]\n\n{result.text}")` (~line 80), and stored on `Run.plan` via `runs.set_plan_ready(run.id, plan=result.text, session_id=..., workspace_path=...)` (runs.py:71-79).
- `Run` model (runs.py:23-31) fields: `id, issue_key, state, created_at, plan: str | None, session_id, workspace_path, result, error`. `plan` is currently typed `str | None` — would need to become/gain a structured field (e.g. `plan_structured: dict | None` or typed submodel) to carry `files_changed`/`risks`/`summary`.
- `execute_plan()` (orchestrator.py, ~lines 50-92) is the integration point: after `run_plan()` returns, it builds the plan.md and Jira comment straight from `result.text`. This is where structured fields would get formatted into both outputs instead of raw text passthrough.

**4. docs/**
- No mention of "structured output", "output_config", "output_format", or "ADLC-28" anywhere in `docs/spec.md`, `docs/plan.md`, `docs/intent.md`, or `docs/WORKFLOW.md` — this is a net-new feature, no existing stub.
- `CLAUDE.md` lines 37-49 ("Claude Agent SDK usage") documents the two settled SDK decisions (`permission_mode="plan"`, `resume`) and says "don't re-litigate without updating docs/spec.md first" — the plan should update this section too if `output_format` becomes a third settled decision.
- `docs/plan.md` Phase 2 (`ADLC-6`) is the original free-text Plan-phase implementation; ADLC-28 is a follow-on refinement, not yet reflected in the phase list.

**5. tests/**
- `tests/test_agent_runner.py`: `_fake_stream()` helper (lines 18-38) builds `AssistantMessage`/`ResultMessage` fakes and monkeypatches `agent_runner.query`; would need a variant/param to also set `structured_output` on the fake `ResultMessage`, plus new assertions on `AgentRunResult`'s new field(s) and that `options.output_format` gets passed to `query`.
- `tests/test_orchestrator.py`: currently only tests `render_run_doc()`; would need new tests for how structured fields flow into `plan.md` and the Jira comment.
- `tests/test_api.py` and `tests/test_poller.py`: both monkeypatch `orchestrator_module.run_plan` / `agent_runner.run_plan` returning `AgentRunResult(text=..., session_id=..., cost_usd=..., num_turns=...)` (e.g. test_api.py:212-217,300-301,350-351; test_poller.py:89-90,123-126,162-165) — every one of these call sites constructs `AgentRunResult` positionally/by-kwarg without a structured field, so adding a new field must be optional/default-valued or all these fakes break.
The plan file is written to `/Users/cloud-f1/.claude/plans/you-are-looking-at-wobbly-pinwheel.md`. Since this is an unattended orchestration session (no interactive user to approve via `ExitPlanMode`, which isn't available here), here is the plan as the deliverable:

## ADLC-28: Structured plan output via Claude Agent SDK `output_format`

### Context

Today the Plan phase (`agent_runner.run_plan()` → `orchestrator.execute_plan()`) treats the model's entire response as one opaque string: `AgentRunResult.text` gets written verbatim into `docs/runs/<key>/plan.md`, posted verbatim as a Jira comment, and stored verbatim on `Run.plan`. There is no structured data — `files_changed`, `risks`, and `summary` all live inside free text that would have to be re-parsed. ADLC-28 proposes using Claude's structured-outputs feature so the Plan phase returns typed fields instead.

The ticket's open question — does `ClaudeAgentOptions` expose `output_config` the same way the raw Messages API does — is now answered: **yes, but the field is named `output_format`, not `output_config`** (confirmed against the installed `claude-agent-sdk==0.2.159`, matching the `pyproject.toml` pin):
- `ClaudeAgentOptions.output_format: dict[str, Any] | None` — pass `{"type": "json_schema", "schema": {...}}`.
- The subprocess transport turns this into a `--json-schema` CLI flag (`_internal/transport/subprocess_cli.py`).
- The result lands on `ResultMessage.structured_output: Any = None` (`_internal/message_parser.py`).
- `agent_runner.py`'s `_consume()` currently only reads `TextBlock`s and a few `ResultMessage` fields (`session_id`, `total_cost_usd`, `num_turns`, `is_error`/`result`) — it never reads `structured_output` today, so wiring this up is additive.

This ticket is explicitly a spike ("propose small before committing to a full design"), so the plan below is scoped to that, not a full production rollout.

### Approach

**Step 1 — Spike:** prove `output_format` works with `permission_mode="plan"` before touching the data model. Run a throwaway script (not a repo change) calling `claude_agent_sdk.query()` with `permission_mode="plan"` and `output_format` set to a minimal schema, against a `PLAN_PROMPT_TEMPLATE`-like prompt. This resolves the real unknown: whether plan-mode's exploratory multi-turn tool use still ends in a `structured_output` matching the schema, or whether forcing a schema degrades the free-form reasoning quality seen on ADLC-17/ADLC-24.

**Step 2 — If confirmed, land the minimal integration:**
1. **`prompts.py`** — add `PLAN_OUTPUT_SCHEMA` (JSON schema: `summary: str`, `files_changed: list[str]`, `risks: list[str]`) alongside `PLAN_PROMPT_TEMPLATE`.
2. **`agent_runner.py`** — `run_plan()` passes `output_format={"type": "json_schema", "schema": PLAN_OUTPUT_SCHEMA}` (Implement phase stays free-text). `_consume()` captures `ResultMessage.structured_output`. `AgentRunResult` gains `structured: dict | None = None` (defaults to `None` so existing `AgentRunResult(...)` call sites in `test_api.py`/`test_poller.py` don't break).
3. **`runs.py`** — `Run` gains `plan_structured: dict | None = None`, set in `set_plan_ready()` alongside the existing `plan: str`. Text stays as fallback when structured output is absent.
4. **`orchestrator.py`** (`execute_plan()`, `render_run_doc()`) — render `plan.md`/the Jira comment from typed fields when `plan_structured` is present; fall back to today's verbatim-text behavior otherwise. This is the actual "more reliable than string-parsing" payoff.
5. **Docs** — update `CLAUDE.md`'s "Claude Agent SDK usage" section and `docs/spec.md` per its own instruction not to re-litigate SDK decisions without updating that doc first.

**Tests:** extend `tests/test_agent_runner.py`'s `_fake_stream()` to optionally set `structured_output`; assert `output_format` is passed for `run_plan()` only; add `tests/test_runs.py` coverage for `plan_structured`; add `tests/test_orchestrator.py` cases for structured-rendered `plan.md`/comment plus the existing free-text fallback case. `test_api.py`/`test_poller.py` need no changes since the new field defaults to `None`.

### Risks / open questions

- **Naming mismatch**: ticket says `output_config.format`; actual field is `output_format`. Worth closing the loop on the ticket text.
- **Plan-mode + structured-output interaction is unverified** — exactly what Step 1's spike resolves.
- **Schema rigidity vs. reasoning quality** — kept deliberately minimal (3 fields); expanding it should wait for spike findings.
- **SDK version coupling** — `output_format`'s CLI translation is SDK-internal plumbing; worth a code comment pinning the verified version.
- **Backward compatibility** — handled via `None`-defaulted new fields, confirmed necessary because of existing `AgentRunResult(...)` construction sites in tests.

### Verification

1. `uv run pytest tests/test_agent_runner.py tests/test_runs.py tests/test_orchestrator.py -v`
2. `uv run pytest` (full suite, confirms no breakage in `test_api.py`/`test_poller.py`)
3. `uv run ruff check . && uv run ruff format --check .`
4. Manual spike run (Step 1) inspecting `ResultMessage.structured_output` against a real plan-mode session, before committing to Step 2's schema.

---

**Jira comment summary:** Investigated ADLC-28: the installed `claude-agent-sdk==0.2.159` does support structured plan output via `ClaudeAgentOptions.output_format={"type": "json_schema", "schema": {...}}` (not `output_config` as the ticket title assumed) — results land on `ResultMessage.structured_output`, which `agent_runner.py`'s `_consume()` doesn't currently read. Proposed approach: (1) a small throwaway spike first to verify `output_format` and `permission_mode="plan"`'s exploratory tool use don't fight each other — the real open question, not SDK availability; (2) if that holds up, a minimal, backward-compatible integration — a 3-field schema (`summary`/`files_changed`/`risks`) in `prompts.py`, an optional `structured` field on `AgentRunResult` and `plan_structured` on `Run` (both default `None` so existing test fakes don't break), and `orchestrator.py` rendering `plan.md`/the Jira comment from typed fields when present, falling back to today's verbatim-text behavior otherwise. Main risk is the reasoning-quality tradeoff the ticket already flags (rigid schema vs. free-form plans like ADLC-17/ADLC-24's) — recommend not committing to the full schema design until the spike confirms plan-mode quality holds up under a forced schema.