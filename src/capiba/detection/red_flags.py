"""Contract red flags and the deterministic CRI (battery D-04).

Responsibility: compute the per-contract red flags of the Fazekas &
Kocsis CRI in the semantics declared in
``docs/preregistrations/PR-D-04.md`` (section 3, as amended): each flag
is 1 (suspect), 0 (not suspect) or None (insufficient data — absent or
malformed source fields never count as a clean flag), and the CRI is the
mean of the non-null flags, rounded to 4 decimals (None when every flag
is null).

Dependencies: capiba.detection.signals
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from capiba.detection.signals import is_non_competitive

# Declared placeholder (PR-D-04 §3); calibration requires a PR-D-04b with
# the observed real distribution as justification.
SHORT_WINDOW_DAYS = 7

# Modality labels that mean "we do not know the modality" — the flag is
# null (insufficient data), not zero (PR-D-04 amendment of 2026-08-19).
_UNKNOWN_MODALITIES = {"", "not_informed"}


@dataclass(frozen=True)
class ContractRedFlags:
    """Red flags of one contract and its composite CRI (None = no data)."""

    f_non_competitive: int | None
    f_short_window: int | None
    f_price_ratio: int | None
    cri: float | None


def _parse_isodate(value: Any) -> date | None:
    """Parses an ISO date/datetime defensively; malformed input is None."""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None


def _parse_amount(value: Any) -> Decimal | None:
    """Parses a decimal amount defensively; malformed input is None."""
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return None


def compute_red_flags(
    payload: dict[str, Any],
    modality: Any,
    short_window_days: int = SHORT_WINDOW_DAYS,
) -> ContractRedFlags:
    """Computes the red flags of one contract.

    Args:
        payload: Raw bronze payload of the contract (PNCP field names).
        modality: Silver modality label (``is_non_competitive`` applies).
        short_window_days: Submission-window threshold (declared 7).

    Returns:
        The flags and the CRI (mean of the non-null flags, 4 decimals).
    """
    text = str(modality or "").strip().lower()
    f_non_competitive = (
        None if text in _UNKNOWN_MODALITIES else int(is_non_competitive(modality))
    )

    opened = _parse_isodate(payload.get("dataAberturaProposta"))
    closed = _parse_isodate(payload.get("dataEncerramentoProposta"))
    f_short_window = (
        int((closed - opened).days < short_window_days)
        if opened is not None and closed is not None
        else None
    )

    estimated = _parse_amount(payload.get("valorInicialCompra"))
    homologated = _parse_amount(payload.get("valorTotalHomologado"))
    f_price_ratio = (
        int(homologated > estimated)
        if estimated is not None
        and homologated is not None
        and estimated > 0
        and homologated > 0
        else None
    )

    values = [
        flag
        for flag in (f_non_competitive, f_short_window, f_price_ratio)
        if flag is not None
    ]
    cri = round(sum(values) / len(values), 4) if values else None
    return ContractRedFlags(f_non_competitive, f_short_window, f_price_ratio, cri)
