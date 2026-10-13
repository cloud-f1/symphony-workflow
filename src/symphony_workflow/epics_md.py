"""`epics-md` source adapter: an epics-as-markdown workflow's files -> the neutral plan (ADLC-200).

Reads the documented files only, never the emitting scripts (two template variants of that workflow
have
different emit scripts, so the files are the contract):

    docs/epics/e{n}-{slug}.md        one Task: `# E{n} - Title`, `## Acceptance Criteria`,
    optionally
                                     `## Implementation Phases` (`### Phase ...`) or `## To-Do`
    docs/context/epic-progress.md    `Phase Status` table (Phase | Epics | Status) and the step
    matrix

Mapping: a Phase row is a Category (`phase-<n>`), an epic file is a Task (`E<n>`), its
implementation phases or
`## To-Do` checkboxes are the To-Dos (`E<n>.d<k>`). Acceptance criteria stay on the Task. Only
phases that are
not complete are imported unless `include_done` is set (spec section 2: no top-heavy history).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from symphony_workflow.plan import (
    Category,
    Plan,
    Status,
    Task,
    Todo,
    rollup_status,
    strip_comment_marker,
)
from symphony_workflow.safepath import is_safe

EPICS_DIR = Path("docs/epics")
PROGRESS_FILE = Path("docs/context/epic-progress.md")

_TITLE_RE = re.compile(r"^#\s+(E\d+)\s*[-—–:]\s*(.+?)\s*$")
_FILE_ID_RE = re.compile(r"^e(\d+)-", re.IGNORECASE)
_CHECK_RE = re.compile(r"^\s*-\s*\[([ xX])\]\s*(.+?)\s*$")
_PHASE_ROW_RE = re.compile(r"^\|\s*(Phase\s*\d+|Infra[^|]*?)\s*\|([^|]*)\|([^|]*)\|")
_MATRIX_ROW_RE = re.compile(r"^\|\s*(E\d+)\s*\|(.*)\|\s*$")
_ACCEPTANCE_HEADING_RE = re.compile(r"(?:\d+\.\s*)?.*Acceptance", re.I)
_STORIES_HEADING_RE = re.compile(r"(?:\d+\.\s*)?(?:User\s+)?Stories\b", re.I)
_STORY_LINE_RE = re.compile(r"^###\s*(?:S\d+|Story\s*\d+)\s*[:.\-—–]\s*(.+?)\s*$", re.I)
_STEP_COLUMNS = 5  # spec, implement, QA, commit, merge


@dataclass
class ParsedEpic:
    id: str
    title: str
    path: Path
    acceptance_criteria: list[str] = field(default_factory=list)
    todos: list[tuple[str, bool]] = field(default_factory=list)  # (title, done)
    todo_origin: str = ""  # "checklist" | "phases" | "stories" | "" (no ready-made breakdown)

    @property
    def has_implementation_phases(self) -> bool:
        return self.todo_origin == "phases"


def _section(lines: list[str], heading_re: re.Pattern[str]) -> list[str]:
    """Lines under the first `##` heading matching `heading_re`, up to the next `##` heading."""
    out: list[str] = []
    inside = False
    for line in lines:
        if line.startswith("## "):
            if inside:
                break
            inside = bool(heading_re.match(line[3:].strip()))
            continue
        if inside:
            out.append(line)
    return out


def parse_epic_file(path: Path) -> ParsedEpic | None:
    """One epic file, or None when it has no recognisable `E<n>` id (not an epic)."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    epic_id, title = None, path.stem
    for line in lines:
        match = _TITLE_RE.match(line)
        if match:
            epic_id, title = match.group(1).upper(), match.group(2)
            break
    if epic_id is None:
        by_name = _FILE_ID_RE.match(path.name)
        if not by_name:
            return None
        epic_id = f"E{by_name.group(1)}"
    epic = ParsedEpic(id=epic_id, title=title, path=path)
    for line in _section(lines, _ACCEPTANCE_HEADING_RE):
        item = _CHECK_RE.match(line)
        if item:
            epic.acceptance_criteria.append(strip_comment_marker(item.group(2)))
    phase_lines = _section(
        lines, re.compile(r"(Implementation Phases|Per-Phase Checkpoints)", re.I)
    )
    phase_titles = [
        re.sub(r"^###\s*", "", line).strip()
        for line in phase_lines
        if re.match(r"^###\s*Phase\b", line, re.I)
    ]
    story_titles = [
        match.group(1)
        for match in map(_STORY_LINE_RE.match, _section(lines, _STORIES_HEADING_RE))
        if match
    ]
    checklist: list[tuple[str, bool]] = []
    for line in _section(lines, re.compile(r"To-?Do", re.I)):
        item = _CHECK_RE.match(line)
        if item:
            checklist.append((strip_comment_marker(item.group(2)), item.group(1) in "xX"))
    # One source for layer 3, in this order: an explicit `## To-Do` checklist (it carries the
    # state), the implementation phases, then `### S1: ...` story headings under `## Stories`.
    if checklist:
        epic.todos, epic.todo_origin = checklist, "checklist"
    elif phase_titles:
        epic.todos, epic.todo_origin = [(t, False) for t in phase_titles], "phases"
    elif story_titles:
        epic.todos, epic.todo_origin = [(t, False) for t in story_titles], "stories"
    return epic


@dataclass
class PhaseRow:
    name: str
    epic_ids: list[str]
    complete: bool


def progress_path(root: Path) -> Path | None:
    """The progress file when it exists and is not a symlink out of the project, else None."""
    path = root / PROGRESS_FILE
    return path if path.is_file() and is_safe(root, path) else None


def parse_progress(path: Path | None) -> list[PhaseRow]:
    if path is None or not path.is_file():
        return []
    rows: list[PhaseRow] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _PHASE_ROW_RE.match(line)
        if match:
            rows.append(
                PhaseRow(
                    name=match.group(1).strip(),
                    epic_ids=re.findall(r"\bE\d+", match.group(2)),
                    complete="✅" in match.group(3),
                )
            )
    return rows


def parse_matrix(path: Path | None) -> dict[str, Status]:
    """Task status from the step matrix: the furthest finished step decides (spec section 4)."""
    if path is None or not path.is_file():
        return {}
    statuses: dict[str, Status] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = _MATRIX_ROW_RE.match(line)
        if not match:
            continue
        cells = [c.strip() for c in match.group(2).split("|")]
        steps = cells[2 : 2 + _STEP_COLUMNS]  # skip Name and Size
        if len(steps) < _STEP_COLUMNS:
            continue
        if any("❌" in s for s in steps):
            status: Status = "blocked"
        elif all(("✅" in s or "⏭" in s) for s in steps):
            status = "done"
        elif any(("✅" in s or "🔄" in s) for s in steps):
            status = "in_progress"
        else:
            status = "todo"
        statuses[match.group(1)] = status
    return statuses


def scan_epics(root: Path) -> list[ParsedEpic]:
    epics: list[ParsedEpic] = []
    for path in sorted((root / EPICS_DIR).glob("e[0-9]*.md")):
        if not is_safe(root, path):  # a symlink could point at any file on the machine
            continue
        parsed = parse_epic_file(path)
        if parsed:
            epics.append(parsed)
    return epics


def _task(epic: ParsedEpic, status: Status) -> Task:
    todos = [
        Todo(id=f"{epic.id}.d{k}", title=title, status="done" if done else "todo")
        for k, (title, done) in enumerate(epic.todos, start=1)
    ]
    return Task(
        id=epic.id,
        title=epic.title,
        acceptance_criteria=epic.acceptance_criteria,
        status=status,
        todos=todos,
    )


def load_epics_md(root: Path, *, include_done: bool = False) -> Plan:
    """The project's epics as a neutral plan. Without a progress table everything lands in one
    `Unphased` Category with status from the matrix when there is one, else `todo`."""
    epics = {e.id: e for e in scan_epics(root)}
    statuses = parse_matrix(progress_path(root))
    phases = parse_progress(progress_path(root))
    categories: list[Category] = []
    placed: set[str] = set()
    for row in phases:
        if row.complete and not include_done:
            placed.update(row.epic_ids)
            continue
        tasks = [
            _task(epics[eid], statuses.get(eid, "done" if row.complete else "todo"))
            for eid in row.epic_ids
            if eid in epics
        ]
        placed.update(row.epic_ids)
        number = re.sub(r"\D", "", row.name) or re.sub(r"\W+", "-", row.name.lower())
        categories.append(
            Category(
                id=f"phase-{number}",
                title=row.name,
                status="done" if row.complete else rollup_status([t.status for t in tasks]),
                tasks=tasks,
            )
        )
    loose = [e for eid, e in epics.items() if eid not in placed]
    if loose and (not phases or include_done or any(not row.complete for row in phases)):
        tasks = [_task(e, statuses.get(e.id, "todo")) for e in loose]
        categories.append(
            Category(
                id="unphased",
                title="Unphased",
                status=rollup_status([t.status for t in tasks]),
                tasks=tasks,
            )
        )
    for category in categories:
        if category.status != "done":
            category.status = rollup_status([t.status for t in category.tasks])
    return Plan(name=root.name, categories=categories)
