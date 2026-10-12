# 0007 — Jira drift detection and re-sync: design

## What exists today (verified on 0.5.2)

- `JiraClient`: `get_issue`, `get_hierarchy_issue`, `list_children(parent_key)` (paged; returns key / summary / status / issue type),
  `search_issues`, `create_issue(... parent=...)`, `update_issue(key, *, summary=None, description=None)` (**only those two fields**),
  `add_comment`, `create_remote_link`, `list_transitions`, `transition_issue`. No delete, no re-parent.
- `decompose.py`: `Task{title, description}`, `Story{title, user_story, acceptance_criteria[], tasks[]}`,
  `Decomposition{epic_key, stories[], definition_of_done[]}`.
- `sync_decomposition.py`: `render_story_description(story, definition_of_done)` builds the Story description
  (plain text + `-` bullets — Jira wiki markup, see CLAUDE.md "Jira API gotchas"); `execute_sync_decomposition` creates Stories then
  Subtasks, optional `initial_status`; **no key bookkeeping, no idempotency**.
- `hierarchy.py` / `rollup.py` / `audit`: tree walking, structural rules, upward roll-up. Reuse their traversal; do not re-implement.
- Tools registry (`tools.py`) already exposes `get_issue`, `search_issues`, `create_issue`, `update_issue`, `add_comment`, `transition_issue`, `rollup_issue`, `create_remote_link`.

## Design decisions to make (recommendations in bold)

### 1. Identity: which Jira issue is which decomposition entry?
`decomposition.json` has titles only. Titles change; positions change. Options:

| Option | How | Verdict |
|---|---|---|
| A. state file | `docs/runs/<epic>/sync-state.json` mapping entry → key, written by `sync-decomposition` | needed for the key map; alone it breaks when the file is lost |
| B. match by title | exact summary among `list_children` | fragile on rename → duplicates |
| C. stable ids | optional `id` on `Story` and `Task` (e.g. `s1`, `s1.t2`), also stamped in the Jira description as a trailing marker line | robust; backward compatible if optional |

**Recommend C + A**: add optional `id` fields (generated and written back to `decomposition.json` the first time they are missing),
stamp a marker line (e.g. `sd-id: s1.t2`) at the end of each description, and keep `sync-state.json` as a cache of id → key. For
*adopting* an Epic that was created before ids existed, add `--adopt`: match existing children to entries by exact summary, then by order
within the parent, print the proposed mapping, and write ids/markers only after confirmation (`--yes`). Never guess silently.

### 2. What `diff-decomposition` reports (read-only, no writes anywhere except stdout)
Per Story and Subtask, one of:
`ok` · `missing` (in the spec, not in Jira) · `summary-changed` · `description-changed` · `orphan` (child in Jira, not in the spec — reported, never deleted) ·
`status-differs` (only when the spec carries an expected status; default is to not compare status) · `parent-differs` (child exists but under a different parent — report only).
Description comparison must normalize what Jira round-trips (line endings, trailing whitespace, wiki-markup escaping); pin this with a live check (see `implement.md`) before trusting it.
Output: a table by default, `--json` for machines. Exit codes: 0 no drift, 1 drift, 2 invalid decomposition / epic mismatch, 3 Jira error.
The Epic's own summary/description may be compared when the decomposition carries them (optional `epic_summary`, `epic_description`).

### 3. `sync-decomposition --update [--dry-run] [--status NAME]`
Applies, in order: create missing Stories → create missing Subtasks → `update_issue` for changed summary/description.
Never deletes; `orphan`s are listed and left. Newly created issues follow the existing `--status` semantics (poller safety: a Story left in "To Do" can be
auto-planned by a running poller — see `plugin/skills/symphony-workflow-ops/SKILL.md`). `--dry-run` prints the same plan with no writes.
Failures: Jira has no transaction; stop at the first error, print what was done, exit 3; re-running is safe because everything is keyed by id.
Without `--update` the command keeps today's behaviour (create-only; refuse if the Epic already has children unless `--adopt`/`--update` is given — decide whether
that refusal is a breaking change and document it).

### 4. Optional extras (separate Stories, only if cheap)
- Tool-registry entries (`diff_decomposition`) so non-CLI callers can use it.
- `--expect-status` per Story in the decomposition for consumers that want status drift flagged.
- Re-parenting support: `update_issue` cannot move a Subtask to another Story (a consumer had to call the REST `fields.parent` update directly after splitting an oversized Story; it works on team-managed projects, and a JQL child list lags by a few seconds afterwards — read `parent` back from the issue itself). Needed if the diff should be able to *fix* `parent-differs`.
- Transition the Epic to In Progress when its first child starts (today nothing does) — decide whether this belongs in `rollup` (downward) or stays a consumer concern.

### 5. Consumer adapter contract (documented here, implemented by each consumer)
A consumer converts its own spec into `docs/runs/<epic>/decomposition.json`. A consumer whose Epic is "a phase of several work items, each with
phases of tasks" maps: work item → `Story` (title = `<id> — <name>`, `user_story` = rationale, `acceptance_criteria` = per-phase checkpoints),
task bullet → `Task` (`title` = bullet, `description` = phase name). Keep the source-of-truth parsing in the consumer. The consumer's spec files must be
**persisted** (not a throw-away temp file) so a later session can re-run the diff.

## Process rules inherited from this repo (do not skip)
- ADLC Epic first, decomposed into Stories/Subtasks before building (`CLAUDE.md` hard rule; dogfood `decompose` → review → `sync-decomposition --status` as needed).
- Branch name starts with the ADLC key. Unit tests are required. `uv run pytest` and `uv run ruff check .` yourself before trusting any result.
- Never `claude -p`. Config only through env vars. Public repo: **no client or personal names, no local paths, no credentials, no consumer repo or ticket keys** in code, docs or `docs/runs/`.
