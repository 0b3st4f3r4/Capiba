"""Reproducible evidence packages per detected signal (O9).

Responsibility: build and store the evidence package of each fraud
signal — operator, run window, code version and source rows with a
SHA-256 hash — so a third party re-executes the package and obtains the
same result (the "data diary" automated).

Two artifacts per detect run, stored via ``EvidenceStorage``:

- one **batch package** with every silver row used in the run (the
  ``anomalous_duration`` operator pools IQR over the whole batch, so
  per-entity rows alone would not reproduce the score) plus the list of
  emitted signals;
- one **manifest per signal**, keyed by the O10 triage key
  (``{entity_type}:{entity_id}:{signal_type}``), referencing the batch
  by its content hash (``batch_sha256``).

``collusion_network`` is graph-derived (ArangoDB), so its manifest is
marked ``reproducible: false`` — calibrating/reproducing it is PR-D-03
scope.

Dependencies: capiba.evidence.storage, capiba.pipeline.tasks (lazy,
for reproduction), capiba.db.triage (signal key)
"""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import logging
import os
from datetime import date
from typing import Any

from capiba.db.triage import signal_key
from capiba.detection.signals import SignalType

logger = logging.getLogger(__name__)

SCHEMA = "capiba.signal-package/1"

# Graph-derived signals cannot be reproduced from the batch rows alone.
NON_REPRODUCIBLE = {str(SignalType.COLLUSION_NETWORK)}

_METADATA_SOURCE = "detect"
_METADATA_CAPTURED_BY = "capiba-pipeline"


def _canonical(payload: Any) -> bytes:
    """Canonical JSON encoding (sorted keys) used for storage and hashing."""
    return json.dumps(payload, sort_keys=True, default=str).encode()


def _sha256(payload: Any) -> str:
    """SHA-256 of the canonical encoding of a payload."""
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _code_version() -> dict[str, str]:
    """Code version metadata: package version + artifact SHA (when published)."""
    try:
        version = importlib.metadata.version("capiba")
    except importlib.metadata.PackageNotFoundError:
        version = "unknown"
    return {
        "package": "capiba",
        "version": version,
        "artifact_sha": os.getenv("CAPIBA_ARTIFACT_SHA", "unknown"),
    }


def _signal_view(signal: dict[str, Any]) -> dict[str, Any]:
    """Serializable view of a signal row (enum-safe)."""
    return {**signal, "signal_type": str(signal["signal_type"])}


def build_batch_package(
    contracts: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    run_date: date | None,
) -> dict[str, Any]:
    """Builds the run batch package: source rows + emitted signals + hash.

    Args:
        contracts: Silver contract rows read by the detect task.
        signals: Signal rows emitted in the run.
        run_date: Run partition date (None for ad-hoc runs).

    Returns:
        Batch package payload (schema ``capiba.signal-package/1``).
    """
    return {
        "schema": SCHEMA,
        "kind": "batch",
        "window": {"run_date": run_date.isoformat() if run_date else None},
        "code": _code_version(),
        "reproduction": {"operator": "detect_fraud_signals"},
        "signals": [_signal_view(signal) for signal in signals],
        "source_rows": contracts,
        "source_rows_sha256": _sha256(contracts),
    }


def build_signal_manifest(
    signal: dict[str, Any], batch_sha256: str
) -> dict[str, Any]:
    """Builds the per-signal manifest referencing the batch package.

    Args:
        signal: Signal row emitted in the run.
        batch_sha256: Content hash (storage SHA-256) of the batch package.

    Returns:
        Signal manifest payload with the O10 triage ``signal_key``.
    """
    key = signal_key(
        str(signal["entity_type"]),
        str(signal["entity_id"]),
        str(signal["signal_type"]),
    )
    return {
        "schema": SCHEMA,
        "kind": "signal_manifest",
        "signal_key": key,
        "signal": _signal_view(signal),
        "batch_sha256": batch_sha256,
        "reproducible": str(signal["signal_type"]) not in NON_REPRODUCIBLE,
    }


def store_signal_packages(
    storage: Any,
    signals: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    run_date: date | None,
) -> dict[str, Any]:
    """Stores the batch package and one manifest per signal.

    Artifacts are content-addressed (SHA-256 object keys), so identical
    reruns converge to the same objects.

    Args:
        storage: EvidenceStorage instance (MinIO).
        signals: Signal rows emitted in the run.
        contracts: Silver contract rows read by the detect task.
        run_date: Run partition date (None for ad-hoc runs).

    Returns:
        Summary ``{"batch_sha256": str, "manifests": int}``;
        ``batch_sha256`` is None when there are no signals.
    """
    if not signals:
        return {"batch_sha256": None, "manifests": 0}

    from datetime import UTC, datetime

    captured_at = datetime.now(UTC).isoformat()
    batch = build_batch_package(contracts, signals, run_date)
    batch_result = storage.store(
        _canonical(batch),
        f"signal-batch-{run_date or 'adhoc'}.json",
        {
            "signal_key": f"batch:{run_date or 'adhoc'}",
            "entity_cnpj": "multiple",
            "evidence_type": "signal_package",
            "captured_at": captured_at,
            "source": _METADATA_SOURCE,
            "hash_sha256": batch["source_rows_sha256"],
            "captured_by": _METADATA_CAPTURED_BY,
        },
        "application/json",
    )
    batch_sha256 = batch_result["sha256"]

    for signal in signals:
        manifest = build_signal_manifest(signal, batch_sha256)
        storage.store(
            _canonical(manifest),
            f"signal-manifest-{manifest['signal_key']}.json",
            {
                "signal_key": manifest["signal_key"],
                "entity_cnpj": str(signal["entity_id"]),
                "evidence_type": "signal_package",
                "captured_at": captured_at,
                "source": _METADATA_SOURCE,
                "hash_sha256": batch_sha256,
                "captured_by": _METADATA_CAPTURED_BY,
                "batch_sha256": batch_sha256,
            },
            "application/json",
        )

    logger.info(
        "Signal evidence packages stored: batch %s + %d manifests",
        batch_sha256,
        len(signals),
    )
    return {"batch_sha256": batch_sha256, "manifests": len(signals)}


def reproduce_signal(
    batch_package: dict[str, Any], signal_key_: str
) -> dict[str, Any]:
    """Re-executes the operator over the package rows and compares the score.

    Args:
        batch_package: Batch package payload (as stored).
        signal_key_: O10 triage key of the signal to reproduce.

    Returns:
        ``{"signal_key", "expected", "actual", "integrity", "match"}`` —
        ``integrity`` is the source-rows hash check, ``actual`` is None
        when the signal does not reappear, and ``match`` requires
        integrity plus equal scores.
    """
    from capiba.pipeline.tasks import detect_fraud_signals

    rows = batch_package.get("source_rows", [])
    integrity = _sha256(rows) == batch_package.get("source_rows_sha256")

    def _score(signals: list[dict[str, Any]]) -> float | None:
        for signal in signals:
            key = signal_key(
                str(signal["entity_type"]),
                str(signal["entity_id"]),
                str(signal["signal_type"]),
            )
            if key == signal_key_:
                return float(signal["score"])
        return None

    expected = _score(batch_package.get("signals", []))
    actual = _score(detect_fraud_signals(rows))
    return {
        "signal_key": signal_key_,
        "expected": expected,
        "actual": actual,
        "integrity": integrity,
        "match": bool(integrity and expected is not None and actual == expected),
    }
