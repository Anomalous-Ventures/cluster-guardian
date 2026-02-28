"""Tests for deep health checks (src.health_checks)."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src.health_checks import DeepHealthChecker, HealthCheckResult, get_health_checker


# ---------------------------------------------------------------------------
# HealthCheckResult
# ---------------------------------------------------------------------------


def test_health_check_result_to_dict():
    """to_dict() returns all expected fields."""
    r = HealthCheckResult(
        service="test",
        healthy=True,
        checks=[{"name": "ssl"}],
        errors=[],
        warnings=["low disk"],
    )
    d = r.to_dict()
    assert d["service"] == "test"
    assert d["healthy"] is True
    assert d["checks"] == [{"name": "ssl"}]
    assert d["warnings"] == ["low disk"]
    assert "timestamp" in d


def test_health_check_result_defaults():
    """HealthCheckResult populates sensible defaults."""
    r = HealthCheckResult(service="x", healthy=False)
    assert r.checks == []
    assert r.errors == []
    assert r.warnings == []
    assert r.timestamp  # non-empty


# ---------------------------------------------------------------------------
# DeepHealthChecker -- domain behavior
# ---------------------------------------------------------------------------


def test_init_default_domain_is_none():
    """Default domain is None (not 'spooty.io')."""
    hc = DeepHealthChecker()
    assert hc.domain is None


def test_init_explicit_domain():
    """Explicit domain is preserved."""
    hc = DeepHealthChecker(domain="example.com")
    assert hc.domain == "example.com"


@pytest.mark.asyncio
async def test_check_all_skips_domain_dependent_when_no_domain():
    """check_all() skips domain-dependent checks when domain is None."""
    hc = DeepHealthChecker(domain=None)

    # Patch all non-domain-dependent checks to return healthy
    non_domain = [
        name for name in hc.service_checks if name not in hc._DOMAIN_DEPENDENT_CHECKS
    ]
    for name in non_domain:
        hc.service_checks[name] = AsyncMock(
            return_value=HealthCheckResult(service=name, healthy=True)
        )

    results = await hc.check_all()

    result_names = {r.service for r in results}
    # Should NOT include any domain-dependent services
    for dep in hc._DOMAIN_DEPENDENT_CHECKS:
        assert dep not in result_names
    # Should include all non-domain services
    for name in non_domain:
        assert name in result_names


@pytest.mark.asyncio
async def test_check_all_includes_domain_dependent_when_domain_set():
    """check_all() includes domain-dependent checks when domain is set."""
    hc = DeepHealthChecker(domain="example.com")

    # Stub ALL checks to return healthy quickly
    for name in list(hc.service_checks):
        hc.service_checks[name] = AsyncMock(
            return_value=HealthCheckResult(service=name, healthy=True)
        )

    results = await hc.check_all()

    result_names = {r.service for r in results}
    # Should include domain-dependent checks
    assert "grafana" in result_names
    assert "vault" in result_names


@pytest.mark.asyncio
async def test_check_service_skips_domain_dependent_when_no_domain():
    """check_service() returns error for domain-dependent check when domain is None."""
    hc = DeepHealthChecker(domain=None)
    result = await hc.check_service("grafana")
    assert result.healthy is False
    assert "no domain configured" in result.errors[0]


@pytest.mark.asyncio
async def test_check_service_unknown():
    """check_service() returns error for unknown service."""
    hc = DeepHealthChecker(domain="example.com")
    result = await hc.check_service("nonexistent")
    assert result.healthy is False
    assert "Unknown service" in result.errors[0]


# ---------------------------------------------------------------------------
# Custom (data-driven) checks
# ---------------------------------------------------------------------------


def test_register_check():
    """register_check() stores the check spec."""
    hc = DeepHealthChecker()
    hc.register_check("my-api", "http://my-api:8080/health", expected_status=200)
    assert "my-api" in hc._custom_checks
    assert hc._custom_checks["my-api"]["url"] == "http://my-api:8080/health"
    assert hc._custom_checks["my-api"]["expected_status"] == 200
    assert hc._custom_checks["my-api"]["expected_content"] is None


def test_register_check_with_content():
    """register_check() stores expected_content."""
    hc = DeepHealthChecker()
    hc.register_check("my-app", "http://my-app/", expected_content="Welcome")
    assert hc._custom_checks["my-app"]["expected_content"] == "Welcome"


@pytest.mark.asyncio
async def test_custom_check_runs_in_check_all():
    """Custom checks are included in check_all() results."""
    hc = DeepHealthChecker(domain=None)

    # Stub all built-in non-domain checks
    non_domain = [
        name for name in hc.service_checks if name not in hc._DOMAIN_DEPENDENT_CHECKS
    ]
    for name in non_domain:
        hc.service_checks[name] = AsyncMock(
            return_value=HealthCheckResult(service=name, healthy=True)
        )

    # Register a custom check
    hc.register_check("my-custom", "http://custom:8080/health")

    with patch.object(hc, "_check_endpoint", new_callable=AsyncMock) as mock_ep:
        mock_ep.return_value = {
            "url": "http://custom:8080/health",
            "success": True,
            "status_code": 200,
        }
        results = await hc.check_all()

    result_names = {r.service for r in results}
    assert "my-custom" in result_names


@pytest.mark.asyncio
async def test_custom_check_unhealthy_on_failure():
    """Custom check reports unhealthy when endpoint fails."""
    hc = DeepHealthChecker()

    hc.register_check("failing-svc", "http://fail:8080/health")

    with patch.object(hc, "_check_endpoint", new_callable=AsyncMock) as mock_ep:
        mock_ep.return_value = {
            "url": "http://fail:8080/health",
            "success": False,
            "error": "Connection refused",
        }
        result = await hc._run_custom_check(
            "failing-svc", hc._custom_checks["failing-svc"]
        )

    assert result.healthy is False
    assert "Endpoint check failed" in result.errors[0]


@pytest.mark.asyncio
async def test_check_service_resolves_custom_check():
    """check_service() can run a custom check by name."""
    hc = DeepHealthChecker()
    hc.register_check("custom-api", "http://api:3000/ping")

    with patch.object(hc, "_check_endpoint", new_callable=AsyncMock) as mock_ep:
        mock_ep.return_value = {
            "url": "http://api:3000/ping",
            "success": True,
            "status_code": 200,
        }
        result = await hc.check_service("custom-api")

    assert result.service == "custom-api"
    assert result.healthy is True


# ---------------------------------------------------------------------------
# _check_endpoint
# ---------------------------------------------------------------------------


def _make_http_response(status_code: int, text: str, elapsed_seconds: float = 0.05):
    """Build a MagicMock that behaves like an httpx.Response."""
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.elapsed.total_seconds.return_value = elapsed_seconds
    return resp


@pytest.mark.asyncio
async def test_check_endpoint_success():
    """_check_endpoint returns success for matching status code."""
    hc = DeepHealthChecker()
    mock_response = _make_http_response(200, "OK healthy service")

    with patch("src.health_checks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await hc._check_endpoint("http://test:8080/health")

    assert result["success"] is True
    assert result["status_code"] == 200


@pytest.mark.asyncio
async def test_check_endpoint_wrong_status():
    """_check_endpoint returns failure for non-matching status code."""
    hc = DeepHealthChecker()
    mock_response = _make_http_response(500, "Internal Server Error")

    with patch("src.health_checks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await hc._check_endpoint("http://test:8080/health")

    assert result["success"] is False


@pytest.mark.asyncio
async def test_check_endpoint_missing_content():
    """_check_endpoint detects missing expected content."""
    hc = DeepHealthChecker()
    mock_response = _make_http_response(200, "Welcome to SomeApp")

    with patch("src.health_checks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await hc._check_endpoint(
            "http://test:8080/", expected_content="Grafana"
        )

    assert result["success"] is False
    assert "Expected content" in result.get("error", "")


@pytest.mark.asyncio
async def test_check_endpoint_connection_error():
    """_check_endpoint returns failure on connection error."""
    hc = DeepHealthChecker()

    with patch("src.health_checks.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("Connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client_cls.return_value = mock_client

        result = await hc._check_endpoint("http://test:8080/health")

    assert result["success"] is False
    assert "error" in result


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------


def test_get_health_checker_default_domain():
    """get_health_checker() creates checker with spooty.io domain by default."""
    hc = get_health_checker()
    assert hc.domain == "spooty.io"


def test_get_health_checker_none_domain():
    """get_health_checker(domain=None) creates checker with no domain."""
    # Reset singleton
    import src.health_checks

    src.health_checks._health_checker = None

    hc = get_health_checker(domain=None)
    assert hc.domain is None
