"""Tests for the anomalous-geography battery runner (bateria D-09).

Responsibility: Validate the planted-population generator (determinism,
case structure), the evaluation of the pre-registered predictions P1-P5
and the raw-output plumbing, using the declarative config
experiments/detect/D-09.json. Pre-registration:
docs/preregistrations/PR-D-09.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batteries import battery_geography

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-09.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def records(tmp_path: Path) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir."""
    return battery_geography.run_battery(CONFIG, tmp_path)


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same population, bit a bit."""
    first = battery_geography.generate_population(CONFIG, seed=17)
    second = battery_geography.generate_population(CONFIG, seed=17)
    assert first == second


def test_generate_population_seed_variation() -> None:
    """Different seeds randomize the neutral fields (documents, amounts)."""
    first = battery_geography.generate_population(CONFIG, seed=17)
    second = battery_geography.generate_population(CONFIG, seed=27)
    assert first != second


def test_generate_population_structure() -> None:
    """Cases G1-G10 and the control pairs planted as declared."""
    population = battery_geography.generate_population(CONFIG, seed=17)
    expected_cases = [case["id"] for case in CONFIG["cases"]]
    assert sorted(population["meta"]) == sorted(
        expected_cases + [f"CTRL-{i:02d}" for i in range(CONFIG["control_pairs"])]
    )
    # The synthetic table carries both seats of every resolvable case.
    names = {row["name"] for row in population["municipalities"]}
    assert "CIDADE G4" in names and "SEDE G4" in names
    # G8 has an establishment with an unknown TOM; G9's buyer is off-table.
    assert "CIDADE G9" not in names
    g8 = next(
        e
        for e in population["establishments"]
        if e["cnpj"] == population["meta"]["G8"]
    )
    assert g8["municipio"] not in {
        row["tom_code"] for row in population["rfb_municipalities"]
    }


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered predictions hold on the reference config."""
    summary = battery_geography.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(
    records: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Raw per-seed outputs and the summary are written."""
    n_expected = len(CONFIG["expected"]["signal_cases"])
    for seed in CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == n_expected
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-09"
    assert summary["verdict"] == "success"


def test_evaluate_detects_refutation() -> None:
    """A tampered signal set refutes P1 and the battery."""
    record = battery_geography.run_seed(CONFIG, seed=17)
    record["signals"] = record["signals"][:-1]  # drop a signal
    summary = battery_geography.evaluate(CONFIG, [record])
    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_detects_score_anchor_deviation() -> None:
    """A score off the pre-registered anchor (G4 = 0.1033) refutes P3."""
    record = battery_geography.run_seed(CONFIG, seed=17)
    meta = battery_geography.generate_population(CONFIG, seed=17)["meta"]
    for signal in record["signals"]:
        if signal["entity_id"] == meta["G4"]:
            signal["score"] = 0.2
    summary = battery_geography.evaluate(CONFIG, [record])
    assert summary["predictions"]["P3"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_detects_missing_data_signal() -> None:
    """A forged signal for G8 (no supplier coordinates) refutes P4."""
    record = battery_geography.run_seed(CONFIG, seed=17)
    meta = battery_geography.generate_population(CONFIG, seed=17)["meta"]
    record["signals"] = record["signals"] + [
        {
            "entity_type": "supplier",
            "entity_id": meta["G8"],
            "signal_type": "anomalous_geography",
            "score": 0.5,
            "details": "{}",
        }
    ]
    summary = battery_geography.evaluate(CONFIG, [record])
    assert summary["predictions"]["P4"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
