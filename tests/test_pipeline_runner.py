"""Tests for the declarative pipeline runner.

Responsibility: Validate the contracts_default and file_dump formulas
end-to-end with mock sources and a local SQLite Iceberg catalog (no infra),
the per-step metrics of the run report, window overrides and failure
semantics (source failure fails the run; lake failures are best-effort).
"""

from __future__ import annotations

import io
import json
import zipfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from capiba.pipeline import lake, tasks
from capiba.pipeline.registry import (
    NORMALIZER_REGISTRY,
    SOURCE_REGISTRY,
    SourceDef,
)
from capiba.pipeline.runner import (
    PipelineRunError,
    run_pipeline,
)
from capiba.pipeline.spec import PipelineSpec
from capiba.pipeline.window import DateRange

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


def _spec(**overrides: Any) -> PipelineSpec:
    """Builds a contracts_default spec over the mock sources."""
    data: dict[str, Any] = {
        "name": "test_run",
        "window": "previous_day",
        "sources": ["mock_pncp", "mock_transparency"],
        "formula": "contracts_default",
        "validate": {"ruleset": "contract_rules"},
        "destinations": ["lake_bronze", "lake_silver"],
    }
    data.update(overrides)
    return PipelineSpec.model_validate(data)


class TestContractsDefaultFormula:
    """End-to-end tests of the contracts_default formula."""

    def test_run_with_mock_sources(
        self, mock_client: MagicMock, local_catalog: Path
    ) -> None:
        """Mock sources flow through crawl/normalize/validate to the lake."""
        report = run_pipeline(_spec(), RUN_DATE)

        assert report.success is True
        assert report.pipeline == "test_run"
        assert report.execution_date == RUN_DATE
        assert [s.name for s in report.steps] == [
            "crawl_mock_pncp",
            "crawl_mock_transparency",
            "normalize",
            "validate",
            "destination_lake_bronze",
            "destination_lake_silver",
        ]

        crawl = report.steps[0]
        assert crawl.rows_out == 1
        assert crawl.errors == 0
        assert crawl.duration_seconds >= 0

        normalize = report.steps[2]
        assert normalize.rows_in == 2
        assert normalize.rows_out == 2
        assert normalize.errors == 0

        assert report.validation is not None
        assert report.validation["total"] == 2
        assert report.validation["valid"] is True
        assert "checksum" in report.validation
        # The contract_rules ruleset ran over the flattened contracts
        assert report.validation["quality_rules"]
        rule_names = {r["rule"] for r in report.validation["quality_rules"]}
        assert "positive_value" in rule_names

        # Bronze raw tables and the silver contracts table were written
        bronze = report.outputs["destination_lake_bronze"]
        assert bronze["sources"] == ["mock_pncp", "mock_transparency"]
        silver_rows = lake.read_silver_contracts()
        assert len(silver_rows) == 2

    def test_gold_report_destination(
        self, mock_client: MagicMock, local_catalog: Path
    ) -> None:
        """The gold_report destination publishes the run report object."""
        report = run_pipeline(_spec(destinations=["gold_report"]), RUN_DATE)

        key = report.outputs["destination_gold_report"]["key"]
        assert key.startswith("reports/test_run/dt=2026-01-15/")
        buckets = {c.args[0] for c in mock_client.put_object.mock_calls}
        assert buckets == {"capiba-gold"}

    def test_arangodb_destination_best_effort(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Without ArangoDB the graph destination degrades to a step error."""
        # Força a queda do ArangoDB: o teste não pode depender de infra
        # local estar parada (ex.: port-forward do cluster ativo na 8529).
        def _no_db() -> Any:
            raise ConnectionError("arangodb down")

        monkeypatch.setattr(tasks, "get_capiba_db", _no_db)

        report = run_pipeline(_spec(destinations=["arangodb_graph"]), RUN_DATE)

        assert report.success is True
        step = report.steps[-1]
        assert step.name == "destination_arangodb_graph"
        assert step.errors == 1
        assert "error" in report.outputs["destination_arangodb_graph"]

    def test_lake_failure_is_best_effort(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Lake destination failures do not fail the run."""
        client = MagicMock()
        client.put_object.side_effect = RuntimeError("lake down")
        monkeypatch.setattr(lake, "get_client", lambda: client)

        report = run_pipeline(_spec(), RUN_DATE)

        assert report.success is True
        bronze_step = next(
            s for s in report.steps if s.name == "destination_lake_bronze"
        )
        assert bronze_step.errors == 2

    def test_transformation_step(
        self, mock_client: MagicMock, local_catalog: Path
    ) -> None:
        """Declared transformations run between normalize and validate."""
        spec = _spec(
            transformations=[{"name": "filter_by_min_value", "params": {"min_value": 20000}}]
        )

        report = run_pipeline(spec, RUN_DATE)

        transform = next(s for s in report.steps if s.name == "transform_filter_by_min_value")
        assert transform.rows_in == 2
        assert transform.rows_out == 1  # the 15000 PNCP mock is dropped
        silver_rows = lake.read_silver_contracts()
        assert len(silver_rows) == 1
        assert report.validation is not None
        assert report.validation["total"] == 1

    def test_per_source_window(
        self, mock_client: MagicMock, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A source window overrides the pipeline default window."""
        seen: dict[str, Any] = {}

        def spy_fetch(start: date | None, end: date | None, **_: Any) -> list[dict[str, Any]]:
            seen["range"] = (start, end)
            return []

        monkeypatch.setitem(SOURCE_REGISTRY, "spy", SourceDef(fetch=spy_fetch))
        monkeypatch.setitem(NORMALIZER_REGISTRY, "spy", lambda raw: None)  # type: ignore[return-value]

        spec = _spec(sources=[{"name": "spy", "window": "current_month"}])

        report = run_pipeline(spec, RUN_DATE)

        assert report.success is True
        assert seen["range"] == (date(2026, 1, 1), date(2026, 2, 1))

    def test_window_override(
        self, mock_client: MagicMock, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An explicit window override wins over the declared windows."""
        seen: dict[str, Any] = {}

        def spy_fetch(start: date | None, end: date | None, **_: Any) -> list[dict[str, Any]]:
            seen["range"] = (start, end)
            return []

        monkeypatch.setitem(SOURCE_REGISTRY, "spy", SourceDef(fetch=spy_fetch))
        monkeypatch.setitem(NORMALIZER_REGISTRY, "spy", lambda raw: None)  # type: ignore[return-value]

        spec = _spec(sources=[{"name": "spy", "window": "current_month"}])
        override = DateRange(start=date(2026, 3, 1), end=date(2026, 3, 31))

        run_pipeline(spec, RUN_DATE, window_override=override)

        assert seen["range"] == (date(2026, 3, 1), date(2026, 3, 31))

    def test_source_failure_fails_run(
        self, mock_client: MagicMock, local_catalog: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failing source raises PipelineRunError carrying the report."""

        def broken_fetch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            raise ConnectionError("source down")

        monkeypatch.setitem(SOURCE_REGISTRY, "broken", SourceDef(fetch=broken_fetch))
        spec = _spec(sources=["broken"])

        with pytest.raises(PipelineRunError, match="source down") as exc_info:
            run_pipeline(spec, RUN_DATE)

        report = exc_info.value.report
        assert report.success is False
        assert report.steps[0].name == "crawl_broken"
        assert report.steps[0].error == "source down"


def _fake_zip(path: Path) -> Path:
    """Writes a minimal valid ZIP file."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("data.csv", "a;b")
    path.write_bytes(buffer.getvalue())
    return path


class TestFileDumpFormula:
    """Tests of the file_dump formula (Federal Revenue dump)."""

    def _spec(self) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_dump",
                "window": "previous_month",
                "sources": ["federal_revenue"],
                "formula": "file_dump",
                "destinations": ["lake_bronze"],
            }
        )

    def test_download_and_manifest(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Downloaded files land in bronze and are recorded in the manifest."""

        def fake_download(destination: Path, *_args: Any, **_kwargs: Any) -> list[Path]:
            return [_fake_zip(destination / "Cnaes.zip")]

        monkeypatch.setitem(
            SOURCE_REGISTRY, "federal_revenue", SourceDef(download=fake_download)
        )

        report = run_pipeline(self._spec(), date(2026, 2, 2))

        assert report.success is True
        assert report.outputs["federal_revenue_reference_month"] == "2026-01"
        assert report.outputs["federal_revenue_files"] == 1

        step = report.steps[0]
        assert step.name == "download_federal_revenue"
        assert step.rows_out == 1

        # The ZIP object landed in the bronze bucket with sha256 metadata
        file_puts = [
            c for c in mock_client.put_object.mock_calls if "files/" in c.args[1]
        ]
        assert len(file_puts) == 1
        assert file_puts[0].args[1].endswith("federal_revenue/files/dt=2026-02-02/Cnaes.zip")

        # The manifest payload went to the bronze audit copy + raw table
        manifest = report.steps[-1]
        assert manifest.name == "destination_lake_bronze"
        assert manifest.errors == 0
        catalog = lake.get_catalog("bronze")
        table = catalog.load_table("capiba.raw_federal_revenue")
        rows = table.scan().to_pandas().to_dict("records")
        assert len(rows) == 1
        assert "2026-01" in rows[0]["payload_json"]

    def test_empty_manifest_fails_run(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty download fails the run loudly (no silent success)."""
        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(download=lambda *_a, **_k: []),
        )

        with pytest.raises(PipelineRunError, match="No files downloaded"):
            run_pipeline(self._spec(), date(2026, 2, 2))

    def test_file_dump_requires_bounded_window(self) -> None:
        """The all window is invalid for dump sources."""
        spec = PipelineSpec.model_validate(
            {
                "name": "test_dump_all",
                "window": "all",
                "sources": ["federal_revenue"],
                "formula": "file_dump",
                "destinations": ["lake_bronze"],
            }
        )

        with pytest.raises(PipelineRunError, match="month-bounded window"):
            run_pipeline(spec, RUN_DATE)


class TestMetricsCollectFormula:
    """Tests of the metrics_collect formula (pod usage snapshots)."""

    def _spec(self) -> PipelineSpec:
        return PipelineSpec.model_validate(
            {
                "name": "test_metrics",
                "window": "all",
                "sources": ["pod_usage"],
                "formula": "metrics_collect",
                "destinations": ["lake_bronze"],
            }
        )

    def test_collect_to_bronze(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The snapshot flows to the bronze raw_pod_usage table."""
        snapshot = [
            {
                "pod": "capiba-api-abc",
                "container": "api",
                "cpu_millicores": 42,
                "memory_bytes": 100 * 1024**2,
                "collected_at": "2026-01-15T12:07:00+00:00",
            }
        ]

        def fake_fetch(*_args: Any, **_kwargs: Any) -> list[dict[str, Any]]:
            return snapshot

        monkeypatch.setitem(SOURCE_REGISTRY, "pod_usage", SourceDef(fetch=fake_fetch))

        report = run_pipeline(self._spec(), RUN_DATE)

        assert report.success is True
        assert [s.name for s in report.steps] == [
            "collect_pod_usage",
            "destination_lake_bronze",
        ]
        assert report.steps[0].rows_out == 1

        catalog = lake.get_catalog("bronze")
        table = catalog.load_table("capiba.raw_pod_usage")
        rows = table.scan().to_pandas().to_dict("records")
        assert len(rows) == 1
        payload = json.loads(rows[0]["payload_json"])
        assert payload[0]["pod"] == "capiba-api-abc"
        assert payload[0]["cpu_millicores"] == 42

    def test_empty_snapshot_is_a_successful_run(
        self,
        mock_client: MagicMock,
        local_catalog: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An empty snapshot (graceful degradation) is not a run failure."""
        monkeypatch.setitem(
            SOURCE_REGISTRY, "pod_usage", SourceDef(fetch=lambda *_a, **_k: [])
        )

        report = run_pipeline(self._spec(), RUN_DATE)

        assert report.success is True
        assert report.steps[0].rows_out == 0


class TestTaskRunPipeline:
    """Tests for the Airflow wrapper task_run_pipeline."""

    def test_runs_spec_and_post_steps(
        self, mock_client: MagicMock, local_catalog: Path, tmp_path: Path
    ) -> None:
        """The wrapper resolves the run date, runs the spec and post steps."""
        from capiba.pipeline.tasks import task_run_pipeline

        spec_path = tmp_path / "spec.yaml"
        spec_path.write_text(
            """\
name: wrapped
window: previous_day
sources: [mock_pncp]
formula: contracts_default
destinations: [lake_silver]
post_steps: [dbt_run, detect]
""",
            encoding="utf-8",
        )

        with (
            patch("capiba.pipeline.tasks.task_dbt_run") as mock_dbt,
            patch("capiba.pipeline.tasks.task_detect") as mock_detect,
        ):
            mock_dbt.return_value = {"dbt": "run"}
            mock_detect.return_value = {"signals": 0}
            summary = task_run_pipeline(str(spec_path), ds="2026-01-15")

        assert summary["pipeline"] == "wrapped"
        assert summary["success"] is True
        assert summary["post_steps"] == {"dbt_run": {"dbt": "run"}, "detect": {"signals": 0}}
        # The run used the resolved previous-day window against the silver table
        silver_rows = lake.read_silver_contracts()
        assert len(silver_rows) == 1
        assert str(silver_rows[0]["dt"]) == "2026-01-15"
