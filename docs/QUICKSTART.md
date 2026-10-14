# Quickstart

A linear walkthrough from zero to driving a real Jira ticket through this repo's Plan/Implement
loop. This is a narrative, not a reference — for day-to-day operation once you're set up, use
`.claude/skills/symphony-workflow-ops/SKILL.md`; for design/why, see `docs/plans/0001-symphony-poc/`;
for the full command surface, `CLAUDE.md`.

## 1. Install and configure

```bash
uv sync --extra dev
uv run symphony-workflow init-env    # creates .env with empty keys and makes sure git ignores it
```

Edit `.env`:
```
JIRA_BASE_URL=https://your-site.atlassian.net
JIRA_EMAIL=you@example.com
JIRA_API_TOKEN=<Atlassian account settings -> Security -> API tokens>
```

Check the file and the connection (never prints a value; the second form makes one read-only call):
```bash
uv run symphony-workflow check-env
uv run symphony-workflow check-env --verify
uv run --env-file .env symphony-workflow call get_myself '{}'    # the same check, as a tool call
```
(`--env-file` is required — `config.py` reads `os.environ` directly, no `python-dotenv`; plain
`uv run symphony-workflow ...` will NOT pick up `.env`. See `CLAUDE.md`'s "Jira API gotchas".)

If this returns your account, not an error, you're connected.

## 2. Try a Jira operation

```bash
uv run --env-file .env symphony-workflow list-tools          # every registered tool, as JSON schema
uv run --env-file .env symphony-workflow call search_issues '{"jql": "project = <YOUR_PROJECT>"}'
```

Every Jira operation in this repo is reachable two ways: a typed `client.py` method, and a
schema-described `tools.py` entry callable via `call` above — this is what makes it usable as an
agent tool, not just a human CLI.

## 3. Drive one ticket through Plan — no server needed

This is the CLI-native control plane (`ADLC-45`), the fastest way to try the actual Plan/Implement
loop without running a background server:

```bash
uv run --env-file .env symphony-workflow plan <YOUR-PROJECT>-<N>
```

This runs a real Claude Agent SDK session in `permission_mode="plan"` (read-only — explores your
target repo, writes no code) against the ticket, writes `docs/runs/<key>/plan.md`, posts the plan
as a Jira comment, and transitions the ticket to "In Review". **Read the plan before doing
anything else** — it's a real judgment call, not a formality (this repo's own stated philosophy:
Implement never runs unattended).

If the plan looks right:
```bash
uv run --env-file .env symphony-workflow implement <YOUR-PROJECT>-<N>
```
This resumes the *same* agent session (not a fresh one) with `permission_mode="acceptEdits"` and
writes the actual diff. It never opens a PR or pushes — you review the diff next, by hand.

## 4. Optional: run the background server + poller instead

For auto-discovery (any ticket landing in `status = "To Do"` gets auto-Planned, never
auto-Implemented — a deliberate safety split) or the HTTP API surface:

```bash
uv run --env-file .env uvicorn symphony_workflow.api:app --port 8811
curl http://127.0.0.1:8811/health   # {"status":"ok","jira_reachable":true,"poller_running":true}
```

See `.claude/skills/symphony-workflow-ops/SKILL.md` for the full manual/poller/API/CLI decision
tree — you don't need the server for step 3 above, it's a separate, complementary mode.

## 5. Optional: break a big ticket into a backlog first

For an Epic that's too big to Plan directly, decompose it into reviewable Stories/Tasks first
(`ADLC-46`/`47`):

```bash
uv run --env-file .env symphony-workflow decompose <EPIC-KEY>          # writes decomposition.md/.json, no Jira writes
# ... review docs/runs/<EPIC-KEY>/decomposition.md by hand ...
uv run --env-file .env symphony-workflow sync-decomposition <EPIC-KEY>  # creates the real Story/Subtask issues
```

`decompose` never touches Jira. `sync-decomposition` only reads the JSON you already reviewed —
it never re-runs the decomposition agent, and it creates real, permanent Jira issues, so read the
Markdown first.

## 6. Optional: push a plan into Linear or keep it as Markdown

For a project whose plan lives as epics-as-markdown files (`docs/epics/e<N>-*.md`), nothing below needs Jira
credentials, and nothing touches a tracker unless you pass `--to linear` (plan 0008):

```bash
uv run symphony-workflow profile --path .                  # reads how the project works, prints two recommendations
uv run symphony-workflow profile --path . --write          # saves docs/context/plan-sync.json after you confirm
uv run symphony-workflow import-plan --path .              # the Category / Task / To-Do plan and its warnings
uv run symphony-workflow sync-plan --path . --to markdown  # Athena-compatible files, idempotent, never deletes
uv run symphony-workflow split-todos --path .              # To-Dos for Tasks that have none, into a review file
# ... edit docs/context/todo-proposal.json, then:
uv run symphony-workflow split-todos --path . --approve
uv run symphony-workflow sync-plan --path . --to linear --project "My plan" --dry-run
uv run symphony-workflow sync-status --path . --to linear --project "My plan" --dry-run
uv run symphony-workflow sync-plan --path . --to jira --project KEY --dry-run     # Jira: Epic / Story / Subtask
```

Put `LINEAR_API_KEY` and `SYMPHONY_WORKFLOW_LINEAR_TEAM` in the project's `.env`, never in a chat. Use a fresh
`--project` name for a first run: issues in an existing project that were made another way have no `sd-id:` marker and
would be duplicated. Both sinks were run against the real services on a throwaway project (and cleaned up afterwards), but keep `--dry-run` on until the output looks right. For Jira the keys of the cards it made are kept in `docs/context/sync-state.json`; commit that file, it is how a re-run finds them.

## 7. Take a ticket from branch to release

```bash
uv run symphony-workflow start ADLC-12     # branch ADLC-12-<slug> + worktree from origin/main, card In Progress
# ...work and commit, each subject ending in (ADLC-12)...
uv run symphony-workflow pr ADLC-12        # push, open the PR with the key in title and body, card In Review
uv run symphony-workflow land ADLC-12      # after the merge: verify on GitHub, clean up, card Done
uv run symphony-workflow release 0.4.0     # after the version-bump PR is merged: gate, tag, GitHub Release
```

The key in the branch name is what makes Jira link the branch, commits and PR; see
[JIRA_GIT_SETUP.md](JIRA_GIT_SETUP.md) for the optional Jira settings.

## 8. Run the tests

```bash
uv run pytest              # all HTTP mocked via respx — no live Jira access, no network
uv run ruff check .
uv run ruff format --check .
```

Every change in this repo ships with tests in the same PR — see `CLAUDE.md`'s "the three
non-negotiables" before adding anything new.

## Where to go next

| Need | Go to |
|---|---|
| Day-to-day operation, the full decision tree (manual/poller/API/CLI) | `.claude/skills/symphony-workflow-ops/SKILL.md` |
| Full command/architecture reference | `CLAUDE.md` |
| Why this exists, the original design | `docs/plans/0001-symphony-poc/{intent,spec,implement}.md` |
| Using this against a *different* repo/Jira project | `docs/PORTABILITY.md` |
| A specific past planning cycle's design | `docs/plans/INDEX.md` |
