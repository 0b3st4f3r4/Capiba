"""DAG factory for declarative ingestion pipelines (Airflow-native).

Reads every ``dags/pipelines/*.yaml`` spec (path relative to this file),
validates it through ``capiba.pipeline.spec.load_spec`` and materializes
one Airflow DAG per spec. Each logical step becomes an Airflow task so the
scheduler can retry failures independently:

- one ``crawl_<source>`` task per source
- ``normalize`` and ``validate`` for ``contracts_default`` formulas
- one ``destination_<name>`` task per destination
- ``dbt_run`` / ``detect`` for declared post steps

Invalid specs are logged and skipped so a single broken file does not take
down the rest of the DagBag.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from functools import partial
from pathlib import Path

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk.definitions.asset import Asset

from capiba.pipeline.openlineage import register_capiba_openlineage
from capiba.pipeline.spec import PipelineSpec, load_spec
from capiba.pipeline.tasks import (
    task_crawl_source,
    task_destination,
    task_download_source,
    task_gold_report,
    task_normalize_pipeline,
    task_post_step,
    task_validate_pipeline,
)

# Register the custom ``capiba://`` OpenLineage scheme so that all Capiba
# datasets are emitted under the single namespace ``capiba`` in Marquez.
register_capiba_openlineage()

logger = logging.getLogger(__name__)

PIPELINES_DIR = Path(__file__).resolve().parent / "pipelines"

DEFAULT_ARGS = {
    "owner": "capiba",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 2,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(hours=6),
}

# Public endpoints crawled per source (OpenLineage inlets).
SOURCE_INLETS = {
    "pncp": Asset(uri="capiba://source/pncp"),
    "transparency": Asset(uri="capiba://source/transparency"),
    "federal_revenue": Asset(uri="capiba://source/federal_revenue"),
}

SILVER_CONTRACTS = Asset(uri="capiba://silver/contracts")
ARANGO_CONTRACTS = Asset(uri="capiba://arangodb/contracts")
GOLD_FRAUD_SIGNALS = Asset(uri="capiba://gold/fraud_signals")
GOLD_MARTS = [
    Asset(uri=f"capiba://gold/{mart}")
    for mart in (
        "contracts_by_agency",
        "contracts_daily",
        "data_quality_daily",
        "supplier_stats",
    )
]


def _inlets(spec: PipelineSpec) -> list[Asset]:
    """Public API assets consumed by the pipeline's sources."""
    return [SOURCE_INLETS[s.name] for s in spec.sources if s.name in SOURCE_INLETS]


def _outlets(spec: PipelineSpec) -> list[Asset]:
    """Lake/graph assets produced by the pipeline's destinations/post steps."""
    outlets: list[Asset] = []
    destination_names = {d.name for d in spec.destinations}
    if "lake_bronze" in destination_names:
        for source in spec.sources:
            outlets.append(Asset(uri=f"capiba://bronze/raw_{source.name}"))
            if source.name == "federal_revenue":
                outlets.append(Asset(uri="capiba://bronze/federal_revenue/files"))
    if "lake_silver" in destination_names:
        outlets.append(SILVER_CONTRACTS)
    if "arangodb_graph" in destination_names:
        outlets.append(ARANGO_CONTRACTS)
    if "gold_report" in destination_names:
        outlets.append(Asset(uri=f"capiba://gold/reports/{spec.name}"))
    if "dbt_run" in spec.post_steps:
        outlets.extend(GOLD_MARTS)
    if "detect" in spec.post_steps:
        outlets.append(GOLD_FRAUD_SIGNALS)
    return outlets


def _crawl_task_id(source_name: str) -> str:
    return f"crawl_{source_name}"


def _download_task_id(source_name: str) -> str:
    return f"download_{source_name}"


def _destination_task_id(destination_name: str) -> str:
    return f"destination_{destination_name}"


def build_dag(spec: PipelineSpec, spec_path: Path) -> DAG:
    """Builds an Airflow DAG from a declarative pipeline spec.

    The resulting DAG has one task per logical step, chained according to the
    spec formula so the Airflow scheduler can retry individual failures.
    """
    dag = DAG(
        dag_id=spec.name,
        default_args=DEFAULT_ARGS,
        description=spec.description or f"Declarative pipeline {spec.name}",
        schedule=spec.schedule,
        start_date=datetime(2026, 1, 1),
        catchup=False,
        tags=["ingestion", "declarative"],
    )

    shared_outlets = _outlets(spec)

    with dag:
        # --- Source step(s) --------------------------------------------------
        source_tasks: list[PythonOperator] = []
        for source in spec.sources:
            if spec.formula == "file_dump":
                task_id = _download_task_id(source.name)
                python_callable = partial(
                    task_download_source,
                    source_name=source.name,
                    spec_path=str(spec_path),
                )
            else:
                task_id = _crawl_task_id(source.name)
                python_callable = partial(
                    task_crawl_source,
                    source_name=source.name,
                    spec_path=str(spec_path),
                )

            source_tasks.append(
                PythonOperator(
                    task_id=task_id,
                    python_callable=python_callable,
                    inlets=[SOURCE_INLETS[source.name]]
                    if source.name in SOURCE_INLETS
                    else [],
                    outlets=shared_outlets,
                )
            )

        # --- Formula-specific intermediate steps -----------------------------
        intermediate_tail: list[PythonOperator] = list(source_tasks)

        if spec.formula == "contracts_default":
            normalize = PythonOperator(
                task_id="normalize",
                python_callable=partial(
                    task_normalize_pipeline, spec_path=str(spec_path)
                ),
                outlets=shared_outlets,
            )
            for src_task in source_tasks:
                src_task >> normalize
            intermediate_tail = [normalize]

            if spec.validation:
                validate = PythonOperator(
                    task_id="validate",
                    python_callable=partial(
                        task_validate_pipeline, spec_path=str(spec_path)
                    ),
                    outlets=shared_outlets,
                )
                normalize >> validate
                intermediate_tail = [validate]

        # --- Destinations ----------------------------------------------------
        destination_tasks: list[PythonOperator] = []
        for dest in spec.destinations:
            if dest.name == "gold_report":
                continue
            destination_tasks.append(
                PythonOperator(
                    task_id=_destination_task_id(dest.name),
                    python_callable=partial(
                        task_destination,
                        destination_name=dest.name,
                        spec_path=str(spec_path),
                    ),
                    outlets=shared_outlets,
                )
            )

        if destination_tasks:
            for up in intermediate_tail:
                for down in destination_tasks:
                    up >> down
            intermediate_tail = destination_tasks

        # --- Post steps ------------------------------------------------------
        post_tasks: list[PythonOperator] = []
        for step in spec.post_steps:
            post_tasks.append(
                PythonOperator(
                    task_id=step,
                    python_callable=partial(
                        task_post_step,
                        step_name=step,
                        spec_path=str(spec_path),
                    ),
                    outlets=shared_outlets,
                )
            )

        if post_tasks:
            for up in intermediate_tail:
                for down in post_tasks:
                    up >> down
            intermediate_tail = post_tasks

        # --- Gold report (runs after everything else) ------------------------
        if any(d.name == "gold_report" for d in spec.destinations):
            gold_report = PythonOperator(
                task_id="destination_gold_report",
                python_callable=partial(
                    task_gold_report, spec_path=str(spec_path)
                ),
                outlets=shared_outlets,
            )
            for up in intermediate_tail:
                up >> gold_report

    return dag


def build_dags(pipelines_dir: Path = PIPELINES_DIR) -> dict[str, DAG]:
    """Builds one DAG per valid YAML spec in ``pipelines_dir``.

    Invalid specs are logged and skipped so a single broken file does not
    break the DagBag parse.
    """
    dags: dict[str, DAG] = {}
    for spec_path in sorted(pipelines_dir.glob("*.yaml")):
        try:
            spec = load_spec(spec_path)
        except Exception:
            logger.exception("Skipping invalid pipeline spec: %s", spec_path)
            continue
        dags[spec.name] = build_dag(spec, spec_path)
    return dags


for _dag_name, _dag in build_dags().items():
    globals()[_dag_name] = _dag
