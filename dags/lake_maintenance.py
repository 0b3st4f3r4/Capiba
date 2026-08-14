"""Weekly Iceberg lake maintenance DAG.

Runs compaction and snapshot expiration on every Iceberg table of the
medallion catalogs (bronze/silver/gold) through Trino, keeping small files
and old snapshots from piling up in MinIO.

The inlets/outlets below feed OpenLineage (Marquez) with dataset-level
lineage for the maintenance operations.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

from airflow import DAG
from airflow.providers.standard.operators.python import PythonOperator
from airflow.sdk.definitions.asset import Asset

from capiba.config import (
    ICEBERG_WAREHOUSE_BRONZE,
    ICEBERG_WAREHOUSE_GOLD,
    ICEBERG_WAREHOUSE_SILVER,
    LAKE_BUCKET_BRONZE,
    LAKE_BUCKET_GOLD,
    LAKE_BUCKET_SILVER,
)
from capiba.pipeline import trino

logger = logging.getLogger(__name__)

DEFAULT_ARGS = {
    "owner": "capiba",
    "depends_on_past": False,
    "email_on_failure": False,
    "email_on_retry": False,
    "retries": 1,
    "retry_delay": timedelta(minutes=10),
    "execution_timeout": timedelta(hours=1),
}

# How long Iceberg snapshots are kept before expiration.
SNAPSHOT_RETENTION = "7d"

CATALOGS = [ICEBERG_WAREHOUSE_BRONZE, ICEBERG_WAREHOUSE_SILVER, ICEBERG_WAREHOUSE_GOLD]
BUCKET_BY_CATALOG = {
    ICEBERG_WAREHOUSE_BRONZE: LAKE_BUCKET_BRONZE,
    ICEBERG_WAREHOUSE_SILVER: LAKE_BUCKET_SILVER,
    ICEBERG_WAREHOUSE_GOLD: LAKE_BUCKET_GOLD,
}

LAKE_TABLES = [Asset(uri=f"s3://{BUCKET_BY_CATALOG[c]}") for c in CATALOGS]


def task_lake_maintenance(**context: Any) -> dict[str, Any]:
    """Task: expire old snapshots and compact every Iceberg table.

    Args:
        context: Airflow context.

    Returns:
        Summary with the maintained tables per catalog.
    """
    summary: dict[str, Any] = {}
    for catalog in CATALOGS:
        tables = trino.list_iceberg_tables(catalog)
        logger.info("Maintaining %s: %s", catalog, tables)
        for table in tables:
            trino.run_query(
                f"ALTER TABLE {catalog}.{table} EXECUTE"
                f" expire_snapshots(retention_threshold => '{SNAPSHOT_RETENTION}')"
            )
            trino.run_query(f"ALTER TABLE {catalog}.{table} EXECUTE optimize")
        summary[catalog] = tables
    return summary


with DAG(
    dag_id="lake_maintenance",
    default_args=DEFAULT_ARGS,
    description="Weekly Iceberg maintenance: snapshot expiration and compaction",
    schedule="17 4 * * 0",  # Sundays, 04:17 UTC
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["lake", "maintenance", "iceberg"],
) as dag:
    PythonOperator(
        task_id="maintain_iceberg_tables",
        python_callable=task_lake_maintenance,
        outlets=LAKE_TABLES,
    )
