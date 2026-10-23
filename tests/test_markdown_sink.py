"""Unit tests for the Markdown sink (markdown_sink.py, ADLC-202)."""

from __future__ import annotations

from pathlib import Path

from symphony_workflow.epics_md import load_epics_md
from symphony_workflow.markdown_sink import merge_block, read_state, task_path, write_markdown
from symphony_workflow.plan import Category, Plan, Task, Todo

from ._athena import make_project


def _plan() -> Plan:
    return Plan(
        name="demo",
        categories=[
            Category(
                id="c1",
                title="Batch one",
                tasks=[
                    Task(
                        id="c1.t1",
                        title="Sign in",
                        description="Let people sign in.",
                        acceptance_criteria=["Form validates"],
                        todos=[
                            Todo(id="c1.t1.d1", title="Build"),
                            Todo(id="c1.t1.d2", title="Tests: unit"),
                        ],
                    ),
                    Task(id="c1.t2", title="Reports", todos=[]),
                ],
            )
        ],
    )


def _actions(changes) -> dict[str, str]:
    return {c.path: c.action for c in changes}


def test_creates_the_index_and_one_file_per_task(tmp_path: Path) -> None:
    changes = write_markdown(_plan(), tmp_path)
    assert _actions(changes) == {
        "docs/epics/c1-t1-sign-in.md": "create",
        "docs/epics/c1-t2-reports.md": "create",
        "docs/epics/EPIC_INDEX.md": "create",
    }
    task = (tmp_path / "docs/epics/c1-t1-sign-in.md").read_text()
    assert "# c1.t1 — Sign in" in task and "- [ ] Form validates" in task
    assert "- [ ] Build <!-- sd-id: c1.t1.d1 -->" in task
    assert "_(none yet)_" in (tmp_path / "docs/epics/c1-t2-reports.md").read_text()
    index = (tmp_path / "docs/epics/EPIC_INDEX.md").read_text()
    assert "### Batch one (todo)" in index and "(c1-t1-sign-in.md)" in index


def test_a_second_run_changes_nothing(tmp_path: Path) -> None:
    write_markdown(_plan(), tmp_path)
    before = {p: p.read_text() for p in (tmp_path / "docs/epics").glob("*.md")}
    changes = write_markdown(_plan(), tmp_path)
    assert {c.action for c in changes} == {"unchanged"}
    assert {p: p.read_text() for p in (tmp_path / "docs/epics").glob("*.md")} == before


def test_dry_run_reports_but_writes_nothing(tmp_path: Path) -> None:
    changes = write_markdown(_plan(), tmp_path, dry_run=True)
    assert {c.action for c in changes} == {"create"}
    assert not (tmp_path / "docs").exists()


def test_existing_files_keep_everything_outside_the_managed_blocks(tmp_path: Path) -> None:
    root = make_project(tmp_path)
    original = (root / "docs/epics/e1-login.md").read_text()
    index = root / "docs/epics/EPIC_INDEX.md"
    index.write_text("# Epic Index\n\nHand-written naming rules.\n", encoding="utf-8")

    changes = _actions(write_markdown(load_epics_md(root), root))

    assert changes["docs/epics/e1-login.md"] == "update"  # a To-Do block was appended
    updated = (root / "docs/epics/e1-login.md").read_text()
    assert updated.startswith(original.rstrip("\n"))  # nothing of the original was touched
    assert (
        "<!-- sd:begin todo -->" in updated
        and "- [ ] Build the form <!-- sd-id: E1.d1 -->" in updated
    )
    assert (
        "Hand-written naming rules." in index.read_text()
        and "<!-- sd:begin index -->" in index.read_text()
    )
    assert len(list((root / "docs/epics").glob("e1-*.md"))) == 1  # the existing file was reused


def test_changing_the_plan_rewrites_only_the_block(tmp_path: Path) -> None:
    write_markdown(_plan(), tmp_path)
    path = tmp_path / "docs/epics/c1-t1-sign-in.md"
    path.write_text(path.read_text() + "\n## My notes\nkeep me\n", encoding="utf-8")
    plan = _plan()
    plan.categories[0].tasks[0].todos[0].title = "Build the form"
    write_markdown(plan, tmp_path)
    text = path.read_text()
    assert "Build the form <!-- sd-id: c1.t1.d1 -->" in text and "keep me" in text
    assert text.count("<!-- sd:begin todo -->") == 1


def test_done_todos_are_ticked(tmp_path: Path) -> None:
    plan = _plan()
    plan.categories[0].tasks[0].todos[0].status = "done"
    write_markdown(plan, tmp_path)
    assert (
        "- [x] Build <!-- sd-id: c1.t1.d1 -->"
        in (tmp_path / "docs/epics/c1-t1-sign-in.md").read_text()
    )


def test_read_state_follows_the_ids_not_the_order(tmp_path: Path) -> None:
    write_markdown(_plan(), tmp_path)
    path = tmp_path / "docs/epics/c1-t1-sign-in.md"
    lines = path.read_text().splitlines()
    # tick the second To-Do and put the lines in the opposite order
    ticked = [ln.replace("- [ ] Tests: unit", "- [x] Tests: unit") for ln in lines]
    first = next(i for i, ln in enumerate(ticked) if "sd-id: c1.t1.d1" in ln)
    ticked[first], ticked[first + 1] = ticked[first + 1], ticked[first]
    path.write_text("\n".join(ticked) + "\n", encoding="utf-8")

    plan = _plan()
    assert read_state(plan, tmp_path) == ["c1.t1.d2"]
    assert [t.status for t in plan.categories[0].tasks[0].todos] == ["todo", "done"]
    assert read_state(plan, tmp_path) == []  # idempotent


def test_an_unticked_box_downgrades_done_but_never_in_progress(tmp_path: Path) -> None:
    write_markdown(_plan(), tmp_path)
    plan = _plan()
    todos = plan.categories[0].tasks[0].todos
    todos[0].status, todos[1].status = "done", "in_progress"
    assert read_state(plan, tmp_path) == ["c1.t1.d1"]
    assert [t.status for t in todos] == ["todo", "in_progress"]


def test_task_path_reuses_exactly_one_existing_match_otherwise_names_a_new_file(
    tmp_path: Path,
) -> None:
    root = make_project(tmp_path)
    task = Task(id="E1", title="Login page")
    assert task_path(task, root).name == "e1-login.md"
    (root / "docs/epics/e1-second.md").write_text("# E1 — dup\n", encoding="utf-8")
    assert task_path(task, root).name == "e1-login-page.md"  # ambiguous, so a new file


def test_merge_block_appends_then_replaces() -> None:
    first = merge_block("# T\n", "x", "<!-- sd:begin x -->\none\n<!-- sd:end x -->")
    assert first.endswith("<!-- sd:end x -->\n") and first.startswith("# T")
    second = merge_block(first, "x", "<!-- sd:begin x -->\ntwo\n<!-- sd:end x -->")
    assert "two" in second and "one" not in second and second.count("sd:begin x") == 1


def test_an_existing_checkbox_list_is_tagged_in_place_not_duplicated(tmp_path: Path) -> None:
    write_markdown(_plan(), tmp_path)
    path = task_path(_plan().categories[0].tasks[0], tmp_path)
    todos = _plan().categories[0].tasks[0].todos
    path.write_text("# T\n\n" + "\n".join(f"- [ ] {t.title}" for t in todos) + "\n")
    write_markdown(_plan(), tmp_path)
    text = path.read_text()
    assert "sd:begin todo" not in text
    assert text.count("sd-id:") == len(todos) and all(t.title in text for t in todos)
    write_markdown(_plan(), tmp_path)
    assert path.read_text() == text


def test_no_index_is_created_when_every_task_file_already_exists(tmp_path: Path) -> None:
    write_markdown(_plan(), tmp_path)
    (tmp_path / "docs/epics/EPIC_INDEX.md").unlink()
    changes = write_markdown(_plan(), tmp_path)
    assert not (tmp_path / "docs/epics/EPIC_INDEX.md").exists()
    assert all(c.path != "docs/epics/EPIC_INDEX.md" for c in changes)
