"""Daily gold rebuild and fraud detection DAG.

Runs after the per-source ingestion pipelines (``daily_pncp`` at 06:00,
``monthly_transparency`` on day 2): rebuilds every gold mart with dbt and
recomputes the fraud signals over the *whole* accumulated silver contracts
table — both steps are global reprocesses, not increments of the day.
A final step exports the LGPD-cleared marts to the public bucket
(``capiba-public``, CSV/Parquet versioned by run date — see
``capiba.pipeline.public_export``).

This is also the "final run" after a backfill: post steps are skipped on
backfill runs (``task_post_step`` raises ``AirflowSkipException`` — see
``docs/ingestao.md``), so after backfilling the ingestion DAGs, trigger this
DAG once (or wait for the daily schedule) to rebuild marts and signals over
the accumulated data.

The inlets/outlets below feed OpenLineage (Marquez) with dataset-level
lineage, mirroring the assets the declarative factory derives for the
``dbt_run``/``detect`` post steps.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk.definitions.asset import Asset

from capiba.pipeline.public_export import task_export_public_marts
from capiba.pipeline.tasks import task_dbt_run, task_detect

# Lineage assets of the dbt marts and fraud signals — keep in sync with
# GOLD_MARTS/DWH_SERVING_MARTS/GOLD_FRAUD_SIGNALS in dags/pipeline_factory.py
# (DAG files do not import each other; the DagBag does not put the dags
# folder on sys.path).
SILVER_CONTRACTS = Asset(uri="capiba://silver/contracts")
GOLD_MARTS = [
    Asset(uri=f"capiba://gold/{mart}")
    for mart in (
        "contracts_by_agency",
        "contracts_daily",
        "data_quality_daily",
        "supplier_stats",
        "pod_usage_hourly",
        "platform_cost_daily",
    )
]
DWH_SERVING_MARTS = [
    Asset(uri=f"capiba://dwh/{mart}")
    for mart in (
        "serving_supplier_stats",
        "serving_municipality_daily",
    )
]
GOLD_FRAUD_SIGNALS = Asset(uri="capiba://gold/fraud_signals")
PUBLIC_MARTS_EXPORT = Asset(uri="capiba://public/marts")

# One-slot pool serializing the memory-heavy lake tasks — keep in sync with
# HEAVY_POOL in dags/pipeline_factory.py (DAG files do not import each
# other). The detect holds the whole silver contracts table in memory and
# shares one container with the ingestion tasks; concurrent peaks
# OOMKilled the pod on real volume (2026-08-21).
HEAVY_POOL = "heavy_lake"

DEFAULT_ARGS = {
    "owner": "capiba",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=5),
    "execution_timeout": timedelta(hours=1),
}

with DAG(
    dag_id="gold_detection",
    default_args=DEFAULT_ARGS,
    description="Daily gold marts rebuild (dbt) + fraud signal detection",
    schedule="0 8 * * *",  # 08:00 UTC, after the ingestion pipelines
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["gold", "detection", "dbt"],
) as dag:
    dbt_run = PythonOperator(
        task_id="dbt_run",
        python_callable=task_dbt_run,
        inlets=[SILVER_CONTRACTS],
        outlets=[*GOLD_MARTS, *DWH_SERVING_MARTS],
    )
    detect = PythonOperator(
        task_id="detect",
        python_callable=task_detect,
        pool=HEAVY_POOL,
        inlets=[SILVER_CONTRACTS],
        outlets=[GOLD_FRAUD_SIGNALS],
    )
    export_public = PythonOperator(
        task_id="export_public_marts",
        python_callable=task_export_public_marts,
        inlets=GOLD_MARTS,
        outlets=[PUBLIC_MARTS_EXPORT],
    )
    dbt_run >> detect
    dbt_run >> export_public
