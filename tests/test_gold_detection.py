"""Tests for the imperative gold_detection DAG.

Responsibility: Validate that dags/gold_detection.py parses into a DAG with
the dbt_run -> detect chain, the post-ingestion schedule and the OpenLineage
assets of the marts/signals it rebuilds — without needing a cluster.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import timedelta
from pathlib import Path

import pytest

DAGS_DIR = Path(__file__).resolve().parent.parent / "dags"


def _load_gold_detection():
    """Loads dags/gold_detection.py as a fresh module."""
    spec = importlib.util.spec_from_file_location(
        "gold_detection_under_test", DAGS_DIR / "gold_detection.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


@pytest.fixture(scope="module")
def gold_detection():
    """The real gold_detection module, loaded once."""
    return _load_gold_detection()


def test_dag_structure(gold_detection) -> None:
    """The DAG chains dbt_run -> detect / export_public_marts (O11)."""
    dag = gold_detection.dag

    assert dag.dag_id == "gold_detection"
    assert dag.schedule == "0 8 * * *"
    assert dag.catchup is False
    assert {t.task_id for t in dag.tasks} == {"dbt_run", "detect", "export_public_marts"}
    assert dag.get_task("dbt_run").downstream_task_ids == {
        "detect",
        "export_public_marts",
    }
    assert dag.get_task("detect").downstream_task_ids == set()
    assert dag.get_task("export_public_marts").downstream_task_ids == set()


def test_retry_and_timeout_defaults(gold_detection) -> None:
    """Both tasks retry once and time out after one hour."""
    dag = gold_detection.dag

    for task in dag.tasks:
        assert task.retries == 1
        assert task.execution_timeout == timedelta(hours=1)


def test_openlineage_assets(gold_detection) -> None:
    """dbt_run outputs the gold/dwh marts; detect outputs fraud_signals."""
    dag = gold_detection.dag

    dbt_outlets = {a.uri for a in dag.get_task("dbt_run").outlets}
    assert "capiba://gold/contracts_daily" in dbt_outlets
    assert "capiba://gold/supplier_stats" in dbt_outlets
    assert "capiba://dwh/serving_supplier_stats" in dbt_outlets
    assert "capiba://gold/fraud_signals" not in dbt_outlets

    detect = dag.get_task("detect")
    assert {a.uri for a in detect.outlets} == {"capiba://gold/fraud_signals"}
    assert {a.uri for a in detect.inlets} == {"capiba://silver/contracts"}

    export = dag.get_task("export_public_marts")
    assert {a.uri for a in export.outlets} == {"capiba://public/marts"}
    assert "capiba://gold/contracts_daily" in {a.uri for a in export.inlets}
