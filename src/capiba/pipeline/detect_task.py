"""Core of the fraud-signal detection task (post step ``detect``).

Responsibility: detection logic of ``task_detect`` — silver/graph loading,
the statistical signals (``detect_fraud_signals``) and the best-effort
screening blocks (sanctions exact/fuzzy, political connection, anomalous
geography, notice_clone over the bronze gazette texts, collusion over the
ArangoDB graph with ranked top-K emission), plus gold write, reproducible
evidence packages, editorial triage registration and internal alerts. Kept
in a dedicated module (same pattern as ``entity_tasks``/``document_tasks``/
``term_tasks``) so ``tasks.py`` keeps the task wrapper thin; every block is
best-effort and never fails the task.

Dependencies: capiba.config/db/detection/evidence/ingestion/notification,
capiba.pipeline.lake, capiba.pipeline.tasks (``_lake_run_date``).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date
from typing import Any

import pandas as pd

from capiba.config import (
    DETECTION_COLLUSION_MAX_PAIRS,
    DETECTION_COLLUSION_MIN_BUYERS,
    DETECTION_COLLUSION_MIN_WINS,
    DETECTION_COLLUSION_TOP_K,
    DETECTION_GEOGRAPHY_MAX_DISTANCE_KM,
    DETECTION_GEOGRAPHY_SCORE_REFERENCE,
    DETECTION_NOTICE_CLONE_ENCODER,
    DETECTION_NOTICE_CLONE_MIN_CHARS,
    DETECTION_NOTICE_CLONE_THRESHOLD,
    DETECTION_NOTICE_CLONE_WINDOW_DAYS,
    DETECTION_POLITICAL_MIN_DONATION,
    DETECTION_POLITICAL_MIN_SHARE,
    DETECTION_POLITICAL_SCORE_REFERENCE,
    TSE_ELECTION_YEAR,
)
from capiba.db.arangodb import get_capiba_db
from capiba.db.triage import register_signals
from capiba.detection.geography import anomalous_geography_signals
from capiba.detection.graphs import (
    collusion_eligibility,
    pair_buyers_from_eligibility_blocked,
    projected_pair_count,
    ranked_emission,
)
from capiba.detection.notice_clone import (
    Notice,
    default_encoder,
    notice_clone_signals,
    notice_id,
)
from capiba.detection.political import political_connection_signals
from capiba.detection.screening import sanctioned_supplier_signals
from capiba.detection.screening_fuzzy import sanctioned_name_match_signals
from capiba.detection.signals import (
    SignalType,
    anomalous_price,
    collusion_signals,
    is_non_competitive,
    single_bid_score,
)
from capiba.detection.statistical import (
    duration_outlier,
    hhi_index,
)
from capiba.evidence.packages import store_signal_packages
from capiba.evidence.storage import EvidenceStorage
from capiba.ingestion.gazette_segments import DEFAULT_MARKERS, segment_edition
from capiba.notification.alerts import notify_fraud_signals
from capiba.pipeline import lake
from capiba.pipeline.tasks import _lake_run_date

logger = logging.getLogger(__name__)


def detect_fraud_signals(contracts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Computes statistical fraud signals over the silver contracts.

    Signals (canonical vocabulary of ``capiba.detection.signals.SignalType``;
    score semantics: higher = more suspicious):

    - ``anomalous_price`` per supplier: composite of the Benford deviation
      (>= 10 positive amounts) and the IsolationForest anomaly rate over
      (log amount, duration) (>= 15 contracts); the score is the max of the
      eligible components, both preserved in ``details`` (null when a
      component is ineligible).
    - ``single_bid`` per supplier: rate of contracts in non-competitive
      modality (dispensa/inexigibilidade); emitted only when the rate is
      positive and the supplier has >= 3 contracts.
    - ``concentration`` per buyer: HHI of the supplier market shares;
      requires >= 3 contracts.
    - ``anomalous_duration`` per supplier: share of contracts whose validity
      duration is a pooled IQR outlier; emitted only when the share > 0.

    Args:
        contracts: Silver contract rows (nested ``buyer``/``supplier``).

    Returns:
        Signal rows (entity_type, entity_id, signal_type, score, details).
    """
    if not contracts:
        return []

    def _party_id(party: Any, key: str) -> Any:
        return party.get(key) if isinstance(party, dict) else None

    df = pd.DataFrame(contracts)
    df["buyer_id"] = df["buyer"].map(lambda b: _party_id(b, "siafi_code"))
    df["supplier_id"] = df["supplier"].map(
        lambda s: _party_id(s, "cnpj") or _party_id(s, "cpf")
    )
    df["amount_float"] = pd.to_numeric(df["amount"], errors="coerce")
    df["duration_days"] = (
        pd.to_datetime(df["validity_end"], errors="coerce")
        - pd.to_datetime(df["validity_start"], errors="coerce")
    ).dt.days
    if "modality" not in df.columns:
        df["modality"] = None

    signals: list[dict[str, Any]] = []

    for supplier_id, group in df.dropna(subset=["supplier_id"]).groupby("supplier_id"):
        composite = anomalous_price(group["amount_float"], group["duration_days"])
        if composite is not None:
            score, components = composite
            signals.append(
                {
                    "entity_type": "supplier",
                    "entity_id": str(supplier_id),
                    "signal_type": SignalType.ANOMALOUS_PRICE,
                    "score": score,
                    "details": json.dumps({**components, "contracts": int(len(group))}),
                }
            )

        non_competitive = int(group["modality"].map(is_non_competitive).sum())
        rate = single_bid_score(group["modality"])
        if rate > 0 and len(group) >= 3:
            signals.append(
                {
                    "entity_type": "supplier",
                    "entity_id": str(supplier_id),
                    "signal_type": SignalType.SINGLE_BID,
                    "score": rate,
                    "details": json.dumps(
                        {
                            "contracts": int(len(group)),
                            "non_competitive": non_competitive,
                        }
                    ),
                }
            )

    hhi_df = df[["buyer_id", "supplier_id", "amount_float"]].rename(
        columns={"amount_float": "amount"}
    )
    for buyer_id, group in df.dropna(subset=["buyer_id"]).groupby("buyer_id"):
        if len(group) >= 3:
            hhi = hhi_index(str(buyer_id), hhi_df)
            signals.append(
                {
                    "entity_type": "buyer",
                    "entity_id": str(buyer_id),
                    "signal_type": SignalType.CONCENTRATION,
                    "score": hhi,
                    "details": json.dumps(
                        {
                            "contracts": len(group),
                            "suppliers": int(group["supplier_id"].nunique()),
                        }
                    ),
                }
            )

    with_durations = df.dropna(subset=["duration_days"])
    if len(with_durations) >= 3:
        outliers = duration_outlier(with_durations)
        flagged = with_durations[outliers]
        for supplier_id, group in with_durations.dropna(subset=["supplier_id"]).groupby(
            "supplier_id"
        ):
            share = len(flagged[flagged["supplier_id"] == supplier_id]) / len(group)
            if share > 0:
                signals.append(
                    {
                        "entity_type": "supplier",
                        "entity_id": str(supplier_id),
                        "signal_type": SignalType.ANOMALOUS_DURATION,
                        "score": round(float(share), 4),
                        "details": json.dumps({"contracts": len(group)}),
                    }
                )

    logger.info("Fraud signals computed: %d", len(signals))
    return signals


# Bronze gazette text file name (``text_file_name``):
# ``<territory_id>-<date>-<sha256(url)[:12]>.txt``.
_BRONZE_GAZETTE_FILE = re.compile(
    r"^(?P<territory>\d+)-(?P<date>\d{4}-\d{2}-\d{2})-(?P<digest>[0-9a-f]{12})\.txt$"
)


def notice_clone_bronze_signals() -> list[dict[str, Any]]:
    """Computes ``notice_clone`` signals over the bronze gazette texts.

    Producer of the PR-D-10 § 8 (step 5) integration, enabled by the D-10b
    verdict (``docs/results/R-D-10b.md``): reads the accumulated
    ``querido_diario`` texts (all run-date partitions), segments each
    edition (``gazette_segments``) and emits the signal with the
    pre-registered gates (``DETECTION_NOTICE_CLONE_*``). The "new" notices
    of the run are the ones published on the latest gazette date of the
    corpus (``reference_date`` semantics of ``notice_clone_signals``) —
    pairs over older dates were emitted by previous runs and deduplicated
    by the stable triage key. The edition component of the notice id is
    the file digest (unique and deterministic per gazette).

    Returns:
        Signal rows (empty when the corpus has no candidate pair).
    """
    notices: list[Notice] = []
    for key in lake.list_all_bronze_files("querido_diario"):
        match = _BRONZE_GAZETTE_FILE.match(key.rsplit("/", 1)[-1])
        if match is None:
            continue
        territory = match.group("territory")
        gazette_date = date.fromisoformat(match.group("date"))
        text = lake.read_bronze_file(key).decode("utf-8")
        for index, segment in enumerate(segment_edition(text, DEFAULT_MARKERS)):
            notices.append(
                Notice(
                    notice_id=notice_id(
                        territory, gazette_date, match.group("digest"), index
                    ),
                    territory_id=territory,
                    date=gazette_date,
                    text=segment,
                )
            )
    if not notices:
        return []
    reference_date = max(notice.date for notice in notices)
    return notice_clone_signals(
        notices,
        encode=default_encoder(DETECTION_NOTICE_CLONE_ENCODER),
        threshold=DETECTION_NOTICE_CLONE_THRESHOLD,
        window_days=DETECTION_NOTICE_CLONE_WINDOW_DAYS,
        min_chars=DETECTION_NOTICE_CLONE_MIN_CHARS,
        reference_date=reference_date,
    )


def run_detection(**context: Any) -> dict[str, Any]:
    """Core of ``task_detect``: compute fraud signals over the silver contracts.

    Args:
        context: Airflow context.

    Returns:
        Detection summary (number of signals written).
    """
    run_date = _lake_run_date(context)

    try:
        contracts = lake.read_silver_contracts()
    except Exception as e:
        logger.warning("Failed to read the silver contracts table: %s", e)
        contracts = []

    signals = detect_fraud_signals(contracts)

    # Best-effort: sanction screening never fails the task (the silver
    # sanctions table may not exist yet).
    try:
        sanctions = [
            row for batch in lake.read_silver_entities("sanctions") for row in batch
        ]
        signals.extend(sanctioned_supplier_signals(contracts, sanctions))
        signals.extend(sanctioned_name_match_signals(contracts, sanctions))
    except Exception as e:
        logger.warning("Sanction screening unavailable (silver sanctions): %s", e)

    # Best-effort: political connection screening (PR-D-08) never fails
    # the task (the silver TSE tables may not exist yet). The mandate window
    # derives from the ingested election year.
    try:
        donations = [
            row
            for batch in lake.read_silver_entities("campaign_donations")
            for row in batch
        ]
        candidacies = [
            row for batch in lake.read_silver_entities("candidacies") for row in batch
        ]
        signals.extend(
            political_connection_signals(
                donations,
                contracts,
                candidacies,
                min_donation_brl=DETECTION_POLITICAL_MIN_DONATION,
                min_supplier_share=DETECTION_POLITICAL_MIN_SHARE,
                score_share_reference=DETECTION_POLITICAL_SCORE_REFERENCE,
                mandate_start=date(TSE_ELECTION_YEAR + 1, 1, 1),
                mandate_end=date(TSE_ELECTION_YEAR + 4, 12, 31),
            )
        )
    except Exception as e:
        logger.warning("Political connection detection unavailable (silver tse): %s", e)

    # Best-effort: anomalous geography screening (PR-D-09) never fails
    # the task (the silver RFB/reference tables may not exist yet). The
    # municipality reference is loaded idempotently first, so a fresh
    # cluster needs no extra DAG run. The establishments read is
    # selective (supplier CNPJs of the contracts only) — the full RFB
    # table has tens of millions of rows and OOMKills the pod.
    try:
        lake.load_municipalities(run_date=run_date)
        supplier_cnpjs = {
            re.sub(r"\D", "", str((contract.get("supplier") or {}).get("cnpj") or ""))
            for contract in contracts
        }
        establishments = lake.read_establishments_for_cnpjs(supplier_cnpjs)
        rfb_municipalities = [
            row
            for batch in lake.read_silver_entities("rfb_municipalities")
            for row in batch
        ]
        municipalities = [
            row for batch in lake.read_silver_entities("municipalities") for row in batch
        ]
        signals.extend(
            anomalous_geography_signals(
                contracts,
                establishments,
                rfb_municipalities,
                municipalities,
                max_distance_km=DETECTION_GEOGRAPHY_MAX_DISTANCE_KM,
                score_distance_reference=DETECTION_GEOGRAPHY_SCORE_REFERENCE,
            )
        )
    except Exception as e:
        logger.warning("Geography detection unavailable (silver geo chain): %s", e)

    # Best-effort: notice_clone screening (PR-D-10 § 8, step 5 — enabled
    # by the D-10b verdict) never fails the task (the bronze gazette
    # texts or the sentence encoder may be unavailable).
    try:
        signals.extend(notice_clone_bronze_signals())
    except Exception as e:
        logger.warning("Notice clone detection unavailable (bronze QD texts): %s", e)

    # Best-effort: graph signals never fail the task (ArangoDB may be down).
    graph_snapshot: dict[str, Any] | None = None
    collusion_projected: int | None = None
    try:
        db = get_capiba_db()
        eligibility = collusion_eligibility(db, min_wins=DETECTION_COLLUSION_MIN_WINS)
        graph_snapshot = {
            "rows": eligibility,
            "min_wins": DETECTION_COLLUSION_MIN_WINS,
            "min_buyers": DETECTION_COLLUSION_MIN_BUYERS,
        }
        # Memory guard: the pair derivation is combinatorial per buyer and
        # explodes on real volume (9,6M pairs on 2026-08-21, OOMKilled the
        # pod); over the budget the eligibility snapshot is still stored as
        # evidence, but no signals are derived.
        collusion_projected = projected_pair_count(eligibility)
        if collusion_projected > DETECTION_COLLUSION_MAX_PAIRS:
            logger.warning(
                "Collusion pair derivation skipped: %d projected pairs over "
                "the budget %d (snapshot kept for evidence)",
                collusion_projected,
                DETECTION_COLLUSION_MAX_PAIRS,
            )
        else:
            # PR-D-03d (promoted by human decision of 2026-08-21): blocked
            # derivation (exact recall, D-03c) + declared top-K ranked
            # emission; the descriptor is recorded in the evidence package.
            pair_buyers = pair_buyers_from_eligibility_blocked(
                eligibility, DETECTION_COLLUSION_MIN_BUYERS
            )
            emission = ranked_emission(
                pair_buyers, eligibility, DETECTION_COLLUSION_TOP_K
            )
            graph_snapshot["top_k"] = emission["top_k"]
            graph_snapshot["qualified_count"] = emission["qualified_count"]
            emitted = emission["emission"]
            signals.extend(
                collusion_signals(
                    [set(entry["pair"]) for entry in emitted],
                    DETECTION_COLLUSION_MIN_WINS,
                    DETECTION_COLLUSION_MIN_BUYERS,
                    {tuple(entry["pair"]): entry["buyers"] for entry in emitted},
                )
            )
        # Editorial triage queue: new signals enter as pending_review.
        register_signals(db, signals)
    except Exception as e:
        logger.warning("Collusion detection unavailable (ArangoDB): %s", e)

    try:
        if signals:
            lake.write_fraud_signals(signals, run_date=run_date)
    except Exception as e:
        logger.warning("Failed to write fraud signals to the gold layer: %s", e)

    # Best-effort: reproducible evidence packages never fail the task.
    try:
        if signals:
            store_signal_packages(
                EvidenceStorage(),
                signals,
                contracts,
                run_date,
                graph_snapshot=graph_snapshot,
            )
    except Exception as e:
        logger.warning("Failed to store signal evidence packages (MinIO): %s", e)

    # Best-effort: alerts never fail the task.
    notify_fraud_signals(signals, run_date)

    summary: dict[str, Any] = {"signals": len(signals)}
    if collusion_projected is not None:
        summary["collusion_projected_pairs"] = collusion_projected
    return summary
