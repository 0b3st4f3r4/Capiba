"""``notice_clone`` signal: cloned/directed notices via semantic similarity.

Responsibility: Emit the ``notice_clone`` signal when a segmented notice
of a gazette edition is a near-copy of a historical notice of the **same
territory** (IBGE), under the semantics pre-registered in
``docs/preregistrations/PR-D-10.md`` (section 3):

- **Unit of analysis**: a notice segmented from the edition
  (``capiba.ingestion.gazette_segments.segment_edition``) with at least
  ``min_chars`` of running text (below that, pure metadata — excluded,
  never signals).
- **Notice identity**: ``notice_id`` derives deterministically from
  (territory, edition date, edition, segment index); the triage key is
  the hash of the ordered pair of ids (``triage_key``).
- **Candidate pair**: new notice x historical notices of the same
  territory within a rolling window of ``window_days``. Cross-territory
  is out of v1.
- **Reedition veto**: the same extractable process number on both sides
  (``gazette_segments.extract_process_number``) means
  rectification/republication — the pair **never signals**. A process
  number missing on one side is neither veto nor evidence.
- **Emission**: signal iff the maximum cosine similarity of the pair is
  **strictly greater** than ``threshold`` (placeholder 0.85; any change
  requires PR-D-10b). Score = ``round(max_similarity, 4)``. One signal
  per pair; ``details`` carries the notice ids, edition dates and
  similarity.
- **Null discipline**: notice below ``min_chars``, edition without a
  valid segment or unavailable encoder -> pair not computable, never
  signals (the prototype ``detect_clone`` of ``nlp_operators.py``, now
  removed, had no such discipline).

The module is pure and deterministic: the encoder is injectable (the
battery and the tests stub it; production uses ``default_encoder`` with
the model pinned in the battery config / ``DETECTION_NOTICE_CLONE_
ENCODER``). Embeddings never enter the graph or the CRI.

Dependencies: capiba.detection.signals, capiba.ingestion.gazette_segments
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

import numpy as np

from capiba.detection.signals import SignalType
from capiba.ingestion.gazette_segments import extract_process_number

logger = logging.getLogger(__name__)

# Pre-registered defaults (PR-D-10, section 3); the battery config
# (experiments/detect/D-10.json) carries the authoritative values.
DEFAULT_THRESHOLD = 0.85
DEFAULT_MIN_CHARS = 200
DEFAULT_WINDOW_DAYS = 365
SCORE_DECIMALS = 4
ENCODER_MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"

# Encodes a batch of texts into one vector per text (array-like, N x D).
EncoderFn = Callable[[list[str]], Any]


@dataclass(frozen=True)
class Notice:
    """A notice segmented from a gazette edition (unit of analysis)."""

    notice_id: str
    territory_id: str
    date: date
    text: str


def notice_id(
    territory_id: str,
    edition_date: date,
    edition: str,
    segment_index: int,
) -> str:
    """Derives the deterministic notice id (PR-D-10 § 3)."""
    raw = f"{territory_id}|{edition_date.isoformat()}|{edition}|{segment_index}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def triage_key(notice_id_a: str, notice_id_b: str) -> str:
    """Triage key of a pair: hash of the ordered pair of notice ids."""
    ordered = "+".join(sorted((notice_id_a, notice_id_b)))
    return hashlib.sha256(ordered.encode()).hexdigest()


def cosine_similarity(vector_a: Any, vector_b: Any) -> float:
    """Cosine similarity between two embedding vectors, clamped to [-1, 1].

    Identical non-zero vectors return 1.0 (the N0 exact-copy anchor); a
    zero vector (uncomputable embedding) returns 0.0 — the pair never
    signals.
    """
    a = np.asarray(vector_a, dtype=np.float64).ravel()
    b = np.asarray(vector_b, dtype=np.float64).ravel()
    denominator = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denominator == 0.0:
        return 0.0
    return float(min(1.0, max(-1.0, float(np.dot(a, b)) / denominator)))


def default_encoder(model: str = ENCODER_MODEL, device: str = "cpu") -> EncoderFn:
    """Builds the production encoder (sentence-transformers, lazy import).

    Loading the model is expensive and requires the package; callers that
    only exercise the pure logic inject a stub instead.
    """
    from sentence_transformers import SentenceTransformer

    encoder = SentenceTransformer(model, device=device)

    def encode(texts: list[str]) -> Any:
        return encoder.encode(texts, convert_to_numpy=True)

    return encode


def valid_notices(notices: Iterable[Notice], min_chars: int) -> list[Notice]:
    """Filters and sorts the analyzable notices (null discipline).

    A notice is analyzable when it carries a territory, a date and at
    least ``min_chars`` of running (stripped) text. The output is sorted
    by (date, notice id) so batch encoding is bit-a-bit deterministic.
    """
    valid = [
        notice
        for notice in notices
        if notice.territory_id
        and notice.date is not None
        and len(notice.text.strip()) >= min_chars
    ]
    return sorted(valid, key=lambda n: (n.date, n.notice_id))


def candidate_pairs(
    notices: Iterable[Notice],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_chars: int = DEFAULT_MIN_CHARS,
    reference_date: date | None = None,
) -> list[tuple[Notice, Notice]]:
    """Computes the candidate (new, historical) pairs of a corpus.

    A pair is candidate iff both notices are analyzable, share the
    territory, the historical is strictly older and within the rolling
    window, and the reedition veto does not apply (same extractable
    process number on both sides -> excluded, never signals). With
    ``reference_date``, only notices published exactly on it are "new"
    (the production run semantics); without it, every notice pairs with
    all older ones (the battery semantics). Sorted by (new id, historical
    id) for determinism.
    """
    valid = valid_notices(notices, min_chars)
    process = {notice.notice_id: extract_process_number(notice.text) for notice in valid}
    pairs: list[tuple[Notice, Notice]] = []
    for new in valid:
        if reference_date is not None and new.date != reference_date:
            continue
        for historical in valid:
            if historical is new or historical.date >= new.date:
                continue
            if (new.date - historical.date).days > window_days:
                continue
            if historical.territory_id != new.territory_id:
                continue
            new_process = process[new.notice_id]
            historical_process = process[historical.notice_id]
            if (
                new_process is not None
                and historical_process is not None
                and new_process == historical_process
            ):
                continue  # reedition veto: rectification/republication
            pairs.append((new, historical))
    return sorted(pairs, key=lambda p: (p[0].notice_id, p[1].notice_id))


def notice_clone_signals(
    notices: Iterable[Notice],
    *,
    encode: EncoderFn,
    threshold: float = DEFAULT_THRESHOLD,
    window_days: int = DEFAULT_WINDOW_DAYS,
    min_chars: int = DEFAULT_MIN_CHARS,
    reference_date: date | None = None,
    score_decimals: int = SCORE_DECIMALS,
) -> list[dict[str, Any]]:
    """Emits one ``notice_clone`` signal per candidate pair above the threshold.

    Args:
        notices: Segmented notices of the corpus (any source; the battery
            segments synthetic editions, production segments the bronze
            Querido Diário texts).
        encode: Batch encoder (texts -> vectors); injectable. If the
            encoder is unavailable the caller must catch it (best-effort
            emission) — no signal is produced.
        threshold: Strict emission threshold (``>``) on the cosine
            similarity (pre-registered placeholder 0.85).
        window_days: Rolling window for the historical candidates.
        min_chars: Minimum running-text size of an analyzable notice.
        reference_date: When set, only notices published on it are "new".
        score_decimals: Decimals of the rounded score (declared 4).

    Returns:
        Signal rows (entity_type, entity_id, signal_type, score, details)
        sorted by (new notice id, historical notice id) for bit-a-bit
        determinism. ``entity_id`` is the triage key (hash of the ordered
        pair of notice ids); ``details`` carries the fields that ground
        the signal (recomputable from the bronze texts, PR-D-10 § 6).
    """
    valid = valid_notices(notices, min_chars)
    pairs = candidate_pairs(
        valid,
        window_days=window_days,
        min_chars=min_chars,
        reference_date=reference_date,
    )
    if not pairs:
        return []

    vectors = np.asarray(encode([notice.text for notice in valid]), dtype=np.float64)
    position = {notice.notice_id: index for index, notice in enumerate(valid)}

    signals: list[dict[str, Any]] = []
    for new, historical in pairs:
        similarity = cosine_similarity(
            vectors[position[new.notice_id]],
            vectors[position[historical.notice_id]],
        )
        if not similarity > threshold:  # strict comparison (PR-D-10 § 3)
            continue
        signals.append(
            {
                "entity_type": "notice",
                "entity_id": triage_key(new.notice_id, historical.notice_id),
                "signal_type": SignalType.NOTICE_CLONE,
                "score": round(similarity, score_decimals),
                "details": json.dumps(
                    {
                        "historical_date": historical.date.isoformat(),
                        "historical_notice_id": historical.notice_id,
                        "new_date": new.date.isoformat(),
                        "new_notice_id": new.notice_id,
                        "similarity": round(similarity, 6),
                        "territory_id": new.territory_id,
                        "threshold": threshold,
                        "window_days": window_days,
                    },
                    sort_keys=True,
                ),
            }
        )
    return signals
