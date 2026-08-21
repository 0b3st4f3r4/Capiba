"""Tests for the per-contract terms persistence (PR-D-05b, plan B).

Responsibility: Validate ``persist_contract_terms`` without a cluster or
network — the PNCP fetch and the lake file functions are mocked: the
bronze checkpoint per contract, the skip-existing resume on retry, the
204-means-empty-list discipline and the best-effort error accounting.
"""

from __future__ import annotations

import gzip
import json
from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest
import requests

from capiba.pipeline import lake, tasks

SOURCE = "pncp_contract_terms"
RUN_DATE = date(2026, 8, 21)


def _record(seq: int, cnpj: str = "00394460000141") -> dict[str, Any]:
    return {"numeroControlePNCP": f"{cnpj}-1-{seq:06d}/2026"}


@pytest.fixture
def mocked_lake(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replaces the lake file functions used by persist_contract_terms."""
    mocks = {
        "list_bronze_files": MagicMock(return_value=[]),
        "write_bronze_file": MagicMock(return_value="key"),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(lake, name, mock)
    return mocks


def _written_payload(mock: MagicMock) -> dict[str, Any]:
    """Decodes the gzip JSON payload of the first write_bronze_file call."""
    data = mock.call_args.args[2]
    return json.loads(gzip.decompress(data))


class TestPersistContractTerms:
    def test_fetches_and_persists_each_contract(
        self, monkeypatch: pytest.MonkeyPatch, mocked_lake: dict[str, MagicMock]
    ) -> None:
        """Each contract's terms land under the deterministic checkpoint name."""
        fetch = MagicMock(
            return_value=[{"tipoTermoContratoNome": "Termo Aditivo"}]
        )
        monkeypatch.setattr(tasks, "fetch_contract_terms", fetch)
        record = _record(12)

        summary = tasks.persist_contract_terms(SOURCE, [record], run_date=RUN_DATE)

        assert summary == {
            "source": SOURCE,
            "terms_fetched": 1,
            "terms_skipped": 0,
            "errors": 0,
        }
        fetch.assert_called_once_with("00394460000141", 2026, 12)
        args = mocked_lake["write_bronze_file"].call_args.args
        assert args[0] == SOURCE
        assert args[1] == "00394460000141/2026/12.json.gz"
        payload = _written_payload(mocked_lake["write_bronze_file"])
        assert payload["numeroControlePNCP"] == record["numeroControlePNCP"]
        assert len(payload["terms"]) == 1
        assert record["terms_bronze_file"] == "00394460000141/2026/12.json.gz"

    def test_no_terms_persists_empty_list(
        self, monkeypatch: pytest.MonkeyPatch, mocked_lake: dict[str, MagicMock]
    ) -> None:
        """HTTP 204 (fetch returns None) persists an empty terms list — data, not failure."""
        monkeypatch.setattr(tasks, "fetch_contract_terms", MagicMock(return_value=None))

        summary = tasks.persist_contract_terms(SOURCE, [_record(3)], run_date=RUN_DATE)

        assert summary["terms_fetched"] == 1
        assert summary["errors"] == 0
        assert _written_payload(mocked_lake["write_bronze_file"])["terms"] == []

    def test_retry_skips_contracts_already_in_bronze(
        self, monkeypatch: pytest.MonkeyPatch, mocked_lake: dict[str, MagicMock]
    ) -> None:
        """A retry resumes where it stopped and never duplicates checkpoints."""
        mocked_lake["list_bronze_files"].return_value = [
            f"{SOURCE}/files/dt={RUN_DATE.isoformat()}/00394460000141/2026/12.json.gz"
        ]
        fetch = MagicMock(return_value=[])
        monkeypatch.setattr(tasks, "fetch_contract_terms", fetch)
        persisted = _record(12)
        pending = _record(13)

        summary = tasks.persist_contract_terms(
            SOURCE, [persisted, pending], run_date=RUN_DATE
        )

        assert summary["terms_skipped"] == 1
        assert summary["terms_fetched"] == 1
        fetch.assert_called_once_with("00394460000141", 2026, 13)
        assert persisted["terms_bronze_file"] == "00394460000141/2026/12.json.gz"
        assert mocked_lake["write_bronze_file"].call_count == 1

    def test_fetch_failure_is_counted_and_never_fatal(
        self, monkeypatch: pytest.MonkeyPatch, mocked_lake: dict[str, MagicMock]
    ) -> None:
        """A failed query leaves no checkpoint (retried later); the crawl goes on."""
        fetch = MagicMock(
            side_effect=[requests.HTTPError("boom"), []]
        )
        monkeypatch.setattr(tasks, "fetch_contract_terms", fetch)
        failing = _record(12)
        healthy = _record(13)

        summary = tasks.persist_contract_terms(
            SOURCE, [failing, healthy], run_date=RUN_DATE
        )

        assert summary["errors"] == 1
        assert summary["terms_fetched"] == 1
        assert "terms_bronze_file" not in failing
        assert "terms_bronze_file" in healthy

    def test_invalid_control_number_is_counted(
        self, monkeypatch: pytest.MonkeyPatch, mocked_lake: dict[str, MagicMock]
    ) -> None:
        """A malformed numeroControlePNCP is an error, not a crash."""
        fetch = MagicMock()
        monkeypatch.setattr(tasks, "fetch_contract_terms", fetch)

        summary = tasks.persist_contract_terms(
            SOURCE, [{"numeroControlePNCP": "lixo"}], run_date=RUN_DATE
        )

        assert summary["errors"] == 1
        fetch.assert_not_called()
        mocked_lake["write_bronze_file"].assert_not_called()

    def test_listing_failure_degrades_to_fetching_everything(
        self, monkeypatch: pytest.MonkeyPatch, mocked_lake: dict[str, MagicMock]
    ) -> None:
        """If the bronze listing fails, the crawl falls back to no checkpoints."""
        mocked_lake["list_bronze_files"].side_effect = RuntimeError("minio down")
        monkeypatch.setattr(tasks, "fetch_contract_terms", MagicMock(return_value=[]))

        summary = tasks.persist_contract_terms(SOURCE, [_record(3)], run_date=RUN_DATE)

        assert summary["terms_fetched"] == 1

    def test_records_without_control_number_are_ignored(
        self, monkeypatch: pytest.MonkeyPatch, mocked_lake: dict[str, MagicMock]
    ) -> None:
        """Records without numeroControlePNCP are silently skipped."""
        fetch = MagicMock()
        monkeypatch.setattr(tasks, "fetch_contract_terms", fetch)

        summary = tasks.persist_contract_terms(SOURCE, [{}], run_date=RUN_DATE)

        assert summary == {
            "source": SOURCE,
            "terms_fetched": 0,
            "terms_skipped": 0,
            "errors": 0,
        }
