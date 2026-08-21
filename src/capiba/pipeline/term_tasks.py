"""Airflow task wrappers for the terms_collect formula.

Responsibility: granular per-source tasks for the contract-terms pipelines
(``persist_<source>_terms``), mirroring the runner's ``terms_collect``
steps in the Airflow context (XCom, run date). Kept in a dedicated module
so the declarative terms flow stays cohesive; bronze writes are
best-effort like the other pipeline tasks.

Dependencies: capiba.pipeline.spec/tasks.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from capiba.pipeline.tasks import _lake_run_date, persist_contract_terms

logger = logging.getLogger(__name__)


def task_persist_contract_terms(
    source_name: str, spec_path: str, **context: Any
) -> dict[str, Any]:
    """Task: fetch and persist the registered terms of each cohort contract.

    Pulls ``raw_<source_name>`` (the enumerated cohort) from the upstream
    crawl task and runs the shared ``persist_contract_terms`` core
    (deterministic per-contract checkpoint names; contracts already
    checkpointed in the bronze layer for the run date are skipped, so a
    retried task resumes where it stopped and never duplicates). The
    enriched records (with ``terms_bronze_file``) are pushed back under
    ``raw_<source_name>`` for the destination tasks.

    Args:
        source_name: Name of the terms source (``pncp_contract_terms``).
        spec_path: Path of the YAML pipeline spec.
        context: Airflow context.

    Returns:
        Summary (terms fetched/skipped, errors).
    """
    run_date = _lake_run_date(context) or date.today()
    ti = context["ti"]

    records = (
        ti.xcom_pull(task_ids=f"crawl_{source_name}", key=f"raw_{source_name}") or []
    )
    summary = persist_contract_terms(source_name, records, run_date=run_date)

    ti.xcom_push(key=f"raw_{source_name}", value=records)
    ti.xcom_push(key=f"terms_{source_name}", value=summary)
    logger.info("Contract terms persisted for %s: %s", source_name, summary)
    return summary
