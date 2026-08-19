"""Detection battery runner (bateria D-05).

Responsibility: Generate the planted amendment cases A1-A9 (per the
declarative config ``experiments/detect/D-05.json``) as bronze
observation sequences, compute the flags with
``capiba.detection.amendments`` in-process and evaluate the
pre-registered predictions P1-P5 (``docs/preregistrations/PR-D-05.md``).
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

from capiba.detection import battery
from capiba.detection.amendments import compute_amendment_flags

_BASE = date(2026, 1, 1)


def _observation(
    spec: dict[str, Any],
    control_number: str,
    observed_on: date,
    contract_start: date,
    estimated: float,
) -> dict[str, Any]:
    """Builds one bronze observation payload from its declarative spec.

    The validity window is anchored at ``contract_start`` (fixed per
    case), not at the observation date — otherwise identical offsets
    across observations would look like a term extension.
    """
    payload: dict[str, Any] = {
        "observed_on": observed_on.isoformat(),
        "numeroControlePNCP": control_number,
    }
    if spec.get("malformed"):
        payload["valorInicial"] = "abc"
        payload["valorAcumulado"] = "n/a"
        payload["dataVigenciaFim"] = "n/a"
        return payload

    if spec.get("initial") == "zero":
        payload["valorInicial"] = 0
    else:
        payload["valorInicial"] = estimated

    ratio = spec.get("ratio")
    if ratio is None or ratio == "absent":
        pass  # valorAcumulado absent from the payload
    else:
        payload["valorAcumulado"] = round(estimated * float(ratio), 2)

    offset = spec.get("validity_end_offset_days")
    if offset is not None:
        payload["dataVigenciaInicio"] = contract_start.isoformat()
        payload["dataVigenciaFim"] = (contract_start + timedelta(days=offset)).isoformat()
    payload["numeroRetificacao"] = spec.get("rectifications", 0)
    return payload


def generate_cases(config: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    """Builds the observation sequence of each planted case for one seed.

    The seed only randomizes neutral fields (control number, base date
    inside the planted window, estimate keeping the planted ratios, and
    the gaps between observations); the case structure is fixed by the
    config.

    Args:
        config: Battery configuration (``experiments/detect/D-05.json``).
        seed: RNG seed (deterministic per seed).

    Returns:
        One entry per case: ``case`` and its ``observations``.
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    cases: list[dict[str, Any]] = []
    for spec in config["cases"]:
        base = _BASE + timedelta(days=rng.randint(0, 120))
        estimated = battery._log_uniform(rng, 1000.0, 10_000_000.0)
        control_number = f"SYN-D05-{seed}-{spec['id']}"
        observations = []
        observed_on = base
        for obs_spec in spec["observations"]:
            observations.append(
                _observation(obs_spec, control_number, observed_on, base, estimated)
            )
            observed_on += timedelta(days=30 + rng.randint(0, 60))
        cases.append({"case": spec["id"], "observations": observations})
    return cases


def _compute(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Computes the amendment flags for each generated case."""
    results = []
    for entry in cases:
        flags = compute_amendment_flags(entry["observations"])
        results.append(
            {
                "case": entry["case"],
                "f_value_amendment": flags.f_value_amendment,
                "f_term_extension": flags.f_term_extension,
                "max_rectifications": flags.max_rectifications,
                "observations": flags.observations,
                "value_ratio": flags.value_ratio,
                "payload": entry["observations"],
            }
        )
    return results


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P5)."""
    results = _compute(generate_cases(config, seed))
    repeat = _compute(generate_cases(config, seed))
    keys = ("f_value_amendment", "f_term_extension", "value_ratio")
    divergences = sum(
        1
        for first, second in zip(results, repeat, strict=True)
        if any(first[key] != second[key] for key in keys)
    )
    return {"seed": seed, "results": results, "repeat_divergences": divergences}


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P5 over the records.

    P1 compares the flag vector of every case against the config's
    ``expected`` block (``value_ratio`` included when declared). P2/P3
    are the value/term boundary anchors, P4 the null-discipline anchors,
    P5 the determinism and last-observation sovereignty checks.
    """
    expected_by_case = {case["id"]: case["expected"] for case in config["cases"]}

    p1_failures: list[str] = []
    p4_failures: list[str] = []
    boundary_failures: dict[str, list[str]] = {"P2": [], "P3": []}
    p5_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        if record["repeat_divergences"]:
            p5_failures.append(
                f"seed {seed}: {record['repeat_divergences']} repeat divergences"
            )
        by_case = {r["case"]: r for r in record["results"]}

        # P1 — exact flag vector (and declared value_ratio) per case
        for case_id, expected in expected_by_case.items():
            got = by_case[case_id]
            for key, want in expected.items():
                if got[key] != want:
                    p1_failures.append(
                        f"seed {seed} {case_id}: {key} {got[key]} != {want}"
                    )

        # P2 — value boundary (ratio 1.0 -> 0, 1.2 -> 1)
        if by_case["A4"]["f_value_amendment"] != 0:
            boundary_failures["P2"].append(f"seed {seed} A4: ratio 1.0 flagged")
        if by_case["A2"]["f_value_amendment"] != 1:
            boundary_failures["P2"].append(f"seed {seed} A2: ratio 1.2 not flagged")

        # P3 — term boundary (extension -> 1, unchanged -> 0)
        if by_case["A3"]["f_term_extension"] != 1:
            boundary_failures["P3"].append(f"seed {seed} A3: extension not flagged")
        if by_case["A1"]["f_term_extension"] != 0:
            boundary_failures["P3"].append(f"seed {seed} A1: unchanged term flagged")

        # P4 — null discipline (A6/A7 value NULL; A8 both NULL, no error)
        for case_id in ("A6", "A7"):
            if by_case[case_id]["f_value_amendment"] is not None:
                p4_failures.append(f"seed {seed} {case_id}: f_value not NULL")
        if by_case["A8"]["f_value_amendment"] is not None or (
            by_case["A8"]["f_term_extension"] is not None
        ):
            p4_failures.append(f"seed {seed} A8: malformed fields not NULL")

        # P5 — last-observation sovereignty (A9 -> 0 despite the high first)
        if by_case["A9"]["f_value_amendment"] != 0:
            p5_failures.append(f"seed {seed} A9: last observation not sovereign")

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
