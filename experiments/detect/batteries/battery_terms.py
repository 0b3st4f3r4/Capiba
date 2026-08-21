"""Detection battery runner (bateria D-05b).

Responsibility: Generate the planted term-list cases B1-B6 (per the
declarative config ``experiments/detect/D-05b.json``) as raw payloads of
the PNCP terms endpoint, compute the flags with
``capiba.detection.amendments.compute_term_flags`` in-process and evaluate
the pre-registered predictions Q1-Q2 (``docs/preregistrations/PR-D-05b.md``).
The real-data probes Q3-Q5 (endpoint viability, proxy agreement, domain)
run over the pilot cut — outside this runner.

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

from capiba.detection.amendments import compute_term_flags

_BASE = date(2026, 1, 1)
_FLOAT_TOLERANCE = 1e-9

# Result keys compared against the config's ``expected`` block and across
# the determinism repeat.
_KEYS = (
    "f_value_amendment_terms",
    "f_term_extension_terms",
    "terms_count",
    "total_value_increase",
    "total_days_extended",
    "term_types",
)


def _term(spec: dict[str, Any], control_number: str, seq: int, signed_on: date) -> dict[str, Any]:
    """Builds one raw terms-endpoint payload from its declarative spec.

    The seed only randomizes neutral fields (control number, signature
    date, sequence); the planted qualifications and amounts/days are fixed
    by the config.
    """
    payload: dict[str, Any] = {
        "numeroControlePNCP": control_number,
        "sequencialTermo": seq,
        "dataAssinatura": signed_on.isoformat(),
        "tipoTermoContratoNome": "Termo Aditivo",
        "qualificacaoAcrescimoSupressao": False,
        "qualificacaoVigencia": False,
        "qualificacaoReajuste": False,
    }
    kind = spec["kind"]
    if kind == "value_increase":
        payload["qualificacaoAcrescimoSupressao"] = True
        payload["valorAcrescido"] = spec["valorAcrescido"]
    elif kind == "term_extension":
        payload["qualificacaoVigencia"] = True
        payload["prazoAditadoDias"] = spec["prazoAditadoDias"]
    elif kind == "pure_reajuste":
        # Index reajuste (IPCA etc.) is a legal price update, not an
        # amendment — it must never fire a flag (PR-D-05b § 2).
        payload["qualificacaoReajuste"] = True
    elif kind == "suppression":
        payload["qualificacaoAcrescimoSupressao"] = True
        payload["valorAcrescido"] = spec["valorAcrescido"]  # negative
    else:
        raise ValueError(f"Unknown planted term kind: {kind}")
    return payload


def generate_cases(config: dict[str, Any], seed: int) -> list[dict[str, Any]]:
    """Builds the term list of each planted case for one seed.

    The seed only randomizes neutral fields (control number, signature
    dates); the case structure is fixed by the config. ``query_failed``
    cases carry ``terms = None`` — a failed query, not an empty list.

    Args:
        config: Battery configuration (``experiments/detect/D-05b.json``).
        seed: RNG seed (deterministic per seed).

    Returns:
        One entry per case: ``case`` and its ``terms`` (list or None).
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    cases: list[dict[str, Any]] = []
    for spec in config["cases"]:
        control_number = f"SYN-D05b-{seed}-{spec['id']}"
        if spec.get("query_failed"):
            cases.append({"case": spec["id"], "terms": None})
            continue
        signed_on = _BASE + timedelta(days=rng.randint(0, 120))
        terms = [
            _term(term_spec, control_number, seq, signed_on)
            for seq, term_spec in enumerate(spec["terms"], start=1)
        ]
        cases.append({"case": spec["id"], "terms": terms})
    return cases


def _compute(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Computes the term flags for each generated case."""
    results = []
    for entry in cases:
        flags = compute_term_flags(entry["terms"])
        results.append(
            {
                "case": entry["case"],
                "f_value_amendment_terms": flags.f_value_amendment_terms,
                "f_term_extension_terms": flags.f_term_extension_terms,
                "terms_count": flags.terms_count,
                "total_value_increase": flags.total_value_increase,
                "total_days_extended": flags.total_days_extended,
                "term_types": flags.term_types,
                "payload": entry["terms"],
            }
        )
    return results


def _matches(got: Any, want: Any) -> bool:
    """Compares a result field against its expectation (float-tolerant)."""
    if isinstance(want, float) and isinstance(got, (int, float)):
        return abs(got - want) <= _FLOAT_TOLERANCE
    return bool(got == want)


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (Q1)."""
    results = _compute(generate_cases(config, seed))
    repeat = _compute(generate_cases(config, seed))
    divergences = sum(
        1
        for first, second in zip(results, repeat, strict=True)
        if any(not _matches(first[key], second[key]) for key in _KEYS)
    )
    return {"seed": seed, "results": results, "repeat_divergences": divergences}


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions Q1-Q2 over the records.

    Q1 compares the full result vector of every case against the config's
    ``expected`` block and requires bit-level determinism (zero repeat
    divergences). Q2 is the null discipline: a failed query (B6) computes
    NULL, an empty term list (B1) computes 0 and a pure reajuste (B4)
    never fires.
    """
    expected_by_case = {case["id"]: case["expected"] for case in config["cases"]}

    q1_failures: list[str] = []
    q2_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        if record["repeat_divergences"]:
            q1_failures.append(
                f"seed {seed}: {record['repeat_divergences']} repeat divergences"
            )
        by_case = {r["case"]: r for r in record["results"]}

        # Q1 — exact result vector per case
        for case_id, expected in expected_by_case.items():
            got = by_case[case_id]
            for key, want in expected.items():
                if not _matches(got[key], want):
                    q1_failures.append(
                        f"seed {seed} {case_id}: {key} {got[key]} != {want}"
                    )

        # Q2 — null discipline
        if by_case["B6"]["f_value_amendment_terms"] is not None or (
            by_case["B6"]["f_term_extension_terms"] is not None
        ):
            q2_failures.append(f"seed {seed} B6: failed query not NULL")
        if by_case["B1"]["f_value_amendment_terms"] != 0 or (
            by_case["B1"]["f_term_extension_terms"] != 0
        ):
            q2_failures.append(f"seed {seed} B1: empty term list not 0")
        if by_case["B4"]["f_value_amendment_terms"] != 0 or (
            by_case["B4"]["f_term_extension_terms"] != 0
        ):
            q2_failures.append(f"seed {seed} B4: pure reajuste fired a flag")

    predictions: dict[str, dict[str, Any]] = {
        "Q1": {
            "verdict": "refuted" if q1_failures else "success",
            "failures": q1_failures,
        },
        "Q2": {
            "verdict": "refuted" if q2_failures else "success",
            "failures": q2_failures,
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
