"""Tests for the fuzzy-screening battery runner (bateria D-06b).

Responsibility: Validate the planted-population generator (determinism,
case structure), the evaluation of the pre-registered predictions P1-P5
and the OS Pairs name-only benchmark plumbing P6-P7 (fixture stream, no
network), using the declarative config experiments/detect/D-06b.json.
Pre-registration: docs/preregistrations/PR-D-06b.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery_entities, battery_screening_fuzzy

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-06b.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())

_PAIRS_FIXTURE = [
    {
        "judgement": "positive",
        "left": {"properties": {"name": ["John Smith"]}},
        "right": {"properties": {"name": ["John Smith"]}},
    },
    # Homonym: the name-only regime predicts a merge and is wrong — the
    # documented failure mode of name-only matching (PR-D-06b § 2).
    {
        "judgement": "negative",
        "left": {"properties": {"name": ["Maria Souza"]}},
        "right": {"properties": {"name": ["Maria Souza"]}},
    },
    {
        "judgement": "negative",
        "left": {"properties": {"name": ["Maria Souza"]}},
        "right": {"properties": {"name": ["Joao Silva"]}},
    },
]


@pytest.fixture
def records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Runs the battery (fixture OS Pairs stream) into a temp dir."""
    monkeypatch.setattr(
        battery_entities, "_stream_pairs", lambda url: iter(_PAIRS_FIXTURE)
    )
    return battery_screening_fuzzy.run_battery(CONFIG, tmp_path)


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same population, bit a bit."""
    first = battery_screening_fuzzy.generate_population(CONFIG, seed=19)
    second = battery_screening_fuzzy.generate_population(CONFIG, seed=19)
    assert first == second


def test_generate_population_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_screening_fuzzy.generate_population(CONFIG, seed=19)
    second = battery_screening_fuzzy.generate_population(CONFIG, seed=29)
    assert first != second


def test_generate_population_structure() -> None:
    """Cases and controls planted as declared."""
    population = battery_screening_fuzzy.generate_population(CONFIG, seed=19)
    meta = population["meta"]
    assert sorted(meta) == [
        "F1",
        "F2",
        "F3",
        "F4",
        "F5",
        "F6",
        "F7",
        "F8",
        "F9",
    ]
    n_controls = sum(
        1 for c in population["contracts"] if "CTRL" in c["id"]
    )
    assert n_controls == CONFIG["control_suppliers"]


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered synthetic predictions hold on the reference config."""
    summary = battery_screening_fuzzy.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(
    records: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Raw per-seed outputs, the OS Pairs sample cache and the summary."""
    n_expected = len(CONFIG["expected"]["signal_cases"])
    for seed in CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == n_expected
    assert (tmp_path / "pairs_sample.jsonl").exists()
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-06b"
    # Fixture: the identical-name positive scores 1.0 ≥ 0.95 → tp; the
    # homonym negative is a false positive (name-only has no document to
    # disambiguate); the disjoint negative is a true negative.
    assert summary["os_pairs"]["tp"] == 1
    assert summary["os_pairs"]["fp"] == 1
    assert summary["os_pairs"]["tn"] == 1


def test_evaluate_detects_refutation() -> None:
    """A tampered signal set refutes P1 and the battery."""
    record = battery_screening_fuzzy.run_seed(CONFIG, seed=19)
    record["signals"] = record["signals"][:-1]  # drop a signal
    summary = battery_screening_fuzzy.evaluate(CONFIG, [record])
    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_os_pairs_metrics_refute_on_low_precision() -> None:
    """Precision below the pre-registered floor refutes P6."""
    sample = [
        {
            "judgement": "negative",
            "left": {"properties": {"name": ["A"]}},
            "right": {"properties": {"name": ["A"]}},
        }
    ]
    metrics = battery_screening_fuzzy.evaluate_os_pairs(CONFIG, sample)
    assert metrics["precision"] == 0.0
    assert metrics["p6"]["verdict"] == "refuted"
