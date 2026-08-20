"""Municipal alert subscriptions (ArangoDB persistence).

Responsibility: store the community-journalist subscriptions to
``published`` signals of a municipality, keyed by the 7-digit IBGE code,
and manage the subscription lifecycle (``pending`` → ``confirmed`` →
``unsubscribed``).

Token model: each subscription carries one opaque management token
(``secrets.token_urlsafe``), delivered once in the confirmation e-mail.
Only the SHA-256 hex digest is persisted; the raw token authorizes both
the confirmation and the (later) unsubscribe — the same link, sent at
confirmation, is the unsubscribe link. A permanent-per-subscription
token was chosen over single-use tokens because the only privilege it
grants is managing the subscription itself, and hash-only storage keeps
no recoverable secret at rest.

LGPD: the e-mail is personal data — the minimum necessary for the
service. Logs never carry the e-mail in clear, only the document key
(a SHA-256 of ``email:ibge_code``) and the IBGE code.

Dependencies: python-arango, capiba.db.arangodb
"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_COLLECTION = "subscriptions"


class SubscriptionStatus(StrEnum):
    """Lifecycle state of a municipal alert subscription."""

    PENDING = "pending"
    CONFIRMED = "confirmed"
    UNSUBSCRIBED = "unsubscribed"


def _hash(value: str) -> str:
    """SHA-256 hex digest of a value (keys and token digests)."""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def subscription_key(email: str, ibge_code: str) -> str:
    """Stable identity of a subscription: hash of the normalized pair."""
    return _hash(f"{email.strip().lower()}:{ibge_code.strip()}")


def ensure_subscriptions_collection(db: StandardDatabase) -> Any:
    """Creates the subscriptions collection lazily (offline-safe imports)."""
    if not db.has_collection(SUBSCRIPTIONS_COLLECTION):
        db.create_collection(SUBSCRIPTIONS_COLLECTION)
        logger.info("Collection created: %s", SUBSCRIPTIONS_COLLECTION)
    return db.collection(SUBSCRIPTIONS_COLLECTION)


def subscribe(
    db: StandardDatabase,
    email: str,
    ibge_code: str,
) -> tuple[dict[str, Any], str | None]:
    """Creates (or re-arms) a pending subscription for a municipality.

    Idempotent by the (email, ibge_code) pair: an already ``confirmed``
    subscription is returned untouched with no token — the caller then
    sends no e-mail and the API answers generically, so the endpoint does
    not leak whether an address is subscribed. A ``pending`` or
    ``unsubscribed`` entry rotates the token and goes back to ``pending``
    (re-confirmation is required after an unsubscribe).

    Returns:
        The subscription document and the raw management token to be
        delivered by e-mail (``None`` when no e-mail must be sent).
    """
    col = ensure_subscriptions_collection(db)
    key = subscription_key(email, ibge_code)
    now = datetime.now(UTC).isoformat()
    doc = col.get(key)

    if doc is not None and doc.get("status") == str(SubscriptionStatus.CONFIRMED):
        logger.info("Subscription %s already confirmed (ibge=%s)", key[:12], ibge_code)
        return dict(doc), None

    token = secrets.token_urlsafe(32)
    if doc is None:
        col.insert(
            {
                "_key": key,
                "email": email.strip().lower(),
                "ibge_code": ibge_code.strip(),
                "token_hash": _hash(token),
                "status": str(SubscriptionStatus.PENDING),
                "created_at": now,
                "updated_at": now,
                "confirmed_at": None,
                "unsubscribed_at": None,
            },
            silent=True,
        )
        logger.info("Subscription %s created (ibge=%s)", key[:12], ibge_code)
    else:
        col.update(
            {
                "_key": key,
                "token_hash": _hash(token),
                "status": str(SubscriptionStatus.PENDING),
                "updated_at": now,
                "confirmed_at": None,
                "unsubscribed_at": None,
            }
        )
        logger.info("Subscription %s re-armed (ibge=%s)", key[:12], ibge_code)
    return dict(col.get(key)), token


def _find_by_token(db: StandardDatabase, token: str) -> dict[str, Any] | None:
    """Finds a subscription by the raw management token (digest match)."""
    if not db.has_collection(SUBSCRIPTIONS_COLLECTION):
        return None
    rows = execute_aql(
        db,
        f"FOR s IN {SUBSCRIPTIONS_COLLECTION} FILTER s.token_hash == @digest RETURN s",
        {"digest": _hash(token)},
    )
    return rows[0] if rows else None


def confirm(db: StandardDatabase, token: str) -> dict[str, Any] | None:
    """Confirms a pending subscription (idempotent when already confirmed).

    Returns:
        The updated document, or None when the token matches nothing.
    """
    doc = _find_by_token(db, token)
    if doc is None:
        return None
    if doc.get("status") == str(SubscriptionStatus.CONFIRMED):
        return dict(doc)
    now = datetime.now(UTC).isoformat()
    col = ensure_subscriptions_collection(db)
    col.update(
        {
            "_key": doc["_key"],
            "status": str(SubscriptionStatus.CONFIRMED),
            "confirmed_at": now,
            "updated_at": now,
        }
    )
    logger.info("Subscription %s confirmed", str(doc["_key"])[:12])
    return dict(col.get(doc["_key"]))


def unsubscribe(db: StandardDatabase, token: str) -> dict[str, Any] | None:
    """Unsubscribes via the management token (idempotent).

    Returns:
        The updated document, or None when the token matches nothing.
    """
    doc = _find_by_token(db, token)
    if doc is None:
        return None
    if doc.get("status") == str(SubscriptionStatus.UNSUBSCRIBED):
        return dict(doc)
    now = datetime.now(UTC).isoformat()
    col = ensure_subscriptions_collection(db)
    col.update(
        {
            "_key": doc["_key"],
            "status": str(SubscriptionStatus.UNSUBSCRIBED),
            "unsubscribed_at": now,
            "updated_at": now,
        }
    )
    logger.info("Subscription %s unsubscribed", str(doc["_key"])[:12])
    return dict(col.get(doc["_key"]))


def confirmed_emails_by_ibge(db: StandardDatabase, ibge_code: str) -> list[str]:
    """E-mails of the confirmed subscribers of a municipality."""
    if not db.has_collection(SUBSCRIPTIONS_COLLECTION):
        return []
    rows = execute_aql(
        db,
        f"FOR s IN {SUBSCRIPTIONS_COLLECTION} "
        "FILTER s.status == @status AND s.ibge_code == @ibge RETURN s.email",
        {"status": str(SubscriptionStatus.CONFIRMED), "ibge": ibge_code.strip()},
    )
    return sorted(str(row) for row in rows)
