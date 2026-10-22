"""Fixed-window rate limiting backed by Redis. Fails open if Redis is unavailable."""
import hashlib
import logging

import redis
from fastapi import Request

from app.core.config import get_settings
from app.core.errors import AppError

log = logging.getLogger(__name__)
_client: redis.Redis | None = None


def get_redis() -> redis.Redis:
    global _client
    if _client is None:
        _client = redis.Redis.from_url(
            get_settings().redis_url,
            socket_connect_timeout=1,
            socket_timeout=1,
            decode_responses=True,
        )
    return _client


def client_ip(request: Request) -> str:
    # Behind a proxy this is the real address only if uvicorn is started with
    # --proxy-headers (we do that at deployment).
    return request.client.host if request.client else "unknown"


def hash_ident(value: str) -> str:
    """Identifiers (like emails) are hashed so they are never stored in Redis or logs."""
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:32]


def hit(key: str, limit: int, window_seconds: int) -> tuple[bool, int]:
    """Count one hit. Returns (allowed, seconds_until_reset)."""
    try:
        r = get_redis()
        pipe = r.pipeline()
        pipe.incr(key)
        pipe.ttl(key)
        count, ttl = pipe.execute()
        if ttl < 0:  # first hit (or the expiry was lost): start the window
            r.expire(key, window_seconds)
            ttl = window_seconds
    except redis.RedisError:
        log.warning("Rate limiter unavailable; allowing the request", exc_info=True)
        return True, 0
    if count > limit:
        return False, max(ttl, 1)
    return True, 0


def enforce(name: str, ident: str, limit: int, window_seconds: int) -> None:
    if not get_settings().rate_limit_enabled:
        return
    allowed, retry_after = hit(f"rl:{name}:{ident}", limit, window_seconds)
    if not allowed:
        raise AppError(
            429,
            "rate_limited",
            "Too many requests. Please try again later.",
            headers={"Retry-After": str(retry_after)},
        )


def ip_limit(name: str, limit: int, window_seconds: int):
    """A route dependency: at most `limit` calls per `window_seconds` per IP address."""

    def dependency(request: Request) -> None:
        enforce(name, client_ip(request), limit, window_seconds)

    return dependency