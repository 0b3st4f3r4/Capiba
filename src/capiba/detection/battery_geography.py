"""Detection battery runner (bateria D-09).

Responsibility: Generate the planted geography cases G1-G10 plus the
sub-threshold control pairs (per the declarative config
``experiments/detect/D-09.json``), compute the ``anomalous_geography``
signals with ``capiba.detection.geography`` in-process over synthetic
silver rows and evaluate the pre-registered predictions P1-P5
(``docs/preregistrations/PR-D-09.md``). P6 (structural invariant over the
real gold) is verified after the integration — outside this runner.

The runner injects a synthetic municipality table carrying the planted
coordinates of each case (fictitious municipalities, plus Recife/Olinda/
João Pessoa/São Paulo seat coordinates from the config), so no network or
external infrastructure is needed.

Doctrine: no battery without a pre-registration. The config is the
single source of parameters (seeds included); raw outputs are versioned
under ``results/detect/<id>/``.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from capiba.detection.geography import anomalous_geography_signals

_UF = "PE"  # synthetic UF shared by all planted municipalities


def _document(rng: random.Random, digits: int) -> str:
    """Draws a random numeric document (synthetic, validity irrelevant)."""
    return "".join(str(rng.randint(0, 9)) for _ in range(digits))


def generate_population(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Builds the synthetic silver rows for one seed.

    The case structure is fixed by the pre-registration (PR-D-09 § 4):
    each case has its own supplier and buyer municipalities (so pairs
    cannot contaminate each other) and the seed only randomizes neutral
    fields — documents, TOM/IBGE codes, contract ids and amounts.

    Returns:
        ``contracts``, ``establishments``, ``rfb_municipalities``,
        ``municipalities`` and the ``meta`` ground truth (case id ->
        expected supplier entity id).
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311

    contracts: list[dict[str, Any]] = []
    establishments: list[dict[str, Any]] = []
    rfb_municipalities: list[dict[str, Any]] = []
    municipalities: list[dict[str, Any]] = []
    meta: dict[str, str] = {}

    def _municipality(name: str, ibge: str, lat: float, lon: float) -> dict[str, Any]:
        return {
            "name": name,
            "uf": _UF,
            "ibge_code": ibge,
            "latitude": lat,
            "longitude": lon,
        }

    def plant(
        index: int,
        case: dict[str, Any],
    ) -> None:
        case_id = case["id"]
        supplier = case["supplier"]
        buyer = case["buyer"]
        doc_type = supplier["doc_type"]
        digits = 14 if doc_type == "cnpj" else 11
        document = _document(rng, digits)
        ibge_base = f"{9000000 + index * 2:07d}"

        if buyer["lat"] is not None:
            municipalities.append(
                _municipality(
                    f"CIDADE {case_id}", ibge_base, buyer["lat"], buyer["lon"]
                )
            )
        if supplier["lat"] is not None:
            municipalities.append(
                _municipality(
                    f"SEDE {case_id}",
                    f"{int(ibge_base) + 1:07d}",
                    supplier["lat"],
                    supplier["lon"],
                )
            )
        if doc_type == "cnpj":
            # G8 plants an establishment with an unknown TOM code (a
            # missing de-para link); the others resolve the full chain.
            tom = f"{7000 + index:04d}" if case_id != "G8" else "9999"
            if case_id != "G8":
                rfb_municipalities.append({"tom_code": tom, "name": f"SEDE {case_id}"})
            establishments.append(
                {
                    "cnpj": document,
                    "municipio": tom,
                    "uf": _UF,
                    "is_matriz": True,
                }
            )

        n_contracts = 2 if case_id == "G4" else 1  # aggregation is neutral
        for k in range(n_contracts):
            contracts.append(
                {
                    "id": f"SYN-D09-{case_id}-{k}-{rng.randrange(10**6)}",
                    "buyer": {
                        "siafi_code": f"9{index:05d}",
                        "name": f"PREFEITURA DE CIDADE {case_id}",
                        "city": f"CIDADE {case_id}",
                        "uf": _UF,
                    },
                    "supplier": {
                        "cnpj": document if doc_type == "cnpj" else None,
                        "cpf": document if doc_type == "cpf" else None,
                        "legal_name": f"FORNECEDOR {case_id}",
                    },
                    "amount": round(rng.uniform(1_000.0, 100_000.0), 2),
                    "signature_date": "2026-01-10",
                }
            )
        meta[case_id] = document

    for index, case in enumerate(config["cases"]):
        plant(index, case)

    # Controls: pseudo-random supplier/buyer seats at most 0.5° apart per
    # axis (< 100 km), so no control pair may signal.
    for i in range(config["control_pairs"]):
        lat = rng.uniform(-30.0, 5.0)
        lon = rng.uniform(-60.0, -30.0)
        supplier = {"doc_type": "cnpj", "lat": lat, "lon": lon}
        buyer = {
            "lat": lat + rng.uniform(0.0, 0.5),
            "lon": lon + rng.uniform(0.0, 0.5),
        }
        plant(
            len(config["cases"]) + i,
            {"id": f"CTRL-{i:02d}", "supplier": supplier, "buyer": buyer},
        )

    return {
        "contracts": contracts,
        "establishments": establishments,
        "rfb_municipalities": rfb_municipalities,
        "municipalities": municipalities,
        "meta": meta,
    }


def _compute(config: dict[str, Any], population: dict[str, Any]) -> list[dict[str, Any]]:
    """Computes the anomalous_geography signals over one population."""
    thresholds = config["thresholds"]
    return anomalous_geography_signals(
        population["contracts"],
        population["establishments"],
        population["rfb_municipalities"],
        population["municipalities"],
        max_distance_km=thresholds["max_distance_km"],
        score_distance_reference=thresholds["score_distance_reference"],
        earth_radius_km=thresholds["earth_radius_km"],
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
    anchors = {
        case["id"]: case["expected"]
        for case in config["cases"]
        if case["expected"].get("signal")
    }
    tolerance = 1e-9

    failures: dict[str, list[str]] = {f"P{i}": [] for i in range(1, 6)}

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
            failures["P5"].append(f"seed {seed}: repeat diverged")

        # P1 — exact signal set (G4-G7; the rest and controls never signal).
        if signaled_cases != signal_cases or len(signals) != len(signal_cases):
            failures["P1"].append(
                f"seed {seed}: signaled {signaled_cases} ({len(signals)}) "
                f"!= {signal_cases}"
            )

        # P2 — strict distance gate (G2/G3 below, G4 above).
        for case_id in ("G2", "G3"):
            if meta[case_id] in signaled_ids:
                failures["P2"].append(f"seed {seed} {case_id}: below gate signaled")
        if meta["G4"] not in signaled_ids:
            failures["P2"].append(f"seed {seed} G4: 103.260266 km did not signal")

        # P3 — score/distance anchors pinned in the config.
        for case_id, anchor in sorted(anchors.items()):
            match = next(
                (s for s in signals if s["entity_id"] == meta[case_id]), None
            )
            if match is None:
                failures["P3"].append(f"seed {seed} {case_id}: anchor not signaled")
                continue
            if abs(match["score"] - anchor["score"]) > tolerance:
                failures["P3"].append(
                    f"seed {seed} {case_id}: score {match['score']} "
                    f"!= {anchor['score']}"
                )
            details = json.loads(match["details"])
            if abs(details["distance_km"] - anchor["distance_km"]) > tolerance:
                failures["P3"].append(
                    f"seed {seed} {case_id}: distance {details['distance_km']} "
                    f"!= {anchor['distance_km']}"
                )

        # P4 — missing-data discipline (G8/G9/G10 never signal).
        for case_id in ("G8", "G9", "G10"):
            if meta[case_id] in signaled_ids:
                failures["P4"].append(f"seed {seed} {case_id}: no-coordinate signaled")

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
