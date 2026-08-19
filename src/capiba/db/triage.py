"""Editorial triage of fraud signals (ArangoDB persistence).

Responsibility: store and update the editorial state of each detected
signal (``pending_review`` → ``confirmed``/``rejected``/``published``),
generating the human label dataset that feeds the per-operator
precision report and, later, the supervised ML training.

Each signal is keyed by its natural identity
``{entity_type}:{entity_id}:{signal_type}`` so the editorial state
survives the daily recomputation of the gold ``fraud_signals`` table;
re-registration only refreshes the score/details snapshot.

Dependencies: python-arango, capiba.db.arangodb
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from arango.database import StandardDatabase

from capiba.db.arangodb import execute_aql

logger = logging.getLogger(__name__)

TRIAGE_COLLECTION = "signal_reviews"


class TriageStatus(StrEnum):
    """Editorial state of a detected signal."""

    PENDING_REVIEW = "pending_review"
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    PUBLISHED = "published"


class TriageError(ValueError):
    """Invalid triage operation (bad transition or missing field)."""


_ALLOWED_TRANSITIONS: dict[TriageStatus, set[TriageStatus]] = {
    TriageStatus.PENDING_REVIEW: {TriageStatus.CONFIRMED, TriageStatus.REJECTED},
    TriageStatus.CONFIRMED: {TriageStatus.PUBLISHED, TriageStatus.REJECTED},
    TriageStatus.REJECTED: {TriageStatus.CONFIRMED},
    TriageStatus.PUBLISHED: set(),
}


def signal_key(entity_type: str, entity_id: str, signal_type: str) -> str:
    """Stable identity of a signal across recomputations."""
    return f"{entity_type}:{entity_id}:{signal_type}"


def ensure_triage_collection(db: StandardDatabase) -> Any:
    """Creates the triage collection lazily (offline-safe imports)."""
    if not db.has_collection(TRIAGE_COLLECTION):
        db.create_collection(TRIAGE_COLLECTION)
        logger.info("Collection created: %s", TRIAGE_COLLECTION)
    return db.collection(TRIAGE_COLLECTION)


def register_signals(db: StandardDatabase, signals: list[dict[str, Any]]) -> int:
    """Registers computed signals as ``pending_review`` (insert-if-absent).

    Existing entries keep their editorial state; only the score/details
    snapshot and ``last_seen`` are refreshed.

    Returns:
        Number of newly created triage entries.
    """
    col = ensure_triage_collection(db)
    now = datetime.now(UTC).isoformat()
    registered = 0
    for signal in signals:
        key = signal_key(
            str(signal["entity_type"]),
            str(signal["entity_id"]),
            str(signal["signal_type"]),
        )
        if col.get(key) is None:
            col.insert(
                {
                    "_key": key,
                    "entity_type": str(signal["entity_type"]),
                    "entity_id": str(signal["entity_id"]),
                    "signal_type": str(signal["signal_type"]),
                    "score": signal.get("score"),
                    "details": signal.get("details"),
                    "status": str(TriageStatus.PENDING_REVIEW),
                    "first_seen": now,
                    "last_seen": now,
                    "history": [],
                },
                silent=True,
            )
            registered += 1
        else:
            col.update(
                {
                    "_key": key,
                    "score": signal.get("score"),
                    "details": signal.get("details"),
                    "last_seen": now,
                }
            )
    logger.info("Triage: %d new signals registered (%d total)", registered, len(signals))
    return registered


def _all_reviews(db: StandardDatabase) -> list[dict[str, Any]]:
    """Reads every triage document (empty when the collection is missing)."""
    if not db.has_collection(TRIAGE_COLLECTION):
        return []
    return execute_aql(db, f"FOR r IN {TRIAGE_COLLECTION} RETURN r")


def list_reviews(
    db: StandardDatabase,
    status: TriageStatus | None = None,
    signal_type: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Lists triage entries, newest first, with optional filters."""
    docs = _all_reviews(db)
    if status is not None:
        docs = [d for d in docs if d.get("status") == str(status)]
    if signal_type is not None:
        docs = [d for d in docs if d.get("signal_type") == str(signal_type)]
    docs.sort(key=lambda d: str(d.get("last_seen") or ""), reverse=True)
    return docs[:limit]


def apply_review(
    db: StandardDatabase,
    key: str,
    status: TriageStatus,
    reviewer: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Applies an editorial transition to a triage entry.

    A reviewer is required on every transition; rejecting requires a
    reason. ``published`` is terminal.

    Raises:
        KeyError: If no triage entry matches the key.
        TriageError: On invalid transition or missing reviewer/reason.
    """
    if not reviewer or not reviewer.strip():
        raise TriageError("reviewer is required for every triage transition")
    if status == TriageStatus.REJECTED and not (reason and reason.strip()):
        raise TriageError("reason is required when rejecting a signal")

    col = ensure_triage_collection(db)
    doc = col.get(key)
    if doc is None:
        raise KeyError(key)

    current = TriageStatus(doc["status"])
    if status not in _ALLOWED_TRANSITIONS[current]:
        raise TriageError(f"invalid transition: {current} -> {status}")

    now = datetime.now(UTC).isoformat()
    entry = {
        "status": str(status),
        "reviewer": reviewer,
        "reason": reason,
        "reviewed_at": now,
    }
    col.update(
        {
            "_key": key,
            "status": str(status),
            "reviewed_by": reviewer,
            "reviewed_at": now,
            "reason": reason,
            "history": [*doc.get("history", []), entry],
        }
    )
    logger.info("Triage: %s -> %s (reviewer=%s)", key, status, reviewer)
    return dict(col.get(key))


def precision_report(db: StandardDatabase) -> list[dict[str, Any]]:
    """Per-operator precision derived from the human labels.

    Precision is confirmed / (confirmed + rejected); ``None`` when the
    operator has no reviewed labels yet.
    """
    by_type: dict[str, dict[str, int]] = {}
    for doc in _all_reviews(db):
        counts = by_type.setdefault(str(doc.get("signal_type")), {})
        status = str(doc.get("status"))
        counts[status] = counts.get(status, 0) + 1

    report = []
    for signal_type, counts in sorted(by_type.items()):
        confirmed = counts.get(str(TriageStatus.CONFIRMED), 0)
        rejected = counts.get(str(TriageStatus.REJECTED), 0)
        reviewed = confirmed + rejected
        report.append(
            {
                "signal_type": signal_type,
                "pending_review": counts.get(str(TriageStatus.PENDING_REVIEW), 0),
                "confirmed": confirmed,
                "rejected": rejected,
                "published": counts.get(str(TriageStatus.PUBLISHED), 0),
                "precision": round(confirmed / reviewed, 4) if reviewed else None,
            }
        )
    return report
