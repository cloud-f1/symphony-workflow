# Intent — Maintainability + architecture improvement plan (`ADLC-88`)

## Why

Alex asked (2026-09-26), after `ADLC-57`/`ADLC-87` shipped the Linear backend, to step back and
improve the codebase more broadly across six dimensions: unit test coverage, module/function
boundaries, SOLID + design patterns, general maintainability, a formalized git-tag release
process, and updating the `symphony-workflow-ops` skill to reflect everything shipped since it
was last touched. This doc records real, measured findings (not generic advice) and a prioritized
plan — see `spec.md` for the concrete action list.

## Grounding data (measured 2026-09-26, not estimated)

- **214 tests, 92% overall coverage** (`pytest --cov=symphony_workflow`). Every module is
  ≥90% except one real outlier: **`cli.py` at 70%** — every command's *failure* branch
  (`except (...) as exc: typer.echo(...); raise typer.Exit`) is untested; only happy paths have
  coverage.
- **3544 lines across 16 modules.** Largest: `orchestrator.py` (407), `api.py` (363), `cli.py`
  (314), `linear_client.py` (287), `poller.py` (269).
- **A real, twice-flagged-but-never-fixed doc gap**: `.claude/skills/symphony-workflow-ops/
  SKILL.md` and its `plugin/skills/` mirror both have **zero** mentions of `SYMPHONY_WORKFLOW_
  TRACKER`, Linear, or `ProjectMilestone` — the skill a fresh session is told to load first
  (`CLAUDE.md`: "New to this repo? Load the `symphony-workflow-ops` skill first") doesn't know
  about half of what shipped in `ADLC-57`/`86`/`87`. This is the most concrete, highest-value
  finding in this whole review.
- **A real, already-observed release-process gap**: `v0.3.0` and `v0.4.0`'s CHANGELOG entries
  say "tagged" but `git tag -l` before this Epic only showed `v0.2.0` — the two-file version bump
  (`pyproject.toml` + `plugin.json`) plus a separate manual `git tag` step is exactly the kind of
  multi-step manual process that silently drops a step under normal workflow pressure. Confirmed
  fixed for `v0.4.1` (this session actually ran `git tag` + pushed it), but nothing stops it
  regressing next time without a script enforcing the sequence.
- **A real duplication**: the "tracker-agnostic vs. Jira-only" client-selection bifurcation is
  implemented **twice**, independently — `cli.py`'s `_client()`/`_jira_client()` pair and
  `api.py`'s `get_tracker()`/`get_jira_client()` pair. Same decision, same `isinstance(client,
  JiraClient)` check, same error message shape, written twice.

## Non-goals

- **Not a rewrite.** This is a POC-scale repo (per its own `CLAUDE.md`, "no CI... tested
  manually") — the plan favors small, surgical fixes over architectural upheaval. A module at
  400 lines is a candidate to watch, not an automatic split.
- **Not adding a 3rd tracker backend speculatively.** The Registry-pattern opportunity in
  `spec.md` is documented and deliberately deferred, same reasoning as `docs/PORTABILITY.md`'s
  "don't build it preemptively" — revisit only when a real 3rd backend is actually being added.
- **Not introducing a new test framework, CI system, or type checker in this pass** — `ruff` +
  `pytest` + `respx` stay the toolchain; closing the `cli.py` coverage gap uses the existing
  patterns already proven in `test_client.py`/`test_linear_client.py`.

## Definition of done

See `spec.md`'s six numbered sections — each has its own concrete, checkable deliverable. Per
`ADLC-86`'s rule, this Epic gets decomposed into Stories/Subtasks (`symphony-workflow decompose
ADLC-88`) once the plan below is confirmed with Alex — nothing here is built directly under the
Epic ticket.
