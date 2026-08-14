"""Tests for the dbt lakehouse project.

Responsibility: guarantee the project in ``dbt/`` stays parseable by
dbt-trino (profile and models compile) without any infra — ``parse``
does not open a Trino connection.
"""

from __future__ import annotations

from dbt.cli.main import dbtRunner

from capiba.config import DBT_PROJECT_DIR


def test_dbt_project_parses() -> None:
    """dbt parse succeeds for the lakehouse project."""
    result = dbtRunner().invoke(
        ["parse", "--project-dir", DBT_PROJECT_DIR, "--profiles-dir", DBT_PROJECT_DIR]
    )
    assert result.success, f"dbt parse failed: {result.exception}"
