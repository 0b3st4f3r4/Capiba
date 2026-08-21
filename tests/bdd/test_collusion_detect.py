"""BDD step definitions for the collusion signal of the detect task.

Feature file: tests/bdd/features/collusion_detect.feature
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from pytest_bdd import given, parsers, scenarios, then, when

from capiba.pipeline.tasks import task_detect

scenarios("features/collusion_detect.feature")


@pytest.fixture
def context() -> dict[str, Any]:
    """Shared scenario state (graph rows in, written signals out)."""
    return {}


@given(
    parsers.parse(
        'the graph has eligible suppliers "{s1}" and "{s2}" for buyer "{buyer}"'
    )
)
def graph_eligible_suppliers(
    context: dict[str, Any], s1: str, s2: str, buyer: str
) -> None:
    """AQL rows of detect_collusion: both suppliers eligible for one buyer."""
    context["graph_rows"] = [
        {"buyer": buyer, "supplier": s1, "wins": 3},
        {"buyer": buyer, "supplier": s2, "wins": 3},
    ]


@given("the graph database is unavailable")
def graph_unavailable(context: dict[str, Any]) -> None:
    context["db_down"] = True


@when("the detect task runs")
def run_detect(context: dict[str, Any]) -> None:
    with (
        patch("capiba.pipeline.detect_task.lake") as mock_lake,
        patch("capiba.pipeline.detect_task.get_capiba_db") as mock_get_db,
        patch("capiba.detection.graphs.execute_aql") as mock_execute,
    ):
        mock_lake.read_silver_contracts.return_value = []
        if context.get("db_down"):
            mock_get_db.side_effect = ConnectionError("arango down")
        else:
            mock_execute.return_value = context.get("graph_rows", [])
        context["summary"] = task_detect(ds="2026-01-10")
        context["lake"] = mock_lake


def _written_signals(context: dict[str, Any]) -> list[dict[str, Any]]:
    """Signals written to the gold layer (empty when the write was skipped)."""
    lake: MagicMock = context["lake"]
    call = lake.write_fraud_signals.call_args
    return call.args[0] if call else []


@then(
    parsers.parse('a "{signal_type}" signal is written for the pair "{entity_id}"')
)
def pair_signal_written(context: dict[str, Any], signal_type: str, entity_id: str) -> None:
    matches = [
        s
        for s in _written_signals(context)
        if s["signal_type"] == signal_type and s["entity_id"] == entity_id
    ]
    assert matches, f"signal {signal_type} for pair {entity_id} not written"


@then(parsers.parse('no "{signal_type}" signal is written'))
def signal_not_written(context: dict[str, Any], signal_type: str) -> None:
    assert not [
        s for s in _written_signals(context) if s["signal_type"] == signal_type
    ]
