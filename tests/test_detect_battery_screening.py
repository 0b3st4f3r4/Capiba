"""Tests for the sanction-screening battery runner (bateria D-06).

Responsibility: Validate the planted-population generator (determinism,
seed variation, planted structure) and the evaluation of the
pre-registered predictions P1-P5 (docs/preregistrations/PR-D-06.md),
using the declarative config experiments/detect/D-06.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery_screening

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-06.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def records(tmp_path: Path) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir and returns the per-seed records."""
    return battery_screening.run_battery(CONFIG, tmp_path)


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same population, bit a bit."""
    first = battery_screening.generate_population(CONFIG, seed=19)
    second = battery_screening.generate_population(CONFIG, seed=19)
    assert first == second


def test_generate_population_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_screening.generate_population(CONFIG, seed=19)
    second = battery_screening.generate_population(CONFIG, seed=29)
    assert first != second


def test_generate_population_structure() -> None:
    """Cases and controls planted as declared."""
    population = battery_screening.generate_population(CONFIG, seed=19)
    n_cases = len(CONFIG["cases"])
    n_controls = CONFIG["control_suppliers"]
    assert len(population["contracts"]) == n_cases + n_controls
    # S7: the sanction document differs from the supplier's
    s7 = [s for s in population["sanctions"] if s["id"].startswith("ceis-S7-")]
    assert len(s7) == 1
    assert s7[0]["cnpj"] != population["meta"]["S7"]
    # S8: the supplier has no document at all
    s8_contract = next(
        c for c in population["contracts"] if c["id"].endswith("-S8")
    )
    assert s8_contract["supplier"]["cnpj"] is None
    assert s8_contract["supplier"]["cpf"] is None


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered predictions hold on the reference config."""
    summary = battery_screening.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(tmp_path: Path) -> None:
    """Raw per-seed outputs and the summary are persisted."""
    battery_screening.run_battery(CONFIG, tmp_path)
    n_signaled = sum(1 for c in CONFIG["cases"] if c["expected"]["signal"])
    for seed in CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == n_signaled
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-06"


def test_evaluate_detects_refutation() -> None:
    """A tampered signal refutes P1 and the battery."""
    record = battery_screening.run_seed(CONFIG, seed=19)
    record["signals"] = record["signals"][:-1]  # drop a signal
    summary = battery_screening.evaluate(CONFIG, [record])
    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
