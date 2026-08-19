"""Contract amendment red flags (battery D-05, O2).

Responsibility: compute the amendment flags of one contract from its
bronze observation sequence (payloads of ``/v1/contratos`` and
``/v1/contratos/atualizacao``), in the semantics declared in
``docs/preregistrations/PR-D-05.md`` (section 3): the sequence is ordered
by ingestion date, the last observation is sovereign, equality never
fires and missing/malformed fields are NULL (insufficient data), never a
clean flag.

Dependencies: capiba.detection.red_flags (defensive parsers)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from capiba.detection.red_flags import _parse_amount, _parse_isodate


@dataclass(frozen=True)
class AmendmentFlags:
    """Amendment flags of one contract plus the calibration descriptors."""

    f_value_amendment: int | None
    f_term_extension: int | None
    max_rectifications: int | None
    observations: int
    value_ratio: float | None


def _parse_rectifications(value: Any) -> int | None:
    """Parses numeroRetificacao defensively; malformed input is None."""
    amount = _parse_amount(value)
    return int(amount) if amount is not None and amount >= 0 else None


def compute_amendment_flags(observations: list[dict[str, Any]]) -> AmendmentFlags:
    """Computes the amendment flags from one contract's bronze observations.

    Args:
        observations: Raw payloads of the contract, each carrying its
            ingestion date in ``observed_on`` plus the PNCP fields
            (``valorInicial``, ``valorAcumulado``, ``dataVigenciaFim``,
            ``numeroRetificacao``). Read order does not matter — the
            sequence is sorted by ``observed_on`` (PR-D-05 § 6).

    Returns:
        The flags (1/0/None) and the descriptors (max rectifications,
        observation count, accumulated/initial ratio of the last
        observation).
    """
    ordered = sorted(observations, key=lambda obs: str(obs.get("observed_on") or ""))

    initial = next(
        (
            value
            for obs in ordered
            if (value := _parse_amount(obs.get("valorInicial"))) is not None
            and value > 0
        ),
        None,
    )
    accumulated = next(
        (
            value
            for obs in reversed(ordered)
            if (value := _parse_amount(obs.get("valorAcumulado"))) is not None
            and value > 0
        ),
        None,
    )
    if initial is not None and accumulated is not None:
        f_value_amendment: int | None = int(accumulated > initial)
        value_ratio: float | None = round(float(accumulated / initial), 4)
    else:
        f_value_amendment = None
        value_ratio = None

    validity_ends = [
        end
        for obs in ordered
        if (end := _parse_isodate(obs.get("dataVigenciaFim"))) is not None
    ]
    if not validity_ends:
        f_term_extension: int | None = None
    else:
        f_term_extension = int(validity_ends[-1] > validity_ends[0])

    rectifications = [
        parsed
        for obs in ordered
        if (parsed := _parse_rectifications(obs.get("numeroRetificacao"))) is not None
    ]

    return AmendmentFlags(
        f_value_amendment=f_value_amendment,
        f_term_extension=f_term_extension,
        max_rectifications=max(rectifications) if rectifications else None,
        observations=len(observations),
        value_ratio=value_ratio,
    )
