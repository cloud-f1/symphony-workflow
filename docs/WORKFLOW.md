# WORKFLOW — symphony-workflow POC prompt policy

Symphony-style, deliberately simplified (see [`plan.md`](plan.md)): a single
policy file for the two phases, no tracker/polling config (symphony-workflow's
own config lives in `.env`, not here). Rendered by `src/symphony_workflow/prompts.py`.

## Plan phase

- **`permission_mode="plan"`** — the agent can read and explore but cannot edit
  files or run side-effecting commands (see `docs/plans/0001-symphony-poc/spec.md`).
- Input: the issue's `key`, `summary`, `status`, `description`.
- Output: a short plan (no code), posted as a Jira comment + saved to
  `docs/runs/<key>/plan.md`. The run's `session_id` is recorded so Implement
  can resume the same thread.
- The agent is told explicitly: this is unattended — don't ask a human to do
  something mid-turn, just note it in the plan and stop.

## Implement phase

- **`permission_mode="acceptEdits"`**, resumes the Plan phase's `session_id`.
- Input: nothing new — the resumed session already has the plan and the
  ticket context from Plan's turn.
- Output: file changes + tests. Explicitly told **not** to open a PR or push —
  a human reviews the diff next (this POC stops there; see `plan.md` Phase 4).

## Exact prompt text

See `src/symphony_workflow/prompts.py` — `PLAN_PROMPT_TEMPLATE` /
`IMPLEMENT_PROMPT_TEMPLATE`. Kept in code rather than parsed out of this file
for the POC (no template-loader indirection); this doc is the read-first
policy summary, the module is the source of truth for exact wording.
