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
from capiba.pipeline.registry import ENTITY_NORMALIZER_REGISTRY
from capiba.pipeline.spec import load_spec
from capiba.pipeline.tasks import _lake_run_date

logger = logging.getLogger(__name__)


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
