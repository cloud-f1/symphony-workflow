"""Layer 3 of the plan: where a Task's To-Dos come from (ADLC-201, plan 0008 section 4).

In order, and the project picks with `todo_source` in `docs/context/plan-sync.json`:

1. the Task's **own breakdown** (read by the source adapter; this module never touches it),
2. an **approved proposal** from the `decompose`-style agent (`split-todos`), which is written to
   `docs/context/todo-proposal.{json,md}` for a human to edit and approve first. Nothing is
   written to a tracker before that,
3. the **five workflow steps** (`plan.STEP_TITLES`), deterministic and free.

`hybrid` = 1, then 2, then 3. `breakdown` = 1 only. `ai-split` = 1, then 2. `steps` = 1, then 3.
Every source ends up inside the 2-to-6 To-Do guardrail or is reported; the agent's output is
normalised to it (`normalise_todos`), an own breakdown outside it is passed through with a warning
(`plan.check_plan`) because inventing or dropping items would change what the author wrote.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from pathlib import Path

from pydantic import BaseModel, Field, ValidationError

from symphony_workflow.plan import MAX_TODOS, MIN_TODOS, STEP_TITLES, Plan, Task, Todo
from symphony_workflow.safepath import check_inside

PROPOSAL_JSON = "docs/context/todo-proposal.json"
PROPOSAL_MD = "docs/context/todo-proposal.md"
TESTS_TODO = "Tests: cover the acceptance criteria"

# What the agent is asked for: a flat list, nothing else. The code decides where it goes.
TODO_SPLIT_SCHEMA = {
    "type": "object",
    "properties": {"todos": {"type": "array", "items": {"type": "string"}}},
    "required": ["todos"],
}

Splitter = Callable[[Task], Awaitable[list[str]]]


class Proposal(BaseModel):
    """What a human reviews: To-Do titles per Task id. `approved` flips only on `--approve`."""

    approved: bool = False
    tasks: dict[str, list[str]] = Field(default_factory=dict)


def five_steps() -> list[Todo]:
    return [Todo(title=title) for title in STEP_TITLES]


def normalise_todos(titles: list[str]) -> list[str]:
    """Inside the guardrail: no blanks, at most `MAX_TODOS`, the last one is the tests."""
    cleaned = [t.strip() for t in titles if t and t.strip()]
    tests = next((t for t in reversed(cleaned) if t.lower().startswith("test")), TESTS_TODO)
    body = [t for t in cleaned if t != tests]
    body = body[: MAX_TODOS - 1]
    result = [*body, tests]
    while len(result) < MIN_TODOS:  # a lone item is a Task, not a To-Do list
        result.insert(0, "Implement the task")
    return result


def _split_prompt(task: Task) -> str:
    criteria = "\n".join(f"- {c}" for c in task.acceptance_criteria) or "- (none written)"
    return (
        f"Split this task into 3 to 5 To-Dos for a tracker. Each one is a short verb-first action "
        f"a single person finishes in one sitting, the last one is the tests. Do not copy the "
        f"acceptance criteria one by one.\n\nTask: {task.title}\n{task.description}\n\n"
        f"Acceptance criteria:\n{criteria}\n"
    )


async def split_with_agent(task: Task, *, cwd: str) -> list[str]:
    """One read-only Plan-phase agent run (`permission_mode="plan"`), same seam as `decompose`."""
    from symphony_workflow.agent_runner import run_plan  # the SDK is imported only when used

    result = await run_plan(
        _split_prompt(task), cwd=cwd, max_turns=3, output_schema=TODO_SPLIT_SCHEMA
    )
    raw = (result.structured or {}).get("todos")
    if not isinstance(raw, list):
        raise ValueError(f"the agent produced no `todos` list for {task.id or task.title}")
    return [str(item) for item in raw]


def _tasks_needing_todos(plan: Plan) -> list[Task]:
    return [t for c in plan.categories for t in c.tasks if not t.todos and t.id]


async def propose(plan: Plan, splitter: Splitter) -> Proposal:
    """Ask for To-Dos for every Task that has none. Never approved here."""
    proposal = Proposal()
    for task in _tasks_needing_todos(plan):
        proposal.tasks[task.id or task.title] = normalise_todos(await splitter(task))
    return proposal


def render_proposal_markdown(proposal: Proposal, plan: Plan) -> str:
    titles = {t.id: t.title for c in plan.categories for t in c.tasks}
    lines = [
        "# To-Do proposal",
        "",
        "Edit `todo-proposal.json` (this file is only a view), then run "
        "`symphony-workflow split-todos --approve`. Nothing reaches a tracker before that.",
        "",
        f"Status: {'approved' if proposal.approved else 'pending review'}",
        "",
    ]
    for task_id, todos in proposal.tasks.items():
        lines.append(f"## {task_id} {titles.get(task_id, '')}".rstrip())
        lines.extend(f"- [ ] {t}" for t in todos)
        lines.append("")
    return "\n".join(lines)


def save_proposal(root: Path, proposal: Proposal, plan: Plan) -> tuple[Path, Path]:
    json_path, md_path = root / PROPOSAL_JSON, root / PROPOSAL_MD
    for path in (json_path, md_path):
        check_inside(root, path)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(proposal.model_dump_json(indent=2) + "\n", encoding="utf-8")
    md_path.write_text(render_proposal_markdown(proposal, plan), encoding="utf-8")
    return json_path, md_path


def load_proposal(root: Path) -> Proposal | None:
    path = root / PROPOSAL_JSON
    check_inside(root, path)
    if not path.is_file():
        return None
    try:
        return Proposal.model_validate(json.loads(path.read_text(encoding="utf-8")))
    except (ValueError, ValidationError) as exc:
        raise ValueError(f"{path} is not a valid To-Do proposal: {exc}") from exc


def approve_proposal(root: Path, plan: Plan) -> Proposal:
    """Mark the saved proposal approved after re-normalising it (a human may have edited it)."""
    proposal = load_proposal(root)
    if proposal is None:
        raise FileNotFoundError(f"{root / PROPOSAL_JSON} does not exist; run `split-todos` first")
    proposal.tasks = {k: normalise_todos(v) for k, v in proposal.tasks.items()}
    proposal.approved = True
    save_proposal(root, proposal, plan)
    return proposal


def fill_todos(plan: Plan, source: str, proposal: Proposal | None = None) -> dict[str, str]:
    """Give every Task without To-Dos some, per `source`. Returns task id -> origin
    (`proposal` or `steps`); Tasks left empty are not in the result. Mutates `plan`."""
    origins: dict[str, str] = {}
    for task in _tasks_needing_todos(plan):
        task_id = task.id or task.title
        if source in ("hybrid", "ai-split") and proposal and proposal.approved:
            titles = proposal.tasks.get(task_id)
            if titles:
                task.todos = [Todo(title=t) for t in normalise_todos(titles)]
                origins[task_id] = "proposal"
                continue
        if source in ("hybrid", "steps"):
            task.todos = five_steps()
            origins[task_id] = "steps"
    return origins
