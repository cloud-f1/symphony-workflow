"""The tracker-agnostic surface `orchestrator.py`/`poller.py` actually need to
drive the Plan/Implement run-loop -- and nothing more. `JiraClient` already
satisfies this Protocol unchanged; `LinearClient` is a new, independent
implementation. Deliberately NOT the full surface either client exposes:
`tools.py` (the AI-agent tool-call layer), `decompose.py`/
`sync_decomposition.py`, and raw-JQL search are genuinely Jira-shaped
(Epic/parent linking, sprint/story-points fields, JQL) and stay typed against
`JiraClient` specifically -- see docs/plans/0005-linear-tracker-backend/
intent.md's non-goals for why porting those isn't just more of the same work.

`get_tracker_client()` is the one place `SYMPHONY_WORKFLOW_TRACKER` is read --
everything else imports the Protocol, not a concrete backend, so adding a
third backend later never touches orchestrator.py/poller.py/cli.py's
tracker-agnostic commands.

Named explicitly (`ADLC-111`), so a future reader recognizes the design
instead of reverse-engineering it: this is a textbook **Strategy** pattern
(`TrackerClient` = the strategy interface, `JiraClient`/`LinearClient` = the
concrete strategies, `get_tracker_client()` = the selector) and each concrete
client is also an **Adapter** (`JiraClient` adapts Jira's REST shape,
`LinearClient` adapts Linear's GraphQL shape, to the one common interface
callers actually use).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from symphony_workflow.models import CurrentUser, Issue, SearchResult

if TYPE_CHECKING:
    # Only for require_jira()'s return type annotation -- the actual runtime
    # check does its own lazy import, same reasoning as get_tracker_client().
    from symphony_workflow.client import JiraClient

TRACKER_ENV = "SYMPHONY_WORKFLOW_TRACKER"
DEFAULT_TRACKER = "jira"


class TrackerError(RuntimeError):
    """Common base for JiraError/LinearError -- code that should work with
    either backend (orchestrator.py, poller.py, cli.py's tracker-agnostic
    commands) catches this, not a backend-specific subclass.
    """


class ConfigError(RuntimeError):
    """Raised by get_tracker_client() for an unknown SYMPHONY_WORKFLOW_TRACKER
    value. Deliberately its own exception, not config.ConfigError -- that one
    means "required env var missing", this one means "env var present but
    doesn't name a real backend", which is a different failure a caller might
    want to handle differently (e.g. list valid choices).
    """


@runtime_checkable
class TrackerClient(Protocol):
    """Structural (not nominal) interface -- JiraClient satisfies this without
    inheriting from it. `runtime_checkable` so `isinstance(client, TrackerClient)`
    works where that's useful, though the honest place for backend-specific
    behavior (`require_jira()`, used by cli.py's Jira-only commands and
    api.py's /tools endpoints, ADLC-106) checks `isinstance(client, JiraClient)`
    specifically, not the absence of this Protocol.

    `close()`/`__enter__`/`__exit__` are hand-written identically on both
    `JiraClient` and `LinearClient` (`ADLC-111`) -- an accepted duplication,
    not an oversight. A shared mixin base class would remove ~6 duplicated
    lines per client at the cost of both classes no longer being pure,
    independent `TrackerClient` implementations (see this module's docstring
    on why that independence matters: `JiraClient` predates this Protocol
    entirely and needed zero changes to satisfy it). Revisit only if a third
    backend makes the duplication three-way instead of two-way.
    """

    def close(self) -> None: ...
    def __enter__(self) -> TrackerClient: ...
    def __exit__(self, *exc_info: object) -> None: ...

    def get_myself(self, *, timeout: float | None = None) -> CurrentUser: ...
    def get_issue(self, key: str) -> Issue: ...
    def add_comment(self, key: str, body: str) -> None: ...
    def transition_issue(self, key: str, state_name: str) -> None: ...
    def issue_url(self, key: str) -> str: ...

    def search_ready_tickets(self, *, max_results: int = 50) -> SearchResult:
        """Backend-native equivalent of "give me unstarted, top-level tickets
        for this instance's configured project/team, oldest first" -- Jira
        builds this as a JQL string (SYMPHONY_WORKFLOW_JIRA_PROJECT, status
        "To Do", issuetype not in Epic/Subtask); Linear builds it as a
        GraphQL filter (SYMPHONY_WORKFLOW_LINEAR_TEAM, state.type
        "unstarted", parent is null -- Linear has no Epic issue type, so
        "top-level" is expressed as "no parent issue" instead). poller.py
        calls this without knowing which.
        """
        ...


def get_tracker_client() -> TrackerClient:
    """Read SYMPHONY_WORKFLOW_TRACKER (default "jira") and construct the
    matching client from its own *Config.from_env(). Backend modules are
    imported lazily, inside each branch, so importing tracker.py itself never
    requires both backends' dependencies to be configured -- only the one
    actually selected.

    The if/elif chain below is NOT Open/Closed (`ADLC-111`) -- adding a third
    backend means editing this function's body, not just adding a new file.
    A small registry (`TRACKER_BACKENDS: dict[str, Callable[[], TrackerClient]]`,
    each backend module registering itself) would fix that. Deliberately not
    built for two backends -- same "don't build it preemptively" reasoning as
    `docs/PORTABILITY.md`'s multi-tenant-poller decision. Revisit if a third
    backend actually happens; documented here so that's not re-discovered
    from scratch.
    """
    backend = os.environ.get(TRACKER_ENV, DEFAULT_TRACKER).strip().lower()

    if backend == "jira":
        from symphony_workflow.client import JiraClient
        from symphony_workflow.config import JiraConfig

        return JiraClient(JiraConfig.from_env())

    if backend == "linear":
        from symphony_workflow.config import LinearConfig
        from symphony_workflow.linear_client import LinearClient

        return LinearClient(LinearConfig.from_env())

    raise ConfigError(
        f"Unknown {TRACKER_ENV}={backend!r} -- expected 'jira' or 'linear'"
    )


def require_jira(client: TrackerClient, *, context: str) -> JiraClient:
    """Assert `client` is a `JiraClient`, raising `ConfigError` (naming
    `context`) if not -- the shared half of the Jira-only guard both
    `cli.py`'s Jira-only commands and `api.py`'s `/tools` endpoints need
    (`ADLC-106`).

    This only covers "check an already-selected client" -- `cli.py`'s
    `_jira_client()` does something genuinely different for a one-shot CLI
    invocation with no shared client to check: it calls `get_tracker_client()`
    (so it still honors `SYMPHONY_WORKFLOW_TRACKER`, fixing a real
    inconsistency -- previously it constructed a fresh `JiraClient` directly
    from `JiraConfig.from_env()`, silently ignoring the env var entirely),
    then passes the result through this same function. `api.py`'s
    `get_jira_client()` passes the server's one already-constructed
    `app.state.tracker_client` straight through. Both end up asserting the
    exact same thing, worded the same way -- that's the actual consolidation,
    not forcing two different construction mechanisms into one shape.

    Import of `JiraClient` is local (not at module level) for the same
    lazy-import reason `get_tracker_client()`'s branches are local -- this
    module stays a leaf that never requires both backends' dependencies just
    to be imported.
    """
    from symphony_workflow.client import JiraClient

    if isinstance(client, JiraClient):
        return client
    raise ConfigError(
        f"{context} is Jira-only; the configured {TRACKER_ENV} backend is not Jira."
    )
