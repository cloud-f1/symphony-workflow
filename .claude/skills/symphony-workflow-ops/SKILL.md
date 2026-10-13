---
name: symphony-workflow-ops
description: >
  Use when operating symphony-workflow itself — starting the API server, driving a
  ticket through Plan then Implement, reading poller status, or picking up a
  ticket by hand. Trigger on: "run symphony-workflow", "plan this ticket", "start
  the poller", "symphony-workflow server", "ADLC ticket", or any request to drive
  a Jira ticket through this repo's Plan/Implement loop. For the why/design
  behind these mechanics, read docs/{intent,spec,plan}.md — this skill is the
  short operational reference, not the design doc.
---

# symphony-workflow operations

**Read this before running anything in this repo for the first time.** For the
*why* (Symphony, the ADLC decision log, findings from real runs) see
`docs/{intent,spec,plan}.md` — this file stays short on purpose and doesn't
duplicate them.

## Session start — check before you start a server

Before assuming the API server is down (and definitely before launching a new
one), check first: `curl -s http://127.0.0.1:8811/health`. A prior session may
have left it running in the background; starting a second instance on the same
port just errors, and starting one against a different `.env`/target repo while
another is already polling is a real way to get two pollers double-dispatching
Plans. If `/health` doesn't respond, then start it as below.

## Quick start

```bash
uv sync --extra dev
cp .env.example .env   # fill in JIRA_BASE_URL / JIRA_EMAIL / JIRA_API_TOKEN
uv run --env-file .env uvicorn symphony_workflow.api:app --port 8811
```

`GET /health` reports tracker connectivity and whether the poller is running.
Note: `config.py` reads `os.environ` directly, no `python-dotenv` — plain `uv
run symphony-workflow ...` will NOT pick up `.env` and fails with "Missing required
env var(s)". Either pass `--env-file .env` to `uv run` (confirmed working,
uv 0.12.3+) or export the required vars into the shell yourself.

## The four ways to drive a ticket

1. **Manual, one ticket**: `POST /runs {issue_key}` → `POST /runs/{id}/plan` →
   **read the plan, it's a real judgment call, not a formality** → `POST
   /runs/{id}/implement`. `GET /issues/{key}/run` finds a Run's id from the
   issue key (needed after auto-discovery, since nothing else returns it).
2. **Auto-discovery (the poller)**: any ticket with `status = "To Do"` on the
   configured Jira project gets auto-Planned — no manual `POST /runs` needed.
   **It never auto-Implements.** That's not a missing feature, it's the whole
   point (see `docs/plans/0001-symphony-poc/spec.md`'s Phase 5 section for why) — don't "complete" it
   by wiring Implement into the poller.
3. **CLI tool-call passthrough**: `symphony-workflow call <tool> '<json args>'` or
   `symphony-workflow list-tools` — the same registry the API's `/tools` exposes, for
   scripting outside the server.
4. **CLI-native control plane** (no server needed): `symphony-workflow plan <key>` /
   `symphony-workflow implement <key>` run the same Plan/Implement loop as way 1
   in-process, via `orchestrator.py` (ADLC-45) — `RunStore` is per-process here, so
   `plan` saves its `Run` to `docs/runs/<key>/run.json` for a later `implement` to
   reload. `symphony-workflow decompose <key>` (ADLC-46) breaks a Jira Epic into
   Stories/Tasks for human review — read-only, no Jira writes, renders
   `docs/runs/<key>/decomposition.{md,json}`. `symphony-workflow sync-decomposition <key>`
   (ADLC-47) turns an already-reviewed `decomposition.json` into real Jira
   Story/Subtask issues; it does not re-run the decompose agent. See
   `docs/plans/0001-symphony-poc/spec.md`'s "CLI control plane" section for design detail.

## Tracker backend — Jira or Linear

`SYMPHONY_WORKFLOW_TRACKER=jira` (default) or `linear` picks the backend for ways 1/2/4 above
(`get`/`comment`/`transition`/`plan`/`implement`, the poller, `/health`, `/issues/{key}`,
`/runs/*`). **Not** tracker-agnostic — stay Jira-only regardless of the env var: `search` (raw
JQL, no Linear equivalent), `call`/`list-tools` (`tools.py`'s field set — Epic/parent linking,
sprint/story-points — is Jira-shaped), `decompose`/`sync-decomposition`. `api.py`'s `/tools` and
`/tools/{name}/call` return `501` when the configured backend isn't Jira, rather than silently
misrouting. See `ADLC-57` / `docs/plans/0005-linear-tracker-backend/intent.md`'s non-goals for why
those don't have a Linear equivalent yet.

**Linear structure mapping** (`ADLC-87`, confirmed against Linear's live GraphQL schema): don't
map Jira's Project→Epic→Story→Task onto Linear Team→Project→Issue — Linear Team count is capped
by plan (this workspace: 2, already at `GAI`+`CLO`), so "one Team per repo" can't scale. Use
instead: Linear **Project** = one repo, **`ProjectMilestone`** (inside that Project) = Epic,
**Issue** = Story, **sub-issue** (`parentId`) = Task. Full detail + English/繁體中文 write-up in
`README.md`'s "Recommended Linear structure" section.

## Epic decomposition — required, not optional

**Never build directly under an Epic ticket.** `symphony-workflow decompose <epic-key>` (read-only,
writes `docs/runs/<epic>/decomposition.md` for review) then `symphony-workflow sync-decomposition
<epic-key>` (materializes the reviewed decomposition as real Story/Subtask issues,
`parent=<epic-key>`) exist exactly for this — `ADLC-46`/`47`. Work happens against the
Stories/Subtasks, each transitioned as it completes; the Epic itself only transitions to Done once
its children are. **Real incident this rule fixes** (`ADLC-86`): `ADLC-57` (an Epic) shipped
end-to-end with zero children — caught only after the fact, then retroactively decomposed. If
you're about to `create_issue` an Epic and start writing code, stop and decompose first instead.

When a Subtask finishes, `symphony-workflow rollup <subtask-key>` does the bookkeeping the rule
above implies: Subtask Done → Story Done if all its Subtasks are → Epic Done if all its Stories
are (forward-only, idempotent, `--dry-run` first if unsure; exit 2 = key not found, 3 = Jira error
with the partial work printed). Jira-only.

To check a project's *existing* cards (not an Epic you are about to build): `symphony-workflow audit <PROJECT> --open`
(read-only; exit 1 on a Story with no Subtasks, a Story under no Epic, an Epic with no Stories, or a Done Story
with open Subtasks; `--assignee-me` also requires everything assigned to the token's user). Fix with
`symphony-workflow add-subtasks <plan.json> --dry-run`, then without `--dry-run` (idempotent). Rules and naming:
`docs/JIRA_HIERARCHY.md`. Jira-only.

If a bulk `sync-decomposition` pass is retroactively documenting already-*shipped* work (not real
forward-looking planning), pass `--status <name>` (`ADLC-121`) — otherwise the newly-created
Stories sit in Jira's default "To Do" and the poller races your own status fix-up with a real
auto-Plan run (confirmed live, twice: `ADLC-64`, `ADLC-89`).

**Content drift** (`ADLC-146`): `sync-decomposition` creates once; afterwards `symphony-workflow
diff-decomposition <epic> [--adopt] [--json]` (read-only; exit 0 in sync, 1 drift, 2 bad input, 3 Jira
error) compares `docs/runs/<epic>/decomposition.json` with the live Epic -> Story -> Subtask tree
(missing / summary-changed / description-changed / orphan; status, comments and links are not drift).
`sync-decomposition <epic> --update [--dry-run] [--adopt] [--yes] [--status NAME]` applies the safe part:
create missing, update changed, never delete, idempotent, stops at the first Jira error. Cards are tied to
spec entries by a stable `id` in the JSON and a trailing `sd-id: <id>` line in the Jira description; plain
`sync-decomposition` now refuses an Epic that already has children. Jira-only.

## Plan sync — a plan into Linear or Markdown (plan 0008)

For a project that keeps its plan as epics-as-markdown files (an `/athena:*`-style workflow, or a model-written plan
saved as `docs/epics/e<N>-*.md`), the plan can be pushed to a tracker or kept as Markdown. Three layers everywhere:
Category (Linear milestone, Markdown index row), Task (Linear issue, one `.md` file), To-Do (sub-issue, checkbox).
Target shape is about 1 : 3 : 9. None of this touches a tracker unless you pass `--to linear`.

- `profile [--path] [--json] [--write]` reads how the project works (plan files, credential key *names*, issue keys in
  git history) and prints two recommendations; `--write` saves `docs/context/plan-sync.json` after asking. Read-only.
- `import-plan` shows the plan and the pyramid warnings. `sync-plan --to markdown` writes Athena-compatible files
  (idempotent, only managed `sd:begin` blocks, never deletes); with no config it prints the recommendation and stops.
- A Task with no breakdown of its own gets To-Dos from `todo_source`: `split-todos` proposes them with one read-only
  agent turn into `docs/context/todo-proposal.{json,md}`, you edit, `split-todos --approve` unlocks them; otherwise the
  five steps (Spec, Implement, Tests and QA, Commit, Merge). Nothing reaches a tracker before the approval.
- `sync-plan --to linear [--project NAME] [--dry-run]` creates the Project, milestones, issues and sub-issues (an
  `sd-id:` line makes a re-run update in place; **hand-made issues without the marker are ignored, so a re-run
  against a project built another way duplicates them: use a fresh `--project`**).
- `sync-plan --to jira --project KEY [--dry-run]` writes Category = Epic, Task = Story, To-Do = Subtask (the same `sd-id:`
  marker; the issue keys it made are kept in `docs/context/sync-state.json` because a JQL child list lags a create).
- `sync-status --to linear|jira [--direction both|push|pull] [--dry-run]` moves To-Do status both ways; a change on both
  sides is a conflict, reported and left alone (exit 1). The progress matrix is not written back. For Jira run
  `sync-plan --to jira` first: it only looks at the cards in that key map.
- Both sinks were run against the real services on a throwaway project (and deleted afterwards), but still start with
  `--dry-run`. Git is only ever run through `safepath.run_git`.

## Branch naming — required, not optional

Every workspace's branch is `{ISSUE-KEY}-{slug-of-summary}` (built by
`workspace.py`'s `branch_name()`), e.g.
`ADLC-24-poller-live-verify-add-a-version-constant`. **The branch must start
with the issue key** — Jira's Development-panel auto-linking depends on it.
If you're doing manual dev work on a ticket outside the automated Plan/Implement
path, use the same pattern by hand:
```bash
git checkout -b ADLC-24-poller-live-verify-add-a-version-constant
```
Confirmed 2026-09-24: the GitHub-for-Jira app is now installed and connected
(`ADLC-30`, Done) — `repository.byInstanceType` is populated where it used to be
`{}`. **Branch-level linking is still unverified**, though: nothing has actually
pushed a correctly-named branch to GitHub since the app connected (Plan-phase
worktrees never commit), so `branch.byInstanceType` hasn't been exercised
end-to-end yet. Don't assume the Development panel populates until a real push
confirms it.

## Safety invariants — don't "helpfully" remove these

- **The Plan/Implement human gate is enforced in `RunStore`** (`RunTransitionError`),
  not just in the API layer — Implement is unreachable without a `plan_ready`
  Run, on purpose.
- **Workspace isolation**: every agent dispatch runs only inside a validated
  per-issue git worktree under one root (`workspace.py`'s `_validate`) —
  Symphony §9.5's invariants, adopted deliberately.
- **Never `claude -p`.** This repo's Agent SDK usage exists specifically to
  avoid that failure mode — see `docs/plans/0001-symphony-poc/spec.md`'s "Why Claude Agent SDK" section
  and a personal memory note.
- **The poller auto-Plans, never auto-Implements.** See point 2 above — this
  is the load-bearing safety property of Phase 5, not an oversight to "fix."

## Known limitations — work around, don't rediscover

- **`RunStore` is in-memory only.** A poller-created Run's `session_id` does
  not survive a server restart — confirmed in practice (`docs/runs/ADLC-24/review.md`),
  not just theoretical. If you restart the server mid-flow, a `plan_ready`
  Run's Implement path is gone; the ticket's Jira state (comment, status)
  persists, the ability to resume that exact agent session does not.
- **`permission_mode="acceptEdits"` can't run Bash.** Implement writes a diff,
  it cannot run its own tests/lint or transition the ticket itself — it will
  say so honestly in its result text rather than claim false verification.
  **Always run `uv run pytest` + `uv run ruff check .` yourself** in the
  worktree before trusting an Implement result.
- **Each new worktree needs its own `uv sync --extra dev`** before its tests
  will run — worktrees don't share a venv with the main checkout.

## Portability

This repo can act as the orchestrator for a *different* repo/Jira project — see
`docs/PORTABILITY.md` for the two env vars that matter
(`SYMPHONY_WORKFLOW_TARGET_REPO`, `SYMPHONY_WORKFLOW_JIRA_PROJECT`) and what's still
hardcoded (the prompt templates — Symphony's per-repo `WORKFLOW.md` model isn't
implemented yet).

## Releasing — a plugin only updates when `version` changes

An installed plugin keeps its cached copy until its `version` string changes, so a skill edit that is not
released is invisible to every other session. Order matters:

1. **Bump all four together in the feature PR**: `version` in `pyproject.toml`,
   `plugin/.claude-plugin/plugin.json` and `.claude-plugin/marketplace.json`, the `symphony-workflow`
   package version line in `uv.lock`, and a `## X.Y.Z` entry in `CHANGELOG.md`. Edit the lock line by hand:
   a newer `uv lock` can rewrite unrelated dependency markers. New feature = minor, fix = patch.
2. **Validate**: `uv run pytest`, `uv run ruff check .`, `claude plugin validate --strict plugin`.
3. **Merge first, tag second.** Confirm the merge with `gh pr view <n> --json state,mergeCommit`, update
   local `main` (`git fetch && git merge --ff-only origin/main`), then `scripts/release.sh X.Y.Z`. It checks
   the three versions, the CHANGELOG heading and that the tag is new, tags the current commit and never pushes;
   push with `git push origin vX.Y.Z`. It also fails when `marketplace.json` lists no plugin or a different
   version (ADLC-249).
4. **Close tickets after the merge, never before**: transition the Stories Done, then `rollup <subtask>`.
5. **Tell the consumers**: the companion status board runs this CLI through `uvx ... @vX.Y.Z`; bump its pin.
   Installed copies of the plugin refresh through `/plugin` once the version differs.

`gh pr merge` can be refused by the auto-mode classifier ("Merge Without Review") for a PR nobody has
reviewed, even when the user asked for it. Do the review a reviewer would (merge all open heads on a scratch
checkout, full tests, a leak scan for this public repo), report it, and let the user run the merge; do not
look for another way around it.
