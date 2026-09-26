# Spec — Cloud deployment architecture (`ADLC-58`)

## Decision

**Deploy the API server to the cloud: yes. One shared multi-tenant instance across repos,
distinguished by a per-request "target tag": no.** Keep the existing per-repo instance model
(`docs/PORTABILITY.md`) — just host each instance in the cloud instead of on a laptop, and add
authentication before doing so.

This answers Alex's framing directly: the API-server-as-interpreter mental model is correct
(`api.py` already is a thin HTTP gateway any caller can drive without installing
symphony-workflow locally) — what doesn't hold is collapsing *multiple repos'* instances into
*one* shared process via tags. That's a different, bigger change than "put it in the cloud," and
it's the one `docs/PORTABILITY.md` already declined to build preemptively.

## Why per-repo, not shared multi-tenant (recap, cloud-specific angle)

Everything `docs/PORTABILITY.md` lists as singular per-process state is filesystem-backed and
host-specific in a way that doesn't get easier in the cloud, only differently shaped:

- Git worktrees (`WORKSPACE_ROOT`) are created **on the server's own disk** — the target repo
  must actually be cloned onto whatever host runs that instance. "Deploy the API server" without
  "...with a clone of the target repo already on it" doesn't work; there's no fetch-on-demand path.
- `RunStore` is in-memory, unpartitioned. A shared process serving repo A and repo B would need
  every Run lookup, every poll tick, every workspace path to also carry "which repo" — real
  new engineering, not a routing config.
- The poller already polls exactly one Jira project or Linear team (`ADLC-57`) per process.
  Multi-project/team polling in one loop was the specific shape rejected in `docs/PORTABILITY.md`.

None of this is cloud-specific — it's the same reasoning restated for a new trigger. The one
thing that *is* new in "the cloud" framing: instances no longer need a developer's laptop running
a terminal to stay up, which is the actual pain the original ask is trying to solve — and that's
solvable without multi-tenancy.

## Recommended shape

**One small container (or lightweight VM) per adopting repo**, each:

- Running `uv run uvicorn symphony_workflow.api:app --host 0.0.0.0 --port 8811` (or behind a
  reverse proxy on 443) — note the `--host` change from today's implicit `127.0.0.1` default,
  required to accept non-loopback connections at all
- With its own `.env` (own `JIRA_*`/`LINEAR_*` credentials, own `SYMPHONY_WORKFLOW_TARGET_REPO`,
  own `SYMPHONY_WORKFLOW_TRACKER`)
- With that repo already `git clone`'d onto the container's disk at the path `TARGET_REPO` points to
- Reachable at its own address, e.g. `https://symphony-<repo-slug>.internal.example.com`

These can share infrastructure (same cloud project, same Kubernetes cluster, same VM host even)
while staying **separate processes on separate ports with separate state** — "co-use one thing"
happens at the *infrastructure/ops* layer (one deployment pipeline, one place to look for logs
across instances), not at the *process* layer. Any Claude Code session — local or cloud, any
developer — then points its `SYMPHONY_WORKFLOW_API_URL` (a new env var this implies; today's
`.claude/hooks/session-start-check-api.sh` hardcodes `http://127.0.0.1:8811`) at that repo's
cloud address instead of localhost.

## Required prerequisite: authentication

`api.py` currently has none. Minimum-viable fix before binding beyond `127.0.0.1`:

- A single shared-secret bearer token per instance (`SYMPHONY_WORKFLOW_API_TOKEN` env var),
  checked via a FastAPI dependency (`Depends`) on every route except `/health` (so a load
  balancer / uptime check doesn't need the secret)
- Reject with `401` on a missing/wrong `Authorization: Bearer <token>` header
- **Not** OAuth/JWT/multi-user auth — there is exactly one caller identity that matters right now
  ("someone with the right secret may drive this instance's Plan/Implement loop"), and a bearer
  token is the honest match for that, not an over-built auth system for a single-tenant server
- TLS termination (a reverse proxy like Caddy/nginx, or the cloud provider's load balancer) so the
  bearer token itself isn't sent in plaintext over the network

This is a small, self-contained piece of work — a follow-up ticket, not bundled into whichever
ticket does the actual first real deployment, since it's a prerequisite for *any* deployment
target, not specific to one.

## What this doc does NOT decide

- **Which cloud provider / which repo deploys first.** No real target was named in the original
  ask — this is the architecture answer, not a deployment ticket. Open a dedicated ticket
  (`docs/plans/0006-...` if the design has real new decisions, or just an `ADLC` Task if it's
  pure execution of this spec) once a specific repo + provider is chosen.
- **Secrets management for the bearer token / `JIRA_API_TOKEN` / `LINEAR_API_KEY` in the cloud**
  (a cloud secrets manager vs. an encrypted `.env` vs. something else) — provider-specific,
  decide when a provider is chosen.
- **Whether `SYMPHONY_WORKFLOW_API_URL` (implied above) actually gets added to config.py**, and
  whether `.claude/hooks/session-start-check-api.sh`'s hardcoded `127.0.0.1:8811` needs to become
  configurable — real code changes, deferred to the implementation ticket.

## Definition of done (recap from intent.md)

This spec + `intent.md` are the full deliverable for `ADLC-58`. No code in this ticket.
