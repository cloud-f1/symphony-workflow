# Spec — Maintainability + architecture improvement plan (`ADLC-88`)

Six sections, matching Alex's six numbered asks. Each ends with a concrete deliverable.

## 1. Unit tests — close the `cli.py` gap, add Protocol-conformance + model tests

**Priority: highest** — this is the one measured number that's actually a real outlier (70% vs.
90%+ everywhere else), and it's the layer a human actually runs by hand most often.

- Add a failure-path test per `cli.py` command: `get`/`comment`/`transition`/`plan`/`implement`
  raising `TrackerError`; `search`/`call`/`decompose`/`sync-decomposition` raising `JiraError`;
  `_client()`/`_jira_client()` raising `ConfigError`/`tracker.ConfigError` when env vars are
  missing or `SYMPHONY_WORKFLOW_TRACKER` is invalid. Same `respx`-mocked-error pattern
  `test_client.py`/`test_linear_client.py` already use — no new test infrastructure needed.
- Add an explicit Protocol-conformance test to `test_tracker.py`:
  `isinstance(JiraClient(cfg), TrackerClient)` and `isinstance(LinearClient(cfg), TrackerClient)`
  both `True` — currently implied by the type hints passing `ruff`/import-time, never actually
  asserted at runtime. Cheap, and it's the test that would catch a future accidental signature
  drift between a concrete client and the Protocol.
- Add `test_models.py` — `models.py` is at 100% coverage today only because every code path
  happens to get exercised indirectly through `test_client.py`/`test_linear_client.py`'s
  fixtures. A dedicated file with edge cases (missing optional fields, both `from_api`/
  `from_linear` on the same model side by side) documents the shared-model contract explicitly
  instead of leaving it implicit.
- **Deliverable**: `cli.py` coverage ≥90%, `uv run pytest` still green, no new external
  dependencies.

## 2. Whole-scope / function-boundary audit

**Finding**: the tracker-agnostic-vs-Jira-only split is implemented twice — `cli.py`'s
`_client()`/`_jira_client()` and `api.py`'s `get_tracker()`/`get_jira_client()` each independently
do `isinstance(client, JiraClient)` and raise their own (differently-worded) error. Two call
sites, two sources of truth for the same decision.

- Move the Jira-only guard into `tracker.py` itself: a `require_jira(client: TrackerClient,
  *, context: str) -> JiraClient` function that both `cli.py` and `api.py` call, raising one
  consistent error message (`context` lets each caller say *what* needs Jira — "the `call`
  command" vs. "`/tools/{name}/call`" — without duplicating the check itself).
- **Secondary finding**: `orchestrator.py` has the same "try a tracker call, log a warning on
  `TrackerError`, never raise" shape in four places (`transition_issue` after plan, after
  implement, `_post_failure_comment`, `poller.py`'s `_post_give_up_comment`). A single
  `_best_effort(fn: Callable[[], None], *, log_msg: str) -> None` helper would remove the
  repeated `try/except TrackerError: logger.warning(...)` block, but this is a smaller win than
  #1 above — the current repetition is at least locally readable, not genuinely confusing.
- **Deliverable**: `require_jira()` in `tracker.py`, both call sites migrated, `cli.py`/`api.py`'s
  duplicated `isinstance` checks removed. The `_best_effort` extraction is optional/lower
  priority — do it only if it doesn't make the four call sites harder to read individually.

## 3. SOLID + design patterns

The existing design already gets the harder principles right — worth stating explicitly so a
future contributor recognizes the pattern instead of reverse-engineering it:

- **Interface Segregation**: `TrackerClient` is deliberately minimal (6 methods) precisely
  *because* `tools.py`/`decompose.py` need Jira's much larger surface and were kept off the
  shared Protocol rather than bloating it — this is ISP working as intended, not an oversight.
- **Dependency Inversion**: `orchestrator.py`/`poller.py` depend on `TrackerClient` (the
  abstraction), never `JiraClient`/`LinearClient` directly.
- **Liskov Substitution**: `JiraClient` and `LinearClient` are freely interchangeable wherever
  `TrackerClient` is expected — proven by the fact `orchestrator.py` needed zero special-casing
  to add the second backend.
- This is a textbook **Strategy pattern** (`TrackerClient` = strategy interface, `JiraClient`/
  `LinearClient` = concrete strategies, `get_tracker_client()` = the selector) and each concrete
  client is also an **Adapter** (adapting Jira's REST shape / Linear's GraphQL shape to one
  common interface). Naming this explicitly in `tracker.py`'s module docstring costs nothing and
  saves a future reader from re-deriving "oh, this is Strategy" from scratch.
- **Opportunity, deliberately deferred**: `get_tracker_client()`'s `if backend == "jira" / elif
  == "linear"` chain isn't Open/Closed — adding a 3rd backend means editing this function's body.
  A small registry (`TRACKER_BACKENDS: dict[str, Callable[[], TrackerClient]]`, each backend
  module registers itself) would fix this. **Not worth building for 2 backends** — same
  "don't build it preemptively" reasoning as `docs/PORTABILITY.md`'s multi-tenant-poller
  decision. Documented here so it's not re-discovered from scratch if a 3rd backend ever
  actually happens.
- Small, cheap win: `JiraClient` and `LinearClient` both hand-write identical
  `close()`/`__enter__`/`__exit__` boilerplate. A tiny shared mixin (or just noting the
  duplication is intentional, since it's 6 lines and adding a base class for 6 lines has its own
  cost) — flagged as optional, not a real problem.
- **Deliverable**: `tracker.py`'s docstring names the Strategy/Adapter pattern explicitly. The
  registry and mixin items are documented as deferred, not built.

## 4. Maintainability

- **The highest-value item in this whole plan**: bring `.claude/skills/symphony-workflow-ops/
  SKILL.md` (and reconcile its `plugin/skills/` mirror, already flagged as drifted in `ADLC-57`'s
  session-summary and never fixed) up to date with everything shipped since it was last touched
  — `SYMPHONY_WORKFLOW_TRACKER`, the Linear backend's real capabilities/limits, the `ADLC-86`
  Epic-decomposition rule, and the Linear structure mapping (`Project`=repo/`ProjectMilestone`=
  Epic/Issue=Story/sub-issue=Task) from `ADLC-87`. This is the doc a fresh session is told to
  load first — right now it's silent on roughly half of what this repo actually does. See
  section 6 below; this and #6 are the same underlying fix.
- Add a short "Adding a new tracker backend" walkthrough to `README.md` or `CLAUDE.md`,
  parallel to the existing "Adding a new tool" section — `ADLC-57` is exactly the kind of work a
  future session redoes for a 3rd backend, and right now the only record of *how* is scattered
  across `docs/plans/0005-linear-tracker-backend/`'s narrative rather than a direct how-to.
- **Deliverable**: skill docs current (see #6), one new "Adding a tracker backend" doc section.

## 5. Formalize the git-tag release process

**Finding**: the release process is three independent manual steps (bump `pyproject.toml` +
`plugin.json` together, write a CHANGELOG entry, `git tag -a` + push) with no single command
enforcing they happen together — which is exactly how `v0.3.0`/`v0.4.0` ended up saying "tagged"
in the CHANGELOG while `git tag -l` disagreed.

- Add `scripts/release.sh <version>`: bumps both version fields (`pyproject.toml`,
  `plugin/.claude-plugin/plugin.json`) to the given version, fails loudly if `CHANGELOG.md`
  has no `## <version>` heading yet (forcing the changelog entry to be written *before* running
  it, not after), runs `uv run pytest tests/test_version.py` to confirm the sync test passes,
  then creates the annotated tag and prints the exact `git push` commands to run (pushing is a
  separate explicit step, not automated — matches this repo's existing "confirm before pushing"
  posture elsewhere).
- Document the release process (`scripts/release.sh` usage) in `README.md`.
- **Deliverable**: `scripts/release.sh`, a `README.md` "Releasing" section, and (retroactively,
  cheap) `git tag`s for `v0.3.0` and `v0.4.0` if Alex wants the tag history made consistent with
  the CHANGELOG's existing claims — otherwise leave the historical gap as-is and just fix it going
  forward.

## 6. Update the `symphony-workflow-ops` skill for the Linear/decompose workflow

Same underlying gap as #4, called out separately because Alex named it explicitly. Concretely:

- `.claude/skills/symphony-workflow-ops/SKILL.md`: add a section on `SYMPHONY_WORKFLOW_TRACKER`
  (what works on Linear vs. stays Jira-only — the same table `README.md` already has, summarized
  for the skill's "short operational reference" format), the `ADLC-86` rule (decompose before
  building under an Epic), and the `ADLC-87` Linear structure mapping.
- Reconcile the `plugin/skills/symphony-workflow-ops/SKILL.md` mirror at the same time — it was
  already confirmed substantively drifted from the canonical copy back in `ADLC-57`'s session
  summary (a real, un-actioned finding from that session, not new). Update both together in one
  commit so they don't drift further apart while being individually "fixed."
- **Deliverable**: both `SKILL.md` copies mention `SYMPHONY_WORKFLOW_TRACKER`, the decomposition
  rule, and the Linear mapping; a diff between the two copies is reviewed and either reconciled
  or the remaining differences are intentional and noted as such (not silently left as unexplained
  drift).

## Priority order (if not all six get done in one pass)

1. **#6 / #4's skill-doc fix** — highest value-to-effort ratio; it's the one gap that actively
   misleads a fresh session today.
2. **#1's `cli.py` test gap** — concrete, measured, mechanical to close.
3. **#5's release script** — prevents a class of error already observed twice.
4. **#2's `require_jira()` consolidation** — small, real, low-risk refactor.
5. **#3's SOLID documentation** — no code change, pure clarity win.
6. **#3's deferred registry/mixin items** — explicitly not built now.
