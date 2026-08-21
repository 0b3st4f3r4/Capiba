"""dbt runner for the lakehouse marts.

Responsibility: execute the dbt project (``dbt/``) that builds the gold
Iceberg marts from the silver ``contracts`` table, using the dbt-core
Python API so it runs in-process (Airflow worker or local CLI) without
shelling out.
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from capiba.config import DBT_PROJECT_DIR

logger = logging.getLogger(__name__)


def run_dbt(
    command: str = "run",
    select: list[str] | None = None,
    exclude: list[str] | None = None,
) -> None:
    """Runs a dbt command against the lakehouse project.

    Args:
        command: dbt command to execute (``run``, ``test``, ``build``...).
        select: Optional dbt model selection (``--select``); empty/None
            builds the whole project.
        exclude: Optional dbt model exclusion (``--exclude``) — used by
            full runs to skip marts whose sources have not landed yet.

    Raises:
        RuntimeError: If dbt finishes with a non-success result.
    """
    from dbt.cli.main import dbtRunner  # pyright: ignore[reportMissingTypeStubs]

    # DuckDB needs a writable home directory for its extensions cache; the
    # Airflow task runtime may run with HOME unset/empty.
    if not os.environ.get("HOME"):
        os.environ["HOME"] = tempfile.gettempdir()

    # The DuckDB Iceberg write path stages files in a `data/` dir relative to
    # the CWD; the Airflow task CWD is read-only, so run from the dbt target
    # dir (already the project's writable scratch area).
    workdir = Path(DBT_PROJECT_DIR) / "target"
    workdir.mkdir(parents=True, exist_ok=True)
    os.chdir(workdir)

    args = [
        command,
        "--project-dir",
        DBT_PROJECT_DIR,
        "--profiles-dir",
        DBT_PROJECT_DIR,
    ]
    if select:
        args.extend(["--select", *select])
    if exclude:
        args.extend(["--exclude", *exclude])
    logger.info("Running dbt %s (project: %s, select: %s)", command, DBT_PROJECT_DIR, select)
    result = dbtRunner().invoke(args)
    if not result.success:
        raise RuntimeError(f"dbt {command} failed: {result.exception or 'see logs'}")
    logger.info("dbt %s finished successfully", command)
