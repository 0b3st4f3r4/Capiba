"""Detection battery runner (bateria D-01).

Responsibility: Generate synthetic silver-contract populations with
planted ground truth (per the declarative configs in
``experiments/detect/``), invoke ``detect_fraud_signals`` in-process and
evaluate the pre-registered predictions (``docs/preregistrations/``).

Doctrine: no battery without a pre-registration. The config is the
single source of parameters (seeds included); raw outputs are versioned
under ``results/detect/<id>/``.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from capiba.detection.signals import (
    MIN_BENFORD_AMOUNTS,
    MIN_ISOLATION_FOREST_CONTRACTS,
    SignalType,
)
from capiba.pipeline.detect_task import detect_fraud_signals

_BASE = date(2026, 1, 1)


def _buyer_supplier_counts(buyer_spec: dict[str, Any]) -> list[int]:
    """Splits a buyer's contracts among its suppliers (mirrors the generator)."""
    counts = [round(buyer_spec["contracts"] * share) for share in buyer_spec["supplier_shares"]]
    counts[-1] += buyer_spec["contracts"] - sum(counts)  # exact split despite rounding
    return counts


def _expected_signal_counts(config: dict[str, Any]) -> dict[SignalType, int]:
    """Derives the expected per-seed signal counts from the eligibility rules.

    ``anomalous_price``: one signal per supplier eligible to any component —
    Benford (>= MIN_BENFORD_AMOUNTS amounts) or IsolationForest (>=
    MIN_ISOLATION_FOREST_CONTRACTS contracts). Buyer suppliers always carry
    an amount (1000.0); the planted duration supplier carries null amounts,
    so it is eligible only via IsolationForest. ``concentration``: one per
    buyer. ``single_bid``: none — the synthetic population is all modality
    "pregao" (rate 0, never emitted).
    """
    n_price = sum(
        spec["count"]
        for spec in config["suppliers"].values()
        if spec["contracts_per_supplier"] >= MIN_BENFORD_AMOUNTS
    )
    for buyer in config["buyers"]:
        n_price += sum(
            1
            for count in _buyer_supplier_counts(buyer)
            if count >= MIN_BENFORD_AMOUNTS or count >= MIN_ISOLATION_FOREST_CONTRACTS
        )
    if config["durations"]["planted"]["contracts"] >= MIN_ISOLATION_FOREST_CONTRACTS:
        n_price += 1  # duration supplier: IsolationForest-only (null amounts)
    return {
        SignalType.ANOMALOUS_PRICE: n_price,
        SignalType.CONCENTRATION: len(config["buyers"]),
        SignalType.SINGLE_BID: 0,
    }


def _log_uniform(rng: random.Random, low: float, high: float) -> float:
    """Draws an amount whose leading digits follow Benford's Law."""
    return round(math.exp(rng.uniform(math.log(low), math.log(high))), 2)


def _leading_digit(rng: random.Random, digit: int, exp_range: list[int]) -> float:
    """Draws an amount whose leading digit is the given one."""
    exponent = rng.randint(exp_range[0], exp_range[1])
    return float(round((digit + rng.uniform(0.0, 1.0)) * 10**exponent, 2))


def _amount(rng: random.Random, spec: dict[str, Any]) -> float:
    """Draws an amount from a distribution spec (single or mixture)."""
    mixture = spec.get("mixture")
    if mixture:
        pick = rng.random()
        cumulative = 0.0
        for component in mixture:
            cumulative += component["weight"]
            if pick < cumulative:
                return _amount(rng, component)
        return _amount(rng, mixture[-1])
    if spec["distribution"] == "log_uniform":
        return _log_uniform(rng, spec["min"], spec["max"])
    if spec["distribution"] == "leading_digit":
        return _leading_digit(rng, spec["digit"], spec["exponent_range"])
    raise ValueError(f"Unknown distribution: {spec['distribution']}")


def _contract(
    seq: int,
    supplier_id: str,
    amount: float | None,
    duration_days: int,
    buyer: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds one synthetic silver contract row (dict form)."""
    start = _BASE + timedelta(days=seq % 28)
    return {
        "id": f"SYN-{seq:05d}",
        "process_number": f"P-{seq:05d}",
        "subject": "Contrato sintético de bateria",
        "amount": amount,
        "signature_date": start.isoformat(),
        "validity_start": start.isoformat(),
        "validity_end": (start + timedelta(days=duration_days)).isoformat(),
        "buyer": buyer,
        "supplier": {"cnpj": supplier_id, "legal_name": f"Fornecedor {supplier_id}"},
        "modality": "pregao",
        "status": "concluido",
    }


def generate_contracts(
    config: dict[str, Any], seed: int
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Generates the synthetic contract population for one seed.

    Suppliers: ``control`` (log-uniform amounts, Benford-conformant),
    ``planted_benford`` (mixture with a manipulated leading digit), the
    per-buyer HHI suppliers (< 10 contracts each, so Benford-ineligible)
    and the duration outlier supplier (null amounts, Benford-ineligible).

    Args:
        config: Battery configuration (see ``experiments/detect/``).
        seed: RNG seed (deterministic per seed).

    Returns:
        (contracts, meta): the contract rows and the ground-truth
        metadata (supplier groups, buyer ids, duration outlier id).
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    contracts: list[dict[str, Any]] = []
    seq = 0

    def _duration() -> int:
        spec = config["durations"]["control"]
        return rng.randint(spec["min_days"], spec["max_days"])

    def _populate(prefix: int, spec: dict[str, Any]) -> list[str]:
        nonlocal seq
        ids = []
        for i in range(spec["count"]):
            supplier_id = str(prefix + i)
            ids.append(supplier_id)
            for _ in range(spec["contracts_per_supplier"]):
                seq += 1
                contracts.append(
                    _contract(
                        seq, supplier_id, _amount(rng, spec["amounts"]), _duration()
                    )
                )
        return ids

    control = _populate(10_000_000_000_000, config["suppliers"]["control"])
    planted = _populate(20_000_000_000_000, config["suppliers"]["planted_benford"])

    buyers_meta: list[str] = []
    for b_index, buyer_spec in enumerate(config["buyers"]):
        buyer = {
            "siafi_code": buyer_spec["id"],
            "name": f"Comprador {buyer_spec['id']}",
            "government_level": "municipal",
            "uf": "MG",
        }
        buyers_meta.append(buyer_spec["id"])
        shares = buyer_spec["supplier_shares"]
        total = buyer_spec["contracts"]
        counts = [round(total * share) for share in shares]
        counts[-1] += total - sum(counts)  # exact split despite rounding
        for s_index, count in enumerate(counts):
            supplier_id = str(30_000_000_000_000 + 10_000 * b_index + s_index)
            for _ in range(count):
                seq += 1
                contracts.append(
                    _contract(seq, supplier_id, 1000.0, _duration(), buyer=buyer)
                )

    planted_duration = config["durations"]["planted"]
    dur_supplier = str(50_000_000_000_000)
    outlier_positions = set(
        rng.sample(
            range(planted_duration["contracts"]), planted_duration["outlier_contracts"]
        )
    )
    for i in range(planted_duration["contracts"]):
        seq += 1
        days = (
            planted_duration["outlier_days"] if i in outlier_positions else _duration()
        )
        contracts.append(_contract(seq, dur_supplier, None, days))

    meta = {
        "control_suppliers": control,
        "planted_suppliers": planted,
        "buyers": buyers_meta,
        "duration_outlier_supplier": dur_supplier,
    }
    return contracts, meta


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions over the battery records.

    Args:
        config: Battery configuration (expectations included).
        records: Per-seed records with ``contracts``, ``signals`` and
            ``meta`` (as produced by ``run_battery``).

    Returns:
        Summary with a verdict per prediction (``success``/``refuted``)
        and the overall battery verdict.
    """
    exp = config["expectations"]
    expected_counts = _expected_signal_counts(config)
    n_buyers = len(config["buyers"])
    threshold = exp["benford_fp_threshold"]

    p1_failures: list[str] = []
    p4_failures: list[str] = []
    p5_failures: list[str] = []
    control_fp = 0
    planted_hits = 0

    for record in records:
        seed = record["seed"]
        meta = record["meta"]
        signals = record["signals"]
        by_type: dict[str, list[dict[str, Any]]] = {}
        for signal in signals:
            by_type.setdefault(signal["signal_type"], []).append(signal)

        # P1 — conservation of the signal count per type
        price = by_type.get(SignalType.ANOMALOUS_PRICE, [])
        hhi = by_type.get(SignalType.CONCENTRATION, [])
        duration = by_type.get(SignalType.ANOMALOUS_DURATION, [])
        single = by_type.get(SignalType.SINGLE_BID, [])
        if len(price) != expected_counts[SignalType.ANOMALOUS_PRICE]:
            p1_failures.append(
                f"seed {seed}: {len(price)} anomalous_price"
                f" != {expected_counts[SignalType.ANOMALOUS_PRICE]}"
            )
        if len(hhi) != n_buyers:
            p1_failures.append(f"seed {seed}: {len(hhi)} hhi != {n_buyers}")
        if len(single) != expected_counts[SignalType.SINGLE_BID]:
            p1_failures.append(f"seed {seed}: {len(single)} unexpected single_bid")
        if any(s["entity_id"] != meta["duration_outlier_supplier"] for s in duration):
            p1_failures.append(f"seed {seed}: duration signal from wrong supplier")
        if len(signals) != (
            expected_counts[SignalType.ANOMALOUS_PRICE]
            + n_buyers
            + len(duration)
            + len(single)
        ):
            p1_failures.append(f"seed {seed}: total {len(signals)}")

        # P2/P3 — Benford calibration and power (component of anomalous_price)
        control_ids = set(meta["control_suppliers"])
        planted_ids = set(meta["planted_suppliers"])
        for signal in price:
            benford_dev = json.loads(signal["details"]).get("benford_deviation")
            if benford_dev is None:
                continue  # IsolationForest-only signal (duration supplier)
            if signal["entity_id"] in control_ids:
                control_fp += int(benford_dev >= threshold)
            elif signal["entity_id"] in planted_ids:
                planted_hits += int(benford_dev >= threshold)

        # P4 — exact HHI anchors
        hhi_by_buyer = {s["entity_id"]: s["score"] for s in hhi}
        for buyer_id, expected in exp["hhi_exact"].items():
            if hhi_by_buyer.get(buyer_id) != expected:
                p4_failures.append(
                    f"seed {seed}: {buyer_id} hhi {hhi_by_buyer.get(buyer_id)}"
                    f" != {expected}"
                )

        # P5 — duration outlier share
        dur = [
            s for s in duration if s["entity_id"] == meta["duration_outlier_supplier"]
        ]
        if len(dur) != 1 or dur[0]["score"] != exp["duration_share_exact"]:
            p5_failures.append(f"seed {seed}: duration share {dur}")

    n_control_cells = len(records) * config["suppliers"]["control"]["count"]
    low, high = exp["benford_fp_band"]
    predictions = {
        "P1": {
            "verdict": "refuted" if p1_failures else "success",
            "failures": p1_failures,
        },
        "P2": {
            "verdict": "success" if low <= control_fp <= high else "refuted",
            "false_positives": control_fp,
            "control_cells": n_control_cells,
            "band": [low, high],
        },
        "P3": {
            "verdict": (
                "success" if planted_hits >= exp["benford_power_min"] else "refuted"
            ),
            "planted_hits": planted_hits,
            "planted_cells": len(records)
            * config["suppliers"]["planted_benford"]["count"],
            "minimum": exp["benford_power_min"],
        },
        "P4": {
            "verdict": "refuted" if p4_failures else "success",
            "failures": p4_failures,
        },
        "P5": {
            "verdict": "refuted" if p5_failures else "success",
            "failures": p5_failures,
        },
    }
    verdict = (
        "success"
        if all(p["verdict"] == "success" for p in predictions.values())
        else "refuted"
    )
    return {"battery": config["id"], "predictions": predictions, "verdict": verdict}


def run_battery(config: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Runs the battery: generate, detect, persist raw outputs and summary.

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs (``seed_<n>.jsonl``)
            and ``summary.json``.

    Returns:
        The per-seed records (contracts, signals, meta).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for seed in config["seeds"]:
        contracts, meta = generate_contracts(config, seed)
        signals = detect_fraud_signals(contracts)
        record = {
            "seed": seed,
            "contracts": contracts,
            "signals": signals,
            "meta": meta,
        }
        with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
            for signal in signals:
                fh.write(json.dumps(signal, default=str) + "\n")
        records.append(record)
    summary = evaluate(config, records)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return records
