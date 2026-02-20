"""
Redis-backed runtime configuration store for Cluster Guardian.

Allows runtime overrides of Settings fields via Redis hash ``guardian:config``.
Reads fall back to environment-derived defaults from ``src/config.py`` when no
Redis override exists.  Writes are type-validated against the Pydantic schema
before persisting.
"""

import json
from typing import Any, Dict, Optional, get_args, get_origin

import structlog
from pydantic.fields import FieldInfo

from .config import Settings, settings
from .redis_client import get_redis_client

logger = structlog.get_logger(__name__)

REDIS_HASH_KEY = "guardian:config"


class ConfigStore:
    """Async, Redis-backed configuration store with Pydantic validation.

    Values stored in Redis take precedence over environment defaults
    exposed by :pydata:`settings`.  Every write is type-checked against the
    ``Settings`` model so invalid values are rejected before reaching Redis.
    """

    def __init__(self) -> None:
        self._field_info: Dict[str, FieldInfo] = Settings.model_fields

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_field_type(self, key: str) -> type | None:
        """Return the outermost concrete type for a Settings field."""
        info = self._field_info.get(key)
        if info is None:
            return None
        annotation = info.annotation
        # Unwrap Optional[X] -> X
        origin = get_origin(annotation)
        if origin is type(None):
            return None
        if origin is not None:
            # e.g. Optional[str] is Union[str, None]
            args = get_args(annotation)
            non_none = [a for a in args if a is not type(None)]
            if non_none:
                # For List[str] the origin is list
                inner_origin = get_origin(non_none[0])
                return inner_origin if inner_origin is not None else non_none[0]
        return annotation

    @staticmethod
    def _serialize(value: Any) -> str:
        """Convert a Python value to a Redis-safe string."""
        if isinstance(value, list):
            return json.dumps(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        return str(value)

    def _deserialize(self, key: str, raw: str) -> Any:
        """Convert a raw Redis string back to the expected Python type."""
        field_type = self._get_field_type(key)
        if field_type is None:
            return raw

        if field_type is bool:
            return raw.lower() in ("true", "1", "yes")
        if field_type is int:
            return int(raw)
        if field_type is float:
            return float(raw)
        if field_type is list:
            return json.loads(raw)
        return raw

    def _validate_value(self, key: str, value: Any) -> Any:
        """Validate *value* against the Settings schema for *key*.

        Constructs a partial model with only the target field populated
        so Pydantic's validators run.  Returns the coerced value on
        success; raises ``ValueError`` on failure.
        """
        if key not in self._field_info:
            raise ValueError(f"Unknown configuration key: {key}")

        try:
            partial = Settings.model_validate({key: value})
            return getattr(partial, key)
        except Exception as exc:
            raise ValueError(f"Validation failed for {key}={value!r}: {exc}") from exc

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get(self, key: str) -> Any:
        """Read a configuration value.

        Returns the Redis override if present, otherwise the environment
        default from ``settings``.
        """
        if key not in self._field_info:
            raise ValueError(f"Unknown configuration key: {key}")

        redis = get_redis_client()
        if redis.available and redis._redis:
            try:
                raw = await redis._redis.hget(REDIS_HASH_KEY, key)
                if raw is not None:
                    value = self._deserialize(key, raw)
                    logger.debug("config.get from redis", key=key, value=value)
                    return value
            except Exception as exc:
                logger.warning(
                    "config.get redis read failed, using default",
                    key=key,
                    error=str(exc),
                )

        default = getattr(settings, key)
        logger.debug("config.get from env default", key=key, value=default)
        return default

    async def set(self, key: str, value: Any) -> None:
        """Validate and store a runtime configuration override in Redis."""
        validated = self._validate_value(key, value)

        redis = get_redis_client()
        if not redis.available or not redis._redis:
            raise RuntimeError("Redis is unavailable; cannot persist runtime config")

        serialized = self._serialize(validated)
        try:
            await redis._redis.hset(REDIS_HASH_KEY, key, serialized)
            logger.info("config.set", key=key, value=validated)
        except Exception as exc:
            logger.error("config.set redis write failed", key=key, error=str(exc))
            raise RuntimeError(f"Failed to write config key {key} to Redis") from exc

    async def get_all(self) -> Dict[str, Any]:
        """Return a merged dict of all configuration values.

        Redis overrides take precedence over environment defaults.
        """
        defaults = {k: getattr(settings, k) for k in self._field_info}

        redis = get_redis_client()
        if redis.available and redis._redis:
            try:
                overrides = await redis._redis.hgetall(REDIS_HASH_KEY)
                for key, raw in overrides.items():
                    if key in self._field_info:
                        defaults[key] = self._deserialize(key, raw)
            except Exception as exc:
                logger.warning(
                    "config.get_all redis read failed, returning defaults only",
                    error=str(exc),
                )

        return defaults

    async def reset(self, key: str) -> None:
        """Delete a runtime override from Redis, reverting to the env default."""
        if key not in self._field_info:
            raise ValueError(f"Unknown configuration key: {key}")

        redis = get_redis_client()
        if not redis.available or not redis._redis:
            raise RuntimeError("Redis is unavailable; cannot reset runtime config")

        try:
            await redis._redis.hdel(REDIS_HASH_KEY, key)
            logger.info("config.reset", key=key)
        except Exception as exc:
            logger.error("config.reset redis delete failed", key=key, error=str(exc))
            raise RuntimeError(f"Failed to reset config key {key} in Redis") from exc


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_config_store: Optional[ConfigStore] = None


def get_config_store() -> ConfigStore:
    """Get or create the ConfigStore singleton."""
    global _config_store
    if _config_store is None:
        _config_store = ConfigStore()
    return _config_store
