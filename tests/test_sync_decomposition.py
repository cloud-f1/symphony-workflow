"""Unit tests for sync_decomposition.py (ADLC-47). Jira HTTP is mocked with
respx (the only Jira calls this module makes are `client.create_issue`,
already covered by test_client.py's own respx-mocked tests for the
`parent=`-field mechanism -- this file focuses on the sync-specific reading
of decomposition.json + Story/Subtask creation loop).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from symphony_workflow.client import JiraClient
from symphony_workflow.config import JiraConfig
from symphony_workflow.decompose import Decomposition, Story, Task
from symphony_workflow.sync_decomposition import (
    SyncDecompositionError,
    execute_sync_decomposition,
    project_key_from_issue_key,
    render_story_description,
)

from .conftest import BASE_URL

DECOMPOSITION = Decomposition(
    epic_key="ADLC-27",
    stories=[
        Story(
            title="Add the widget model",
            user_story="As a user, I want a widget so that I can widge.",
            acceptance_criteria=["Widget model exists", "Widget model is typed"],
            tasks=[
                Task(title="Add Widget pydantic model", description="In models.py"),
                Task(title="Add respx test", description="In test_client.py"),
            ],
        ),
        Story(
            title="Expose widget over the API",
            user_story="As a user, I want to fetch widgets over HTTP.",
            acceptance_criteria=["GET /widgets works"],
            tasks=[Task(title="Add endpoint", description="In api.py")],
        ),
    ],
    definition_of_done=["Tests pass", "Lint clean"],
)


def _client() -> JiraClient:
    return JiraClient(JiraConfig(base_url=BASE_URL, email="bot@example.com", api_token="t"))


def _write_decomposition(
    runs_docs_root: Path, decomposition: Decomposition = DECOMPOSITION
) -> Path:
    out_dir = runs_docs_root / decomposition.epic_key
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "decomposition.json"
    path.write_text(decomposition.model_dump_json(indent=2), encoding="utf-8")
    return path


def _mock_create_issue() -> None:
    counter = {"n": 0}

    def _respond(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        key = f"ADLC-{100 + counter['n']}"
        return httpx.Response(201, json={"key": key, "id": str(100 + counter["n"])})

    respx.post(f"{BASE_URL}/rest/api/2/issue").mock(side_effect=_respond)


def test_project_key_from_issue_key() -> None:
    assert project_key_from_issue_key("ADLC-47") == "ADLC"
    assert project_key_from_issue_key("CF1-123") == "CF1"


def test_project_key_from_issue_key_rejects_unshaped_key() -> None:
    with pytest.raises(SyncDecompositionError, match="Can't derive a project key"):
        project_key_from_issue_key("nodash")


def test_render_story_description_includes_ac_and_dod_without_atx_headers() -> None:
    story = DECOMPOSITION.stories[0]
    description = render_story_description(story, DECOMPOSITION.definition_of_done)

    assert story.user_story in description
    assert "Acceptance Criteria:" in description
    assert "- Widget model exists" in description
    assert "Definition of Done:" in description
    assert "- [ ] Tests pass" in description
    assert "- [ ] Lint clean" in description
    assert "#" not in description


@respx.mock
def test_execute_sync_decomposition_creates_stories_and_subtasks_with_parent(
    tmp_path: Path,
) -> None:
    _write_decomposition(tmp_path)
    _mock_create_issue()

    with _client() as client:
        result = execute_sync_decomposition("ADLC-27", client=client, runs_docs_root=tmp_path)

    assert result.epic_key == "ADLC-27"
    assert result.project_key == "ADLC"
    assert len(result.stories) == 2
    assert result.stories[0].tasks[0].title == "Add Widget pydantic model"
    assert result.stories[1].tasks[0].title == "Add endpoint"

    # 2 stories + 2 + 1 tasks = 5 create_issue calls total.
    assert len(respx.calls) == 5

    story_call = json.loads(respx.calls[0].request.content)
    assert story_call["fields"]["project"] == {"key": "ADLC"}
    assert story_call["fields"]["issuetype"] == {"name": "Story"}
    assert story_call["fields"]["parent"] == {"key": "ADLC-27"}
    assert "Acceptance Criteria:" in story_call["fields"]["description"]
    assert "- [ ] Tests pass" in story_call["fields"]["description"]

    task_call = json.loads(respx.calls[1].request.content)
    assert task_call["fields"]["issuetype"] == {"name": "Subtask"}
    assert task_call["fields"]["parent"] == {"key": result.stories[0].key}
    assert task_call["fields"]["description"] == "In models.py"


@respx.mock
def test_execute_sync_decomposition_honors_explicit_project_override(tmp_path: Path) -> None:
    _write_decomposition(tmp_path)
    _mock_create_issue()

    with _client() as client:
        result = execute_sync_decomposition(
            "ADLC-27", client=client, runs_docs_root=tmp_path, project_key="OTHER"
        )

    assert result.project_key == "OTHER"
    story_call = json.loads(respx.calls[0].request.content)
    assert story_call["fields"]["project"] == {"key": "OTHER"}


def test_execute_sync_decomposition_raises_clear_error_when_file_missing(
    tmp_path: Path,
) -> None:
    with _client() as client:
        with pytest.raises(SyncDecompositionError, match="symphony-workflow decompose ADLC-27"):
            execute_sync_decomposition("ADLC-27", client=client, runs_docs_root=tmp_path)


def test_execute_sync_decomposition_raises_on_epic_key_mismatch(tmp_path: Path) -> None:
    out_dir = tmp_path / "ADLC-99"
    out_dir.mkdir(parents=True)
    (out_dir / "decomposition.json").write_text(
        DECOMPOSITION.model_dump_json(indent=2), encoding="utf-8"
    )

    with _client() as client:
        with pytest.raises(SyncDecompositionError, match="stale or hand-edited"):
            execute_sync_decomposition("ADLC-99", client=client, runs_docs_root=tmp_path)


def test_execute_sync_decomposition_raises_on_malformed_json(tmp_path: Path) -> None:
    out_dir = tmp_path / "ADLC-27"
    out_dir.mkdir(parents=True)
    (out_dir / "decomposition.json").write_text("{not valid json", encoding="utf-8")

    with _client() as client:
        with pytest.raises(SyncDecompositionError, match="is malformed"):
            execute_sync_decomposition("ADLC-27", client=client, runs_docs_root=tmp_path)


def test_execute_sync_decomposition_raises_on_empty_stories(tmp_path: Path) -> None:
    empty = Decomposition(epic_key="ADLC-27", stories=[], definition_of_done=["x"])
    _write_decomposition(tmp_path, empty)

    with _client() as client:
        with pytest.raises(SyncDecompositionError, match="nothing to sync"):
            execute_sync_decomposition("ADLC-27", client=client, runs_docs_root=tmp_path)


@respx.mock
def test_execute_sync_decomposition_never_touches_filesystem_for_project_lookup(
    tmp_path: Path,
) -> None:
    # No `--project` given, no epic GET call made -- project key is derived
    # purely from the epic key string, no extra Jira round trip.
    _write_decomposition(tmp_path)
    _mock_create_issue()

    with _client() as client:
        execute_sync_decomposition("ADLC-27", client=client, runs_docs_root=tmp_path)

    for call in respx.calls:
        assert call.request.method == "POST"
        assert call.request.url.path == "/rest/api/2/issue"
