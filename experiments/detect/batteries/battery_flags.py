"""Detection battery runner (bateria D-04).

Responsibility: Generate the planted red-flag cases K1-K9 (per the
declarative config ``experiments/detect/D-04.json``), compute the flags
and CRI with ``capiba.detection.red_flags`` in-process and evaluate the
pre-registered predictions P1-P5 (``docs/preregistrations/PR-D-04.md``).
The real-data invariants P6-P8 are dbt data tests over the gold mart,
executed after the backfill — outside this runner.

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

from batteries import battery
from capiba.detection.red_flags import compute_red_flags

_BASE = date(2026, 1, 1)


def generate_cases(config: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    """Builds one PNCP-like payload per planted case for one seed.

    The seed only randomizes neutral fields (ids, names, the base date
    inside the planted window and the estimate keeping the planted
    ratio); the case structure (modality, window, ratio, nulls) is fixed
    by the config.

    Args:
        config: Battery configuration (``experiments/detect/D-04.json``).
        seed: RNG seed (deterministic per seed).

    Returns:
        One entry per case: ``case``, ``modality`` and ``payload``.
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    cases: list[dict[str, Any]] = []
    for spec in config["cases"]:
        base = _BASE + timedelta(days=rng.randint(0, 180))
        payload: dict[str, Any] = {
            "numeroControlePNCP": f"SYN-D04-{seed}-{spec['id']}",
            "objetoCompra": f"Objeto sintético {spec['id']} (seed {seed})",
            "orgaoEntidade": {
                "cnpj": "00000000000191",
                "razaoSocial": "Órgão Sintético",
            },
        }
        if spec["malformed"]:
            payload["dataAberturaProposta"] = "n/a"
            payload["dataEncerramentoProposta"] = "n/a"
            payload["valorInicialCompra"] = "abc"
            payload["valorTotalHomologado"] = "abc"
        else:
            if spec["window_days"] is not None:
                opened = base
                closed = base + timedelta(days=spec["window_days"])
                payload["dataAberturaProposta"] = f"{opened.isoformat()}T09:00:00"
                payload["dataEncerramentoProposta"] = f"{closed.isoformat()}T17:00:00"
            if spec["ratio"] is not None:
                estimated = battery._log_uniform(rng, 1000.0, 10_000_000.0)
                payload["valorInicialCompra"] = estimated
                payload["valorTotalHomologado"] = round(estimated * spec["ratio"], 2)
        cases.append(
            {"case": spec["id"], "modality": spec["modality"], "payload": payload}
        )
    return cases


def _compute(config: dict[str, Any], cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Computes flags and CRI for each generated case."""
    short_window_days = config["short_window_days"]
    results = []
    for entry in cases:
        flags = compute_red_flags(
            entry["payload"], entry["modality"], short_window_days=short_window_days
        )
        results.append(
            {
                "case": entry["case"],
                "modality": entry["modality"],
                "f_non_competitive": flags.f_non_competitive,
                "f_short_window": flags.f_short_window,
                "f_price_ratio": flags.f_price_ratio,
                "cri": flags.cri,
                "payload": entry["payload"],
            }
        )
    return results


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P5)."""
    results = _compute(config, generate_cases(config, seed))
    repeat = _compute(config, generate_cases(config, seed))
    divergences = sum(
        1
        for first, second in zip(results, repeat, strict=True)
        if any(
            first[key] != second[key]
            for key in ("f_non_competitive", "f_short_window", "f_price_ratio", "cri")
        )
    )
    return {"seed": seed, "results": results, "repeat_divergences": divergences}


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P5 over the records.

    P1 compares the flag vector of every case against the config's
    ``expected`` block (the CRI expectation declared there is checked
    too — it is a direct consequence of the flags and of the composition
    invariant of PR-D-04 § 6). P2/P3 are the window/ratio boundary
    anchors, P4 the null-composition anchors, P5 the determinism check.
    """
    expected_by_case = {case["id"]: case["expected"] for case in config["cases"]}
    flag_keys = ("f_non_competitive", "f_short_window", "f_price_ratio")

    p1_failures: list[str] = []
    p4_failures: list[str] = []
    boundary_failures: dict[str, list[str]] = {"P2": [], "P3": []}
    repeat_divergences = 0

    for record in records:
        seed = record["seed"]
        repeat_divergences += record["repeat_divergences"]
        by_case = {r["case"]: r for r in record["results"]}

        # P1 — exact flag vector (and declared CRI) per case
        for case_id, expected in expected_by_case.items():
            got = by_case[case_id]
            for key in (*flag_keys, "cri"):
                if got[key] != expected[key]:
                    p1_failures.append(
                        f"seed {seed} {case_id}: {key} {got[key]} != {expected[key]}"
                    )

        # P2 — submission-window boundary (7 days -> 0, 6 days -> 1)
        if by_case["K3"]["f_short_window"] != 0:
            boundary_failures["P2"].append(f"seed {seed} K3: boundary 7d flagged")
        if by_case["K4"]["f_short_window"] != 1:
            boundary_failures["P2"].append(f"seed {seed} K4: 6d not flagged")

        # P3 — price-ratio boundary (1.0 -> 0, 1.01 -> 1)
        if by_case["K5"]["f_price_ratio"] != 0:
            boundary_failures["P3"].append(f"seed {seed} K5: ratio 1.0 flagged")
        if by_case["K6"]["f_price_ratio"] != 1:
            boundary_failures["P3"].append(f"seed {seed} K6: ratio 1.01 not flagged")

        # P4 — null composition (K7 -> 0.5, K8 -> NULL)
        if by_case["K7"]["cri"] != 0.5:
            p4_failures.append(f"seed {seed} K7: cri {by_case['K7']['cri']} != 0.5")
        if by_case["K8"]["cri"] is not None:
            p4_failures.append(f"seed {seed} K8: cri {by_case['K8']['cri']} != NULL")

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
            "verdict": "refuted" if repeat_divergences else "success",
            "repeat_divergences": repeat_divergences,
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
        The per-seed records (results, repeat divergences).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for seed in config["seeds"]:
        record = run_seed(config, seed)
        with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
            for result in record["results"]:
                fh.write(json.dumps(result, default=str) + "\n")
        records.append(record)
    summary = evaluate(config, records)
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return records
