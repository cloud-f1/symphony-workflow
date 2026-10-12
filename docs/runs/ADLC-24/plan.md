This is a plan only; nothing has been changed or tested yet. `src/jira_agent_cli/__init__.py` is currently empty, `pyproject.toml` sets `version = "0.1.0"`, and nothing else in the code or tests uses a version today.

**Files that would change**
1. **`src/jira_agent_cli/__init__.py`**: add a one-line module docstring and `__version__ = "0.1.0"`, typed in directly as the ticket asks.
2. **`tests/test_version.py`** (new): the repo's CLAUDE.md requires a test for every change, so this adds two:
   - `test_version_is_exposed` checks that `jira_agent_cli.__version__ == "0.1.0"`.
   - `test_version_matches_pyproject` reads `pyproject.toml` with Python's built-in `tomllib` and checks that its `version` equals `__version__`. If one is bumped without the other, this test fails.

   No HTTP calls are involved, so no `respx` mocking is needed. It gets its own file because the change doesn't fit the client, tools or CLI test files.

**Not in scope:** no `--version` CLI flag and no tool-registry entry. The ticket doesn't ask for them, and the repo's rule that a client method also needs a tool entry only covers Jira operations.

**Risks and open questions**
- The version is written in two places, so they could drift apart. The second test catches that.
- Two possible follow-ups, not part of this ticket: keeping the version in one place (read from `pyproject.toml` via the build tool or `importlib.metadata`), and adding a `jira-agent --version` flag.

**How to check it once implemented:** `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run python -c "import jira_agent_cli; print(jira_agent_cli.__version__)"`, which should print `0.1.0`. Move ADLC-24 to "In Progress", then "Done", with `jira-agent transition`.

The plan is saved at `<local plan file>`.

**Summary for the Jira comment:**
Plan for ADLC-24: add `__version__ = "0.1.0"` and a short module docstring to `src/jira_agent_cli/__init__.py`, which is currently empty. The value matches the version in `pyproject.toml`. Also add `tests/test_version.py` with two tests: one checks the constant is there, and one reads `pyproject.toml` and fails if the two versions don't match. No client, tool or CLI changes, and no HTTP mocking. Check with `uv run pytest`, `uv run ruff check .` and a quick import. Low risk. Possible follow-ups, not in this ticket: keep the version in one place, and add a `jira-agent --version` flag.

The Atlassian Rovo connector needs to be authorized in your claude.ai connector settings before I can post this comment to Jira myself. Until then, post it by hand or with `jira-agent comment ADLC-24 "..."`.