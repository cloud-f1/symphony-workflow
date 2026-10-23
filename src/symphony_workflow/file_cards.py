"""`file-cards`: file a batch of cards from a manifest, safely repeatable (plan 0010, ADLC-418).

A manifest lists the cards one piece of work should leave behind. Running it again must not make a
second card, must not leave a card in the wrong state, and must not skip one. The three guarantees:

- **Identity** is a label `fu-<id>`, never the summary (summaries get edited). One card per label;
  two cards on one label is a hard error for that item.
- **Read back**: every card created or adopted is read by key (a GET, not a search: the JQL index
  lags a write by a few seconds) and compared with the manifest. A mismatch is an error, not a log.
- **Forward only**: status moves To Do -> In Progress -> Done through workflow transitions; a card
  already further along (or Done) is reported and left alone. A Done card is never reopened.

The whole manifest is validated first and every problem is listed at once; a bad manifest makes no
Jira call. Dry-run is the default (reads only); `apply` writes. One failing item does not stop the
others.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel

from symphony_workflow.client import (
    JiraError,
    ReadBackError,
    raise_on_read_back_mismatch,
)
from symphony_workflow.models import Issue, IssueCreateResult

ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,48}$")
PROJECT_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+$")
KEY_PATTERN = re.compile(r"^[A-Z][A-Z0-9]+-[0-9]+$")
TYPES = ("Story", "Subtask")
STATUSES = ("To Do", "In Progress", "Done")
LABEL_PREFIX = "fu-"
_ITEM_FIELDS = {"id", "summary", "description", "type", "parent", "status", "key"}
_TOP_FIELDS = {"project", "default_parent", "items"}
# Jira's statusCategory.key, which survives a renamed workflow, mapped to the target statuses.
_CATEGORY_RANK = {"new": 0, "indeterminate": 1, "done": 2}


class ManifestError(ValueError):
    """The manifest cannot be read or is invalid; `problems` lists every reason."""

    def __init__(self, problems: list[str]) -> None:
        self.problems = problems
        super().__init__("; ".join(problems))


class CardClient(Protocol):
    """The slice of `JiraClient` this module uses (a fake stands in for it in tests)."""

    def find_by_label(self, project_key: str, label: str) -> list[Issue]: ...

    def get_issue(self, key: str) -> Issue: ...

    def create_issue(
        self,
        project_key: str,
        summary: str,
        description: str | None = None,
        issue_type: str = "Task",
        *,
        labels: list[str] | None = None,
        parent: str | None = None,
    ) -> IssueCreateResult: ...

    def update_issue(self, key: str, *, labels_add: list[str] | None = None) -> None: ...

    def transition_issue(self, key: str, transition_name: str) -> None: ...


class Item(BaseModel):
    id: str
    summary: str | None = None
    description: str | None = None
    type: str = "Story"
    parent: str | None = None
    status: str | None = None
    key: str | None = None

    @property
    def label(self) -> str:
        return f"{LABEL_PREFIX}{self.id}"


class Manifest(BaseModel):
    project: str
    default_parent: str | None = None
    items: list[Item]


def validate_manifest(raw: Any) -> list[str]:
    """Every problem with `raw`, at once. An empty list means it is valid."""
    if not isinstance(raw, dict):
        return ["the manifest must be a JSON object"]
    problems = [f"unknown top-level field {name!r}" for name in sorted(set(raw) - _TOP_FIELDS)]
    project = raw.get("project")
    if not isinstance(project, str) or not PROJECT_PATTERN.match(project):
        problems.append("project must be a Jira project key like ADLC")
        project = None
    default_parent = raw.get("default_parent")
    if default_parent is not None and (
        not isinstance(default_parent, str) or not KEY_PATTERN.match(default_parent)
    ):
        problems.append("default_parent must be a card key like ADLC-12")
        default_parent = None
    items = raw.get("items")
    if not isinstance(items, list) or not items:
        problems.append("items must be a non-empty list")
        return problems
    seen: dict[str, int] = {}
    for index, item in enumerate(items, start=1):
        where = f"item {index}"
        if not isinstance(item, dict):
            problems.append(f"{where} must be an object")
            continue
        item_id = item.get("id")
        if isinstance(item_id, str) and ID_PATTERN.match(item_id):
            where = f"item {index} ({item_id})"
            if item_id in seen:
                problems.append(f"{where}: id repeats item {seen[item_id]}")
            seen.setdefault(item_id, index)
        else:
            problems.append(f"{where}: id must match {ID_PATTERN.pattern}")
        problems.extend(f"{where}: unknown field {n!r}" for n in sorted(set(item) - _ITEM_FIELDS))
        problems.extend(_item_problems(where, item, project, default_parent))
    return problems


def _item_problems(
    where: str, item: dict[str, Any], project: str | None, default_parent: str | None
) -> list[str]:
    problems: list[str] = []
    issue_type = item.get("type", "Story")
    if issue_type not in TYPES:
        problems.append(f"{where}: type must be one of {', '.join(TYPES)}")
    status = item.get("status")
    if status is not None and status not in STATUSES:
        problems.append(f"{where}: status must be one of {', '.join(STATUSES)}")
    for field in ("key", "parent"):
        value = item.get(field)
        if value is not None and (not isinstance(value, str) or not KEY_PATTERN.match(value)):
            problems.append(f"{where}: {field} must be a card key like ADLC-12")
    key = item.get("key")
    if isinstance(key, str) and project and not key.startswith(f"{project}-"):
        problems.append(f"{where}: key {key} is not in project {project}")
    if key is None:  # a card that will be created needs its text
        for field in ("summary", "description"):
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                problems.append(f"{where}: {field} is required for a card that will be created")
        if issue_type == "Subtask" and not (item.get("parent") or default_parent):
            problems.append(f"{where}: a Subtask needs a parent (or a default_parent)")
    return problems


def load_manifest(path: Path) -> Manifest:
    """Read and validate the manifest at `path`; raise `ManifestError` with every problem."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ManifestError([f"cannot read {path}: {exc.strerror or exc}"]) from exc
    except json.JSONDecodeError as exc:
        raise ManifestError([f"{path} is not valid JSON: {exc}"]) from exc
    return parse_manifest(raw)


def parse_manifest(raw: Any) -> Manifest:
    problems = validate_manifest(raw)
    if problems:
        raise ManifestError(problems)
    return Manifest.model_validate(raw)


Action = Literal["create", "exists", "adopt", "error"]


class ItemResult(BaseModel):
    id: str
    action: Action
    key: str | None = None
    # What happened or, in a dry run, what would: created / exists / adopted / left alone / moved.
    notes: list[str] = []
    error: str | None = None
    created: bool = False


class Report(BaseModel):
    applied: bool
    items: list[ItemResult]

    @property
    def failed(self) -> bool:
        return any(item.action == "error" for item in self.items)


def _rank(issue: Issue) -> int:
    return _CATEGORY_RANK.get(issue.status_category or "", 0)


def file_cards(manifest: Manifest, client: CardClient, *, apply: bool = False) -> Report:
    """Run every item of an already-validated manifest. Reads only unless `apply`."""
    results = []
    for item in manifest.items:
        try:
            results.append(_run_item(manifest, item, client, apply))
        except JiraError as exc:  # includes ReadBackError; one failure does not stop the rest
            results.append(ItemResult(id=item.id, action="error", key=item.key, error=str(exc)))
    return Report(applied=apply, items=results)


def _run_item(manifest: Manifest, item: Item, client: CardClient, apply: bool) -> ItemResult:
    parent = item.parent or manifest.default_parent
    notes: list[str] = []
    if item.key:
        card = client.get_issue(item.key)  # a missing card is a 404 here: nothing is created
        others = [h.key for h in client.find_by_label(manifest.project, item.label)]
        if others and others != [card.key]:
            raise ReadBackError(f"label {item.label!r} is already on {', '.join(others)}")
        if item.label in card.labels:
            action: Action = "exists"
        else:
            action = "adopt"
            notes.append(f"add label {item.label}" if not apply else f"added label {item.label}")
            if apply:
                client.update_issue(card.key, labels_add=[item.label])
                card = client.get_issue(card.key)
                raise_on_read_back_mismatch(card, label=item.label)
    else:
        hits = client.find_by_label(manifest.project, item.label)  # 2+ hits raise
        if hits:
            card, action = hits[0], "exists"
        elif not apply:
            return ItemResult(
                id=item.id,
                action="create",
                notes=[f"would create {item.type} with label {item.label}"],
            )
        else:
            assert item.summary is not None and item.description is not None  # validated
            made = client.create_issue(
                manifest.project,
                item.summary,
                item.description,
                item.type,
                labels=[item.label],
                parent=parent,
            )
            card = client.get_issue(made.key)
            raise_on_read_back_mismatch(card, summary=item.summary, parent=parent, label=item.label)
            return _finish(item, client, card, "create", ["created"], apply, created=True)
    if item.summary and card.summary != item.summary:
        notes.append(f"summary differs ({card.summary!r}), left as is")
    if parent and card.parent != parent:
        notes.append(f"parent is {card.parent!r}, manifest says {parent!r}; left as is")
    return _finish(item, client, card, action, notes, apply)


def _finish(
    item: Item,
    client: CardClient,
    card: Issue,
    action: Action,
    notes: list[str],
    apply: bool,
    *,
    created: bool = False,
) -> ItemResult:
    """Move the card forward to the manifest status, never backward, and read it back."""
    if item.status:
        target = STATUSES.index(item.status)
        current = _rank(card)
        if target < current:
            notes.append(f"already {card.status}, manifest says {item.status}; left as is")
        elif target > current:
            if not apply:
                notes.append(f"would move {card.status} -> {item.status}")
            else:
                _move(client, card.key, current, target)
                moved = client.get_issue(card.key)
                if _rank(moved) != target:
                    raise ReadBackError(
                        f"{card.key} is {moved.status!r} after moving to {item.status!r}"
                    )
                notes.append(f"moved {card.status} -> {moved.status}")
    return ItemResult(id=item.id, action=action, key=card.key, notes=notes, created=created)


def _move(client: CardClient, key: str, current: int, target: int) -> None:
    """Try the target transition directly, then step through the ones in between."""
    try:
        client.transition_issue(key, STATUSES[target])
        return
    except JiraError as exc:
        if exc.status_code != 404 or target - current < 2:
            raise
    for step in range(current + 1, target + 1):
        client.transition_issue(key, STATUSES[step])


def write_keys(path: Path, report: Report) -> int:
    """Store the key of every card this run created into its manifest item, so later runs adopt by
    key. Returns how many were written. Only `key` is added; the rest of the file is kept."""
    created = {r.id: r.key for r in report.items if r.created and r.key}
    if not created:
        return 0
    raw = json.loads(path.read_text(encoding="utf-8"))
    count = 0
    for item in raw["items"]:
        if item.get("id") in created and not item.get("key"):
            item["key"] = created[item["id"]]
            count += 1
    path.write_text(json.dumps(raw, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return count
