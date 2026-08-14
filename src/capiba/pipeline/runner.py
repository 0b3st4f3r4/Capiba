"""Declarative pipeline runner.

Responsibility: execute a ``PipelineSpec`` (loaded from YAML) through its
formula, collecting per-step metrics (name, duration, rows in/out, errors)
into a ``PipelineReport``. Pure Python — no Airflow imports; the
``task_run_pipeline`` wrapper in ``capiba.pipeline.tasks`` adapts it to the
Airflow context.

Failure semantics mirror the legacy tasks: lake/gold/graph writes are
best-effort (logged warnings, counted as step errors), while source crawl
failures and critical validation conditions (e.g. an empty Federal Revenue
manifest) fail the run via ``PipelineRunError`` carrying the partial report.

Formulas and destination handlers register themselves into
``capiba.pipeline.registry.FORMULA_REGISTRY``/``DESTINATION_REGISTRY`` at
the bottom of this module; importing the runner (done by
``capiba.pipeline.spec.load_spec``) populates them before validation.

Dependencies: capiba.pipeline.registry/window/lake/tasks, quality validators.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pandas as pd
from pydantic import BaseModel, Field

from capiba.config import FEDERAL_REVENUE_FILES
from capiba.pipeline import lake
from capiba.pipeline.registry import (
    DESTINATION_REGISTRY,
    FORMULA_REGISTRY,
    NORMALIZER_REGISTRY,
    RULESET_REGISTRY,
    SOURCE_REGISTRY,
    get_transformation,
)
from capiba.pipeline.tasks import persist_contracts, validate_contracts
from capiba.pipeline.window import DateRange, resolve_window
from capiba.quality.validators import QualityValidator

if TYPE_CHECKING:
    from capiba.pipeline.spec import PipelineSpec

logger = logging.getLogger(__name__)


class StepMetrics(BaseModel):
    """Metrics collected for one pipeline step."""

    name: str
    duration_seconds: float
    rows_in: int | None = None
    rows_out: int | None = None
    errors: int = 0
    error: str | None = None


class PipelineReport(BaseModel):
    """Run report of a declarative pipeline (published to the gold layer)."""

    pipeline: str
    execution_date: date
    started_at: datetime
    duration_seconds: float
    success: bool
    steps: list[StepMetrics] = Field(default_factory=list)
    validation: dict[str, Any] | None = None
    outputs: dict[str, Any] = Field(default_factory=dict)


class PipelineRunError(RuntimeError):
    """Raised when a pipeline run fails; carries the partial report."""

    def __init__(self, message: str, report: PipelineReport) -> None:
        super().__init__(message)
        self.report = report


@dataclass
class FormulaResult:
    """Intermediate artifacts produced by a formula, consumed by destinations."""

    raw: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    contracts: list[dict[str, Any]] = field(default_factory=list)
    validation: dict[str, Any] | None = None
    manifests: dict[str, dict[str, Any]] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)


# Step functions return (value, rows_out, errors); rows_in is declared by
# the caller. Destination handlers share the same convention.
StepFn = Callable[[], tuple[Any, int | None, int]]
DestinationHandler = Callable[..., tuple[dict[str, Any], int | None, int]]
FormulaFn = Callable[
    ["PipelineSpec", date, list[StepMetrics], DateRange | None], FormulaResult
]


def _run_step(
    steps: list[StepMetrics], name: str, fn: StepFn, rows_in: int | None = None
) -> Any:
    """Runs a step, recording its metrics; exceptions are recorded and raised."""
    started = time.perf_counter()
    try:
        value, rows_out, errors = fn()
    except Exception as exc:
        steps.append(
            StepMetrics(
                name=name,
                duration_seconds=round(time.perf_counter() - started, 4),
                rows_in=rows_in,
                error=str(exc),
            )
        )
        raise
    steps.append(
        StepMetrics(
            name=name,
            duration_seconds=round(time.perf_counter() - started, 4),
            rows_in=rows_in,
            rows_out=rows_out,
            errors=errors,
        )
    )
    return value


def _formula_contracts_default(
    spec: PipelineSpec,
    execution_date: date,
    steps: list[StepMetrics],
    window_override: DateRange | None = None,
) -> FormulaResult:
    """Formula: crawl -> normalize -> transform? -> validate? -> destinations.

    Mirrors the legacy daily_ingestion flow: each source is crawled with its
    own window (per-source ``window`` overriding the pipeline default), raw
    payloads are kept for the bronze destination, records are normalized
    into the unified Contract schema, optionally transformed and validated.
    """
    result = FormulaResult()

    for source in spec.sources:
        date_range = window_override or resolve_window(
            source.window or spec.window, execution_date
        )
        fetch = SOURCE_REGISTRY[source.name].fetch
        if fetch is None:  # guarded by spec validation
            raise ValueError(f"Source '{source.name}' has no record fetcher")

        def _crawl(
            fetch: Callable[..., list[dict[str, Any]]] = fetch,
            source_name: str = source.name,
            source_params: dict[str, Any] = source.params,
            date_range: DateRange = date_range,
        ) -> tuple[list[dict[str, Any]], int, int]:
            records = fetch(date_range.start, date_range.end, **source_params)
            return records, len(records), 0

        result.raw[source.name] = _run_step(steps, f"crawl_{source.name}", _crawl)

    normalization_errors = 0

    def _normalize() -> tuple[list[dict[str, Any]], int, int]:
        nonlocal normalization_errors
        contracts: list[dict[str, Any]] = []
        for source in spec.sources:
            normalizer = NORMALIZER_REGISTRY[source.name]
            for raw in result.raw[source.name]:
                try:
                    contracts.append(normalizer(raw).model_dump(mode="json"))
                except Exception as exc:
                    normalization_errors += 1
                    logger.warning(
                        "Failed to normalize %s record: %s", source.name, exc
                    )
        return contracts, len(contracts), normalization_errors

    total_raw = sum(len(records) for records in result.raw.values())
    result.contracts = _run_step(steps, "normalize", _normalize, rows_in=total_raw)

    for transformation in spec.transformations:
        transform = get_transformation(transformation.name)

        def _apply(
            transform: Callable[..., list[dict[str, Any]]] = transform,
            params: dict[str, Any] = transformation.params,
        ) -> tuple[list[dict[str, Any]], int, int]:
            transformed = transform(result.contracts, **params)
            return transformed, len(transformed), 0

        result.contracts = _run_step(
            steps,
            f"transform_{transformation.name}",
            _apply,
            rows_in=len(result.contracts),
        )

    if spec.validation:
        ruleset = spec.validation.ruleset

        def _validate(ruleset: str = ruleset) -> tuple[dict[str, Any], int, int]:
            report = validate_contracts(
                result.contracts, normalization_errors=normalization_errors
            )
            quality = _apply_ruleset(ruleset, result.contracts)
            if quality:
                report["quality_rules"] = quality
            return report, len(result.contracts), int(report["duplicates"])

        result.validation = _run_step(
            steps, "validate", _validate, rows_in=len(result.contracts)
        )

    return result


def _apply_ruleset(ruleset: str, contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Applies a quality ruleset over the flattened contracts DataFrame.

    Nested entity fields are flattened with ``_`` separators so rules can
    reference e.g. ``supplier_cnpj`` (see ``CONTRACT_RULES``). ``amount``
    (serialized as string in JSON mode) is coerced to numeric; a rule that
    fails to run is recorded with its error instead of aborting the step.
    """
    if not contracts:
        return []
    df = pd.json_normalize(contracts, sep="_")
    if "amount" in df.columns:
        df["amount"] = pd.to_numeric(df["amount"], errors="coerce")

    results: list[dict[str, Any]] = []
    for rule in RULESET_REGISTRY[ruleset]:
        validator = QualityValidator()
        validator.add_rule(rule)
        try:
            results.extend(vars(r) for r in validator.validate(df))
        except Exception as exc:
            logger.warning("Quality rule '%s' failed to run: %s", rule.name, exc)
            results.append(
                {"rule": rule.name, "severity": rule.severity, "error": str(exc)}
            )
    return results


def _formula_file_dump(
    spec: PipelineSpec,
    execution_date: date,
    steps: list[StepMetrics],
    window_override: DateRange | None = None,
) -> FormulaResult:
    """Formula: download dump files -> bronze files + manifest.

    Mirrors the legacy ``task_crawl_federal_revenue``: the reference month
    comes from the (month-aligned) window, files are downloaded to a temp
    dir, uploaded to the bronze bucket and recorded in a manifest. An empty
    manifest fails the run loudly — a missing share must not be recorded as
    a successful run.
    """
    result = FormulaResult()

    for source in spec.sources:
        date_range = window_override or resolve_window(
            source.window or spec.window, execution_date
        )
        if date_range.start is None:
            raise ValueError(
                f"Source '{source.name}' (file_dump) requires a month-bounded window"
            )
        reference_month = date_range.start.strftime("%Y-%m")
        download = SOURCE_REGISTRY[source.name].download
        if download is None:  # guarded by spec validation
            raise ValueError(f"Source '{source.name}' has no dump downloader")

        params = {"files": FEDERAL_REVENUE_FILES, **source.params}

        def _download(
            download: Callable[..., list[Path]] = download,
            source_name: str = source.name,
            reference_month: str = reference_month,
            params: dict[str, Any] = params,
        ) -> tuple[dict[str, Any], int, int]:
            manifest: list[dict[str, Any]] = []
            with tempfile.TemporaryDirectory() as tmp:
                downloaded = download(Path(tmp), reference_month, **params)
                for path in downloaded:
                    data = path.read_bytes()
                    key = lake.write_bronze_file(
                        source_name, path.name, data, run_date=execution_date
                    )
                    manifest.append(
                        {
                            "file": path.name,
                            "bytes": len(data),
                            "sha256": hashlib.sha256(data).hexdigest(),
                            "lake_key": key,
                        }
                    )
            if not manifest:
                raise RuntimeError(
                    f"No files downloaded for source '{source_name}'"
                    f" (reference month {reference_month})"
                )
            payload = {"reference_month": reference_month, "files": manifest}
            return payload, len(manifest), 0

        result.manifests[source.name] = _run_step(
            steps, f"download_{source.name}", _download
        )
        result.outputs[f"{source.name}_reference_month"] = reference_month
        result.outputs[f"{source.name}_files"] = len(
            result.manifests[source.name]["files"]
        )

    return result


def _formula_metrics_collect(
    spec: PipelineSpec,
    execution_date: date,
    steps: list[StepMetrics],
    window_override: DateRange | None = None,
) -> FormulaResult:
    """Formula: collect point-in-time metric snapshots -> destinations.

    Metrics sources (e.g. ``pod_usage``) emit snapshot records that flow
    straight to the destinations as raw payloads (bronze ``raw_<source>``
    tables) — no normalization or validation steps. The declared window is
    ignored: a snapshot has no temporal range.
    """
    result = FormulaResult()

    for source in spec.sources:
        fetch = SOURCE_REGISTRY[source.name].fetch
        if fetch is None:  # guarded by spec validation
            raise ValueError(f"Source '{source.name}' has no record fetcher")

        def _collect(
            fetch: Callable[..., list[dict[str, Any]]] = fetch,
            source_name: str = source.name,
            source_params: dict[str, Any] = source.params,
        ) -> tuple[list[dict[str, Any]], int, int]:
            records = fetch(None, None, **source_params)
            return records, len(records), 0

        result.raw[source.name] = _run_step(steps, f"collect_{source.name}", _collect)

    return result


def _dest_lake_bronze(
    spec: PipelineSpec, execution_date: date, result: FormulaResult
) -> tuple[dict[str, Any], int | None, int]:
    """Best-effort: raw payloads/manifests to the bronze layer."""
    written = 0
    errors = 0
    payloads: dict[str, Any] = {**result.raw, **result.manifests}
    for source_name, payload in payloads.items():
        try:
            lake.write_bronze(source_name, payload, run_date=execution_date)
            lake.write_bronze_table(source_name, payload, run_date=execution_date)
            written += 1
        except Exception as exc:
            errors += 1
            logger.warning(
                "Failed to write %s payload to the bronze layer: %s",
                source_name,
                exc,
            )
    return {"sources": sorted(payloads)}, written, errors


def _dest_lake_silver(
    spec: PipelineSpec, execution_date: date, result: FormulaResult
) -> tuple[dict[str, Any], int | None, int]:
    """Best-effort: normalized contracts to the silver Iceberg table."""
    try:
        table = lake.write_silver(result.contracts, run_date=execution_date)
    except Exception as exc:
        logger.warning("Failed to write contracts to the silver layer: %s", exc)
        return {"error": str(exc)}, None, 1
    return {"table": table}, len(result.contracts), 0


def _dest_arangodb_graph(
    spec: PipelineSpec, execution_date: date, result: FormulaResult
) -> tuple[dict[str, Any], int | None, int]:
    """Best-effort: contracts upserted into the ArangoDB graph."""
    summary = persist_contracts(
        result.contracts, execution_date=execution_date.isoformat()
    )
    errors = 1 if "error" in summary else 0
    return summary, len(result.contracts), errors


def _dest_gold_report(
    spec: PipelineSpec, execution_date: date, report: dict[str, Any]
) -> tuple[dict[str, Any], int | None, int]:
    """Best-effort: the run report object to the gold bucket.

    Unlike the other handlers, this one receives the final report payload
    (built by ``run_pipeline``) instead of the formula result.
    """
    key = lake.write_gold(report, report_name=spec.name, run_date=execution_date)
    return {"key": key}, None, 0


def _publish_platform_metrics(report: PipelineReport, execution_date: date) -> None:
    """Best-effort: per-step run metrics to the gold platform_metrics table."""
    try:
        lake.write_platform_metrics(report, run_date=execution_date)
    except Exception as exc:
        logger.warning("Failed to publish platform metrics: %s", exc)


def run_pipeline(
    spec: PipelineSpec,
    execution_date: date,
    window_override: DateRange | None = None,
) -> PipelineReport:
    """Executes a declarative pipeline spec.

    Args:
        spec: The validated pipeline spec.
        execution_date: Reference date of the run.
        window_override: Explicit date range applied to every source,
            ignoring the declared windows (used by the manual CLI).

    Returns:
        The ``PipelineReport`` with per-step metrics.

    Raises:
        PipelineRunError: On source/critical failures; the partial report
            is available as ``exc.report``.
    """
    started_at = datetime.now(UTC)
    monotonic = time.perf_counter()
    steps: list[StepMetrics] = []

    formula = FORMULA_REGISTRY[spec.formula]
    try:
        result = formula(spec, execution_date, steps, window_override)
    except Exception as exc:
        report = PipelineReport(
            pipeline=spec.name,
            execution_date=execution_date,
            started_at=started_at,
            duration_seconds=round(time.perf_counter() - monotonic, 4),
            success=False,
            steps=steps,
        )
        _publish_platform_metrics(report, execution_date)
        raise PipelineRunError(f"Pipeline '{spec.name}' failed: {exc}", report) from exc

    outputs = dict(result.outputs)
    for destination in spec.destinations:
        if destination.name == "gold_report":
            continue  # handled after the report is built
        handler = DESTINATION_REGISTRY[destination.name]

        def _destination_step(
            handler: DestinationHandler = handler,
        ) -> tuple[dict[str, Any], int | None, int]:
            return handler(spec, execution_date, result)

        summary = _run_step(
            steps,
            f"destination_{destination.name}",
            _destination_step,
        )
        outputs[f"destination_{destination.name}"] = summary

    report = PipelineReport(
        pipeline=spec.name,
        execution_date=execution_date,
        started_at=started_at,
        duration_seconds=round(time.perf_counter() - monotonic, 4),
        success=True,
        steps=steps,
        validation=result.validation,
        outputs=outputs,
    )

    if any(d.name == "gold_report" for d in spec.destinations):
        try:
            summary = _run_step(
                steps,
                "destination_gold_report",
                lambda: _dest_gold_report(
                    spec, execution_date, report.model_dump(mode="json")
                ),
            )
            report.outputs["destination_gold_report"] = summary
        except Exception as exc:
            logger.warning("Failed to write run report to the gold layer: %s", exc)

    _publish_platform_metrics(report, execution_date)

    logger.info(
        "Pipeline '%s' finished in %.2fs (%d steps)",
        spec.name,
        report.duration_seconds,
        len(report.steps),
    )
    return report


# Plugin registration: importing this module populates the registries used
# by capiba.pipeline.spec for cross-validation.
FORMULA_REGISTRY.update(
    {
        "contracts_default": _formula_contracts_default,
        "file_dump": _formula_file_dump,
        "metrics_collect": _formula_metrics_collect,
    }
)
DESTINATION_REGISTRY.update(
    {
        "lake_bronze": _dest_lake_bronze,
        "lake_silver": _dest_lake_silver,
        "arangodb_graph": _dest_arangodb_graph,
        "gold_report": _dest_gold_report,
    }
)
