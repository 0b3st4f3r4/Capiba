"""BDD step definitions for the declarative pipeline framework.

Feature file: tests/bdd/features/pipeline_framework.feature
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
from capiba.pipeline.spec import SpecError, load_spec

scenarios("features/pipeline_framework.feature")


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (spec path, report, load error)."""
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
name: bdd_pipeline
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


@given(parsers.parse('a YAML spec declaring the source "{source}"'))
def unknown_source_spec(context: dict[str, Any], tmp_path: Path, source: str) -> None:
    path = tmp_path / "bad_pipeline.yaml"
    path.write_text(
        f"""\
name: bdd_bad_pipeline
sources:
  - {source}
formula: contracts_default
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


@when("the spec is loaded")
def load_declared_spec(context: dict[str, Any]) -> None:
    try:
        context["spec"] = load_spec(context["spec_path"])
    except SpecError as exc:
        context["error"] = exc


@then("the run report is successful")
def report_successful(context: dict[str, Any]) -> None:
    assert context["report"].success is True


@then("the report records the crawl, normalize and validate steps")
def report_steps(context: dict[str, Any]) -> None:
    names = [s.name for s in context["report"].steps]
    assert "crawl_mock_pncp" in names
    assert "crawl_mock_transparency" in names
    assert "normalize" in names
    assert "validate" in names


@then("the normalized contracts reach the silver layer")
def contracts_in_silver(context: dict[str, Any]) -> None:
    rows = lake.read_silver_contracts()
    assert len(rows) == 2


@then(parsers.parse('the error states that the source "{source}" is unknown'))
def error_mentions_source(context: dict[str, Any], source: str) -> None:
    assert "error" in context
    assert f"unknown source '{source}'" in str(context["error"])
