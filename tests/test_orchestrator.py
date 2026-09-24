"""Unit tests for orchestrator.py helpers not already covered indirectly via
test_api.py (execute_plan/execute_implement) and test_poller.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jira_agent_cli.orchestrator import jira_safe_comment_body, render_run_doc


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
