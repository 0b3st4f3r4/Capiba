"""Unit tests for the fuzzy-screening battery runner helpers (D-06b).

Responsibility: Cover the pure functions of
``capiba.detection.battery_screening_fuzzy`` (population generation,
evaluation of P1-P5, OS Pairs scoring) in the fast suite — the slow
regime tests in ``tests/test_detect_battery_screening_fuzzy.py`` only run
under ``CAPIBA_SLOW=1`` and leave the runner below the coverage floor.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from capiba.detection import battery_screening_fuzzy as battery

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG: dict[str, Any] = json.loads(
    (REPO_ROOT / "experiments" / "detect" / "D-06b.json").read_text()
)
SEED = CONFIG["seeds"][0]


def test_generate_population_structure() -> None:
    population = battery.generate_population(CONFIG, SEED)

    assert len(population["contracts"]) == 9 + CONFIG["control_suppliers"]
    assert len(population["sanctions"]) == 9
    assert sorted(population["meta"]) == [
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


def test_generate_population_is_deterministic() -> None:
    assert battery.generate_population(CONFIG, SEED) == battery.generate_population(
        CONFIG, SEED
    )


def test_run_seed_has_no_repeat_divergences() -> None:
    record = battery.run_seed(CONFIG, SEED)

    assert record["repeat_divergences"] == 0


def test_run_seed_signals_the_expected_cases() -> None:
    record = battery.run_seed(CONFIG, SEED)
    meta = battery.generate_population(CONFIG, SEED)["meta"]
    case_by_entity = {entity: case for case, entity in meta.items()}

    signaled = sorted(
        case_by_entity[s["entity_id"]]
        for s in record["signals"]
        if s["entity_id"] in case_by_entity
    )
    assert signaled == sorted(CONFIG["expected"]["signal_cases"])


def test_evaluate_accepts_a_clean_record() -> None:
    record = battery.run_seed(CONFIG, SEED)
    summary = battery.evaluate(CONFIG, [record])

    assert summary["verdict"] == "success"
    assert all(
        prediction["verdict"] == "success"
        for prediction in summary["predictions"].values()
    )


def test_evaluate_refutes_a_document_veto_violation() -> None:
    meta = battery.generate_population(CONFIG, SEED)["meta"]
    record = {
        "seed": SEED,
        "signals": [{"entity_id": meta["F3"]}],
        "repeat_divergences": 0,
    }
    summary = battery.evaluate(CONFIG, [record])

    assert summary["verdict"] == "refuted"
    assert summary["predictions"]["P2"]["verdict"] == "refuted"


def _pair(judgement: str, left: str, right: str) -> dict[str, Any]:
    return {
        "judgement": judgement,
        "left": {"properties": {"name": [left]}},
        "right": {"properties": {"name": [right]}},
    }


def test_evaluate_os_pairs_counts_confusion_matrix() -> None:
    sample = [
        _pair("positive", "John Smith", "John Smith"),  # tp
        _pair("positive", "John Smith", "Zuleika Fernandes"),  # fn
        _pair("negative", "Maria Souza", "Maria Souza"),  # fp (name-only failure)
        _pair("negative", "John Smith", "Zuleika Fernandes"),  # tn
    ]
    metrics = battery.evaluate_os_pairs(CONFIG, sample)

    assert (metrics["tp"], metrics["fp"], metrics["fn"], metrics["tn"]) == (
        1,
        1,
        1,
        1,
    )
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["p6"]["verdict"] == "refuted"
    assert metrics["p7"]["verdict"] == "refuted"


def test_evaluate_os_pairs_perfect_sample() -> None:
    sample = [
        _pair("positive", "John Smith", "John Smith"),
        _pair("negative", "John Smith", "Zuleika Fernandes"),
    ]
    metrics = battery.evaluate_os_pairs(CONFIG, sample)

    assert metrics["precision"] == 1.0
    assert metrics["p6"]["verdict"] == "success"
