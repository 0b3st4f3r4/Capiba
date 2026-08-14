"""Tests for the Trino HTTP client (mocked HTTP layer)."""

from __future__ import annotations

from typing import Any

import pytest

from capiba.pipeline import trino


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]):
        self.payload = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict[str, Any]:
        return self.payload


def test_run_query_paginates_and_collects_rows(monkeypatch: pytest.MonkeyPatch) -> None:
    pages = {
        "http://trino/next/1": {
            "columns": [{"name": "n"}],
            "data": [[1]],
            "nextUri": "http://trino/next/2",
        },
        "http://trino/next/2": {"data": [[2]]},
    }
    calls: dict[str, Any] = {}

    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        calls["post"] = (url, kwargs)
        return _FakeResponse({"nextUri": "http://trino/next/1"})

    def fake_get(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(pages[url])

    monkeypatch.setattr(trino.requests, "post", fake_post)
    monkeypatch.setattr(trino.requests, "get", fake_get)

    rows = trino.run_query("SELECT 1")

    assert rows == [{"n": 1}, {"n": 2}]
    assert calls["post"][0].endswith("/v1/statement")
    assert calls["post"][1]["data"] == "SELECT 1"
    assert "X-Trino-User" in calls["post"][1]["headers"]


def test_auth_only_sent_over_https(monkeypatch: pytest.MonkeyPatch) -> None:
    # Trino 483 refuses passwords over insecure HTTP; basic auth must only go
    # out when the gateway is reached over HTTPS (the ingress path).
    monkeypatch.setattr(trino, "TRINO_URL", "http://capiba-trino:8080")
    assert trino._auth() is None
    monkeypatch.setattr(trino, "TRINO_URL", "https://trino.capiba.local:8443")
    assert trino._auth() == (trino.TRINO_USER, trino.TRINO_PASSWORD)


def test_run_query_raises_on_trino_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_post(url: str, **kwargs: Any) -> _FakeResponse:
        return _FakeResponse(
            {"error": {"errorName": "SYNTAX_ERROR", "message": "mismatched input"}}
        )

    monkeypatch.setattr(trino.requests, "post", fake_post)

    with pytest.raises(RuntimeError, match="SYNTAX_ERROR"):
        trino.run_query("SELECT broken")


def test_list_iceberg_tables_filters_information_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        trino,
        "run_query",
        lambda sql: [
            {"table_schema": "capiba", "table_name": "contracts"},
            {"table_schema": "capiba", "table_name": "raw_pncp"},
        ],
    )

    assert trino.list_iceberg_tables("silver") == [
        "capiba.contracts",
        "capiba.raw_pncp",
    ]
