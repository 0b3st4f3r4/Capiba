"""Segmentation of official gazette editions into notices (avisos).

Chunk: querido_diario (PR-D-10)
Responsibility: Split the integral plain text of a gazette edition (as
persisted in the bronze layer by ``crawler_querido_diario`` — no markup
structure) into notice-sized units (avisos/editais/extratos) by the
structural markers declared in ``experiments/detect/D-10.json``, and
extract process numbers for the reedition veto of the ``notice_clone``
signal (``capiba.detection.notice_clone``).

Semantics pre-registered in ``docs/preregistrations/PR-D-10.md``
(section 3): the bronze text is the whole edition; segmentation by
structural markers ("EDITAL", "AVISO DE LICITAÇÃO", "EXTRATO",
"PROCESSO") is part of the design and its error rate is measured (P6).

Dependencies: none (pure text processing)
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable

# Structural markers declared in the battery config (accent-free, compared
# after normalization). Never hardcode different values in callers — the
# config is the single source.
DEFAULT_MARKERS = ("EDITAL", "AVISO DE LICITACAO", "EXTRATO", "PROCESSO")

# A marker line longer than this is prose mentioning the marker, not a
# header (e.g. "... conforme publicado no EXTRATO de ...").
MAX_HEADER_LINE_CHARS = 120

# Canonical NUP ``NNNNN.NNNNNNNN/NNNN-NN`` and punctuated variations
# (spaces around the separators, 4-6 + 6-8 digit groups), per PR-D-10 § 3.
_NUP_PATTERN = re.compile(r"\d{4,6}\s*\.\s*\d{6,8}\s*/\s*\d{4}\s*-\s*\d{2}")

# Label-anchored fallback: "Processo nº/n./no" followed by a numeric
# reference (digits with ., / or - separators).
_LABELED_PATTERN = re.compile(
    r"processo\s*n[º°oa.]*\s*[:\-]?\s*(\d[\d./\-]{3,}\d)",
    re.IGNORECASE,
)

# Plausible digit counts of a normalized process number (digits only).
_MIN_PROCESS_DIGITS = 6
_MAX_PROCESS_DIGITS = 19


def _normalize_line(line: str) -> str:
    """Uppercases, strips diacritics and collapses whitespace."""
    decomposed = unicodedata.normalize("NFKD", line)
    ascii_line = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(ascii_line.upper().split())


def _normalized_markers(markers: Iterable[str]) -> tuple[str, ...]:
    return tuple(_normalize_line(marker) for marker in markers)


def _is_header(line: str, markers: tuple[str, ...]) -> bool:
    """Whether the line opens a new unit: short and marker-prefixed."""
    normalized = _normalize_line(line)
    if not normalized or len(normalized) > MAX_HEADER_LINE_CHARS:
        return False
    return any(normalized.startswith(marker) for marker in markers)


def segment_edition(
    text: str,
    markers: Iterable[str] = DEFAULT_MARKERS,
) -> list[str]:
    """Splits the integral text of a gazette edition into raw units.

    A new unit starts at every header line (short line whose normalized
    form starts with one of the declared markers). The preamble before the
    first marker is discarded. Units are returned verbatim (lines joined
    with ``\\n``), so a planted notice is recovered bit a bit. The minimum
    running-text filter (200 characters) is **not** applied here — it is a
    concern of the signal (``detection.notice_clone``); segmentation
    returns the raw units so the battery can measure coverage and split
    failures (P6).

    Args:
        text: Integral plain text of the edition.
        markers: Structural markers (normalized before matching).

    Returns:
        The raw units, in document order. Empty for text without markers.
    """
    normalized = _normalized_markers(markers)
    segments: list[str] = []
    current: list[str] = []
    for line in text.splitlines():
        if _is_header(line, normalized):
            segment = "\n".join(current).strip()
            if segment:
                segments.append(segment)
            current = [line.rstrip()]
        elif current:
            current.append(line.rstrip())
    segment = "\n".join(current).strip()
    if segment:
        segments.append(segment)
    return segments


def _digits(candidate: str) -> str:
    return re.sub(r"\D", "", candidate)


def extract_process_number(text: str) -> str | None:
    """Extracts the process number of a notice, normalized to digits.

    Tries the NUP pattern first (canonical or punctuated variation), then
    the label-anchored form ("Processo nº ..."). The normalized form is
    digit-only, so punctuation variations compare equal — the reedition
    veto of ``notice_clone`` uses this value. An absent or implausible
    number returns None: no veto and no evidence (PR-D-10 § 3).

    Args:
        text: Notice text.

    Returns:
        The digit-normalized process number, or None.
    """
    for match in _NUP_PATTERN.finditer(text):
        digits = _digits(match.group(0))
        if _MIN_PROCESS_DIGITS <= len(digits) <= _MAX_PROCESS_DIGITS:
            return digits
    for match in _LABELED_PATTERN.finditer(text):
        digits = _digits(match.group(1))
        if _MIN_PROCESS_DIGITS <= len(digits) <= _MAX_PROCESS_DIGITS:
            return digits
    return None
