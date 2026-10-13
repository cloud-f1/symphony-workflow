"""Project profile and adaptive recommendation (ADLC-199; design:
docs/plans/0008-adaptive-plan-sync/spec.md).

`profile_project` reads how a project already works, read-only, and `recommend` turns that into two
recommendations: the first from *usage* (which tracker, which plan source), the second refined from
the
*content* (shape of the plan files). The project chooses; the choice is saved in
`docs/context/plan-sync.json`.

Credentials: only whether the key names are set is read, never a value, and no value reaches any
output.
"""

from __future__ import annotations

import os
import re
import statistics
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from symphony_workflow.envsetup import REQUIRED_KEYS, parse_env
from symphony_workflow.epics_md import (
    EPICS_DIR,
    PROGRESS_FILE,
    parse_progress,
    progress_path,
    scan_epics,
)
from symphony_workflow.safepath import is_safe, run_git

CONFIG_PATH = Path("docs/context/plan-sync.json")
TRACKER_ENV = "SYMPHONY_WORKFLOW_TRACKER"
LINEAR_KEY_ENV = "LINEAR_API_KEY"

_BRANCH_KEY_RE = re.compile(r"^([A-Z][A-Z0-9]{1,9})-\d+")
_SUBJECT_KEY_RE = re.compile(r"(?:\(([A-Z][A-Z0-9]{1,9})-\d+\)\s*$|^([A-Z][A-Z0-9]{1,9})-\d+\b)")

# Thresholds from the survey of about 20 projects (spec section 2).
MANY_PHASES = 20
PHASES_SHARE_FOR_PHASES_FIRST = 0.3
MANY_ACCEPTANCE_ITEMS = 8
MAX_TASKS_PER_CATEGORY = 8


class PlanSyncConfig(BaseModel):
    """What the project picked. No secrets: a tracker name, never a credential."""

    tracker: Literal["jira", "linear", "markdown"] = "markdown"
    plan_source: Literal["epics-md", "decomposition"] = "epics-md"
    todo_source: Literal["hybrid", "breakdown", "ai-split", "steps"] = "hybrid"
    push: Literal["manual"] = "manual"  # `on-approve` is not in the first release
    record: Literal["markdown", "none"] = "markdown"
    include_done: bool = False


def load_config(root: Path) -> PlanSyncConfig | None:
    path = root / CONFIG_PATH
    if not path.is_file():
        return None
    try:
        return PlanSyncConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise ValueError(
            f"{path} is not a valid plan-sync config: {exc.error_count()} problem(s)"
        ) from exc


def save_config(root: Path, config: PlanSyncConfig) -> Path:
    path = root / CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


@dataclass
class PlanSignals:
    epics: int = 0
    has_index: bool = False
    has_progress: bool = False
    has_strategy_log: bool = False
    plan_folders: int = 0
    athena_commands: bool = False


@dataclass
class TrackerSignals:
    # In this project's own `.env`: evidence the project uses that tracker. Names only; a value is
    # never read out.
    jira_credentials: bool = False  # all three JIRA_* names are set
    linear_credentials: bool = False
    # In the shell environment: shared by every project, so only weak evidence on its own.
    jira_in_environment: bool = False
    linear_in_environment: bool = False
    tracker_env: str | None = None  # SYMPHONY_WORKFLOW_TRACKER, a setting and not a secret
    key_prefixes: dict[str, int] = field(
        default_factory=dict
    )  # from branch names and commit subjects


@dataclass
class ContentShape:
    epics: int = 0
    phases: int = 0
    open_phases: int = 0
    tasks_per_category_median: float | None = None
    acceptance_median: float | None = None
    ready_todo_share: float = 0.0


@dataclass
class Profile:
    root: str
    plan: PlanSignals
    tracker: TrackerSignals
    content: ContentShape

    def as_dict(self) -> dict[str, Any]:
        return {
            "root": self.root,
            "plan": self.plan.__dict__,
            "tracker": self.tracker.__dict__,
            "content": self.content.__dict__,
        }


def _project_env_names(root: Path) -> set[str]:
    """Names with a non-empty value in the project's own `.env` (never the values)."""
    env_file = root / ".env"
    if not env_file.is_file() or not is_safe(root, env_file):
        return set()
    values = parse_env(env_file.read_text(encoding="utf-8", errors="replace"))
    return {k for k, v in values.items() if v}


def _shell_env_names() -> set[str]:
    return {k for k, v in os.environ.items() if v}


def _git_lines(root: Path, *args: str) -> list[str]:
    proc = run_git(root, *args)
    return proc.stdout.splitlines() if proc is not None and proc.returncode == 0 else []


def _key_prefixes(root: Path) -> dict[str, int]:
    counts: Counter[str] = Counter()
    for branch in _git_lines(root, "branch", "-a", "--format=%(refname:short)"):
        match = _BRANCH_KEY_RE.match(branch.split("/")[-1])
        if match:
            counts[match.group(1)] += 1
    for subject in _git_lines(root, "log", "-n", "200", "--format=%s"):
        match = _SUBJECT_KEY_RE.search(subject)
        if match:
            counts[match.group(1) or match.group(2)] += 1
    return dict(counts.most_common(5))


def profile_project(root: Path) -> Profile:
    """Facts only; no recommendation. Read-only."""
    root = root.resolve()
    epics = scan_epics(root)
    rows = parse_progress(progress_path(root))
    plan = PlanSignals(
        epics=len(epics),
        has_index=(root / EPICS_DIR / "EPIC_INDEX.md").is_file(),
        has_progress=(root / PROGRESS_FILE).is_file(),
        has_strategy_log=(root / "docs/context/strategy-log.md").is_file(),
        plan_folders=len([p for p in (root / "docs/plans").glob("*/") if p.is_dir()]),
        athena_commands=(root / ".claude/commands/athena").is_dir(),
    )
    project_names, shell_names = _project_env_names(root), _shell_env_names()
    tracker = TrackerSignals(
        jira_credentials=all(k in project_names for k in REQUIRED_KEYS),
        linear_credentials=LINEAR_KEY_ENV in project_names,
        jira_in_environment=all(k in shell_names for k in REQUIRED_KEYS),
        linear_in_environment=LINEAR_KEY_ENV in shell_names,
        tracker_env=os.environ.get(TRACKER_ENV) or None,
        key_prefixes=_key_prefixes(root),
    )
    acceptance = [len(e.acceptance_criteria) for e in epics if e.acceptance_criteria]
    per_phase = [len(r.epic_ids) for r in rows if r.epic_ids]
    content = ContentShape(
        epics=len(epics),
        phases=len(rows),
        open_phases=sum(1 for r in rows if not r.complete),
        tasks_per_category_median=statistics.median(per_phase) if per_phase else None,
        acceptance_median=statistics.median(acceptance) if acceptance else None,
        ready_todo_share=(sum(1 for e in epics if e.todo_origin) / len(epics) if epics else 0.0),
    )
    return Profile(str(root), plan, tracker, content)


@dataclass
class Note:
    topic: str
    evidence: str
    advice: str

    def as_dict(self) -> dict[str, str]:
        return {"topic": self.topic, "evidence": self.evidence, "advice": self.advice}


@dataclass
class Recommendation:
    config: PlanSyncConfig
    notes: list[Note] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"config": self.config.model_dump(), "notes": [n.as_dict() for n in self.notes]}


def recommend_from_usage(profile: Profile) -> Recommendation:
    """Recommendation 1: which tracker and which plan source, from how the project is used today."""
    t, p = profile.tracker, profile.plan
    notes: list[Note] = []
    prefixes = ", ".join(f"{k} ({v})" for k, v in t.key_prefixes.items())
    jira_creds = t.jira_credentials or t.jira_in_environment
    if t.tracker_env in ("jira", "linear"):
        tracker = t.tracker_env
        notes.append(
            Note("tracker", f"{TRACKER_ENV}={t.tracker_env}", "explicitly configured: keep it")
        )
    elif t.jira_credentials or (jira_creds and t.key_prefixes):
        tracker = "jira"
        where = "this project's .env" if t.jira_credentials else "the environment"
        keys = f"; issue keys in history: {prefixes}" if t.key_prefixes else ""
        notes.append(Note("tracker", f"Jira credentials in {where}{keys}", "use Jira"))
    elif t.linear_credentials:
        tracker = "linear"
        notes.append(Note("tracker", f"{LINEAR_KEY_ENV} is in this project's .env", "use Linear"))
    elif t.key_prefixes:
        tracker = "markdown"
        notes.append(
            Note(
                "tracker",
                f"issue keys in history ({prefixes}) but no complete Jira credentials",
                "run `symphony-workflow init-env`, fill it in, then profile again to get Jira; "
                "Markdown until then",
            )
        )
    else:
        tracker = "markdown"
        notes.append(Note("tracker", "no tracker evidence in this project", "use Markdown files"))
    if t.linear_in_environment and not t.linear_credentials and tracker != "linear":
        notes.append(
            Note(
                "tracker",
                f"{LINEAR_KEY_ENV} is set in the shell, shared by every project, but not in this "
                "project's .env",
                "not enough to pick Linear; pass --to linear if you want it",
            )
        )
    if p.epics:
        source = "epics-md"
        notes.append(
            Note(
                "plan source",
                f"{p.epics} epic file(s) under docs/epics",
                "read them with `epics-md`",
            )
        )
    else:
        source = "decomposition"
        notes.append(
            Note("plan source", "no docs/epics files", "start from a reviewed decomposition.json")
        )
    config = PlanSyncConfig(tracker=tracker, plan_source=source)
    return Recommendation(config, notes)


def refine_from_content(profile: Profile, base: PlanSyncConfig) -> Recommendation:
    """Recommendation 2: adjust the first one from the shape of the plan files."""
    c = profile.content
    config = base.model_copy()
    notes: list[Note] = []
    if c.epics and c.ready_todo_share >= PHASES_SHARE_FOR_PHASES_FIRST:
        config.todo_source = "breakdown"
        notes.append(
            Note(
                "To-Do source",
                f"{c.ready_todo_share:.0%} of epics have a ready-made To-Do breakdown",
                "use each epic's own breakdown (to-do list, phases or story headings) as To-Dos",
            )
        )
    elif c.epics:
        notes.append(
            Note(
                "To-Do source",
                f"only {c.ready_todo_share:.0%} of epics have a ready-made To-Do breakdown",
                "hybrid: AI-split behind the review gate, five workflow steps as the fallback",
            )
        )
    if c.acceptance_median and c.acceptance_median > MANY_ACCEPTANCE_ITEMS:
        notes.append(
            Note(
                "acceptance criteria",
                f"median {c.acceptance_median:g} per epic",
                "keep them as a checklist in the Task description; never one To-Do each",
            )
        )
    if c.tasks_per_category_median and c.tasks_per_category_median > MAX_TASKS_PER_CATEGORY:
        notes.append(
            Note(
                "Category size",
                f"median {c.tasks_per_category_median:g} Tasks per phase",
                f"split Categories above {MAX_TASKS_PER_CATEGORY} Tasks",
            )
        )
    if c.phases >= MANY_PHASES and c.open_phases <= max(1, c.phases // 20):
        notes.append(
            Note(
                "history",
                f"{c.phases - c.open_phases} of {c.phases} phases are already complete",
                "keep include_done off; only not-yet-complete phases sync",
            )
        )
    if not c.epics:
        notes.append(Note("content", "no epic files to measure", "nothing to refine yet"))
    return Recommendation(config, notes)


def recommend(profile: Profile) -> tuple[Recommendation, Recommendation]:
    first = recommend_from_usage(profile)
    return first, refine_from_content(profile, first.config)
