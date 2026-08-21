"""Detection battery runner (bateria D-12).

Responsibility: Generate the planted PEP-screening cases Q1-Q7 plus
control suppliers (per the declarative config
``experiments/detect/D-12.json``), exercise the yente adapter
``capiba.detection.pep_screening`` with a **stubbed** backend (the
synthetic regime tests that the Capiba speaks FtM correctly, not the
yente itself — PR-D-12 § 4) and evaluate the pre-registered prediction
P2 (exact adapter behavior). P1 (documentary profile of the ``br_pep``
snapshot) is measured over the pinned bulk; P3-P4 (OpenSanctions Pairs
benchmark, in-process ``logic-v2``, with the paired control of the local
D-06b matcher) require the pinned yente install and are skipped with a
marker when ``nomenklatura`` is not importable; P6 (real annotated
sample) runs against the self-hosted yente — outside this runner.

Doctrine: no battery without a pre-registration
(``docs/preregistrations/PR-D-12.md``). The config is the single source
of parameters (seeds included); raw outputs are versioned under
``results/detect/<id>/``.
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

from capiba.detection.battery_entities import sample_os_pairs
from capiba.detection.entities import name_similarity
from capiba.detection.pep_screening import build_match_query, pep_supplier_match_signals

# Paired internal control (PR-D-12 § 6): the local D-06b matcher in the
# name-only regime, re-executed over the same seed-61 sample.
LOCAL_NAME_ONLY_THRESHOLD = 0.95


def _document(rng: random.Random, digits: int) -> str:
    """Draws a random numeric document (synthetic, validity irrelevant)."""
    return "".join(str(rng.randint(0, 9)) for _ in range(digits))


def _contract(
    rng: random.Random,
    case: str,
    name: str | None,
    cpf: str | None,
    cnpj: str | None = None,
    occurrence: int = 0,
) -> dict[str, Any]:
    supplier: dict[str, Any] = {}
    if name is not None:
        supplier["legal_name"] = name
    if cpf:
        supplier["cpf"] = cpf
    if cnpj:
        supplier["cnpj"] = cnpj
    return {
        "id": f"SYN-D12-{case}-{occurrence}-{rng.randrange(10**6)}",
        "signature_date": "2026-02-15",
        "supplier": supplier,
        "buyer": {"siafi_code": "900000"},
        "amount": 1000.0,
    }


def _candidates(base_score: float) -> list[dict[str, Any]]:
    """Two stub candidates above the default threshold, descending scores."""
    return [
        {
            "id": "br-pep-stub-1",
            "score": base_score,
            "schema": "Person",
            "properties": {"name": ["Stub Candidato Um"]},
        },
        {
            "id": "br-pep-stub-2",
            "score": base_score - 0.1,
            "schema": "Person",
            "properties": {"name": ["Stub Candidato Dois"]},
        },
    ]


def generate_population(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Builds the synthetic contracts and stub responses for one seed.

    The case structure is fixed by the pre-registration (PR-D-12 § 4);
    the seed only randomizes neutral fields (documents, contract ids).

    Returns:
        ``contracts``, the ``canned`` stub responses (supplier name ->
        candidates) and the ``meta`` ground truth (case id -> expected
        supplier entity id).
    """
    rng = random.Random(
        seed
    )  # deterministic synthetic data, not cryptographic  # nosec B311

    contracts: list[dict[str, Any]] = []
    canned: dict[str, list[dict[str, Any]]] = {}
    meta: dict[str, str] = {}

    # Q1 — PF with CPF and name -> Person query with name+idNumber+nationality.
    cpf_q1 = _document(rng, 11)
    contracts.append(_contract(rng, "Q1", "MARIA DE FATIMA PEREIRA", cpf_q1))
    canned["MARIA DE FATIMA PEREIRA"] = []
    meta["Q1"] = cpf_q1

    # Q2 — PF without CPF -> name-only query (no idNumber).
    contracts.append(_contract(rng, "Q2", "JOSE CARLOS SANTOS", None))
    canned["JOSE CARLOS SANTOS"] = []
    meta["Q2"] = "JOSE CARLOS SANTOS"

    # Q3 — PJ -> no query (a company is not a PEP).
    contracts.append(
        _contract(rng, "Q3", "EMPRESA CONTROLE LTDA", None, cnpj=_document(rng, 14))
    )
    meta["Q3"] = "EMPRESA CONTROLE LTDA"

    # Q4 — nameless supplier -> no query.
    cpf_q4 = _document(rng, 11)
    contracts.append(_contract(rng, "Q4", None, cpf_q4))
    meta["Q4"] = cpf_q4

    # Q5 — stub returns 2 candidates above the threshold -> 1 signal,
    # score = best candidate score, details with both ids.
    cpf_q5 = _document(rng, 11)
    contracts.append(_contract(rng, "Q5", "ANA PAULA OLIVEIRA", cpf_q5))
    canned["ANA PAULA OLIVEIRA"] = _candidates(0.9)
    meta["Q5"] = cpf_q5

    # Q6 — stub returns no candidates -> no signal.
    cpf_q6 = _document(rng, 11)
    contracts.append(_contract(rng, "Q6", "RICARDO ALMEIDA BARROS", cpf_q6))
    canned["RICARDO ALMEIDA BARROS"] = []
    meta["Q6"] = cpf_q6

    # Q7 — the same PF supplier in N contracts -> 1 query, at most 1 signal.
    cpf_q7 = _document(rng, 11)
    for occurrence in range(3):
        contracts.append(
            _contract(
                rng, "Q7", "CAMILA RODRIGUES PINTO", cpf_q7, occurrence=occurrence
            )
        )
    canned["CAMILA RODRIGUES PINTO"] = []
    meta["Q7"] = cpf_q7

    # Control suppliers: PF contracts the stub never matches.
    for i in range(config["synthetic"]["control_suppliers"]):
        name = f"CONTROLE PEP {i:02d}"
        contracts.append(_contract(rng, f"CTRL-{i:02d}", name, _document(rng, 11)))
        canned[name] = []

    return {"contracts": contracts, "canned": canned, "meta": meta}


def _compute(config: dict[str, Any], population: dict[str, Any]) -> dict[str, Any]:
    """Runs the adapter with a recording stub over one generated population."""
    yente = config["yente"]
    queries: list[dict[str, Any]] = []

    def stub(query: dict[str, Any]) -> list[dict[str, Any]]:
        queries.append(query)
        name = query["properties"]["name"][0]
        canned: list[dict[str, Any]] = population["canned"].get(name, [])
        return canned

    signals = pep_supplier_match_signals(
        population["contracts"],
        stub,
        threshold=yente["threshold"],
        dataset=yente["dataset"],
    )
    return {"signals": signals, "queries": queries}


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P5-style)."""
    record = _compute(config, generate_population(config, seed))
    repeat = _compute(config, generate_population(config, seed))
    divergences = int(record != repeat)
    return {
        "seed": seed,
        "signals": record["signals"],
        "queries": record["queries"],
        "repeat_divergences": divergences,
    }


def _expected_queries(config: dict[str, Any], seed: int) -> dict[str, dict[str, Any]]:
    """The exact FtM queries each consulting case (Q1/Q2/Q5/Q6/Q7) must issue."""
    population = generate_population(config, seed)
    suppliers = {
        str(
            contract["supplier"].get("cpf") or contract["supplier"].get("legal_name")
        ): contract["supplier"]
        for contract in population["contracts"]
    }
    meta = population["meta"]
    expected: dict[str, dict[str, Any]] = {}
    for case in ("Q1", "Q2", "Q5", "Q6", "Q7"):
        query = build_match_query(suppliers[meta[case]])
        if query is not None:
            expected[case] = query
    return expected


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered synthetic prediction P2 (exact adapter)."""
    cases = config["synthetic"]["cases"]
    signal_cases = sorted(cases["signal_cases"])
    no_signal_cases = sorted(cases["no_signal_cases"])
    failures: list[str] = []

    for record in records:
        seed = record["seed"]
        if record["repeat_divergences"]:
            failures.append(f"seed {seed}: repeat diverged")
        population = generate_population(config, seed)
        meta = population["meta"]
        case_by_entity = {entity: case for case, entity in meta.items()}
        expected = _expected_queries(config, seed)

        signaled_cases = sorted(
            case_by_entity[s["entity_id"]]
            for s in record["signals"]
            if s["entity_id"] in case_by_entity
        )
        control_signals = [
            s for s in record["signals"] if s["entity_id"] not in case_by_entity
        ]

        # Exact signal set: only the declared signal cases, never the
        # no-signal cases, never the controls.
        if signaled_cases != signal_cases or len(record["signals"]) != len(
            signal_cases
        ):
            failures.append(f"seed {seed}: signaled {signaled_cases} != {signal_cases}")
        if control_signals:
            failures.append(f"seed {seed}: {len(control_signals)} control signals")
        for case_id in no_signal_cases:
            if meta[case_id] in {s["entity_id"] for s in record["signals"]}:
                failures.append(f"seed {seed} {case_id}: unexpected signal")

        # Exact queries: every consulting case (Q1/Q2/Q5/Q6/Q7) issues
        # exactly the declared FtM query, exactly once; Q3/Q4 never query
        # (covered by the total count).
        for case_id, expected_query in expected.items():
            issued = [q for q in record["queries"] if q == expected_query]
            if len(issued) != 1:
                failures.append(f"seed {seed} {case_id}: {len(issued)} queries != 1")
        # Q1, Q2, Q5, Q6, Q7 (deduplicated) plus the controls: every query
        # beyond that is a case that should never consult (Q3 PJ, Q4
        # nameless) or a broken dedup.
        expected_total = 5 + config["synthetic"]["control_suppliers"]
        if len(record["queries"]) != expected_total:
            failures.append(
                f"seed {seed}: {len(record['queries'])} queries != {expected_total}"
            )

        # Q5 payload: score = best candidate score, details with both ids.
        q5_signals = [s for s in record["signals"] if s["entity_id"] == meta["Q5"]]
        if len(q5_signals) == 1:
            signal = q5_signals[0]
            details = json.loads(signal["details"])
            candidate_ids = [c["id"] for c in details["candidates"]]
            expected_candidates = generate_population(config, seed)["canned"][
                "ANA PAULA OLIVEIRA"
            ]
            best = max(float(c["score"]) for c in expected_candidates)
            if signal["score"] != round(best, 4):
                failures.append(f"seed {seed} Q5: score {signal['score']} != {best}")
            if candidate_ids != [c["id"] for c in expected_candidates]:
                failures.append(f"seed {seed} Q5: candidate ids {candidate_ids}")
            if details["query"] != expected["Q5"]:
                failures.append(f"seed {seed} Q5: archived query mismatch")

    return {
        "battery": config["id"],
        "predictions": {
            "P2": {
                "verdict": "refuted" if failures else "success",
                "failures": failures,
            }
        },
        "verdict": "refuted" if failures else "success",
    }


def _ftm_entity(data: dict[str, Any], entity_id: str) -> Any:
    """Builds a followthemoney entity proxy from a pair row side."""
    from followthemoney import model  # type: ignore[import-not-found]

    entity = model.make_entity(data.get("schema") or "Person")
    for prop, values in (data.get("properties") or {}).items():
        entity.add(prop, values, quiet=True)
    entity.id = data.get("id") or entity_id
    return entity


def evaluate_os_pairs_yente(
    config: dict[str, Any], sample: list[dict[str, Any]]
) -> dict[str, Any]:
    """Scores the OS Pairs sample in-process with the pinned ``logic-v2``.

    Requires the pinned yente install (``nomenklatura``); when it is not
    importable the benchmark is reported as ``skipped`` (the synthetic
    regime does not depend on it). Prediction: each pair is positive iff
    the ``logic-v2`` score reaches the config threshold; the left side is
    the query, the right side the candidate.
    """
    try:
        from nomenklatura.matching import (  # type: ignore[import-not-found]
            ScoringConfig,
            get_algorithm,
        )
    except ImportError:
        return {"skipped": "nomenklatura unavailable (run under the yente venv)"}

    algorithm = get_algorithm(config["yente"]["algorithm"])
    threshold = config["yente"]["threshold"]
    scoring_config = ScoringConfig.defaults()
    tp = fp = fn = tn = 0
    for index, row in enumerate(sample):
        left = _ftm_entity(row["left"], f"pair-{index}-left")
        right = _ftm_entity(row["right"], f"pair-{index}-right")
        score = algorithm.compare(query=left, result=right, config=scoring_config).score
        predicted = score >= threshold
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
    return {
        "algorithm": config["yente"]["algorithm"],
        "threshold": threshold,
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "p3": {
            "verdict": "success" if precision >= spec["min_precision"] else "refuted"
        },
        "p4": {"verdict": "success" if recall >= spec["min_recall"] else "refuted"},
    }


def evaluate_os_pairs_local(
    config: dict[str, Any], sample: list[dict[str, Any]]
) -> dict[str, Any]:
    """Paired internal control: the local D-06b matcher, name-only regime."""
    tp = fp = fn = tn = 0
    for row in sample:
        left = (row["left"].get("properties", {}).get("name") or [None])[0]
        right = (row["right"].get("properties", {}).get("name") or [None])[0]
        predicted = (
            bool(left and right)
            and name_similarity(left, right) >= LOCAL_NAME_ONLY_THRESHOLD
        )
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
    return {
        "matcher": config["os_pairs"]["paired_control"],
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
    }


def run_battery(config: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Runs the battery: synthetic seeds + OS Pairs benchmark (when available).

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs (``seed_<n>.jsonl``
            signals, ``seed_<n>_queries.jsonl`` issued queries), the cached
            ``pairs_sample.jsonl`` and ``summary.json``.

    Returns:
        The per-seed records (signals, queries, repeat divergences).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for seed in config["seeds"]:
        record = run_seed(config, seed)
        with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
            for signal in record["signals"]:
                fh.write(json.dumps(signal, default=str) + "\n")
        with (out_dir / f"seed_{seed}_queries.jsonl").open("w") as fh:
            for query in record["queries"]:
                fh.write(json.dumps(query, default=str) + "\n")
        records.append(record)

    summary = evaluate(config, records)
    sample = sample_os_pairs(
        config, out_dir / "pairs_sample.jsonl", config["os_pairs"]["seed"]
    )
    yente_metrics = evaluate_os_pairs_yente(config, sample)
    summary["os_pairs_yente"] = yente_metrics
    summary["os_pairs_local_control"] = evaluate_os_pairs_local(config, sample)
    if "skipped" in yente_metrics:
        summary["predictions"]["P3"] = {"verdict": "skipped"}
        summary["predictions"]["P4"] = {"verdict": "skipped"}
    else:
        summary["predictions"]["P3"] = {
            "verdict": yente_metrics["p3"]["verdict"],
            "precision": yente_metrics["precision"],
        }
        summary["predictions"]["P4"] = {
            "verdict": yente_metrics["p4"]["verdict"],
            "recall": yente_metrics["recall"],
        }
        if (
            summary["predictions"]["P3"]["verdict"] != "success"
            or summary["predictions"]["P4"]["verdict"] != "success"
        ):
            summary["verdict"] = "refuted"
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return records
