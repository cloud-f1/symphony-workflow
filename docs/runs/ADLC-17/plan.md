Here's the ADLC-17 plan; nothing has been built or tested yet. It's also saved at `<local plan file>`.

## Files that would change
- **`models.py`**: add a `CurrentUser` model (`account_id`, `display_name`, `email_address`, `active`) with a `from_api()` constructor, built the same way as `Issue`.
- **`client.py`**: add `JiraClient.get_myself()`, which calls `GET /rest/api/2/myself` and uses the existing `_raise_for_status` error check.
- **`tools.py`**: add `GetMyselfArgs` (no fields), a `_get_myself` handler and a registered `get_myself` tool. The project rules require every client method to also be a tool. People can use it through `jira-agent call get_myself`, so no new CLI subcommand is needed.
- **`api.py`**: add a `HealthResponse` model and `GET /health`. The endpoint gets the client the same way the others do (`Depends(get_jira_client)`) and calls `get_myself()`. It always returns HTTP 200 with `status: "ok"`. It sets `jira_reachable: false` if it gets either:
  - a `JiraError` (Jira answered with an error status), or
  - an `httpx.TransportError` (couldn't connect, DNS failure, timeout).

  The second catch is needed because the client only turns error statuses into `JiraError`. Connection failures come through as raw httpx exceptions and would otherwise turn `/health` into a 500. The endpoint should be a plain `def` like `get_issue`, so the blocking call doesn't hold up the server. The response won't include any user details.
- **Tests**:
  - `test_client.py`: respx-mocked success and 401 cases for `get_myself`.
  - `test_tools.py`: a dispatch test with a `MagicMock` client.
  - `test_api.py`: three cases — Jira healthy (200), Jira returns an error status (401/503), and a connection error.

## Risks and open questions
1. **Timeout:** the client's default timeout is 30s, so `/health` could hang for 30s if Jira hangs. I recommend giving `get_myself` an optional timeout and having `/health` use about 5s.
2. **Bad credentials look like an outage:** a 401 or 403 reports `jira_reachable: false` even though Jira was reached. A separate `jira_error` field could tell these apart later; that's outside this ticket.
3. **httpx in `api.py`:** catching `httpx.TransportError` there brings an httpx type into the API layer. CLAUDE.md only forbids *calling* httpx outside `client.py`, so this is a judgment call. The alternative, converting connection errors to `JiraError` inside the client, would change behaviour for every method.
4. **Missing config:** if the Jira environment variables aren't set, the server doesn't start, so `/health` can't report that case. This is unchanged from today.

## How to check it once built
- `uv run pytest`
- `uv run ruff check .`
- `uv run jira-agent list-tools` should show `get_myself`.
- Optionally, with a real `.env`, run `curl localhost:8000/health` against the running server.

## Jira comment
Plan for ADLC-17: add a `GET /health` endpoint to `api.py` that returns `{"status": "ok", "jira_reachable": bool}`. The Jira check is a real call to `GET /rest/api/2/myself`, added as a new `JiraClient.get_myself()` method. Per the repo rule that every client method is also a tool, it is also exposed as a `get_myself` tool, with a small `CurrentUser` model. The endpoint always returns HTTP 200. It reports `jira_reachable: false` when Jira returns an error status (a `JiraError`, e.g. 401 or 5xx) or can't be reached at all (an `httpx.TransportError` for connection, DNS or timeout failures). It doesn't expose any user details. Tests: respx-mocked client tests for `get_myself` (success and 401), a tool dispatch test, and three API tests (healthy, Jira error status, connection error). Open questions: (1) whether the health check should use a short timeout (~5s) instead of the client's 30s default; (2) bad credentials currently show up as "unreachable", and a separate `jira_error` field could tell them apart later.

The Atlassian Rovo connector needs to be authorized in your claude.ai connector settings before it can be used. It isn't required here, since this repo has its own Jira CLI.