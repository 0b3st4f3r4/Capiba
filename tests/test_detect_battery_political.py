"""Tests for the political-connection battery runner (bateria D-08).

Responsibility: Validate the planted-population generator (determinism,
case structure), the evaluation of the pre-registered predictions P1-P7
and the raw-output plumbing, using the declarative config
experiments/detect/D-08.json. Pre-registration:
docs/preregistrations/PR-D-08.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batteries import battery_political

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-08.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def records(tmp_path: Path) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir."""
    return battery_political.run_battery(CONFIG, tmp_path)


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same population, bit a bit."""
    first = battery_political.generate_population(CONFIG, seed=17)
    second = battery_political.generate_population(CONFIG, seed=17)
    assert first == second


def test_generate_population_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_political.generate_population(CONFIG, seed=17)
    second = battery_political.generate_population(CONFIG, seed=27)
    assert first != second


def test_generate_population_structure() -> None:
    """Cases E1-E10 and the control pairs planted as declared."""
    population = battery_political.generate_population(CONFIG, seed=17)
    assert sorted(population["meta"]) == [
        "E1",
        "E10",
        "E2",
        "E3",
        "E4",
        "E5",
        "E6",
        "E7",
        "E8",
        "E9",
    ]
    n_controls = sum(1 for c in population["contracts"] if "CTRL" in c["id"])
    assert n_controls == 2 * CONFIG["control_pairs"]  # supplier + filler
    # Each case has its own municipality (no share contamination); control
    # donors supply only their city B (city A receives no contract).
    cities = {c["buyer"]["city"] for c in population["contracts"]}
    assert len(cities) == 10 + CONFIG["control_pairs"]


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered predictions hold on the reference config."""
    summary = battery_political.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
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
    assert summary["battery"] == "D-08"
    assert summary["verdict"] == "success"


def test_evaluate_detects_refutation() -> None:
    """A tampered signal set refutes P1 and the battery."""
    record = battery_political.run_seed(CONFIG, seed=17)
    record["signals"] = record["signals"][:-1]  # drop a signal
    summary = battery_political.evaluate(CONFIG, [record])
    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_detects_score_anchor_deviation() -> None:
    """A score off the pre-registered anchor (E5 = 0.5) refutes P4."""
    record = battery_political.run_seed(CONFIG, seed=17)
    meta = battery_political.generate_population(CONFIG, seed=17)["meta"]
    for signal in record["signals"]:
        if signal["entity_id"] == meta["E5"]:
            signal["score"] = 0.4
    summary = battery_political.evaluate(CONFIG, [record])
    assert summary["predictions"]["P4"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
