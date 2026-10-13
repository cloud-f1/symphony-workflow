# 0008 — Adaptive plan sync: intent

Status: **planning** (2026-10-13). Nothing below is built yet. Design in `spec.md`, build log and ticket tree in `implement.md`.

## Problem

Plans are now produced in many places: a frontier model asked for a roadmap, an epics-as-markdown workflow (the Athena
plugin keeps `docs/epics/e{n}-slug.md`, `EPIC_INDEX.md` and `docs/context/epic-progress.md`), a hand-written TODO file. This
repo can push **one** shape of plan (`decomposition.json`) into **one** tracker (Jira), once and then, since 0007, again
idempotently. Everything else is missing:

1. **Only Jira.** `LinearClient` reads, comments and transitions; it cannot create or update an issue, and it knows nothing
   about hierarchy. The README documents the Linear mapping (Project / ProjectMilestone / Issue / sub-issue, `ADLC-87`) but
   no code does it.
2. **No tracker at all.** A project with neither Jira nor Linear has nowhere to put the plan except ad-hoc files.
3. **One direction.** Status only flows plan to tracker at creation time. A card moved to Done in the tracker never reaches
   the plan files, and a ticked checkbox in a plan file never reaches the tracker.
4. **One-size-fits-all setup.** Every project has to be told which tracker, which layer-3 source and which push style to use,
   although most of that can be read from the project itself.

A survey of about 20 sibling projects (docs only; no credential files read) shows what real plans look like: a phase holds a
median of **3** epics (p10 1, p90 6); an epic holds a median of **7** acceptance checkboxes (p10 4, p90 17); about **21 %** of epic
files already carry a To-Do breakdown (19 % `### S1:` story headings, 2 % implementation phases) with a median of **4** items
(p10 3, p90 6); and **99.8 %** of the phases ever recorded are already complete.

## Goals

1. One **neutral three-layer plan**: Category, Task, To-Do, shaped like a pyramid (about 1 : 3 : 9 for a typical batch).
2. **Adaptive setup**: `profile` reads the project (plan files, tracker credentials by key name, branch and commit naming,
   content shape) and makes a *default recommendation*; a second recommendation is refined from the content. The project
   picks the system to integrate with, how to push and how to record, and the choice is saved in a committed config file.
3. Three **sinks**: Jira, Linear, Markdown (Athena-compatible files in the project's `docs/`, committed).
4. **Two-way status**: tracker status flows back into the plan files; ticked To-Dos flow up to the tracker. Content stays owned
   by the plan.
5. **Idempotent and re-runnable at any time**, with `--dry-run` everywhere (inherited from 0007).
6. A **stage-2 review**, done after everything is built: compare how each neighbouring project works today with adopting this
   workflow (pros and cons), then decide the rollout together.
7. A **stage-3 refresh, the very last step**: update every skill, plugin file and CLI capability (help text, examples, tool
   descriptions) using what stages 1 and 2 taught us and the real state of the code.

## Non-goals

- Not a full Markdown tracker. Markdown is an export plus checkbox state; the Plan/Implement run-loop does not run on it.
- No automatic Implement, ever. The poller still only auto-Plans (see `CLAUDE.md`).
- No bulk import of history by default: completed phases are not synced unless asked (`--include-done`).
- No credentials in the config file, the profile output, the plan files or the Markdown export.
- No code that names a particular consumer project. The Athena adapter is called `epics-md`; Athena is named only in docs.

## Definition of done

- A project with no configuration runs `profile` and gets a recommendation it can accept in one command.
- The same plan syncs to Jira, to Linear and to Markdown and a second run changes nothing (verified on a throwaway project).
- A status change on either side is reflected on the other after one `sync-plan --pull` / `--push`, and a content conflict is
  reported, never silently overwritten.
- Layer sizes stay inside the guardrails (2 to 8 Tasks per Category, 2 to 6 To-Dos per Task) or the run warns.
- Unit tests for every new module, HTTP mocked with `respx`, nothing talks to a live tracker in the suite.
- Stage 2 is written and discussed before the rollout plan is fixed.
- Stage 3 ships last: skills (both copies) and plugin files match the shipped commands, `claude plugin validate --strict` passes,
  and a plugin version bump makes installed copies refresh.
