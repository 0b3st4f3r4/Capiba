"""Airflow task wrappers for the entities_collect formula.

Responsibility: granular per-source tasks for the entity pipelines
(``normalize_<source>`` and the silver destination summary), mirroring the
runner's ``entities_collect`` steps in the Airflow context (XCom, run
date). Kept in a dedicated module so the declarative entity flow stays
cohesive; silver writes are best-effort like the other pipeline tasks.

Dependencies: capiba.pipeline.registry/lake/spec/tasks.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from capiba.pipeline import lake
from capiba.pipeline.registry import ENTITY_NORMALIZER_REGISTRY, SOURCE_REGISTRY
from capiba.pipeline.spec import load_spec
from capiba.pipeline.tasks import _lake_run_date

logger = logging.getLogger(__name__)


def task_crawl_entities(
    source_name: str, spec_path: str, **context: Any
) -> list[dict[str, Any]]:
    """Task: crawl an entity source with per-page bronze checkpoints.

    Same contract as ``task_crawl_source`` (raw records pushed to XCom under
    ``raw_<source_name>``, plus the bronze audit copy and raw table), but
    each fetched page is persisted to the bronze layer as it lands
    (``lake.write_bronze_page``). On a retry, the pages already persisted
    for the run date are read back and the walk resumes from the next
    unpersisted page — a killed/interrupted crawl no longer restarts a
    700+ page walk from scratch (observed in the 2026-08 weekly_sanctions
    run killed by a liveness restart mid-crawl).

    Checkpoint reads/writes are best-effort: failures log a warning and the
    crawl falls back to the non-incremental behavior.

    Args:
        source_name: Name of the entity source to crawl.
        spec_path: Path of the YAML pipeline spec.
        context: Airflow context.

    Returns:
        List of raw records (checkpointed pages + newly fetched).
    """
    run_date = _lake_run_date(context) or date.today()
    spec = load_spec(spec_path)
    source = next((s for s in spec.sources if s.name == source_name), None)
    if source is None:
        raise ValueError(f"Source '{source_name}' not found in spec '{spec_path}'")

    fetch = SOURCE_REGISTRY[source_name].fetch
    if fetch is None:
        raise ValueError(f"Source '{source_name}' has no record fetcher")

    try:
        persisted = lake.list_bronze_pages(source_name, run_date)
        prior_records = [
            record
            for page in sorted(persisted)
            for record in lake.read_bronze_page(persisted[page])
        ]
    except Exception as exc:
        logger.warning(
            "Failed to read %s page checkpoints; restarting the walk: %s",
            source_name,
            exc,
        )
        persisted, prior_records = {}, []

    start_page = max(persisted, default=0) + 1
    if prior_records:
        logger.info(
            "Resuming source '%s' from page %d (%d checkpointed records)",
            source_name,
            start_page,
            len(prior_records),
        )

    new_records: list[dict[str, Any]] = []

    def _persist_page(page: int, records: list[dict[str, Any]]) -> None:
        new_records.extend(records)
        try:
            lake.write_bronze_page(source_name, page, records, run_date=run_date)
        except Exception as exc:
            logger.warning(
                "Failed to checkpoint %s page %d to the bronze layer: %s",
                source_name,
                page,
                exc,
            )

    logger.info("Crawling entity source '%s' for %s", source_name, run_date)
    fetch(None, None, start_page=start_page, on_page=_persist_page, **source.params)
    records = prior_records + new_records

    try:
        lake.write_bronze(source_name, records, run_date=run_date)
        lake.write_bronze_table(source_name, records, run_date=run_date)
    except Exception as e:
        logger.warning("Failed to write %s payload to the bronze layer: %s", source_name, e)

    context["ti"].xcom_push(key=f"raw_{source_name}", value=records)
    return records


def task_normalize_entities(
    source_name: str, spec_path: str, **context: Any
) -> dict[str, Any]:
    """Task: normalize a crawled entity source into its silver entity table.

    Pulls ``raw_<source_name>`` from the upstream crawl task, validates each
    record against the entity model registered in
    ``ENTITY_NORMALIZER_REGISTRY`` and appends the valid rows to the silver
    entity table (best-effort: invalid records and write failures are
    counted as errors, never fatal to the task).

    Args:
        source_name: Name of the entity source to normalize.
        spec_path: Path of the YAML pipeline spec.
        context: Airflow context.

    Returns:
        Summary with the entity row count and the error count.
    """
    run_date = _lake_run_date(context) or date.today()
    ti = context["ti"]

    normalizer_def = ENTITY_NORMALIZER_REGISTRY[source_name]
    raw_records = (
        ti.xcom_pull(task_ids=f"crawl_{source_name}", key=f"raw_{source_name}") or []
    )

    rows: list[dict[str, Any]] = []
    errors = 0
    for raw in raw_records:
        try:
            rows.append(normalizer_def.normalize(raw).model_dump(mode="json"))
        except Exception as exc:
            errors += 1
            logger.warning("Failed to normalize %s record: %s", source_name, exc)

    try:
        lake.write_silver_entities(normalizer_def.entity, rows, run_date=run_date)
    except Exception as exc:
        errors += 1
        logger.warning(
            "Failed to write %s rows to the silver layer: %s",
            normalizer_def.entity,
            exc,
        )

    summary: dict[str, Any] = {
        "source": source_name,
        "entities": {normalizer_def.entity: len(rows)},
        "errors": errors,
    }
    ti.xcom_push(key=f"entities_{source_name}", value=summary)
    logger.info("Entity normalization finished for %s: %s", source_name, summary)
    return summary


def task_silver_entities_summary(spec_path: str, **context: Any) -> dict[str, Any]:
    """Task: report the entity counts written by the normalize tasks.

    The lake_silver destination of an entities_collect spec only reports —
    the silver writes already happened in the ``normalize_<source>`` tasks
    (same contract as the file_dump silver destination).

    Args:
        spec_path: Path of the YAML pipeline spec.
        context: Airflow context.

    Returns:
        Summary with the per-source normalize results.
    """
    spec = load_spec(spec_path)
    ti = context["ti"]

    entities: dict[str, Any] = {}
    for source in spec.sources:
        value = ti.xcom_pull(
            task_ids=f"normalize_{source.name}", key=f"entities_{source.name}"
        )
        if value is not None:
            entities[source.name] = value
    return {"entities": entities}
