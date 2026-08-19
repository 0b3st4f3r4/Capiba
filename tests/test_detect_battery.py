"""Tests for the detection battery runner (bateria D-01b).

Responsibility: Validate the synthetic contract generator (determinism,
population counts, planted patterns) and the evaluation of the
pre-registered predictions P1-P5 (docs/preregistrations/PR-D-01b.md),
using the declarative config experiments/detect/D-01b.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-01b.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def records(tmp_path: Path) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir and returns the per-seed records."""
    return battery.run_battery(CONFIG, tmp_path)


def test_generate_contracts_deterministic_per_seed() -> None:
    """The same seed reproduces the same contract list, bit a bit."""
    first, _ = battery.generate_contracts(CONFIG, seed=11)
    second, _ = battery.generate_contracts(CONFIG, seed=11)
    assert first == second


def test_generate_contracts_seed_variation() -> None:
    """Different seeds produce different populations."""
    first, _ = battery.generate_contracts(CONFIG, seed=11)
    second, _ = battery.generate_contracts(CONFIG, seed=22)
    assert first != second


def test_generator_population_counts() -> None:
    """Population sizes match the pre-registered design."""
    contracts, meta = battery.generate_contracts(CONFIG, seed=11)
    assert len(contracts) == 40 * 20 + 40 * 20 + 12 + 10 + 20
    assert len(meta["control_suppliers"]) == 40
    assert len(meta["planted_suppliers"]) == 40
    # Benford-eligible suppliers: exactly control + planted (>= 10 amounts)
    amounts_per_supplier: dict[str, int] = {}
    for c in contracts:
        if c["amount"] is not None and c["supplier"]:
            sid = c["supplier"]["cnpj"]
            amounts_per_supplier[sid] = amounts_per_supplier.get(sid, 0) + 1
    assert sum(1 for n in amounts_per_supplier.values() if n >= 10) == 80


def test_planted_leading_digit_nine_share() -> None:
    """Planted suppliers carry ~60% of amounts with leading digit 9."""
    contracts, meta = battery.generate_contracts(CONFIG, seed=11)
    planted_ids = set(meta["planted_suppliers"])
    amounts = [
        c["amount"]
        for c in contracts
        if c["supplier"] and c["supplier"]["cnpj"] in planted_ids
    ]
    share = sum(1 for a in amounts if str(a)[0] == "9") / len(amounts)
    assert 0.55 < share < 0.65


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered predictions hold on the reference config."""
    summary = battery.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(tmp_path: Path) -> None:
    """Raw per-seed outputs and the summary are persisted."""
    battery.run_battery(CONFIG, tmp_path)
    seeds = CONFIG["seeds"]
    for seed in seeds:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        # 81 anomalous_price (80 Benford-eligible + 1 IsolationForest-only
        # duration supplier) + 2 concentration + 1 anomalous_duration
        assert len(lines) == 84
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-01b"


def test_anomalous_price_components_in_details() -> None:
    """anomalous_price details carry the Benford/IsolationForest components."""
    contracts, meta = battery.generate_contracts(CONFIG, seed=11)
    signals = battery.detect_fraud_signals(contracts)
    by_entity = {
        s["entity_id"]: json.loads(s["details"])
        for s in signals
        if s["signal_type"] == "anomalous_price"
    }
    # Benford-eligible suppliers (20 contracts) carry both components
    control = by_entity[meta["control_suppliers"][0]]
    assert control["benford_deviation"] is not None
    assert control["isolation_forest_rate"] is not None
    # The duration supplier (null amounts) is IsolationForest-only
    duration = by_entity[meta["duration_outlier_supplier"]]
    assert duration["benford_deviation"] is None
    assert duration["isolation_forest_rate"] is not None


def test_no_single_bid_in_synthetic_population() -> None:
    """All synthetic contracts are modality 'pregao': single_bid never fires."""
    contracts, _ = battery.generate_contracts(CONFIG, seed=11)
    signals = battery.detect_fraud_signals(contracts)
    assert not [s for s in signals if s["signal_type"] == "single_bid"]


def test_evaluate_detects_refutation() -> None:
    """A tampered HHI value refutes P4 and the battery."""
    contracts, meta = battery.generate_contracts(CONFIG, seed=11)
    signals = battery.detect_fraud_signals(contracts)
    for signal in signals:
        if signal["entity_id"] == "EQ4":
            signal["score"] = 0.9999
    record = {"seed": 11, "contracts": contracts, "signals": signals, "meta": meta}
    summary = battery.evaluate(CONFIG, [record])
    assert summary["predictions"]["P4"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
