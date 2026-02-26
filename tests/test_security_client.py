"""Tests for src.security_client."""

from src.security_client import FalcoAlertProcessor


# ---------------------------------------------------------------------------
# FalcoAlertProcessor.parse_alert
# ---------------------------------------------------------------------------


class TestParseAlert:
    def test_parse_alert_basic(self):
        payload = {
            "rule": "Terminal shell in container",
            "priority": "Warning",
            "output": "A shell was spawned in a container",
            "time": "2025-01-15T10:30:00Z",
            "output_fields": {
                "k8s.ns.name": "production",
                "k8s.pod.name": "web-abc123",
                "container.name": "nginx",
            },
        }

        proc = FalcoAlertProcessor()
        alert = proc.parse_alert(payload)

        assert alert["rule"] == "Terminal shell in container"
        assert alert["priority"] == "Warning"
        assert alert["severity"] == "warning"
        assert alert["output"] == "A shell was spawned in a container"
        assert alert["timestamp"] == "2025-01-15T10:30:00Z"
        assert alert["namespace"] == "production"
        assert alert["pod"] == "web-abc123"
        assert alert["container"] == "nginx"

    def test_parse_alert_severity_mapping(self):
        proc = FalcoAlertProcessor()

        cases = {
            "Emergency": "critical",
            "Warning": "warning",
            "Notice": "info",
        }
        for priority, expected_severity in cases.items():
            payload = {"priority": priority}
            alert = proc.parse_alert(payload)
            assert alert["severity"] == expected_severity, (
                f"priority={priority!r} should map to severity={expected_severity!r}"
            )

    def test_parse_alert_empty_fields(self):
        proc = FalcoAlertProcessor()
        alert = proc.parse_alert({})

        assert alert["rule"] == ""
        assert alert["priority"] == ""
        assert alert["output"] == ""
        assert alert["namespace"] == ""
        assert alert["pod"] == ""
        assert alert["container"] == ""


# ---------------------------------------------------------------------------
# FalcoAlertProcessor.format_alert_summary
# ---------------------------------------------------------------------------


class TestFormatAlertSummary:
    def test_format_alert_summary_empty(self):
        proc = FalcoAlertProcessor()
        result = proc.format_alert_summary([])
        assert result == "No Falco alerts."

    def test_format_alert_summary_with_alerts(self):
        proc = FalcoAlertProcessor()
        alerts = [
            {
                "severity": "critical",
                "rule": "Privilege escalation",
                "namespace": "prod",
                "pod": "api-server-1",
                "output": "Privilege escalation detected",
            },
            {
                "severity": "warning",
                "rule": "Shell spawned",
                "namespace": "staging",
                "pod": "worker-2",
                "output": "Unexpected shell in container",
            },
        ]

        result = proc.format_alert_summary(alerts)

        assert result.startswith("Falco alerts (2):")
        assert "[CRITICAL] Privilege escalation" in result
        assert "ns=prod" in result
        assert "pod=api-server-1" in result
        assert "[WARNING] Shell spawned" in result
        assert "ns=staging" in result
        assert "pod=worker-2" in result
