"""Detection battery runner (bateria D-07).

Responsibility: Generate the planted entity-resolution cases E1-E8 plus
control persons (per the declarative config ``experiments/detect/D-07.json``),
score the pairs with ``capiba.detection.entities`` in-process and
evaluate the pre-registered predictions P1-P5 (synthetic regime) and
P6-P7 (OpenSanctions Pairs benchmark, deterministic reservoir sample of
the public flat pairs-20251209.json.gz — downloaded once and cached next
to the raw outputs). P8 (structural invariant over the real graph) is
verified after the integration — outside this runner.

Doctrine: no battery without a pre-registration
(``docs/preregistrations/PR-D-07.md``). The config is the single source
of parameters (seeds included); raw outputs are versioned under
``results/detect/<id>/``.
"""

from __future__ import annotations

import json
import logging
import random
from collections.abc import Callable
from pathlib import Path
from typing import Any

from capiba.detection.entities import (
    is_merge,
    link_supplier_company,
    score_person_pair,
)

logger = logging.getLogger(__name__)

_FIRST_NAMES = [
    "JOAO",
    "MARIA",
    "CARLOS",
    "ANA",
    "PAULO",
    "LUCIA",
    "PEDRO",
    "FERNANDA",
]
_LAST_NAMES = ["SILVA", "SOUZA", "LIMA", "PEREIRA", "COSTA", "ROCHA"]

# Name-noise transformations for E2 (accent/case/order/punctuation).
_NAME_NOISE: list[Callable[[str], str]] = [
    lambda name: name.lower(),
    lambda name: name.replace("O", "Ô").replace("A", "Á"),
    lambda name: " ".join(reversed(name.split())),
    lambda name: name.replace(" ", ", ", 1),
]


def _document(rng: random.Random, digits: int) -> str:
    """Draws a random numeric document (synthetic, validity irrelevant)."""
    return "".join(str(rng.randint(0, 9)) for _ in range(digits))


def _masked(cpf_digits: str) -> str:
    """Masks an 11-digit CPF the RFB way (``***123456**``)."""
    return f"***{cpf_digits[3:9]}**"


def generate_population(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Builds the synthetic person pairs and supplier links for one seed.

    The seed only randomizes neutral fields (names, documents, which
    noise transformation E2 uses); the case structure is fixed by the
    pre-registration (PR-D-07, section 4).

    Returns:
        ``person_pairs`` (list of ``{case, a, b}``), ``supplier_links``
        (list of ``{case, supplier_cnpj, cnpj_basico}``) and the ``meta``
        ground truth (expected merges/links from the config).
    """
    rng = random.Random(
        seed
    )  # deterministic synthetic data, not cryptographic  # nosec B311

    def person(case: str, side: str) -> dict[str, Any]:
        return {
            "case": case,
            "nome": f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}",
            "cnpj_cpf_socio": _masked(_document(rng, 11)),
            "faixa_etaria": str(rng.randint(1, 9)),
        }

    pairs: list[dict[str, Any]] = []

    # E1 — same person, identical name, same masked document.
    a = person("E1", "a")
    pairs.append({"case": "E1", "a": a, "b": dict(a)})

    # E2 — same person, noisy name (accent/case/order), same document.
    a = person("E2", "a")
    b = dict(a)
    b["nome"] = rng.choice(_NAME_NOISE)(a["nome"])
    pairs.append({"case": "E2", "a": a, "b": b})

    # E3 — homonyms: same name, different masked document.
    a = person("E3", "a")
    b = person("E3", "b")
    b["nome"] = a["nome"]
    pairs.append({"case": "E3", "a": a, "b": b})

    # E4 — same masked document, disjoint names (digit coincidence).
    a = person("E4", "a")
    b = person("E4", "b")
    b["cnpj_cpf_socio"] = a["cnpj_cpf_socio"]
    while b["nome"] == a["nome"]:
        b["nome"] = f"{rng.choice(_FIRST_NAMES)} {rng.choice(_LAST_NAMES)}"
    pairs.append({"case": "E4", "a": a, "b": b})

    # E5 — same person, same name, NO document.
    a = person("E5", "a")
    b = {**a, "cnpj_cpf_socio": None}
    a = {**a, "cnpj_cpf_socio": None}
    pairs.append({"case": "E5", "a": a, "b": b})

    # E6 — same name + document, diverging age range.
    a = person("E6", "a")
    b = dict(a)
    b["faixa_etaria"] = str((int(a["faixa_etaria"]) % 9) + 1)
    pairs.append({"case": "E6", "a": a, "b": b})

    # Controls: disjoint persons scored pairwise (no merge expected).
    controls = [person("CTRL", str(i)) for i in range(config["control_persons"])]
    for i, a in enumerate(controls):
        for b in controls[i + 1 :]:
            pairs.append({"case": "CTRL", "a": a, "b": b})

    # E7/E8 — supplier↔company deterministic link.
    basico = _document(rng, 8)
    supplier_links = [
        {
            "case": "E7",
            "supplier_cnpj": f"{basico}{_document(rng, 6)}",
            "cnpj_basico": basico,
        },
        {"case": "E8", "supplier_cnpj": None, "cnpj_basico": _document(rng, 8)},
    ]

    return {"person_pairs": pairs, "supplier_links": supplier_links}


def _compute(population: dict[str, Any], config: dict[str, Any]) -> dict[str, Any]:
    """Scores one generated population (merges + links)."""
    weights = config["weights"]
    threshold = config["threshold"]
    merges: list[dict[str, Any]] = []
    for pair in population["person_pairs"]:
        score = score_person_pair(
            pair["a"],
            pair["b"],
            name_weight=weights["name"],
            document_weight=weights["document"],
            age_range_weight=weights["age_range"],
        )
        if is_merge(score, threshold):
            merges.append({"case": pair["case"], "score": score})
    links = [
        {"case": link["case"], "score": score}
        for link in population["supplier_links"]
        if (score := link_supplier_company(link["supplier_cnpj"], link["cnpj_basico"]))
    ]
    return {"merges": merges, "links": links}


def run_seed(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Runs one seed twice and records the repeat divergences (P4)."""
    result = _compute(generate_population(config, seed), config)
    repeat = _compute(generate_population(config, seed), config)
    return {
        "seed": seed,
        **result,
        "repeat_divergences": int(result != repeat),
    }


def _os_entity(entity: dict[str, Any]) -> dict[str, Any]:
    """Flattens an FtM entity of the OS Pairs file to matcher fields."""
    properties = entity.get("properties", {})
    names = properties.get("name") or []
    ids = (
        properties.get("idNumber")
        or properties.get("registrationNumber")
        or properties.get("taxNumber")
        or []
    )
    return {
        "name": names[0] if names else None,
        "id_number": ids[0] if ids else None,
    }


def _stream_pairs(url: str) -> Any:
    """Streams the (optionally gzipped) pairs file line by line."""
    import gzip

    import requests

    with requests.get(url, stream=True, timeout=120) as response:
        response.raise_for_status()
        raw = response.raw
        if url.endswith(".gz"):
            raw = gzip.GzipFile(fileobj=raw)
        for line in raw:
            text = line.decode("utf-8").strip() if isinstance(line, bytes) else line
            if text:
                yield json.loads(text)


def sample_os_pairs(
    config: dict[str, Any], cache_path: Path, seed: int
) -> list[dict[str, Any]]:
    """Deterministic stratified reservoir sample of the OS Pairs file.

    The sample is cached next to the raw battery outputs and reused on
    later runs; delete the cache to regenerate from the declared snapshot.

    Returns:
        List of ``{judgement, left, right}`` rows (``unsure`` excluded).
    """
    if cache_path.exists():
        return [json.loads(line) for line in cache_path.read_text().splitlines()]

    spec = config["os_pairs"]
    rng = random.Random(seed)  # deterministic sampling, not cryptographic  # nosec B311
    reservoirs: dict[str, list[dict[str, Any]]] = {"positive": [], "negative": []}
    targets = {
        "positive": spec["sample_positive"],
        "negative": spec["sample_negative"],
    }
    seen = {"positive": 0, "negative": 0}
    for row in _stream_pairs(spec["url"]):
        judgement = row.get("judgement")
        if judgement not in reservoirs:
            continue
        seen[judgement] += 1
        reservoir = reservoirs[judgement]
        target = targets[judgement]
        if len(reservoir) < target:
            reservoir.append(row)
        elif (slot := rng.randrange(seen[judgement])) < target:
            reservoir[slot] = row

    sample = reservoirs["positive"] + reservoirs["negative"]
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text("".join(json.dumps(row) + "\n" for row in sample))
    logger.info(
        "OS Pairs sample: %d rows (%s) cached at %s", len(sample), seen, cache_path
    )
    return sample


def _os_has_id(entity: dict[str, Any]) -> bool:
    """Whether the OS Pairs entity carries any identifier property."""
    properties = entity.get("properties", {})
    return any(
        properties.get(key) for key in ("idNumber", "registrationNumber", "taxNumber")
    )


def evaluate_os_pairs(
    config: dict[str, Any], sample: list[dict[str, Any]]
) -> dict[str, Any]:
    """Scores the OS Pairs sample and evaluates P6/P7 (precision/recall)."""
    weights = config["weights"]
    threshold = config["threshold"]
    tp = fp = fn = tn = 0
    bilateral_positive = 0
    for row in sample:
        score = score_person_pair(
            _os_entity(row["left"]),
            _os_entity(row["right"]),
            name_weight=weights["name"],
            document_weight=weights["document"],
            age_range_weight=weights["age_range"],
        )
        predicted = is_merge(score, threshold)
        positive = row["judgement"] == "positive"
        if positive and _os_has_id(row["left"]) and _os_has_id(row["right"]):
            bilateral_positive += 1
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
    n_positive = tp + fn
    spec = config["os_pairs"]
    low, high = spec["recall_band"]
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "bilateral_doc_positive_rate": (
            round(bilateral_positive / n_positive, 4) if n_positive else None
        ),
        "p6": {
            "verdict": "success" if precision >= spec["min_precision"] else "refuted"
        },
        "p7": {"verdict": "success" if low <= recall <= high else "refuted"},
    }


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P5 over the records."""
    expected = config["expected"]
    merge_cases = set(expected["merge_cases"])
    no_merge_cases = set(expected["no_merge_cases"])

    p1_failures: list[str] = []
    p4_failures: list[str] = []
    p5_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        if record["repeat_divergences"]:
            p4_failures.append(f"seed {seed}: repeat diverged")
        merged = {m["case"] for m in record["merges"]}
        linked = {link["case"] for link in record["links"]}

        # P1 — exact merge set (E1, E2, E6; controls never merge)
        want = merge_cases
        if merged != want:
            p1_failures.append(
                f"seed {seed}: merged {sorted(merged)} != {sorted(want)}"
            )

        # P2/P3 — homonym discipline and name-noise robustness
        if "E3" in merged or "E4" in merged or "E5" in merged:
            p1_failures.append(
                f"seed {seed}: no-merge cases merged: {merged & no_merge_cases}"
            )

        # P5 — exact supplier↔company link (E7 links at 1.0, E8 never)
        if linked != set(expected["link_cases"]):
            p5_failures.append(f"seed {seed}: linked {sorted(linked)}")
        e7 = [link for link in record["links"] if link["case"] == "E7"]
        if e7 and e7[0]["score"] != 1.0:
            p5_failures.append(f"seed {seed} E7: score {e7[0]['score']} != 1.0")

    predictions: dict[str, dict[str, Any]] = {
        "P1": {
            "verdict": "refuted" if p1_failures else "success",
            "failures": p1_failures,
        },
        # P2 (homonyms) and P3 (name noise) are subsumed in the exact-set
        # check of P1: E3/E4/E5 merging or E2 failing flips P1.
        "P2": {"verdict": "refuted" if p1_failures else "success", "failures": []},
        "P3": {"verdict": "refuted" if p1_failures else "success", "failures": []},
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
    """Runs the battery: synthetic seeds + OS Pairs benchmark sample.

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs (``seed_<n>.jsonl``),
            the cached OS Pairs sample(s) (``pairs_sample[_<seed>].jsonl``)
            and ``summary.json``.

    Returns:
        The per-seed records (merges, links, repeat divergences).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []
    for seed in config["seeds"]:
        record = run_seed(config, seed)
        with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
            for row in [*record["merges"], *record["links"]]:
                fh.write(json.dumps(row, default=str) + "\n")
        records.append(record)

    summary = evaluate(config, records)
    spec = config["os_pairs"]
    sample_seeds: list[int] = spec.get("sample_seeds") or [spec.get("seed")]
    samples: dict[int, dict[str, Any]] = {}
    for sample_seed in sample_seeds:
        cache_name = (
            "pairs_sample.jsonl"
            if len(sample_seeds) == 1
            else f"pairs_sample_{sample_seed}.jsonl"
        )
        samples[sample_seed] = evaluate_os_pairs(
            config, sample_os_pairs(config, out_dir / cache_name, sample_seed)
        )
    os_summary: dict[str, Any] = {
        "samples": {str(seed): metrics for seed, metrics in samples.items()}
    }
    if len(samples) == 1:  # single-sample configs keep the flat shape
        os_summary.update(next(iter(samples.values())))
    summary["os_pairs"] = os_summary
    summary["predictions"]["P6"] = {
        "verdict": (
            "success"
            if all(m["p6"]["verdict"] == "success" for m in samples.values())
            else "refuted"
        ),
        "precision": min(m["precision"] for m in samples.values()),
    }
    summary["predictions"]["P7"] = {
        "verdict": (
            "success"
            if all(m["p7"]["verdict"] == "success" for m in samples.values())
            else "refuted"
        ),
        "recall": {str(seed): m["recall"] for seed, m in samples.items()},
    }
    if summary["predictions"]["P6"]["verdict"] != "success" or (
        summary["predictions"]["P7"]["verdict"] != "success"
    ):
        summary["verdict"] = "refuted"
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
    return records
