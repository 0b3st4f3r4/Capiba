"""Tests for the entity-resolution battery runner (bateria D-07).

Responsibility: Validate the planted-population generator (determinism,
seed variation, case structure), the evaluation of the pre-registered
predictions P1-P5 and the OS Pairs benchmark plumbing P6-P7 (with a
fixture stream, no network), using the declarative config
experiments/detect/D-07.json. Pre-registration:
docs/preregistrations/PR-D-07.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery_entities

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-07.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())

_PAIRS_FIXTURE = [
    {
        "judgement": "positive",
        "left": {"properties": {"name": ["John Smith"], "idNumber": ["A123"]}},
        "right": {"properties": {"name": ["John Smith"], "idNumber": ["A123"]}},
    },
    {
        "judgement": "negative",
        "left": {"properties": {"name": ["John Smith"], "idNumber": ["A123"]}},
        "right": {"properties": {"name": ["John Smith"], "idNumber": ["B999"]}},
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
    return battery_entities.run_battery(CONFIG, tmp_path)


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same population, bit a bit."""
    first = battery_entities.generate_population(CONFIG, seed=19)
    second = battery_entities.generate_population(CONFIG, seed=19)
    assert first == second


def test_generate_population_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_entities.generate_population(CONFIG, seed=19)
    second = battery_entities.generate_population(CONFIG, seed=29)
    assert first != second


def test_generate_population_structure() -> None:
    """Cases and controls planted as declared."""
    population = battery_entities.generate_population(CONFIG, seed=19)
    cases = {pair["case"] for pair in population["person_pairs"]}
    assert {"E1", "E2", "E3", "E4", "E5", "E6", "CTRL"} == cases
    n_controls = CONFIG["control_persons"]
    n_ctrl_pairs = sum(1 for p in population["person_pairs"] if p["case"] == "CTRL")
    assert n_ctrl_pairs == n_controls * (n_controls - 1) // 2
    assert {link["case"] for link in population["supplier_links"]} == {"E7", "E8"}
    e5 = next(p for p in population["person_pairs"] if p["case"] == "E5")
    assert e5["a"]["cnpj_cpf_socio"] is None
    assert e5["b"]["cnpj_cpf_socio"] is None


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered synthetic predictions hold on the reference config."""
    summary = battery_entities.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(
    records: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Raw per-seed outputs, the OS Pairs sample cache and the summary."""
    n_expected = len(CONFIG["expected"]["merge_cases"]) + len(
        CONFIG["expected"]["link_cases"]
    )
    for seed in CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == n_expected
    assert (tmp_path / "pairs_sample.jsonl").exists()
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-07"
    # Fixture: the positive pair scores 0.9 (name+document) → tp=1;
    # the homonym negative is a true negative; the disjoint negative a tn.
    assert summary["os_pairs"]["tp"] == 1
    assert summary["os_pairs"]["fp"] == 0
    assert summary["os_pairs"]["precision"] == 1.0
    # The fixture's only positive carries identifiers on both sides.
    assert summary["os_pairs"]["bilateral_doc_positive_rate"] == 1.0


def test_battery_multi_sample_os_pairs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Multi-sample configs (D-07b) score one cached sample per seed."""
    config: dict[str, Any] = json.loads(
        (REPO_ROOT / "experiments" / "detect" / "D-07b.json").read_text()
    )
    monkeypatch.setattr(
        battery_entities, "_stream_pairs", lambda url: iter(_PAIRS_FIXTURE)
    )
    battery_entities.run_battery(config, tmp_path)
    for seed in config["os_pairs"]["sample_seeds"]:
        assert (tmp_path / f"pairs_sample_{seed}.jsonl").exists()
    summary = json.loads((tmp_path / "summary.json").read_text())
    samples = summary["os_pairs"]["samples"]
    assert set(samples) == {"23", "37", "41"}
    for metrics in samples.values():
        assert metrics["tp"] == 1
        assert metrics["fp"] == 0
    # Recall 1.0 per sample sits outside the recalibrated band [0.0, 0.1].
    assert summary["predictions"]["P7"]["verdict"] == "refuted"
    assert summary["predictions"]["P6"]["verdict"] == "success"


def test_evaluate_detects_refutation() -> None:
    """A tampered merge refutes P1 and the battery."""
    record = battery_entities.run_seed(CONFIG, seed=19)
    record["merges"] = record["merges"][:-1]  # drop a merge
    summary = battery_entities.evaluate(CONFIG, [record])
    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_os_pairs_metrics_refute_on_low_precision() -> None:
    """Precision below the pre-registered floor refutes P6."""
    sample = [
        {
            "judgement": "negative",
            "left": {"properties": {"name": ["A"], "idNumber": ["X1"]}},
            "right": {"properties": {"name": ["A"], "idNumber": ["X1"]}},
        }
    ]
    metrics = battery_entities.evaluate_os_pairs(CONFIG, sample)
    assert metrics["precision"] == 0.0
    assert metrics["p6"]["verdict"] == "refuted"
