# Intent — Linear as an alternative tracker backend (`ADLC-57`)

## Why

Alex asked (2026-09-26) to give `symphony-workflow` a second tracker backend: Linear, alongside
the existing Jira integration, so a repo whose issues live in Linear can drive the same
Plan/Implement loop this repo already drives against Jira. A parallel investigation of another internal tool
(another internal tool)'s own Linear integration confirmed Linear connectivity, config,
and the underlying data model — but that tool is scoped to that tool's own content-tracking needs
(epics-as-projects, that tool's CLI) and explicitly not what this ticket extends. This repo gets its
own, independent Linear client, matching how it already owns its own Jira client rather than
depending on another repo's.

## Non-goals (explicit, so scope doesn't silently creep)

- **Not porting the AI-agent tool-call surface (`tools.py`).** `create_issue`'s field set
  (`epic`/`parent`/`sprint_id`/`story_points`) is genuinely Jira-shaped — Linear has no Epic
  issue type, no sprint/story-points custom fields, and issues belong to a Team+Project instead.
  Faking those fields for Linear would be dishonest; a real Linear tool-call surface (if wanted)
  is separate follow-up work with its own field design.
- **Not porting `decompose`/`sync-decomposition`.** Same reasoning — Epic decomposition into
  Story/Subtask is a Jira-shaped operation (`ADLC-46`/`47`). A Linear equivalent (Project →
  Issue, no Subtask) is a different design, not this ticket.
- **Not porting raw-JQL `search`.** Linear has no JQL equivalent; arbitrary query power is a
  Jira-only capability in this version.
- **Not migrating this repo's own tracking off Jira.** `ADLC` stays the board this repo tracks
  its own work on. `SYMPHONY_WORKFLOW_TRACKER` selects which backend a *given deployment* drives
  its target repo's tickets through — independent of what tracks `symphony-workflow`'s own
  development.

## Definition of done

- A `TrackerClient` Protocol (`tracker.py`) covers exactly the Plan/Implement run-loop's real
  surface: `get_myself`, `get_issue`, `add_comment`, `transition_issue`,
  `search_ready_tickets`, `issue_url`, `close`/context-manager.
- `orchestrator.py` and `poller.py` — the shared run-loop logic — are typed against
  `TrackerClient`, not `JiraClient`, and catch `TrackerError` (a new common base), not
  `JiraError` specifically. `JiraClient` still satisfies the protocol unchanged;
  `LinearClient` is a new, independent implementation.
- `SYMPHONY_WORKFLOW_TRACKER=jira|linear` (default `jira`, so every existing deployment is
  unaffected) selects the backend via a `get_tracker_client()` factory. `LINEAR_API_KEY` /
  `SYMPHONY_WORKFLOW_LINEAR_TEAM` configure the Linear side (`LINEAR_API_KEY` deliberately reuses
  the same env var name another internal tool's Linear tooling already uses — one Linear key per
  machine, not two differently-named ones for the same credential).
- `cli.py`'s tracker-agnostic commands (`get`/`comment`/`transition`/`plan`/`implement`) work
  against either backend. Its Jira-only commands (`search`/`call`/`list-tools`/`decompose`/
  `sync-decomposition`) stay explicitly Jira-typed, unaffected by `SYMPHONY_WORKFLOW_TRACKER`.
- `api.py` follows the same split: `/health`, `/issues/{key}`, `/runs/{id}/plan`,
  `/runs/{id}/implement` are tracker-agnostic; `/tools`, `/tools/{name}/call` return `501` when
  the configured backend isn't Jira (explicit, not a silent no-op).
- `LinearClient` has its own test suite (mocked via `respx`, same pattern as `test_client.py`),
  and every existing test still passes unmodified in spirit (renamed types/imports aside).
- Docs (`README.md`, `CLAUDE.md`, `docs/PORTABILITY.md`) describe the backend switch honestly,
  including what does and doesn't work on Linear.

## Acceptance test (manual, since this repo has no CI)

Point a real `SYMPHONY_WORKFLOW_TRACKER=linear` instance at a real Linear team + a Todo issue,
run `symphony-workflow plan <TEAM-N>`, and confirm: the issue is fetched, a plan is written to
`docs/runs/<TEAM-N>/plan.md`, a comment is posted on the Linear issue, and the issue transitions
to "In Review" (or whatever state the team's workflow has under that name).
