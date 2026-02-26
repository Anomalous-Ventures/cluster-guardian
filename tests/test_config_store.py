"""Tests for src.config_store.ConfigStore."""

from unittest.mock import patch

import pytest

from src.config_store import ConfigStore


@pytest.fixture
def store(settings_env):
    """Return a fresh ConfigStore instance with env vars set."""
    return ConfigStore()


# -------------------------------------------------------------------
# get
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_returns_default_when_no_redis_override(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        value = await store.get("port")

    assert value == 8900


@pytest.mark.asyncio
async def test_get_raises_for_unknown_key(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        with pytest.raises(ValueError, match="Unknown configuration key"):
            await store.get("nonexistent_key")


# -------------------------------------------------------------------
# set + get round-trip
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_and_get_roundtrip(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        await store.set("port", 9999)
        value = await store.get("port")

    assert value == 9999


# -------------------------------------------------------------------
# set validation / unavailability
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_validates_type(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        with pytest.raises(ValueError, match="Validation failed"):
            await store.set("port", "not_a_number")


@pytest.mark.asyncio
async def test_set_raises_when_redis_unavailable(store, disconnected_redis_client):
    with patch(
        "src.config_store.get_redis_client", return_value=disconnected_redis_client
    ):
        with pytest.raises(RuntimeError, match="Redis is unavailable"):
            await store.set("port", 9999)


# -------------------------------------------------------------------
# serialize / deserialize round-trips
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_serialize_deserialize_bool(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        await store.set("debug", True)
        assert await store.get("debug") is True

        await store.set("debug", False)
        assert await store.get("debug") is False


@pytest.mark.asyncio
async def test_serialize_deserialize_int(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        await store.set("scan_interval_seconds", 120)
        value = await store.get("scan_interval_seconds")

    assert value == 120
    assert isinstance(value, int)


@pytest.mark.asyncio
async def test_serialize_deserialize_list(store, mock_redis_client):
    namespaces = ["ns-a", "ns-b", "ns-c"]
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        await store.set("protected_namespaces", namespaces)
        value = await store.get("protected_namespaces")

    assert value == namespaces


# -------------------------------------------------------------------
# get_all
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_all_merges_overrides(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        await store.set("port", 7777)
        await store.set("debug", True)

        merged = await store.get_all()

    assert merged["port"] == 7777
    assert merged["debug"] is True
    # Non-overridden key should still be the default
    assert merged["scan_interval_seconds"] == 300


# -------------------------------------------------------------------
# reset
# -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_reset_removes_override(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        await store.set("port", 7777)
        assert await store.get("port") == 7777

        await store.reset("port")
        assert await store.get("port") == 8900


@pytest.mark.asyncio
async def test_reset_raises_for_unknown_key(store, mock_redis_client):
    with patch("src.config_store.get_redis_client", return_value=mock_redis_client):
        with pytest.raises(ValueError, match="Unknown configuration key"):
            await store.reset("nonexistent_key")


@pytest.mark.asyncio
async def test_reset_raises_when_redis_unavailable(store, disconnected_redis_client):
    with patch(
        "src.config_store.get_redis_client", return_value=disconnected_redis_client
    ):
        with pytest.raises(RuntimeError, match="Redis is unavailable"):
            await store.reset("port")
