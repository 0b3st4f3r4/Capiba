"""Canonical fraud signals shared by the batch pipeline and the API.

Responsibility: single source of truth for the signal vocabulary
(``SignalType``) and the threshold-free signal computations, consumed by the
pipeline post step ``detect`` (``capiba.pipeline.tasks.detect_fraud_signals``,
which emits raw scores per entity into the gold ``fraud_signals`` table) and
by the on-demand API service layer (``capiba.api.services``, which applies
its own emission thresholds and evidence messages on top).

Dependencies: capiba.detection.statistical, capiba.detection.ml_models
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from enum import StrEnum
from typing import Any

import numpy as np
import pandas as pd

from capiba.detection.ml_models import train_if
from capiba.detection.statistical import benford_score, duration_outlier

# Minimum sample sizes for signal eligibility
MIN_BENFORD_AMOUNTS = 10
MIN_ISOLATION_FOREST_CONTRACTS = 15


class SignalType(StrEnum):
    """Detection signal types (canonical vocabulary)."""

    SINGLE_BID = "single_bid"
    CONCENTRATION = "concentration"
    COLLUSION_NETWORK = "collusion_network"
    ANOMALOUS_PRICE = "anomalous_price"
    SEMANTIC_GAP = "semantic_gap"
    ANOMALOUS_DURATION = "anomalous_duration"
    SANCTIONED_SUPPLIER = "sanctioned_supplier"
    SANCTIONED_NAME_MATCH = "sanctioned_name_match"
    POLITICAL_CONNECTION = "political_connection"
    ANOMALOUS_GEOGRAPHY = "anomalous_geography"
    PEP_SUPPLIER_MATCH = "pep_supplier_match"
    NOTICE_CLONE = "notice_clone"


def is_non_competitive(modality: Any) -> bool:
    """Checks whether the modality is non-competitive (dispensa/inexigibilidade)."""
    text = str(modality or "").lower()
    return "dispensa" in text or "inexigibilidade" in text


def single_bid_score(modalities: Iterable[Any]) -> float:
    """Rate of non-competitive modalities, as a single-bid proxy.

    Persisted contracts do not store 'num_participants';
    dispensa/inexigibilidade are structurally dispute-free processes.

    Args:
        modalities: Contract modality labels of one entity group.

    Returns:
        Non-competitive rate (0-1, rounded to 4 decimals); 0.0 when empty.
    """
    values = list(modalities)
    if not values:
        return 0.0
    rate = sum(1 for modality in values if is_non_competitive(modality)) / len(values)
    return round(rate, 4)


def benford_deviation(amounts: Iterable[float]) -> float | None:
    """Deviation from Benford's Law (``1 - conformance``) over the amounts.

    Args:
        amounts: Contract amounts of one entity group (nulls/non-positives
            are filtered out, as in ``benford_score``).

    Returns:
        Deviation (0-1, rounded to 4 decimals), or None when the group has
        fewer than MIN_BENFORD_AMOUNTS positive amounts or the conformance
        score is NaN.
    """
    series = pd.Series(list(amounts), dtype=float)
    positives = series[series > 0].dropna()
    if len(positives) < MIN_BENFORD_AMOUNTS:
        return None
    conformance = benford_score(positives)
    if pd.isna(conformance):
        return None
    return round(1.0 - conformance, 4)


def isolation_forest_rate(
    amounts: Iterable[float],
    durations: Iterable[float | None],
) -> float | None:
    """IsolationForest anomaly rate over (log1p amount, duration days).

    ``train_if`` fixes ``random_state=42``, so the rate is deterministic for
    a given input. Null amounts/durations are imputed to 0 (log1p 0 = 0).

    Args:
        amounts: Contract amounts of one entity group (one row per contract).
        durations: Contract validity durations in days (same length).

    Returns:
        Anomaly rate (0-1, rounded to 4 decimals), or None when the group
        has fewer than MIN_ISOLATION_FOREST_CONTRACTS contracts.
    """
    amount_values = pd.Series(list(amounts), dtype=float)
    if len(amount_values) < MIN_ISOLATION_FOREST_CONTRACTS:
        return None
    duration_values = pd.Series(list(durations), dtype=float)
    features = pd.DataFrame(
        {
            "log_amount": np.log1p(amount_values.fillna(0).clip(lower=0)),
            "duration_days": duration_values.fillna(0.0),
        }
    )
    model = train_if(features)
    return round(float((model.predict(features) == -1).mean()), 4)


def anomalous_price(
    amounts: Iterable[float],
    durations: Iterable[float | None],
) -> tuple[float, dict[str, float | None]] | None:
    """Composite price signal: max of the Benford and IsolationForest components.

    Mirrors the semantics of the API's anomalous_price signal, without the
    emission thresholds: the score is the maximum of the eligible
    components and both components are preserved (None when ineligible).

    Args:
        amounts: Contract amounts of one entity group.
        durations: Contract validity durations in days (same length).

    Returns:
        (score, components) with keys ``benford_deviation`` and
        ``isolation_forest_rate``, or None when no component is eligible.
    """
    benford = benford_deviation(amounts)
    forest = isolation_forest_rate(amounts, durations)
    available = [value for value in (benford, forest) if value is not None]
    if not available:
        return None
    return max(available), {
        "benford_deviation": benford,
        "isolation_forest_rate": forest,
    }


def collusion_signals(
    pairs: list[set[str]],
    min_wins: int,
    min_buyers: int = 1,
    buyers_by_pair: dict[tuple[str, str], list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Converts collusion pairs (``detect_collusion``) into signal rows.

    Binary score (1.0) — a calibration placeholder validated by battery D-02;
    battery D-03 measured the real-volume semantics and D-03b refined the
    pair by co-occurrence across distinct buyers (``min_buyers``). One signal
    per pair, addressed to the supplier pair itself: ``entity_id`` is the two
    CNPJs sorted and joined by ``+`` (deterministic), and the raw pair is
    preserved in ``details`` together with the calibration thresholds and,
    when provided, the sorted list of buyers where the pair co-occurs.

    Args:
        pairs: Supplier pairs (sets of two CNPJs) from ``detect_collusion``.
        min_wins: Eligibility threshold used to produce the pairs (metadata).
        min_buyers: Minimum distinct buyers per pair (metadata; 1 = the
            single-buyer semantics of D-03).
        buyers_by_pair: Optional mapping of sorted pair tuple to the sorted
            buyer ids where the pair co-occurs (PR-D-03b annotation).

    Returns:
        Signal rows (entity_type, entity_id, signal_type, score, details).
    """
    signals: list[dict[str, Any]] = []
    for pair in pairs:
        suppliers = sorted(pair)
        details: dict[str, Any] = {
            "min_wins": min_wins,
            "min_buyers": min_buyers,
            "suppliers": suppliers,
        }
        if buyers_by_pair is not None:
            details["buyers"] = buyers_by_pair.get((suppliers[0], suppliers[1]), [])
        signals.append(
            {
                "entity_type": "supplier",
                "entity_id": "+".join(suppliers),
                "signal_type": SignalType.COLLUSION_NETWORK,
                "score": 1.0,
                "details": json.dumps(details),
            }
        )
    return signals


def duration_outlier_share(
    durations: Iterable[float | None],
    minimum: int,
) -> float | None:
    """Share of IQR duration outliers within one entity group.

    Args:
        durations: Contract validity durations in days (nulls dropped).
        minimum: Minimum number of valid durations required.

    Returns:
        Outlier share (0-1, unrounded), or None below the minimum.
    """
    values = [d for d in durations if d is not None and not pd.isna(d)]
    if len(values) < minimum:
        return None
    outliers = duration_outlier(pd.DataFrame({"duration_days": values}))
    return float(outliers.mean())
