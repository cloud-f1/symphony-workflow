"""Roll a finished Subtask up the Epic -> Story -> Subtask tree.

Rule (the user's, 2026-10-08): a Subtask is completed -> mark it Done; if ALL of its
Story's Subtasks are Done, the Story is Done; if ALL of the Epic's Stories are Done, the
Epic is Done. Forward-only (nothing is ever reopened), stops at the first level that is
not fully done, idempotent (re-running changes nothing once everything is Done).

Jira-only: it needs the `parent` field and child search, which `TrackerClient` does not
model. The one subtlety is Jira's search being eventually consistent -- right after we
transition a Subtask, a child search may still report it as not done. So the keys we have
just (really or, in a dry run, virtually) completed are remembered and treated as done
when judging their parent, instead of trusting the search to have caught up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from symphony_workflow.client import JiraClient, JiraError
from symphony_workflow.models import HierarchyIssue

DONE = "Done"

_SUBTASK, _STORY, _EPIC = -1, 0, 1


class RollupNotFound(Exception):
    """The starting key does not exist (or is malformed)."""


class RollupAborted(Exception):
    """A Jira call failed mid-roll-up. `result` says exactly what had been transitioned
    before the failure, so a partial application is never silent."""

    def __init__(self, result: RollupResult, cause: JiraError) -> None:
        self.result = result
        self.cause = cause
        super().__init__(str(cause))


@dataclass
class Transition:
    key: str
    from_status: str
    to: str = DONE

    def as_dict(self) -> dict[str, str]:
        return {"key": self.key, "from": self.from_status, "to": self.to}


@dataclass
class RollupResult:
    key: str
    dry_run: bool = False
    transitions: list[Transition] = field(default_factory=list)
    stopped_at: dict[str, str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "dry_run": self.dry_run,
            "transitions": [t.as_dict() for t in self.transitions],
            "stopped_at": self.stopped_at,
        }


def hierarchy_level(issue: HierarchyIssue) -> int:
    """-1 Subtask, 0 Story/Task, 1 Epic. Prefers Jira's own `hierarchyLevel`; falls back
    to the issue type's name for responses that omit it."""
    if issue.hierarchy_level is not None:
        if issue.hierarchy_level < 0:
            return _SUBTASK
        return _EPIC if issue.hierarchy_level >= 1 else _STORY
    name = issue.issue_type.strip().lower().replace("-", "").replace(" ", "")
    if name == "subtask":
        return _SUBTASK
    if name == "epic":
        return _EPIC
    return _STORY


def rollup(client: JiraClient, key: str, *, dry_run: bool = False) -> RollupResult:
    result = RollupResult(key=key, dry_run=dry_run)
    completed: set[str] = set()  # keys we have just made Done (really or virtually)

    def mark_done(issue: HierarchyIssue) -> None:
        if issue.is_done or issue.key in completed:
            return
        if not dry_run:
            client.transition_issue(issue.key, DONE)
        completed.add(issue.key)
        result.transitions.append(Transition(issue.key, issue.status))

    def children_not_done(issue: HierarchyIssue) -> tuple[int, int]:
        kids = client.list_children(issue.key)
        pending = [c for c in kids if not (c.is_done or c.key in completed)]
        return len(pending), len(kids)

    def stop(issue: HierarchyIssue, reason: str) -> RollupResult:
        result.stopped_at = {"key": issue.key, "reason": reason}
        return result

    try:
        try:
            start = client.get_hierarchy_issue(key)
        except JiraError as exc:
            if exc.status_code in (400, 404):
                raise RollupNotFound(f"{key}: {exc}") from exc
            raise

        level = hierarchy_level(start)
        current: HierarchyIssue | None = start

        if level == _SUBTASK:
            mark_done(start)
            if not start.parent_key:
                return stop(start, "subtask has no parent story")
            current = client.get_hierarchy_issue(start.parent_key)
            level = hierarchy_level(current)

        if level == _STORY and current is not None:
            pending, total = children_not_done(current)
            if total == 0:
                if not current.is_done:
                    return stop(current, "story has no subtasks and is not Done itself")
            elif pending:
                return stop(current, f"{pending} of {total} subtask(s) not Done")
            else:
                mark_done(current)
            if not current.parent_key:
                return result
            current = client.get_hierarchy_issue(current.parent_key)
            level = hierarchy_level(current)

        if level == _EPIC and current is not None:
            pending, total = children_not_done(current)
            if total == 0:
                if not current.is_done:
                    return stop(current, "epic has no stories and is not Done itself")
            elif pending:
                return stop(current, f"{pending} of {total} child issue(s) not Done")
            else:
                mark_done(current)
        elif current is not None and level == _STORY:
            return stop(current, "parent is not an epic (nested stories are not rolled up)")
    except JiraError as exc:
        raise RollupAborted(result, exc) from exc
    return result
