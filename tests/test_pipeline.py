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
    normalize_contracts,
    persist_contracts,
    task_crawl_federal_revenue,
    task_crawl_pncp,
    task_crawl_transparency,
    task_dbt_run,
    task_detect,
    task_normalize,
    task_persist,
    task_validate,
)


class TestTasks:
    """Tests for atomic tasks."""

    @patch("capiba.pipeline.tasks.fetch_contracts")
    def test_task_crawl_pncp(self, mock_fetch: MagicMock) -> None:
        """Crawl task must convert ds to ISO string and call fetch_contracts."""
        mock_fetch.return_value = [{"id": "1"}]
        mock_ti = MagicMock()
        context = {
            "ds": "2026-01-01",
            "ti": mock_ti,
        }

        result = task_crawl_pncp(**context)

        assert result == [{"id": "1"}]
        mock_fetch.assert_called_once_with(
            start_date="2025-12-31",
            end_date="2026-01-01",
        )
        mock_ti.xcom_push.assert_called_once_with(key="pncp_raw", value=[{"id": "1"}])

    def test_task_validate(self) -> None:
        """Validation task must return a report."""
        mock_ti = MagicMock()
        mock_ti.xcom_pull.return_value = [
            {"id": "C001"},
            {"id": "C002"},
        ]
        context = {"ti": mock_ti}

        report = task_validate(**context)
        assert report["total"] == 2
        assert report["valid"] is True
        assert "checksum" in report

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.ingestion.crawler_federal_revenue.download_cnpj_dump")
    def test_task_crawl_federal_revenue(
        self, mock_download: MagicMock, mock_lake: MagicMock
    ) -> None:
        """Federal Revenue task uploads files and returns the manifest summary."""
        from pathlib import Path

        def fake_download(
            destination: Path, *_args: object, **_kwargs: object
        ) -> list[Path]:
            dump = destination / "Cnaes.zip"
            dump.write_bytes(b"zip-bytes")
            return [dump]

        mock_download.side_effect = fake_download
        mock_lake.write_bronze_file.side_effect = (
            lambda source, filename, data, run_date=None: f"{source}/files/{filename}"
        )

        result = task_crawl_federal_revenue(ds="2026-02-02")

        assert result == {"reference_month": "2026-01", "files": 1}
        assert mock_lake.write_bronze_file.call_count == 1
        args = mock_lake.write_bronze_file.call_args.args
        assert args[0] == "federal_revenue"
        assert args[1] == "Cnaes.zip"
        assert args[2] == b"zip-bytes"
        mock_lake.write_bronze_table.assert_called_once()

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.ingestion.crawler_federal_revenue.download_cnpj_dump")
    def test_task_crawl_federal_revenue_no_files(
        self, mock_download: MagicMock, mock_lake: MagicMock
    ) -> None:
        """The task fails loudly when the share serves no valid file."""
        mock_download.return_value = []

        with pytest.raises(RuntimeError, match="No Federal Revenue files"):
            task_crawl_federal_revenue(ds="2026-02-02")

        mock_lake.write_bronze_table.assert_not_called()


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


class TestNormalizeContracts:
    """Tests for the pure normalization step."""

    def test_normalize_valid_records(self) -> None:
        """Valid raw records from both sources must be normalized."""
        pncp_raw = [
            {
                "numeroControlePNCP": "PNCP-1",
                "objetoCompra": "Office supplies",
                "valorGlobal": 1500.0,
            }
        ]
        transparency_raw = [
            {
                "id": "TR-1",
                "objeto": "Cleaning services",
                "valor": 2000.0,
            }
        ]

        contracts, errors = normalize_contracts(pncp_raw, transparency_raw)

        assert errors == 0
        assert len(contracts) == 2
        assert {c["id"] for c in contracts} == {"PNCP-1", "TR-1"}

    def test_normalize_invalid_records(self) -> None:
        """Records that fail to parse must be counted as errors."""
        contracts, errors = normalize_contracts([None], ["not-a-dict"])

        assert contracts == []
        assert errors == 2


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


class TestTaskCrawlTransparency:
    """Tests for the Transparency Portal crawl task."""

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.fetch_purchases")
    def test_task_crawl_transparency(
        self, mock_fetch: MagicMock, mock_lake: MagicMock
    ) -> None:
        """The task must fetch purchases and push them to XCom."""
        mock_fetch.return_value = [{"id": "P1"}]
        mock_ti = MagicMock()

        result = task_crawl_transparency(ds="2026-01-15", ti=mock_ti)

        assert result == [{"id": "P1"}]
        mock_fetch.assert_called_once_with(year=2026, month=1)
        mock_lake.write_bronze.assert_called_once()
        mock_lake.write_bronze_table.assert_called_once()
        mock_ti.xcom_push.assert_called_once_with(
            key="transparency_raw", value=[{"id": "P1"}]
        )

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.fetch_purchases")
    def test_task_crawl_transparency_lake_failure(
        self, mock_fetch: MagicMock, mock_lake: MagicMock
    ) -> None:
        """Bronze layer failures must not abort the task."""
        mock_fetch.return_value = [{"id": "P1"}]
        mock_lake.write_bronze.side_effect = RuntimeError("lake down")
        mock_ti = MagicMock()

        result = task_crawl_transparency(ds="2026-01-15", ti=mock_ti)

        assert result == [{"id": "P1"}]
        mock_ti.xcom_push.assert_called_once()


class TestTaskCrawlPncpLakeFailure:
    """Tests for the PNCP crawl task bronze failure path."""

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.fetch_contracts")
    def test_task_crawl_pncp_lake_failure(
        self, mock_fetch: MagicMock, mock_lake: MagicMock
    ) -> None:
        """Bronze layer failures must not abort the task."""
        mock_fetch.return_value = [{"id": "1"}]
        mock_lake.write_bronze.side_effect = RuntimeError("lake down")
        mock_ti = MagicMock()

        result = task_crawl_pncp(ds="2026-01-01", ti=mock_ti)

        assert result == [{"id": "1"}]
        mock_ti.xcom_push.assert_called_once_with(key="pncp_raw", value=[{"id": "1"}])


class TestTaskNormalize:
    """Tests for the normalize task wrapper."""

    def test_task_normalize(self) -> None:
        """The task must pull raw payloads and push normalized contracts."""
        mock_ti = MagicMock()
        pulls = {
            "pncp_raw": [
                {
                    "numeroControlePNCP": "PNCP-1",
                    "objetoCompra": "Office supplies",
                    "valorGlobal": 1500.0,
                }
            ],
            "transparency_raw": [],
        }
        mock_ti.xcom_pull.side_effect = lambda task_ids=None, key=None: pulls.get(key)

        contracts = task_normalize(ti=mock_ti)

        assert len(contracts) == 1
        assert contracts[0]["id"] == "PNCP-1"
        pushed = {
            c.kwargs["key"]: c.kwargs["value"] for c in mock_ti.xcom_push.mock_calls
        }
        assert pushed["normalized_contracts"] == contracts
        assert pushed["normalization_errors"] == 0

    def test_task_normalize_empty_xcom(self) -> None:
        """Missing XCom payloads must be treated as empty lists."""
        mock_ti = MagicMock()
        mock_ti.xcom_pull.return_value = None

        contracts = task_normalize(ti=mock_ti)

        assert contracts == []


class TestTaskValidateLakeFailure:
    """Tests for the validate task silver failure path."""

    @patch("capiba.pipeline.tasks.lake")
    def test_task_validate_silver_failure(self, mock_lake: MagicMock) -> None:
        """Silver layer failures must not abort validation."""
        mock_lake.write_silver.side_effect = RuntimeError("lake down")
        mock_ti = MagicMock()
        mock_ti.xcom_pull.return_value = [{"id": "C001"}]

        report = task_validate(ds="2026-01-01", ti=mock_ti)

        assert report["total"] == 1
        assert report["valid"] is True


class TestTaskPersist:
    """Tests for the persist task wrapper."""

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.persist_contracts")
    def test_task_persist(self, mock_persist: MagicMock, mock_lake: MagicMock) -> None:
        """The task must persist contracts and write the gold run report."""
        mock_persist.return_value = {"inserted": 2}
        mock_ti = MagicMock()
        pulls = {
            ("normalize", "normalized_contracts"): [{"id": "C001"}],
            ("validate", None): {"valid": True},
        }
        mock_ti.xcom_pull.side_effect = lambda task_ids=None, key=None: pulls.get(
            (task_ids, key)
        )

        summary = task_persist(ds="2026-01-01", ti=mock_ti)

        assert summary == {"inserted": 2}
        mock_persist.assert_called_once_with(
            [{"id": "C001"}], execution_date="2026-01-01"
        )
        mock_lake.write_gold.assert_called_once()
        gold_kwargs = mock_lake.write_gold.call_args.kwargs
        assert gold_kwargs["report_name"] == "daily_ingestion"
        assert gold_kwargs["run_date"] == date(2026, 1, 1)
        gold_report = mock_lake.write_gold.call_args.args[0]
        assert gold_report["execution_date"] == "2026-01-01"
        assert gold_report["persistence"] == {"inserted": 2}
        mock_ti.xcom_push.assert_called_once_with(
            key="persistence", value={"inserted": 2}
        )

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.persist_contracts")
    def test_task_persist_gold_failure(
        self, mock_persist: MagicMock, mock_lake: MagicMock
    ) -> None:
        """Gold layer failures must not abort the task."""
        mock_persist.return_value = {"inserted": 1}
        mock_lake.write_gold.side_effect = RuntimeError("lake down")
        mock_ti = MagicMock()
        mock_ti.xcom_pull.return_value = []

        summary = task_persist(ds="2026-01-01", ti=mock_ti)

        assert summary == {"inserted": 1}
        mock_ti.xcom_push.assert_called_once()


class TestTaskDetect:
    """Tests for the fraud-signal detection task."""

    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_writes_signals(self, mock_lake: MagicMock) -> None:
        """Signals computed from the silver table must be written to gold."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_lake.write_fraud_signals.assert_called_once()
        signals = mock_lake.write_fraud_signals.call_args.args[0]
        assert mock_lake.write_fraud_signals.call_args.kwargs["run_date"] == date(
            2026, 1, 1
        )
        assert {s["signal_type"] for s in signals} == {"concentration"}

    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_read_failure(self, mock_lake: MagicMock) -> None:
        """Silver read failures must yield an empty signal set."""
        mock_lake.read_silver_contracts.side_effect = RuntimeError("lake down")

        summary = task_detect(ds="2026-01-01")

        assert summary == {"signals": 0}
        mock_lake.write_fraud_signals.assert_not_called()

    @patch("capiba.pipeline.tasks.lake")
    def test_task_detect_write_failure(self, mock_lake: MagicMock) -> None:
        """Gold write failures must not abort the task."""
        mock_lake.read_silver_contracts.return_value = [
            _silver_contract(f"C{i:03d}", supplier_cnpj=f"1111111100019{i}")
            for i in range(3)
        ]
        mock_lake.write_fraud_signals.side_effect = RuntimeError("lake down")

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1


class TestTaskCrawlFederalRevenueBronzeFailure:
    """Tests for the Federal Revenue task manifest failure path."""

    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.ingestion.crawler_federal_revenue.download_cnpj_dump")
    def test_manifest_write_failure(
        self, mock_download: MagicMock, mock_lake: MagicMock
    ) -> None:
        """Bronze manifest write failures must not abort the task."""
        from pathlib import Path

        def fake_download(
            destination: Path, *_args: object, **_kwargs: object
        ) -> list[Path]:
            dump = destination / "Cnaes.zip"
            dump.write_bytes(b"zip-bytes")
            return [dump]

        mock_download.side_effect = fake_download
        mock_lake.write_bronze_file.side_effect = (
            lambda source, filename, data, run_date=None: f"{source}/files/{filename}"
        )
        mock_lake.write_bronze.side_effect = RuntimeError("lake down")

        result = task_crawl_federal_revenue(ds="2026-02-02")

        assert result == {"reference_month": "2026-01", "files": 1}


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
