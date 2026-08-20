"""Editorial triage endpoints.

Chunk: triage
Responsibility: List signals under editorial review, apply human
transitions (confirmed/rejected/published — rejection requires a
reason) and expose the per-operator precision report derived from
the human labels.

Dependencies: fastapi, capiba.api.services, capiba.db.triage
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from capiba.api import services
from capiba.api.schemas import (
    SignalReview,
    TriageMetrics,
    TriageRequest,
    TriageStatus,
)
from capiba.db import triage
from capiba.detection.signals import SignalType
from capiba.notification import subscriptions as subscription_alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/triage", tags=["triage"])

_HTTP_503_DETAIL = "ArangoDB database unavailable"


def _public(doc: dict[str, Any]) -> SignalReview:
    """Maps a triage document to the API schema (``_key`` → ``key``)."""
    return SignalReview(**{**doc, "key": doc["_key"]})


@router.get("/signals", response_model=list[SignalReview])
async def list_triage_signals(
    status: TriageStatus | None = None,
    signal_type: SignalType | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[SignalReview]:
    """Lists signals under editorial review, newest first.

    Raises:
        HTTPException 503: If the database is unavailable.
    """
    db = services.get_db()
    try:
        docs = triage.list_reviews(
            db,
            status=status,
            signal_type=str(signal_type) if signal_type else None,
            limit=limit,
        )
    except Exception as exc:
        logger.warning("Triage listing failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return [_public(doc) for doc in docs]


@router.post("/signals/{key}/review", response_model=SignalReview)
async def review_signal(key: str, body: TriageRequest) -> SignalReview:
    """Applies an editorial transition to a signal.

    A reviewer is required on every transition; rejecting requires a
    reason; ``published`` is terminal.

    Raises:
        HTTPException 404: If no triage entry matches the key.
        HTTPException 422: On invalid transition or missing
            reviewer/reason.
        HTTPException 503: If the database is unavailable.
    """
    db = services.get_db()
    try:
        doc = triage.apply_review(db, key, body.status, body.reviewer, body.reason)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Signal not found") from exc
    except triage.TriageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        logger.warning("Triage review failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    if doc.get("status") == str(triage.TriageStatus.PUBLISHED):
        # Notify the confirmed subscribers of the signal's municipality.
        # Best-effort — never raises, never breaks the transition.
        subscription_alerts.notify_published_signal(db, doc)
    return _public(doc)


@router.get("/metrics", response_model=list[TriageMetrics])
async def triage_metrics() -> list[TriageMetrics]:
    """Per-operator precision report derived from the human labels.

    Raises:
        HTTPException 503: If the database is unavailable.
    """
    db = services.get_db()
    try:
        report = triage.precision_report(db)
    except Exception as exc:
        logger.warning("Triage metrics failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return [TriageMetrics(**row) for row in report]
