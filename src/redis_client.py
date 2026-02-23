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
KEY_PENDING_APPROVALS = "guardian:pending_approvals"
KEY_INCIDENTS = "guardian:incidents"
KEY_ISSUE_PATTERNS = "guardian:issue_patterns"
KEY_ESCALATION_PREFIX = "guardian:escalated:"

RATE_LIMIT_TTL = 7200
AUDIT_LOG_MAX_LEN = 500
INCIDENTS_TTL = 7200
ESCALATION_COOLDOWN = 86400


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

    async def store_pending_approval(self, approval: dict):
        """Persist a pending approval entry to Redis."""
        if not self.available or not self._redis:
            return
        try:
            await self._redis.hset(
                KEY_PENDING_APPROVALS, approval["id"], json.dumps(approval)
            )
        except Exception as exc:
            logger.warning("Redis store_pending_approval failed", error=str(exc))

    async def update_pending_approval(self, approval_id: str, status: str):
        """Update the status of a pending approval in Redis."""
        if not self.available or not self._redis:
            return
        try:
            raw = await self._redis.hget(KEY_PENDING_APPROVALS, approval_id)
            if raw:
                entry = json.loads(raw)
                entry["status"] = status
                await self._redis.hset(
                    KEY_PENDING_APPROVALS, approval_id, json.dumps(entry)
                )
        except Exception as exc:
            logger.warning("Redis update_pending_approval failed", error=str(exc))

    async def get_pending_approvals(self) -> list[dict]:
        """Retrieve all pending approval entries from Redis."""
        if not self.available or not self._redis:
            return []
        try:
            raw_map = await self._redis.hgetall(KEY_PENDING_APPROVALS)
            return [json.loads(v) for v in raw_map.values()]
        except Exception as exc:
            logger.warning("Redis get_pending_approvals failed", error=str(exc))
            return []

    async def store_incidents(self, incidents: list[dict]):
        """Persist active incidents for recovery across restarts."""
        if not self.available or not self._redis:
            return
        try:
            await self._redis.set(KEY_INCIDENTS, json.dumps(incidents))
            await self._redis.expire(KEY_INCIDENTS, INCIDENTS_TTL)
        except Exception as exc:
            logger.warning("Redis store_incidents failed", error=str(exc))

    async def get_incidents(self) -> list[dict]:
        """Retrieve persisted incidents."""
        if not self.available or not self._redis:
            return []
        try:
            raw = await self._redis.get(KEY_INCIDENTS)
            if raw:
                return json.loads(raw)
            return []
        except Exception as exc:
            logger.warning("Redis get_incidents failed", error=str(exc))
            return []

    async def increment_issue_pattern(self, pattern_key: str) -> int:
        """Increment the counter for an issue pattern. Returns new count."""
        if not self.available or not self._redis:
            return 0
        try:
            count = await self._redis.hincrby(KEY_ISSUE_PATTERNS, pattern_key, 1)
            return count
        except Exception as exc:
            logger.warning("Redis increment_issue_pattern failed", error=str(exc))
            return 0

    async def get_issue_pattern_count(self, pattern_key: str) -> int:
        """Get the current count for an issue pattern."""
        if not self.available or not self._redis:
            return 0
        try:
            raw = await self._redis.hget(KEY_ISSUE_PATTERNS, pattern_key)
            return int(raw) if raw else 0
        except Exception as exc:
            logger.warning("Redis get_issue_pattern_count failed", error=str(exc))
            return 0

    async def record_escalation(self, pattern_key: str):
        """Mark a pattern as escalated with 24h TTL."""
        if not self.available or not self._redis:
            return
        try:
            key = f"{KEY_ESCALATION_PREFIX}{pattern_key}"
            await self._redis.set(key, "1")
            await self._redis.expire(key, ESCALATION_COOLDOWN)
        except Exception as exc:
            logger.warning("Redis record_escalation failed", error=str(exc))

    async def was_recently_escalated(self, pattern_key: str) -> bool:
        """Check if a pattern was escalated within the 24h cooldown."""
        if not self.available or not self._redis:
            return False
        try:
            key = f"{KEY_ESCALATION_PREFIX}{pattern_key}"
            return await self._redis.get(key) is not None
        except Exception as exc:
            logger.warning("Redis was_recently_escalated failed", error=str(exc))
            return False

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
