"""Unit tests for drift.py (ADLC-146): ids, markers, diff_decomposition, apply_update. A
small in-memory fake stands in for JiraClient (list_children/create_issue/update_issue/
transition_issue) -- HTTP behaviour is test_client.py's job.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from symphony_workflow.client import JiraError
from symphony_workflow.decompose import Decomposition, Story, Task, assign_ids
from symphony_workflow.drift import (
    DESCRIPTION_CHANGED,
    MISSING,
    OK,
    ORPHAN,
    PARENT_DIFFERS,
    SUMMARY_CHANGED,
    apply_update,
    diff_decomposition,
    extract_marker,
    normalize,
)
from symphony_workflow.models import HierarchyIssue
from symphony_workflow.sync_decomposition import (
    load_state,
    render_story_description,
    render_task_description,
    save_state,
)


def _spec() -> Decomposition:
    d = Decomposition(
        epic_key="ADLC-1",
        stories=[
            Story(
                title="Story A",
                user_story="As a user, I want A.",
                acceptance_criteria=["A works"],
                tasks=[
                    Task(title="Task A1", description="Do A1"),
                    Task(title="Task A2", description="Do A2"),
                ],
            ),
            Story(
                title="Story B",
                user_story="As a user, I want B.",
                acceptance_criteria=["B works"],
                tasks=[Task(title="Task B1", description="Do B1")],
            ),
        ],
        definition_of_done=["Tests pass"],
    )
    assign_ids(d)
    return d


class FakeJira:
    """children[parent_key] -> list[HierarchyIssue]; records writes."""

    def __init__(self) -> None:
        self.children: dict[str, list[HierarchyIssue]] = {}
        self.created: list[tuple] = []
        self.updated: list[tuple] = []
        self.transitions: list[tuple] = []
        self.fail_on_create: int | None = None
        self._n = 1000

    def add(self, parent: str, key: str, summary: str, description: str | None) -> None:
        self.children.setdefault(parent, []).append(
            HierarchyIssue(key=key, summary=summary, description=description, parent_key=parent)
        )

    def list_children(self, parent_key: str) -> list[HierarchyIssue]:
        return list(self.children.get(parent_key, []))

    def get_hierarchy_issue(self, key: str) -> HierarchyIssue:
        for kids in self.children.values():
            for kid in kids:
                if kid.key == key:
                    return kid
        raise JiraError(404, key)

    def create_issue(self, project, summary, description, issue_type, parent=None):
        if self.fail_on_create is not None and len(self.created) >= self.fail_on_create:
            raise JiraError(500, "boom")
        self._n += 1
        key = f"{project}-{self._n}"
        self.created.append((project, summary, description, issue_type, parent, key))
        self.add(parent, key, summary, description)
        return SimpleNamespace(key=key, url=f"https://x/{key}")

    def update_issue(self, key, *, summary=None, description=None, parent=None):
        self.updated.append((key, summary, description))
        for kids in self.children.values():
            for i, kid in enumerate(kids):
                if kid.key == key:
                    kids[i] = kid.model_copy(
                        update={
                            "summary": summary if summary is not None else kid.summary,
                            "description": description
                            if description is not None
                            else kid.description,
                        }
                    )

    def transition_issue(self, key, name):
        self.transitions.append((key, name))


def _synced(spec: Decomposition) -> FakeJira:
    """A Jira that exactly mirrors `spec` (what sync would have produced)."""
    jira = FakeJira()
    for si, story in enumerate(spec.stories):
        skey = f"ADLC-{10 + si * 10}"
        jira.add(
            "ADLC-1", skey, story.title, render_story_description(story, spec.definition_of_done)
        )
        for ti, task in enumerate(story.tasks):
            jira.add(skey, f"{skey}{ti}", task.title, render_task_description(task))
    return jira


def _kinds(report) -> list[str]:
    return [i.kind for i in report.items]


def test_assign_ids_is_stable_and_back_compatible() -> None:
    d = Decomposition.model_validate(
        {
            "epic_key": "E-1",
            "stories": [
                {
                    "title": "a",
                    "user_story": "u",
                    "acceptance_criteria": [],
                    "tasks": [{"title": "t", "description": "d"}],
                }
            ],
            "definition_of_done": [],
        }
    )
    assert d.stories[0].id is None  # old file without ids still loads
    assert assign_ids(d) is True
    assert (d.stories[0].id, d.stories[0].tasks[0].id) == ("s1", "s1.t1")
    assert assign_ids(d) is False  # second run changes nothing
    d.stories.insert(0, Story(title="new", user_story="u", acceptance_criteria=[], tasks=[]))
    assign_ids(d)
    assert d.stories[1].id == "s1"  # existing id untouched when order changes
    assert d.stories[0].id == "s1-2"


def test_marker_and_normalize() -> None:
    assert extract_marker("text\n\nsd-id: s1.t2\n") == "s1.t2"
    assert extract_marker("no marker") is None
    assert extract_marker(None) is None
    assert normalize("a  \r\nb\r\n\r\n\r\n\r\nc\n\nsd-id: s1") == "a\nb\n\nc"


def test_in_sync_reports_no_drift() -> None:
    spec = _spec()
    report = diff_decomposition("ADLC-1", spec, client=_synced(spec))
    assert not report.drift
    assert set(_kinds(report)) == {OK}
    assert report.counts() == {OK: 5}


def test_jira_roundtrip_whitespace_is_not_drift() -> None:
    spec = _spec()
    jira = _synced(spec)
    kid = jira.children["ADLC-1"][0]
    jira.children["ADLC-1"][0] = kid.model_copy(
        update={"description": (kid.description or "").replace("\n", "  \r\n")}
    )
    assert not diff_decomposition("ADLC-1", spec, client=jira).drift


def test_missing_story_reports_its_subtasks_too() -> None:
    spec = _spec()
    jira = _synced(spec)
    jira.children["ADLC-1"].pop()  # Story B gone
    report = diff_decomposition("ADLC-1", spec, client=jira)
    assert [(i.level, i.id) for i in report.items if i.kind == MISSING] == [
        ("story", "s2"),
        ("subtask", "s2.t1"),
    ]
    assert report.drift


def test_summary_and_description_changed_and_orphan() -> None:
    spec = _spec()
    jira = _synced(spec)
    story = jira.children["ADLC-1"][0]
    jira.children["ADLC-1"][0] = story.model_copy(
        update={"summary": "Renamed in Jira", "description": "hand edited\n\nsd-id: s1"}
    )
    jira.add("ADLC-10", "ADLC-99", "Added by hand", "x")  # Subtask only in Jira
    report = diff_decomposition("ADLC-1", spec, client=jira)
    by = {(i.kind, i.key) for i in report.items if i.kind != OK}
    assert by == {
        (SUMMARY_CHANGED, "ADLC-10"),
        (DESCRIPTION_CHANGED, "ADLC-10"),
        (ORPHAN, "ADLC-99"),
    }


def test_rename_in_spec_matches_by_marker_not_duplicate() -> None:
    spec = _spec()
    jira = _synced(spec)
    spec.stories[0].title = "Story A renamed"
    report = diff_decomposition("ADLC-1", spec, client=jira)
    assert MISSING not in _kinds(report)
    assert any(i.kind == SUMMARY_CHANGED and i.id == "s1" for i in report.items)


def test_unmarked_cards_need_adopt() -> None:
    spec = _spec()
    jira = FakeJira()
    jira.add("ADLC-1", "ADLC-10", "Story A", "legacy text")
    without = diff_decomposition("ADLC-1", spec, client=jira)
    assert any(i.kind == MISSING and i.id == "s1" for i in without.items)
    assert any(i.kind == ORPHAN and i.key == "ADLC-10" for i in without.items)
    adopted = diff_decomposition("ADLC-1", spec, client=jira, adopt=True)
    assert not any(
        i.id == "s1" and i.level == "story" and i.kind in (MISSING, ORPHAN) for i in adopted.items
    )
    assert any(i.kind == DESCRIPTION_CHANGED and i.key == "ADLC-10" for i in adopted.items)


def test_empty_epic_is_all_missing() -> None:
    report = diff_decomposition("ADLC-1", _spec(), client=FakeJira())
    assert _kinds(report).count(MISSING) == 5


def test_update_creates_missing_updates_changed_and_is_idempotent() -> None:
    spec = _spec()
    jira = _synced(spec)
    jira.children["ADLC-1"].pop()  # Story B missing
    story = jira.children["ADLC-1"][0]
    jira.children["ADLC-1"][0] = story.model_copy(update={"summary": "stale"})

    actions = apply_update("ADLC-1", spec, client=jira, initial_status="In Progress")
    assert [(a.action, a.level) for a in actions] == [
        ("create", "story"),
        ("create", "subtask"),
        ("update", "story"),
    ]
    assert all(a.done for a in actions)
    created_story, created_task = jira.created
    assert created_story[3:5] == ("Story", "ADLC-1")
    assert created_task[3] == "Subtask" and created_task[4] == created_story[5]
    assert "sd-id: s2" in created_story[2] and "sd-id: s2.t1" in created_task[2]
    assert jira.updated == [("ADLC-10", "Story A", None)]
    assert len(jira.transitions) == 2

    assert apply_update("ADLC-1", spec, client=jira) == []  # idempotent
    assert not diff_decomposition("ADLC-1", spec, client=jira).drift


def test_update_dry_run_writes_nothing() -> None:
    jira = FakeJira()
    actions = apply_update("ADLC-1", _spec(), client=jira, dry_run=True)
    assert len(actions) == 5 and not any(a.done for a in actions)
    assert jira.created == [] and jira.updated == []


def test_update_never_deletes_orphans() -> None:
    spec = _spec()
    jira = _synced(spec)
    jira.add("ADLC-1", "ADLC-77", "Only in Jira", "x")
    assert apply_update("ADLC-1", spec, client=jira) == []
    assert any(k.key == "ADLC-77" for k in jira.children["ADLC-1"])


def test_update_adds_marker_to_adopted_cards() -> None:
    spec = _spec()
    jira = FakeJira()
    jira.add(
        "ADLC-1",
        "ADLC-10",
        "Story A",
        render_story_description(
            spec.stories[0].model_copy(update={"id": None}), spec.definition_of_done
        ),
    )
    apply_update("ADLC-1", spec, client=jira, adopt=True)
    assert any(key == "ADLC-10" and "sd-id: s1" in (desc or "") for key, _, desc in jira.updated)


def test_update_stops_at_first_jira_error_and_resumes() -> None:
    spec = _spec()
    jira = FakeJira()
    jira.fail_on_create = 2
    with pytest.raises(JiraError):
        apply_update("ADLC-1", spec, client=jira)
    assert len(jira.created) == 2
    jira.fail_on_create = None
    apply_update("ADLC-1", spec, client=jira)  # resumes: no duplicates
    assert not diff_decomposition("ADLC-1", spec, client=jira).drift
    assert len(jira.created) == 5


def _move_first_task_to_story_b(jira: FakeJira) -> None:
    moved = jira.children["ADLC-10"].pop(0)  # Story A's first Subtask
    jira.children.setdefault("ADLC-20", []).append(
        moved.model_copy(update={"parent_key": "ADLC-20"})
    )


def test_moved_subtask_is_parent_differs_not_missing_plus_orphan() -> None:
    spec = _spec()
    jira = _synced(spec)
    _move_first_task_to_story_b(jira)
    report = diff_decomposition("ADLC-1", spec, client=jira)
    drift = [i for i in report.items if i.kind != OK]
    assert [(i.kind, i.id, i.parent_key) for i in drift] == [(PARENT_DIFFERS, "s1.t1", "ADLC-20")]
    assert drift[0].parent_id == "s1"


def test_update_reparents_moved_subtask_and_never_creates_a_duplicate() -> None:
    spec = _spec()
    jira = _synced(spec)
    _move_first_task_to_story_b(jira)
    calls: list[tuple] = []
    original = jira.update_issue

    def spy(key, **kw):
        calls.append((key, kw))
        if kw.get("parent"):  # a real Jira moves it; mirror that in the fake
            moved = jira.get_hierarchy_issue(key)
            for kids in jira.children.values():
                if moved in kids:
                    kids.remove(moved)
            jira.children[kw["parent"]].append(
                moved.model_copy(update={"parent_key": kw["parent"]})
            )
        else:
            original(key, **kw)

    jira.update_issue = spy  # type: ignore[method-assign]
    actions = apply_update("ADLC-1", spec, client=jira)
    assert [(a.action, a.id) for a in actions] == [("reparent", "s1.t1")]
    assert calls == [("ADLC-100", {"parent": "ADLC-10"})]
    assert jira.created == []
    assert not diff_decomposition("ADLC-1", spec, client=jira).drift


def test_subtask_under_unmatched_story_is_found_as_stray() -> None:
    spec = _spec()
    jira = _synced(spec)
    jira.children["ADLC-1"].pop(0)  # Story A card removed from the spec's view...
    jira.add(
        "ADLC-1", "ADLC-55", "Legacy wrapper", "no marker"
    )  # ...replaced by an unrelated Story
    jira.children["ADLC-55"] = [jira.children["ADLC-10"].pop(0)]  # holding Task A1's card
    report = diff_decomposition("ADLC-1", spec, client=jira)
    assert any(
        i.kind == PARENT_DIFFERS and i.id == "s1.t1" and i.parent_key == "ADLC-55"
        for i in report.items
    )


def test_state_cache_matches_a_card_whose_marker_was_removed(tmp_path) -> None:
    spec = _spec()
    jira = _synced(spec)
    kid = jira.children["ADLC-1"][0]
    jira.children["ADLC-1"][0] = kid.model_copy(update={"description": "marker edited away"})
    without = diff_decomposition("ADLC-1", spec, client=jira)
    assert any(i.kind == MISSING and i.id == "s1" for i in without.items)
    cached = diff_decomposition("ADLC-1", spec, client=jira, known={"s1": "ADLC-10"})
    assert not any(i.kind in (MISSING, ORPHAN) and i.id == "s1" for i in cached.items)
    assert any(i.kind == DESCRIPTION_CHANGED and i.key == "ADLC-10" for i in cached.items)


def test_apply_update_writes_state_file_even_after_partial_failure(tmp_path) -> None:
    spec = _spec()
    jira = FakeJira()
    jira.fail_on_create = 2
    state = tmp_path / "sync-state.json"
    with pytest.raises(JiraError):
        apply_update("ADLC-1", spec, client=jira, state_path=state)
    ids = load_state(state)
    assert set(ids) == {"s1", "s2"} and all(v.startswith("ADLC-") for v in ids.values())
    jira.fail_on_create = None
    apply_update("ADLC-1", spec, client=jira, state_path=state, known=ids)
    assert set(load_state(state)) == {"s1", "s1.t1", "s1.t2", "s2", "s2.t1"}


def test_state_file_roundtrip_and_bad_file(tmp_path) -> None:
    path = tmp_path / "sync-state.json"
    assert load_state(path) == {}  # missing
    save_state(path, "ADLC-1", {"s2": "ADLC-2", "s1": "ADLC-1"})
    assert load_state(path) == {"s1": "ADLC-1", "s2": "ADLC-2"}
    path.write_text("not json")
    assert load_state(path) == {}
