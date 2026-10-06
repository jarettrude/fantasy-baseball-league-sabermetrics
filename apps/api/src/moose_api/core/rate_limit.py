"""Redis-backed sliding-window rate limiter.

Industry-standard sliding-window counter approach:
  - Uses a Redis sorted set per key (user + endpoint).
  - Each request adds the current timestamp as a member.
  - Expired entries (outside the window) are pruned on each call.
  - The remaining set size is the request count in the window.
  - Atomic via Redis pipeline (MULTI/EXEC).

Benefits over alternatives:
  - More accurate than fixed-window (no burst at window edges).
  - Simpler and more predictable than full token-bucket for HTTP APIs.
  - Natural Retry-After calculation from the oldest entry in the window.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import HTTPException, Request, status

from moose_api.core.redis import redis_client


class RateLimiter:
    """Reusable sliding-window rate limiter as a FastAPI dependency.

    Usage::

        @router.post(
            "/endpoint",
            dependencies=[Depends(RateLimiter(max_requests=10, window_seconds=60))],
        )
        async def my_endpoint(): ...

    Or for per-user limiting:
        @router.post("/endpoint")
        async def my_endpoint(user=Depends(get_current_user), _=Depends(RateLimiter(...))):
            ...
    """

    def __init__(
        self,
        max_requests: int = 60,
        window_seconds: int = 60,
        key_prefix: str = "rl",
    ) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self.key_prefix = key_prefix

    def _build_key(self, request: Request) -> str:
        """Build a rate-limit key from the client identity.

        Priority:
          1. Authenticated user ID (from state set by auth middleware).
          2. Connecting client IP (fallback for unauthenticated endpoints).

        The endpoint path is included so limits are per-route.
        """
        user: dict[str, Any] | None = getattr(request.state, "user", None)
        if user and user.get("id"):
            identity = f"user:{user['id']}"
        else:
            # Use X-Forwarded-For from Traefik, fall back to direct client IP
            forwarded = request.headers.get("x-forwarded-for")
            if forwarded:
                identity = f"ip:{forwarded.split(',')[0].strip()}"
            else:
                identity = f"ip:{request.client.host if request.client else 'unknown'}"

        path = request.url.path.rstrip("/") or "/"
        return f"{self.key_prefix}:{identity}:{path}"

    async def __call__(self, request: Request) -> None:
        key = self._build_key(request)
        now = time.time()
        window_start = now - self.window_seconds

        pipe = redis_client.pipeline(transaction=True)
        pipe.zremrangebyscore(key, "-inf", window_start)
        pipe.zadd(key, {str(now): now})
        pipe.zcard(key)
        pipe.zrange(key, 0, 0, withscores=True)
        pipe.expire(key, self.window_seconds + 1)

        results = await pipe.execute()
        request_count: int = results[2]

        if request_count > self.max_requests:
            oldest_entries = results[3]
            if oldest_entries:
                oldest_score = oldest_entries[0][1]
                retry_after = int(self.window_seconds - (now - oldest_score)) + 1
            else:
                retry_after = self.window_seconds

            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded. Try again in {retry_after} seconds.",
                headers={"Retry-After": str(retry_after)},
            )


# ── Pre-configured limiters for common use cases ─────────────────────

# Auth endpoints: 10 requests per minute per IP (brute-force protection)
auth_rate_limit = RateLimiter(max_requests=10, window_seconds=60, key_prefix="rl:auth")

# Admin sync triggers: 5 per minute per user (prevents hammering external APIs)
admin_sync_rate_limit = RateLimiter(max_requests=5, window_seconds=60, key_prefix="rl:sync")

# General API: 120 requests per minute per user/IP
general_rate_limit = RateLimiter(max_requests=120, window_seconds=60, key_prefix="rl:api")

# Recap generation: 3 per 5 minutes per user (expensive AI calls)
recap_rate_limit = RateLimiter(max_requests=3, window_seconds=300, key_prefix="rl:recap")
