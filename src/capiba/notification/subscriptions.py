"""Municipal alert subscriptions — confirmation and broadcast.

Responsibility: deliver the confirmation e-mail of a new subscription
and, when the editorial triage publishes a signal, notify the
confirmed subscribers of the signal's municipality by e-mail, linking
the reproducible evidence package (``GET /v1/signals/{key}/evidence``).

Signal → municipality match: the published signal resolves its buyer
municipality to a 7-digit IBGE code via the vendored geographic
reference (``capiba.ingestion.geography``). The match tries, in order:

1. ``city``/``uf`` (or ``municipality``/``uf``) fields of the signal
   ``details`` JSON;
2. the buyer (city, UF) pairs of the entity's contracts (ArangoDB
   ``contracts`` collection) — the most frequent pair wins.

Signals with no resolvable municipality do not dispatch; they are
counted and logged (never raise — the broadcast is best-effort and
must never break the triage transition).

LGPD: one e-mail per subscriber (never a shared recipient list), logs
carry no e-mail in clear.

Dependencies: capiba.config, capiba.db.subscriptions,
capiba.ingestion.geography, capiba.notification.dispatcher
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import Counter
from collections.abc import Callable
from typing import Any
from urllib.parse import quote

from arango.database import StandardDatabase

from capiba.config import PUBLIC_API_URL
from capiba.db import subscriptions as db_subscriptions
from capiba.db.arangodb import execute_aql
from capiba.db.triage import signal_key
from capiba.ingestion import geography
from capiba.notification.dispatcher import (
    NotificationAlert,
    NotificationChannel,
    NotificationDispatcher,
    Priority,
)

logger = logging.getLogger(__name__)


def _dispatch(alert: NotificationAlert) -> bool:
    """Runs the async dispatcher synchronously (never raises)."""
    try:
        return asyncio.run(NotificationDispatcher().dispatch(alert))
    except Exception as e:
        logger.warning("Subscription dispatch failed: %s", e)
        return False


def _details_payload(details: Any) -> dict[str, Any]:
    """Parses the signal details (JSON string or mapping; else empty)."""
    if isinstance(details, dict):
        return details
    try:
        parsed = json.loads(str(details or ""))
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _entity_ids(signal: dict[str, Any]) -> list[str]:
    """Entity ids of the signal (collusion pairs are joined by ``+``)."""
    return [part for part in str(signal.get("entity_id") or "").split("+") if part]


def buyer_pairs_from_contracts(
    signal: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> list[tuple[str, str]]:
    """Buyer (city, UF) pairs of the contracts related to the signal entity.

    ``buyer`` entities match by ``buyer.siafi_code``; ``supplier``
    entities by ``supplier.cnpj``/``supplier.cpf`` (any of the ``+``-joined
    ids). Contracts without buyer city/UF are ignored.
    """
    ids = set(_entity_ids(signal))
    pairs: list[tuple[str, str]] = []
    for contract in contracts:
        buyer = contract.get("buyer") or {}
        supplier = contract.get("supplier") or {}
        if str(signal.get("entity_type")) == "buyer":
            related = str(buyer.get("siafi_code") or "") in ids
        else:
            related = bool(ids & {str(supplier.get("cnpj") or ""), str(supplier.get("cpf") or "")})
        city, uf = buyer.get("city"), buyer.get("uf")
        if related and city and uf:
            pairs.append((str(city), str(uf)))
    return pairs


def resolve_signal_ibge(
    signal: dict[str, Any],
    contracts: list[dict[str, Any]] | None = None,
    lookup: Callable[[Any, Any], geography.Municipality | None] = geography.lookup_by_name,
) -> str | None:
    """Resolves the 7-digit IBGE code of the published signal's municipality.

    Tries the ``details`` payload first (``city``/``uf`` or
    ``municipality``/``uf``), then the most frequent buyer (city, UF)
    pair of the entity's contracts.

    Args:
        signal: Triage-shaped signal (entity_type, entity_id, details).
        contracts: Silver-shaped contract rows of the entity (optional).
        lookup: (name, uf) -> Municipality resolver (injectable for tests).

    Returns:
        The IBGE code, or None when the municipality is not resolvable.
    """
    payload = _details_payload(signal.get("details"))
    city = payload.get("city") or payload.get("municipality")
    uf = payload.get("uf")
    if city and uf:
        municipality = lookup(city, uf)
        if municipality is not None:
            return municipality.ibge_code

    pairs = buyer_pairs_from_contracts(signal, contracts or [])
    for (pair_city, pair_uf), _ in Counter(pairs).most_common():
        municipality = lookup(pair_city, pair_uf)
        if municipality is not None:
            return municipality.ibge_code
    return None


def fetch_signal_contracts(db: StandardDatabase, signal: dict[str, Any]) -> list[dict[str, Any]]:
    """Fetches the entity's contracts from ArangoDB for the geo match."""
    ids = _entity_ids(signal)
    if not ids:
        return []
    if str(signal.get("entity_type")) == "buyer":
        query = (
            "FOR c IN contracts FILTER c.buyer.siafi_code IN @ids "
            "RETURN {buyer: c.buyer}"
        )
    else:
        query = (
            "FOR c IN contracts "
            "FILTER c.supplier.cnpj IN @ids OR c.supplier.cpf IN @ids "
            "RETURN {buyer: c.buyer, supplier: c.supplier}"
        )
    return execute_aql(db, query, {"ids": ids})


def send_confirmation(email: str, municipality: geography.Municipality, token: str) -> bool:
    """Sends the confirmation e-mail with the management token links.

    The same opaque token confirms and (later) unsubscribes: the link is
    kept by the subscriber and is the only copy of the token (only its
    digest is persisted).
    """
    base = PUBLIC_API_URL.rstrip("/")
    encoded = quote(token, safe="")
    alert = NotificationAlert(
        title=f"Confirme sua assinatura: alertas de {municipality.name}",
        message=(
            "Recebemos uma inscrição de alertas de sinais publicados do "
            f"município de {municipality.name} — {municipality.uf} "
            f"(IBGE {municipality.ibge_code}). Confirme no link abaixo; "
            "guarde o link de cancelamento."
        ),
        priority=Priority.MEDIUM,
        channel=NotificationChannel.EMAIL,
        recipients=[email],
        metadata={
            "template": "subscription",
            "municipality": municipality.name,
            "uf": municipality.uf,
            "confirm_url": f"{base}/v1/subscriptions/confirm?token={encoded}",
            "unsubscribe_url": f"{base}/v1/subscriptions/unsubscribe?token={encoded}",
        },
    )
    return _dispatch(alert)


def notify_published_signal(db: StandardDatabase, signal: dict[str, Any]) -> int:
    """Notifies the confirmed subscribers of a published signal's municipality.

    Best-effort: resolution, persistence and dispatch failures are logged
    and counted, never raised — the triage transition must not break.

    Returns:
        Number of subscriber e-mails sent successfully.
    """
    try:
        return _notify_published_signal(db, signal)
    except Exception as e:
        logger.warning("Published-signal broadcast failed: %s", e)
        return 0


def _notify_published_signal(db: StandardDatabase, signal: dict[str, Any]) -> int:
    """Builds and dispatches the per-subscriber alerts (see above)."""
    key = signal_key(
        str(signal.get("entity_type")),
        str(signal.get("entity_id")),
        str(signal.get("signal_type")),
    )
    ibge_code = resolve_signal_ibge(signal)
    if ibge_code is None:
        ibge_code = resolve_signal_ibge(signal, fetch_signal_contracts(db, signal))
    if ibge_code is None:
        logger.warning("Published signal %s has no resolvable municipality; skipped", key)
        return 0

    emails = db_subscriptions.confirmed_emails_by_ibge(db, ibge_code)
    if not emails:
        logger.debug("No confirmed subscribers for ibge=%s (signal %s)", ibge_code, key)
        return 0

    municipality = geography.lookup_by_ibge(ibge_code)
    name = municipality.name if municipality else ibge_code
    uf = municipality.uf if municipality else None
    evidence_url = f"{PUBLIC_API_URL.rstrip('/')}/v1/signals/{quote(key, safe='')}/evidence"

    sent = 0
    for email in emails:
        alert = NotificationAlert(
            title=f"Novo sinal publicado em {name}: {signal.get('signal_type')}",
            message=(
                "Um sinal de fraude foi verificado e publicado pela triagem "
                f"editorial no município de {name}. O pacote de evidências "
                "reproduzível está no link abaixo. Para cancelar a assinatura, "
                "use o link de cancelamento recebido no e-mail de confirmação."
            ),
            priority=Priority.HIGH,
            channel=NotificationChannel.EMAIL,
            recipients=[email],
            metadata={
                "template": "subscription",
                "municipality": name,
                "uf": uf,
                "signal_type": str(signal.get("signal_type")),
                "score": signal.get("score"),
                "entity": f"{signal.get('entity_type')}:{signal.get('entity_id')}",
                "evidence_url": evidence_url,
            },
        )
        sent += 1 if _dispatch(alert) else 0
    logger.info(
        "Published signal %s broadcast to %d/%d subscriber(s) of ibge=%s",
        key,
        sent,
        len(emails),
        ibge_code,
    )
    return sent
