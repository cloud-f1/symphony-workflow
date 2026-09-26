# Spec — Linear as an alternative tracker backend (`ADLC-57`)

## The shape: a Protocol, not a base class

`tracker.py`'s `TrackerClient` is a `Protocol` (structural typing), not an abstract base class
`JiraClient`/`LinearClient` inherit from. `JiraClient` satisfies it unchanged — zero changes to
its class hierarchy — which matters because `client.py` predates this design by a full POC's
worth of work and shouldn't need a retroactive inheritance change to participate.

## Why the run-loop surface is exactly six methods

`orchestrator.py`/`poller.py` were read line-by-line (not guessed) to find every `client.*` call
they actually make: `get_issue`, `add_comment`, `transition_issue`, `get_myself` (health check),
plus two derived from prose that assumed too much Jira knowledge — `client.base_url` (used to
hand-build a `/browse/{key}` URL, Jira-specific) became `issue_url(key)`, and poller.py's
module-level `READY_JQL`/`JIRA_PROJECT` (JQL string built in poller.py itself) became
`search_ready_tickets(max_results)` — the query language is now each backend's own concern, not
something poller.py assembles.

## Why `search_ready_tickets` moved onto the client, not a shared query builder

The alternative — a shared function that takes "project/team key, status, exclusions" and builds
either JQL or a GraphQL filter — would need to abstract over two fundamentally different query
models (Jira: a string DSL; Linear: a nested filter object) for a net result of one call site
(`poller.py`'s `tick()`). Putting the query construction on each client instead means each
backend's own module is the only place that needs to know its own query language, and adding a
third backend later touches one new file, not a shared query-builder's growing if/elif chain.

## What `search_ready_tickets` means differently per backend

| Concept | Jira (JQL) | Linear (GraphQL filter) |
|---|---|---|
| Project/team scope | `project = {JIRA_PROJECT}` | `team: { key: { eq: $teamKey } }` |
| "Ready to work" | `status = "To Do"` (a specific status name) | `state: { type: { eq: "unstarted" } }` (a state *category*, matches any team's differently-named Todo/Backlog state) |
| "Not an Epic" | `issuetype not in (Epic, ...)` | Vacuous — Linear has no Epic issue type; a Linear Project is a different object entirely, not an Issue |
| "Not a Subtask" | `... not in (..., Subtask)` | `parent: { null: true }` — Linear's sub-issue relationship is the closest analog |

This is a real semantic difference, not just syntax — Jira's exclusion list names two issue
*types*; Linear's filter names a workflow-state *category* plus a structural *parent* check. Both
land on the same intent ("only top-level, not-yet-started work"), reached differently.

## Why `get_issue`/`add_comment`/`transition_issue` need an extra round-trip on Linear

Jira's REST endpoints take the human-readable key (`GET /issue/{key}`) directly. Linear's GraphQL
mutations take the issue's internal UUID. `LinearClient._resolve_issue_ids()` does one extra
query per write call to bridge this — no caching, since a Run's handful of calls over one
Plan/Implement pass doesn't justify the complexity of a cache with its own invalidation story.
`transition_issue` additionally needs the issue's *team* id (to look up that team's workflow
states) — Linear's states are scoped per-team, not global the way Jira's transitions are scoped
per-workflow-but-effectively-global-for-a-project.

## The `render_run_doc` frontmatter rename: `jira_url` → `tracker_url`

A real, deliberate breaking change to the local `docs/runs/<key>/plan.md` schema. The function is
now called by both backends; a Linear-backed run's plan.md claiming `jira_url:` would be actively
wrong, not just imprecise. Historical files (written before this rename) keep their original
`jira_url:` key unchanged — they're point-in-time records of what was true when written, same
reasoning as leaving `docs/runs/**` alone during the `ADLC-53` repo rename.

## The CLI/API split: tracker-agnostic vs. Jira-only, by command/endpoint

| Tracker-agnostic (`TrackerClient`, `TrackerError`) | Jira-only (`JiraClient`, `JiraError`) |
|---|---|
| `get`, `comment`, `transition`, `plan`, `implement` | `search` (raw JQL), `call`/`list-tools` (tools.py's field set), `decompose`, `sync-decomposition` |
| `/health`, `/issues/{key}`, `/runs/{id}/plan`, `/runs/{id}/implement` | `/tools`, `/tools/{name}/call` |

`api.py`'s Jira-only endpoints get an explicit `501` (via `get_jira_client`'s `isinstance` check)
when the configured backend isn't Jira — not a silent pass of the wrong client type into
`tools.py`'s Jira-shaped handlers, which would fail confusingly deep inside a GraphQL client that
doesn't have a `create_issue` method at all.

## `/health`'s `jira_reachable` → `tracker_reachable` rename

Same reasoning as the frontmatter rename — a real, deliberate breaking change to the JSON
response shape, not additive. A Linear-backed instance reporting `jira_reachable: true` would be
lying about what it actually checked.

## Confirmed live (read-only) against the real `Gaia` (`GAI`) Linear team

`get_myself`, `issue_url`, `get_issue` (parsed `GAI-5` — same status/summary the CAS `gaia linear
issues` CLI independently reported), and `search_ready_tickets` all ran successfully against
real data, confirming the GraphQL response shapes assumed by `models.Issue.from_linear`/
`CurrentUser.from_linear` match reality, not just the mocked test fixtures. Write operations
(`add_comment`, `transition_issue`) were **not** exercised live — `Gaia` is CAS's real production
tracker, not a sandbox, and those paths have full `respx`-mocked coverage instead
(`tests/test_linear_client.py`).
