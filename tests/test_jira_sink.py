"""Neutral plan -> Jira (ADLC-275): create, idempotent re-run, adoption, dry run, status."""

from __future__ import annotations

import pytest

from symphony_workflow.client import JiraError
from symphony_workflow.jira_sink import (
    STATUS_NAME,
    JiraStatusBackend,
    remote_status,
    sync_plan_to_jira,
)
from symphony_workflow.models import HierarchyIssue, IssueCreateResult, Transition
from symphony_workflow.plan import Category, Plan, Task, Todo, extract_marker
from symphony_workflow.status_sync import Card, SyncState, sync_status_with

CATEGORY = {"new": "To Do", "indeterminate": "In Progress", "done": "Done"}
BY_NAME = {v: k for k, v in CATEGORY.items()}


class FakeJira:
    """In-memory Jira. Like the real v3 search, listings carry no description: a caller has to
    re-read each issue (the v2 `get_hierarchy_issue`) to see the marker."""

    def __init__(self) -> None:
        self.issues: dict[str, dict] = {}
        self.calls: list[str] = []
        self.fail_on: str | None = None
        self._counter = 0  # real Jira never reuses a key, even after a delete

    def _hi(self, key: str, *, with_description: bool) -> HierarchyIssue:
        i = self.issues[key]
        return HierarchyIssue(
            key=key, summary=i["summary"], status=CATEGORY[i["cat"]], status_category=i["cat"],
            issue_type=i["type"], parent_key=i["parent"],
            description=i["description"] if with_description else None,
        )  # fmt: skip

    def get_hierarchy_issue(self, key):
        if key not in self.issues:
            raise JiraError(404, f"Issue does not exist: {key}")
        return self._hi(key, with_description=True)

    def search_hierarchy(self, jql):
        return [self._hi(k, with_description=False) for k, i in self.issues.items()
                if i["type"] == "Epic"]  # fmt: skip

    def list_children(self, parent_key):
        return [self._hi(k, with_description=False) for k, i in self.issues.items()
                if i["parent"] == parent_key]  # fmt: skip

    def create_issue(self, project_key, summary, description=None, issue_type="Task", *,
                     parent=None, **kw):  # fmt: skip
        self.calls.append("create")
        if self.fail_on == summary:
            raise JiraError(500, "boom")
        self._counter += 1
        key = f"{project_key}-{self._counter}"
        self.issues[key] = {"summary": summary, "description": description, "type": issue_type,
                            "parent": parent, "cat": "new"}  # fmt: skip
        return IssueCreateResult(key=key, id=str(self._counter), url=f"https://x/browse/{key}")

    def update_issue(self, key, *, summary=None, description=None, parent=None):
        self.calls.append("update")
        if summary is not None:
            self.issues[key]["summary"] = summary
        if description is not None:
            self.issues[key]["description"] = description

    def list_transitions(self, key):
        return [Transition(id=n, name=n) for n in CATEGORY.values()]

    def transition_issue(self, key, name):
        self.calls.append(f"transition:{name}")
        if name not in BY_NAME:
            raise JiraError(404, f"No transition named {name!r}")
        self.issues[key]["cat"] = BY_NAME[name]


def _plan() -> Plan:
    tasks = [
        Task(id="c1.t1", title="Sign in", description="Let users in", acceptance_criteria=["a"],
             status="in_progress",
             todos=[Todo(id="c1.t1.d1", title="Build", status="done"),
                    Todo(id="c1.t1.d2", title="Tests: it")]),
        Task(id="c1.t2", title="Sign out", todos=[Todo(id="c1.t2.d1", title="Build")]),
    ]  # fmt: skip
    return Plan(name="P", categories=[Category(id="c1", title="Auth", tasks=tasks)])


def _run(fake, plan=None, **kw):
    return sync_plan_to_jira(plan or _plan(), fake, project_key="ZZ", **kw)


def test_first_run_creates_epic_stories_and_subtasks_with_parents_and_markers() -> None:
    fake = FakeJira()
    changes, keys = _run(fake)
    assert [(c.kind, c.action) for c in changes] == (
        [("epic", "create"), ("story", "create"), ("subtask", "create"), ("subtask", "create")]
        + [("story", "create"), ("subtask", "create")]
    )
    by_id = {extract_marker(i["description"]): (k, i) for k, i in fake.issues.items()}
    epic_key, epic = by_id["c1"]
    story_key, story = by_id["c1.t1"]
    assert epic["type"] == "Epic" and story["type"] == "Story" and story["parent"] == epic_key
    assert by_id["c1.t1.d1"][1]["type"] == "Subtask" and by_id["c1.t1.d1"][1]["parent"] == story_key
    assert "* a" in story["description"] and set(keys) == set(by_id)


def test_new_cards_get_the_plan_status_through_a_transition() -> None:
    fake = FakeJira()
    _run(fake)
    cats = {extract_marker(i["description"]): i["cat"] for i in fake.issues.values()}
    assert cats["c1.t1"] == "indeterminate" and cats["c1.t1.d1"] == "done"
    assert cats["c1.t1.d2"] == "new" and "transition:To Do" not in fake.calls


def test_a_second_run_with_the_saved_keys_changes_nothing() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    fake.calls.clear()
    changes, _ = _run(fake, keys=keys)
    assert {c.action for c in changes} == {"unchanged"} and fake.calls == []


def test_a_run_without_the_key_map_adopts_cards_by_marker_and_does_not_duplicate() -> None:
    fake = FakeJira()
    _run(fake)
    before = len(fake.issues)
    fake.calls.clear()
    changes, keys = _run(fake, keys={})  # the cache was lost
    assert len(fake.issues) == before and {c.action for c in changes} == {"unchanged"}
    assert "create" not in fake.calls and len(keys) == before


def test_a_renamed_task_is_updated_in_place() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    plan = _plan()
    plan.categories[0].tasks[0].title = "Log in"
    fake.calls.clear()
    changes, _ = _run(fake, plan, keys=keys)
    assert [c.id for c in changes if c.action == "update"] == ["c1.t1"]
    assert fake.calls == ["update"]


def test_jira_whitespace_round_trip_is_not_drift() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    for i in fake.issues.values():  # Jira hands text back with CRLF and trailing spaces
        i["description"] = i["description"].replace("\n", "  \r\n")
    fake.calls.clear()
    changes, _ = _run(fake, keys=keys)
    assert {c.action for c in changes} == {"unchanged"}


def test_an_existing_card_keeps_the_status_jira_has() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    story_key = next(
        k for k, i in fake.issues.items() if extract_marker(i["description"]) == "c1.t1"
    )
    fake.issues[story_key]["cat"] = "done"
    plan = _plan()
    plan.categories[0].tasks[0].title = "Log in"
    _run(fake, plan, keys=keys)
    assert fake.issues[story_key]["cat"] == "done"


def test_dry_run_writes_nothing() -> None:
    fake = FakeJira()
    changes, keys = _run(fake, dry_run=True)
    assert fake.calls == [] and not fake.issues and keys == {}
    assert {c.action for c in changes} == {"create"} and len(changes) == 6


def test_a_card_deleted_by_hand_is_recreated_not_crashed_on() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    gone = keys["c1.t2.d1"]
    del fake.issues[gone]
    changes, new_keys = _run(fake, keys=keys)
    assert [c.id for c in changes if c.action == "create"] == ["c1.t2.d1"]
    assert new_keys["c1.t2.d1"] != gone


def test_nothing_is_ever_deleted() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    smaller = _plan()
    smaller.categories[0].tasks.pop()
    _run(fake, smaller, keys=keys)
    assert len(fake.issues) == 6 and not [c for c in fake.calls if "delete" in c]


def test_a_failure_part_way_keeps_what_was_created_and_raises() -> None:
    fake = FakeJira()
    fake.fail_on = "Sign out"
    with pytest.raises(JiraError, match="boom"):
        _run(fake)
    assert len(fake.issues) == 4  # the epic, the first story and its two subtasks stay


def test_a_classic_project_can_name_its_subtask_type() -> None:
    fake = FakeJira()
    _run(fake, subtask_type="Sub-task")
    assert {i["type"] for i in fake.issues.values()} == {"Epic", "Story", "Sub-task"}


@pytest.mark.parametrize(
    ("name", "category", "expected"),
    [("To Do", "new", "todo"), ("In Review", "indeterminate", "in_progress"),
     ("Done", "done", "done"), ("Blocked", "indeterminate", "blocked"), ("x", None, "todo")],
)  # fmt: skip
def test_remote_status(name, category, expected) -> None:
    assert (
        remote_status(HierarchyIssue(key="K-1", status=name, status_category=category)) == expected
    )


def test_a_config_name_beats_the_category() -> None:
    issue = HierarchyIssue(key="K-1", status="QA", status_category="indeterminate")
    assert remote_status(issue, {"qa": "done"}) == "done"


def test_status_names_cover_every_plan_status() -> None:
    assert set(STATUS_NAME) == {"todo", "in_progress", "done", "blocked"}


# ---- sync-status against Jira ----


def test_status_pull_and_push_through_the_jira_backend() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    plan = _plan()
    plan.categories[0].tasks[1].todos[0].status = "done"  # ticked locally: push
    d1 = keys["c1.t1.d2"]
    fake.issues[d1]["cat"] = "done"  # moved in Jira: pull
    base = SyncState(status={"c1.t1.d1": "done"}, keys=keys)  # d1 was already done at the last sync
    report = sync_status_with(plan, JiraStatusBackend(fake, keys, project_key="ZZ"), base=base)
    actions = {c.id: c.action for c in report.changes if c.kind == "to-do"}
    assert actions["c1.t2.d1"] == "push" and actions["c1.t1.d2"] == "pull"
    assert fake.issues[keys["c1.t2.d1"]]["cat"] == "done"
    assert plan.categories[0].tasks[0].todos[1].status == "done"
    assert report.new_state.keys == keys  # the key map survives a status sync


def test_the_backend_reports_a_card_deleted_by_hand_as_an_orphan() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    del fake.issues[keys["c1.t2.d1"]]
    report = sync_status_with(
        _plan(), JiraStatusBackend(fake, keys, project_key="ZZ"), base=SyncState(keys=keys)
    )
    assert [c.id for c in report.changes if c.action == "orphan"] == ["c1.t2.d1"]


def test_a_move_without_a_matching_transition_is_no_state_not_a_crash() -> None:
    fake = FakeJira()
    _, keys = _run(fake)
    backend = JiraStatusBackend(fake, keys, project_key="ZZ")
    card = backend.cards(_plan())["c1.t1.d2"]
    fake.transition_issue = lambda key, name: (_ for _ in ()).throw(JiraError(404, "none"))
    assert backend.move(card, "done", dry_run=False) is False
    assert isinstance(card, Card)


# ---- the key map comes from a file in the project directory, which may be someone else's ----


def _foreign_issue(fake: FakeJira, key: str = "OTHER-1") -> None:
    fake.issues[key] = {
        "summary": "Someone else's ticket", "description": "private notes", "type": "Story",
        "parent": None, "cat": "new",
    }  # fmt: skip


def test_a_key_map_entry_pointing_at_another_project_is_never_touched() -> None:
    fake = FakeJira()
    _foreign_issue(fake)
    changes, keys = _run(fake, keys={"c1.t1": "OTHER-1"})
    assert fake.issues["OTHER-1"]["summary"] == "Someone else's ticket"
    assert (
        fake.issues["OTHER-1"]["description"] == "private notes"
        and fake.issues["OTHER-1"]["cat"] == "new"
    )
    assert "OTHER-1" not in keys.values()  # and the bad entry is dropped, not saved back


def test_a_key_in_the_right_project_without_the_matching_marker_is_not_trusted() -> None:
    fake = FakeJira()
    _foreign_issue(fake, "ZZ-99")  # same project key, but not a card this plan made
    _run(fake, keys={"c1.t1": "ZZ-99"})
    assert fake.issues["ZZ-99"]["summary"] == "Someone else's ticket"
    assert fake.issues["ZZ-99"]["description"] == "private notes"


def test_status_sync_does_not_move_a_card_the_key_map_wrongly_names() -> None:
    fake = FakeJira()
    _foreign_issue(fake)
    plan = _plan()
    plan.categories[0].tasks[0].todos[0].status = "done"
    report = sync_status_with(
        plan, JiraStatusBackend(fake, {"c1.t1.d1": "OTHER-1"}, project_key="ZZ"),
        base=SyncState(keys={"c1.t1.d1": "OTHER-1"}),
    )  # fmt: skip
    assert fake.issues["OTHER-1"]["cat"] == "new"
    assert [c.action for c in report.changes if c.id == "c1.t1.d1"] == ["orphan"]
