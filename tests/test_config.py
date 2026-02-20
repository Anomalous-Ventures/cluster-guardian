"""Tests for cluster-guardian configuration."""

from src.config import Settings


class TestSettings:
    """Test Settings loads from environment."""

    def test_defaults(self, settings_env):
        s = Settings()
        assert s.host == "0.0.0.0"
        assert s.port == 8900
        assert s.scan_interval_seconds == 300
        assert s.max_actions_per_hour == 30

    def test_env_prefix(self, settings_env, monkeypatch):
        monkeypatch.setenv("CLUSTER_GUARDIAN_PORT", "9999")
        s = Settings()
        assert s.port == 9999

    def test_protected_namespaces(self, settings_env):
        s = Settings()
        assert "kube-system" in s.protected_namespaces

    def test_autonomy_level_default(self, settings_env):
        s = Settings()
        assert s.autonomy_level == "conditional"
