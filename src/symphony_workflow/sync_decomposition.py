"""Materialize a human-reviewed Epic decomposition (ADLC-46) as real Jira
issues (ADLC-47).

Reads `docs/runs/<epic_key>/decomposition.json` -- written by `symphony-workflow
decompose` and, by the time this runs, already reviewed by a human as
`decomposition.md` -- and creates one Story per `Decomposition.stories` entry
(`parent=<epic_key>`, the standard-`parent`-field mechanism team-managed
projects like `ADLC` use for Story->Epic linking; see `client.py`'s
`create_issue` docstring and CLAUDE.md's "Jira API gotchas"), then one
Subtask per that Story's `tasks` entries (`parent=<story_key>`, the same
mechanism Subtask->Story linking uses).

This module does NOT re-run the decompose agent -- it only reads the
already-generated `decomposition.json` (never the Markdown; that file is for
human review only). Created issues land in Jira's default "To Do" state
untouched, so the existing poller / manual `symphony-workflow plan`+`implement` flow
picks up the new Stories unchanged -- this is a new front stage, not a
replacement. Subtasks are deliberately invisible to the poller's `READY_JQL`
(it excludes `issuetype in (Epic, Subtask)`, same as every other Subtask this
repo creates for its own tracking) -- planning/implementing happens at the
Story level, Subtasks are the finer-grained checklist underneath.

No client.py/tools.py changes needed: this reuses the existing
`client.create_issue(..., parent=...)` path in a loop, the same mechanism
already used to create this repo's own ADLC-44..50 subtasks -- no new Jira
operation is introduced here.
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ValidationError

from symphony_workflow.client import JiraClient
from symphony_workflow.decompose import Decomposition, Story


class SyncDecompositionError(RuntimeError):
    """Raised when decomposition.json is missing, stale, or malformed --
    i.e. this command was asked to sync something that isn't a faithful,
    reviewable decomposition for `epic_key`.
    """


class SyncedTask(BaseModel):
    title: str
    key: str
    url: str


class SyncedStory(BaseModel):
    title: str
    key: str
    url: str
    tasks: list[SyncedTask]


class SyncResult(BaseModel):
    epic_key: str
    project_key: str
    stories: list[SyncedStory]


def project_key_from_issue_key(issue_key: str) -> str:
    """`"ADLC-47"` -> `"ADLC"`. No project-lookup call needed -- Jira issue
    keys are always `<PROJECT_KEY>-<number>`, and this is the default project
    a sync targets when `--project` isn't given: the epic's own project.
    """
    prefix, sep, rest = issue_key.partition("-")
    if not sep or not prefix or not rest:
        raise SyncDecompositionError(
            f"Can't derive a project key from {issue_key!r} -- expected an issue key "
            "shaped like '<PROJECT>-<number>' (e.g. 'ADLC-27'). Pass --project explicitly."
        )
    return prefix


def render_story_description(story: Story, definition_of_done: list[str]) -> str:
    """Acceptance Criteria + this repo's Definition of Done as a checklist,
    appended to every Story issue this command creates. Plain text, not
    GitHub Markdown -- API v2 description bodies render through Jira wiki
    markup, where a leading '#' means numbered-list depth, not a heading
    (CLAUDE.md's "Jira API gotchas"), so this deliberately avoids ATX
    headers and sticks to plain lines + '-' bullets.
    """
    lines = [story.user_story, "", "Acceptance Criteria:"]
    lines.extend(f"- {criterion}" for criterion in story.acceptance_criteria)
    lines.append("")
    lines.append("Definition of Done:")
    lines.extend(f"- [ ] {item}" for item in definition_of_done)
    return "\n".join(lines)


def _load_decomposition(epic_key: str, runs_docs_root: Path) -> Decomposition:
    path = runs_docs_root / epic_key / "decomposition.json"
    if not path.exists():
        raise SyncDecompositionError(
            f"No decomposition found for {epic_key} at {path} -- run "
            f"`symphony-workflow decompose {epic_key}` first, review the rendered "
            f"{runs_docs_root / epic_key / 'decomposition.md'}, then re-run "
            "`symphony-workflow sync-decomposition`."
        )
    try:
        decomposition = Decomposition.model_validate_json(path.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise SyncDecompositionError(f"{path} is malformed: {exc}") from exc

    if decomposition.epic_key != epic_key:
        raise SyncDecompositionError(
            f"{path} was generated for epic {decomposition.epic_key!r}, not "
            f"{epic_key!r} -- stale or hand-edited file. Re-run `symphony-workflow "
            f"decompose {epic_key}` to regenerate it before syncing."
        )
    if not decomposition.stories:
        raise SyncDecompositionError(f"{path} has no stories -- nothing to sync.")
    return decomposition


def execute_sync_decomposition(
    epic_key: str,
    *,
    client: JiraClient,
    runs_docs_root: Path,
    project_key: str | None = None,
) -> SyncResult:
    """Read the already-reviewed `decomposition.json` for `epic_key` and
    create one real Story per entry (`parent=epic_key`) plus one real
    Subtask per Story's `tasks` (`parent=<story_key>`).

    `project_key` defaults to `epic_key`'s own project (the prefix before
    the dash) -- pass it explicitly only to target a different project than
    the epic's.

    Raises `SyncDecompositionError` if `decomposition.json` is missing,
    stale (epic_key mismatch), empty, or fails schema validation, and
    `JiraError` if any issue-creation call fails partway through. Jira has
    no create-issue transaction, so issues already created before a failure
    stay created -- the caller (CLI layer) is responsible for telling the
    human what was and wasn't created.
    """
    decomposition = _load_decomposition(epic_key, runs_docs_root)
    resolved_project_key = project_key or project_key_from_issue_key(epic_key)

    synced_stories: list[SyncedStory] = []
    for story in decomposition.stories:
        story_result = client.create_issue(
            resolved_project_key,
            story.title,
            render_story_description(story, decomposition.definition_of_done),
            "Story",
            parent=epic_key,
        )
        synced_tasks: list[SyncedTask] = []
        for task in story.tasks:
            task_result = client.create_issue(
                resolved_project_key,
                task.title,
                task.description,
                "Subtask",
                parent=story_result.key,
            )
            synced_tasks.append(
                SyncedTask(title=task.title, key=task_result.key, url=task_result.url)
            )
        synced_stories.append(
            SyncedStory(
                title=story.title,
                key=story_result.key,
                url=story_result.url,
                tasks=synced_tasks,
            )
        )

    return SyncResult(epic_key=epic_key, project_key=resolved_project_key, stories=synced_stories)
