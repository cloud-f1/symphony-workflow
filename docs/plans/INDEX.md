# Planning cycle index

See [`README.md`](README.md) for the convention. Newest last.

| Folder | Summary |
|---|---|
| [`0001-symphony-poc`](0001-symphony-poc/) | The original POC (Phases 0–5): Symphony-inspired Plan/Implement loop, Jira integration as tool calls, auto-discovery poller (auto-Plan only, never auto-Implement). Migrated here from the old top-level `docs/intent.md`/`docs/spec.md`/`docs/plan.md` (`ADLC-43`). |
| [`0002-richer-polling-triage`](0002-richer-polling-triage/) | Design for `ADLC-29`: a throttled, batched Claude Agent SDK triage step in front of the cheap JQL poll (plan/skip/flag a newly-discovered ticket before committing to a full auto-Plan). Design only — not built yet, blocked on Epic 2's first wave shipping first. |
