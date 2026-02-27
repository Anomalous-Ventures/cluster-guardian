"""Integration tests for auto-discovery (require kind cluster)."""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KUBECONFIG"),
    reason="KUBECONFIG not set (no cluster available)",
)


class TestAutoDiscovery:
    """Auto-discovery should work against a real cluster."""

    def test_placeholder(self):
        """Placeholder -- actual integration tests need the app running in-cluster."""
        assert True
