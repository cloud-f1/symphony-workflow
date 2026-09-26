"""Shared pytest fixtures."""

from __future__ import annotations

import pytest

from symphony_workflow.config import JiraConfig, LinearConfig

BASE_URL = "https://example.atlassian.net"
LINEAR_API_URL = "https://api.linear.app/graphql"


@pytest.fixture
def config() -> JiraConfig:
    return JiraConfig(base_url=BASE_URL, email="bot@example.com", api_token="token")


@pytest.fixture
def linear_config() -> LinearConfig:
    return LinearConfig(api_key="lin_api_test_token", team_key="GAI")
