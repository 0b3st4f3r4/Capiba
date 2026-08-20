"""Detection battery runner (bateria D-08).

Responsibility: Generate the planted political-connection cases E1-E10
plus the disjoint donor-supplier control pairs (per the declarative
config ``experiments/detect/D-08.json``), compute the
``political_connection`` signals with ``capiba.detection.political``
in-process over synthetic silver rows and evaluate the pre-registered
predictions P1-P7 (``docs/preregistrations/PR-D-08.md``). P8 (structural
invariant over the real gold) is verified after the integration, by the
singular dbt tests — outside this runner.

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

from capiba.detection.political import political_connection_signals

_UF = "PE"
_IN_WINDOW_START = date(2025, 2, 1)  # signature dates spread from here
_PRE_WINDOW_START = date(2024, 3, 1)  # E2 signs before the inauguration


def _document(rng: random.Random, digits: int) -> str:
    """Draws a random numeric document (synthetic, validity irrelevant)."""
    return "".join(str(rng.randint(0, 9)) for _ in range(digits))


def _candidacy(
    seq: str,
    city: str,
    office: str = "Prefeito",
    status: str = "Eleito",
    year: int = 2024,
) -> dict[str, Any]:
    return {
        "election_year": year,
        "candidate_sequential": seq,
        "candidate_name": f"CANDIDATO {city}",
        "party": "XX",
        "office": office,
        "ue_name": city,
        "uf": _UF,
        "totalization_status": status,
    }


def _donation(
    document: str,
    amount: float,
    seq: str,
    origin_document: str | None = None,
    year: int = 2024,
) -> dict[str, Any]:
    return {
        "election_year": year,
        "donor_document": document,
        "donor_name": f"DOADOR {document[-4:]}",
        "donor_origin_document": origin_document,
        "donation_date": date(2024, 8, 1).isoformat(),
        "amount": amount,
        "candidate_sequential": seq,
    }


def _contract(
    rng: random.Random,
    case: str,
    city: str,
    document: str,
    amount: float,
    signed: date,
) -> dict[str, Any]:
    supplier: dict[str, Any] = {"name": f"FORNECEDOR {case}"}
    if len(document) == 14:
        supplier["cnpj"] = document
    else:
        supplier["cpf"] = document
    return {
        "id": f"SYN-D08-{case}-{rng.randrange(10**6)}",
        "buyer": {
            # Deterministic across processes (no hash(), which is salted).
            "siafi_code": f"9{sum(ord(c) for c in city) % 100000:05d}",
            "name": f"PREFEITURA DE {city}",
            "city": city,
            "uf": _UF,
        },
        "supplier": supplier,
        "signature_date": signed.isoformat(),
        "amount": amount,
    }


def _share_amounts(share: float) -> tuple[float, float]:
    """Supplier and filler amounts totaling 100k at exactly ``share``."""
    supplier = round(100_000.0 * share, 2)
    return supplier, round(100_000.0 - supplier, 2)


def generate_population(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Builds the synthetic silver rows for one seed.

    The case structure is fixed by the pre-registration (PR-D-08 § 4):
    each case has its own municipality (so shares cannot contaminate each
    other) and the seed only randomizes neutral fields — documents,
    candidate sequentials, contract ids and signature dates.

    Returns:
        ``donations``, ``contracts``, ``candidacies`` and the ``meta``
        ground truth (case id -> expected donor/supplier entity id).
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    year = config["source"]["election_year"]
    office = config["source"]["office"]
    thresholds = config["thresholds"]
    mandate_start = date.fromisoformat(thresholds["mandate_start"])
    # Randomized-but-in-window signature date (neutral field).
    signed_in = mandate_start + timedelta(days=rng.randint(31, 300))
    signed_pre = _PRE_WINDOW_START + timedelta(days=rng.randint(0, 120))

    donations: list[dict[str, Any]] = []
    contracts: list[dict[str, Any]] = []
    candidacies: list[dict[str, Any]] = []
    meta: dict[str, str] = {}

    def plant(
        case: str,
        *,
        donation_amount: float,
        share: float,
        status: str = "Eleito",
        case_office: str = office.title(),
        digits: int = 14,
        signed: date = signed_in,
        via_party: bool = False,
        divergent_doc: bool = False,
    ) -> None:
        city = f"CIDADE {case}"
        seq = str(rng.randrange(10**5, 10**6))
        donor_doc = _document(rng, digits)
        supplier_doc = _document(rng, digits) if divergent_doc else donor_doc
        candidacies.append(
            _candidacy(seq, city, office=case_office, status=status, year=year)
        )
        if via_party:
            donations.append(
                _donation(
                    _document(rng, 14),  # party directory document
                    donation_amount,
                    seq,
                    origin_document=donor_doc,
                    year=year,
                )
            )
        else:
            donations.append(_donation(donor_doc, donation_amount, seq, year=year))
        supplier_amount, filler_amount = _share_amounts(share)
        contracts.append(
            _contract(rng, case, city, supplier_doc, supplier_amount, signed)
        )
        contracts.append(
            _contract(rng, f"{case}-FILL", city, _document(rng, 14), filler_amount, signed)
        )
        meta[case] = donor_doc

    # E1 — PJ donor, elected mayor, share 0.40 -> signal, score 1.0.
    plant("E1", donation_amount=50_000.0, share=0.40)
    # E2 — contract signed before the inauguration -> no signal.
    plant("E2", donation_amount=50_000.0, share=0.40, signed=signed_pre)
    # E3 — defeated candidate -> no signal.
    plant("E3", donation_amount=50_000.0, share=0.40, status="Não eleito")
    # E4 — share 0.01 below the concentration gate -> no signal.
    plant("E4", donation_amount=50_000.0, share=0.01)
    # E5 — PF donor (CPF), share 0.125 -> signal, score 0.5 (anchor).
    plant("E5", donation_amount=20_000.0, share=0.125, digits=11)
    # E6 — donation 500 below the floor -> no signal.
    plant("E6", donation_amount=500.0, share=0.40)
    # E7 — elected councillor -> no signal (v1: executive only).
    plant(
        "E7",
        donation_amount=50_000.0,
        share=0.40,
        case_office="Vereador",
        status="Eleito por QP",
    )
    # E8 — same name, divergent documents -> no signal.
    plant("E8", donation_amount=50_000.0, share=0.40, divergent_doc=True)
    # E9 — donation via party; match on the origin donor -> signal.
    plant("E9", donation_amount=30_000.0, share=0.30, via_party=True)
    # E10 — share exactly 0.05 (inclusive boundary) -> signal, score 0.2.
    plant("E10", donation_amount=5_000.0, share=0.05)

    # Controls: disjoint donor-supplier pairs — the donor backs the elected
    # mayor of city A but supplies city B (municipality gate), so no pair
    # may signal.
    for i in range(config["control_pairs"]):
        city_a = f"CONTROLE A{i:02d}"
        city_b = f"CONTROLE B{i:02d}"
        seq_a = str(rng.randrange(10**5, 10**6))
        seq_b = str(rng.randrange(10**5, 10**6))
        donor_doc = _document(rng, 14)
        candidacies.append(_candidacy(seq_a, city_a, office=office.title(), year=year))
        candidacies.append(_candidacy(seq_b, city_b, office=office.title(), year=year))
        donations.append(_donation(donor_doc, 50_000.0, seq_a, year=year))
        supplier_amount, filler_amount = _share_amounts(0.40)
        contracts.append(
            _contract(rng, f"CTRL-{i:02d}", city_b, donor_doc, supplier_amount, signed_in)
        )
        contracts.append(
            _contract(
                rng, f"CTRL-{i:02d}-FILL", city_b, _document(rng, 14), filler_amount,
                signed_in,
            )
        )

    return {
        "donations": donations,
        "contracts": contracts,
        "candidacies": candidacies,
        "meta": meta,
    }


def _compute(config: dict[str, Any], population: dict[str, Any]) -> list[dict[str, Any]]:
    """Computes the political_connection signals over one population."""
    thresholds = config["thresholds"]
    return political_connection_signals(
        population["donations"],
        population["contracts"],
        population["candidacies"],
        min_donation_brl=thresholds["min_donation_brl"],
        min_supplier_share=thresholds["min_supplier_share"],
        score_share_reference=thresholds["score_share_reference"],
        mandate_start=date.fromisoformat(thresholds["mandate_start"]),
        mandate_end=date.fromisoformat(thresholds["mandate_end"]),
        office=config["source"]["office"],
    )


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P7)."""
    signals = _compute(config, generate_population(config, seed))
    repeat = _compute(config, generate_population(config, seed))
    divergences = int(signals != repeat)
    return {"seed": seed, "signals": signals, "repeat_divergences": divergences}


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P7 over the records."""
    expected = config["expected"]
    signal_cases = sorted(expected["signal_cases"])
    score_anchors = {"E1": 1.0, "E5": 0.5, "E10": 0.2}  # P4 (PR-D-08 § 5)
    tolerance = 1e-9

    failures: dict[str, list[str]] = {f"P{i}": [] for i in range(1, 8)}

    for record in records:
        seed = record["seed"]
        signals = record["signals"]
        meta = generate_population(config, seed)["meta"]
        case_by_entity = {entity: case for case, entity in meta.items()}
        signaled_cases = sorted(
            case_by_entity[s["entity_id"]]
            for s in signals
            if s["entity_id"] in case_by_entity
        )
        signaled_ids = {s["entity_id"] for s in signals}

        if record["repeat_divergences"]:
            failures["P7"].append(f"seed {seed}: repeat diverged")

        # P1 — exact signal set (E1, E5, E9, E10; controls never signal).
        if signaled_cases != signal_cases or len(signals) != len(signal_cases):
            failures["P1"].append(
                f"seed {seed}: signaled {signaled_cases} ({len(signals)}) "
                f"!= {signal_cases}"
            )

        # P2 — temporal gate (E2 signs before the inauguration).
        if meta["E2"] in signaled_ids:
            failures["P2"].append(f"seed {seed} E2: pre-mandate contract signaled")

        # P3 — elected gate (E3 defeated, E7 councillor).
        for case_id in ("E3", "E7"):
            if meta[case_id] in signaled_ids:
                failures["P3"].append(f"seed {seed} {case_id}: gate violated")

        # P4 — concentration gate and score anchors.
        if meta["E4"] in signaled_ids:
            failures["P4"].append(f"seed {seed} E4: share 0.01 signaled")
        for case_id, expected_score in score_anchors.items():
            match = next(
                (s for s in signals if s["entity_id"] == meta[case_id]), None
            )
            if match is None:
                failures["P4"].append(f"seed {seed} {case_id}: anchor not signaled")
            elif abs(match["score"] - expected_score) > tolerance:
                failures["P4"].append(
                    f"seed {seed} {case_id}: score {match['score']} "
                    f"!= {expected_score}"
                )

        # P5 — donation floor.
        if meta["E6"] in signaled_ids:
            failures["P5"].append(f"seed {seed} E6: donation below floor signaled")

        # P6 — document discipline (E8 divergent never signals; E9 origin).
        if meta["E8"] in signaled_ids:
            failures["P6"].append(f"seed {seed} E8: divergent document signaled")
        if meta["E9"] not in signaled_ids:
            failures["P6"].append(f"seed {seed} E9: origin-donor match failed")

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


def run_battery(config: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Runs the battery over the configured synthetic seeds.

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
