"""Pipeline alert helpers.

Chunk: alerts
Responsibility: Best-effort synchronous wrappers around the async
NotificationDispatcher, called from Airflow tasks (detection signals
and validation failures). Notifications are disabled (no-op with a
debug log) when NOTIFICATION_RECIPIENTS is empty, and dispatch
failures never raise — they only log a warning.

Dependencies: capiba.config, capiba.notification.dispatcher
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime
from typing import Any

from capiba.config import (
    NOTIFICATION_ALERT_MAX_SIGNALS,
    NOTIFICATION_ALERT_SCORE,
    NOTIFICATION_RECIPIENTS,
)
from capiba.notification.dispatcher import (
    NotificationAlert,
    NotificationChannel,
    NotificationDispatcher,
    Priority,
)

logger = logging.getLogger(__name__)

# Score at or above which a detection alert escalates to CRITICAL.
CRITICAL_SCORE = 0.9

# Share of normalization errors over the total records that triggers a
# quality alert even when the validation report is formally valid
# (duplicates == 0). 5% is a pragmatic default: below that, per-record
# normalization noise (a bad date, a missing field) is expected at scale.
NORMALIZATION_ERROR_RATE_THRESHOLD = 0.05


def _dispatch(alert: NotificationAlert) -> bool:
    """Runs the async dispatcher from a synchronous Airflow task.

    Args:
        alert: Alert to be notified.

    Returns:
        True if sent successfully, False otherwise (never raises).
    """
    try:
        return asyncio.run(NotificationDispatcher().dispatch(alert))
    except Exception as e:
        logger.warning("Notification dispatch failed: %s", e)
        return False


def notify_fraud_signals(signals: list[dict[str, Any]], run_date: date | None) -> bool:
    """Sends a detection alert for the signals above the alert threshold.

    Args:
        signals: Signal rows written to the gold layer.
        run_date: Pipeline run date (metadata only).

    Returns:
        True if an alert was sent, False otherwise (never raises).
    """
    try:
        return _notify_fraud_signals(signals, run_date)
    except Exception as e:
        logger.warning("Failed to notify fraud signals: %s", e)
        return False


def _notify_fraud_signals(signals: list[dict[str, Any]], run_date: date | None) -> bool:
    """Builds and dispatches the detection alert (see notify_fraud_signals).

    Adapts the pipeline signal shape (entity_type, entity_id, signal_type,
    score, details) to the detection e-mail template, which iterates
    ``signal.type``/``signal.score``/``signal.evidence``.
    """
    if not NOTIFICATION_RECIPIENTS:
        logger.debug("Notifications disabled: NOTIFICATION_RECIPIENTS is empty")
        return False

    flagged = [s for s in signals if float(s.get("score") or 0) >= NOTIFICATION_ALERT_SCORE]
    if not flagged:
        logger.debug("No signal above the alert threshold (%.2f)", NOTIFICATION_ALERT_SCORE)
        return False

    flagged.sort(key=lambda s: float(s.get("score") or 0), reverse=True)
    max_score = float(flagged[0]["score"])
    priority = Priority.CRITICAL if max_score >= CRITICAL_SCORE else Priority.HIGH

    # Top-K by score: the e-mail payload stays sendable even when a run
    # flags hundreds of thousands of signals; the full count is kept in
    # the title/message and in ``flagged_total``.
    top = flagged[:NOTIFICATION_ALERT_MAX_SIGNALS]
    adapted = [
        {
            "type": str(s.get("signal_type")),
            "score": s.get("score"),
            "evidence": s.get("details"),
        }
        for s in top
    ]
    entities = sorted({f"{s.get('entity_type')}:{s.get('entity_id')}" for s in top})

    alert = NotificationAlert(
        title=f"Detection: {len(flagged)} fraud signals >= {NOTIFICATION_ALERT_SCORE}",
        message=(
            f"{len(flagged)} signal(s) reached the alert threshold "
            f"({NOTIFICATION_ALERT_SCORE}) in the run of {run_date or 'today'}."
        ),
        priority=priority,
        channel=NotificationChannel.EMAIL,
        recipients=NOTIFICATION_RECIPIENTS,
        metadata={
            "signals": adapted,
            "flagged_total": len(flagged),
            "entity": ", ".join(entities),
            "risk_index": max_score,
            "run_date": run_date.isoformat() if run_date else None,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    return _dispatch(alert)


def notify_validation_failure(report: dict[str, Any], pipeline: str) -> bool:
    """Sends a quality alert when the validation report fails.

    Args:
        report: Validation report (total, duplicates, normalization_errors,
            valid).
        pipeline: Pipeline name (used as the alert dataset).

    Returns:
        True if an alert was sent, False otherwise (never raises).
    """
    try:
        return _notify_validation_failure(report, pipeline)
    except Exception as e:
        logger.warning("Failed to notify validation failure: %s", e)
        return False


def _notify_validation_failure(report: dict[str, Any], pipeline: str) -> bool:
    """Builds and dispatches the quality alert (see notify_validation_failure).

    Triggers when the report is invalid (``valid: false``, i.e. duplicates
    were found) or when the normalization error rate exceeds
    NORMALIZATION_ERROR_RATE_THRESHOLD (5% of the total records).
    """
    if not NOTIFICATION_RECIPIENTS:
        logger.debug("Notifications disabled: NOTIFICATION_RECIPIENTS is empty")
        return False

    total = int(report.get("total") or 0)
    duplicates = int(report.get("duplicates") or 0)
    errors = int(report.get("normalization_errors") or 0)
    error_rate = errors / total if total else (1.0 if errors else 0.0)

    problems: list[str] = []
    if not report.get("valid", True):
        problems.append(f"{duplicates} duplicate record(s) found")
    if error_rate > NORMALIZATION_ERROR_RATE_THRESHOLD:
        problems.append(
            f"normalization error rate {error_rate:.1%} above "
            f"{NORMALIZATION_ERROR_RATE_THRESHOLD:.0%} ({errors} of {total})"
        )
    if not problems:
        return False

    alert = NotificationAlert(
        title=f"Quality alert: {pipeline}",
        message=f"Validation of pipeline '{pipeline}' reported problems.",
        priority=Priority.CRITICAL,
        channel=NotificationChannel.EMAIL,
        recipients=NOTIFICATION_RECIPIENTS,
        metadata={
            "dataset": pipeline,
            "score": error_rate,
            "alerts": problems,
            "timestamp": datetime.now(UTC).isoformat(),
        },
    )
    return _dispatch(alert)
