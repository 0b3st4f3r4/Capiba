"""BDD step definitions for the platform metrics publication.

Feature file: tests/bdd/features/platform_metrics.feature
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.pipeline import lake
from capiba.pipeline.runner import run_pipeline
from capiba.pipeline.spec import load_spec

scenarios("features/platform_metrics.feature")


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (spec path, report)."""
    return {}


@pytest.fixture
def local_catalog(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Points the lake to a SQLite catalog with a local warehouse."""
    monkeypatch.setattr(lake, "ICEBERG_CATALOG_URI", f"sqlite:///{tmp_path}/catalog.db")
    monkeypatch.setattr(lake, "ICEBERG_LOCAL_WAREHOUSE", str(tmp_path / "warehouse"))
    lake._catalogs.clear()
    yield
    lake._catalogs.clear()


@given("a YAML spec declaring a pipeline with mock sources")
def mock_pipeline_spec(context: dict[str, Any], tmp_path: Path) -> None:
    path = tmp_path / "pipeline.yaml"
    path.write_text(
        """\
name: bdd_metrics
window: previous_day
sources:
  - mock_pncp
  - mock_transparency
formula: contracts_default
validate:
  ruleset: contract_rules
destinations:
  - lake_silver
""",
        encoding="utf-8",
    )
    context["spec_path"] = path


@when(parsers.parse('the pipeline runs for the date "{day}"'))
def run_declared_pipeline(
    context: dict[str, Any], day: str, local_catalog: None
) -> None:
    spec = load_spec(context["spec_path"])
    context["report"] = run_pipeline(spec, date.fromisoformat(day))


@then("the gold platform_metrics table has one row per step of the run")
def metrics_rows_match_steps(context: dict[str, Any]) -> None:
    table = lake.get_catalog(lake.ICEBERG_WAREHOUSE_GOLD).load_table(
        "capiba.platform_metrics"
    )
    rows = table.scan().to_pandas().to_dict("records")
    context["metrics_rows"] = rows
    report_steps = {step.name for step in context["report"].steps}
    assert {row["step"] for row in rows} == report_steps


@then("each metrics row records the pipeline name, duration and row counts")
def metrics_rows_content(context: dict[str, Any]) -> None:
    for row in context["metrics_rows"]:
        assert row["pipeline"] == "bdd_metrics"
        assert row["duration_s"] >= 0
        assert row["dt"] == "2026-01-15"
    by_step = {row["step"]: row for row in context["metrics_rows"]}
    assert by_step["crawl_mock_pncp"]["rows_out"] == 1
    assert by_step["normalize"]["rows_in"] == 2
    assert by_step["normalize"]["rows_out"] == 2
