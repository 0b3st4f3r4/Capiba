"""Tests for the API cache helpers.

Responsibility: guarantee the cached() helper hits, misses, passes the TTL
through and degrades gracefully when Redis is unavailable (the value is
still computed and returned).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import MagicMock

import pytest
import redis

from capiba.api import cache
from capiba.api.cache import cached
from capiba.api.schemas import SignalsResponse


@pytest.fixture
def redis_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Returns a mocked Redis client injected into the cache module."""
    client = MagicMock()
    monkeypatch.setattr(cache, "_get_client", lambda: client)
    return client


class TestCached:
    """Tests for the cached() helper."""

    def test_hit_returns_cached_without_computing(
        self, redis_client: MagicMock
    ) -> None:
        """A cached payload must be returned without calling compute."""
        redis_client.get.return_value = b'{"a": 1}'
        compute = MagicMock()

        result = cached("k", compute, ttl=60)

        assert result == {"a": 1}
        redis_client.get.assert_called_once_with("k")
        compute.assert_not_called()
        redis_client.set.assert_not_called()

    def test_hit_rebuilds_pydantic_model(self, redis_client: MagicMock) -> None:
        """A cached model payload must be rebuilt as the Pydantic model."""
        redis_client.get.return_value = (
            b'{"entity": "12345678000195", "risk_index": 0.5, '
            b'"signals": [], "alert": false}'
        )

        result = cached("k", MagicMock(), ttl=60, model=SignalsResponse)

        assert isinstance(result, SignalsResponse)
        assert result.entity == "12345678000195"
        assert result.risk_index == 0.5

    def test_miss_computes_and_stores_with_ttl(
        self, redis_client: MagicMock
    ) -> None:
        """A miss must compute the value and store it with the given TTL."""
        redis_client.get.return_value = None
        compute = MagicMock(return_value={"a": 1})

        result = cached("k", compute, ttl=123)

        assert result == {"a": 1}
        compute.assert_called_once()
        redis_client.set.assert_called_once_with("k", '{"a": 1}', ex=123)

    def test_miss_serializes_pydantic_model(self, redis_client: MagicMock) -> None:
        """A Pydantic result must be stored as its JSON-mode dump."""
        redis_client.get.return_value = None
        response = SignalsResponse(
            entity="12345678000195", risk_index=0.0, signals=[], alert=False
        )

        cached("k", lambda: response, ttl=60, model=SignalsResponse)

        payload = redis_client.set.call_args[0][1]
        assert json.loads(payload) == response.model_dump(mode="json")

    def test_non_json_types_serialized_as_str(self, redis_client: MagicMock) -> None:
        """Decimal/datetime payloads must not break the serialization."""
        from datetime import date
        from decimal import Decimal

        redis_client.get.return_value = None
        value: dict[str, Any] = {"amount": Decimal("10.5"), "day": date(2024, 1, 2)}

        cached("k", lambda: value, ttl=60)

        payload = redis_client.set.call_args[0][1]
        assert payload == '{"amount": "10.5", "day": "2024-01-02"}'

    def test_redis_down_on_get_computes_directly(
        self, redis_client: MagicMock
    ) -> None:
        """A Redis failure on get must degrade to a direct computation."""
        redis_client.get.side_effect = redis.ConnectionError("boom")
        redis_client.set.side_effect = redis.ConnectionError("boom")
        compute = MagicMock(return_value={"a": 1})

        result = cached("k", compute, ttl=60)

        assert result == {"a": 1}
        compute.assert_called_once()

    def test_redis_down_on_set_still_returns(self, redis_client: MagicMock) -> None:
        """A Redis failure on set must not lose the computed value."""
        redis_client.get.return_value = None
        redis_client.set.side_effect = redis.ConnectionError("boom")

        result = cached("k", lambda: {"a": 1}, ttl=60)

        assert result == {"a": 1}

    def test_client_creation_failure_computes_directly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failure creating the client must also degrade gracefully."""

        def broken() -> Any:
            raise redis.ConnectionError("boom")

        monkeypatch.setattr(cache, "_get_client", broken)

        result = cached("k", lambda: {"a": 1}, ttl=60)

        assert result == {"a": 1}
