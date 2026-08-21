"""Collusion calibration battery runner (baterias D-03, D-03b e D-03c).

Responsibility: calibrate the thresholds of the ``collusion_network``
signal over the real accumulated graph and validate the reproducible
graph evidence package, per the pre-registered designs in
``docs/preregistrations/PR-D-03.md`` (config ``experiments/detect/D-03.json``),
``docs/preregistrations/PR-D-03b.md`` (config
``experiments/detect/D-03b.json`` — refined co-occurrence semantics:
pairs eligible in >= ``min_buyers`` distinct buyers, grid
``(min_wins, min_buyers)``; detected by ``candidates_min_wins`` in the
calibration block) and ``docs/preregistrations/PR-D-03c.md`` (config
``experiments/detect/D-03c.json`` — exact-recall blocking of the pair
derivation, bit-a-bit equivalence against the unblocked path,
before/after arithmetic projections, per-path time/heap and the
deterministic ordered emission; detected by ``blocking`` in the
calibration block). ``docs/preregistrations/PR-D-03d.md`` (config
``experiments/detect/D-03d.json`` — declared top-K ranked emission over
the blocked derivation with an explicit editorial budget, prefix
equivalence against the full ordered set and evidence reproduction with
declared truncation; detected by ``emission`` in the calibration block,
which takes precedence over ``blocking``).

Part A/C (synthetic): plants the collusion population — with the
pre-registered 30-day date windows — into a disposable ArangoDB database
and checks the exact anchors (D-03: P1-P4 histogram/counts/increment/
evidence; D-03b: Q1-Q5 control points with buyer annotation).

Part B (real sweep): read-only measurement over the production graph —
eligibility export, double counting (AQL aggregation vs Python
recomputation), siafi coverage, operational budget — and applies the
pre-registered decision rule. Pair sets are only materialized at the
calibrated candidate and under the triage budget.

Doctrine: no battery without a pre-registration. The config is the single
source of parameters (seeds included); raw outputs are versioned under
``results/detect/<id>/``.
"""

from __future__ import annotations

import json
import logging
import random
import time
import tracemalloc
from collections.abc import Callable
from datetime import date, timedelta
from pathlib import Path
from typing import Any, cast

from arango.database import StandardDatabase

from capiba.config import ARANGODB_ROOT_PASSWORD
from capiba.db.arangodb import (
    ensure_collections,
    execute_aql,
    get_arango_client,
    get_capiba_db,
    get_system_db,
    upsert_edge,
    upsert_vertex,
)
from capiba.db.triage import signal_key
from batteries.battery_graphs import battery_database_name
from capiba.detection.graphs import (
    _blocked_buyers_by_pair,
    blocked_projection,
    collusion_eligibility,
    pair_buyers_from_eligibility,
    pair_buyers_from_eligibility_blocked,
    pairs_from_eligibility,
    projected_pair_count,
    ranked_emission,
)
from capiba.detection.signals import collusion_signals
from capiba.evidence import packages as evidence_packages

logger = logging.getLogger(__name__)

# Full eligibility export (no threshold): one row per (buyer, supplier)
# with total wins and wins inside the increment/robustness windows.
_EXPORT_QUERY = """
    FOR c IN contracts
        FILTER c.buyer.siafi_code != null
        FOR s IN INBOUND c won
            COLLECT buyer = c.buyer.siafi_code, supplier = s._key INTO g
            RETURN {
                buyer,
                supplier,
                wins: LENGTH(g),
                recent_wins: LENGTH(
                    g[* FILTER CURRENT.c.signature_date >= @cutoff]
                ),
                robust_wins: LENGTH(
                    g[* FILTER CURRENT.c.signature_date >= @cutoffRobust]
                )
            }
"""

# Won edges whose contract has a non-null buyer siafi code (coverage, P6).
_COVERAGE_QUERY = """
    FOR e IN won
        LET c = DOCUMENT(e._to)
        FILTER c.buyer.siafi_code != null
        COLLECT WITH COUNT INTO n
        RETURN n
"""

_DOCUMENT_COLLECTIONS = ["contracts", "suppliers"]
_EDGE_COLLECTIONS = ["won"]


def _supplier_buyer_wins(config: dict[str, Any]) -> list[tuple[str, str, int, bool]]:
    """Flattens the synthetic spec into (supplier, buyer, wins, recent) tuples."""
    tuples: list[tuple[str, str, int, bool]] = []
    synthetic = config["synthetic"]
    planted = synthetic["planted_buyer"]
    planted_recent = bool(planted["within_increment_window"])
    for supplier in planted["suppliers"]:
        tuples.append((supplier["id"], planted["id"], supplier["wins"], planted_recent))
    boundary = planted["boundary_supplier"]
    tuples.append((boundary["id"], planted["id"], boundary["wins"], planted_recent))

    # PR-D-03b blocks (optional): pairs co-occurring across distinct buyers.
    itinerant = synthetic.get("itinerant_pair")
    if itinerant is not None:
        for buyer in itinerant["buyers"]:
            for supplier_id in itinerant["supplier_ids"]:
                tuples.append(
                    (
                        supplier_id,
                        buyer["id"],
                        int(buyer["wins_each"]),
                        bool(buyer["within_increment_window"]),
                    )
                )
    boundary_pair = synthetic.get("boundary_pair")
    if boundary_pair is not None:
        recent_by_buyer = boundary_pair["buyers_within_increment_window"]
        for supplier in boundary_pair["suppliers"]:
            for buyer_id, wins in supplier["wins_by_buyer"].items():
                tuples.append(
                    (supplier["id"], buyer_id, int(wins), bool(recent_by_buyer[buyer_id]))
                )

    control = synthetic["control_same_buyer"]
    for supplier_id in control["suppliers"]:
        tuples.append(
            (
                supplier_id,
                control["buyer_id"],
                control["wins_each"],
                bool(control["within_increment_window"]),
            )
        )
    solo = synthetic["control_solo_buyers"]
    for buyer_id, supplier_id in zip(
        solo["buyer_ids"], solo["supplier_ids"], strict=True
    ):
        tuples.append(
            (
                supplier_id,
                buyer_id,
                solo["wins_each"],
                bool(solo["within_increment_window"]),
            )
        )
    return tuples


def _graph_from_tuples(
    tuples: list[tuple[str, str, int, bool]],
    seed: int,
    reference_date: date,
    window: int,
    key_prefix: str,
) -> dict[str, Any]:
    """Builds the graph documents from (supplier, buyer, wins, recent)."""
    rng = random.Random(
        seed
    )  # deterministic synthetic data, not cryptographic  # nosec B311
    suppliers: dict[str, dict[str, Any]] = {}
    contracts: list[dict[str, Any]] = []
    won: list[dict[str, str]] = []
    seq = 0

    for supplier_id, buyer_id, wins, recent in tuples:
        suppliers.setdefault(
            supplier_id,
            {
                "_key": supplier_id,
                "legal_name": f"Fornecedor {supplier_id} {rng.randint(100, 999)}",
            },
        )
        for _ in range(wins):
            seq += 1
            days_back = (
                rng.randint(0, window - 1)
                if recent
                else rng.randint(window + 1, window + 90)
            )
            signature = reference_date - timedelta(days=days_back)
            contracts.append(
                {
                    "_key": f"{key_prefix}{seq:04d}",
                    "buyer": {
                        "siafi_code": buyer_id,
                        "name": f"Comprador {buyer_id} {rng.randint(100, 999)}",
                    },
                    "supplier": {"cnpj": supplier_id},
                    "amount": round(rng.uniform(1000.0, 50000.0), 2),
                    "signature_date": signature.isoformat(),
                }
            )
            won.append(
                {
                    "_from": f"suppliers/{supplier_id}",
                    "_to": f"contracts/{key_prefix}{seq:04d}",
                }
            )

    return {"suppliers": list(suppliers.values()), "contracts": contracts, "won": won}


def generate(config: dict[str, Any], seed: int, reference_date: date) -> dict[str, Any]:
    """Generates the synthetic graph documents for one seed.

    The graph structure is identical across seeds; the seed only
    randomizes neutral fields (names, amounts, exact signature dates
    **within** the pre-registered windows: contracts flagged
    ``within_increment_window`` fall in the last ``increment_window_days``
    days before ``reference_date``; the others fall before the window).

    Args:
        config: Battery configuration (see ``experiments/detect/D-03.json``).
        seed: RNG seed (deterministic per seed and reference date).
        reference_date: Sweep date (window anchor).

    Returns:
        Dict with the ``suppliers``/``contracts`` vertex documents and the
        ``won`` edge documents.
    """
    window = int(config["calibration"]["increment_window_days"])
    return _graph_from_tuples(
        _supplier_buyer_wins(config), seed, reference_date, window, f"SYN-{seed}-"
    )


def _stress_tuples(config: dict[str, Any]) -> list[tuple[str, str, int, bool]]:
    """Flattens the Part A-stress block (PR-D-03c) into tuples.

    One large buyer with N exclusive suppliers × ``wins_each`` wins; the
    supplier ids are deterministic (``98`` prefix, unused by the base
    population).
    """
    stress = config["calibration"].get("stress")
    if stress is None:
        return []
    buyer_id = str(stress["buyer_id"])
    wins = int(stress["wins_each"])
    recent = bool(stress["within_increment_window"])
    return [
        (f"98{i + 1:012d}", buyer_id, wins, recent)
        for i in range(int(stress["suppliers"]))
    ]


def generate_stress(
    config: dict[str, Any], seed: int, reference_date: date
) -> dict[str, Any]:
    """Generates only the Part A-stress graph documents (PR-D-03c).

    Planted **on top of** the base population of ``generate`` (same seed):
    the big buyer's suppliers are exclusive, so the unblocked projection
    grows by exactly C(suppliers, 2) while the blocked projection at
    ``min_buyers >= 2`` is untouched (R5 anchors).
    """
    window = int(config["calibration"]["increment_window_days"])
    return _graph_from_tuples(
        _stress_tuples(config), seed, reference_date, window, f"SYN-{seed}-S"
    )


def plant(db: StandardDatabase, graph: dict[str, Any]) -> None:
    """Inserts the synthetic graph into the battery database."""
    for doc in graph["suppliers"]:
        upsert_vertex(db, "suppliers", doc["_key"], {"legal_name": doc["legal_name"]})
    for doc in graph["contracts"]:
        data = {k: v for k, v in doc.items() if k != "_key"}
        upsert_vertex(db, "contracts", doc["_key"], data)
    for edge in graph["won"]:
        upsert_edge(db, "won", edge["_from"], edge["_to"])


def _clear_collections(db: StandardDatabase) -> None:
    """Truncates every battery collection (seed isolation)."""
    for name in [*_DOCUMENT_COLLECTIONS, *_EDGE_COLLECTIONS]:
        db.collection(name).truncate()


def histogram_from_rows(rows: list[dict[str, Any]]) -> dict[int, int]:
    """Wins histogram over the exported (buyer, supplier) rows."""
    histogram: dict[int, int] = {}
    for row in rows:
        wins = int(row["wins"])
        histogram[wins] = histogram.get(wins, 0) + 1
    return dict(sorted(histogram.items()))


def _total_wins(row: dict[str, Any]) -> int:
    """Default win extractor: the exported total win count."""
    return int(row["wins"])


def _eligible_row(row: dict[str, Any]) -> int:
    """AQL-path extractor: eligibility rows are already threshold-filtered."""
    return 1


def _pair_count(
    rows: list[dict[str, Any]],
    min_wins: int,
    wins_of: Callable[[dict[str, Any]], int] = _total_wins,
) -> int:
    """Arithmetic pair count: sum over buyers of C(eligible, 2)."""
    eligible_by_buyer: dict[str, int] = {}
    for row in rows:
        if wins_of(row) >= min_wins:
            eligible_by_buyer[row["buyer"]] = eligible_by_buyer.get(row["buyer"], 0) + 1
    return sum(k * (k - 1) // 2 for k in eligible_by_buyer.values())


def pair_counts(
    rows: list[dict[str, Any]],
    candidates: list[int],
    wins_of: Callable[[dict[str, Any]], int] = _total_wins,
) -> dict[int, int]:
    """Arithmetic pair counts per candidate threshold (no materialization)."""
    return {w: _pair_count(rows, w, wins_of) for w in sorted(candidates)}


def increments(
    rows: list[dict[str, Any]],
    candidates: list[int],
    window_days: int,
    recent_key: str = "recent_wins",
) -> dict[int, float]:
    """Daily pair increment per candidate: (full − excluding window) / days."""
    full = pair_counts(rows, candidates)
    old = pair_counts(
        rows, candidates, lambda row: int(row["wins"]) - int(row[recent_key])
    )
    return {w: (full[w] - old[w]) / window_days for w in sorted(candidates)}


def decide(
    counts: dict[int, int],
    daily_increments: dict[int, float],
    candidates: list[int],
    budget: dict[str, Any],
) -> int | None:
    """Pre-registered decision rule (PR-D-03, section 3).

    Returns the smallest candidate whose total pair count fits the
    editorial backlog budget and whose daily increment fits the daily
    budget; None when no candidate qualifies (inconclusive battery).
    """
    for w in sorted(candidates):
        if (
            counts[w] <= int(budget["backlog_max_pairs"])
            and daily_increments[w] <= float(budget["daily_max_pairs"])
        ):
            return w
    return None


# ---------------------------------------------------------------------------
# Refined mode (PR-D-03b): (min_wins, min_buyers) grid
# ---------------------------------------------------------------------------


def is_refined(config: dict[str, Any]) -> bool:
    """True when the config declares the PR-D-03b (w, n) candidate grid."""
    return "candidates_min_wins" in config["calibration"]


def grid_order(
    candidates_w: list[int], candidates_n: list[int]
) -> list[tuple[int, int]]:
    """Pre-registered decision order: min_buyers outer, min_wins inner."""
    return [(w, n) for n in sorted(candidates_n) for w in sorted(candidates_w)]


def _pair_buyer_count(
    rows: list[dict[str, Any]],
    min_wins: int,
    min_buyers: int,
    wins_of: Callable[[dict[str, Any]], int] = _total_wins,
) -> int:
    """Pair count under the refined semantics: >= min_buyers distinct buyers."""
    eligible = [row for row in rows if wins_of(row) >= min_wins]
    return len(pair_buyers_from_eligibility(eligible, min_buyers))


def pair_counts_grid(
    rows: list[dict[str, Any]],
    candidates_w: list[int],
    candidates_n: list[int],
    wins_of: Callable[[dict[str, Any]], int] = _total_wins,
) -> dict[str, int]:
    """Arithmetic pair counts per (w, n) grid point (no materialization)."""
    return {
        f"{w}:{n}": _pair_buyer_count(rows, w, n, wins_of)
        for w, n in grid_order(candidates_w, candidates_n)
    }


def increments_grid(
    rows: list[dict[str, Any]],
    candidates_w: list[int],
    candidates_n: list[int],
    window_days: int,
    recent_key: str = "recent_wins",
) -> dict[str, float]:
    """Daily pair increment per grid point: (full − excluding window) / days."""
    full = pair_counts_grid(rows, candidates_w, candidates_n)
    old = pair_counts_grid(
        rows,
        candidates_w,
        candidates_n,
        lambda row: int(row["wins"]) - int(row[recent_key]),
    )
    return {key: (full[key] - old[key]) / window_days for key in full}


def decide_grid(
    counts: dict[str, int],
    daily_increments: dict[str, float],
    candidates_w: list[int],
    candidates_n: list[int],
    budget: dict[str, Any],
) -> tuple[int, int] | None:
    """Pre-registered decision rule (PR-D-03b, section 4).

    Walks the grid in the pre-registered order (min_buyers outer,
    min_wins inner) and returns the first (w, n) whose total pair count
    fits the editorial backlog budget and whose daily increment fits the
    daily budget; None when no grid point qualifies (inconclusive).
    """
    for w, n in grid_order(candidates_w, candidates_n):
        key = f"{w}:{n}"
        if (
            counts[key] <= int(budget["backlog_max_pairs"])
            and daily_increments[key] <= float(budget["daily_max_pairs"])
        ):
            return (w, n)
    return None


def measure(
    db: StandardDatabase, config: dict[str, Any], reference_date: date
) -> dict[str, Any]:
    """Sweeps the graph: export, double counting, coverage, increment, rule.

    Read-only. Pair sets are materialized only at the calibrated
    ``min_wins`` and only when the count fits the backlog budget (the
    non-materialization invariant, P7).

    Args:
        db: ArangoDB connection (battery or production database).
        config: Battery configuration (calibration block).
        reference_date: Sweep date (anchor of the increment windows).

    Returns:
        Measurement dict (histogram, counts per candidate via both paths,
        increments, coverage, elapsed time, calibrated ``min_wins``).
    """
    calibration = config["calibration"]
    candidates = sorted(int(w) for w in calibration["candidates"])
    window = int(calibration["increment_window_days"])
    robust_window = int(calibration["robustness_window_days"])
    cutoff = (reference_date - timedelta(days=window)).isoformat()
    cutoff_robust = (reference_date - timedelta(days=robust_window)).isoformat()

    started = time.monotonic()

    rows = execute_aql(
        db, _EXPORT_QUERY, {"cutoff": cutoff, "cutoffRobust": cutoff_robust}
    )
    histogram = histogram_from_rows(rows)
    counts_python = pair_counts(rows, candidates)
    daily = increments(rows, candidates, window)
    daily_robust = increments(rows, candidates, robust_window, "robust_wins")

    # AQL aggregation path: the eligibility query applies the threshold;
    # the pair count is arithmetic over the returned rows (each counts as
    # one eligible supplier — threshold already applied by the query).
    counts_aql = {
        w: _pair_count(collusion_eligibility(db, min_wins=w), 1, _eligible_row)
        for w in candidates
    }

    # Window control (P3): w = 2 is out of the decision candidates (PR §3)
    # but its recent-pair count is measured to validate the window logic.
    control_w = int(calibration.get("control_min_wins", 2))
    control_full = pair_counts(rows, [control_w])[control_w]
    control_old = pair_counts(
        rows, [control_w], lambda row: int(row["wins"]) - int(row["recent_wins"])
    )[control_w]

    total_won = cast("int", db.collection("won").count())
    coverage_rows = execute_aql(db, _COVERAGE_QUERY, {})
    eligible_won = cast("int", coverage_rows[0]) if coverage_rows else 0
    coverage = eligible_won / total_won if total_won else 1.0

    calibrated = decide(counts_python, daily, candidates, calibration["budget"])

    materialized: int | None = None
    if calibrated is not None and counts_python[calibrated] <= int(
        calibration["budget"]["backlog_max_pairs"]
    ):
        materialized = len(
            pairs_from_eligibility(collusion_eligibility(db, min_wins=calibrated))
        )

    elapsed = time.monotonic() - started
    return {
        "reference_date": reference_date.isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "candidates": candidates,
        "exported_rows": len(rows),
        "histogram": {str(k): v for k, v in histogram.items()},
        "histogram_sum_wins": sum(int(row["wins"]) for row in rows),
        "pairs_by_candidate_python": {str(w): n for w, n in counts_python.items()},
        "pairs_by_candidate_aql": {str(w): n for w, n in counts_aql.items()},
        "increment_daily": {str(w): round(v, 4) for w, v in daily.items()},
        "increment_daily_robust": {
            str(w): round(v, 4) for w, v in daily_robust.items()
        },
        "control_min2": {
            "pairs_full": control_full,
            "recent_pairs": control_full - control_old,
        },
        "total_won_edges": total_won,
        "eligible_won_edges": eligible_won,
        "siafi_coverage": round(coverage, 6),
        "calibrated_min_wins": calibrated,
        "materialized_pairs": materialized,
    }


def measure_refined(
    db: StandardDatabase, config: dict[str, Any], reference_date: date
) -> dict[str, Any]:
    """Sweeps the graph over the (min_wins, min_buyers) grid (PR-D-03b).

    Read-only. Pair sets are materialized only at the calibrated grid
    point and only when the count fits the backlog budget.

    Args:
        db: ArangoDB connection (battery or production database).
        config: Battery configuration (calibration block with
            ``candidates_min_wins``/``candidates_min_buyers``).
        reference_date: Sweep date (anchor of the increment windows).

    Returns:
        Measurement dict (histogram, counts per grid point via both
        paths, increments, coverage, elapsed time, calibrated grid point).
    """
    calibration = config["calibration"]
    candidates_w = sorted(int(w) for w in calibration["candidates_min_wins"])
    candidates_n = sorted(int(n) for n in calibration["candidates_min_buyers"])
    window = int(calibration["increment_window_days"])
    robust_window = int(calibration["robustness_window_days"])
    cutoff = (reference_date - timedelta(days=window)).isoformat()
    cutoff_robust = (reference_date - timedelta(days=robust_window)).isoformat()

    started = time.monotonic()

    rows = execute_aql(
        db, _EXPORT_QUERY, {"cutoff": cutoff, "cutoffRobust": cutoff_robust}
    )
    histogram = histogram_from_rows(rows)
    counts_python = pair_counts_grid(rows, candidates_w, candidates_n)
    daily = increments_grid(rows, candidates_w, candidates_n, window)
    daily_robust = increments_grid(
        rows, candidates_w, candidates_n, robust_window, "robust_wins"
    )

    # AQL aggregation path: the eligibility query applies the win
    # threshold server-side; the buyer-co-occurrence filter reuses the
    # production pure function over the returned rows.
    counts_aql = {
        f"{w}:{n}": len(
            pair_buyers_from_eligibility(collusion_eligibility(db, min_wins=w), n)
        )
        for w, n in grid_order(candidates_w, candidates_n)
    }

    # Window control: the (control_min_wins, control_min_buyers) point is
    # out of the decision candidates but measured to validate the windows.
    control_w = int(calibration.get("control_min_wins", 2))
    control_n = int(calibration.get("control_min_buyers", 2))
    control_full = _pair_buyer_count(rows, control_w, control_n)
    control_old = _pair_buyer_count(
        rows,
        control_w,
        control_n,
        lambda row: int(row["wins"]) - int(row["recent_wins"]),
    )

    total_won = cast("int", db.collection("won").count())
    coverage_rows = execute_aql(db, _COVERAGE_QUERY, {})
    eligible_won = cast("int", coverage_rows[0]) if coverage_rows else 0
    coverage = eligible_won / total_won if total_won else 1.0

    calibrated = decide_grid(
        counts_python, daily, candidates_w, candidates_n, calibration["budget"]
    )

    materialized: int | None = None
    if calibrated is not None and counts_python[f"{calibrated[0]}:{calibrated[1]}"] <= int(
        calibration["budget"]["backlog_max_pairs"]
    ):
        materialized = len(
            pair_buyers_from_eligibility(
                collusion_eligibility(db, min_wins=calibrated[0]), calibrated[1]
            )
        )

    elapsed = time.monotonic() - started
    return {
        "reference_date": reference_date.isoformat(),
        "elapsed_seconds": round(elapsed, 3),
        "candidates_min_wins": candidates_w,
        "candidates_min_buyers": candidates_n,
        "grid_order": [f"{w}:{n}" for w, n in grid_order(candidates_w, candidates_n)],
        "exported_rows": len(rows),
        "histogram": {str(k): v for k, v in histogram.items()},
        "histogram_sum_wins": sum(int(row["wins"]) for row in rows),
        "pairs_grid_python": counts_python,
        "pairs_grid_aql": counts_aql,
        "increment_daily_grid": {k: round(v, 4) for k, v in daily.items()},
        "increment_daily_robust_grid": {
            k: round(v, 4) for k, v in daily_robust.items()
        },
        "control": {
            "min_wins": control_w,
            "min_buyers": control_n,
            "pairs_full": control_full,
            "recent_pairs": control_full - control_old,
        },
        "total_won_edges": total_won,
        "eligible_won_edges": eligible_won,
        "siafi_coverage": round(coverage, 6),
        "calibrated": (
            {"min_wins": calibrated[0], "min_buyers": calibrated[1]}
            if calibrated is not None
            else None
        ),
        "materialized_pairs": materialized,
    }


def _evidence_check(db: StandardDatabase, min_wins: int) -> dict[str, Any]:
    """Part C: builds the graph evidence package and reproduces it (exact).

    Reproduction of every emitted signal must match; removing one snapshot
    row must break integrity and the match (P4).
    """
    rows = collusion_eligibility(db, min_wins=min_wins)
    signals = collusion_signals(pairs_from_eligibility(rows), min_wins)
    package = evidence_packages.build_graph_batch_package(rows, signals, min_wins, None)

    keys = [
        signal_key(
            str(signal["entity_type"]),
            str(signal["entity_id"]),
            str(signal["signal_type"]),
        )
        for signal in package["signals"]
    ]
    matches = [
        evidence_packages.reproduce_signal(package, key)["match"] for key in keys
    ]

    tampered = json.loads(json.dumps(package))
    tampered["snapshot_rows"] = tampered["snapshot_rows"][1:]
    tampered_outcome = (
        evidence_packages.reproduce_signal(tampered, keys[0]) if keys else None
    )
    return {
        "signals": len(signals),
        "matches": matches,
        "tampered": tampered_outcome,
    }


def _evidence_check_refined(
    db: StandardDatabase, min_wins: int, min_buyers: int
) -> dict[str, Any]:
    """Part C (refined): evidence package at (min_wins, min_buyers).

    Reproduction of every emitted signal must match; removing one snapshot
    row must break integrity and the match (Q5).
    """
    rows = collusion_eligibility(db, min_wins=min_wins)
    pair_buyers = pair_buyers_from_eligibility(rows, min_buyers)
    signals = collusion_signals(
        [set(pair) for pair, _ in pair_buyers],
        min_wins,
        min_buyers,
        dict(pair_buyers),
    )
    package = evidence_packages.build_graph_batch_package(
        rows, signals, min_wins, None, min_buyers
    )

    keys = [
        signal_key(
            str(signal["entity_type"]),
            str(signal["entity_id"]),
            str(signal["signal_type"]),
        )
        for signal in package["signals"]
    ]
    matches = [
        evidence_packages.reproduce_signal(package, key)["match"] for key in keys
    ]

    tampered = json.loads(json.dumps(package))
    tampered["snapshot_rows"] = tampered["snapshot_rows"][1:]
    tampered_outcome = (
        evidence_packages.reproduce_signal(tampered, keys[0]) if keys else None
    )
    return {
        "signals": len(signals),
        "matches": matches,
        "tampered": tampered_outcome,
    }


def run_seed(
    db: StandardDatabase, config: dict[str, Any], seed: int
) -> dict[str, Any]:
    """Plants one seed, sweeps it and checks the evidence reproduction.

    Returns:
        Per-seed record with the raw measurement and evidence outcomes.
    """
    _clear_collections(db)
    reference_date = date.today()
    plant(db, generate(config, seed, reference_date))
    measurement = measure(db, config, reference_date)
    candidates = measurement["candidates"]
    return {
        "seed": seed,
        "histogram": measurement["histogram"],
        "pairs_by_candidate": measurement["pairs_by_candidate_aql"],
        "pairs_by_candidate_python": measurement["pairs_by_candidate_python"],
        "increment_daily": measurement["increment_daily"],
        "control_min2": measurement["control_min2"],
        "siafi_coverage": measurement["siafi_coverage"],
        "evidence": _evidence_check(db, min(candidates)),
    }


def run_seed_refined(
    db: StandardDatabase, config: dict[str, Any], seed: int
) -> dict[str, Any]:
    """Plants one seed and measures the refined control points (PR-D-03b).

    The synthetic population is tiny, so the pair sets (with the buyer
    annotation) are materialized at every pre-registered control point:
    ``(min_w, 1)``, ``(min_w, min_n)``, ``(min_w + 1, min_n)`` and the
    control ``(control_min_wins, control_min_buyers)``.

    Returns:
        Per-seed record with the control points, the window increment at
        the target point and the evidence reproduction outcomes.
    """
    _clear_collections(db)
    reference_date = date.today()
    plant(db, generate(config, seed, reference_date))

    calibration = config["calibration"]
    min_w = min(int(w) for w in calibration["candidates_min_wins"])
    min_n = min(int(n) for n in calibration["candidates_min_buyers"])
    control_w = int(calibration.get("control_min_wins", 2))
    control_n = int(calibration.get("control_min_buyers", 2))
    points = [(min_w, 1), (min_w, min_n), (min_w + 1, min_n), (control_w, control_n)]

    control_points: dict[str, Any] = {}
    for w, n in points:
        rows = collusion_eligibility(db, min_wins=w)
        pair_buyers = pair_buyers_from_eligibility(rows, n)
        control_points[f"{w}:{n}"] = {
            "count": len(pair_buyers),
            "pairs": [list(pair) for pair, _ in pair_buyers],
            "buyers": {"+".join(pair): buyers for pair, buyers in pair_buyers},
        }

    window = int(calibration["increment_window_days"])
    robust_window = int(calibration["robustness_window_days"])
    export_rows = execute_aql(
        db,
        _EXPORT_QUERY,
        {
            "cutoff": (reference_date - timedelta(days=window)).isoformat(),
            "cutoffRobust": (reference_date - timedelta(days=robust_window)).isoformat(),
        },
    )
    full = _pair_buyer_count(export_rows, min_w, min_n)
    old = _pair_buyer_count(
        export_rows,
        min_w,
        min_n,
        lambda row: int(row["wins"]) - int(row["recent_wins"]),
    )

    total_won = cast("int", db.collection("won").count())
    coverage_rows = execute_aql(db, _COVERAGE_QUERY, {})
    eligible_won = cast("int", coverage_rows[0]) if coverage_rows else 0
    coverage = eligible_won / total_won if total_won else 1.0

    return {
        "seed": seed,
        "histogram": histogram_from_rows(export_rows),
        "target_point": f"{min_w}:{min_n}",
        "control_points": control_points,
        "pairs_full_target": full,
        "recent_pairs_target": full - old,
        "siafi_coverage": round(coverage, 6),
        "evidence": _evidence_check_refined(db, min_w, min_n),
    }


def evaluate_synthetic(
    config: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Evaluates the pre-registered predictions P1-P4 plus invariants.

    Pure (no ArangoDB): consumes the per-seed records produced by
    ``run_seed``.

    Args:
        config: Battery configuration (expectations included).
        records: Per-seed records.

    Returns:
        Summary with a verdict per prediction (``success``/``refuted``),
        the monotonicity invariant and the overall battery verdict.
    """
    exp = config["expectations"]
    window = int(config["calibration"]["increment_window_days"])
    failures: dict[str, list[str]] = {f"P{i}": [] for i in range(1, 5)}
    monotonicity_failures: list[str] = []

    for record in records:
        seed = record["seed"]

        # P1 — exact wins histogram
        if record["histogram"] != exp["histogram_exact"]:
            failures["P1"].append(
                f"seed {seed}: histogram {record['histogram']}"
                f" != {exp['histogram_exact']}"
            )

        # P2 — exact pair counts per candidate (AQL aggregation path)
        got_counts = record["pairs_by_candidate"]
        expected_counts = exp["pairs_by_candidate_exact"]
        if got_counts != expected_counts:
            failures["P2"].append(
                f"seed {seed}: pairs by candidate {got_counts} != {expected_counts}"
            )
        # the Python recomputation must agree with the AQL aggregation
        if record["pairs_by_candidate_python"] != got_counts:
            failures["P2"].append(
                f"seed {seed}: python counts {record['pairs_by_candidate_python']}"
                f" diverge from AQL {got_counts}"
            )

        # P3 — exact 30-day increment (recent pairs = increment × window)
        recent3 = round(record["increment_daily"]["3"] * window)
        recent2 = record["control_min2"]["recent_pairs"]
        if recent3 != exp["recent_pairs_min3_exact"]:
            failures["P3"].append(
                f"seed {seed}: recent pairs min3 {recent3}"
                f" != {exp['recent_pairs_min3_exact']}"
            )
        if recent2 != exp["recent_pairs_min2_control_exact"]:
            failures["P3"].append(
                f"seed {seed}: recent pairs min2 {recent2}"
                f" != {exp['recent_pairs_min2_control_exact']}"
            )

        # P4 — exact evidence reproduction; tampering breaks it
        evidence = record["evidence"]
        if not evidence["matches"] or not all(evidence["matches"]):
            failures["P4"].append(
                f"seed {seed}: reproduction matches {evidence['matches']}"
            )
        tampered = evidence["tampered"]
        if tampered is None or tampered["integrity"] or tampered["match"]:
            failures["P4"].append(f"seed {seed}: tampered outcome {tampered}")

        # Monotonicity invariant: pair counts non-increasing over candidates
        counts = [
            (int(w), n)
            for w, n in sorted(got_counts.items(), key=lambda kv: int(kv[0]))
        ]
        for (w_lo, n_lo), (w_hi, n_hi) in zip(counts, counts[1:], strict=False):
            if n_hi > n_lo:
                monotonicity_failures.append(
                    f"seed {seed}: pairs({w_hi})={n_hi} > pairs({w_lo})={n_lo}"
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
        "part": "synthetic",
        "predictions": predictions,
        "invariants": invariants,
        "verdict": verdict,
    }


def evaluate_real(
    config: dict[str, Any], measurement: dict[str, Any]
) -> dict[str, Any]:
    """Evaluates the pre-registered real-sweep predictions P5-P8.

    Pure: consumes the measurement produced by ``measure`` over the
    production graph. P6/P8 yield ``inconclusive`` (not ``refuted``) when
    the regime is degraded or no candidate fits the triage budget —
    measured properties of the regime, per PR-D-03 sections 3 and 5.
    """
    calibration = config["calibration"]
    counts_aql = measurement["pairs_by_candidate_aql"]
    counts_python = measurement["pairs_by_candidate_python"]
    materialized = measurement["materialized_pairs"]
    calibrated = measurement["calibrated_min_wins"]

    p5_failures: list[str] = []
    if counts_aql != counts_python:
        p5_failures.append(
            f"AQL counts {counts_aql} diverge from Python {counts_python}"
        )

    p6_failures: list[str] = []
    if measurement["histogram_sum_wins"] != measurement["eligible_won_edges"]:
        p6_failures.append(
            f"histogram sum {measurement['histogram_sum_wins']}"
            f" != eligible won edges {measurement['eligible_won_edges']}"
        )
    if measurement["siafi_coverage"] < float(calibration["siafi_coverage_min"]):
        p6_failures.append(
            f"siafi coverage {measurement['siafi_coverage']}"
            f" < {calibration['siafi_coverage_min']}"
        )

    p7_failures: list[str] = []
    if measurement["elapsed_seconds"] >= float(calibration["time_budget_seconds"]):
        p7_failures.append(
            f"sweep took {measurement['elapsed_seconds']}s"
            f" >= {calibration['time_budget_seconds']}s"
        )
    if materialized is not None:
        if materialized > int(calibration["budget"]["backlog_max_pairs"]):
            p7_failures.append(
                f"materialized {materialized} pairs above the backlog budget"
            )
        if calibrated is not None and materialized != counts_python[str(calibrated)]:
            p7_failures.append(
                f"materialized {materialized} != counted"
                f" {counts_python[str(calibrated)]} at w={calibrated}"
            )

    refuted = bool(p5_failures or p7_failures)
    predictions = {
        "P5": {
            "verdict": "refuted" if p5_failures else "success",
            "failures": p5_failures,
        },
        "P6": {
            "verdict": "inconclusive" if p6_failures else "success",
            "failures": p6_failures,
        },
        "P7": {
            "verdict": "refuted" if p7_failures else "success",
            "failures": p7_failures,
        },
        "P8": (
            {"verdict": "success", "calibrated_min_wins": calibrated}
            if calibrated is not None
            else {"verdict": "inconclusive", "calibrated_min_wins": None}
        ),
    }
    if refuted:
        verdict = "refuted"
    elif p6_failures or calibrated is None:
        verdict = "inconclusive"
    else:
        verdict = "success"
    return {
        "battery": config["id"],
        "part": "real",
        "measurement": measurement,
        "predictions": predictions,
        "verdict": verdict,
    }


def evaluate_synthetic_refined(
    config: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Evaluates the pre-registered predictions Q1-Q5 (PR-D-03b).

    Pure (no ArangoDB): consumes the per-seed records produced by
    ``run_seed_refined``. Control-point keys derive from the calibration
    block; the exact expectations live in the same config file.
    """
    exp = config["expectations"]
    calibration = config["calibration"]
    min_w = min(int(w) for w in calibration["candidates_min_wins"])
    min_n = min(int(n) for n in calibration["candidates_min_buyers"])
    control_w = int(calibration.get("control_min_wins", 2))
    control_n = int(calibration.get("control_min_buyers", 2))
    k1 = f"{min_w}:1"
    k2 = f"{min_w}:{min_n}"
    k3 = f"{min_w + 1}:{min_n}"
    kc = f"{control_w}:{control_n}"
    failures: dict[str, list[str]] = {f"Q{i}": [] for i in range(1, 6)}
    monotonicity_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        points = record["control_points"]

        # Q1 — degenerate control: (min_w, 1) reproduces the D-03 semantics
        if points[k1]["count"] != exp["pairs_min3_buyers1_exact_count"]:
            failures["Q1"].append(
                f"seed {seed}: pairs {k1} {points[k1]['count']}"
                f" != {exp['pairs_min3_buyers1_exact_count']}"
            )

        # Q2 — exact pair set and buyer annotation at the target point
        expected_pairs = [list(p) for p in exp["pairs_min3_buyers2_exact"]]
        if points[k2]["pairs"] != expected_pairs:
            failures["Q2"].append(
                f"seed {seed}: pairs {k2} {points[k2]['pairs']} != {expected_pairs}"
            )
        if points[k2]["buyers"] != exp["pair_buyers_min3_buyers2_exact"]:
            failures["Q2"].append(
                f"seed {seed}: buyers {k2} {points[k2]['buyers']}"
                f" != {exp['pair_buyers_min3_buyers2_exact']}"
            )

        # Q3 — boundary: empty at (min_w + 1, min_n); exact control pairs
        if points[k3]["count"] != exp["pairs_min4_buyers2_exact_count"]:
            failures["Q3"].append(
                f"seed {seed}: pairs {k3} {points[k3]['count']}"
                f" != {exp['pairs_min4_buyers2_exact_count']}"
            )
        expected_control = [list(p) for p in exp["pairs_min2_buyers2_control_exact"]]
        if points[kc]["pairs"] != expected_control:
            failures["Q3"].append(
                f"seed {seed}: control pairs {kc} {points[kc]['pairs']}"
                f" != {expected_control}"
            )

        # Q4 — exact window increment at the target point
        if record["recent_pairs_target"] != exp["recent_pairs_min3_buyers2_exact"]:
            failures["Q4"].append(
                f"seed {seed}: recent pairs {record['recent_pairs_target']}"
                f" != {exp['recent_pairs_min3_buyers2_exact']}"
            )

        # Q5 — exact evidence reproduction; tampering breaks it
        evidence = record["evidence"]
        if not evidence["matches"] or not all(evidence["matches"]):
            failures["Q5"].append(
                f"seed {seed}: reproduction matches {evidence['matches']}"
            )
        tampered = evidence["tampered"]
        if tampered is None or tampered["integrity"] or tampered["match"]:
            failures["Q5"].append(f"seed {seed}: tampered outcome {tampered}")

        # Monotonicity invariant over the measured control points:
        # non-increasing in min_wins (fixed min_buyers) and in min_buyers
        # (fixed min_wins).
        if points[k3]["count"] > points[k2]["count"]:
            monotonicity_failures.append(
                f"seed {seed}: pairs({k3})={points[k3]['count']}"
                f" > pairs({k2})={points[k2]['count']}"
            )
        if points[k2]["count"] > points[k1]["count"]:
            monotonicity_failures.append(
                f"seed {seed}: pairs({k2})={points[k2]['count']}"
                f" > pairs({k1})={points[k1]['count']}"
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
        "part": "synthetic",
        "predictions": predictions,
        "invariants": invariants,
        "verdict": verdict,
    }


def evaluate_real_refined(
    config: dict[str, Any], measurement: dict[str, Any]
) -> dict[str, Any]:
    """Evaluates the pre-registered real-sweep predictions Q6-Q9.

    Pure: consumes the measurement produced by ``measure_refined`` over
    the production graph. Q7/Q9 yield ``inconclusive`` (not ``refuted``)
    when the regime is degraded or no grid point fits the triage budget.
    """
    calibration = config["calibration"]
    counts_aql = measurement["pairs_grid_aql"]
    counts_python = measurement["pairs_grid_python"]
    materialized = measurement["materialized_pairs"]
    calibrated = measurement["calibrated"]

    q6_failures: list[str] = []
    if counts_aql != counts_python:
        q6_failures.append(
            f"AQL counts {counts_aql} diverge from Python {counts_python}"
        )

    q7_failures: list[str] = []
    if measurement["histogram_sum_wins"] != measurement["eligible_won_edges"]:
        q7_failures.append(
            f"histogram sum {measurement['histogram_sum_wins']}"
            f" != eligible won edges {measurement['eligible_won_edges']}"
        )
    if measurement["siafi_coverage"] < float(calibration["siafi_coverage_min"]):
        q7_failures.append(
            f"siafi coverage {measurement['siafi_coverage']}"
            f" < {calibration['siafi_coverage_min']}"
        )

    q8_failures: list[str] = []
    if measurement["elapsed_seconds"] >= float(calibration["time_budget_seconds"]):
        q8_failures.append(
            f"sweep took {measurement['elapsed_seconds']}s"
            f" >= {calibration['time_budget_seconds']}s"
        )
    if materialized is not None:
        if materialized > int(calibration["budget"]["backlog_max_pairs"]):
            q8_failures.append(
                f"materialized {materialized} pairs above the backlog budget"
            )
        if calibrated is not None:
            key = f"{calibrated['min_wins']}:{calibrated['min_buyers']}"
            if materialized != counts_python[key]:
                q8_failures.append(
                    f"materialized {materialized} != counted"
                    f" {counts_python[key]} at {key}"
                )

    refuted = bool(q6_failures or q8_failures)
    predictions = {
        "Q6": {
            "verdict": "refuted" if q6_failures else "success",
            "failures": q6_failures,
        },
        "Q7": {
            "verdict": "inconclusive" if q7_failures else "success",
            "failures": q7_failures,
        },
        "Q8": {
            "verdict": "refuted" if q8_failures else "success",
            "failures": q8_failures,
        },
        "Q9": (
            {"verdict": "success", "calibrated": calibrated}
            if calibrated is not None
            else {"verdict": "inconclusive", "calibrated": None}
        ),
    }
    if refuted:
        verdict = "refuted"
    elif q7_failures or calibrated is None:
        verdict = "inconclusive"
    else:
        verdict = "success"
    return {
        "battery": config["id"],
        "part": "real",
        "measurement": measurement,
        "predictions": predictions,
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Blocked mode (PR-D-03c): exact-recall blocking of the pair derivation
# ---------------------------------------------------------------------------


def is_blocked(config: dict[str, Any]) -> bool:
    """True when the config declares the PR-D-03c blocking block."""
    return "blocking" in config["calibration"]


def _equivalence_points(config: dict[str, Any]) -> list[tuple[int, int]]:
    """Pre-registered (min_wins, min_buyers) control points of Part A."""
    return [
        (int(w), int(n))
        for w, n in (
            point.split(":")
            for point in config["calibration"]["blocking"]["equivalence_points"]
        )
    ]


def _timed_derivation(
    derivation: Callable[
        [list[dict[str, Any]], int], list[tuple[tuple[str, str], list[str]]]
    ],
    rows: list[dict[str, Any]],
    min_buyers: int,
    traced: bool,
) -> tuple[list[tuple[tuple[str, str], list[str]]], float, int]:
    """Runs one derivation path timed; heap-traced (tracemalloc) when asked.

    Returns:
        (pairs, wall-clock seconds, peak traced heap in bytes — 0 when not
        traced).
    """
    if traced:
        tracemalloc.start()
    started = time.perf_counter()
    pairs = derivation(rows, min_buyers)
    elapsed = time.perf_counter() - started
    peak = 0
    if traced:
        _current, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return pairs, elapsed, peak


def ranked_pair_emission(
    pair_buyers: list[tuple[tuple[str, str], list[str]]],
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Deterministic emission ordering (PR-D-03c, section 5).

    Ranking by ``buyer_count`` descending, ``wins_sum`` descending (sum of
    the wins of both suppliers over the pair's co-eligible buyers), pair
    ascending as the lexicographic tiebreak. Ordering descriptor for
    triage priority only — the emitted set is unchanged (no truncation).
    Thin wrapper over ``graphs.ranked_emission`` (PR-D-03d) without
    truncation.
    """
    emission: list[dict[str, Any]] = ranked_emission(pair_buyers, rows)["emission"]
    return emission


def _signal_score(signals: list[dict[str, Any]], key: str) -> float | None:
    """Score of the signal with the given triage key (None if absent)."""
    for signal in signals:
        if (
            signal_key(
                str(signal["entity_type"]),
                str(signal["entity_id"]),
                str(signal["signal_type"]),
            )
            == key
        ):
            return float(signal["score"])
    return None


def _reproduce_graph_signal_blocked(
    package: dict[str, Any], key: str
) -> dict[str, Any]:
    """Reproduces a graph_batch package via the blocked derivation.

    Retrocompatibility check (PR-D-03c, R4): packages emitted by the
    unblocked derivation reproduce with the same result through the
    blocked path. Integrity and the expected score come from the
    production reproduction (``reproduce_signal``, unblocked path).
    """
    outcome = evidence_packages.reproduce_signal(package, key)
    rows = package.get("snapshot_rows", [])
    reproduction = package.get("reproduction", {})
    min_wins = int(reproduction.get("min_wins", 3))
    min_buyers = int(reproduction.get("min_buyers", 1))
    eligible = [row for row in rows if int(row.get("wins", 0)) >= min_wins]
    recomputed = collusion_signals(
        [
            set(pair)
            for pair, _ in pair_buyers_from_eligibility_blocked(eligible, min_buyers)
        ],
        min_wins,
        min_buyers,
    )
    actual = _signal_score(recomputed, key)
    expected = outcome["expected"]
    return {
        "signal_key": key,
        "expected": expected,
        "actual": actual,
        "integrity": outcome["integrity"],
        "match": bool(
            outcome["integrity"] and expected is not None and actual == expected
        ),
    }


def _evidence_check_blocked(
    db: StandardDatabase, min_wins: int, min_buyers: int
) -> dict[str, Any]:
    """Part C (blocked): evidence package emitted via the blocked derivation.

    Every emitted signal must reproduce with ``match = true`` through both
    the production (unblocked) and the blocked reproduction paths;
    removing one snapshot row must break integrity and the match (R4).
    """
    rows = collusion_eligibility(db, min_wins=min_wins)
    pair_buyers = pair_buyers_from_eligibility_blocked(rows, min_buyers)
    signals = collusion_signals(
        [set(pair) for pair, _ in pair_buyers],
        min_wins,
        min_buyers,
        dict(pair_buyers),
    )
    package = evidence_packages.build_graph_batch_package(
        rows, signals, min_wins, None, min_buyers
    )

    keys = [
        signal_key(
            str(signal["entity_type"]),
            str(signal["entity_id"]),
            str(signal["signal_type"]),
        )
        for signal in package["signals"]
    ]
    matches = [
        evidence_packages.reproduce_signal(package, key)["match"] for key in keys
    ]
    blocked_matches = [
        _reproduce_graph_signal_blocked(package, key)["match"] for key in keys
    ]

    tampered = json.loads(json.dumps(package))
    tampered["snapshot_rows"] = tampered["snapshot_rows"][1:]
    tampered_outcome = (
        evidence_packages.reproduce_signal(tampered, keys[0]) if keys else None
    )
    return {
        "signals": len(signals),
        "matches": matches,
        "blocked_matches": blocked_matches,
        "tampered": tampered_outcome,
    }


def _stress_check(
    db: StandardDatabase,
    config: dict[str, Any],
    seed: int,
    reference_date: date,
    min_wins: int,
    min_buyers: int,
) -> dict[str, Any]:
    """Part A-stress: plants the big buyer and compares both derivations.

    Anchors (R5): the unblocked projection grows by exactly C(N, 2); the
    blocked projection is untouched (the big buyer's suppliers are
    exclusive); both derivation paths are timed and heap-traced in the
    same run.
    """
    started = time.monotonic()
    plant(db, generate_stress(config, seed, reference_date))
    rows = collusion_eligibility(db, min_wins=min_wins)
    unblocked, time_unblocked, peak_unblocked = _timed_derivation(
        pair_buyers_from_eligibility, rows, min_buyers, traced=True
    )
    blocked, time_blocked, peak_blocked = _timed_derivation(
        pair_buyers_from_eligibility_blocked, rows, min_buyers, traced=True
    )
    return {
        "projection_unblocked": projected_pair_count(rows),
        "projection_blocked": blocked_projection(rows, min_buyers),
        "unblocked_pairs": [list(pair) for pair, _ in unblocked],
        "blocked_pairs": [list(pair) for pair, _ in blocked],
        "time_unblocked_seconds": round(time_unblocked, 4),
        "time_blocked_seconds": round(time_blocked, 4),
        "peak_unblocked_bytes": peak_unblocked,
        "peak_blocked_bytes": peak_blocked,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def run_seed_blocked(
    db: StandardDatabase, config: dict[str, Any], seed: int
) -> dict[str, Any]:
    """Parts A/A-stress/C for one seed (PR-D-03c).

    Plants the base population (identical to D-03b — the degenerate
    control stays comparable), measures the pre-registered equivalence
    points with both derivation paths plus the arithmetic projections,
    checks the evidence reproduction (Part C, target point) and then
    plants the stress buyer for the scale anchors (R5).

    Returns:
        Per-seed record with control points, evidence and stress outcomes.
    """
    _clear_collections(db)
    reference_date = date.today()
    plant(db, generate(config, seed, reference_date))

    calibration = config["calibration"]
    min_w = min(int(w) for w in calibration["candidates_min_wins"])
    min_n = min(int(n) for n in calibration["candidates_min_buyers"])

    control_points: dict[str, Any] = {}
    for w, n in _equivalence_points(config):
        rows = collusion_eligibility(db, min_wins=w)
        unblocked = pair_buyers_from_eligibility(rows, n)
        blocked = pair_buyers_from_eligibility_blocked(rows, n)
        control_points[f"{w}:{n}"] = {
            "unblocked_pairs": [list(pair) for pair, _ in unblocked],
            "blocked_pairs": [list(pair) for pair, _ in blocked],
            "unblocked_buyers": {"+".join(pair): buyers for pair, buyers in unblocked},
            "blocked_buyers": {"+".join(pair): buyers for pair, buyers in blocked},
            "equivalent": unblocked == blocked,
            "projection_unblocked": projected_pair_count(rows),
            "projection_blocked": blocked_projection(rows, n),
            "incidences_blocked": sum(
                len(buyers) for buyers in _blocked_buyers_by_pair(rows, n).values()
            ),
        }

    return {
        "seed": seed,
        "control_points": control_points,
        "evidence": _evidence_check_blocked(db, min_w, min_n),
        "stress": _stress_check(db, config, seed, reference_date, min_w, min_n),
    }


def evaluate_synthetic_blocked(
    config: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Evaluates the pre-registered predictions R1-R5 (PR-D-03c).

    Pure (no ArangoDB): consumes the per-seed records produced by
    ``run_seed_blocked``. Control-point keys derive from the calibration
    block; the exact anchors live in the same config file.
    """
    exp = config["expectations"]
    calibration = config["calibration"]
    min_w = min(int(w) for w in calibration["candidates_min_wins"])
    min_n = min(int(n) for n in calibration["candidates_min_buyers"])
    control_w = int(calibration.get("control_min_wins", 2))
    control_n = int(calibration.get("control_min_buyers", 2))
    stress_budget = float(calibration["stress"]["time_budget_seconds"])
    k1 = f"{min_w}:1"
    k2 = f"{min_w}:{min_n}"
    k3 = f"{min_w + 1}:{min_n}"
    kc = f"{control_w}:{control_n}"
    failures: dict[str, list[str]] = {f"R{i}": [] for i in range(1, 6)}
    monotonicity_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        points = record["control_points"]

        # R1 — bit-a-bit equivalence and exact anchors per control point
        for key, point in points.items():
            if not point["equivalent"]:
                failures["R1"].append(f"seed {seed}: derivations diverge at {key}")
        if len(points[k1]["blocked_pairs"]) != exp["pairs_min3_buyers1_exact_count"]:
            failures["R1"].append(
                f"seed {seed}: pairs {k1} {len(points[k1]['blocked_pairs'])}"
                f" != {exp['pairs_min3_buyers1_exact_count']}"
            )
        if points[k2]["blocked_pairs"] != [
            list(p) for p in exp["pairs_min3_buyers2_exact"]
        ]:
            failures["R1"].append(
                f"seed {seed}: pairs {k2} {points[k2]['blocked_pairs']}"
                f" != {exp['pairs_min3_buyers2_exact']}"
            )
        if len(points[k3]["blocked_pairs"]) != exp["pairs_min4_buyers2_exact_count"]:
            failures["R1"].append(
                f"seed {seed}: pairs {k3} {len(points[k3]['blocked_pairs'])}"
                f" != {exp['pairs_min4_buyers2_exact_count']}"
            )
        if points[kc]["blocked_pairs"] != [
            list(p) for p in exp["pairs_min2_buyers2_control_exact"]
        ]:
            failures["R1"].append(
                f"seed {seed}: control pairs {kc} {points[kc]['blocked_pairs']}"
                f" != {exp['pairs_min2_buyers2_control_exact']}"
            )

        # R2 — exact arithmetic projections, before/after the blocking
        for key, point in points.items():
            w = key.split(":")[0]
            if point["projection_unblocked"] != exp["unblocked_projection_exact"][w]:
                failures["R2"].append(
                    f"seed {seed}: unblocked projection at {key}"
                    f" {point['projection_unblocked']}"
                    f" != {exp['unblocked_projection_exact'][w]}"
                )
            if point["projection_blocked"] != exp["blocked_projection_exact"][key]:
                failures["R2"].append(
                    f"seed {seed}: blocked projection at {key}"
                    f" {point['projection_blocked']}"
                    f" != {exp['blocked_projection_exact'][key]}"
                )

        # R3 — double counting: projection == materialized incidences
        for key, point in points.items():
            if point["projection_blocked"] != point["incidences_blocked"]:
                failures["R3"].append(
                    f"seed {seed}: projection {point['projection_blocked']}"
                    f" != incidences {point['incidences_blocked']} at {key}"
                )

        # R4 — exact evidence reproduction via both paths; tampering breaks
        evidence = record["evidence"]
        if not evidence["matches"] or not all(evidence["matches"]):
            failures["R4"].append(
                f"seed {seed}: reproduction matches {evidence['matches']}"
            )
        if not evidence["blocked_matches"] or not all(evidence["blocked_matches"]):
            failures["R4"].append(
                f"seed {seed}: blocked reproduction {evidence['blocked_matches']}"
            )
        tampered = evidence["tampered"]
        if tampered is None or tampered["integrity"] or tampered["match"]:
            failures["R4"].append(f"seed {seed}: tampered outcome {tampered}")

        # R5 — scale anchors of the stress population
        stress = record["stress"]
        if (
            stress["projection_unblocked"]
            != exp["stress_unblocked_projection_min3_exact"]
        ):
            failures["R5"].append(
                f"seed {seed}: stress unblocked projection"
                f" {stress['projection_unblocked']}"
                f" != {exp['stress_unblocked_projection_min3_exact']}"
            )
        if (
            stress["projection_blocked"]
            != exp["stress_blocked_projection_min3_buyers2_exact"]
        ):
            failures["R5"].append(
                f"seed {seed}: stress blocked projection"
                f" {stress['projection_blocked']}"
                f" != {exp['stress_blocked_projection_min3_buyers2_exact']}"
            )
        if stress["blocked_pairs"] != [
            list(p) for p in exp["stress_pairs_min3_buyers2_exact"]
        ]:
            failures["R5"].append(
                f"seed {seed}: stress pairs {stress['blocked_pairs']}"
                f" != {exp['stress_pairs_min3_buyers2_exact']}"
            )
        if stress["time_blocked_seconds"] > stress["time_unblocked_seconds"]:
            failures["R5"].append(
                f"seed {seed}: blocked derivation slower"
                f" ({stress['time_blocked_seconds']}s"
                f" > {stress['time_unblocked_seconds']}s)"
            )
        if stress["elapsed_seconds"] >= stress_budget:
            failures["R5"].append(
                f"seed {seed}: stress sweep {stress['elapsed_seconds']}s"
                f" >= {stress_budget}s"
            )

        # Monotonicity invariant: blocked <= unblocked per point, and
        # non-increasing in min_wins (fixed min_buyers) and in min_buyers
        # (fixed min_wins).
        for key, point in points.items():
            if point["projection_blocked"] > point["projection_unblocked"]:
                monotonicity_failures.append(
                    f"seed {seed}: blocked {point['projection_blocked']}"
                    f" > unblocked {point['projection_unblocked']} at {key}"
                )
        if points[k3]["projection_blocked"] > points[k2]["projection_blocked"]:
            monotonicity_failures.append(
                f"seed {seed}: blocked({k3})={points[k3]['projection_blocked']}"
                f" > blocked({k2})={points[k2]['projection_blocked']}"
            )
        if points[k2]["projection_blocked"] > points[k1]["projection_blocked"]:
            monotonicity_failures.append(
                f"seed {seed}: blocked({k2})={points[k2]['projection_blocked']}"
                f" > blocked({k1})={points[k1]['projection_blocked']}"
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
        "part": "synthetic",
        "predictions": predictions,
        "invariants": invariants,
        "verdict": verdict,
    }


def measure_blocked(
    db: StandardDatabase, config: dict[str, Any], reference_date: date
) -> dict[str, Any]:
    """Part B: real sweep with dual derivation (PR-D-03c, read-only).

    Per grid point ``{min_wins} × {min_buyers}``: arithmetic projections
    before/after the blocking, both derivation paths materialized
    (required by the equivalence check R6) and timed, the blocked
    incidence double counting (R7), and — at the first grid point — peak
    heap per path (tracemalloc, R8) and the ordered emission executed
    twice (R9). The graph must be frozen: a changed ``won`` count between
    the boundaries marks the run as discarded (not a refutation).

    Args:
        db: ArangoDB connection (production graph).
        config: Battery configuration (blocking calibration block).
        reference_date: Sweep date (anchor of the increment windows).

    Returns:
        Measurement dict (points, emission, coverage, stability, elapsed).
    """
    calibration = config["calibration"]
    candidates_w = sorted(int(w) for w in calibration["candidates_min_wins"])
    candidates_n = sorted(int(n) for n in calibration["candidates_min_buyers"])
    blocking = calibration["blocking"]
    window = int(calibration["increment_window_days"])
    robust_window = int(calibration["robustness_window_days"])
    cutoff = (reference_date - timedelta(days=window)).isoformat()
    cutoff_robust = (reference_date - timedelta(days=robust_window)).isoformat()
    traced_key = f"{candidates_w[0]}:{candidates_n[0]}"

    started = time.monotonic()
    won_before = cast("int", db.collection("won").count())

    export_rows = execute_aql(
        db, _EXPORT_QUERY, {"cutoff": cutoff, "cutoffRobust": cutoff_robust}
    )
    rows_by_w = {w: collusion_eligibility(db, min_wins=w) for w in candidates_w}

    points: dict[str, Any] = {}
    for w, n in grid_order(candidates_w, candidates_n):
        key = f"{w}:{n}"
        rows = rows_by_w[w]
        traced = key == traced_key
        unblocked, time_unblocked, peak_unblocked = _timed_derivation(
            pair_buyers_from_eligibility, rows, n, traced
        )
        blocked, time_blocked, peak_blocked = _timed_derivation(
            pair_buyers_from_eligibility_blocked, rows, n, traced
        )
        points[key] = {
            "projection_unblocked": projected_pair_count(rows),
            "projection_blocked": blocked_projection(rows, n),
            "incidences_blocked": sum(
                len(buyers) for buyers in _blocked_buyers_by_pair(rows, n).values()
            ),
            "pair_count": len(blocked),
            "equivalent": unblocked == blocked,
            "time_unblocked_seconds": round(time_unblocked, 4),
            "time_blocked_seconds": round(time_blocked, 4),
            "peak_unblocked_bytes": peak_unblocked,
            "peak_blocked_bytes": peak_blocked,
        }

    # R9 — ordered emission executed twice in the same freeze window
    ranking = calibration["ranking"]
    top_k = int(ranking["top_k_descriptor"])
    traced_rows = rows_by_w[candidates_w[0]]
    emission_blobs = []
    emission_size = 0
    for _ in range(int(ranking["determinism_reruns"])):
        pair_buyers = pair_buyers_from_eligibility_blocked(traced_rows, candidates_n[0])
        emission = ranked_pair_emission(pair_buyers, traced_rows)
        emission_size = len(emission)
        emission_blobs.append(json.dumps(emission, sort_keys=True))

    coverage_rows = execute_aql(db, _COVERAGE_QUERY, {})
    eligible_won = cast("int", coverage_rows[0]) if coverage_rows else 0
    won_after = cast("int", db.collection("won").count())
    coverage = eligible_won / won_after if won_after else 1.0

    return {
        "reference_date": reference_date.isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "candidates_min_wins": candidates_w,
        "candidates_min_buyers": candidates_n,
        "grid_order": [f"{w}:{n}" for w, n in grid_order(candidates_w, candidates_n)],
        "traced_point": traced_key,
        "exported_rows": len(export_rows),
        "histogram": {str(k): v for k, v in histogram_from_rows(export_rows).items()},
        "histogram_sum_wins": sum(int(row["wins"]) for row in export_rows),
        "total_won_edges": won_after,
        "eligible_won_edges": eligible_won,
        "siafi_coverage": round(coverage, 6),
        "graph_stable": won_before == won_after,
        "points": points,
        "emission": {
            "deterministic": len(set(emission_blobs)) == 1,
            "top_k": top_k,
            "top_k_is_prefix": len(set(emission_blobs)) == 1,
            "size": emission_size,
        },
        "memory_budget_bytes": int(float(blocking["memory_budget_mb"]) * 1024 * 1024),
        "max_pairs_guard": int(blocking["max_pairs_guard"]),
        "time_budget_seconds": float(calibration["time_budget_seconds"]),
    }


def evaluate_real_blocked(
    config: dict[str, Any], measurement: dict[str, Any]
) -> dict[str, Any]:
    """Evaluates the pre-registered real-sweep predictions R6-R9.

    Pure: consumes the measurement produced by ``measure_blocked`` over
    the production graph. Degraded regime (low coverage, graph changed
    during the measurement) yields ``inconclusive``, not ``refuted`` —
    the run is discarded and reexecuted (PR-D-03c, sections 2 and 7).
    """
    points = measurement["points"]

    r6_failures = [
        f"derivations diverge at {key}"
        for key, point in points.items()
        if not point["equivalent"]
    ]

    r7_failures = [
        f"projection {point['projection_blocked']} != incidences"
        f" {point['incidences_blocked']} at {key}"
        for key, point in points.items()
        if point["projection_blocked"] != point["incidences_blocked"]
    ]

    r8_failures: list[str] = []
    if measurement["elapsed_seconds"] >= measurement["time_budget_seconds"]:
        r8_failures.append(
            f"sweep took {measurement['elapsed_seconds']}s"
            f" >= {measurement['time_budget_seconds']}s"
        )
    traced = points[measurement["traced_point"]]
    if traced["peak_blocked_bytes"] >= measurement["memory_budget_bytes"]:
        r8_failures.append(
            f"blocked peak heap {traced['peak_blocked_bytes']}"
            f" >= {measurement['memory_budget_bytes']} bytes"
        )
    if traced["peak_blocked_bytes"] > traced["peak_unblocked_bytes"]:
        r8_failures.append(
            f"blocked peak heap {traced['peak_blocked_bytes']}"
            f" > unblocked {traced['peak_unblocked_bytes']} bytes"
        )
    for key, point in points.items():
        if point["projection_blocked"] >= measurement["max_pairs_guard"]:
            r8_failures.append(
                f"blocked projection {point['projection_blocked']}"
                f" >= guard {measurement['max_pairs_guard']} at {key}"
            )

    r9_failures: list[str] = []
    if not measurement["emission"]["deterministic"]:
        r9_failures.append("ordered emission not byte-identical across reruns")
    if not measurement["emission"]["top_k_is_prefix"]:
        r9_failures.append("top-k descriptor is not an exact prefix")

    regime_failures: list[str] = []
    if not measurement["graph_stable"]:
        regime_failures.append(
            "won edge count changed during the measurement — run discarded"
        )
    if measurement["histogram_sum_wins"] != measurement["eligible_won_edges"]:
        regime_failures.append(
            f"histogram sum {measurement['histogram_sum_wins']}"
            f" != eligible won edges {measurement['eligible_won_edges']}"
        )
    coverage_min = float(config["calibration"]["siafi_coverage_min"])
    if measurement["siafi_coverage"] < coverage_min:
        regime_failures.append(
            f"siafi coverage {measurement['siafi_coverage']} < {coverage_min}"
        )

    refuted = bool(r6_failures or r7_failures or r8_failures or r9_failures)
    predictions = {
        "R6": {
            "verdict": "refuted" if r6_failures else "success",
            "failures": r6_failures,
        },
        "R7": {
            "verdict": "refuted" if r7_failures else "success",
            "failures": r7_failures,
        },
        "R8": {
            "verdict": "refuted" if r8_failures else "success",
            "failures": r8_failures,
        },
        "R9": {
            "verdict": "refuted" if r9_failures else "success",
            "failures": r9_failures,
        },
    }
    if refuted:
        verdict = "refuted"
    elif regime_failures:
        verdict = "inconclusive"
    else:
        verdict = "success"
    return {
        "battery": config["id"],
        "part": "real",
        "measurement": measurement,
        "predictions": predictions,
        "regime": {
            "verdict": "degraded" if regime_failures else "ok",
            "failures": regime_failures,
        },
        "verdict": verdict,
    }


# ---------------------------------------------------------------------------
# Emission mode (PR-D-03d): declared top-K ranked emission over the blocked
# derivation
# ---------------------------------------------------------------------------


def is_emission(config: dict[str, Any]) -> bool:
    """True when the config declares the PR-D-03d emission block."""
    return "emission" in config["calibration"]


def _emission_points(config: dict[str, Any]) -> list[tuple[int, int]]:
    """Pre-registered control points of Part A: {(min_w,1), (min_w,min_n),
    (min_w+1,min_n), (control_w,control_n)}."""
    calibration = config["calibration"]
    min_w = min(int(w) for w in calibration["candidates_min_wins"])
    min_n = min(int(n) for n in calibration["candidates_min_buyers"])
    control_w = int(calibration.get("control_min_wins", 2))
    control_n = int(calibration.get("control_min_buyers", 2))
    return [(min_w, 1), (min_w, min_n), (min_w + 1, min_n), (control_w, control_n)]


def _emitted_pair_set(
    rows: list[dict[str, Any]], min_wins: int, min_buyers: int, top_k: int
) -> set[tuple[str, str]]:
    """Emitted top-K pair set from raw export rows (increment delta, PR §4).

    ``rows`` carries the raw ``wins`` counts (already window-adjusted by
    the caller when measuring the "old" snapshot); the eligibility filter
    is applied here.
    """
    eligible = [row for row in rows if int(row["wins"]) >= min_wins]
    pair_buyers = pair_buyers_from_eligibility_blocked(eligible, min_buyers)
    return {
        tuple(entry["pair"])
        for entry in ranked_emission(pair_buyers, eligible, top_k)["emission"]
    }


def _evidence_check_emission(
    db: StandardDatabase, min_wins: int, min_buyers: int, top_k: int
) -> dict[str, Any]:
    """Part C (emission): graph_batch package with declared top_k (PR-D-03d).

    Every emitted signal must reproduce with ``match = true`` (the
    reproduction re-derives, re-ranks and re-truncates deterministically);
    removing one snapshot row must break integrity and the match; legacy
    packages (``top_k = null``) reproduce unchanged (T3).
    """
    rows = collusion_eligibility(db, min_wins=min_wins)
    pair_buyers = pair_buyers_from_eligibility_blocked(rows, min_buyers)
    emission = ranked_emission(pair_buyers, rows, top_k)
    emitted = emission["emission"]
    signals = collusion_signals(
        [set(entry["pair"]) for entry in emitted],
        min_wins,
        min_buyers,
        {tuple(entry["pair"]): entry["buyers"] for entry in emitted},
    )
    package = evidence_packages.build_graph_batch_package(
        rows,
        signals,
        min_wins,
        None,
        min_buyers,
        top_k=top_k,
        qualified_count=emission["qualified_count"],
    )

    keys = [
        signal_key(
            str(signal["entity_type"]),
            str(signal["entity_id"]),
            str(signal["signal_type"]),
        )
        for signal in package["signals"]
    ]
    matches = [
        evidence_packages.reproduce_signal(package, key)["match"] for key in keys
    ]

    tampered = json.loads(json.dumps(package))
    tampered["snapshot_rows"] = tampered["snapshot_rows"][1:]
    tampered_outcome = (
        evidence_packages.reproduce_signal(tampered, keys[0]) if keys else None
    )

    # Legacy retrocompatibility: a package without top_k over the full
    # signal set reproduces unchanged (T3).
    legacy_signals = collusion_signals(
        [set(pair) for pair, _ in pair_buyers],
        min_wins,
        min_buyers,
        dict(pair_buyers),
    )
    legacy_package = evidence_packages.build_graph_batch_package(
        rows, legacy_signals, min_wins, None, min_buyers
    )
    legacy_matches = [
        evidence_packages.reproduce_signal(
            legacy_package,
            signal_key(
                str(signal["entity_type"]),
                str(signal["entity_id"]),
                str(signal["signal_type"]),
            ),
        )["match"]
        for signal in legacy_package["signals"]
    ]
    return {
        "signals": len(signals),
        "matches": matches,
        "tampered": tampered_outcome,
        "legacy_matches": legacy_matches,
    }


def _stress_check_emission(
    db: StandardDatabase,
    config: dict[str, Any],
    seed: int,
    reference_date: date,
    min_wins: int,
    min_buyers: int,
    top_k: int,
) -> dict[str, Any]:
    """Part A-stress (emission): scale anchors of the top-K emission (T4).

    With ``BIG-B`` planted: blocked projection at (min_w, min_n); emission
    at (min_w, min_n) with K (vacuous truncation under blocking); emission
    at (min_w, 1) with K (active truncation over the big buyer's pairs).
    """
    started = time.monotonic()
    plant(db, generate_stress(config, seed, reference_date))
    rows = collusion_eligibility(db, min_wins=min_wins)
    emission_n = ranked_emission(
        pair_buyers_from_eligibility_blocked(rows, min_buyers), rows, top_k
    )
    pair_buyers_1 = pair_buyers_from_eligibility_blocked(rows, 1)
    emission_1 = ranked_emission(pair_buyers_1, rows, top_k)
    full_1 = ranked_emission(pair_buyers_1, rows)
    return {
        "projection_blocked": blocked_projection(rows, min_buyers),
        "emission_min_buyers": [entry["pair"] for entry in emission_n["emission"]],
        "emission_min_buyers_1_count": len(emission_1["emission"]),
        "emission_min_buyers_1_first": (
            emission_1["emission"][0]["pair"] if emission_1["emission"] else None
        ),
        "qualified_min_buyers_1": emission_1["qualified_count"],
        "prefix_min_buyers_1": emission_1["emission"] == full_1["emission"][:top_k],
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def run_seed_emission(
    db: StandardDatabase, config: dict[str, Any], seed: int
) -> dict[str, Any]:
    """Parts A/A-stress/C for one seed (PR-D-03d).

    Plants the base population (identical to D-03b/D-03c), measures the
    pre-registered control points with the blocked derivation — vacuous
    (K = ``top_k``) and active (K = ``synthetic_truncation_top_k``)
    truncations —, checks the evidence reproduction with declared top_k
    (Part C, target point) and then plants the stress buyer for the scale
    anchors (T4).

    Returns:
        Per-seed record with control points, evidence and stress outcomes.
    """
    _clear_collections(db)
    reference_date = date.today()
    plant(db, generate(config, seed, reference_date))

    calibration = config["calibration"]
    emission_config = calibration["emission"]
    top_k = int(emission_config["top_k"])
    trunc_k = int(emission_config["synthetic_truncation_top_k"])
    min_w = min(int(w) for w in calibration["candidates_min_wins"])
    min_n = min(int(n) for n in calibration["candidates_min_buyers"])
    truncated_points = {(min_w, 1), _emission_points(config)[3]}

    control_points: dict[str, Any] = {}
    for w, n in _emission_points(config):
        rows = collusion_eligibility(db, min_wins=w)
        pair_buyers = pair_buyers_from_eligibility_blocked(rows, n)
        full = ranked_emission(pair_buyers, rows, top_k)
        point: dict[str, Any] = {
            "top_k": full["top_k"],
            "qualified_count": full["qualified_count"],
            "coverage": full["coverage"],
            "emission": [entry["pair"] for entry in full["emission"]],
            "equivalent": pair_buyers == pair_buyers_from_eligibility(rows, n),
            "projection_blocked": blocked_projection(rows, n),
            "incidences_blocked": sum(
                len(buyers) for buyers in _blocked_buyers_by_pair(rows, n).values()
            ),
        }
        if (w, n) in truncated_points:
            truncated = ranked_emission(pair_buyers, rows, trunc_k)
            point["truncated"] = {
                "top_k": truncated["top_k"],
                "qualified_count": truncated["qualified_count"],
                "coverage": truncated["coverage"],
                "emission": [entry["pair"] for entry in truncated["emission"]],
                "is_prefix": truncated["emission"] == full["emission"][:trunc_k],
            }
        control_points[f"{w}:{n}"] = point

    return {
        "seed": seed,
        "control_points": control_points,
        "evidence": _evidence_check_emission(db, min_w, min_n, top_k),
        "stress": _stress_check_emission(
            db, config, seed, reference_date, min_w, min_n, top_k
        ),
    }


def evaluate_synthetic_emission(
    config: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Evaluates the pre-registered predictions T1-T4 (PR-D-03d).

    Pure (no ArangoDB): consumes the per-seed records produced by
    ``run_seed_emission``. Control-point keys derive from the calibration
    block; the exact anchors live in the same config file.
    """
    exp = config["expectations"]
    calibration = config["calibration"]
    min_w = min(int(w) for w in calibration["candidates_min_wins"])
    min_n = min(int(n) for n in calibration["candidates_min_buyers"])
    control_w = int(calibration.get("control_min_wins", 2))
    control_n = int(calibration.get("control_min_buyers", 2))
    stress_budget = float(calibration["stress"]["time_budget_seconds"])
    k1 = f"{min_w}:1"
    k2 = f"{min_w}:{min_n}"
    k3 = f"{min_w + 1}:{min_n}"
    kc = f"{control_w}:{control_n}"
    failures: dict[str, list[str]] = {f"T{i}": [] for i in range(1, 5)}
    monotonicity_failures: list[str] = []

    for record in records:
        seed = record["seed"]
        points = record["control_points"]

        # T1 — active truncation at the control point, wins_sum tiebreak
        trunc_c = points[kc]["truncated"]
        if trunc_c["emission"] != exp["emission_top1_min2_buyers2_exact"]:
            failures["T1"].append(
                f"seed {seed}: top-1 at {kc} {trunc_c['emission']}"
                f" != {exp['emission_top1_min2_buyers2_exact']}"
            )
        if trunc_c["qualified_count"] != exp[
            "emission_top1_min2_buyers2_qualified_count_exact"
        ]:
            failures["T1"].append(
                f"seed {seed}: qualified at {kc} {trunc_c['qualified_count']}"
                f" != {exp['emission_top1_min2_buyers2_qualified_count_exact']}"
            )
        if trunc_c["coverage"] != exp["emission_top1_min2_buyers2_coverage_exact"]:
            failures["T1"].append(
                f"seed {seed}: coverage at {kc} {trunc_c['coverage']}"
                f" != {exp['emission_top1_min2_buyers2_coverage_exact']}"
            )

        # T2 — active truncation at (min_w, 1), buyer_count tiebreak
        trunc_1 = points[k1]["truncated"]
        if trunc_1["emission"] != exp["emission_top1_min3_buyers1_exact"]:
            failures["T2"].append(
                f"seed {seed}: top-1 at {k1} {trunc_1['emission']}"
                f" != {exp['emission_top1_min3_buyers1_exact']}"
            )
        if trunc_1["qualified_count"] != exp[
            "emission_top1_min3_buyers1_qualified_count_exact"
        ]:
            failures["T2"].append(
                f"seed {seed}: qualified at {k1} {trunc_1['qualified_count']}"
                f" != {exp['emission_top1_min3_buyers1_qualified_count_exact']}"
            )

        # T3 — vacuous truncation == full ordered set; evidence reproduction
        if not trunc_c["is_prefix"] or not trunc_1["is_prefix"]:
            failures["T3"].append(f"seed {seed}: truncated emission not a prefix")
        if len(points[k1]["emission"]) != exp["emission_full_min3_buyers1_exact_count"]:
            failures["T3"].append(
                f"seed {seed}: emission {k1} count {len(points[k1]['emission'])}"
                f" != {exp['emission_full_min3_buyers1_exact_count']}"
            )
        if points[k2]["emission"] != exp["emission_full_min3_buyers2_exact"]:
            failures["T3"].append(
                f"seed {seed}: emission {k2} {points[k2]['emission']}"
                f" != {exp['emission_full_min3_buyers2_exact']}"
            )
        if len(points[k3]["emission"]) != exp["emission_full_min4_buyers2_exact_count"]:
            failures["T3"].append(
                f"seed {seed}: emission {k3} count {len(points[k3]['emission'])}"
                f" != {exp['emission_full_min4_buyers2_exact_count']}"
            )
        if points[kc]["emission"] != exp["emission_full_min2_buyers2_exact"]:
            failures["T3"].append(
                f"seed {seed}: emission {kc} {points[kc]['emission']}"
                f" != {exp['emission_full_min2_buyers2_exact']}"
            )
        evidence = record["evidence"]
        if not evidence["matches"] or not all(evidence["matches"]):
            failures["T3"].append(
                f"seed {seed}: reproduction matches {evidence['matches']}"
            )
        tampered = evidence["tampered"]
        if tampered is None or tampered["integrity"] or tampered["match"]:
            failures["T3"].append(f"seed {seed}: tampered outcome {tampered}")
        if not evidence["legacy_matches"] or not all(evidence["legacy_matches"]):
            failures["T3"].append(
                f"seed {seed}: legacy reproduction {evidence['legacy_matches']}"
            )

        # T4 — scale anchors of the stress population
        stress = record["stress"]
        if (
            stress["projection_blocked"]
            != exp["stress_blocked_projection_min3_buyers2_exact"]
        ):
            failures["T4"].append(
                f"seed {seed}: stress blocked projection"
                f" {stress['projection_blocked']}"
                f" != {exp['stress_blocked_projection_min3_buyers2_exact']}"
            )
        if stress["emission_min_buyers"] != exp["stress_emission_min3_buyers2_exact"]:
            failures["T4"].append(
                f"seed {seed}: stress emission (3,2) {stress['emission_min_buyers']}"
                f" != {exp['stress_emission_min3_buyers2_exact']}"
            )
        if (
            stress["emission_min_buyers_1_count"]
            != exp["stress_emission_min3_buyers1_top500_count_exact"]
        ):
            failures["T4"].append(
                f"seed {seed}: stress top-500 count"
                f" {stress['emission_min_buyers_1_count']}"
                f" != {exp['stress_emission_min3_buyers1_top500_count_exact']}"
            )
        if (
            stress["emission_min_buyers_1_first"]
            != exp["stress_emission_min3_buyers1_first_exact"]
        ):
            failures["T4"].append(
                f"seed {seed}: stress top-500 first"
                f" {stress['emission_min_buyers_1_first']}"
                f" != {exp['stress_emission_min3_buyers1_first_exact']}"
            )
        if stress["qualified_min_buyers_1"] != exp["stress_qualified_min3_buyers1_exact"]:
            failures["T4"].append(
                f"seed {seed}: stress qualified {stress['qualified_min_buyers_1']}"
                f" != {exp['stress_qualified_min3_buyers1_exact']}"
            )
        if not stress["prefix_min_buyers_1"]:
            failures["T4"].append(f"seed {seed}: stress top-500 not a prefix")
        if stress["elapsed_seconds"] >= stress_budget:
            failures["T4"].append(
                f"seed {seed}: stress sweep {stress['elapsed_seconds']}s"
                f" >= {stress_budget}s"
            )

        # Blocking regression: derivations stay bit-a-bit equivalent.
        for key, point in points.items():
            if not point["equivalent"]:
                failures["T3"].append(
                    f"seed {seed}: derivations diverge at {key} (blocking regression)"
                )

        # Monotonicity invariant: blocked projection non-increasing in
        # min_wins (fixed min_buyers) and in min_buyers (fixed min_wins).
        if points[k3]["projection_blocked"] > points[k2]["projection_blocked"]:
            monotonicity_failures.append(
                f"seed {seed}: blocked({k3})={points[k3]['projection_blocked']}"
                f" > blocked({k2})={points[k2]['projection_blocked']}"
            )
        if points[k2]["projection_blocked"] > points[k1]["projection_blocked"]:
            monotonicity_failures.append(
                f"seed {seed}: blocked({k2})={points[k2]['projection_blocked']}"
                f" > blocked({k1})={points[k1]['projection_blocked']}"
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
        "part": "synthetic",
        "predictions": predictions,
        "invariants": invariants,
        "verdict": verdict,
    }


def measure_emission(
    db: StandardDatabase, config: dict[str, Any], reference_date: date
) -> dict[str, Any]:
    """Part B: real sweep with declared top-K emission (PR-D-03d, read-only).

    Per grid point ``{min_wins} × {min_buyers}``: arithmetic blocked
    projections only (the non-materialization guard — the derivation is
    materialized **only** at the selected point, the first whose
    projection fits the guard). At the selected point: blocked derivation
    materialized with ``tracemalloc``, top-K emission, prefix equivalence
    against the full ordered set, incidence double counting, editorial
    increment of the emitted set (30 days, robustness 60 days) and the
    emission executed twice (determinism). The graph must be frozen: a
    changed ``won`` count between the boundaries marks the run as
    discarded (not a refutation).

    Args:
        db: ArangoDB connection (production graph).
        config: Battery configuration (emission calibration block).
        reference_date: Sweep date (anchor of the increment windows).

    Returns:
        Measurement dict (points, selected point, emission, coverage,
        stability, elapsed).
    """
    calibration = config["calibration"]
    emission_config = calibration["emission"]
    blocking = calibration["blocking"]
    candidates_w = sorted(int(w) for w in calibration["candidates_min_wins"])
    candidates_n = sorted(int(n) for n in calibration["candidates_min_buyers"])
    window = int(calibration["increment_window_days"])
    robust_window = int(calibration["robustness_window_days"])
    cutoff = (reference_date - timedelta(days=window)).isoformat()
    cutoff_robust = (reference_date - timedelta(days=robust_window)).isoformat()
    top_k = int(emission_config["top_k"])
    guard = int(blocking["max_pairs_guard"])

    started = time.monotonic()
    won_before = cast("int", db.collection("won").count())

    export_rows = execute_aql(
        db, _EXPORT_QUERY, {"cutoff": cutoff, "cutoffRobust": cutoff_robust}
    )
    rows_by_w = {w: collusion_eligibility(db, min_wins=w) for w in candidates_w}

    points: dict[str, Any] = {}
    selected: tuple[int, int] | None = None
    for w, n in grid_order(candidates_w, candidates_n):
        rows = rows_by_w[w]
        projection = blocked_projection(rows, n)
        points[f"{w}:{n}"] = {
            "projection_unblocked": projected_pair_count(rows),
            "projection_blocked": projection,
        }
        if selected is None and projection < guard:
            selected = (w, n)

    emission_block: dict[str, Any] | None = None
    if selected is not None:
        w, n = selected
        rows = rows_by_w[w]
        pair_buyers, _elapsed, peak = _timed_derivation(
            pair_buyers_from_eligibility_blocked, rows, n, traced=True
        )
        full = ranked_emission(pair_buyers, rows)
        emitted = ranked_emission(pair_buyers, rows, top_k)
        blobs = [
            json.dumps(ranked_emission(pair_buyers, rows, top_k)["emission"], sort_keys=True)
            for _ in range(int(emission_config["determinism_reruns"]))
        ]

        old_rows = [
            {**row, "wins": int(row["wins"]) - int(row["recent_wins"])}
            for row in export_rows
        ]
        robust_rows = [
            {**row, "wins": int(row["wins"]) - int(row["robust_wins"])}
            for row in export_rows
        ]
        emitted_now = _emitted_pair_set(export_rows, w, n, top_k)
        increment = len(emitted_now - _emitted_pair_set(old_rows, w, n, top_k)) / window
        increment_robust = (
            len(emitted_now - _emitted_pair_set(robust_rows, w, n, top_k))
            / robust_window
        )

        emission_block = {
            "point": f"{w}:{n}",
            "top_k": top_k,
            "emitted_count": len(emitted["emission"]),
            "qualified_count": emitted["qualified_count"],
            "coverage": emitted["coverage"],
            "prefix_ok": emitted["emission"] == full["emission"][:top_k],
            "deterministic": len(set(blobs)) == 1,
            "incidences_blocked": sum(
                len(buyers)
                for buyers in _blocked_buyers_by_pair(rows, n).values()
            ),
            "peak_blocked_bytes": peak,
            "increment_daily": round(increment, 4),
            "increment_daily_robust": round(increment_robust, 4),
            "emitted_pairs": [entry["pair"] for entry in emitted["emission"]],
        }

    coverage_rows = execute_aql(db, _COVERAGE_QUERY, {})
    eligible_won = cast("int", coverage_rows[0]) if coverage_rows else 0
    won_after = cast("int", db.collection("won").count())
    coverage = eligible_won / won_after if won_after else 1.0

    return {
        "reference_date": reference_date.isoformat(),
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "candidates_min_wins": candidates_w,
        "candidates_min_buyers": candidates_n,
        "grid_order": [f"{w}:{n}" for w, n in grid_order(candidates_w, candidates_n)],
        "exported_rows": len(export_rows),
        "histogram": {str(k): v for k, v in histogram_from_rows(export_rows).items()},
        "histogram_sum_wins": sum(int(row["wins"]) for row in export_rows),
        "total_won_edges": won_after,
        "eligible_won_edges": eligible_won,
        "siafi_coverage": round(coverage, 6),
        "graph_stable": won_before == won_after,
        "points": points,
        "selected_point": (
            {"min_wins": selected[0], "min_buyers": selected[1]}
            if selected is not None
            else None
        ),
        "emission": emission_block,
        "memory_budget_bytes": int(float(blocking["memory_budget_mb"]) * 1024 * 1024),
        "max_pairs_guard": guard,
        "time_budget_seconds": float(calibration["time_budget_seconds"]),
        "budget": calibration["budget"],
    }


def evaluate_real_emission(
    config: dict[str, Any], measurement: dict[str, Any]
) -> dict[str, Any]:
    """Evaluates the pre-registered real-sweep predictions T5-T9.

    Pure: consumes the measurement produced by ``measure_emission`` over
    the production graph. Degraded regime (low coverage, graph changed
    during the measurement) yields ``inconclusive``, not ``refuted`` —
    the run is discarded and reexecuted (PR-D-03d, sections 3 and 7).
    """
    points = measurement["points"]
    emission = measurement["emission"]
    budget = measurement["budget"]

    t5_failures: list[str] = []
    if measurement["selected_point"] is None:
        t5_failures.append("no grid point with blocked projection below the guard")

    t6_failures: list[str] = []
    if measurement["elapsed_seconds"] >= measurement["time_budget_seconds"]:
        t6_failures.append(
            f"sweep took {measurement['elapsed_seconds']}s"
            f" >= {measurement['time_budget_seconds']}s"
        )
    if emission is not None:
        key = emission["point"]
        if emission["peak_blocked_bytes"] >= measurement["memory_budget_bytes"]:
            t6_failures.append(
                f"blocked peak heap {emission['peak_blocked_bytes']}"
                f" >= {measurement['memory_budget_bytes']} bytes"
            )
        if emission["incidences_blocked"] != points[key]["projection_blocked"]:
            t6_failures.append(
                f"projection {points[key]['projection_blocked']} != incidences"
                f" {emission['incidences_blocked']} at {key}"
            )

    t7_failures: list[str] = []
    if emission is not None:
        if emission["emitted_count"] > int(budget["backlog_max_pairs"]):
            t7_failures.append(
                f"emitted {emission['emitted_count']} pairs above the backlog"
                f" budget {budget['backlog_max_pairs']}"
            )
        if emission["increment_daily"] > float(budget["daily_max_pairs"]):
            t7_failures.append(
                f"daily increment {emission['increment_daily']}"
                f" > {budget['daily_max_pairs']}"
            )

    t8_failures: list[str] = []
    if emission is not None and not emission["prefix_ok"]:
        t8_failures.append("emission is not the exact top-K prefix of the full set")

    t9_failures: list[str] = []
    if emission is not None and not emission["deterministic"]:
        t9_failures.append("emission not byte-identical across reruns")

    regime_failures: list[str] = []
    if not measurement["graph_stable"]:
        regime_failures.append(
            "won edge count changed during the measurement — run discarded"
        )
    if measurement["histogram_sum_wins"] != measurement["eligible_won_edges"]:
        regime_failures.append(
            f"histogram sum {measurement['histogram_sum_wins']}"
            f" != eligible won edges {measurement['eligible_won_edges']}"
        )
    coverage_min = float(config["calibration"]["siafi_coverage_min"])
    if measurement["siafi_coverage"] < coverage_min:
        regime_failures.append(
            f"siafi coverage {measurement['siafi_coverage']} < {coverage_min}"
        )

    refuted = bool(
        t5_failures or t6_failures or t7_failures or t8_failures or t9_failures
    )
    predictions = {
        "T5": {
            "verdict": "refuted" if t5_failures else "success",
            "failures": t5_failures,
        },
        "T6": {
            "verdict": "refuted" if t6_failures else "success",
            "failures": t6_failures,
        },
        "T7": {
            "verdict": "refuted" if t7_failures else "success",
            "failures": t7_failures,
        },
        "T8": {
            "verdict": "refuted" if t8_failures else "success",
            "failures": t8_failures,
        },
        "T9": {
            "verdict": "refuted" if t9_failures else "success",
            "failures": t9_failures,
        },
    }
    if refuted:
        verdict = "refuted"
    elif regime_failures:
        verdict = "inconclusive"
    else:
        verdict = "success"
    return {
        "battery": config["id"],
        "part": "real",
        "measurement": measurement,
        "predictions": predictions,
        "regime": {
            "verdict": "degraded" if regime_failures else "ok",
            "failures": regime_failures,
        },
        "verdict": verdict,
    }


def _seed_lines(record: dict[str, Any]) -> list[str]:
    """Serializes the raw outputs of one seed, one JSON per line."""
    seed = record["seed"]
    aspects = [
        ("sweep_histogram", {"histogram": record["histogram"]}),
        (
            "sweep_pairs_by_candidate",
            {
                "aql": record["pairs_by_candidate"],
                "python": record["pairs_by_candidate_python"],
            },
        ),
        ("sweep_increment_daily", record["increment_daily"]),
        (
            "evidence_reproduction",
            {
                "signals": record["evidence"]["signals"],
                "matches": record["evidence"]["matches"],
                "tampered": record["evidence"]["tampered"],
            },
        ),
    ]
    return [
        json.dumps({"seed": seed, "operator": op, **payload}, default=str)
        for op, payload in aspects
    ]


def _seed_lines_refined(record: dict[str, Any]) -> list[str]:
    """Serializes the raw outputs of one refined seed, one JSON per line."""
    seed = record["seed"]
    aspects = [
        ("sweep_histogram", {"histogram": record["histogram"]}),
        ("sweep_control_points", record["control_points"]),
        (
            "sweep_increment_target",
            {
                "target_point": record["target_point"],
                "pairs_full": record["pairs_full_target"],
                "recent_pairs": record["recent_pairs_target"],
            },
        ),
        (
            "evidence_reproduction",
            {
                "signals": record["evidence"]["signals"],
                "matches": record["evidence"]["matches"],
                "tampered": record["evidence"]["tampered"],
            },
        ),
    ]
    return [
        json.dumps({"seed": seed, "operator": op, **payload}, default=str)
        for op, payload in aspects
    ]


def _seed_lines_blocked(record: dict[str, Any]) -> list[str]:
    """Serializes the raw outputs of one blocked seed, one JSON per line."""
    seed = record["seed"]
    aspects = [
        ("sweep_control_points", record["control_points"]),
        (
            "evidence_reproduction",
            {
                "signals": record["evidence"]["signals"],
                "matches": record["evidence"]["matches"],
                "blocked_matches": record["evidence"]["blocked_matches"],
                "tampered": record["evidence"]["tampered"],
            },
        ),
        ("stress", record["stress"]),
    ]
    return [
        json.dumps({"seed": seed, "operator": op, **payload}, default=str)
        for op, payload in aspects
    ]


def _seed_lines_emission(record: dict[str, Any]) -> list[str]:
    """Serializes the raw outputs of one emission seed, one JSON per line."""
    seed = record["seed"]
    aspects = [
        ("sweep_control_points", record["control_points"]),
        (
            "evidence_reproduction",
            {
                "signals": record["evidence"]["signals"],
                "matches": record["evidence"]["matches"],
                "legacy_matches": record["evidence"]["legacy_matches"],
                "tampered": record["evidence"]["tampered"],
            },
        ),
        ("stress", record["stress"]),
    ]
    return [
        json.dumps({"seed": seed, "operator": op, **payload}, default=str)
        for op, payload in aspects
    ]


def run_battery(config: dict[str, Any], out_dir: Path) -> list[dict[str, Any]]:
    """Runs Part A/C: synthetic seeds against a disposable ArangoDB.

    Creates a disposable database (dropping any leftover at start) and
    drops it again at the end, success or failure. Writes the per-seed
    raw outputs and the synthetic summary (P1-P4).

    Args:
        config: Battery configuration.
        out_dir: Directory for the raw per-seed outputs and summary.

    Returns:
        The per-seed records.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    emission = is_emission(config)
    refined = is_refined(config)
    blocked = is_blocked(config) and not emission
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
            if emission:
                record = run_seed_emission(db, config, seed)
                lines = _seed_lines_emission(record)
            elif blocked:
                record = run_seed_blocked(db, config, seed)
                lines = _seed_lines_blocked(record)
            elif refined:
                record = run_seed_refined(db, config, seed)
                lines = _seed_lines_refined(record)
            else:
                record = run_seed(db, config, seed)
                lines = _seed_lines(record)
            with (out_dir / f"seed_{seed}.jsonl").open("w") as fh:
                for line in lines:
                    fh.write(line + "\n")
            records.append(record)
        if emission:
            summary = evaluate_synthetic_emission(config, records)
        elif blocked:
            summary = evaluate_synthetic_blocked(config, records)
        elif refined:
            summary = evaluate_synthetic_refined(config, records)
        else:
            summary = evaluate_synthetic(config, records)
        (out_dir / "summary_synthetic.json").write_text(
            json.dumps(summary, indent=2) + "\n"
        )
        if blocked or emission:
            # detect_battery.py reads summary.json even with --skip-real;
            # the real sweep (Part B) rewrites it when it runs.
            (out_dir / "summary.json").write_text(
                json.dumps(
                    {
                        "battery": config["id"],
                        "synthetic": {
                            "predictions": summary["predictions"],
                            "invariants": summary["invariants"],
                            "verdict": summary["verdict"],
                        },
                        "real": "pending",
                        "predictions": summary["predictions"],
                        "verdict": summary["verdict"],
                    },
                    indent=2,
                )
                + "\n"
            )
        return records
    finally:
        sys_db.delete_database(db_name)
        logger.info("Battery database dropped: %s", db_name)


def run_real_sweep(
    config: dict[str, Any], out_dir: Path, db: Any = None
) -> dict[str, Any]:
    """Runs Part B: read-only calibration sweep over the production graph.

    Must run in a freeze window (no ingestion during the measurement, per
    PR-D-03 section 6). Writes ``real_sweep.json`` and the merged
    ``summary.json`` (P1-P4 from the synthetic part, when present, plus
    P5-P8 and the real monotonicity invariant).

    Args:
        config: Battery configuration.
        out_dir: Battery output directory.
        db: ArangoDB connection (defaults to the production graph).

    Returns:
        The merged summary dict.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    emission = is_emission(config)
    refined = is_refined(config)
    blocked = is_blocked(config) and not emission
    if db is None:
        db = get_capiba_db()
    if emission:
        measurement = measure_emission(db, config, date.today())
    elif blocked:
        measurement = measure_blocked(db, config, date.today())
    elif refined:
        measurement = measure_refined(db, config, date.today())
    else:
        measurement = measure(db, config, date.today())
    (out_dir / "real_sweep.json").write_text(json.dumps(measurement, indent=2) + "\n")
    if emission:
        real_summary = evaluate_real_emission(config, measurement)
    elif blocked:
        real_summary = evaluate_real_blocked(config, measurement)
    elif refined:
        real_summary = evaluate_real_refined(config, measurement)
    else:
        real_summary = evaluate_real(config, measurement)

    if blocked or emission:
        pts = measurement["points"]
        mono_failures: list[str] = []
        for n in measurement["candidates_min_buyers"]:
            ordered_w = [
                pts[f"{w}:{n}"]["projection_blocked"]
                for w in sorted(measurement["candidates_min_wins"])
            ]
            if any(hi > lo for lo, hi in zip(ordered_w, ordered_w[1:], strict=False)):
                mono_failures.append(
                    f"blocked projection not monotonic over w at n={n}"
                )
        for w in measurement["candidates_min_wins"]:
            ordered_n = [
                pts[f"{w}:{n}"]["projection_blocked"]
                for n in sorted(measurement["candidates_min_buyers"])
            ]
            if any(hi > lo for lo, hi in zip(ordered_n, ordered_n[1:], strict=False)):
                mono_failures.append(
                    f"blocked projection not monotonic over n at w={w}"
                )
        for key, point in pts.items():
            if point["projection_blocked"] > point["projection_unblocked"]:
                mono_failures.append(f"blocked projection above unblocked at {key}")
        monotonic = not mono_failures
    elif refined:
        counts_grid = measurement["pairs_grid_python"]
        mono_failures = []
        for n in measurement["candidates_min_buyers"]:
            ordered_w = [
                counts_grid[f"{w}:{n}"]
                for w in sorted(measurement["candidates_min_wins"])
            ]
            if any(hi > lo for lo, hi in zip(ordered_w, ordered_w[1:], strict=False)):
                mono_failures.append(f"counts not monotonic over w at n={n}")
        for w in measurement["candidates_min_wins"]:
            ordered_n = [
                counts_grid[f"{w}:{n}"]
                for n in sorted(measurement["candidates_min_buyers"])
            ]
            if any(hi > lo for lo, hi in zip(ordered_n, ordered_n[1:], strict=False)):
                mono_failures.append(f"counts not monotonic over n at w={w}")
        monotonic = not mono_failures
    else:
        counts = measurement["pairs_by_candidate_python"]
        ordered = [n for _, n in sorted(counts.items(), key=lambda kv: int(kv[0]))]
        monotonic = all(hi <= lo for lo, hi in zip(ordered, ordered[1:], strict=False))
        mono_failures = [] if monotonic else [f"counts not monotonic: {counts}"]
    real_summary["invariants"] = {
        "monotonicity": {
            "verdict": "success" if monotonic else "refuted",
            "failures": mono_failures,
        }
    }
    if not monotonic and real_summary["verdict"] != "refuted":
        real_summary["verdict"] = "refuted"

    merged: dict[str, Any] = {"battery": config["id"]}
    synthetic_path = out_dir / "summary_synthetic.json"
    synthetic_ok = True
    if synthetic_path.exists():
        synthetic = json.loads(synthetic_path.read_text())
        merged["synthetic"] = {
            "predictions": synthetic["predictions"],
            "invariants": synthetic["invariants"],
            "verdict": synthetic["verdict"],
        }
        synthetic_ok = synthetic["verdict"] == "success"
    merged["real"] = real_summary
    merged["predictions"] = real_summary["predictions"]
    if not synthetic_ok or real_summary["verdict"] == "refuted":
        merged["verdict"] = "refuted"
    elif real_summary["verdict"] == "inconclusive":
        merged["verdict"] = "inconclusive"
    else:
        merged["verdict"] = "success"
    (out_dir / "summary.json").write_text(json.dumps(merged, indent=2) + "\n")
    return merged
