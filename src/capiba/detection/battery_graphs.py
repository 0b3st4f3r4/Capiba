"""Graph operators battery runner (bateria D-02).

Responsibility: Plant the synthetic graph with known ground truth (per
``experiments/detect/D-02.json``) into a disposable ArangoDB database,
invoke the adapted graph operators (``detect_collusion``,
``trace_ownership``) in-process and evaluate the pre-registered
predictions P1-P6 (``docs/preregistrations/PR-D-02.md``).

Doctrine: no battery without a pre-registration. The config is the
single source of parameters (seeds included); raw outputs are versioned
under ``results/detect/<id>/``. Unlike D-01 (offline), this battery
requires live ArangoDB infrastructure (``requires_infra: arangodb``):
the runner creates a disposable database, drops any leftover at start
and drops it again at the end.
"""

from __future__ import annotations

import json
import logging
import random
from datetime import date, timedelta
from itertools import combinations, pairwise
from pathlib import Path
from typing import Any

from arango.database import StandardDatabase

from capiba.config import ARANGODB_ROOT_PASSWORD
from capiba.db.arangodb import (
    ensure_collections,
    get_arango_client,
    get_system_db,
    upsert_edge,
    upsert_vertex,
)
from capiba.detection.graphs import detect_collusion, trace_ownership

logger = logging.getLogger(__name__)

_BASE = date(2026, 1, 1)

# Collections the adapted operators read (plain collections, no graph):
# documents and edges.
_DOCUMENT_COLLECTIONS = ["contracts", "suppliers", "companies"]
_EDGE_COLLECTIONS = ["won", "owns"]


def battery_database_name(config: dict[str, Any]) -> str:
    """Derives the disposable database name from the battery id."""
    return f"capiba_{config['id'].lower().replace('-', '')}_battery"


def _supplier_buyer_wins(config: dict[str, Any]) -> list[tuple[str, str, int]]:
    """Flattens the collusion spec into (supplier, buyer, wins) triples."""
    triples: list[tuple[str, str, int]] = []
    collusion = config["collusion"]
    planted = collusion["planted_buyer"]
    for supplier in planted["suppliers"]:
        triples.append((supplier["id"], planted["id"], supplier["wins"]))
    boundary = planted["boundary_supplier"]
    triples.append((boundary["id"], planted["id"], boundary["wins"]))
    control = collusion["control_same_buyer"]
    for supplier_id in control["suppliers"]:
        triples.append((supplier_id, control["buyer_id"], control["wins_each"]))
    solo = collusion["control_solo_buyers"]
    for buyer_id, supplier_id in zip(
        solo["buyer_ids"], solo["supplier_ids"], strict=True
    ):
        triples.append((supplier_id, buyer_id, solo["wins_each"]))
    return triples


def generate(config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Generates the synthetic graph documents for one seed.

    The graph structure is identical across seeds; the seed only
    randomizes neutral fields (names, dates, contract amounts).

    Args:
        config: Battery configuration (see ``experiments/detect/D-02.json``).
        seed: RNG seed (deterministic per seed).

    Returns:
        Dict with the ``suppliers``/``companies``/``contracts`` vertex
        documents and the ``won``/``owns`` edge documents.
    """
    rng = random.Random(seed)  # deterministic synthetic data, not cryptographic  # nosec B311
    suppliers: dict[str, dict[str, Any]] = {}
    contracts: list[dict[str, Any]] = []
    won: list[dict[str, str]] = []
    seq = 0

    for supplier_id, buyer_id, wins in _supplier_buyer_wins(config):
        suppliers.setdefault(
            supplier_id,
            {
                "_key": supplier_id,
                "legal_name": f"Fornecedor {supplier_id} {rng.randint(100, 999)}",
            },
        )
        for _ in range(wins):
            seq += 1
            start = _BASE + timedelta(days=rng.randint(0, 364))
            contracts.append(
                {
                    "_key": f"SYN-{seed}-{seq:04d}",
                    "buyer": {
                        "siafi_code": buyer_id,
                        "name": f"Comprador {buyer_id} {rng.randint(100, 999)}",
                    },
                    "supplier": {"cnpj": supplier_id},
                    "amount": round(rng.uniform(1000.0, 50000.0), 2),
                    "signature_date": start.isoformat(),
                }
            )
            won.append(
                {
                    "_from": f"suppliers/{supplier_id}",
                    "_to": f"contracts/SYN-{seed}-{seq:04d}",
                }
            )

    ownership = config["ownership"]
    chain = ownership["chain"]
    isolated = ownership["isolated"]
    cycle = ownership["cycle"]
    company_keys = [*chain, isolated, *cycle]
    companies = [
        {"_key": key, "legal_name": f"Empresa {key} {rng.randint(100, 999)}"}
        for key in dict.fromkeys(company_keys)
    ]
    owns = [
        {"_from": f"companies/{src}", "_to": f"companies/{dst}"}
        for src, dst in pairwise(chain)
    ]
    owns.append({"_from": f"companies/{cycle[0]}", "_to": f"companies/{cycle[1]}"})
    owns.append({"_from": f"companies/{cycle[1]}", "_to": f"companies/{cycle[0]}"})

    return {
        "suppliers": list(suppliers.values()),
        "companies": companies,
        "contracts": contracts,
        "won": won,
        "owns": owns,
    }


def plant(db: StandardDatabase, graph: dict[str, Any]) -> None:
    """Inserts the synthetic graph into the battery database."""
    for doc in graph["suppliers"]:
        upsert_vertex(db, "suppliers", doc["_key"], {"legal_name": doc["legal_name"]})
    for doc in graph["companies"]:
        upsert_vertex(db, "companies", doc["_key"], {"legal_name": doc["legal_name"]})
    for doc in graph["contracts"]:
        data = {k: v for k, v in doc.items() if k != "_key"}
        upsert_vertex(db, "contracts", doc["_key"], data)
    for edge in graph["won"]:
        upsert_edge(db, "won", edge["_from"], edge["_to"])
    for edge in graph["owns"]:
        upsert_edge(db, "owns", edge["_from"], edge["_to"])


def _clear_collections(db: StandardDatabase) -> None:
    """Truncates every battery collection (seed isolation)."""
    for name in [*_DOCUMENT_COLLECTIONS, *_EDGE_COLLECTIONS]:
        db.collection(name).truncate()


def run_seed(db: StandardDatabase, config: dict[str, Any], seed: int) -> dict[str, Any]:
    """Plants one seed and invokes the operators against the battery db.

    Returns:
        Per-seed record with the raw operator outputs (pairs as sorted
        two-element lists, paths as lists of vertex keys).
    """
    _clear_collections(db)
    plant(db, generate(config, seed))

    min_wins = config["collusion"]["min_wins"]
    ownership = config["ownership"]
    max_depth = ownership["max_depth"]

    pairs_min3 = [sorted(pair) for pair in detect_collusion(db, min_wins)]
    pairs_min2 = [sorted(pair) for pair in detect_collusion(db, min_wins - 1)]
    chain_depth3 = trace_ownership(ownership["chain"][0], max_depth, db)
    chain_depth2 = trace_ownership(ownership["chain"][0], max_depth - 1, db)
    isolated = trace_ownership(ownership["isolated"], max_depth, db)
    cycle = trace_ownership(ownership["cycle"][0], max_depth, db)

    return {
        "seed": seed,
        "collusion_min3": pairs_min3,
        "collusion_min2": pairs_min2,
        "ownership_chain_depth3": chain_depth3,
        "ownership_chain_depth2": chain_depth2,
        "ownership_isolated": isolated,
        "ownership_cycle": cycle,
    }


def _expected_collusion_pairs(
    config: dict[str, Any], min_wins: int
) -> set[frozenset[str]]:
    """Derives the exact expected pair set for a ``min_wins`` threshold."""
    wins_by_buyer: dict[str, dict[str, int]] = {}
    for supplier_id, buyer_id, wins in _supplier_buyer_wins(config):
        wins_by_buyer.setdefault(buyer_id, {})[supplier_id] = wins
    pairs: set[frozenset[str]] = set()
    for suppliers in wins_by_buyer.values():
        eligible = sorted(s for s, n in suppliers.items() if n >= min_wins)
        pairs.update(frozenset(pair) for pair in combinations(eligible, 2))
    return pairs


def evaluate(config: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P6 plus invariants.

    Pure (no ArangoDB): consumes the per-seed records produced by
    ``run_seed`` with the raw operator outputs.

    Args:
        config: Battery configuration (expectations included).
        records: Per-seed records with ``collusion_min3``,
            ``collusion_min2``, ``ownership_chain_depth3``,
            ``ownership_chain_depth2``, ``ownership_isolated`` and
            ``ownership_cycle``.

    Returns:
        Summary with a verdict per prediction (``success``/``refuted``),
        the monotonicity invariant and the overall battery verdict.
    """
    exp = config["expectations"]
    min_wins = config["collusion"]["min_wins"]
    expected_min3 = {frozenset(pair) for pair in exp["collusion_pairs_min3_exact"]}
    expected_min2 = _expected_collusion_pairs(config, min_wins - 1)
    expected_depth3 = {tuple(path) for path in exp["ownership_paths_depth3_exact"]}
    expected_cycle = {tuple(path) for path in exp["ownership_cycle_paths_exact"]}
    never_reaches = exp["ownership_depth2_never_reaches"]

    failures: dict[str, list[str]] = {f"P{i}": [] for i in range(1, 7)}
    monotonicity_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        got_min3 = {frozenset(pair) for pair in record["collusion_min3"]}
        got_min2 = {frozenset(pair) for pair in record["collusion_min2"]}

        # P1 — collusion recall: every planted pair shows up
        missing = sorted(expected_min3 - got_min3)
        if missing:
            failures["P1"].append(f"seed {seed}: missing pairs {missing}")

        # P2 — collusion precision: no false positives at min_wins
        extra = sorted(got_min3 - expected_min3)
        if len(extra) != exp["collusion_control_fp_min3"]:
            failures["P2"].append(f"seed {seed}: unexpected pairs {extra}")

        # P3 — min_wins boundary: exact pair set and count at min_wins - 1
        if got_min2 != expected_min2:
            failures["P3"].append(
                f"seed {seed}: min{min_wins - 1} pairs diverge: "
                f"missing {sorted(expected_min2 - got_min2)}, "
                f"extra {sorted(got_min2 - expected_min2)}"
            )
        if len(got_min2) != exp["collusion_pairs_min2_exact_count"]:
            failures["P3"].append(
                f"seed {seed}: {len(got_min2)} min{min_wins - 1} pairs"
                f" != {exp['collusion_pairs_min2_exact_count']}"
            )

        # P4 — exact ownership chain at max depth
        got_depth3 = {tuple(path) for path in record["ownership_chain_depth3"]}
        if got_depth3 != expected_depth3:
            failures["P4"].append(
                f"seed {seed}: depth3 paths diverge: "
                f"missing {sorted(expected_depth3 - got_depth3)}, "
                f"extra {sorted(got_depth3 - expected_depth3)}"
            )

        # P5 — depth boundary: exact count, deep vertex never reached
        got_depth2 = [tuple(path) for path in record["ownership_chain_depth2"]]
        if len(got_depth2) != exp["ownership_paths_depth2_count"]:
            failures["P5"].append(
                f"seed {seed}: {len(got_depth2)} depth2 paths"
                f" != {exp['ownership_paths_depth2_count']}"
            )
        if any(never_reaches in path for path in got_depth2):
            failures["P5"].append(f"seed {seed}: {never_reaches} reached at depth2")

        # P6 — isolation and cycle
        if len(record["ownership_isolated"]) != exp["ownership_isolated_paths"]:
            failures["P6"].append(
                f"seed {seed}: {len(record['ownership_isolated'])} isolated paths"
                f" != {exp['ownership_isolated_paths']}"
            )
        got_cycle = {tuple(path) for path in record["ownership_cycle"]}
        if got_cycle != expected_cycle:
            failures["P6"].append(
                f"seed {seed}: cycle paths diverge: "
                f"missing {sorted(expected_cycle - got_cycle)}, "
                f"extra {sorted(got_cycle - expected_cycle)}"
            )

        # Monotonicity invariant: min_wins pairs ⊆ (min_wins - 1) pairs
        if not got_min3 <= got_min2:
            monotonicity_failures.append(
                f"seed {seed}: min{min_wins} pairs not a subset of"
                f" min{min_wins - 1}: {sorted(got_min3 - got_min2)}"
            )

    predictions = {
        name: {"verdict": "refuted" if failed else "success", "failures": failed}
        for name, failed in failures.items()
    }
    invariants = {
        "monotonicity": {
            "verdict": "refuted" if monotonicity_failures else "success",
            "failures": monotonicity_failures,
        }
    }
    verdict = (
        "success"
        if all(p["verdict"] == "success" for p in predictions.values())
        and all(i["verdict"] == "success" for i in invariants.values())
        else "refuted"
    )
    return {
        "battery": config["id"],
        "predictions": predictions,
        "invariants": invariants,
        "verdict": verdict,
    }


def _seed_lines(config: dict[str, Any], record: dict[str, Any]) -> list[str]:
    """Serializes the raw operator outputs of one seed, one JSON per line."""
    min_wins = config["collusion"]["min_wins"]
    ownership = config["ownership"]
    max_depth = ownership["max_depth"]
    invocations = [
        (
            "detect_collusion",
            {"min_wins": min_wins},
            record["collusion_min3"],
        ),
        (
            "detect_collusion",
            {"min_wins": min_wins - 1},
            record["collusion_min2"],
        ),
        (
            "trace_ownership",
            {"cnpj": ownership["chain"][0], "max_depth": max_depth},
            record["ownership_chain_depth3"],
        ),
        (
            "trace_ownership",
            {"cnpj": ownership["chain"][0], "max_depth": max_depth - 1},
            record["ownership_chain_depth2"],
        ),
        (
            "trace_ownership",
            {"cnpj": ownership["isolated"], "max_depth": max_depth},
            record["ownership_isolated"],
        ),
        (
            "trace_ownership",
            {"cnpj": ownership["cycle"][0], "max_depth": max_depth},
            record["ownership_cycle"],
        ),
    ]
    return [
        json.dumps({"operator": op, **params, "output": output}, default=str)
        for op, params, output in invocations
    ]


def run_battery(config: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Runs the battery: plant, invoke operators, persist outputs/summary.

    Creates a disposable ArangoDB database (dropping any leftover at
    start) and drops it again at the end, success or failure.

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs (``seed_<n>.jsonl``)
            and ``summary.json``.

    Returns:
        The per-seed records (raw operator outputs).
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    db_name = battery_database_name(config)
    sys_db = get_system_db()
    if sys_db.has_database(db_name):
        sys_db.delete_database(db_name)
    sys_db.create_database(db_name)
    logger.info("Battery database created: %s", db_name)

    try:
        db = get_arango_client().db(
            db_name, username="root", password=ARANGODB_ROOT_PASSWORD
        )
        ensure_collections(db)
        records: list[dict[str, Any]] = []
        for seed in config["seeds"]:
            record = run_seed(db, config, seed)
            with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
                for line in _seed_lines(config, record):
                    fh.write(line + "\n")
            records.append(record)
        summary = evaluate(config, records)
        (out_dir / "summary.json").write_text(json.dumps(summary, indent=2) + "\n")
        return records
    finally:
        sys_db.delete_database(db_name)
        logger.info("Battery database dropped: %s", db_name)
