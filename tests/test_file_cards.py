"""file-cards (plan 0010): a fake client with switches, so each guard fails for one reason."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from symphony_workflow.client import JiraError, ReadBackError
from symphony_workflow.file_cards import (
    ManifestError,
    file_cards,
    load_manifest,
    parse_manifest,
    validate_manifest,
    write_keys,
)
from symphony_workflow.models import Issue, IssueCreateResult

CATEGORY = {"To Do": "new", "In Progress": "indeterminate", "Done": "done"}


class FakeClient:
    def __init__(self) -> None:
        self.cards: dict[str, Issue] = {}
        self.writes: list[str] = []
        self.next_number = 100
        # switches: each makes the read-back fail for exactly one reason
        self.drop_label_on_create = False
        self.wrong_parent = False
        self.no_transition = False
        self.fail_create_for: set[str] = set()
        self.no_direct_done = False
        self.drop_label_on_update = False

    def add(self, key: str, summary: str = "S", status: str = "To Do", labels=None, parent=None):
        self.cards[key] = Issue(
            key=key,
            id=key,
            summary=summary,
            status=status,
            status_category=CATEGORY[status],
            labels=list(labels or []),
            parent=parent,
        )

    def find_by_label(self, project_key: str, label: str) -> list[Issue]:
        hits = [c for c in self.cards.values() if label in c.labels]
        if len(hits) > 1:
            raise ReadBackError(f"Label {label!r} is on more than one card")
        return hits

    def get_issue(self, key: str) -> Issue:
        if key not in self.cards:
            raise JiraError(404, "Issue does not exist")
        return self.cards[key]

    def create_issue(
        self, project_key, summary, description=None, issue_type="Task", *, labels=None, parent=None
    ):
        if summary in self.fail_create_for:
            raise JiraError(500, "boom")
        self.writes.append("create")
        self.next_number += 1
        key = f"{project_key}-{self.next_number}"
        kept = [] if self.drop_label_on_create else list(labels or [])
        self.add(key, summary, labels=kept, parent="ADLC-999" if self.wrong_parent else parent)
        return IssueCreateResult(key=key, id=key, url="u")

    def update_issue(self, key, *, labels_add=None):
        self.writes.append("update")
        if not self.drop_label_on_update:
            self.cards[key].labels.extend(labels_add or [])

    def transition_issue(self, key, transition_name):
        self.writes.append(f"transition:{transition_name}")
        if self.no_direct_done and transition_name == "Done" and self.cards[key].status == "To Do":
            raise JiraError(404, "No transition named 'Done'")
        if self.no_transition:
            return
        card = self.cards[key]
        card.status, card.status_category = transition_name, CATEGORY[transition_name]


def manifest(*items: dict, default_parent: str | None = "ADLC-1") -> dict:
    out: dict = {"project": "ADLC", "items": list(items)}
    if default_parent:
        out["default_parent"] = default_parent
    return out


def item(id_: str = "one", **extra) -> dict:
    return {"id": id_, "summary": f"Card {id_}", "description": "d", **extra}


def run(raw: dict, client: FakeClient, apply: bool = True):
    return file_cards(parse_manifest(raw), client, apply=apply)


def test_default_is_dry_run_and_writes_nothing() -> None:
    client = FakeClient()
    report = file_cards(parse_manifest(manifest(item())), client)
    assert report.applied is False and client.writes == [] and client.cards == {}
    assert report.items[0].action == "create"


def test_apply_creates_with_label_parent_and_status_and_rerun_creates_nothing() -> None:
    client = FakeClient()
    raw = manifest(item(status="In Progress"))
    first = run(raw, client)
    key = first.items[0].key
    assert first.items[0].created and not first.failed
    card = client.cards[key]
    assert card.labels == ["fu-one"] and card.parent == "ADLC-1" and card.status == "In Progress"
    writes_after_first = list(client.writes)
    second = run(raw, client)
    assert client.writes == writes_after_first  # nothing created, nothing moved
    assert second.items[0].action == "exists" and second.items[0].key == key


def test_to_do_is_not_moved() -> None:
    client = FakeClient()
    run(manifest(item(status="To Do")), client)
    assert not [w for w in client.writes if w.startswith("transition")]


def test_status_steps_through_in_progress_when_done_is_not_direct() -> None:
    client = FakeClient()
    client.no_direct_done = True
    report = run(manifest(item(status="Done")), client)
    assert client.cards[report.items[0].key].status == "Done"
    assert client.writes[-2:] == ["transition:In Progress", "transition:Done"]


def test_adopt_by_key_adds_the_label_and_never_creates() -> None:
    client = FakeClient()
    client.add("ADLC-5", "Hand made")
    report = run(manifest(item(key="ADLC-5")), client)
    assert report.items[0].action == "adopt" and report.items[0].key == "ADLC-5"
    assert client.cards["ADLC-5"].labels == ["fu-one"]
    assert "create" not in client.writes


def test_adopt_read_back_catches_a_label_that_did_not_stick() -> None:
    client = FakeClient()
    client.drop_label_on_update = True
    client.add("ADLC-5", "Hand made")
    report = run(manifest(item(key="ADLC-5")), client)
    assert report.failed and "label 'fu-one' is missing" in (report.items[0].error or "")


def test_adopting_a_key_that_does_not_exist_is_an_error_and_creates_nothing() -> None:
    client = FakeClient()
    report = run(manifest(item(key="ADLC-77")), client)
    assert report.failed and "does not exist" in (report.items[0].error or "")
    assert client.writes == []


def test_two_cards_with_one_label_is_a_hard_error() -> None:
    client = FakeClient()
    client.add("ADLC-5", labels=["fu-one"])
    client.add("ADLC-6", labels=["fu-one"])
    report = run(manifest(item()), client)
    assert report.failed and "more than one" in (report.items[0].error or "")
    assert "create" not in client.writes


def test_adopting_when_the_label_is_on_another_card_is_an_error() -> None:
    client = FakeClient()
    client.add("ADLC-5")
    client.add("ADLC-6", labels=["fu-one"])
    report = run(manifest(item(key="ADLC-5")), client)
    assert report.failed and "already on ADLC-6" in (report.items[0].error or "")
    assert client.cards["ADLC-5"].labels == []


def test_never_reopens_a_done_card() -> None:
    client = FakeClient()
    client.add("ADLC-5", "Card one", status="Done", labels=["fu-one"], parent="ADLC-1")
    report = run(manifest(item(status="To Do")), client)
    assert client.cards["ADLC-5"].status == "Done" and client.writes == []
    assert report.items[0].notes == ["already Done, manifest says To Do; left as is"]


def test_summary_drift_is_reported_not_overwritten() -> None:
    client = FakeClient()
    client.add("ADLC-5", "Renamed by a human", labels=["fu-one"], parent="ADLC-1")
    report = run(manifest(item()), client)
    assert client.cards["ADLC-5"].summary == "Renamed by a human" and client.writes == []
    assert "summary differs" in " ".join(report.items[0].notes)
    assert not report.failed


def test_one_failing_item_does_not_stop_the_others() -> None:
    client = FakeClient()
    client.fail_create_for = {"Card bad"}
    report = run(manifest(item("bad"), item("good")), client)
    assert [r.action for r in report.items] == ["error", "create"]
    assert report.failed
    assert any(c.labels == ["fu-good"] for c in client.cards.values())


def test_read_back_catches_a_missing_label() -> None:
    client = FakeClient()
    client.drop_label_on_create = True
    report = run(manifest(item()), client)
    assert report.failed and "label 'fu-one' is missing" in (report.items[0].error or "")


def test_read_back_catches_a_wrong_parent() -> None:
    client = FakeClient()
    client.wrong_parent = True
    report = run(manifest(item()), client)
    assert report.failed and "parent is 'ADLC-999'" in (report.items[0].error or "")


def test_read_back_catches_a_status_that_was_not_reached() -> None:
    client = FakeClient()
    client.no_transition = True
    report = run(manifest(item(status="In Progress")), client)
    assert report.failed and "after moving to 'In Progress'" in (report.items[0].error or "")


def test_validator_reports_every_problem_at_once() -> None:
    raw = {
        "project": "ADLC",
        "default_parent": "nope",
        "extra": 1,
        "items": [
            {"id": "Bad Id", "type": "Epic", "status": "Later"},
            {"id": "dup", "summary": "x", "description": "y", "type": "Subtask"},
            {"id": "dup", "summary": "x", "description": "y", "key": "OTHER-1"},
            "not an object",
        ],
    }
    problems = validate_manifest(raw)
    text = "\n".join(problems)
    for expected in (
        "default_parent must be",
        "unknown top-level field 'extra'",
        "id must match",
        "type must be one of",
        "status must be one of",
        "a Subtask needs a parent",
        "id repeats item 2",
        "key OTHER-1 is not in project",
        "item 4 must be an object",
    ):
        assert expected in text, expected
    assert len(problems) >= 10


def test_a_bad_manifest_makes_no_call_and_load_raises(tmp_path: Path) -> None:
    path = tmp_path / "m.json"
    path.write_text(json.dumps({"project": "ADLC", "items": []}))
    with pytest.raises(ManifestError, match="non-empty"):
        load_manifest(path)


def test_an_unreadable_or_non_json_manifest_is_a_manifest_error(tmp_path: Path) -> None:
    with pytest.raises(ManifestError, match="cannot read"):
        load_manifest(tmp_path / "missing.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(bad)


def test_write_keys_stores_created_keys_and_keeps_the_rest(tmp_path: Path) -> None:
    client = FakeClient()
    raw = manifest(item("a"), item("b"))
    path = tmp_path / "m.json"
    path.write_text(json.dumps(raw))
    report = run(raw, client)
    assert write_keys(path, report) == 2
    saved = json.loads(path.read_text())
    assert [i["key"] for i in saved["items"]] == [r.key for r in report.items]
    assert saved["items"][0]["summary"] == "Card a" and saved["default_parent"] == "ADLC-1"
    assert write_keys(path, run(saved, client)) == 0  # a rerun adopts, writes nothing new


def test_a_lowercase_project_is_a_problem() -> None:
    assert any(
        "project must be" in p for p in validate_manifest({"project": "adlc", "items": [{}]})
    )
