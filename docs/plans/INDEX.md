# Planning cycle index

See [`README.md`](README.md) for the convention. Newest last.

| Folder | Summary |
|---|---|
| [`0001-symphony-poc`](0001-symphony-poc/) | The original POC (Phases 0–5): Symphony-inspired Plan/Implement loop, Jira integration as tool calls, auto-discovery poller (auto-Plan only, never auto-Implement). Migrated here from the old top-level `docs/intent.md`/`docs/spec.md`/`docs/plan.md` (`ADLC-43`). |
| [`0002-richer-polling-triage`](0002-richer-polling-triage/) | Design for `ADLC-29`: a throttled, batched Claude Agent SDK triage step in front of the cheap JQL poll (plan/skip/flag a newly-discovered ticket before committing to a full auto-Plan). Built and merged. |
| [`0003-portability-validation-nexjs`](0003-portability-validation-nexjs/) | Record of `ADLC-41`/`42`: a real Plan run against `ai-coding-nexjs-template` (project `NXJS`) — confirmed the plan reads the target repo's own conventions, and surfaced two real bugs (`DEFAULT_PLAN_MAX_TURNS` too low for real-world repos; `AgentRunResult.text` can be empty with structured output populated). |
