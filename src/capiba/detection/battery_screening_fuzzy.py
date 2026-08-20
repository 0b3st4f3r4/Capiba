"""Detection battery runner (bateria D-06b).

Responsibility: Generate the planted fuzzy-screening cases F1-F9 plus
control suppliers (per the declarative config
``experiments/detect/D-06b.json``), compute the signals with
``capiba.detection.screening_fuzzy`` in-process and evaluate the
pre-registered predictions P1-P5 (synthetic regime) and P6-P7
(OpenSanctions Pairs benchmark, name-only regime, deterministic reservoir
sample — ``docs/preregistrations/PR-D-06b.md``). P8 (structural invariant
over the real gold) is verified after the integration — outside this
runner.

Doctrine: no battery without a pre-registration. The config is the
single source of parameters (seeds included); raw outputs are versioned
under ``results/detect/<id>/``.
"""

from __future__ import annotations

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from capiba.detection.battery_entities import sample_os_pairs
from capiba.detection.entities import name_similarity
from capiba.detection.screening_fuzzy import sanctioned_name_match_signals

_BASE = date(2026, 1, 1)


def _document(rng: random.Random, digits: int) -> str:
    """Draws a random numeric document (synthetic, validity irrelevant)."""
    return "".join(str(rng.randint(0, 9)) for _ in range(digits))


def _mask(cpf_digits: str) -> str:
    """Masks an 11-digit CPF the CEAF/RFB way (``***123456**``)."""
    return f"***{cpf_digits[3:9]}**"


def _other_masked(rng: random.Random, masked: str) -> str:
    """Draws a masked document whose visible digits differ from ``masked``."""
    while True:
        candidate = _mask(_document(rng, 11))
        if candidate != masked:
            return candidate


def _contract(
    rng: random.Random,
    case: str,
    name: str,
    cpf: str | None,
    signed: date,
) -> dict[str, Any]:
    supplier: dict[str, Any] = {"legal_name": name}
    if cpf:
        supplier["cpf"] = cpf
    return {
        "id": f"SYN-D06B-{case}-{rng.randrange(10**6)}",
        "signature_date": signed.isoformat(),
        "supplier": supplier,
        "buyer": {"siafi_code": "900000"},
        "amount": 1000.0,
    }


def _sanction(
    case: str,
    name: str,
    list_name: str = "ceaf",
    masked: str | None = None,
    cpf: str | None = None,
    start: date | None = _BASE,
    end: date | None = None,
) -> dict[str, Any]:
    return {
        "id": f"{list_name}-{case}",
        "list_name": list_name,
        "cnpj": None,
        "cpf": cpf,
        "masked_document": masked,
        "sanctioned_name": name,
        "start_date": start.isoformat() if start else None,
        "end_date": end.isoformat() if end else None,
    }


def generate_population(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Builds the synthetic contracts and sanctions for one seed.

    The case names are structurally meaningful (they fix the similarity
    bands), so the seed only randomizes neutral fields (documents, base
    dates, contract ids) — the case structure is fixed by the
    pre-registration (PR-D-06b, section 4).

    Returns:
        ``contracts``, ``sanctions`` and the ``meta`` ground truth
        (case id -> expected supplier entity id).
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    base = _BASE + timedelta(days=rng.randint(0, 60))
    signed = base + timedelta(days=30)

    contracts: list[dict[str, Any]] = []
    sanctions: list[dict[str, Any]] = []
    meta: dict[str, str] = {}

    # Each case gets a DISTINCT name (cross-case similarity <= 0.56, below
    # every regime's reach) so cases cannot contaminate each other.
    name_f1 = "MARIA DE FATIMA PEREIRA"
    name_f2 = "JOSE CARLOS SANTOS"
    name_f3 = "ANA PAULA OLIVEIRA"
    name_f4a, name_f4b = "JORGE HENRIQUE AMORIM", "LUCAS FERREIRA COSTA"
    name_f5 = "FERNANDA LIMA ROCHA"
    name_f6a, name_f6b = "PAULA MENDES TEIXEIRA", "PAULA MENDES TEIXEIRA SILVA"
    name_f7 = "RICARDO ALMEIDA BARROS"
    name_f8 = "CAMILA RODRIGUES PINTO"
    name_f9 = "BRUNO CARDOSO MELLO"

    # F1 — CEAF: identical name, compatible masked CPF -> signal.
    cpf1 = _document(rng, 11)
    contracts.append(_contract(rng, "F1", name_f1, cpf1, signed))
    sanctions.append(_sanction("F1", name_f1, masked=_mask(cpf1), start=base))
    meta["F1"] = cpf1

    # F2 — CEAF: noisy name (accent/case/order), same masked CPF -> signal.
    cpf2 = _document(rng, 11)
    contracts.append(_contract(rng, "F2", "Santos, José Carlos", cpf2, signed))
    sanctions.append(_sanction("F2", name_f2, masked=_mask(cpf2), start=base))
    meta["F2"] = cpf2

    # F3 — homonym: identical name, contradictory masked CPF -> veto.
    cpf3 = _document(rng, 11)
    contracts.append(_contract(rng, "F3", name_f3, cpf3, signed))
    sanctions.append(
        _sanction("F3", name_f3, masked=_other_masked(rng, _mask(cpf3)), start=base)
    )
    meta["F3"] = cpf3

    # F4 — compatible masked CPF, disjoint names (sim 0.24) -> no signal.
    cpf4 = _document(rng, 11)
    contracts.append(_contract(rng, "F4", name_f4a, cpf4, signed))
    sanctions.append(_sanction("F4", name_f4b, masked=_mask(cpf4), start=base))
    meta["F4"] = cpf4

    # F5 — name-only (no document either side), identical name -> signal.
    contracts.append(_contract(rng, "F5", name_f5, None, signed))
    sanctions.append(_sanction("F5", name_f5, masked=None, start=base))
    meta["F5"] = name_f5

    # F6 — name-only, similarity 0.875 in [0.85, 0.95) -> no signal.
    contracts.append(_contract(rng, "F6", name_f6b, None, signed))
    sanctions.append(_sanction("F6", name_f6a, masked=None, start=base))
    meta["F6"] = name_f6b

    # F7 — CEIS: full CPF divergent, identical name -> veto.
    cpf7 = _document(rng, 11)
    contracts.append(_contract(rng, "F7", name_f7, cpf7, signed))
    sanctions.append(
        _sanction("F7", name_f7, list_name="ceis", cpf=_document(rng, 11), start=base)
    )
    meta["F7"] = cpf7

    # F8 — compatible masked CPF, sanction NOT vigent at signature -> no signal.
    cpf8 = _document(rng, 11)
    contracts.append(_contract(rng, "F8", name_f8, cpf8, signed))
    sanctions.append(
        _sanction("F8", name_f8, masked=_mask(cpf8), start=signed + timedelta(days=1))
    )
    meta["F8"] = cpf8

    # F9 — exact document match on the sanction -> fuzzy suppressed.
    cpf9 = _document(rng, 11)
    contracts.append(_contract(rng, "F9", name_f9, cpf9, signed))
    sanctions.append(_sanction("F9", name_f9, list_name="ceis", cpf=cpf9, start=base))
    meta["F9"] = cpf9

    # Control suppliers: contracts without any sanction.
    for i in range(config["control_suppliers"]):
        contracts.append(
            _contract(rng, f"CTRL-{i:02d}", f"Controle {i}", _document(rng, 11), signed)
        )

    return {"contracts": contracts, "sanctions": sanctions, "meta": meta}


def _compute(config: dict[str, Any], population: dict[str, Any]) -> list[dict[str, Any]]:
    """Computes the fuzzy screening signals over one generated population."""
    return sanctioned_name_match_signals(
        population["contracts"],
        population["sanctions"],
        name_weight=config["weights"]["name"],
        document_weight=config["weights"]["masked_document"],
        doc_assisted_threshold=config["thresholds"]["doc_assisted"],
        name_only_threshold=config["thresholds"]["name_only"],
    )


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P5)."""
    signals = _compute(config, generate_population(config, seed))
    repeat = _compute(config, generate_population(config, seed))
    divergences = int(signals != repeat)
    return {"seed": seed, "signals": signals, "repeat_divergences": divergences}


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P5 over the records."""
    expected = config["expected"]
    signal_cases = sorted(expected["signal_cases"])

    failures: dict[str, list[str]] = {f"P{i}": [] for i in range(1, 6)}

    for record in records:
        seed = record["seed"]
        if record["repeat_divergences"]:
            failures["P5"].append(f"seed {seed}: repeat diverged")
        population = generate_population(config, seed)
        meta = population["meta"]
        case_by_entity = {entity: case for case, entity in meta.items()}
        signaled_cases = sorted(
            case_by_entity[s["entity_id"]]
            for s in record["signals"]
            if s["entity_id"] in case_by_entity
        )

        # P1 — exact signal set (F1, F2, F5; controls never signal)
        if signaled_cases != signal_cases or len(record["signals"]) != len(
            signal_cases
        ):
            failures["P1"].append(
                f"seed {seed}: signaled {signaled_cases} != {signal_cases}"
            )

        # P2 — document veto (F3 masked contradiction, F7 full contradiction)
        for case_id in ("F3", "F7"):
            if meta[case_id] in {s["entity_id"] for s in record["signals"]}:
                failures["P2"].append(f"seed {seed} {case_id}: veto violated")

        # P3 — name-noise robustness (F2 signals)
        if meta["F2"] not in {s["entity_id"] for s in record["signals"]}:
            failures["P3"].append(f"seed {seed} F2: not signaled")

        # P4 — name-only threshold (F5 signals, F6 does not)
        signaled_ids = {s["entity_id"] for s in record["signals"]}
        if meta["F5"] not in signaled_ids or meta["F6"] in signaled_ids:
            failures["P4"].append(f"seed {seed}: name-only threshold inverted")

    predictions: dict[str, dict[str, Any]] = {
        name: {"verdict": "refuted" if fails else "success", "failures": fails}
        for name, fails in failures.items()
    }
    verdict = (
        "success"
        if all(p["verdict"] == "success" for p in predictions.values())
        else "refuted"
    )
    return {"battery": config["id"], "predictions": predictions, "verdict": verdict}


def evaluate_os_pairs(config: dict[str, Any], sample: list[dict[str, Any]]) -> dict[str, Any]:
    """Scores the OS Pairs sample with the name-only regime (P6/P7)."""
    threshold = config["thresholds"]["name_only"]
    tp = fp = fn = tn = 0
    for row in sample:
        left = (row["left"].get("properties", {}).get("name") or [None])[0]
        right = (row["right"].get("properties", {}).get("name") or [None])[0]
        predicted = bool(left and right) and name_similarity(left, right) >= threshold
        positive = row["judgement"] == "positive"
        if predicted and positive:
            tp += 1
        elif predicted:
            fp += 1
        elif positive:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    spec = config["os_pairs"]
    low, high = spec["recall_band"]
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "p6": {
            "verdict": "success" if precision >= spec["min_precision"] else "refuted"
        },
        "p7": {"verdict": "success" if low <= recall <= high else "refuted"},
    }


def run_battery(config: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Runs the battery: synthetic seeds + OS Pairs name-only benchmark.

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs (``seed_<n>.jsonl``),
            the cached ``pairs_sample.jsonl`` and ``summary.json``.

    Returns:
        The per-seed records (signals, repeat divergences).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for seed in config["seeds"]:
        record = run_seed(config, seed)
        with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
            for signal in record["signals"]:
                fh.write(json.dumps(signal, default=str) + "\n")
        records.append(record)

    summary = evaluate(config, records)
    os_metrics = evaluate_os_pairs(
        config,
        sample_os_pairs(
            config, out_dir / "pairs_sample.jsonl", config["os_pairs"]["seed"]
        ),
    )
    summary["os_pairs"] = os_metrics
    summary["predictions"]["P6"] = {
        "verdict": os_metrics["p6"]["verdict"],
        "precision": os_metrics["precision"],
    }
    summary["predictions"]["P7"] = {
        "verdict": os_metrics["p7"]["verdict"],
        "recall": os_metrics["recall"],
    }
    if summary["predictions"]["P6"]["verdict"] != "success" or (
        summary["predictions"]["P7"]["verdict"] != "success"
    ):
        summary["verdict"] = "refuted"
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return records
