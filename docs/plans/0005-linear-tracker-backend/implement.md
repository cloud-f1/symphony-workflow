# Implement log — Linear as an alternative tracker backend (`ADLC-57`)

## 2026-09-26

Built in one pass, in dependency order:

1. **`tracker.py`** (new) — `TrackerError`, `ConfigError`, the `TrackerClient` Protocol
   (`runtime_checkable`), `get_tracker_client()` (reads `SYMPHONY_WORKFLOW_TRACKER`, lazy-imports
   whichever backend is selected so importing `tracker.py` never requires both backends'
   dependencies).
2. **`models.py`** — added `Issue.from_linear`/`CurrentUser.from_linear` classmethods alongside
   the existing Jira `from_api` ones. `IssueCreateResult`/`Transition` stay Jira-only.
3. **`config.py`** — added `LinearConfig` (`LINEAR_API_KEY`, `SYMPHONY_WORKFLOW_LINEAR_TEAM`,
   team key upper-cased on load).
4. **`client.py`** — `JiraError` now subclasses `tracker.TrackerError`. Added `issue_url()` and
   `search_ready_tickets()` (moved the JQL `READY_JQL`/`JIRA_PROJECT` construction here from
   `poller.py`).
5. **`linear_client.py`** (new) — `LinearClient` implementing the `TrackerClient` surface only
   (no `create_issue`, no arbitrary search — see `intent.md`'s non-goals). 214 lines, mirrors
   `client.py`'s structure and docstring conventions.
6. **`orchestrator.py`** — `JiraClient`/`JiraError` → `TrackerClient`/`TrackerError` throughout.
   `render_run_doc`'s `client.base_url` + hand-built `/browse/{key}` → `client.issue_url(key)`;
   its frontmatter key `jira_url:` → `tracker_url:`.
7. **`poller.py`** — same type/exception rename. `READY_JQL`/`JIRA_PROJECT` module constants
   removed; `tick()` calls `self._client.search_ready_tickets(max_results=...)` instead.
8. **`cli.py`** — split `_client()` (now `get_tracker_client()`-backed, tracker-agnostic) from a
   new `_jira_client()` (unconditionally `JiraClient`, for the genuinely Jira-only commands).
   `get`/`comment`/`transition`/`plan`/`implement` use `_client()`; `search`/`call`/`decompose`/
   `sync-decomposition` use `_jira_client()`.
9. **`api.py`** — `app.state.jira_client` → `app.state.tracker_client`. New `get_tracker()`
   dependency for the tracker-agnostic endpoints; `get_jira_client()` now does an `isinstance`
   check and raises `501` for the Jira-only endpoints (`/tools`, `/tools/{name}/call`) when the
   backend isn't Jira. `HealthResponse.jira_reachable` → `tracker_reachable`.

## Verification

- `uv run pytest` — 214 passed (194 existing + 20 new: 13 `test_linear_client.py`, 5
  `test_tracker.py`, 2 new `LinearConfig` cases in `test_config.py`). Every existing test that
  referenced a renamed field (`test_render_run_doc_prepends_jira_url_frontmatter` →
  `..._tracker_url_frontmatter`, `test_health_reports_jira_reachable` → `..._tracker_reachable`)
  was updated in place, not skipped.
- `uv run ruff check .` — clean.
- **Live smoke test** (read-only, against the real `Gaia`/`GAI` Linear team — see `spec.md`'s
  closing section): `get_myself`, `issue_url`, `get_issue("GAI-5")`, `search_ready_tickets()` all
  confirmed against real API responses, not just mocks.
- One real incident caught mid-implementation, unrelated to this ticket's code: a `sed` command's
  redaction regex used a GNU-only `\|` alternation that BSD/macOS `sed` doesn't support, printing
  a real Jira API token in plaintext into the session transcript. Recorded here because it
  happened during this ticket's own credential setup, not because it's Linear-specific — see the
  session's own record for the rotation recommendation given at the time.

## What's explicitly still Jira-only after this ticket (by design, not oversight)

`tools.py` (the AI-agent tool-call surface), `decompose.py`/`sync_decomposition.py`, and raw-JQL
`search` — see `intent.md`'s non-goals. A future ticket porting any of these to Linear needs its
own field-mapping design (Linear has no Epic/parent/sprint/story-points concepts to map onto).
