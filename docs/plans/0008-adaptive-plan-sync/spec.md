# 0008 — Adaptive plan sync: design

Recommendations are in **bold**. Decisions already agreed with the owner are marked *(agreed)*.

## 1. The three layers and their mapping *(agreed)*

| Layer | Meaning | Markdown (Athena-compatible) | Jira | Linear |
|---|---|---|---|---|
| 1 Category | a batch / theme of work | one row in `docs/epics/EPIC_INDEX.md` and `docs/context/epic-progress.md` (Phase Status) | Epic | ProjectMilestone |
| 2 Task | one deliverable | `docs/epics/e{n}-slug.md` | Story | Issue (with `projectMilestoneId`) |
| 3 To-Do | one concrete piece of work | checkbox under `## To-Do` in the Task file | Subtask | sub-issue (`parentId`) |

Internally the code says *layer 1/2/3*, never "epic": the word means different things in Athena, in Jira and in Linear.
Acceptance criteria are **not** To-Dos: they stay a checklist in the Task description (`## Acceptance Criteria`), as
`sync-decomposition` already renders them. Mapping each one to a Subtask would give a median of 8 and up to 63 per Task.

## 2. Pyramid sizing

Observed medians: 3 Tasks per Category, 3 to 5 To-Dos per Task. **Target about 1 : 3 : 9 per batch; each layer about 3x the
one above.** Guardrails (warnings, reused from `audit`): 2 to 8 Tasks per Category, 2 to 6 To-Dos per Task; the last To-Do is
the tests. A Category with more than 8 Tasks is reported as "split"; a Task with more than 8 To-Dos as "split the Task".

Top-heavy risk: a project with 76 recorded phases would become 76 Epics. **Only phases that are not complete sync by default;
completed history needs `--include-done`.** Layer 3 is where the weight belongs, but only through concrete To-Dos, never by
promoting every acceptance criterion.

## 3. Adaptive setup: profile, then two recommendations

`symphony-workflow profile [--path DIR] [--json]` is **read-only** and prints facts and a recommendation. Signals:

| Signal | Read from | Never |
|---|---|---|
| Plan source | `docs/epics/e*.md`, `EPIC_INDEX.md`, `docs/context/epic-progress.md`, `strategy-log.md`; any `docs/plans/*/` | |
| Tracker in use | the **names** of `JIRA_*` / `LINEAR_API_KEY` / `SYMPHONY_WORKFLOW_TRACKER` set in the environment or `.env` (via `check-env`); Jira-style keys in branch names and commit subjects (`KEY-12-...`, `(KEY-12)`) | values of any credential |
| Content shape | epics per phase, acceptance items per epic, share of epics with implementation phases, number of non-complete phases | |
| Workflow | an installed Athena command set (`.claude/commands/athena/`) | |

**Recommendation 1 (from usage, the default)**: tracker = Jira if Jira credentials *and* key-style history exist; Linear if a
Linear key exists; **Markdown otherwise**. Layer-3 source, push mode and record mode are the defaults in section 5.

**Recommendation 2 (from content, refines 1)**: for example, many epics with implementation phases leads to "use the phases";
a median above 8 acceptance items leads to "group before creating To-Dos"; more than 8 open Tasks per Category leads to "split
the Category"; a large completed history leads to "keep backfill off". Both are printed with the evidence, so the project
can override any of them.

The choice is saved in **`docs/context/plan-sync.json`** (committed, no secrets): `tracker`, `plan_source`, `todo_source`,
`push`, `record`, `include_done`. `profile --write` creates it after a confirmation; `sync-plan` reads it and any flag overrides it.

## 4. Layer-3 source (To-Do) *(agreed: hybrid)*

1. The Task's **implementation phases / checkpoints** when it has them (median 3).
2. Otherwise the **`decompose` agent** splits the acceptance criteria into 3 to 5 verb-first To-Dos, last one the tests,
   written to `decomposition.md` for human review first (the existing review gate; no tracker write before approval).
3. When the agent is unavailable or the user wants it deterministic: the **five workflow steps** (spec, implement, QA, commit,
   merge), which line up with the status matrix.

Status mapping for the Task: its status is the **furthest finished step** of the matrix; To-Do status is the checkbox.

## 5. Push and record modes (the project picks)

- **push**: `manual` (default; run `sync-plan`, `--dry-run` first), `on-approve` (an optional hook that runs `sync-plan` after
  a plan is approved). Never on Implement.
- **record**: `markdown` (default; the Athena-compatible files plus `sync-state.json` mirror the tracker), `none`.
- **direction**: `--push` (plan to tracker), `--pull` (tracker status to plan), default both, in that order.

## 6. Identity, status and conflicts

- Identity reuses 0007: a stable `id` per entry (`c1`, `c1.t2`, `c1.t2.d3`), a trailing `sd-id:` marker in the description
  (Jira, Linear), the id in a comment in the Markdown file, and the `sync-state.json` id-to-key cache.
- **Content** (title, description, acceptance criteria) belongs to the plan. A difference is drift: `--push` updates the tracker.
- **Status** belongs to the tracker when one is configured, and to Markdown when none is. `--pull` writes the tracker status
  into the matrix and the checkboxes; `--push` of a ticked To-Do transitions the tracker card.
- A change on both sides since the last sync is a **conflict**: report it, change nothing, exit non-zero.
- Status names differ per backend: one mapping table per sink (to do / in progress / done / blocked), overridable in the config.
- Nothing is deleted, in any direction. A card or file that exists on one side only is reported as an orphan.

## 7. Markdown sink layout (also what the Athena adapter reads)

```
docs/epics/EPIC_INDEX.md          layer 1: Categories (name, status, link)
docs/epics/e{n}-{slug}.md         layer 2: one Task: Goal, ## Acceptance Criteria, ## To-Do (- [ ] items, id comments)
docs/context/epic-progress.md     Phase Status table + step matrix (spec, impl, QA, commit, merge)
docs/context/sync-state.json      id -> tracker key cache (no secrets)
```

The adapter is tolerant: two template variants of the source workflow differ in their emit scripts, so it reads the documented
files and does not call those scripts. Unknown sections are preserved on write.

## 8. Linear

`LinearClient` gains create / update issue, create ProjectMilestone, create sub-issue and list children, behind an extended
`TrackerClient` Protocol that Jira satisfies too. Hierarchy follows the README mapping. The `tools.py` surface and
`decompose` stay Jira-only until the Linear sink is proven; the new `sync-plan` is tracker-agnostic.

## 9. Process rules inherited from this repo

Every new capability gets a CLI subcommand and a registered tool, with `respx`-mocked tests. Decompose an Epic into Stories
before building (`CLAUDE.md`). No credential, personal path or consumer project name in code, tests or docs. Public repo.

## Open questions

1. Is `on-approve` worth building in the first release, or only `manual`?
2. Should `profile --write` run automatically the first time `sync-plan` is invoked in an unconfigured project?
