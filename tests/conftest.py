"""Shared test fixtures for cluster-guardian."""

import pytest


@pytest.fixture
def settings_env(monkeypatch):
    """Set minimal environment for Settings to load."""
    monkeypatch.setenv("CLUSTER_GUARDIAN_LLM_API_KEY", "test-key")
    monkeypatch.setenv("CLUSTER_GUARDIAN_REDIS_URL", "redis://localhost:6379")
    monkeypatch.setenv("CLUSTER_GUARDIAN_QDRANT_URL", "http://localhost:6333")
