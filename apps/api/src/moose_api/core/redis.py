"""Redis client connection and caching utilities.

Provides async Redis connection, JSON serialization caching,
and queue management for the Moose Sports Empire application.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import redis.asyncio as aioredis

from moose_api.core.config import settings

logger = logging.getLogger(__name__)

redis_client = aioredis.from_url(
    settings.redis_url,
    encoding="utf-8",
    decode_responses=True,
)


async def get_redis() -> aioredis.Redis:
    """Get the global Redis client instance.

    Returns:
        Redis client for async operations
    """
    return redis_client


async def get_cached(key: str) -> Any | None:
    """Get JSON-serialized value from Redis cache.

    Handles cache misses and Redis errors gracefully by returning None.

    Args:
        key: Cache key to retrieve

    Returns:
        Deserialized value or None if not found/error occurred
    """
    try:
        raw = await redis_client.get(key)
        if raw is None:
            return None
        return json.loads(raw)
    except Exception as e:
        logger.warning("Redis get_cached error for key %s: %s", key, e)
        return None


async def set_cached(key: str, value: Any, ttl_seconds: int) -> None:
    """Store value in Redis cache with JSON serialization and TTL.

    Args:
        key: Cache key for storage
        value: Data to serialize and store
        ttl_seconds: Time-to-live in seconds
    """
    try:
        await redis_client.setex(key, ttl_seconds, json.dumps(value, default=str))
    except Exception as e:
        logger.warning("Redis set_cached error for key %s: %s", key, e)


async def invalidate_cache(*keys: str) -> None:
    """Delete one or more cache keys.

    Used for cache invalidation when data changes.

    Args:
        keys: Cache keys to delete
    """
    try:
        if keys:
            await redis_client.delete(*keys)
    except Exception as e:
        logger.warning("Redis invalidate_cache error: %s", e)


async def reset_arq_queues(redis: aioredis.Redis) -> None:
    """Remove stale ARQ queue keys that can cause WRONGTYPE errors.

    Cleans up job queue state during application startup or
    after queue corruption to prevent ARQ worker failures.

    Args:
        redis: Redis client instance
    """
    queue_keys = [
        "arq:queue:default",
        "arq:queue:default:pending",
        "arq:queue:default:scheduled",
        "arq:queue:default:in-progress",
    ]
    for key in queue_keys:
        await redis.delete(key)
