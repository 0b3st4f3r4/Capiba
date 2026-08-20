"""Airflow task wrappers for the documents_collect formula.

Responsibility: granular per-source tasks for the document pipelines
(``download_<source>_texts`` and ``validate``), mirroring the runner's
``documents_collect`` steps in the Airflow context (XCom, run date). Kept
in a dedicated module so the declarative document flow stays cohesive;
bronze writes are best-effort like the other pipeline tasks.

Dependencies: capiba.pipeline.registry/lake/spec/tasks.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from capiba.notification.alerts import notify_validation_failure
from capiba.pipeline.spec import load_spec
from capiba.pipeline.tasks import (
    _lake_run_date,
    _record_quality_batch,
    persist_document_texts,
    validate_documents,
)

logger = logging.getLogger(__name__)


def task_download_document_texts(
    source_name: str, spec_path: str, **context: Any
) -> dict[str, Any]:
    """Task: persist the extracted text of each crawled document to bronze.

    Pulls ``raw_<source_name>`` from the upstream crawl task and runs the
    shared ``persist_document_texts`` core (deterministic file names;
    texts already in the bronze layer for the run date are skipped, so a
    retried task resumes where it stopped). The enriched records (with
    ``text_bronze_file``) are pushed back under ``raw_<source_name>`` for
    the validate/destination tasks.

    Args:
        source_name: Name of the document source to download.
        spec_path: Path of the YAML pipeline spec.
        context: Airflow context.

    Returns:
        Summary (texts downloaded/skipped, errors).
    """
    run_date = _lake_run_date(context) or date.today()
    ti = context["ti"]

    records = (
        ti.xcom_pull(task_ids=f"crawl_{source_name}", key=f"raw_{source_name}") or []
    )
    summary = persist_document_texts(source_name, records, run_date=run_date)

    ti.xcom_push(key=f"raw_{source_name}", value=records)
    ti.xcom_push(key=f"texts_{source_name}", value=summary)
    return summary


def task_validate_documents(spec_path: str, **context: Any) -> dict[str, Any]:
    """Task: validate the crawled document records with the declared ruleset.

    Pulls the enriched ``raw_<source>`` records from the download tasks,
    builds the validation report (duplicates by ``url``, download errors)
    and applies the declared quality ruleset over the raw records. Feeds
    the validation alerts and the quality monitor (both best-effort).

    Args:
        spec_path: Path of the YAML pipeline spec.
        context: Airflow context.

    Returns:
        Validation report.
    """
    ti = context["ti"]
    spec = load_spec(spec_path)

    records: list[dict[str, Any]] = []
    download_errors = 0
    for source in spec.sources:
        records.extend(
            ti.xcom_pull(
                task_ids=f"download_{source.name}_texts", key=f"raw_{source.name}"
            )
            or []
        )
        summary = ti.xcom_pull(
            task_ids=f"download_{source.name}_texts", key=f"texts_{source.name}"
        )
        if summary:
            download_errors += summary.get("errors", 0)

    report = validate_documents(records, download_errors=download_errors)

    if spec.validation:
        # Import here to avoid circular imports and keep registry populated.
        from capiba.pipeline.runner import _apply_ruleset

        try:
            quality = _apply_ruleset(spec.validation.ruleset, records)
            report["quality_rules"] = quality
        except Exception as exc:
            logger.warning("Failed to apply quality ruleset: %s", exc)

    # Best-effort: alerts never fail the task.
    notify_validation_failure(report, spec.name)
    # Best-effort: feed the continuous quality monitor (no-op without Redis).
    _record_quality_batch(spec.name, records, report)

    ti.xcom_push(key="validation_report", value=report)
    return report
