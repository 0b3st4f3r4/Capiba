"""Network analysis for coordinated fraud detection.

Chunks: collusion_network, ownership_chain
Responsibility: Model buyer-supplier relationships via graphs
in ArangoDB to detect collusion, shell companies and anomalous patterns.

The legacy ``anomalous_geography`` AQL operator was removed (PR-D-09,
Revisões 2026-08-20): it filtered ``bid`` vertices the graph never
creates and approximated distance with a planar euclidean shortcut — the
signal now lives in ``capiba.detection.geography`` as a pure function over
the silver tables.

Dependencies: python-arango
"""

from __future__ import annotations

import logging
from itertools import combinations
from typing import Any, cast

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql, get_capiba_db

logger = logging.getLogger(__name__)


def collusion_eligibility(
    db: StandardDatabase | None = None,
    min_wins: int = 3,
) -> list[dict[str, Any]]:
    """Lists the eligible (buyer, supplier) rows with their win counts.

    Adapted semantics (PR-D-02, section 3): there is no contract→buyer
    edge, so the buyer is the ``buyer.siafi_code`` attribute of the
    ``contracts`` document. A row is eligible when the supplier has >=
    ``min_wins`` ``won`` edges (suppliers → contracts) to contracts of the
    buyer. The sorted rows are also the eligibility snapshot stored in the
    graph evidence package (PR-D-03).

    Args:
        db: ArangoDB connection. If None, creates a new one.
        min_wins: Minimum number of wins per (buyer, supplier) to be eligible.

    Returns:
        Rows ``{"buyer", "supplier", "wins"}`` sorted by (buyer, supplier).
    """
    if db is None:
        db = get_capiba_db()

    query = """
        FOR c IN contracts
            FILTER c.buyer.siafi_code != null
            FOR s IN INBOUND c won
                COLLECT buyer = c.buyer.siafi_code, supplier = s._key INTO wins
                FILTER LENGTH(wins) >= @minWins
                RETURN {buyer, supplier, wins: LENGTH(wins)}
    """

    rows = execute_aql(db, query, {"minWins": min_wins})
    return sorted(rows, key=lambda row: (row["buyer"], row["supplier"]))


def pair_buyers_from_eligibility(
    rows: list[dict[str, Any]], min_buyers: int = 1
) -> list[tuple[tuple[str, str], list[str]]]:
    """Derives collusion pairs with the buyers where each pair is eligible.

    Refined semantics (PR-D-03b, section 3): for each buyer b with
    eligible suppliers S_b, form every pair {s1, s2} ⊆ S_b; a pair is
    flagged only when it is eligible in >= ``min_buyers`` distinct buyers.
    ``min_buyers = 1`` reduces exactly to the D-02/D-03 semantics.

    Args:
        rows: Eligibility rows from ``collusion_eligibility``.
        min_buyers: Minimum number of distinct buyers sharing the pair.

    Returns:
        Sorted list of ``(pair, buyers)`` — pair as a sorted tuple of two
        CNPJs, buyers as the sorted list of siafi codes.
    """
    suppliers_by_buyer: dict[str, list[str]] = {}
    for row in rows:
        suppliers_by_buyer.setdefault(row["buyer"], []).append(row["supplier"])

    buyers_by_pair: dict[tuple[str, str], list[str]] = {}
    for buyer in sorted(suppliers_by_buyer):
        for s1, s2 in combinations(sorted(suppliers_by_buyer[buyer]), 2):
            buyers_by_pair.setdefault((s1, s2), []).append(buyer)

    return sorted(
        (pair, sorted(buyers))
        for pair, buyers in buyers_by_pair.items()
        if len(buyers) >= min_buyers
    )


def pairs_from_eligibility(
    rows: list[dict[str, Any]], min_buyers: int = 1
) -> list[set[str]]:
    """Derives the collusion pairs from eligibility rows (pure function).

    For each buyer b with eligible suppliers S_b, the output is every pair
    {s1, s2} ⊆ S_b, s1 ≠ s2 — suppliers alternating wins for the same
    buyer (PR-D-02, section 3) — restricted to pairs eligible in >=
    ``min_buyers`` distinct buyers (PR-D-03b). Deterministic and exact by
    construction.

    Args:
        rows: Eligibility rows from ``collusion_eligibility``.
        min_buyers: Minimum number of distinct buyers sharing the pair.

    Returns:
        List of pairs (sets of two CNPJs), sorted deterministically.
    """
    return [
        {s1, s2}
        for (s1, s2), _buyers in pair_buyers_from_eligibility(rows, min_buyers)
    ]


def detect_collusion(
    db: StandardDatabase | None = None,
    min_wins: int = 3,
    min_buyers: int = 1,
) -> list[set[str]]:
    """Detects pairs of suppliers alternating wins for the same buyer(s).

    Composition of ``collusion_eligibility`` (AQL aggregation) and
    ``pairs_from_eligibility`` (pure pair derivation), in the adapted
    semantics of PR-D-02, section 3, refined by co-occurrence across
    buyers in PR-D-03b, section 3.

    Args:
        db: ArangoDB connection. If None, creates a new one.
        min_wins: Minimum number of wins per (buyer, supplier) to be eligible.
        min_buyers: Minimum number of distinct buyers sharing the pair.

    Returns:
        List of pairs (sets of two CNPJs), sorted deterministically.
    """
    pairs = pairs_from_eligibility(collusion_eligibility(db, min_wins), min_buyers)
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
    ``companies/<cnpj_basico>`` over the FtM ``ownership`` edge collection
    (fed with real corporate partners since then), depth
    1..``max_depth``. A 14-digit CNPJ is normalized to its ``cnpj_basico``
    (the vertex key).

    Args:
        cnpj: Input CNPJ (unformatted, 8 or 14 digits).
        max_depth: Maximum search depth.
        db: ArangoDB connection. If None, creates a new one.

    Returns:
        List of ownership paths (sequences of ``_key``, start vertex
        included), sorted deterministically.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        FOR v, e, p IN 1..@maxDepth OUTBOUND CONCAT("companies/", @cnpj) ownership
            OPTIONS {uniqueVertices: "path"}
            RETURN p.vertices[*]._key
    """

    bind_vars = {
        "cnpj": cnpj[:8],
        "maxDepth": max_depth,
    }

    rows = execute_aql(db, query, bind_vars)
    # The AQL query returns lists of vertex keys, not documents
    paths = sorted(cast(list[list[str]], [list(row) for row in rows]))
    logger.info("Ownership paths found: %d", len(paths))
    return paths


def partners_of_buyer(
    siafi_code: str,
    db: StandardDatabase | None = None,
) -> list[dict[str, Any]]:
    """Lists the partners (sócios) of the suppliers of a buyer.

    contracts of the buyer → supplier CNPJ (14d) → FtM ``companies``
    vertex (``cnpj_basico``) → INBOUND ``ownership``/``directorship``
    → persons/companies.

    Args:
        siafi_code: SIAFI code of the buying agency.
        db: ArangoDB connection. If None, creates a new one.

    Returns:
        List of ``{supplier_cnpj, company, edge, partner_key,
        partner_schema, partner_name}``, deduplicated and sorted
        deterministically.
    """
    if db is None:
        db = get_capiba_db()

    query = """
        FOR c IN contracts
            FILTER c.buyer.siafi_code == @siafiCode
            LET supplierCnpj = c.supplier.cnpj
            FILTER supplierCnpj != null
            FOR v, e IN 1..1 INBOUND CONCAT("companies/", LEFT(supplierCnpj, 8))
                ownership, directorship
                RETURN DISTINCT {
                    supplier_cnpj: supplierCnpj,
                    company: LEFT(supplierCnpj, 8),
                    edge: PARSE_IDENTIFIER(e._id).collection,
                    partner_key: v._key,
                    partner_schema: v.schema,
                    partner_name: v.nome != null ? v.nome : v.razao_social
                }
    """

    rows = execute_aql(db, query, {"siafiCode": siafi_code})
    rows.sort(key=lambda r: (r["supplier_cnpj"], r["partner_key"], r["edge"]))
    logger.info("Partners of buyer %s: %d", siafi_code, len(rows))
    return rows
