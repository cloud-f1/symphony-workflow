"""Neutral three-layer plan: Category -> Task -> To-Do (ADLC-198).

One shape for every source (an epics-as-markdown workflow, a frontier-model decomposition, a
hand-written file)
and every sink (Jira, Linear, Markdown). Design and sizing data:
docs/plans/0008-adaptive-plan-sync/spec.md.

| Layer | Jira | Linear | Markdown |
|---|---|---|---|
| Category | Epic | ProjectMilestone | a row in the epic index |
| Task | Story | Issue | one `.md` file |
| To-Do | Subtask | sub-issue | a checkbox under `## To-Do` |

Acceptance criteria belong to the Task (a checklist in its description), never one To-Do each:
surveyed plans have a
median of 8 per Task, which would make the bottom layer far too wide.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

from symphony_workflow.decompose import Decomposition
from symphony_workflow.drift import (
    extract_marker,  # noqa: F401  (re-exported: one marker parser for all sinks)
)
from symphony_workflow.sync_decomposition import (
    marker_line,  # noqa: F401  (re-exported, same reason)
)

Status = Literal["todo", "in_progress", "done", "blocked"]

# Jira and Linear carry the id as a trailing `sd-id: <id>` line (`marker_line`); Markdown carries it
# as an invisible HTML comment at the end of a line. One definition of each, shared by every sink
# and source.
COMMENT_MARKER_RE = re.compile(r"\s*<!--\s*sd-id:\s*(\S+)\s*-->")


def comment_marker(entry_id: str) -> str:
    return f"<!-- sd-id: {entry_id} -->"


def strip_comment_marker(text: str) -> str:
    return COMMENT_MARKER_RE.sub("", text)


# Pyramid guardrails (spec section 2): warnings, never errors.
MIN_TASKS, MAX_TASKS = 2, 8
MIN_TODOS, MAX_TODOS = 2, 6
SPLIT_TODOS = 8  # above this the message says "split the Task"
# The fixed fallback To-Dos (ADLC-201): they line up with the Athena status matrix columns.
STEP_TITLES = ("Spec", "Implement", "Tests and QA", "Commit", "Merge")


class Todo(BaseModel):
    id: str | None = None
    title: str
    description: str = ""
    status: Status = "todo"


class Task(BaseModel):
    id: str | None = None
    title: str
    description: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    status: Status = "todo"
    todos: list[Todo] = Field(default_factory=list)


class Category(BaseModel):
    id: str | None = None
    title: str
    description: str = ""
    status: Status = "todo"
    tasks: list[Task] = Field(default_factory=list)


class Plan(BaseModel):
    name: str = ""
    categories: list[Category] = Field(default_factory=list)

    def counts(self) -> tuple[int, int, int]:
        """(categories, tasks, to-dos): the pyramid, top to bottom."""
        tasks = [t for c in self.categories for t in c.tasks]
        return len(self.categories), len(tasks), sum(len(t.todos) for t in tasks)


def rollup_status(children: list[Status]) -> Status:
    """A parent's status from its children: all done is done, any movement is in progress, any
    blocked child blocks it (unless everything else is done)."""
    if not children:
        return "todo"
    if all(s == "done" for s in children):
        return "done"
    if "blocked" in children:
        return "blocked"
    if any(s in ("done", "in_progress") for s in children):
        return "in_progress"
    return "todo"


def assign_plan_ids(plan: Plan) -> bool:
    """Fill in missing ids (`c<n>`, `c<n>.t<m>`, `c<n>.t<m>.d<k>`, by position) and return whether
    anything changed. Existing ids are never touched, so reordering keeps identity stable."""
    used = {c.id for c in plan.categories if c.id}
    used |= {t.id for c in plan.categories for t in c.tasks if t.id}
    used |= {d.id for c in plan.categories for t in c.tasks for d in t.todos if d.id}
    changed = False

    def fresh(base: str) -> str:
        candidate, n = base, 1
        while candidate in used:
            n += 1
            candidate = f"{base}-{n}"
        used.add(candidate)
        return candidate

    for i, category in enumerate(plan.categories, start=1):
        if not category.id:
            category.id, changed = fresh(f"c{i}"), True
        for j, task in enumerate(category.tasks, start=1):
            if not task.id:
                task.id, changed = fresh(f"{category.id}.t{j}"), True
            for k, todo in enumerate(task.todos, start=1):
                if not todo.id:
                    todo.id, changed = fresh(f"{task.id}.d{k}"), True
    return changed


@dataclass
class Finding:
    rule: str
    where: str  # the id (or title) of the Category / Task the finding is about
    detail: str

    def as_dict(self) -> dict[str, str]:
        return {"rule": self.rule, "where": self.where, "detail": self.detail}


def check_plan(plan: Plan) -> list[Finding]:
    """Pyramid guardrails. Warnings only: an empty bottom layer is legitimate until layer-3
    generation has run, so `no_todos` is informational, not a reason to stop."""
    findings: list[Finding] = []
    for category in plan.categories:
        where = category.id or category.title
        n = len(category.tasks)
        if n == 1:
            findings.append(Finding("one_task", where, "a Category with one Task is really a Task"))
        elif n > MAX_TASKS:
            findings.append(Finding("too_many_tasks", where, f"{n} Tasks: split the Category"))
        for task in category.tasks:
            tw = task.id or task.title
            m = len(task.todos)
            if m == 0:
                findings.append(
                    Finding("no_todos", tw, "no To-Dos yet (layer-3 generation fills them in)")
                )
                continue
            if m == 1:
                findings.append(
                    Finding("one_todo", tw, "one To-Do means the Task is really a To-Do")
                )
            elif m > SPLIT_TODOS:
                findings.append(Finding("too_many_todos", tw, f"{m} To-Dos: split the Task"))
            elif m > MAX_TODOS:
                findings.append(
                    Finding("many_todos", tw, f"{m} To-Dos: aim for {MIN_TODOS} to {MAX_TODOS}")
                )
            is_steps = tuple(t.title for t in task.todos) == STEP_TITLES
            if not is_steps and not re.search(r"\btest", task.todos[-1].title, re.IGNORECASE):
                findings.append(Finding("no_tests_todo", tw, "the last To-Do should be the tests"))
    return findings


def from_decomposition(decomposition: Decomposition) -> Plan:
    """A reviewed `decomposition.json` (Epic -> Stories -> Tasks) as a one-Category plan. Existing
    ids are kept, so cards already tagged with an `sd-id:` marker keep their identity."""
    tasks = [
        Task(
            id=story.id,
            title=story.title,
            description=story.user_story,
            acceptance_criteria=list(story.acceptance_criteria),
            todos=[Todo(id=t.id, title=t.title, description=t.description) for t in story.tasks],
        )
        for story in decomposition.stories
    ]
    plan = Plan(
        name=decomposition.epic_key,
        categories=[Category(title=decomposition.epic_key, tasks=tasks)],
    )
    assign_plan_ids(plan)
    return plan
