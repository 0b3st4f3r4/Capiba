"""Tests for the Querido Diário gazette crawler (O7).

Responsibility: Validate the /gazettes pagination and window params, the
extracted-text download (retry on 5xx/network, immediate raise on 4xx) and
the deterministic bronze file names — all offline (HTTP mocked).
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from capiba.ingestion import crawler_querido_diario as crawler


def _page(gazettes: list[dict[str, Any]], total: int) -> dict[str, Any]:
    return {"total_gazettes": total, "gazettes": gazettes}


def _gazette(n: int) -> dict[str, Any]:
    return {
        "territory_id": "2611606",
        "territory_name": "Recife",
        "state_code": "PE",
        "date": f"2026-08-{n:02d}",
        "edition": str(n),
        "is_extra_edition": False,
        "scraped_at": "2026-08-19T03:53:53",
        "url": f"https://data.queridodiario.ok.org.br/2611606/2026-08-{n:02d}/abc{n}.pdf",
        "txt_url": f"https://data.queridodiario.ok.org.br/2611606/2026-08-{n:02d}/abc{n}.txt",
        "excerpts": [],
    }


class TestFetchGazettes:
    def test_paginates_until_the_total(self, monkeypatch: pytest.MonkeyPatch) -> None:
        pages = {
            0: _page([_gazette(1), _gazette(2)], total=3),
            2: _page([_gazette(3)], total=3),
        }
        fetch = MagicMock(
            side_effect=lambda _url, params, **_kw: pages[params["offset"]]
        )
        monkeypatch.setattr(crawler, "fetch_page", fetch)

        records = crawler.fetch_gazettes("2611606", date(2026, 8, 1), date(2026, 8, 18))

        assert [r["edition"] for r in records] == ["1", "2", "3"]
        assert fetch.call_count == 2

    def test_sends_territory_window_and_sort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fetch = MagicMock(return_value=_page([], total=0))
        monkeypatch.setattr(crawler, "fetch_page", fetch)

        crawler.fetch_gazettes("2611606", date(2026, 8, 17), date(2026, 8, 18))

        params = fetch.call_args.args[1]
        assert params["territory_ids"] == ["2611606"]
        assert params["published_since"] == "2026-08-17"
        assert params["published_until"] == "2026-08-18"
        assert params["sort_by"] == "ascending_date"

    def test_stops_on_an_empty_page(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fetch = MagicMock(return_value=_page([], total=100))
        monkeypatch.setattr(crawler, "fetch_page", fetch)

        assert (
            crawler.fetch_gazettes("2611606", date(2026, 8, 1), date(2026, 8, 2)) == []
        )
        assert fetch.call_count == 1


class TestDownloadGazetteText:
    def test_returns_the_file_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response = MagicMock(content=b"plain text", status_code=200)
        response.raise_for_status.return_value = None
        monkeypatch.setattr(crawler.requests, "get", MagicMock(return_value=response))

        assert crawler.download_gazette_text("https://x/1.txt") == b"plain text"

    def test_retries_transient_server_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawler.time, "sleep", lambda *_a: None)
        error = requests.HTTPError(response=MagicMock(status_code=503))
        ok = MagicMock(content=b"ok", status_code=200)
        ok.raise_for_status.return_value = None
        get = MagicMock(side_effect=[error, ok])
        monkeypatch.setattr(crawler.requests, "get", get)

        assert crawler.download_gazette_text("https://x/1.txt") == b"ok"
        assert get.call_count == 2

    def test_client_errors_raise_immediately(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(crawler.time, "sleep", lambda *_a: None)
        error = requests.HTTPError(response=MagicMock(status_code=404))
        get = MagicMock(side_effect=error)
        monkeypatch.setattr(crawler.requests, "get", get)

        with pytest.raises(requests.HTTPError):
            crawler.download_gazette_text("https://x/missing.txt")
        assert get.call_count == 1


class TestTextFileName:
    def test_is_deterministic_and_scoped(self) -> None:
        record = _gazette(1)
        name = crawler.text_file_name(record)

        assert name == crawler.text_file_name(record)
        assert name.startswith("2611606-2026-08-01-")
        assert name.endswith(".txt")

    def test_changes_with_the_source_url(self) -> None:
        assert crawler.text_file_name(_gazette(1)) != crawler.text_file_name(
            _gazette(2)
        )
