"""Municipal alert subscription endpoints.

Chunk: subscriptions
Responsibility: Public (no-SSO) subscription lifecycle for community
journalists: subscribe by the 7-digit IBGE municipality code, confirm
and unsubscribe via the opaque management token delivered by e-mail.

The POST answer is always generic — it never reveals whether an e-mail
address already has a subscription (enumeration-safe). The token is
permanent per subscription: it confirms and later unsubscribes; only
its SHA-256 digest is persisted.

Dependencies: fastapi, capiba.api.services, capiba.db.subscriptions,
capiba.ingestion.geography, capiba.notification.subscriptions
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from capiba.api import services
from capiba.api.schemas import SubscriptionRequest, SubscriptionStatusResponse
from capiba.db import subscriptions
from capiba.ingestion import geography
from capiba.notification import subscriptions as subscription_alerts

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/subscriptions", tags=["subscriptions"])

_HTTP_503_DETAIL = "ArangoDB database unavailable"
_SUBSCRIBE_DETAIL = "Se o endereço for válido, um e-mail de confirmação será enviado."


@router.post("", response_model=SubscriptionStatusResponse, status_code=201)
async def create_subscription(body: SubscriptionRequest) -> SubscriptionStatusResponse:
    """Registers a pending subscription and e-mails the confirmation link.

    Raises:
        HTTPException 422: If the IBGE code is not a known municipality.
        HTTPException 503: If the database is unavailable.
    """
    municipality = geography.lookup_by_ibge(body.ibge_code)
    if municipality is None:
        raise HTTPException(status_code=422, detail="Unknown IBGE municipality code")

    db = services.get_db()
    try:
        _, token = subscriptions.subscribe(db, body.email, body.ibge_code)
    except Exception as exc:
        logger.warning("Subscription failed (ibge=%s): %s", body.ibge_code, exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc

    # token is None when the pair is already confirmed — no e-mail, same
    # generic answer (the endpoint does not enumerate subscribers).
    if token is not None:
        subscription_alerts.send_confirmation(body.email, municipality, token)
    return SubscriptionStatusResponse(
        status=str(subscriptions.SubscriptionStatus.PENDING),
        detail=_SUBSCRIBE_DETAIL,
    )


@router.get("/confirm", response_model=SubscriptionStatusResponse)
async def confirm_subscription(token: str = Query(min_length=20)) -> SubscriptionStatusResponse:
    """Confirms a pending subscription via the management token.

    Raises:
        HTTPException 404: If the token matches no subscription.
        HTTPException 503: If the database is unavailable.
    """
    db = services.get_db()
    try:
        doc = subscriptions.confirm(db, token)
    except Exception as exc:
        logger.warning("Subscription confirmation failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="Invalid token")
    return SubscriptionStatusResponse(
        status=str(subscriptions.SubscriptionStatus.CONFIRMED),
        detail="Assinatura confirmada. Você receberá os alertas do município.",
    )


@router.get("/unsubscribe", response_model=SubscriptionStatusResponse)
async def unsubscribe_subscription(token: str = Query(min_length=20)) -> SubscriptionStatusResponse:
    """Unsubscribes via the management token.

    Raises:
        HTTPException 404: If the token matches no subscription.
        HTTPException 503: If the database is unavailable.
    """
    db = services.get_db()
    try:
        doc = subscriptions.unsubscribe(db, token)
    except Exception as exc:
        logger.warning("Unsubscribe failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    if doc is None:
        raise HTTPException(status_code=404, detail="Invalid token")
    return SubscriptionStatusResponse(
        status=str(subscriptions.SubscriptionStatus.UNSUBSCRIBED),
        detail="Assinatura cancelada. Você não receberá mais alertas.",
    )
