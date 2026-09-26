# Intent — Cloud deployment architecture (`ADLC-58`)

## Why

Alex asked (2026-09-26): if we deploy the API server to the cloud so "any cloud code section"
(any Claude Code session — local or cloud, any developer, any repo) can connect to it instead of
each running a local `uv run uvicorn` process, should that be **one shared multi-tenant server**
(different sessions distinguished by a "target tag" per request), or **the API server bundled
together with agent execution as a kind of interpreter**? This doc records the research and the
decision so it isn't re-litigated from scratch later — same convention as `docs/PORTABILITY.md`'s
own "don't re-litigate without updating this section" multi-repo policy.

## What's already decided (don't re-derive this)

`docs/PORTABILITY.md`, written 2026-09-24, already answered the shared-multi-tenant half of this
question for a different but structurally identical trigger (multiple repos adopting
symphony-workflow) and landed on: **one instance per repo, not one shared multi-tenant poller.**
Reasons that still apply unchanged to the cloud-deployment question:

- `TARGET_REPO`, `WORKSPACE_ROOT` are single env vars read once per process — not per-request state
- `RunStore` is in-memory and not partitioned by tenant
- The poller polls exactly one Jira project's JQL (or, after `ADLC-57`, one Linear team) — no
  per-issue repo routing exists anywhere
- Deliberately deferred: "revisit only if running several small processes becomes the actual
  operational pain" — that hasn't changed; what's new is *where* those processes run (a laptop vs
  the cloud), not *how many* or *how they're addressed*.

A cloud deployment doesn't change any of this. "Deploy to the cloud" and "make it multi-tenant"
are two independent questions that got conflated in the original ask — this doc treats them
separately and answers only the first one as in-scope now.

## Non-goals

- **Not building a shared multi-tenant server.** Per the above, that's still deliberately
  deferred, not solved by moving to the cloud.
- **Not implementing anything in this ticket.** This is the intent/spec half of the `ADLC-43`
  planning-cycle convention; `implement.md` gets written when a real deployment actually happens
  (tracked as its own follow-up ticket against a real target, since "the cloud" isn't one specific
  provider/target yet).
- **Not solving `prompts.py`'s target-repo-conventions gap** (`docs/PORTABILITY.md`'s own
  deferred item) — orthogonal to where the process runs.

## The real gap this research surfaced (not originally asked about, but blocking)

`api.py` has **zero authentication** on any endpoint — no API key, no bearer token, nothing.
Today that's safe only because the server binds to `127.0.0.1`: the loopback interface *is* the
entire security boundary. `POST /runs/{id}/implement` runs the Claude Agent SDK with
`permission_mode="acceptEdits"` (real file writes in a git worktree) — an unauthenticated,
network-reachable version of this endpoint is a real remote-code-execution-shaped exposure, not a
theoretical one. This must be fixed *before* any deployment that binds beyond loopback,
independent of the multi-tenant question. See `spec.md` for the minimum-viable fix.

## Definition of done (for this doc)

- `spec.md` records: the shared-vs-per-repo decision (per-repo, cloud-hosted), the concrete
  deployment shape (one small container/VM per adopting repo, each with its own port/`.env`/git
  checkout), and the minimum-viable auth fix (shared-secret bearer token) with enough detail that
  a future ticket can implement it without re-deriving the reasoning.
- `INDEX.md` gets a row for this folder.
- No code changes in this ticket.
