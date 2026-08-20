"""Fast (non-slow) tests for the geography battery runner (bateria D-09).

Responsibility: Exercise the runner end to end — population generation,
prediction evaluation, success/refutation verdicts and raw-output writing
— with a minimal inline config (few cases, two seeds), so the fast suite
covers the module without waiting for the full pre-registered battery
(the slow regime test reads ``experiments/detect/D-09.json``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from capiba.detection import battery_geography

# Minimal config with the cases ``evaluate`` inspects unconditionally
# (G2/G3/G4 gate, G8/G9/G10 missing-data discipline) plus G1; G5-G7 are
# omitted, so the only signal case is the G4 anchor.
FAST_CONFIG: dict[str, Any] = {
    "id": "D-09-fast",
    "seeds": [7, 11],
    "thresholds": {
        "max_distance_km": 100.0,
        "score_distance_reference": 1000.0,
        "earth_radius_km": 6371.0,
    },
    "control_pairs": 2,
    "expected": {
        "signal_cases": ["G4"],
        "no_signal_cases": ["G1", "G2", "G3", "G8", "G9", "G10"],
    },
    "cases": [
        {
            "id": "G1",
            "supplier": {"doc_type": "cnpj", "lat": -8.0476, "lon": -34.8770},
            "buyer": {"lat": -8.0476, "lon": -34.8770},
            "expected": {"signal": False},
        },
        {
            "id": "G2",
            "supplier": {"doc_type": "cnpj", "lat": -8.0476, "lon": -34.8770},
            "buyer": {"lat": -8.0089, "lon": -34.8553},
            "expected": {"signal": False, "distance_km": 4.922050},
        },
        {
            "id": "G3",
            "supplier": {"doc_type": "cnpj", "lat": 0.0, "lon": 0.0},
            "buyer": {"lat": 0.0, "lon": 0.75},
            "expected": {"signal": False, "distance_km": 83.396195},
        },
        {
            "id": "G4",
            "supplier": {"doc_type": "cnpj", "lat": -8.0476, "lon": -34.8770},
            "buyer": {"lat": -7.1195, "lon": -34.8450},
            "expected": {"signal": True, "distance_km": 103.260266, "score": 0.1033},
        },
        {
            "id": "G8",
            "supplier": {"doc_type": "cnpj", "lat": None, "lon": None},
            "buyer": {"lat": -23.5505, "lon": -46.6333},
            "expected": {"signal": False},
        },
        {
            "id": "G9",
            "supplier": {"doc_type": "cnpj", "lat": -8.0476, "lon": -34.8770},
            "buyer": {"lat": None, "lon": None},
            "expected": {"signal": False},
        },
        {
            "id": "G10",
            "supplier": {"doc_type": "cpf", "lat": None, "lon": None},
            "buyer": {"lat": -8.0476, "lon": -34.8770},
            "expected": {"signal": False},
        },
    ],
}


def test_run_battery_success_end_to_end(tmp_path: Path) -> None:
    """The minimal config runs the full pipeline to a success verdict."""
    records = battery_geography.run_battery(FAST_CONFIG, tmp_path)

    assert len(records) == 2
    for record in records:
        assert len(record["signals"]) == 1  # only G4 signals
        assert record["repeat_divergences"] == 0

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-09-fast"
    assert summary["verdict"] == "success"
    for name in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][name]["verdict"] == "success"

    for seed in FAST_CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == 1
        signal = json.loads(lines[0])
        assert signal["signal_type"] == "anomalous_geography"
        assert signal["score"] == 0.1033


def test_evaluate_refutes_on_gate_violation() -> None:
    """A forged signal below the gate (G3) refutes P1 and P2."""
    record = battery_geography.run_seed(FAST_CONFIG, seed=7)
    meta = battery_geography.generate_population(FAST_CONFIG, seed=7)["meta"]
    record["signals"] = record["signals"] + [
        {
            "entity_type": "supplier",
            "entity_id": meta["G3"],
            "signal_type": "anomalous_geography",
            "score": 0.1,
            "details": "{}",
        }
    ]

    summary = battery_geography.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_repeat_divergence() -> None:
    """A non-deterministic run refutes P5."""
    record = battery_geography.run_seed(FAST_CONFIG, seed=7)
    record["repeat_divergences"] = 1

    summary = battery_geography.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P5"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_missing_anchor() -> None:
    """Dropping the only expected signal refutes P1, P2 and P3."""
    record = battery_geography.run_seed(FAST_CONFIG, seed=7)
    record["signals"] = []

    summary = battery_geography.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert summary["predictions"]["P3"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_score_anchor_deviation() -> None:
    """A score off the anchor (G4 = 0.1033) refutes P3."""
    record = battery_geography.run_seed(FAST_CONFIG, seed=7)
    meta = battery_geography.generate_population(FAST_CONFIG, seed=7)["meta"]
    for signal in record["signals"]:
        if signal["entity_id"] == meta["G4"]:
            signal["score"] = 0.2

    summary = battery_geography.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P3"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_distance_anchor_deviation() -> None:
    """A details distance off the pinned anchor refutes P3."""
    record = battery_geography.run_seed(FAST_CONFIG, seed=7)
    meta = battery_geography.generate_population(FAST_CONFIG, seed=7)["meta"]
    for signal in record["signals"]:
        if signal["entity_id"] == meta["G4"]:
            details = json.loads(signal["details"])
            details["distance_km"] = 200.0
            signal["details"] = json.dumps(details, sort_keys=True)

    summary = battery_geography.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P3"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_missing_data_signal() -> None:
    """A forged signal for G8 (no supplier coordinates) refutes P4."""
    record = battery_geography.run_seed(FAST_CONFIG, seed=7)
    meta = battery_geography.generate_population(FAST_CONFIG, seed=7)["meta"]
    record["signals"] = record["signals"] + [
        {
            "entity_type": "supplier",
            "entity_id": meta["G8"],
            "signal_type": "anomalous_geography",
            "score": 0.5,
            "details": "{}",
        }
    ]

    summary = battery_geography.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P4"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
