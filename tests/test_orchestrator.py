"""Unit tests for orchestrator.py helpers not already covered indirectly via
test_api.py (execute_plan/execute_implement) and test_poller.py -- plus a
direct execute_plan test for ADLC-56's empty-text-but-structured-present
fallback, since that regression is about execute_plan's own plan.md/comment
rendering, not something test_api.py's happy-path fixtures happen to cover.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
import respx

import jira_agent_cli.orchestrator as orchestrator_module
from jira_agent_cli.agent_runner import AgentRunError, AgentRunResult
from jira_agent_cli.client import JiraClient, JiraError
from jira_agent_cli.config import JiraConfig
from jira_agent_cli.models import Issue, TriageDecision
from jira_agent_cli.orchestrator import (
    cleanup_workspace_if_terminal,
    execute_plan,
    execute_triage,
    jira_safe_comment_body,
    render_run_doc,
    sweep_terminal_workspaces,
)
from jira_agent_cli.runs import RunStore, TriageStore

from .conftest import BASE_URL


@pytest.fixture(autouse=True)
def _clear_cleanup_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # cleanup_workspace_if_terminal/sweep_terminal_workspaces both read env
    # vars at call time (see orchestrator._cleanup_enabled) — start every test
    # from the documented off-by-default state, same reasoning as
    # test_cli.py's env-clearing fixture.
    monkeypatch.delenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", raising=False)
    monkeypatch.delenv("JIRA_AGENT_CLI_TERMINAL_STATUSES", raising=False)


def _issue(status: str) -> MagicMock:
    issue = MagicMock()
    issue.status = status
    return issue


def test_cleanup_disabled_by_default_never_calls_jira() -> None:
    client = MagicMock()
    workspaces = MagicMock()

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is False
    client.get_issue.assert_not_called()
    workspaces.remove.assert_not_called()


def test_cleanup_removes_workspace_when_status_is_terminal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    client.get_issue.return_value = _issue("Done")
    workspaces = MagicMock()
    workspaces.remove.return_value = True

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is True
    workspaces.remove.assert_called_once_with("ADLC-1")


def test_cleanup_leaves_non_terminal_workspace_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    client.get_issue.return_value = _issue("In Progress")
    workspaces = MagicMock()

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is False
    workspaces.remove.assert_not_called()


def test_cleanup_respects_custom_terminal_statuses(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    monkeypatch.setenv("JIRA_AGENT_CLI_TERMINAL_STATUSES", "Done, Won't Do")
    client = MagicMock()
    client.get_issue.return_value = _issue("Won't Do")
    workspaces = MagicMock()
    workspaces.remove.return_value = True

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is True


def test_cleanup_swallows_jira_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    client.get_issue.side_effect = JiraError(500, "boom")
    workspaces = MagicMock()

    removed = cleanup_workspace_if_terminal("ADLC-1", client=client, workspaces=workspaces)

    assert removed is False
    workspaces.remove.assert_not_called()


def test_sweep_disabled_by_default_does_nothing(tmp_path: Path) -> None:
    (tmp_path / "ADLC-1").mkdir()
    client = MagicMock()
    workspaces = MagicMock()
    workspaces.root = tmp_path

    removed = sweep_terminal_workspaces(client=client, workspaces=workspaces)

    assert removed == []
    client.get_issue.assert_not_called()


def test_sweep_removes_only_terminal_issues_and_skips_non_directories(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    (tmp_path / "ADLC-1").mkdir()
    (tmp_path / "ADLC-2").mkdir()
    (tmp_path / "not-a-workspace.txt").write_text("stray file")

    client = MagicMock()
    statuses = {"ADLC-1": "Done", "ADLC-2": "In Progress"}
    client.get_issue.side_effect = lambda key: _issue(statuses[key])

    workspaces = MagicMock()
    workspaces.root = tmp_path
    workspaces.remove.return_value = True

    removed = sweep_terminal_workspaces(client=client, workspaces=workspaces)

    assert removed == ["ADLC-1"]
    workspaces.remove.assert_called_once_with("ADLC-1")


def test_sweep_returns_empty_list_when_workspace_root_does_not_exist(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("JIRA_AGENT_CLI_WORKSPACE_CLEANUP_ENABLED", "true")
    client = MagicMock()
    workspaces = MagicMock()
    workspaces.root = tmp_path / "does-not-exist"

    assert sweep_terminal_workspaces(client=client, workspaces=workspaces) == []


def test_render_run_doc_prepends_jira_url_frontmatter() -> None:
    client = MagicMock()
    client.base_url = "https://cloud-f1.atlassian.net"

    doc = render_run_doc(client, "ADLC-17", "Here is the plan.")

    assert doc == (
        "---\njira_url: https://cloud-f1.atlassian.net/browse/ADLC-17\n---\n\nHere is the plan."
    )


def test_render_run_doc_frontmatter_is_parseable_yaml_prefix() -> None:
    client = MagicMock()
    client.base_url = "https://cloud-f1.atlassian.net"

    doc = render_run_doc(client, "ADLC-24", "body text")
    lines = doc.splitlines()

    assert lines[0] == "---"
    assert lines[1] == "jira_url: https://cloud-f1.atlassian.net/browse/ADLC-24"
    assert lines[2] == "---"


def test_jira_safe_comment_body_converts_atx_headers_to_bold() -> None:
    # Reproduces the garbled ADLC-28/ADLC-30 comment: Jira's wiki-markup
    # renderer treats a leading '#'/'##'/'###' as nested ordered-list depth,
    # not a heading, so raw GFM headers must become bold lines instead.
    text = "## Plan: ADLC-30\n\n### Context\nSome body text.\n#### Deep\nmore"

    result = jira_safe_comment_body(text)

    assert result == ("**Plan: ADLC-30**\n\n**Context**\nSome body text.\n**Deep**\nmore")
    assert "#" not in result


def test_jira_safe_comment_body_leaves_non_header_text_untouched() -> None:
    text = "**already bold** and `inline code` and a normal line\n- a bullet"

    result = jira_safe_comment_body(text)

    assert result == text


# --- execute_triage (ADLC-29) --------------------------------------------


def _candidate(key: str, *, status: str = "To Do") -> Issue:
    return Issue(key=key, id=key, summary=f"Ticket {key}", status=status, description="desc")


@pytest.mark.asyncio
async def test_execute_triage_empty_candidates_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("run_triage should not be called for an empty batch")

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_triage", fail_if_called)

    decisions = await execute_triage([], client=MagicMock(), triage_store=TriageStore())

    assert decisions == []


@pytest.mark.asyncio
async def test_execute_triage_plan_decision_is_noop_here_but_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        return [TriageDecision(issue_key="ADLC-1", action="plan", reason="well-formed")]

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_triage", fake_run_triage)
    client = MagicMock()
    store = TriageStore()

    decisions = await execute_triage([_candidate("ADLC-1")], client=client, triage_store=store)

    assert [d.action for d in decisions] == ["plan"]
    client.add_comment.assert_not_called()
    # Recorded (not just flag/skip) so a plan-decided ticket isn't re-queued
    # for triage forever on the next poll tick -- see execute_triage's
    # docstring.
    assert store.get("ADLC-1").action == "plan"


@pytest.mark.asyncio
@respx.mock
async def test_execute_triage_flag_posts_a_comment(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        return [TriageDecision(issue_key="ADLC-2", action="flag", reason="ambiguous scope")]

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_triage", fake_run_triage)
    comment_route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-2/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    store = TriageStore()

    decisions = await execute_triage(
        [_candidate("ADLC-2")], client=JiraClient(config), triage_store=store
    )

    assert [d.action for d in decisions] == ["flag"]
    assert comment_route.called
    body = json.loads(comment_route.calls.last.request.content)["body"]
    assert "ambiguous scope" in body
    assert "triage" in body
    assert store.get("ADLC-2").action == "flag"


@pytest.mark.asyncio
async def test_execute_triage_skip_records_and_is_not_re_triaged_on_second_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        nonlocal call_count
        call_count += 1
        return [TriageDecision(issue_key="ADLC-3", action="skip", reason="duplicate summary")]

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_triage", fake_run_triage)
    client = MagicMock()
    store = TriageStore()

    first = await execute_triage([_candidate("ADLC-3")], client=client, triage_store=store)
    assert [d.action for d in first] == ["skip"]
    client.add_comment.assert_not_called()
    assert store.get("ADLC-3").action == "skip"

    # execute_triage itself doesn't consult the store to skip re-running --
    # that's poller.py's job (it never re-queues an already-triaged key). A
    # second explicit call re-triages, but the store still reflects the
    # latest decision and add_comment is never called for "skip" either way.
    assert call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_execute_triage_flag_comment_post_failure_is_swallowed(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fake_run_triage(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        return [TriageDecision(issue_key="ADLC-4", action="flag", reason="risky")]

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_triage", fake_run_triage)
    respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-4/comment").mock(
        return_value=httpx.Response(500, json={"errorMessages": ["boom"]})
    )
    store = TriageStore()

    decisions = await execute_triage(
        [_candidate("ADLC-4")], client=JiraClient(config), triage_store=store
    )

    assert [d.action for d in decisions] == ["flag"]
    # Comment post failed but did not raise -- and the decision is still
    # recorded so it isn't retried every triage cycle.
    assert store.get("ADLC-4").action == "flag"


@pytest.mark.asyncio
async def test_execute_triage_propagates_agent_run_error(monkeypatch: pytest.MonkeyPatch) -> None:
    async def always_fails(candidates: list[dict], *, cwd: str) -> list[TriageDecision]:
        raise AgentRunError("budget exceeded")

    monkeypatch.setattr("jira_agent_cli.orchestrator.run_triage", always_fails)

    with pytest.raises(AgentRunError):
        await execute_triage([_candidate("ADLC-5")], client=MagicMock(), triage_store=TriageStore())


# --- execute_plan: ADLC-56 empty-text-but-structured fallback -------------


class _FakeWorkspaceManager:
    """Avoids a real `git worktree add` against this repo. Mirrors
    test_api.py's `_FakeWorkspaceManager` -- kept local here rather than
    imported from test_api.py so this module doesn't take on a cross-test-
    file dependency for one fixture.
    """

    def ensure(self, issue_key: str, *, branch_slug: str | None = None) -> Path:
        return Path("/tmp/fake-workspace") / issue_key

    def run_before_run(self, issue_key: str, workspace_path: Path) -> None:
        pass

    def run_after_run(self, issue_key: str, workspace_path: Path) -> None:
        pass


def _mock_get_issue(key: str) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/{key}").mock(
        return_value=httpx.Response(
            200,
            json={
                "key": key,
                "id": "1",
                "fields": {"summary": "Do the thing", "status": {"name": "To Do"}},
            },
        )
    )


def _mock_transition(key: str, name: str) -> None:
    respx.get(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(200, json={"transitions": [{"id": "1", "name": name}]})
    )
    respx.post(f"{BASE_URL}/rest/api/2/issue/{key}/transitions").mock(
        return_value=httpx.Response(204)
    )


@pytest.mark.asyncio
@respx.mock
async def test_execute_plan_falls_back_to_structured_render_when_text_is_empty(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Reproduces ADLC-56 live: a Plan session's final turn emitted only the
    structured `output_format` result with no accompanying TextBlock, so
    `result.text` came back "" while `result.structured` was fully
    populated. Before the fix, this silently wrote a blank plan.md and
    posted a blank Jira comment despite there being a valuable structured
    result sitting right there -- this test fails against the old code
    (which wrote/posted `result.text` verbatim, i.e. "") and passes against
    the fix (`orchestrator._render_plan_text`'s fallback).
    """
    monkeypatch.setattr(orchestrator_module, "RUNS_DOCS_ROOT", tmp_path / "runs")

    _mock_get_issue("ADLC-56")
    comment_route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-56/comment").mock(
        return_value=httpx.Response(201, json={})
    )
    _mock_transition("ADLC-56", "In Review")

    structured = {
        "summary": "Add a version constant to the poller module.",
        "files_changed": ["src/jira_agent_cli/poller.py"],
        "risks": ["None -- additive only."],
    }

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(
            text="", session_id="sess-56", cost_usd=0.01, num_turns=3, structured=structured
        )

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)

    client = JiraClient(config)
    runs = RunStore()
    run = runs.create("ADLC-56")

    updated = await execute_plan(run, runs=runs, client=client, workspaces=_FakeWorkspaceManager())

    # The Run's own `plan` field must not be blank.
    assert updated.state.value == "plan_ready"
    assert updated.plan
    assert "Add a version constant" in updated.plan
    assert updated.plan_structured == structured

    # plan.md must not be blank, and must carry the structured content.
    plan_md = (tmp_path / "runs" / "ADLC-56" / "plan.md").read_text(encoding="utf-8")
    assert "Add a version constant to the poller module." in plan_md
    assert "src/jira_agent_cli/poller.py" in plan_md
    assert "None -- additive only." in plan_md

    # The Jira comment must not be blank either.
    assert comment_route.called
    comment_body = json.loads(comment_route.calls.last.request.content)["body"]
    assert "Add a version constant to the poller module." in comment_body
    assert "src/jira_agent_cli/poller.py" in comment_body


@pytest.mark.asyncio
@respx.mock
async def test_execute_plan_raises_when_text_and_structured_are_both_empty(
    config: JiraConfig, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The pre-existing ADLC-56 fallback-of-last-resort: if there is truly no
    text AND no usable structured output, execute_plan must fail loudly
    (mark the Run failed, post a failure comment, raise AgentRunError)
    rather than silently reaching plan_ready with nothing to show for it.
    """
    monkeypatch.setattr(orchestrator_module, "RUNS_DOCS_ROOT", tmp_path / "runs")

    _mock_get_issue("ADLC-57")
    failure_comment_route = respx.post(f"{BASE_URL}/rest/api/2/issue/ADLC-57/comment").mock(
        return_value=httpx.Response(201, json={})
    )

    async def fake_run_plan(prompt: str, *, cwd: str, **kwargs: object) -> AgentRunResult:
        return AgentRunResult(text="", session_id="sess-57", cost_usd=0.01, num_turns=1)

    monkeypatch.setattr(orchestrator_module, "run_plan", fake_run_plan)

    client = JiraClient(config)
    runs = RunStore()
    run = runs.create("ADLC-57")

    with pytest.raises(AgentRunError):
        await execute_plan(run, runs=runs, client=client, workspaces=_FakeWorkspaceManager())

    stored = runs.get(run.id)
    assert stored is not None
    assert stored.state.value == "failed"
    assert not (tmp_path / "runs" / "ADLC-57" / "plan.md").exists()
    assert failure_comment_route.called
    failure_body = json.loads(failure_comment_route.calls.last.request.content)["body"]
    assert "FAILED" in failure_body
