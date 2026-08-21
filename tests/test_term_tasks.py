"""Tests for the terms_collect wiring (PR-D-05b).

Responsibility: Validate the registry adapter of the
``pncp_contract_terms`` source (cohort params pass-through, window
ignored) and the granular ``persist_<source>_terms`` Airflow task (XCom
contract) without a cluster — the lake and the terms persistence core are
mocked.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.pipeline import lake, term_tasks
from capiba.pipeline.registry import SOURCE_REGISTRY
from capiba.pipeline.term_tasks import task_persist_contract_terms


class TestTermsSourceAdapter:
    """Tests for the pncp_contract_terms registry fetch adapter."""

    def test_delegates_to_the_cohort_reader(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The adapter enumerates the cohort from the lake, params verbatim."""
        cohort = [{"numeroControlePNCP": "00394460000141-1-000012/2026"}]
        reader = MagicMock(return_value=cohort)
        monkeypatch.setattr(lake, "read_terms_pilot_cohort", reader)

        fetch = SOURCE_REGISTRY["pncp_contract_terms"].fetch
        assert fetch is not None
        records = fetch(None, None, include_flagged=True, siafi_codes=["2531"])

        assert records == cohort
        reader.assert_called_once_with(include_flagged=True, siafi_codes=["2531"])

    def test_window_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A bounded window does not reach the cohort reader."""
        reader = MagicMock(return_value=[])
        monkeypatch.setattr(lake, "read_terms_pilot_cohort", reader)

        fetch = SOURCE_REGISTRY["pncp_contract_terms"].fetch
        assert fetch is not None
        fetch(date(2026, 8, 20), date(2026, 8, 21), siafi_codes=["2531"])

        reader.assert_called_once_with(siafi_codes=["2531"])


class TestPersistContractTermsTask:
    """Tests for the persist_<source>_terms task (XCom contract)."""

    def test_pulls_cohort_persists_and_pushes_back(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The cohort flows from the crawl XCom through the persist core."""
        records: list[dict[str, Any]] = [
            {"numeroControlePNCP": "00394460000141-1-000012/2026"}
        ]
        expected = {
            "source": "pncp_contract_terms",
            "terms_fetched": 1,
            "terms_skipped": 0,
            "errors": 0,
        }
        persist = MagicMock(return_value=expected)
        monkeypatch.setattr(term_tasks, "persist_contract_terms", persist)
        ti = MagicMock()
        ti.xcom_pull.return_value = records

        summary = task_persist_contract_terms(
            "pncp_contract_terms", "unused.yaml", ti=ti, ds="2026-08-21"
        )

        assert summary == expected
        ti.xcom_pull.assert_called_once_with(
            task_ids="crawl_pncp_contract_terms", key="raw_pncp_contract_terms"
        )
        persist.assert_called_once_with(
            "pncp_contract_terms", records, run_date=date(2026, 8, 21)
        )
        assert ti.xcom_push.call_count == 2

    def test_missing_cohort_pulls_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No upstream XCom (empty cohort) persists nothing."""
        persist = MagicMock(
            return_value={
                "source": "pncp_contract_terms",
                "terms_fetched": 0,
                "terms_skipped": 0,
                "errors": 0,
            }
        )
        monkeypatch.setattr(term_tasks, "persist_contract_terms", persist)
        ti = MagicMock()
        ti.xcom_pull.return_value = None

        task_persist_contract_terms(
            "pncp_contract_terms", "unused.yaml", ti=ti, ds="2026-08-21"
        )

        persist.assert_called_once_with(
            "pncp_contract_terms", [], run_date=date(2026, 8, 21)
        )
