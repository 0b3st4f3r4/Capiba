"""Tests for the amendment-flags battery runner (bateria D-05).

Responsibility: Validate the planted-sequence generator (determinism,
seed variation, planted structure) and the evaluation of the
pre-registered predictions P1-P5 (docs/preregistrations/PR-D-05.md),
using the declarative config experiments/detect/D-05.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery_amendments

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-05.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def records(tmp_path: Path) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir and returns the per-seed records."""
    return battery_amendments.run_battery(CONFIG, tmp_path)


def test_generate_cases_deterministic_per_seed() -> None:
    """The same seed reproduces the same observation sequences, bit a bit."""
    first = battery_amendments.generate_cases(CONFIG, seed=13)
    second = battery_amendments.generate_cases(CONFIG, seed=13)
    assert first == second


def test_generate_cases_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_amendments.generate_cases(CONFIG, seed=13)
    second = battery_amendments.generate_cases(CONFIG, seed=23)
    assert first != second


def test_generate_cases_structure() -> None:
    """One case per config entry, sequences and nulls planted as declared."""
    cases = battery_amendments.generate_cases(CONFIG, seed=13)
    assert [c["case"] for c in cases] == [c["id"] for c in CONFIG["cases"]]
    by_case = {c["case"]: c for c in cases}
    # A3: two observations with increasing ingestion dates
    a3 = by_case["A3"]["observations"]
    assert len(a3) == 2
    assert a3[0]["observed_on"] < a3[1]["observed_on"]
    assert a3[0]["numeroControlePNCP"] == a3[1]["numeroControlePNCP"]
    # A6: valorAcumulado absent; A8: malformed instead of absent
    assert "valorAcumulado" not in by_case["A6"]["observations"][0]
    assert by_case["A8"]["observations"][0]["valorInicial"] == "abc"


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered predictions hold on the reference config."""
    summary = battery_amendments.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(tmp_path: Path) -> None:
    """Raw per-seed outputs and the summary are persisted."""
    battery_amendments.run_battery(CONFIG, tmp_path)
    for seed in CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == len(CONFIG["cases"])
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-05"


def test_evaluate_detects_refutation() -> None:
    """A tampered flag refutes P1 and the battery."""
    record = battery_amendments.run_seed(CONFIG, seed=13)
    record["results"][0]["f_value_amendment"] = 1  # A1 expects 0
    summary = battery_amendments.evaluate(CONFIG, [record])
    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
