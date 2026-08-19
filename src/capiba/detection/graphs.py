"""Network analysis for coordinated fraud detection.

Chunks: collusion_network, ownership_chain, anomalous_geography
Responsibility: Model buyer-supplier relationships via graphs
in ArangoDB to detect collusion, shell companies and anomalous patterns.

Dependencies: python-arango
"""

from __future__ import annotations

import logging
from itertools import combinations
from typing import Any, cast

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql, get_capiba_db

logger = logging.getLogger(__name__)


def detect_collusion(
    db: StandardDatabase | None = None,
    min_wins: int = 3,
) -> list[set[str]]:
    """Detects pairs of suppliers alternating wins for the same buyer.

    Adapted semantics (PR-D-02, section 3): there is no contract→buyer
    edge, so the buyer is the ``buyer.siafi_code`` attribute of the
    ``contracts`` document. For each buyer b, let S_b be the set of
    suppliers with >= ``min_wins`` ``won`` edges (suppliers → contracts)
    to contracts of b. The output is every pair {s1, s2} ⊆ S_b, s1 ≠ s2.

    Args:
        db: ArangoDB connection. If None, creates a new one.
        min_wins: Minimum number of wins per (buyer, supplier) to be eligible.

    Returns:
        List of pairs (sets of two CNPJs), sorted deterministically.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        FOR c IN contracts
            FILTER c.buyer.siafi_code != null
            FOR s IN INBOUND c won
                COLLECT buyer = c.buyer.siafi_code, supplier = s._key INTO wins
                FILTER LENGTH(wins) >= @minWins
                RETURN {buyer, supplier}
    """

    rows = execute_aql(db, query, {"minWins": min_wins})

    suppliers_by_buyer: dict[str, list[str]] = {}
    for row in rows:
        suppliers_by_buyer.setdefault(row["buyer"], []).append(row["supplier"])

    pairs: list[set[str]] = []
    for buyer in sorted(suppliers_by_buyer):
        for s1, s2 in combinations(sorted(suppliers_by_buyer[buyer]), 2):
            pairs.append({s1, s2})
    pairs.sort(key=lambda pair: tuple(sorted(pair)))
    logger.info("Suspected collusion pairs: %d", len(pairs))
    return pairs


def trace_ownership(
    cnpj: str,
    max_depth: int = 3,
    db: StandardDatabase | None = None,
) -> list[list[str]]:
    """Traces the ownership chain (beneficial ownership).

    Adapted semantics (PR-D-02, section 3): simple paths (no repeated
    vertex, cycles blocked via ``uniqueVertices: "path"``) OUTBOUND from
    ``companies/<cnpj>`` over the ``owns`` edge collection, depth
    1..``max_depth``.

    Args:
        cnpj: Input CNPJ (unformatted).
        max_depth: Maximum search depth.
        db: ArangoDB connection. If None, creates a new one.

    Returns:
        List of ownership paths (sequences of ``_key``, start vertex
        included), sorted deterministically.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        FOR v, e, p IN 1..@maxDepth OUTBOUND CONCAT("companies/", @cnpj) owns
            OPTIONS {uniqueVertices: "path"}
            RETURN p.vertices[*]._key
    """

    bind_vars = {
        "cnpj": cnpj,
        "maxDepth": max_depth,
    }

    rows = execute_aql(db, query, bind_vars)
    # The AQL query returns lists of vertex keys, not documents
    paths = sorted(cast(list[list[str]], [list(row) for row in rows]))
    logger.info("Ownership paths found: %d", len(paths))
    return paths


def anomalous_geography(
    db: StandardDatabase | None = None,
    max_distance_km: float = 100.0,
) -> list[dict[str, Any]]:
    """Detects dispersed suppliers with concentrated wins.

    Args:
        db: ArangoDB connection. If None, creates a new one.
        max_distance_km: Maximum distance considered anomalous.

    Returns:
        List of supplier/bid pairs with distance above the limit.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        FOR f IN suppliers
            FILTER f.latitude != null AND f.longitude != null
            FOR v IN OUTBOUND f GRAPH @graphName
                FILTER v.type == "bid"
                FILTER v.latitude != null AND v.longitude != null
                LET d = SQRT(
                    POW(f.latitude - v.latitude, 2) +
                    POW(f.longitude - v.longitude, 2)
                ) * 111.0
                FILTER d > @maxDistance
                RETURN {
                    supplier: f._key,
                    bid: v._key,
                    distance_km: d
                }
    """

    bind_vars = {
        "graphName": db.graph("capiba_graph").name,
        "maxDistance": max_distance_km,
    }

    anomalies = execute_aql(db, query, bind_vars)
    logger.info("Geographic anomalies found: %d", len(anomalies))
    return anomalies
