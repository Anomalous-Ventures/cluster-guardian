"""Integration tests for health endpoints (require kind cluster)."""

import os
import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("KUBECONFIG"),
    reason="KUBECONFIG not set (no cluster available)",
)


class TestHealthEndpoint:
    """Health endpoint should work without external dependencies."""

    def test_placeholder(self):
        """Placeholder -- actual integration tests need the app running in-cluster."""
        assert True
