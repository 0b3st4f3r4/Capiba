"""Network analysis for coordinated fraud detection.

Chunks: collusion_network, ownership_chain, anomalous_geography
Responsibility: Model buyer-supplier relationships via graphs
in ArangoDB to detect collusion, shell companies and anomalous patterns.

Dependencies: python-arango
"""

from __future__ import annotations

import logging
from typing import Any, cast

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql, get_capiba_db

logger = logging.getLogger(__name__)


def detect_collusion(
    db: StandardDatabase | None = None,
    min_wins: int = 3,
) -> list[set[str]]:
    """Detects groups of companies in potential collusion (bid-rigging).

    Identifies dense subgraphs where a group of companies
    alternates wins in bids from the same buyer.

    Args:
        db: ArangoDB connection. If None, creates a new one.
        min_wins: Minimum number of wins to consider a pattern.

    Returns:
        List of sets of CNPJs in potential collusion.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        FOR bid IN bids
            LET winners = (
                FOR f IN INBOUND bid GRAPH @graphName
                    RETURN f._key
            )
            FILTER LENGTH(winners) >= 2
            FOR a IN winners
                FOR b IN winners
                    FILTER a < b
                    COLLECT pair = { a, b } INTO occurrences
                    FILTER LENGTH(occurrences) >= @minWins
                    RETURN [pair.a, pair.b]
    """

    bind_vars = {
        "graphName": db.graph("capiba_graph").name,
        "minWins": min_wins,
    }

    results = execute_aql(db, query, bind_vars)
    suspects = [set(pair) for pair in results]
    logger.info("Suspected collusion groups: %d", len(suspects))
    return suspects


def trace_ownership(
    cnpj: str,
    max_depth: int = 3,
    db: StandardDatabase | None = None,
) -> list[list[str]]:
    """Traces the ownership chain (beneficial ownership).

    Args:
        cnpj: Input CNPJ (unformatted).
        max_depth: Maximum search depth.
        db: ArangoDB connection. If None, creates a new one.

    Returns:
        List of ownership paths.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        WITH companies
        FOR v, e, p IN 1..@maxDepth OUTBOUND CONCAT("companies/", @cnpj)
            GRAPH @graphName
            RETURN p.vertices[*]._key
    """

    bind_vars = {
        "cnpj": cnpj,
        "maxDepth": max_depth,
        "graphName": db.graph("capiba_graph").name,
    }

    paths = execute_aql(db, query, bind_vars)
    logger.info("Ownership paths found: %d", len(paths))
    # The AQL query returns lists of vertex keys, not documents
    return cast(list[list[str]], paths)


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
