"""Unit tests for the `epics-md` source adapter (epics_md.py, ADLC-200)."""

from __future__ import annotations

from pathlib import Path

from symphony_workflow.epics_md import (
    load_epics_md,
    parse_epic_file,
    parse_matrix,
    parse_progress,
    scan_epics,
)

from ._athena import make_project


def _file(tmp_path: Path, name: str, text: str) -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


def test_acceptance_heading_variants_all_count(tmp_path: Path) -> None:
    for heading in (
        "Acceptance Criteria",
        "8. Acceptance Criteria",
        "Acceptance",
        "Stories with Acceptance Criteria",
    ):
        path = _file(tmp_path, "e9-x.md", f"# E9 — T\n\n## {heading}\n\n- [ ] one\n- [x] two\n")
        epic = parse_epic_file(path)
        assert epic is not None and epic.acceptance_criteria == ["one", "two"], heading


def test_title_id_and_the_sd_id_comment_is_stripped(tmp_path: Path) -> None:
    path = _file(
        tmp_path,
        "e12-a.md",
        "# E12 - Plain dash\n\n## Acceptance Criteria\n- [ ] item <!-- sd-id: x -->\n",
    )
    epic = parse_epic_file(path)
    assert (epic.id, epic.title, epic.acceptance_criteria) == ("E12", "Plain dash", ["item"])


def test_a_file_without_an_epic_id_is_ignored(tmp_path: Path) -> None:
    assert parse_epic_file(_file(tmp_path, "notes.md", "# Notes\n")) is None


def test_id_falls_back_to_the_file_name(tmp_path: Path) -> None:
    epic = parse_epic_file(_file(tmp_path, "e7-thing.md", "No heading here\n"))
    assert epic is not None and epic.id == "E7"


def test_todo_source_priority_checklist_then_phases_then_story_headings(tmp_path: Path) -> None:
    both = (
        "# E1 — T\n\n## To-Do\n- [x] from checklist\n\n## Implementation Phases\n### Phase 1 — x\n"
    )
    epic = parse_epic_file(_file(tmp_path, "e1-a.md", both))
    assert (epic.todo_origin, epic.todos) == ("checklist", [("from checklist", True)])

    phases = parse_epic_file(
        _file(
            tmp_path,
            "e2-b.md",
            "# E2 — T\n\n## Implementation Phases\n### Phase 1 — Data\n### Phase 2 — UI\n",
        )
    )
    assert phases.todo_origin == "phases" and [t for t, _ in phases.todos] == [
        "Phase 1 — Data",
        "Phase 2 — UI",
    ]
    assert phases.has_implementation_phases

    stories = parse_epic_file(
        _file(tmp_path, "e3-c.md", "# E3 — T\n\n## Stories\n### S1: Build\n### S2: Tests\n")
    )
    assert stories.todo_origin == "stories" and [t for t, _ in stories.todos] == ["Build", "Tests"]

    none = parse_epic_file(_file(tmp_path, "e4-d.md", "# E4 — T\n\n## Goal\nx\n"))
    assert none.todo_origin == "" and none.todos == []


def test_progress_rows_and_matrix_statuses(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    rows = parse_progress(root / "docs/context/epic-progress.md")
    assert [(r.name, r.epic_ids, r.complete) for r in rows] == [
        ("Phase 1", ["E3"], True),
        ("Phase 2", ["E1", "E2"], False),
    ]
    assert parse_matrix(root / "docs/context/epic-progress.md") == {
        "E1": "in_progress",
        "E2": "todo",
        "E3": "done",
    }
    assert (
        parse_progress(tmp_path / "missing.md") == []
        and parse_matrix(tmp_path / "missing.md") == {}
    )


def test_matrix_blocked_and_skipped_steps(tmp_path: Path) -> None:
    table = (
        "| Epic | Name | Size | Spec | Impl | QA | Commit | Merge | Notes |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| E1 | a | S | ✅ | ❌ | ⬜ | ⬜ | ⬜ | |\n| E2 | b | S | ✅ | ✅ | ⏭️ | ✅ | ✅ | |\n"
    )
    assert parse_matrix(_file(tmp_path, "p.md", table)) == {"E1": "blocked", "E2": "done"}


def test_scan_epics_skips_non_epics(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    assert [e.id for e in scan_epics(root)] == ["E1", "E2", "E3", "E4"]


def test_load_skips_completed_phases_by_default(tmp_path: Path) -> None:
    plan = load_epics_md(make_project(tmp_path))
    ids = [(c.id, [t.id for t in c.tasks]) for c in plan.categories]
    assert ids == [("phase-2", ["E1", "E2"]), ("unphased", ["E4"])]  # phase 1 (E3) is complete
    e1 = plan.categories[0].tasks[0]
    assert e1.status == "in_progress" and e1.acceptance_criteria == [
        "Form validates the email",
        "Wrong password shows an error",
    ]
    assert [(d.id, d.title) for d in e1.todos] == [
        ("E1.d1", "Build the form"),
        ("E1.d2", "Wire the API"),
        ("E1.d3", "Tests"),
    ]
    assert plan.categories[0].status == "in_progress"
    assert plan.counts() == (2, 3, 5)


def test_include_done_brings_the_completed_phase_back_with_checkbox_state(tmp_path: Path) -> None:
    plan = load_epics_md(make_project(tmp_path), include_done=True)
    first = plan.categories[0]
    assert first.id == "phase-1" and first.status == "done"
    assert first.tasks[0].status == "done" and first.tasks[0].todos[0].status == "done"


def test_a_project_without_a_progress_table_gets_one_unphased_category(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    (root / "docs/context/epic-progress.md").unlink()
    plan = load_epics_md(root)
    assert [c.id for c in plan.categories] == ["unphased"]
    assert len(plan.categories[0].tasks) == 4


def test_a_project_with_no_epics_gives_an_empty_plan(tmp_path: Path) -> None:
    assert load_epics_md(tmp_path).categories == []


# ---- projects without a phase table: group, do not pile into one category (stage 2 finding) ----


def _flat_project(tmp_path: Path, n: int) -> Path:
    epics = tmp_path / "docs/epics"
    epics.mkdir(parents=True)
    for i in range(1, n + 1):
        (epics / f"e{i}-thing-{i}.md").write_text(f"# E{i} — Thing {i}\n", encoding="utf-8")
    return tmp_path


def test_a_flat_project_is_grouped_in_eights_in_numeric_order(tmp_path: Path) -> None:
    plan = load_epics_md(_flat_project(tmp_path, 20))
    assert [len(c.tasks) for c in plan.categories] == [8, 8, 4]
    assert [c.id for c in plan.categories] == ["unphased", "unphased-2", "unphased-3"]
    # numeric, not alphabetical: E2 comes before E10, so a group never straddles by accident
    assert [t.id for t in plan.categories[0].tasks] == [f"E{i}" for i in range(1, 9)]
    assert plan.categories[1].tasks[0].id == "E9" and plan.categories[2].tasks[-1].id == "E20"
    assert plan.categories[0].title == "Unphased 1 (E1 to E8)"


def test_grouping_ids_stay_put_when_epics_are_added_later(tmp_path: Path) -> None:
    before = load_epics_md(_flat_project(tmp_path, 10))
    for i in (11, 12):
        (tmp_path / f"docs/epics/e{i}-more.md").write_text(f"# E{i} — More\n", encoding="utf-8")
    after = load_epics_md(tmp_path)
    assert [c.id for c in before.categories] == ["unphased", "unphased-2"]
    assert [c.id for c in after.categories] == ["unphased", "unphased-2"]
    assert [t.id for t in after.categories[0].tasks] == [t.id for t in before.categories[0].tasks]


def test_eight_or_fewer_stay_one_unphased_category(tmp_path: Path) -> None:
    plan = load_epics_md(_flat_project(tmp_path, 8))
    assert [c.id for c in plan.categories] == ["unphased"]
    assert plan.categories[0].title == "Unphased"  # unchanged for existing synced projects


def test_tasks_carry_where_their_to_dos_came_from(tmp_path: Path) -> None:
    plan = load_epics_md(make_project(tmp_path), include_done=True)
    origins = {t.id: t.todo_origin for c in plan.categories for t in c.tasks}
    assert origins["E1"] == "stories" and origins["E2"] == "phases" and origins["E3"] == "checklist"
    assert origins["E4"] == ""  # no breakdown of its own
