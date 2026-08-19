"""Detection battery runner (bateria D-06).

Responsibility: Generate the planted screening cases S1-S10 plus control
suppliers (per the declarative config ``experiments/detect/D-06.json``),
compute the signals with ``capiba.detection.screening`` in-process and
evaluate the pre-registered predictions P1-P5
(``docs/preregistrations/PR-D-06.md``). The real-data invariants P6-P7
are verified by queries over the gold/silver after the backfill — outside
this runner.

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

from capiba.detection.screening import sanctioned_supplier_signals

_BASE = date(2026, 1, 1)


def _document(rng: random.Random, digits: int) -> str:
    """Draws a random numeric document (synthetic, validity irrelevant)."""
    return "".join(str(rng.randint(0, 9)) for _ in range(digits))


def generate_population(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Builds the synthetic contracts and sanctions for one seed.

    The seed only randomizes neutral fields (documents, names, base
    dates, amounts); the case structure (vigence offsets, document
    discipline) is fixed by the config.

    Args:
        config: Battery configuration (``experiments/detect/D-06.json``).
        seed: RNG seed (deterministic per seed).

    Returns:
        ``contracts``, ``sanctions`` and the ``meta`` ground truth
        (case id -> supplier document).
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    contracts: list[dict[str, Any]] = []
    sanctions: list[dict[str, Any]] = []
    meta: dict[str, str] = {}

    for index, spec in enumerate(config["cases"]):
        base = _BASE + timedelta(days=rng.randint(0, 60))
        doc_type = spec["doc_type"]
        cnpj = _document(rng, 14) if doc_type == "cnpj" else None
        cpf = _document(rng, 11) if doc_type == "cpf" else None
        name = f"Fornecedor {spec['id']} SA"
        supplier: dict[str, Any] = {"legal_name": name, "cnpj": cnpj, "cpf": cpf}
        document = cnpj or cpf
        meta[spec["id"]] = document or f"no-doc-{spec['id']}"

        signed = base + timedelta(days=spec["signature_offset_days"])
        contracts.append(
            {
                "id": f"SYN-D06-{seed}-{spec['id']}",
                "signature_date": signed.isoformat(),
                "supplier": supplier,
                "buyer": {"siafi_code": "900000"},
                "amount": 1000.0,
            }
        )

        for sanc in spec["sanctions"]:
            # S7: the sanction targets ANOTHER document of the same name.
            sanc_cnpj: str | None
            sanc_cpf: str | None
            if sanc.get("other_document"):
                sanc_cnpj = _document(rng, 14)
                sanc_cpf = None
            else:
                sanc_cnpj, sanc_cpf = cnpj, cpf
            start_offset = sanc["start_offset_days"]
            end_offset = sanc["end_offset_days"]
            sanctions.append(
                {
                    "id": f"{sanc['list_name']}-{spec['id']}-{sanc['id_suffix']}",
                    "list_name": sanc["list_name"],
                    "cnpj": sanc_cnpj,
                    "cpf": sanc_cpf,
                    "sanctioned_name": name,
                    "start_date": (
                        (base + timedelta(days=start_offset)).isoformat()
                        if start_offset is not None
                        else None
                    ),
                    "end_date": (
                        (base + timedelta(days=end_offset)).isoformat()
                        if end_offset is not None
                        else None
                    ),
                }
            )

    # Control suppliers: contracts without any sanction.
    for i in range(config["control_suppliers"]):
        contracts.append(
            {
                "id": f"SYN-D06-{seed}-CTRL-{i:02d}",
                "signature_date": _BASE.isoformat(),
                "supplier": {
                    "legal_name": f"Controle {i}",
                    "cnpj": _document(rng, 14),
                    "cpf": None,
                },
                "buyer": {"siafi_code": "900000"},
                "amount": 1000.0,
            }
        )

    return {"contracts": contracts, "sanctions": sanctions, "meta": meta}


def _compute(population: dict[str, Any]) -> list[dict[str, Any]]:
    """Computes the screening signals over one generated population."""
    return sanctioned_supplier_signals(population["contracts"], population["sanctions"])


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P5)."""
    signals = _compute(generate_population(config, seed))
    repeat = _compute(generate_population(config, seed))
    divergences = int(signals != repeat)
    return {"seed": seed, "signals": signals, "repeat_divergences": divergences}


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P5 over the records."""
    expected_signal = {
        case["id"]: case["expected"]["signal"] for case in config["cases"]
    }
    expected_signaled = sorted(
        case_id for case_id, signaled in expected_signal.items() if signaled
    )

    p1_failures: list[str] = []
    boundary_failures: dict[str, list[str]] = {"P2": [], "P3": []}
    p4_failures: list[str] = []
    p5_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        if record["repeat_divergences"]:
            p5_failures.append(f"seed {seed}: repeat diverged")
        population = generate_population(config, seed)
        meta = population["meta"]
        case_by_doc = {doc: case_id for case_id, doc in meta.items()}
        signaled_docs = {s["entity_id"] for s in record["signals"]}
        signaled_cases = sorted(
            case_by_doc[doc] for doc in signaled_docs if doc in case_by_doc
        )

        # P1 — exact signal set and binary score
        if signaled_cases != expected_signaled:
            p1_failures.append(
                f"seed {seed}: signaled {signaled_cases} != {expected_signaled}"
            )
        if len(record["signals"]) != len(expected_signaled):
            p1_failures.append(
                f"seed {seed}: {len(record['signals'])} signals"
                f" != {len(expected_signaled)}"
            )
        for signal in record["signals"]:
            if signal["score"] != 1.0:
                p1_failures.append(
                    f"seed {seed} {signal['entity_id']}: score {signal['score']} != 1.0"
                )

        # P2 — vigence boundaries (S2 no, S3 yes, S4 no, S5 yes)
        for case_id, want in (("S2", False), ("S3", True), ("S4", False), ("S5", True)):
            got = meta[case_id] in signaled_docs
            if got != want:
                boundary_failures["P2"].append(
                    f"seed {seed} {case_id}: signaled={got}, expected={want}"
                )

        # P3 — document discipline (S7 same name, S8 no document)
        for case_id in ("S7", "S8"):
            if meta[case_id] in signaled_docs:
                boundary_failures["P3"].append(f"seed {seed} {case_id}: signaled")

        # P4 — S9 details list only the vigent sanction
        s9 = [s for s in record["signals"] if s["entity_id"] == meta["S9"]]
        if len(s9) == 1:
            listed = json.loads(s9[0]["details"])["sanctions"]
            want_ids = [
                f"cnep-S9-{suffix}" for suffix in ("CURRENT",)
            ]
            if listed != want_ids:
                p4_failures.append(f"seed {seed} S9: details {listed} != {want_ids}")
        else:
            p4_failures.append(f"seed {seed} S9: {len(s9)} signals != 1")

    predictions: dict[str, dict[str, Any]] = {
        "P1": {
            "verdict": "refuted" if p1_failures else "success",
            "failures": p1_failures,
        },
        "P2": {
            "verdict": "refuted" if boundary_failures["P2"] else "success",
            "failures": boundary_failures["P2"],
        },
        "P3": {
            "verdict": "refuted" if boundary_failures["P3"] else "success",
            "failures": boundary_failures["P3"],
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
    """Runs the battery: generate, compute, persist raw outputs and summary.

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs (``seed_<n>.jsonl``)
            and ``summary.json``.

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
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return records
