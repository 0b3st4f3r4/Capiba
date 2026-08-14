"""Tests for the gold platform_metrics table.

Responsibility: Validate the write_platform_metrics round-trip (one row
per pipeline step, SQLite catalog, no infra) and that run_pipeline
publishes the report metrics best-effort on success and on failure.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.pipeline import lake
from capiba.pipeline.registry import NORMALIZER_REGISTRY, SOURCE_REGISTRY, SourceDef
from capiba.pipeline.runner import (
    PipelineReport,
    PipelineRunError,
    StepMetrics,
    run_pipeline,
)
from capiba.pipeline.spec import PipelineSpec

RUN_DATE = date(2026, 1, 15)


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    """Replaces the lazy lake MinIO client factory with a mock."""
    client = MagicMock()
    monkeypatch.setattr(lake, "get_client", lambda: client)
    return client


@pytest.fixture
def local_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Points the lake to a SQLite catalog with a local warehouse."""
    monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path}/catalog.db")
    monkeypatch.setattr(lake, "ICEBERG_LOCAL_WAREHOUSE", str(tmp_path / "warehouse"))
    lake._catalogs.clear()
    yield tmp_path
    lake._catalogs.clear()


def _report() -> PipelineReport:
    """Builds a run report with two steps (one with errors)."""
    return PipelineReport(
        pipeline="daily_ingestion",
        execution_date=RUN_DATE,
        started_at=datetime(2026, 1, 15, 6, 0, tzinfo=UTC),
        duration_seconds=12.5,
        success=True,
        steps=[
            StepMetrics(name="crawl_pncp", duration_seconds=3.25, rows_out=10),
            StepMetrics(
                name="validate",
                duration_seconds=0.5,
                rows_in=10,
                rows_out=10,
                errors=2,
            ),
        ],
    )


def _read_metrics(catalog_path: Path) -> list[dict[str, Any]]:
    """Reads every row of the gold platform_metrics table."""
    table = lake.get_catalog(lake.ICEBERG_WAREHOUSE_GOLD).load_table(
        "capiba.platform_metrics"
    )
    return table.scan().to_pandas().to_dict("records")


class TestWritePlatformMetrics:
    """Round-trip of the gold platform_metrics Iceberg table."""

    def test_writes_one_row_per_step(self, local_catalog: Path) -> None:
        """Each report step lands as a typed row in the gold table."""
        identifier = lake.write_platform_metrics(_report(), run_date=RUN_DATE)

        assert identifier == "capiba.platform_metrics"
        rows = _read_metrics(local_catalog)
        assert len(rows) == 2
        assert rows[0]["dt"] == "2026-01-15"
        assert rows[0]["run_id"] == "daily_ingestion-20260115T060000"
        assert rows[0]["pipeline"] == "daily_ingestion"
        assert rows[0]["step"] == "crawl_pncp"
        assert rows[0]["duration_s"] == 3.25
        assert rows[0]["rows_out"] == 10
        assert rows[1]["step"] == "validate"
        assert rows[1]["validation_errors"] == 2

    def test_empty_steps_creates_table(self, local_catalog: Path) -> None:
        """A report without steps creates the table without appending."""
        report = _report().model_copy(update={"steps": []})

        lake.write_platform_metrics(report, run_date=RUN_DATE)

        assert _read_metrics(local_catalog) == []


class TestRunPipelinePublishesMetrics:
    """run_pipeline persists the report metrics best-effort."""

    def test_success_run_publishes_metrics(
        self, mock_client: MagicMock, local_catalog: Path
    ) -> None:
        """A successful run lands one row per step in platform_metrics."""
        spec = PipelineSpec.model_validate(
            {
                "name": "metrics_run",
                "sources": ["mock_pncp"],
                "formula": "contracts_default",
                "destinations": ["lake_bronze"],
            }
        )

        report = run_pipeline(spec, RUN_DATE)

        assert report.success is True
        rows = _read_metrics(local_catalog)
        assert {row["step"] for row in rows} == {step.name for step in report.steps}
        assert all(row["pipeline"] == "metrics_run" for row in rows)

    def test_failed_run_still_publishes_metrics(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A failing run also publishes the partial report metrics."""

        def broken_fetch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            raise ConnectionError("source down")

        monkeypatch.setitem(SOURCE_REGISTRY, "broken", SourceDef(fetch=broken_fetch))
        monkeypatch.setitem(NORMALIZER_REGISTRY, "broken", lambda raw: None)  # type: ignore[return-value]
        spec = PipelineSpec.model_validate(
            {
                "name": "broken_run",
                "sources": ["broken"],
                "formula": "contracts_default",
                "destinations": ["lake_bronze"],
            }
        )

        with pytest.raises(PipelineRunError, match="source down"):
            run_pipeline(spec, RUN_DATE)

        rows = _read_metrics(local_catalog)
        assert len(rows) == 1
        assert rows[0]["step"] == "crawl_broken"

    def test_metrics_write_failure_is_best_effort(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A platform_metrics write failure does not fail the run."""
        monkeypatch.setattr(
            lake,
            "write_platform_metrics",
            MagicMock(side_effect=RuntimeError("catalog down")),
        )
        spec = PipelineSpec.model_validate(
            {
                "name": "metrics_off",
                "sources": ["mock_pncp"],
                "formula": "contracts_default",
                "destinations": ["lake_bronze"],
            }
        )

        report = run_pipeline(spec, RUN_DATE)

        assert report.success is True
