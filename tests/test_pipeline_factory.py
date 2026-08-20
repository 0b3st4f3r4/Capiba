"""Tests for the declarative DAG factory.

Responsibility: Validate that YAML specs in a pipelines directory become
Airflow DAGs with one task per pipeline step, and that invalid specs are
skipped without breaking the others — without needing a cluster.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

DAGS_DIR = Path(__file__).resolve().parent.parent / "dags"

VALID_SPEC = """\
name: factory_test
schedule: "0 6 * * *"
window: previous_day
sources:
  - name: pncp
formula: contracts_default
validate:
  ruleset: contract_rules
destinations:
  - lake_bronze
  - lake_silver
  - arangodb_graph
  - gold_report
post_steps:
  - dbt_run
  - detect
"""

DUMP_SPEC = """\
name: factory_dump
schedule: "23 5 2 * *"
window: previous_month
sources:
  - name: federal_revenue
formula: file_dump
destinations:
  - lake_bronze
"""

DUMP_GRAPH_SPEC = """\
name: factory_dump_graph
schedule: "23 5 2 * *"
window: previous_month
sources:
  - name: federal_revenue
formula: file_dump
destinations:
  - lake_bronze
  - lake_silver
  - arangodb_graph
"""

DUMP_TSE_SPEC = """\
name: factory_dump_tse
schedule: "37 6 3 * *"
window: previous_month
sources:
  - name: tse
    params:
      year: 2024
formula: file_dump
destinations:
  - lake_bronze
  - lake_silver
"""

MANUAL_SPEC = """\
name: factory_manual
sources:
  - name: mock_pncp
formula: contracts_default
destinations:
  - lake_silver
"""


def _load_factory():
    """Loads dags/pipeline_factory.py as a fresh module."""
    spec = importlib.util.spec_from_file_location(
        "pipeline_factory_under_test", DAGS_DIR / "pipeline_factory.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@pytest.fixture(scope="module")
def factory():
    """The real pipeline_factory module, loaded once."""
    return _load_factory()


class TestBuildDags:
    """Tests for build_dags over temporary pipelines directories."""

    def test_generates_dag_from_yaml(self, factory, tmp_path: Path) -> None:
        """A valid spec becomes a DAG with Airflow-native tasks per step."""
        (tmp_path / "factory_test.yaml").write_text(VALID_SPEC, encoding="utf-8")

        dags = factory.build_dags(tmp_path)

        assert set(dags) == {"factory_test"}
        dag = dags["factory_test"]
        assert dag.dag_id == "factory_test"
        assert dag.schedule == "0 6 * * *"
        assert dag.catchup is False
        # Overlapping runs race the non-atomic silver upsert (DELETE+append)
        # and duplicate rows; runs of the same pipeline must be serialized.
        assert dag.max_active_runs == 1

        # The single-task simplification must never return.
        assert "run" not in {t.task_id for t in dag.tasks}

        expected_tasks = {
            "crawl_pncp",
            "normalize",
            "validate",
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_arangodb_graph",
            "destination_gold_report",
            "dbt_run",
            "detect",
        }
        assert {t.task_id for t in dag.tasks} == expected_tasks

        # Dependencies: crawls -> normalize -> validate -> destinations -> post -> gold_report
        assert dag.get_task("crawl_pncp").downstream_task_ids == {"normalize"}
        assert dag.get_task("normalize").downstream_task_ids == {"validate"}
        assert dag.get_task("validate").downstream_task_ids == {
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_arangodb_graph",
        }
        assert dag.get_task("destination_lake_bronze").downstream_task_ids == {
            "dbt_run",
            "detect",
        }
        assert dag.get_task("dbt_run").downstream_task_ids == {"destination_gold_report"}

        crawl_task = dag.get_task("crawl_pncp")
        inlet_uris = {a.uri for a in crawl_task.inlets}
        assert inlet_uris == {"capiba://source/pncp"}
        outlet_uris = {a.uri for a in crawl_task.outlets}
        assert "capiba://bronze/raw_pncp" in outlet_uris
        assert "capiba://silver/contracts" in outlet_uris
        assert "capiba://arangodb/contracts" in outlet_uris
        assert "capiba://gold/reports/factory_test" in outlet_uris
        # post steps contribute the dbt marts and fraud signals assets
        assert "capiba://gold/contracts_daily" in outlet_uris
        assert "capiba://gold/pod_usage_hourly" in outlet_uris
        assert "capiba://gold/platform_cost_daily" in outlet_uris
        assert "capiba://dwh/serving_supplier_stats" in outlet_uris
        assert "capiba://dwh/serving_municipality_daily" in outlet_uris
        assert "capiba://gold/fraud_signals" in outlet_uris

    def test_dump_pipeline_assets(self, factory, tmp_path: Path) -> None:
        """A file_dump spec exposes the bronze files asset."""
        (tmp_path / "dump.yaml").write_text(DUMP_SPEC, encoding="utf-8")

        dags = factory.build_dags(tmp_path)
        dag = dags["factory_dump"]
        assert {t.task_id for t in dag.tasks} == {
            "download_federal_revenue",
            "destination_lake_bronze",
        }
        outlet_uris = {a.uri for a in dag.get_task("download_federal_revenue").outlets}
        assert "capiba://bronze/raw_federal_revenue" in outlet_uris
        assert "capiba://bronze/federal_revenue/files" in outlet_uris

    def test_dump_pipeline_with_graph_destinations(
        self, factory, tmp_path: Path
    ) -> None:
        """A file_dump spec with silver/graph destinations gets a normalize task."""
        (tmp_path / "dump_graph.yaml").write_text(DUMP_GRAPH_SPEC, encoding="utf-8")

        dags = factory.build_dags(tmp_path)
        dag = dags["factory_dump_graph"]

        assert {t.task_id for t in dag.tasks} == {
            "download_federal_revenue",
            "normalize_federal_revenue",
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_arangodb_graph",
        }
        assert dag.get_task("download_federal_revenue").downstream_task_ids == {
            "normalize_federal_revenue"
        }
        assert dag.get_task("normalize_federal_revenue").downstream_task_ids == {
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_arangodb_graph",
        }

        outlet_uris = {
            a.uri for a in dag.get_task("normalize_federal_revenue").outlets
        }
        assert "capiba://silver/companies" in outlet_uris
        assert "capiba://silver/establishments" in outlet_uris
        assert "capiba://silver/partners" in outlet_uris
        assert "capiba://arangodb/companies" in outlet_uris
        assert "capiba://arangodb/partners" in outlet_uris
        assert "capiba://silver/contracts" not in outlet_uris

    def test_dump_tse_pipeline_assets(self, factory, tmp_path: Path) -> None:
        """A TSE file_dump spec gets a normalize task and its own silver asset."""
        (tmp_path / "dump_tse.yaml").write_text(DUMP_TSE_SPEC, encoding="utf-8")

        dags = factory.build_dags(tmp_path)
        dag = dags["factory_dump_tse"]

        assert {t.task_id for t in dag.tasks} == {
            "download_tse",
            "normalize_tse",
            "destination_lake_bronze",
            "destination_lake_silver",
        }
        assert dag.get_task("download_tse").downstream_task_ids == {"normalize_tse"}

        download = dag.get_task("download_tse")
        assert {a.uri for a in download.inlets} == {"capiba://source/tse"}
        outlet_uris = {a.uri for a in download.outlets}
        assert "capiba://bronze/raw_tse" in outlet_uris
        assert "capiba://silver/campaign_donations" in outlet_uris
        assert "capiba://silver/candidacies" in outlet_uris
        # The CNPJ entity assets belong to the federal_revenue dump only.
        assert "capiba://silver/companies" not in outlet_uris
        assert "capiba://silver/contracts" not in outlet_uris

    def test_unscheduled_pipeline(self, factory, tmp_path: Path) -> None:
        """A spec without schedule yields a manually triggered DAG."""
        (tmp_path / "manual.yaml").write_text(MANUAL_SPEC, encoding="utf-8")

        dags = factory.build_dags(tmp_path)

        assert dags["factory_manual"].schedule is None
        assert {t.task_id for t in dags["factory_manual"].tasks} == {
            "crawl_mock_pncp",
            "normalize",
            "destination_lake_silver",
        }

    def test_invalid_yaml_is_skipped(
        self, factory, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A broken spec is logged and skipped; valid ones still load."""
        (tmp_path / "broken.yaml").write_text("name: [unclosed", encoding="utf-8")
        (tmp_path / "bad_source.yaml").write_text(
            "name: bad\nsources: [nope]\nformula: contracts_default\n"
            "destinations: [lake_silver]\n",
            encoding="utf-8",
        )
        (tmp_path / "ok.yaml").write_text(MANUAL_SPEC, encoding="utf-8")

        with caplog.at_level("ERROR"):
            dags = factory.build_dags(tmp_path)

        assert set(dags) == {"factory_manual"}
        assert caplog.text.count("Skipping invalid pipeline spec") == 2

    def test_empty_directory(self, factory, tmp_path: Path) -> None:
        """No YAML files means no DAGs (and no error)."""
        assert factory.build_dags(tmp_path) == {}


class TestRealPipelines:
    """The shipped dags/pipelines specs must produce the expected DAGs."""

    def test_module_registers_dags_in_globals(self, factory) -> None:
        """Module parse registers one global DAG per valid spec."""
        daily = factory.daily_pncp
        transparency = factory.monthly_transparency
        monthly = factory.monthly_federal_revenue

        assert daily.schedule == "0 6 * * *"
        assert transparency.schedule == "0 7 2 * *"
        assert monthly.schedule == "23 5 2 * *"
        crawl_pncp = daily.get_task("crawl_pncp")
        assert crawl_pncp.python_callable.keywords["spec_path"].endswith(
            "dags/pipelines/daily_pncp.yaml"
        )

    def test_daily_ingestion_is_gone(self, factory) -> None:
        """The merged daily_contracts spec no longer generates a DAG."""
        assert not hasattr(factory, "daily_ingestion")

    def test_per_source_dags_have_no_post_steps(self, factory) -> None:
        """Ingestion is split per source; dbt/detect live in gold_detection."""
        daily = factory.daily_pncp
        assert {t.task_id for t in daily.tasks} == {
            "crawl_pncp",
            "normalize",
            "validate",
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_arangodb_graph",
            "destination_gold_report",
        }

        transparency = factory.monthly_transparency
        assert {t.task_id for t in transparency.tasks} == {
            "crawl_transparency",
            "normalize",
            "validate",
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_arangodb_graph",
            "destination_gold_report",
        }

    def test_real_monthly_tse_dag(self, factory) -> None:
        """The shipped monthly_tse spec produces a granular dump DAG (O8)."""
        dag = factory.monthly_tse

        assert dag.schedule == "37 6 3 * *"
        assert {t.task_id for t in dag.tasks} == {
            "download_tse",
            "normalize_tse",
            "destination_lake_bronze",
            "destination_lake_silver",
        }

    def test_dagbag_parses_without_import_errors(self, factory) -> None:
        """The whole dags/ folder parses cleanly through the DagBag."""
        from airflow.dag_processing.dagbag import DagBag

        bag = DagBag(dag_folder=str(DAGS_DIR))

        assert bag.import_errors == {}
        assert {
            "daily_pncp",
            "monthly_transparency",
            "monthly_federal_revenue",
            "lake_maintenance",
            "gold_detection",
        } <= set(bag.dag_ids)
        assert "daily_ingestion" not in bag.dag_ids

    def test_hourly_pod_usage_refreshes_marts(self, factory) -> None:
        """The hourly pod usage pipeline refreshes the usage marts (dbt_run)."""
        dag = factory.hourly_pod_usage

        assert {t.task_id for t in dag.tasks} == {
            "crawl_pod_usage",
            "destination_lake_bronze",
            "dbt_run",
        }
        assert dag.get_task("destination_lake_bronze").downstream_task_ids == {
            "dbt_run"
        }

        crawl = dag.get_task("crawl_pod_usage")
        assert {a.uri for a in crawl.inlets} == {"capiba://source/pod_usage"}
        outlet_uris = {a.uri for a in crawl.outlets}
        assert "capiba://bronze/raw_pod_usage" in outlet_uris
        assert "capiba://gold/pod_usage_hourly" in outlet_uris
        assert "capiba://gold/platform_cost_daily" in outlet_uris

        # The dbt_run task is scoped to the usage marts (a full run rebuilds
        # the contract marts hourly and OOMKilled Trino on 2026-08-19).
        dbt_callable = dag.get_task("dbt_run").python_callable
        assert dbt_callable.keywords["select"] == [
            "pod_usage_hourly",
            "platform_cost_daily",
        ]

ENTITIES_SPEC = """\
name: factory_entities
schedule: "22 3 * * 2"
window: all
sources:
  - name: ceis
  - name: cnep
formula: entities_collect
destinations:
  - lake_bronze
  - lake_silver
  - gold_report
"""


class TestEntitiesCollectFactory:
    """Tests for the entities_collect formula in the DAG factory."""

    def test_generates_granular_tasks(
        self, factory, tmp_path: Path
    ) -> None:
        """An entities_collect spec gets crawl + normalize tasks per source."""
        (tmp_path / "entities.yaml").write_text(ENTITIES_SPEC, encoding="utf-8")

        dags = factory.build_dags(tmp_path)
        dag = dags["factory_entities"]

        # The single-task simplification must never return.
        assert "run" not in {t.task_id for t in dag.tasks}
        assert {t.task_id for t in dag.tasks} == {
            "crawl_ceis",
            "crawl_cnep",
            "normalize_ceis",
            "normalize_cnep",
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_gold_report",
        }

        # Dependencies: crawl_<source> -> normalize_<source> -> destinations
        assert dag.get_task("crawl_ceis").downstream_task_ids == {"normalize_ceis"}
        assert dag.get_task("crawl_cnep").downstream_task_ids == {"normalize_cnep"}
        assert dag.get_task("normalize_ceis").downstream_task_ids == {
            "destination_lake_bronze",
            "destination_lake_silver",
        }
        assert dag.get_task("normalize_cnep").downstream_task_ids == {
            "destination_lake_bronze",
            "destination_lake_silver",
        }
        assert dag.get_task("destination_lake_bronze").downstream_task_ids == {
            "destination_gold_report"
        }

        # Lineage assets: source inlets + bronze/sanctions/gold outlets
        crawl = dag.get_task("crawl_ceis")
        assert {a.uri for a in crawl.inlets} == {"capiba://source/ceis"}
        outlet_uris = {a.uri for a in crawl.outlets}
        assert "capiba://bronze/raw_ceis" in outlet_uris
        assert "capiba://bronze/raw_cnep" in outlet_uris
        assert "capiba://silver/sanctions" in outlet_uris
        assert "capiba://silver/contracts" not in outlet_uris
        assert "capiba://gold/reports/factory_entities" in outlet_uris

    def test_crawl_uses_checkpointed_entities_task(
        self, factory, tmp_path: Path
    ) -> None:
        """entities_collect crawls run the per-page checkpointed task."""
        from capiba.pipeline.entity_tasks import task_crawl_entities

        (tmp_path / "entities.yaml").write_text(ENTITIES_SPEC, encoding="utf-8")

        dag = factory.build_dags(tmp_path)["factory_entities"]

        for task_id in ("crawl_ceis", "crawl_cnep"):
            callable_ = dag.get_task(task_id).python_callable
            assert callable_.func is task_crawl_entities

    def test_real_weekly_sanctions_dag(self, factory) -> None:
        """The shipped weekly_sanctions spec produces a granular DAG."""
        dag = factory.weekly_sanctions

        assert dag.schedule == "22 3 * * 2"
        assert {t.task_id for t in dag.tasks} == {
            "crawl_ceis",
            "crawl_cnep",
            "crawl_ceaf",
            "normalize_ceis",
            "normalize_cnep",
            "normalize_ceaf",
            "destination_lake_bronze",
            "destination_lake_silver",
            "destination_gold_report",
        }


DOCUMENTS_SPEC = """\
name: factory_documents
schedule: "41 4 * * *"
window: previous_day
sources:
  - name: querido_diario
    params:
      territory_id: "2611606"
formula: documents_collect
validate:
  ruleset: gazette_rules
destinations:
  - lake_bronze
  - gold_report
"""


class TestDocumentsCollectFactory:
    """Tests for the documents_collect formula in the DAG factory (O7)."""

    def test_generates_granular_tasks(self, factory, tmp_path: Path) -> None:
        """A documents_collect spec gets crawl + download + validate tasks."""
        (tmp_path / "documents.yaml").write_text(DOCUMENTS_SPEC, encoding="utf-8")

        dags = factory.build_dags(tmp_path)
        dag = dags["factory_documents"]

        # The single-task simplification must never return.
        assert "run" not in {t.task_id for t in dag.tasks}
        assert {t.task_id for t in dag.tasks} == {
            "crawl_querido_diario",
            "download_querido_diario_texts",
            "validate",
            "destination_lake_bronze",
            "destination_gold_report",
        }

        # Dependencies: crawl -> download_texts -> validate -> destinations
        assert dag.get_task("crawl_querido_diario").downstream_task_ids == {
            "download_querido_diario_texts"
        }
        assert dag.get_task("download_querido_diario_texts").downstream_task_ids == {
            "validate"
        }
        assert dag.get_task("validate").downstream_task_ids == {
            "destination_lake_bronze"
        }
        assert dag.get_task("destination_lake_bronze").downstream_task_ids == {
            "destination_gold_report"
        }

        # Lineage assets: source inlet + bronze/gold outlets
        crawl = dag.get_task("crawl_querido_diario")
        assert {a.uri for a in crawl.inlets} == {"capiba://source/querido_diario"}
        outlet_uris = {a.uri for a in crawl.outlets}
        assert "capiba://bronze/raw_querido_diario" in outlet_uris
        assert "capiba://silver/contracts" not in outlet_uris
        assert "capiba://gold/reports/factory_documents" in outlet_uris

    def test_download_uses_the_document_texts_task(
        self, factory, tmp_path: Path
    ) -> None:
        """documents_collect downloads run the skip-existing texts task."""
        from capiba.pipeline.document_tasks import (
            task_download_document_texts,
            task_validate_documents,
        )

        (tmp_path / "documents.yaml").write_text(DOCUMENTS_SPEC, encoding="utf-8")

        dag = factory.build_dags(tmp_path)["factory_documents"]

        download = dag.get_task("download_querido_diario_texts").python_callable
        assert download.func is task_download_document_texts
        validate = dag.get_task("validate").python_callable
        assert validate.func is task_validate_documents

    def test_real_daily_querido_diario_dag(self, factory) -> None:
        """The shipped daily_querido_diario spec produces a granular DAG."""
        dag = factory.daily_querido_diario

        assert dag.schedule == "41 4 * * *"
        assert {t.task_id for t in dag.tasks} == {
            "crawl_querido_diario",
            "download_querido_diario_texts",
            "validate",
            "destination_lake_bronze",
            "destination_gold_report",
        }
