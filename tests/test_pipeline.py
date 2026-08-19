"""Tests for the pipeline vertical slice.

Responsibility: Validate atomic tasks and DAGs.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from capiba.pipeline.tasks import (
    _lake_run_date,
    persist_contracts,
    task_dbt_run,
    task_detect,
)


def _silver_contract(
    contract_id: str,
    buyer_siafi: str = "123456",
    supplier_cnpj: str = "12345678000195",
    amount: float = 1000.0,
    validity_days: int = 30,
) -> dict[str, Any]:
    """Builds a silver-shaped contract row for the detection tests."""
    return {
        "id": contract_id,
        "amount": amount,
        "validity_start": "2026-01-01",
        "validity_end": f"2026-01-{1 + validity_days:02d}",
        "buyer": {"siafi_code": buyer_siafi},
        "supplier": {"cnpj": supplier_cnpj},
    }


class TestLakeRunDate:
    """Tests for the Airflow context run-date extraction."""

    def test_logical_date_datetime(self) -> None:
        """A datetime ``logical_date`` must be converted to a date."""
        context = {"logical_date": datetime(2026, 3, 5, 12, 30)}

        assert _lake_run_date(context) == date(2026, 3, 5)

    def test_dag_run_run_after_string(self) -> None:
        """A string ``run_after`` on the DAG run must be parsed as a date."""
        dag_run = MagicMock()
        dag_run.run_after = "2026-03-05T00:00:00+00:00"

        assert _lake_run_date({"dag_run": dag_run}) == date(2026, 3, 5)

    def test_dag_run_run_after_datetime(self) -> None:
        """A datetime ``run_after`` on the DAG run must be converted to a date."""
        dag_run = MagicMock()
        dag_run.run_after = datetime(2026, 3, 5, 0, 0)

        assert _lake_run_date({"dag_run": dag_run}) == date(2026, 3, 5)

    def test_empty_context(self) -> None:
        """Without any date key the result must be None."""
        assert _lake_run_date({}) is None


class TestPersistContracts:
    """Tests for the pure persistence step."""

    @patch("capiba.pipeline.tasks.bulk_upsert_contracts")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_persist_success(
        self,
        mock_get_db: MagicMock,
        mock_bulk: MagicMock,
        sample_contracts: list[dict[str, object]],
    ) -> None:
        """Valid contracts must be revalidated, persisted and traced."""
        mock_bulk.return_value = {"inserted": 2, "updated": 0}

        summary = persist_contracts(sample_contracts, execution_date="2026-01-01")

        assert summary["inserted"] == 2
        assert summary["source_id"]
        assert "lineage" in summary
        db, contracts = mock_bulk.call_args.args[:2]
        assert db is mock_get_db.return_value
        assert len(contracts) == 2

    @patch("capiba.pipeline.tasks.bulk_upsert_contracts")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_persist_skips_invalid_contracts(
        self,
        mock_get_db: MagicMock,
        mock_bulk: MagicMock,
        sample_contracts: list[dict[str, object]],
    ) -> None:
        """Contracts that fail revalidation must be skipped."""
        mock_bulk.return_value = {"inserted": 1, "updated": 0}

        summary = persist_contracts([*sample_contracts, {"id": "broken"}])

        assert summary["inserted"] == 1
        contracts = mock_bulk.call_args.args[1]
        assert len(contracts) == 2

    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_persist_failure(self, mock_get_db: MagicMock) -> None:
        """Database failures must be reported as an error summary."""
        mock_get_db.side_effect = ConnectionError("arango down")

        summary = persist_contracts([{"id": "C001"}])

        assert summary == {"error": "arango down"}


class TestTaskDetect:
    """Tests for the fraud-signal detection task."""

    @pytest.fixture(autouse=True)
    def _mock_evidence(self) -> Any:
        """Evidence storage (MinIO) is mocked in every detect test."""
        with (
            patch("capiba.pipeline.tasks.EvidenceStorage"),
            patch("capiba.pipeline.tasks.store_signal_packages") as mock_store,
        ):
            self.mock_store_packages = mock_store
            yield

    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_writes_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Signals computed from the silver table must be written to gold."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_lake.write_fraud_signals.assert_called_once()
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert mock_lake.write_fraud_signals.call_args.kwargs["run_date"] == date(
            2026, 1, 1
        )
        assert {s["signal_type"] for s in signals} == {"concentration"}

    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_adds_collusion_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Collusion pairs from the graph must be aggregated as signals."""
        from capiba.config import DETECTION_COLLUSION_MIN_WINS

        mock_lake.read_silver_contracts.return_value = []
        mock_collusion.return_value = [{"91000000000002", "91000000000001"}]

        summary = task_detect(ds="2026-01-01")

        mock_collusion.assert_called_once_with(
            mock_get_db.return_value, min_wins=DETECTION_COLLUSION_MIN_WINS
        )
        assert summary["signals"] == 1
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert signals[0]["signal_type"] == "collusion_network"
        assert signals[0]["entity_id"] == "91000000000001+91000000000002"

    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_arango_failure_keeps_statistical_signals(
        self, mock_lake: MagicMock, mock_get_db: MagicMock
    ) -> None:
        """An ArangoDB failure must not abort the task nor drop the signals."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_get_db.side_effect = ConnectionError("arango down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert {s["signal_type"] for s in signals} == {"concentration"}

    @patch("capiba.pipeline.tasks.register_signals")
    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_registers_signals_for_triage(
        self,
        mock_lake: MagicMock,
        mock_get_db: MagicMock,
        mock_collusion: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """Computed signals must enter the editorial triage queue (O10)."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        mock_register.assert_called_once()
        assert mock_register.call_args.args[0] is mock_get_db.return_value
        assert len(mock_register.call_args.args[1]) == summary["signals"]

    @patch("capiba.pipeline.tasks.register_signals")
    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_triage_failure_does_not_abort(
        self,
        mock_lake: MagicMock,
        mock_get_db: MagicMock,
        mock_collusion: MagicMock,
        mock_register: MagicMock,
    ) -> None:
        """A triage failure must not abort the task nor drop the signals."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []
        mock_register.side_effect = RuntimeError("triage down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_lake.write_fraud_signals.assert_called_once()

    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_stores_evidence_packages(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Signals must be stored as reproducible evidence packages (O9)."""
        contracts = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_lake.read_silver_contracts.return_value = contracts
        mock_collusion.return_value = []

        task_detect(ds="2026-01-01")

        self.mock_store_packages.assert_called_once()
        args = self.mock_store_packages.call_args.args
        assert args[1] == mock_lake.write_fraud_signals.call_args.args[0]
        assert args[2] == contracts
        assert args[3] == date(2026, 1, 1)

    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_evidence_failure_does_not_abort(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """A MinIO failure storing packages must not abort the task."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []
        self.mock_store_packages.side_effect = RuntimeError("minio down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_lake.write_fraud_signals.assert_called_once()

    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_read_failure(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Silver read failures must yield an empty signal set."""
        mock_lake.read_silver_contracts.side_effect = RuntimeError("lake down")
        mock_collusion.return_value = []

        summary = task_detect(ds="2026-01-01")

        assert summary == {"signals": 0}
        mock_lake.write_fraud_signals.assert_not_called()

    @patch("capiba.pipeline.tasks.detect_collusion")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_write_failure(
        self, mock_lake: MagicMock, mock_get_db: MagicMock, mock_collusion: MagicMock
    ) -> None:
        """Gold write failures must not abort the task."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_collusion.return_value = []
        mock_lake.write_fraud_signals.side_effect = RuntimeError("lake down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1


class TestTaskDbtRun:
    """Tests for the dbt run task."""

    @patch("capiba.pipeline.dbt_runner.run_dbt")
    def test_task_dbt_run(self, mock_run_dbt: MagicMock) -> None:
        """The task must invoke dbt and return an execution summary."""
        from capiba.config import DBT_PROJECT_DIR

        summary = task_dbt_run()

        mock_run_dbt.assert_called_once_with("run")
        assert summary == {"dbt": "run", "project_dir": DBT_PROJECT_DIR}


class TestRecordQualityBatch:
    """Tests for the quality-monitor hook of the validate task."""

    @patch("capiba.quality.monitor.QualityMonitor")
    def test_records_profile_and_batch_metrics(self, mock_cls: MagicMock) -> None:
        """A non-empty batch must be profiled, checked and recorded."""
        from capiba.pipeline.tasks import _record_quality_batch

        monitor = mock_cls.return_value
        contracts = [
            {"id": "C001", "amount": "100.0", "buyer": {"siafi_code": "1"}},
            {"id": "C002", "amount": "200.0", "buyer": {"siafi_code": "2"}},
        ]
        report = {
            "total": 2,
            "duplicates": 0,
            "normalization_errors": 1,
            "quality_rules": [
                {"rule": "r1", "severity": "error", "violations": 3},
                {"rule": "r2", "severity": "warning", "error": "boom"},
                {"rule": "r3", "severity": "info", "violations": 0},
            ],
        }

        _record_quality_batch("daily_ingestion", contracts, report)

        monitor.register_baseline.assert_called_once()
        assert monitor.register_baseline.call_args.args[0] == "pipeline:daily_ingestion"
        monitor.check.assert_called_once()
        monitor.record_batch.assert_called_once_with(
            "pipeline:daily_ingestion",
            {
                "total": 2,
                "duplicates": 0,
                "normalization_errors": 1,
                "quality_rule_failures": {"error": 1, "warning": 1},
            },
        )

    @patch("capiba.quality.monitor.QualityMonitor")
    def test_empty_batch_records_without_profile(self, mock_cls: MagicMock) -> None:
        """An empty batch skips the profile but still records the metrics."""
        from capiba.pipeline.tasks import _record_quality_batch

        monitor = mock_cls.return_value

        _record_quality_batch("daily_ingestion", [], {"total": 0, "duplicates": 0})

        monitor.register_baseline.assert_not_called()
        monitor.check.assert_not_called()
        monitor.record_batch.assert_called_once_with(
            "pipeline:daily_ingestion",
            {
                "total": 0,
                "duplicates": 0,
                "normalization_errors": 0,
                "quality_rule_failures": {},
            },
        )

    @patch("capiba.quality.monitor.QualityMonitor")
    def test_monitor_failure_is_swallowed(self, mock_cls: MagicMock) -> None:
        """A monitor failure (e.g. Redis down) must never break the task."""
        from capiba.pipeline.tasks import _record_quality_batch

        mock_cls.side_effect = RuntimeError("redis down")

        _record_quality_batch("daily_ingestion", [{"id": "C001"}], {"total": 1})
