"""
Redis persistence module for Cluster Guardian.

Provides optional persistence for rate limiting, audit logs, and scan results.
All operations degrade gracefully when Redis is unavailable.
"""

import json
import time
from datetime import datetime
from typing import Optional

import redis.asyncio as aioredis
import structlog

from .config import settings

logger = structlog.get_logger(__name__)

KEY_RATE_LIMIT = "guardian:rate_limit"
KEY_AUDIT_LOG = "guardian:audit_log"
KEY_LAST_SCAN = "guardian:last_scan"

RATE_LIMIT_TTL = 7200
AUDIT_LOG_MAX_LEN = 500


class RedisClient:
    """Async Redis client with graceful degradation."""

    def __init__(self, url: str):
        self.url = url
        self.available = False
        self._redis: Optional[aioredis.Redis] = None

    async def connect(self):
        """Establish Redis connection. Logs warning on failure."""
        try:
            self._redis = aioredis.from_url(
                self.url,
                decode_responses=True,
                socket_connect_timeout=5,
            )
            await self._redis.ping()
            self.available = True
            logger.info("Redis connected", url=self.url)
        except Exception as exc:
            logger.warning(
                "Redis unavailable, running without persistence", error=str(exc)
            )
            self.available = False
            self._redis = None

    async def close(self):
        """Close the Redis connection."""
        if self._redis:
            try:
                await self._redis.close()
            except Exception:
                pass
            self._redis = None
            self.available = False

    async def record_action(self, action: str, timestamp_iso: str):
        """Store an action in the rate-limit sorted set.

        Score is the unix timestamp; member is ``timestamp|action``.
        TTL of 7200s is refreshed on every write.
        """
        if not self.available or not self._redis:
            return
        try:
            ts = datetime.fromisoformat(timestamp_iso).timestamp()
            member = f"{timestamp_iso}|{action}"
            await self._redis.zadd(KEY_RATE_LIMIT, {member: ts})
            await self._redis.expire(KEY_RATE_LIMIT, RATE_LIMIT_TTL)
        except Exception as exc:
            logger.warning("Redis record_action failed", error=str(exc))

    async def get_actions_in_window(self, window_seconds: int = 3600) -> int:
        """Count actions within the given window and prune old entries."""
        if not self.available or not self._redis:
            return 0
        try:
            now = time.time()
            cutoff = now - window_seconds
            # Prune entries older than the window
            await self._redis.zremrangebyscore(KEY_RATE_LIMIT, "-inf", cutoff)
            count = await self._redis.zrangebyscore(KEY_RATE_LIMIT, cutoff, "+inf")
            return len(count)
        except Exception as exc:
            logger.warning("Redis get_actions_in_window failed", error=str(exc))
            return 0

    async def append_audit_entry(self, entry: dict):
        """Push an audit entry and trim the list to the last 500."""
        if not self.available or not self._redis:
            return
        try:
            await self._redis.lpush(KEY_AUDIT_LOG, json.dumps(entry))
            await self._redis.ltrim(KEY_AUDIT_LOG, 0, AUDIT_LOG_MAX_LEN - 1)
        except Exception as exc:
            logger.warning("Redis append_audit_entry failed", error=str(exc))

    async def get_audit_entries(self, count: int = 50) -> list[dict]:
        """Return the most recent audit entries from Redis."""
        if not self.available or not self._redis:
            return []
        try:
            raw = await self._redis.lrange(KEY_AUDIT_LOG, 0, count - 1)
            return [json.loads(item) for item in raw]
        except Exception as exc:
            logger.warning("Redis get_audit_entries failed", error=str(exc))
            return []

    async def store_scan_result(self, result: dict):
        """Persist the latest scan result as JSON."""
        if not self.available or not self._redis:
            return
        try:
            await self._redis.set(KEY_LAST_SCAN, json.dumps(result))
        except Exception as exc:
            logger.warning("Redis store_scan_result failed", error=str(exc))

    async def get_last_scan(self) -> dict | None:
        """Retrieve the last stored scan result."""
        if not self.available or not self._redis:
            return None
        try:
            raw = await self._redis.get(KEY_LAST_SCAN)
            if raw:
                return json.loads(raw)
            return None
        except Exception as exc:
            logger.warning("Redis get_last_scan failed", error=str(exc))
            return None

    async def health_check(self) -> bool:
        """Return True if Redis responds to PING."""
        if not self.available or not self._redis:
            return False
        try:
            return await self._redis.ping()
        except Exception:
            return False


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_redis_client: Optional[RedisClient] = None


def get_redis_client() -> RedisClient:
    """Get or create the RedisClient singleton."""
    global _redis_client
    if _redis_client is None:
        _redis_client = RedisClient(url=settings.redis_url)
    return _redis_client
