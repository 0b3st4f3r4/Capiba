"""Redis cache helpers for the API hot paths.

Chunk: api_cache
Responsibility: Cache expensive API computations (risk signals, municipal
ranking) in Redis, degrading gracefully: any Redis failure logs a warning
and the value is computed directly, so the API keeps responding without
cache.

Dependencies: redis, capiba.config
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any, cast

import redis
from pydantic import BaseModel

from capiba.config import REDIS_TTL_DEFAULT, REDIS_URL

logger = logging.getLogger(__name__)

_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    """Returns the shared Redis client, created lazily on first use."""
    global _client
    if _client is None:
        _client = redis.from_url(REDIS_URL)
    return _client


def cached[T](
    key: str,
    compute: Callable[[], T],
    *,
    ttl: int = REDIS_TTL_DEFAULT,
    model: type[BaseModel] | None = None,
) -> T:
    """Returns the cached value for ``key``, computing and storing it on miss.

    Args:
        key: Cache key, derived from the request parameters.
        compute: Zero-argument callable that produces the value on a miss.
        ttl: Time-to-live of the cached payload, in seconds.
        model: Pydantic model used to rebuild the cached payload when the
            computed value is a BaseModel.

    Returns:
        The cached or freshly computed value. Any Redis error degrades to
        a direct computation (warning logged, nothing cached).
    """
    try:
        raw = _get_client().get(key)
    except redis.RedisError as exc:
        logger.warning("Redis unavailable (get %s); computing directly: %s", key, exc)
    else:
        if raw is not None:
            data: Any = json.loads(raw)
            return cast("T", model.model_validate(data) if model is not None else data)

    result = compute()
    payload = (
        result.model_dump(mode="json") if isinstance(result, BaseModel) else result
    )
    try:
        # default=str keeps Decimal/datetime payloads JSON-serializable.
        _get_client().set(key, json.dumps(payload, default=str), ex=ttl)
    except redis.RedisError as exc:
        logger.warning("Redis unavailable (set %s); response not cached: %s", key, exc)
    return result
