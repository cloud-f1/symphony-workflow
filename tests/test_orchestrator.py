"""Unit tests for orchestrator.py helpers not already covered indirectly via
test_api.py (execute_plan/execute_implement) and test_poller.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from jira_agent_cli.orchestrator import render_run_doc


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
