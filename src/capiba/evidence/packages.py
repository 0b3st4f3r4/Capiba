"""Reproducible evidence packages per detected signal.

Responsibility: build and store the evidence package of each fraud
signal — operator, run window, code version and source rows with a
SHA-256 hash — so a third party re-executes the package and obtains the
same result (the "data diary" automated).

Two artifacts per detect run, stored via ``EvidenceStorage``:

- one **batch package** with every silver row used in the run (the
  ``anomalous_duration`` operator pools IQR over the whole batch, so
  per-entity rows alone would not reproduce the score) plus the list of
  emitted signals;
- one **manifest per signal**, keyed by the triage key
  (``{entity_type}:{entity_id}:{signal_type}``), referencing the batch
  by its content hash (``batch_sha256``).

``collusion_network`` is graph-derived (ArangoDB), so it carries a third
artifact (PR-D-03): a **graph batch package** (``kind: "graph_batch"``)
with the eligibility snapshot — rows ``{buyer, supplier, wins}`` with
``wins >= min_wins`` — and ``snapshot_sha256``; the ``reproduction`` block
also carries ``min_buyers`` (PR-D-03b, default 1 for older packages). Its
manifests reference ``graph_sha256`` and are ``reproducible: true``. When the graph snapshot
is unavailable (ArangoDB down, best-effort path), the manifest falls back
to ``reproducible: false``.

Dependencies: capiba.evidence.storage, capiba.pipeline.tasks (lazy,
for reproduction), capiba.db.triage (signal key), capiba.detection.graphs
(pair derivation from the eligibility snapshot)
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
from capiba.detection.graphs import pairs_from_eligibility
from capiba.detection.signals import SignalType, collusion_signals

logger = logging.getLogger(__name__)

SCHEMA = "capiba.signal-package/1"

# Graph-derived signals are reproducible only with a graph snapshot.
GRAPH_DERIVED = {str(SignalType.COLLUSION_NETWORK)}

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


def build_graph_batch_package(
    snapshot_rows: list[dict[str, Any]],
    signals: list[dict[str, Any]],
    min_wins: int,
    run_date: date | None,
    min_buyers: int = 1,
) -> dict[str, Any]:
    """Builds the graph batch package: eligibility snapshot + signals + hash.

    Args:
        snapshot_rows: Eligibility rows ``{"buyer", "supplier", "wins"}``
            from ``collusion_eligibility`` at ``min_wins``.
        signals: Collusion signal rows emitted in the run.
        min_wins: Eligibility threshold that produced the snapshot.
        run_date: Run partition date (None for ad-hoc runs).
        min_buyers: Distinct-buyer threshold applied to the pairs
            (PR-D-03b; 1 = single-buyer semantics of D-03).

    Returns:
        Graph batch package payload (schema ``capiba.signal-package/1``).
    """
    rows = sorted(snapshot_rows, key=lambda row: (row["buyer"], row["supplier"]))
    return {
        "schema": SCHEMA,
        "kind": "graph_batch",
        "window": {"run_date": run_date.isoformat() if run_date else None},
        "code": _code_version(),
        "reproduction": {
            "operator": "detect_collusion",
            "min_wins": min_wins,
            "min_buyers": min_buyers,
        },
        "signals": [_signal_view(signal) for signal in signals],
        "snapshot_rows": rows,
        "snapshot_sha256": _sha256(rows),
    }


def build_signal_manifest(
    signal: dict[str, Any], batch_sha256: str, graph_sha256: str | None = None
) -> dict[str, Any]:
    """Builds the per-signal manifest referencing the batch package.

    Args:
        signal: Signal row emitted in the run.
        batch_sha256: Content hash (storage SHA-256) of the batch package.
        graph_sha256: Content hash of the graph batch package, when the
            signal is graph-derived and the eligibility snapshot was
            stored (PR-D-03); makes the signal reproducible.

    Returns:
        Signal manifest payload with the triage ``signal_key``.
    """
    key = signal_key(
        str(signal["entity_type"]),
        str(signal["entity_id"]),
        str(signal["signal_type"]),
    )
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": "signal_manifest",
        "signal_key": key,
        "signal": _signal_view(signal),
        "batch_sha256": batch_sha256,
    }
    if graph_sha256 is not None:
        manifest["graph_sha256"] = graph_sha256
    manifest["reproducible"] = (
        str(signal["signal_type"]) not in GRAPH_DERIVED or graph_sha256 is not None
    )
    return manifest


def store_signal_packages(
    storage: Any,
    signals: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    run_date: date | None,
    graph_snapshot: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stores the batch package and one manifest per signal.

    Artifacts are content-addressed (SHA-256 object keys), so identical
    reruns converge to the same objects. When ``graph_snapshot`` is given
    (``{"rows": eligibility rows, "min_wins": int}``), a graph batch
    package is stored as well and the graph-derived manifests reference it
    with ``reproducible: true`` (PR-D-03).

    Args:
        storage: EvidenceStorage instance (MinIO).
        signals: Signal rows emitted in the run.
        contracts: Silver contract rows read by the detect task.
        run_date: Run partition date (None for ad-hoc runs).
        graph_snapshot: Eligibility snapshot of the collusion detection,
            or None when ArangoDB was unavailable (manifests stay
            non-reproducible).

    Returns:
        Summary ``{"batch_sha256": str, "graph_sha256": str | None,
        "manifests": int}``; ``batch_sha256`` is None when there are no
        signals.
    """
    if not signals:
        return {"batch_sha256": None, "graph_sha256": None, "manifests": 0}

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

    graph_sha256: str | None = None
    if graph_snapshot is not None:
        graph_signals = [
            signal
            for signal in signals
            if str(signal["signal_type"]) in GRAPH_DERIVED
        ]
        graph_batch = build_graph_batch_package(
            graph_snapshot["rows"],
            graph_signals,
            graph_snapshot["min_wins"],
            run_date,
            int(graph_snapshot.get("min_buyers", 1)),
        )
        graph_result = storage.store(
            _canonical(graph_batch),
            f"signal-graph-batch-{run_date or 'adhoc'}.json",
            {
                "signal_key": f"graph-batch:{run_date or 'adhoc'}",
                "entity_cnpj": "multiple",
                "evidence_type": "signal_package",
                "captured_at": captured_at,
                "source": _METADATA_SOURCE,
                "hash_sha256": graph_batch["snapshot_sha256"],
                "captured_by": _METADATA_CAPTURED_BY,
            },
            "application/json",
        )
        graph_sha256 = graph_result["sha256"]

    for signal in signals:
        manifest = build_signal_manifest(
            signal,
            batch_sha256,
            graph_sha256
            if str(signal["signal_type"]) in GRAPH_DERIVED
            else None,
        )
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
        "Signal evidence packages stored: batch %s + graph %s + %d manifests",
        batch_sha256,
        graph_sha256,
        len(signals),
    )
    return {
        "batch_sha256": batch_sha256,
        "graph_sha256": graph_sha256,
        "manifests": len(signals),
    }


def _score(signals: list[dict[str, Any]], signal_key_: str) -> float | None:
    """Score of the signal identified by the triage key, None when absent."""
    for signal in signals:
        key = signal_key(
            str(signal["entity_type"]),
            str(signal["entity_id"]),
            str(signal["signal_type"]),
        )
        if key == signal_key_:
            return float(signal["score"])
    return None


def reproduce_signal(
    batch_package: dict[str, Any], signal_key_: str
) -> dict[str, Any]:
    """Re-executes the operator over the package rows and compares the score.

    Dispatches by ``kind``: ``batch`` re-runs ``detect_fraud_signals`` over
    the silver rows; ``graph_batch`` (PR-D-03) re-derives the collusion
    pairs from the eligibility snapshot.

    Args:
        batch_package: Batch package payload (as stored).
        signal_key_: Triage key of the signal to reproduce.

    Returns:
        ``{"signal_key", "expected", "actual", "integrity", "match"}`` —
        ``integrity`` is the source-rows hash check, ``actual`` is None
        when the signal does not reappear, and ``match`` requires
        integrity plus equal scores.
    """
    if batch_package.get("kind") == "graph_batch":
        return _reproduce_graph_signal(batch_package, signal_key_)

    from capiba.pipeline.tasks import detect_fraud_signals

    rows = batch_package.get("source_rows", [])
    integrity = _sha256(rows) == batch_package.get("source_rows_sha256")

    expected = _score(batch_package.get("signals", []), signal_key_)
    actual = _score(detect_fraud_signals(rows), signal_key_)
    return {
        "signal_key": signal_key_,
        "expected": expected,
        "actual": actual,
        "integrity": integrity,
        "match": bool(integrity and expected is not None and actual == expected),
    }


def _reproduce_graph_signal(
    graph_package: dict[str, Any], signal_key_: str
) -> dict[str, Any]:
    """Re-derives the collusion pairs from the eligibility snapshot.

    The snapshot rows are re-filtered by the package ``min_wins`` (the
    derivation rule under test), turned into pairs by
    ``pairs_from_eligibility`` at the package ``min_buyers`` (default 1 —
    packages written before PR-D-03b) and converted back into signals —
    the same composition the detect task runs against the live graph.
    """
    rows = graph_package.get("snapshot_rows", [])
    integrity = _sha256(rows) == graph_package.get("snapshot_sha256")
    min_wins = int(graph_package.get("reproduction", {}).get("min_wins", 3))
    min_buyers = int(graph_package.get("reproduction", {}).get("min_buyers", 1))

    eligible = [row for row in rows if int(row.get("wins", 0)) >= min_wins]
    recomputed = collusion_signals(
        pairs_from_eligibility(eligible, min_buyers), min_wins, min_buyers
    )

    expected = _score(graph_package.get("signals", []), signal_key_)
    actual = _score(recomputed, signal_key_)
    return {
        "signal_key": signal_key_,
        "expected": expected,
        "actual": actual,
        "integrity": integrity,
        "match": bool(integrity and expected is not None and actual == expected),
    }
