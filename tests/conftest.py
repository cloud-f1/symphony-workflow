"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from symphony_workflow.config import JiraConfig

BASE_URL = "https://example.atlassian.net"


@pytest.fixture
def config() -> JiraConfig:
    return JiraConfig(base_url=BASE_URL, email="bot@example.com", api_token="token")
