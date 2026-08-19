"""API service layer.

Responsibility: ArangoDB access (AQL queries) and composition
of risk signals from the detection operators, keeping
routers free of business logic.

Dependencies: capiba.db.arangodb, capiba.detection
"""

from __future__ import annotations

import logging
from datetime import date
from decimal import Decimal
from typing import Any

import pandas as pd
from arango.database import StandardDatabase
from fastapi import HTTPException

from capiba.api.cache import cached
from capiba.api.schemas import (
    RankingItem,
    RankingResponse,
    Signal,
    SignalsResponse,
    SignalType,
)
from capiba.config import REDIS_TTL_RANKING, REDIS_TTL_SIGNALS
from capiba.db.arangodb import execute_aql, get_capiba_db
from capiba.detection.signals import (
    benford_deviation,
    duration_outlier_share,
    isolation_forest_rate,
    single_bid_score,
)
from capiba.detection.statistical import hhi_index

logger = logging.getLogger(__name__)

# Signal emission thresholds (eligibility minimums live in
# capiba.detection.signals: MIN_BENFORD_AMOUNTS, MIN_ISOLATION_FOREST_CONTRACTS)
_THRESHOLD_NON_COMPETITIVE = 0.5
_THRESHOLD_HHI = 0.25  # classic high-concentration reference
_THRESHOLD_BENFORD_P = 0.05
_THRESHOLD_DURATION_OUTLIER = 0.2
_MIN_DURATION = 4
_THRESHOLD_ISOLATION_FOREST = 0.2

# Composite index weights (renormalized over the emitted signals)
_SIGNAL_WEIGHTS = {
    SignalType.SINGLE_BID: 0.35,
    SignalType.CONCENTRATION: 0.35,
    SignalType.ANOMALOUS_PRICE: 0.15,
    SignalType.ANOMALOUS_DURATION: 0.15,
}
_ALERT_THRESHOLD = 0.7

_HTTP_503_DETAIL = "ArangoDB database unavailable"


def get_db() -> StandardDatabase:
    """Returns a connection to ArangoDB.

    Raises:
        HTTPException 503: If the database is unavailable.
    """
    try:
        return get_capiba_db()
    except Exception as exc:
        logger.warning("ArangoDB unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc


def _execute(
    db: StandardDatabase,
    query: str,
    bind_vars: dict[str, Any],
) -> list[dict[str, Any]]:
    """Executes AQL converting database failures into HTTP 503."""
    try:
        return execute_aql(db, query, bind_vars)
    except Exception as exc:
        logger.warning("AQL query failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc


def fetch_supplier_contracts(db: StandardDatabase, cnpj: str) -> list[dict[str, Any]]:
    """Fetches all contracts won by a supplier (CNPJ)."""
    query = """
        FOR c IN contracts
            FILTER c.supplier.cnpj == @cnpj
            RETURN c
    """
    return _execute(db, query, {"cnpj": cnpj})


def fetch_buyer_contracts(
    db: StandardDatabase,
    siafi_code: str,
) -> list[dict[str, Any]]:
    """Fetches all contracts of a buying agency (SIAFI code)."""
    query = """
        FOR c IN contracts
            FILTER c.buyer.siafi_code == @siafi_code
            RETURN c
    """
    return _execute(db, query, {"siafi_code": siafi_code})


def aggregate_ranking(
    db: StandardDatabase,
    uf: str | None,
    period_start: date | None,
    period_end: date | None,
) -> list[dict[str, Any]]:
    """Aggregates risk metrics per municipality via AQL (cached in Redis).

    The aggregation is a full scan over the contracts collection, so the
    result is cached per (uf, period) with REDIS_TTL_RANKING; without
    Redis the query runs directly on every request.

    Returns per (municipality, UF): total contracts, total value,
    count in non-competitive modality and supplier HHI.
    """
    key = f"capiba:api:ranking:{uf or 'all'}:{period_start or 'any'}:{period_end or 'any'}"
    return cached(
        key,
        lambda: _aggregate_ranking(db, uf, period_start, period_end),
        ttl=REDIS_TTL_RANKING,
    )


def _aggregate_ranking(
    db: StandardDatabase,
    uf: str | None,
    period_start: date | None,
    period_end: date | None,
) -> list[dict[str, Any]]:
    """Runs the ranking aggregation AQL (uncached — see aggregate_ranking)."""
    query = """
        LET per_supplier = (
            FOR c IN contracts
                FILTER c.buyer.city != null
                FILTER @uf == null || c.buyer.uf == @uf
                FILTER @start == null || c.signature_date >= @start
                FILTER @end == null || c.signature_date <= @end
                LET non_competitive = CONTAINS(c.modality, "dispensa")
                    || CONTAINS(c.modality, "inexigibilidade")
                COLLECT municipality = c.buyer.city,
                        uf = c.buyer.uf,
                        supplier = c.supplier.cnpj
                AGGREGATE supplier_value = SUM(TO_NUMBER(c.amount)),
                           supplier_contracts = COUNT(1),
                           non_competitive_count = SUM(non_competitive ? 1 : 0)
                RETURN {municipality, uf, supplier_value,
                        supplier_contracts, non_competitive_count}
        )
        FOR r IN per_supplier
            COLLECT municipality = r.municipality, uf = r.uf INTO grp
            LET total_value = SUM(grp[*].r.supplier_value)
            LET total_contracts = SUM(grp[*].r.supplier_contracts)
            LET non_competitive_count = SUM(grp[*].r.non_competitive_count)
            LET hhi = total_value > 0
                ? SUM(FOR g IN grp
                      RETURN POW(g.r.supplier_value / total_value, 2))
                : 0.0
            RETURN {municipality, uf, total_contracts, total_value,
                    non_competitive_count, hhi}
    """
    bind_vars = {
        "uf": uf,
        "start": period_start.isoformat() if period_start else None,
        "end": period_end.isoformat() if period_end else None,
    }
    return _execute(db, query, bind_vars)


def build_ranking(
    rows: list[dict[str, Any]],
    period_start: date | None,
    period_end: date | None,
    limit: int,
) -> RankingResponse:
    """Builds the RankingResponse from the database aggregations.

    Municipal risk index: average between supplier HHI and the
    rate of contracts in non-competitive modality, descending order.
    """
    today = date.today()
    items: list[RankingItem] = []
    for row in rows:
        total_contracts = int(row["total_contracts"])
        non_competitive_rate = (
            row["non_competitive_count"] / total_contracts if total_contracts else 0.0
        )
        index = round(min(0.5 * float(row["hhi"]) + 0.5 * non_competitive_rate, 1.0), 4)
        items.append(
            RankingItem(
                municipality=row["municipality"],
                uf=row["uf"],
                risk_index=index,
                total_contracts=total_contracts,
                total_value=Decimal(str(row["total_value"])),
            )
        )

    items.sort(key=lambda item: item.risk_index, reverse=True)
    return RankingResponse(
        period_start=period_start or today,
        period_end=period_end or today,
        ranking=items[:limit],
    )


def compute_signals(
    cnpj: str,
    contracts: list[dict[str, Any]],
    db: StandardDatabase,
) -> SignalsResponse:
    """Runs the detection operators over the supplier's contracts (cached).

    The computation trains an IsolationForest per request, so the response
    is cached per CNPJ with REDIS_TTL_SIGNALS; without Redis it is computed
    directly on every request.

    Args:
        cnpj: Entity CNPJ (14 digits).
        contracts: Supplier contract documents in ArangoDB.
        db: Database connection (for complementary HHI queries).

    Returns:
        SignalsResponse with detected signals and composite risk index.
        A CNPJ without contracts returns a valid empty response (risk 0).
    """
    return cached(
        f"capiba:api:signals:{cnpj}",
        lambda: _compute_signals(cnpj, contracts, db),
        ttl=REDIS_TTL_SIGNALS,
        model=SignalsResponse,
    )


def _compute_signals(
    cnpj: str,
    contracts: list[dict[str, Any]],
    db: StandardDatabase,
) -> SignalsResponse:
    """Computes the risk signals (uncached — see compute_signals)."""
    if not contracts:
        return SignalsResponse(entity=cnpj, risk_index=0.0, signals=[], alert=False)

    signals: list[Signal] = []

    signal_single_bid = _signal_single_bid(contracts)
    if signal_single_bid is not None:
        signals.append(signal_single_bid)

    signal_concentration = _signal_concentration(contracts, db)
    if signal_concentration is not None:
        signals.append(signal_concentration)

    signal_price = _signal_anomalous_price(contracts)
    if signal_price is not None:
        signals.append(signal_price)

    signal_duration = _signal_anomalous_duration(contracts)
    if signal_duration is not None:
        signals.append(signal_duration)

    if signals:
        total_weight = sum(_SIGNAL_WEIGHTS[s.type] for s in signals)
        index = round(
            sum(_SIGNAL_WEIGHTS[s.type] * s.score for s in signals) / total_weight, 4
        )
    else:
        index = 0.0

    return SignalsResponse(
        entity=cnpj,
        risk_index=index,
        signals=signals,
        alert=index >= _ALERT_THRESHOLD,
    )


def _signal_single_bid(contracts: list[dict[str, Any]]) -> Signal | None:
    """Rate of non-competitive modalities as a single-bid proxy.

    Persisted contracts do not store 'num_participants';
    dispensa/inexigibilidade are structurally dispute-free processes
    (see ``capiba.detection.signals.is_non_competitive``).
    """
    rate = single_bid_score(c.get("modality") for c in contracts)
    if rate < _THRESHOLD_NON_COMPETITIVE:
        return None
    return Signal(
        type=SignalType.SINGLE_BID,
        score=rate,
        evidence=(
            f"{rate:.0%} of contracts in non-competitive modality "
            "(dispensa/inexigibilidade)"
        ),
    )


def _signal_concentration(
    contracts: list[dict[str, Any]],
    db: StandardDatabase,
) -> Signal | None:
    """Maximum HHI among the buyers served by the supplier."""
    codes = {
        c["buyer"]["siafi_code"]
        for c in contracts
        if c.get("buyer") and c["buyer"].get("siafi_code")
    }
    hhi_max = 0.0
    top_buyer: str | None = None
    for code in codes:
        buyer_contracts = fetch_buyer_contracts(db, code)
        df = pd.DataFrame(
            [
                {
                    "buyer_id": code,
                    "supplier_id": _supplier_id(doc),
                    "amount": _float_amount(doc),
                }
                for doc in buyer_contracts
            ]
        )
        hhi = hhi_index(code, df)
        if hhi > hhi_max:
            hhi_max, top_buyer = hhi, code

    if hhi_max < _THRESHOLD_HHI:
        return None
    return Signal(
        type=SignalType.CONCENTRATION,
        score=hhi_max,
        evidence=f"HHI {hhi_max:.2f} concentration on buyer {top_buyer}",
    )


def _signal_anomalous_price(contracts: list[dict[str, Any]]) -> Signal | None:
    """Benford deviation and value anomalies via IsolationForest."""
    score = 0.0
    evidence: str | None = None

    amounts = [_float_amount(c) for c in contracts]
    benford = benford_deviation(amounts)
    if benford is not None and (1 - benford) < _THRESHOLD_BENFORD_P:
        score = benford
        evidence = f"Benford's Law deviation (p={1 - benford:.4f})"

    durations = [_duration_days(c) for c in contracts]
    forest_rate = isolation_forest_rate(amounts, durations)
    if (
        forest_rate is not None
        and forest_rate >= _THRESHOLD_ISOLATION_FOREST
        and forest_rate > score
    ):
        score = forest_rate
        evidence = (
            f"{forest_rate:.0%} of contracts anomalous in amount/duration "
            "(IsolationForest)"
        )

    if score <= 0:
        return None
    return Signal(type=SignalType.ANOMALOUS_PRICE, score=score, evidence=evidence)


def _signal_anomalous_duration(contracts: list[dict[str, Any]]) -> Signal | None:
    """Proportion of contracts with anomalous validity (IQR outliers)."""
    durations = [d for d in (_duration_days(c) for c in contracts) if d is not None]
    rate = duration_outlier_share(durations, minimum=_MIN_DURATION)
    if rate is None or rate < _THRESHOLD_DURATION_OUTLIER:
        return None
    # rate is the exact share (k / n), so rate * n recovers the outlier count
    outlier_count = int(round(rate * len(durations)))
    return Signal(
        type=SignalType.ANOMALOUS_DURATION,
        score=round(rate, 4),
        evidence=f"{outlier_count} of {len(durations)} contracts with anomalous validity (IQR)",
    )


def _supplier_id(doc: dict[str, Any]) -> str:
    """Extracts the supplier identifier from a contract document."""
    supplier = doc.get("supplier") or {}
    return str(supplier.get("cnpj") or supplier.get("cpf") or "unknown")


def _float_amount(doc: dict[str, Any]) -> float:
    """Converts the contract amount (persisted as string) to float."""
    try:
        return float(doc.get("amount") or 0)
    except (TypeError, ValueError):
        return 0.0


def _duration_days(doc: dict[str, Any]) -> float | None:
    """Computes the contract validity duration in days."""
    try:
        start = date.fromisoformat(str(doc["validity_start"])[:10])
        end = date.fromisoformat(str(doc["validity_end"])[:10])
    except (KeyError, TypeError, ValueError):
        return None
    return float((end - start).days)
