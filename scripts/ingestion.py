#!/usr/bin/env python3
"""Manual CLI for public data ingestion into Capiba.

Thin wrapper over the declarative pipeline framework
(``capiba.pipeline.runner``): builds an in-memory spec for the selected
sources and runs the ``contracts_default`` formula with an explicit date
range, with no Airflow dependency. The ``--mock`` mode runs offline with
the sample data from ``capiba.ingestion.mock``, and ``--dry-run`` skips
ArangoDB persistence (lake writes stay best-effort).

Usage:
    python scripts/ingestion.py --source pncp --start-date 2026-01-01 --end-date 2026-01-01 --dry-run
    python scripts/ingestion.py --mock --start-date 2026-01-01 --end-date 2026-01-01
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from capiba.pipeline.runner import run_pipeline
from capiba.pipeline.spec import PipelineSpec
from capiba.pipeline.window import DateRange

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Manual ingestion of public data into Capiba"
    )
    parser.add_argument(
        "--source",
        choices=["pncp", "transparency", "both"],
        default="both",
        help="Data source to ingest",
    )
    parser.add_argument(
        "--start-date",
        help="Start date in YYYY-MM-DD format (required outside --mock mode)",
    )
    parser.add_argument(
        "--end-date",
        help="End date in YYYY-MM-DD format (required outside --mock mode)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Does not persist to the database, only simulates the pipeline",
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Persists the normalized contracts in ArangoDB (dry-run disables)",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Uses sample data instead of calling external APIs",
    )
    return parser.parse_args(argv)


def _build_spec(source: str, mock: bool, persist: bool) -> PipelineSpec:
    """Builds the in-memory spec for the CLI selection.

    The window is irrelevant (the CLI always passes an explicit date range
    as override), so ``all`` is declared and every source uses it.
    """
    names = []
    if source in ("pncp", "both"):
        names.append("mock_pncp" if mock else "pncp")
    if source in ("transparency", "both"):
        names.append("mock_transparency" if mock else "transparency")

    destinations = ["lake_bronze", "lake_silver"]
    if persist:
        destinations.append("arangodb_graph")

    return PipelineSpec.model_validate(
        {
            "name": "manual_ingestion",
            "window": "all",
            "sources": names,
            "formula": "contracts_default",
            "validate": {"ruleset": "contract_rules"},
            "destinations": destinations,
        }
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    persist = args.persist and not args.dry_run

    if not args.start_date or not args.end_date:
        if not args.mock:
            logger.error("--start-date and --end-date are required outside --mock mode")
            return 2
        today = date.today().isoformat()
        args.start_date = args.start_date or today
        args.end_date = args.end_date or today

    start = date.fromisoformat(args.start_date)
    end = date.fromisoformat(args.end_date)

    spec = _build_spec(args.source, args.mock, persist)
    logger.info(
        "Running manual ingestion: sources=%s, %s to %s (persist=%s)",
        [s.name for s in spec.sources],
        start,
        end,
        persist,
    )

    report = run_pipeline(
        spec, execution_date=start, window_override=DateRange(start=start, end=end)
    )

    if not persist:
        report.outputs["persistence"] = {"mode": "dry-run", "persisted": False}

    print(report.model_dump_json(indent=2))
    valid = report.validation is None or report.validation.get("valid", False)
    return 0 if valid else 1


if __name__ == "__main__":
    sys.exit(main())
