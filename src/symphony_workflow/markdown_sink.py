"""Markdown sink: the neutral plan as Athena-compatible files, and checkbox state read back
(ADLC-202).

    docs/epics/EPIC_INDEX.md       layer 1: the Categories (a managed `index` block)
    docs/epics/<id>-<slug>.md      layer 2: one Task; layer 3 is the managed `todo` block (`- [ ]`
    items)

Only the blocks between `<!-- sd:begin NAME -->` and `<!-- sd:end NAME -->` are ever rewritten.
Everything else in
an existing file (hand-written sections, a source workflow's own headings) is preserved. Re-running
with the same
plan changes nothing. Each To-Do line ends with its id (`<!-- sd-id: ... -->`), so state read back
from a file
lands on the right To-Do even after a rename or a reorder.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from symphony_workflow.plan import (
    COMMENT_MARKER_RE,
    Plan,
    Status,
    Task,
    assign_plan_ids,
    comment_marker,
)
from symphony_workflow.safepath import check_inside, is_safe

EPICS_DIR = Path("docs/epics")
INDEX_FILE = EPICS_DIR / "EPIC_INDEX.md"

_TODO_LINE_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.*)$")


@dataclass
class FileChange:
    path: str
    action: Literal["create", "update", "unchanged"]

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "action": self.action}


def _begin(name: str) -> str:
    return f"<!-- sd:begin {name} -->"


def _end(name: str) -> str:
    return f"<!-- sd:end {name} -->"


def _block(name: str, body: str) -> str:
    return f"{_begin(name)}\n{body.rstrip()}\n{_end(name)}"


def merge_block(existing: str, name: str, block: str) -> str:
    """Replace the managed block in `existing`, or append it when the file has none yet."""
    pattern = re.compile(re.escape(_begin(name)) + r".*?" + re.escape(_end(name)), re.DOTALL)
    if pattern.search(existing):
        return pattern.sub(lambda _m: block, existing, count=1)
    return existing.rstrip("\n") + "\n\n" + block + "\n"


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:48] or "task"


def _prefix(task: Task) -> str:
    return (task.id or "task").lower().replace(".", "-")


def task_path(task: Task, root: Path) -> Path:
    """The existing file for this Task when there is exactly one (`e197-*.md`), else a new name."""
    folder = root / EPICS_DIR
    prefix = _prefix(task)
    found = folder.glob(f"{prefix}-*.md") if folder.is_dir() else []
    matches = sorted(m for m in found if is_safe(root, m))
    if len(matches) == 1:
        return matches[0]
    return folder / f"{prefix}-{_slug(task.title)}.md"


def _box(status: Status) -> str:
    return "x" if status == "done" else " "


def render_todo_block(task: Task) -> str:
    lines = ["## To-Do", ""]
    if task.todos:
        lines += [
            f"- [{_box(t.status)}] {t.title} {comment_marker(t.id or '')}" for t in task.todos
        ]
    else:
        lines.append("_(none yet)_")
    return _block("todo", "\n".join(lines))


def render_task_file(task: Task) -> str:
    parts = [f"# {task.id} — {task.title}", "", "## Goal", "", task.description or task.title, ""]
    if task.acceptance_criteria:
        parts += ["## Acceptance Criteria", ""]
        parts += [f"- [{_box(task.status)}] {item}" for item in task.acceptance_criteria]
        parts.append("")
    parts.append(render_todo_block(task))
    return "\n".join(parts) + "\n"


def render_index_block(plan: Plan, root: Path) -> str:
    lines = ["## Plan", ""]
    for category in plan.categories:
        lines.append(f"### {category.title} ({category.status.replace('_', ' ')})")
        lines.append("")
        for task in category.tasks:
            link = task_path(task, root).name
            lines.append(f"- [{_box(task.status)}] [{task.id} — {task.title}]({link})")
        lines.append("")
    return _block("index", "\n".join(lines))


def _write(path: Path, content: str, dry_run: bool, root: Path) -> FileChange:
    check_inside(root, path)
    shown = str(path.relative_to(root))
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return FileChange(shown, "unchanged")
        action: Literal["create", "update"] = "update"
    else:
        action = "create"
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return FileChange(shown, action)


def write_markdown(plan: Plan, root: Path, *, dry_run: bool = False) -> list[FileChange]:
    """Render `plan` under `root`. Idempotent; nothing is deleted. Returns one change per file."""
    assign_plan_ids(plan)
    changes: list[FileChange] = []
    for category in plan.categories:
        for task in category.tasks:
            path = task_path(task, root)
            check_inside(root, path)
            if path.exists():
                content = merge_block(
                    path.read_text(encoding="utf-8"), "todo", render_todo_block(task)
                )
            else:
                content = render_task_file(task)
            changes.append(_write(path, content, dry_run, root))
    index = root / INDEX_FILE
    check_inside(root, index)
    block = render_index_block(plan, root)
    existing = index.read_text(encoding="utf-8") if index.exists() else "# Epic Index\n"
    changes.append(_write(index, merge_block(existing, "index", block), dry_run, root))
    return changes


def read_state(plan: Plan, root: Path) -> list[str]:
    """Copy ticked / unticked To-Do checkboxes from the files into `plan`. Returns the ids that
    changed. A box that is unticked never downgrades an `in_progress` or `blocked` To-Do."""
    changed: list[str] = []
    for category in plan.categories:
        for task in category.tasks:
            path = task_path(task, root)
            if not path.is_file() or not is_safe(root, path):
                continue
            ticked: dict[str, bool] = {}
            for line in path.read_text(encoding="utf-8").splitlines():
                box, marker = _TODO_LINE_RE.match(line), COMMENT_MARKER_RE.search(line)
                if box and marker:
                    ticked[marker.group(1)] = box.group(1) in "xX"
            for todo in task.todos:
                if todo.id not in ticked:
                    continue
                if ticked[todo.id] and todo.status != "done":
                    todo.status = "done"
                    changed.append(todo.id)
                elif not ticked[todo.id] and todo.status == "done":
                    todo.status = "todo"
                    changed.append(todo.id)
    return changed
