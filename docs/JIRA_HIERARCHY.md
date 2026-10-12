# Jira hierarchy: Epic -> Story -> Subtask

The standard every project that uses `symphony-workflow` follows, and the two commands that keep it
true. It started in one consumer repo (its `jira-hierarchy` rule) and was applied to a second;
this is the shared version.

## Why it is mandatory

Two repos hit the same failure on their own:

* Consumer repo A: 33 of 73 hand-created Stories had no Subtasks, so the board showed no progress and
  nobody could see what was left.
* Consumer repo B: 36 cards, no Story with a Subtask, every card Unassigned, bugs and tasks hanging
  directly under Epics, a Done Epic that had open work under it.

`decompose` / `sync-decomposition` fix this for an Epic you have not built yet. They do nothing for
Stories that already exist. `audit` and `add-subtasks` do.

## The three layers

| Layer | Issue type | Naming | Size |
|---|---|---|---|
| Epic | Epic | `<KEY>-E<n>: <outcome>` (for example `JEV-E7: Usability`) | a direction the owner cares about; 3-8 Stories |
| Story | Story (a Bug or Task at the same level is fine) | a noun phrase: what changes for the user | one pull request, or one tightly related batch |
| Subtask | Subtask | verb-first, concrete, testable (`Add X`, `Wire Y`, `Tests: unit + e2e`) | an hour or two; 2 to 6 per Story |

## Rules

1. **Create the Subtasks when you create the Story**: from the plan, from `decompose`, or with
   `add-subtasks`. Never leave a Story as a bare title. One Subtask means the Story is really a Subtask;
   more than 8 means split the Story.
2. **The last Subtask of every Story is the tests** (`Tests: ...`), so "done" includes proof.
3. **Done-when**:
   * Subtask: its piece is committed on the Story's branch, or verified (for ops Subtasks).
   * Story: the pull request is **merged** (confirm with `gh pr view <branch> --json state`, not the exit
     code of `gh pr merge`) and all its Subtasks are Done. Transition after the merge, never before.
   * Epic: every Story is Done (and, for user-facing work, the deploy is verified live).
4. **Roll-up**: when a Story goes Done, its open Subtasks go Done with it, so the board shows 4/4, not 0/4.
   `rollup` does this going up; `add-subtasks` does it for Stories that were already Done.
5. **One owner**: if the project has an owner who is accountable for the cards, every Epic and Story is
   assigned to them (`audit --assignee "<display name>"` enforces it). Unassigned cards are invisible on a
   filtered board.
6. **Branch, worktree and commit carry the Story key**: branch `PROJ-241-short-slug`, commit subject ends
   `(PROJ-241)`; Jira links them automatically.
7. **Defaults the owner did not decide are written on the Story** ("Default applied: ..."), so the choice
   is visible.
8. **Bugs and Tasks are Story-level**: they need Subtasks and an Epic parent like a Story does. A Bug with
   no Epic is how work disappears from the plan.

## The two commands

```bash
# Read-only. Exit 0 = no errors, 1 = at least one error, 3 = Jira API error.
symphony-workflow audit ADLC                       # the whole project
symphony-workflow audit ADLC --open                # skip Stories that are already Done
symphony-workflow audit ADLC --assignee-me         # also require every Epic/Story to be assigned to the token's user
symphony-workflow audit ADLC --json                # machine-readable (also the `audit_hierarchy` tool)

# Writes. Idempotent (a Story that already has Subtasks is skipped).
symphony-workflow add-subtasks plan.json --dry-run # show what would be created
symphony-workflow add-subtasks plan.json --assign-to-me
```

`plan.json` is `{ "ADLC-12": ["Add X", "Wire Y", "Tests: unit + e2e"], ... }`. Plan problems (an empty
list, a blank or over-long summary, a key that is not an issue key) are rejected before anything is
created. A Story that is Done gets its new Subtasks transitioned to Done too. If Jira fails part-way, what
was already created is printed (and a re-run skips that Story, so finish it by hand). The tool call is
`add_subtasks`.

### What `audit` reports

| Finding | Severity | Meaning |
|---|---|---|
| `no_subtasks` | error | a Story-level issue with no Subtasks |
| `no_epic` | error | a Story-level issue under no Epic |
| `epic_without_stories` | error | an Epic with no children |
| `done_with_open_subtasks` | error | roll-up missed: a Done Story still has open Subtasks |
| `unassigned` / `wrong_assignee` | error | only with `--assignee` / `--assignee-me` |
| `one_subtask` | warning | the Story is really a Subtask |
| `too_many_subtasks` | warning | more than 8: split the Story |

`add-subtasks` warns (never blocks) when a plan has one Subtask, more than 8, or no `Tests: ...` Subtask.

## Notes

* Jira only (needs `parent`, `subtasks` and child search). Linear has no equivalent shape; see the README's
  Linear mapping.
* The Subtask issue type is `Subtask` in team-managed projects and `Sub-task` in some company-managed ones;
  `add-subtasks` tries the first and falls back to the second on a 400.
* Local source of truth for structure: keep the plan file in the repo next to the work (for example
  `docs/jira/plan.json`) so the structure is reviewable and re-runnable.
