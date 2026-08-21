"""Contract amendment red flags (battery D-05).

Responsibility: compute the amendment flags of one contract from its
bronze observation sequence (payloads of ``/v1/contratos`` and
``/v1/contratos/atualizacao``), in the semantics declared in
``docs/preregistrations/PR-D-05.md`` (section 3): the sequence is ordered
by ingestion date, the last observation is sovereign, equality never
fires and missing/malformed fields are NULL (insufficient data), never a
clean flag.

``compute_term_flags`` (PR-D-05b, plan B after the proxy coverage was
refuted at 23.54% — ``docs/results/R-D-05.md`` section 4) computes the
same question from the contract's **registered terms** (``GET
/v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/termos``), the
authoritative source: it answers "was there a formal amendment?", not
"when did it change". The proxy semantics above remains the vigente
reference until the Q4 verdict of PR-D-05b.

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


def _parse_int(value: Any) -> int | None:
    """Parses an integer field (e.g. prazoAditadoDias) defensively."""
    amount = _parse_amount(value)
    return int(amount) if amount is not None else None


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
        # Full precision: rounding (e.g. to 4 decimals) can collapse a tiny
        # but positive ratio to 0.0, outside the declared domain (P7 of
        # PR-D-05: ratio > 0 whenever present).
        value_ratio: float | None = float(accumulated / initial)
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


@dataclass(frozen=True)
class TermFlags:
    """Amendment flags of one contract computed from its registered terms."""

    f_value_amendment_terms: int | None
    f_term_extension_terms: int | None
    terms_count: int | None
    total_value_increase: float | None
    total_days_extended: int | None
    term_types: list[str] | None


_AMENDMENT_TERM_TYPE = "Termo Aditivo"


def compute_term_flags(terms: list[dict[str, Any]] | None) -> TermFlags:
    """Computes the amendment flags from the contract's registered terms.

    Reference semantics: ``docs/preregistrations/PR-D-05b.md`` (section 3).
    A flag fires only on a term of type ``Termo Aditivo`` with the matching
    qualification and a positive amount/days; ``qualificacaoReajuste`` is
    NOT a flag (index reajuste is a legal price update, not an amendment —
    accusing it would be a structural false positive) and a supressão
    (negative ``valorAcrescido``) never fires the value flag.

    Args:
        terms: Raw payloads of the terms endpoint. An empty list (HTTP
            204) computes clean flags (0); ``None`` means the query failed
            and computes NULL (insufficient data) for flags and descriptors.

    Returns:
        The flags (1/0/None) and the descriptors (term count, summed
        ``valorAcrescido``, summed ``prazoAditadoDias``, distinct term
        types observed).
    """
    if terms is None:
        return TermFlags(
            f_value_amendment_terms=None,
            f_term_extension_terms=None,
            terms_count=None,
            total_value_increase=None,
            total_days_extended=None,
            term_types=None,
        )

    amendments = [
        term
        for term in terms
        if term.get("tipoTermoContratoNome") == _AMENDMENT_TERM_TYPE
    ]
    f_value_amendment_terms = int(
        any(
            term.get("qualificacaoAcrescimoSupressao") is True
            and (value := _parse_amount(term.get("valorAcrescido"))) is not None
            and value > 0
            for term in amendments
        )
    )
    f_term_extension_terms = int(
        any(
            term.get("qualificacaoVigencia") is True
            and (days := _parse_int(term.get("prazoAditadoDias"))) is not None
            and days > 0
            for term in amendments
        )
    )

    increases = [
        value
        for term in terms
        if (value := _parse_amount(term.get("valorAcrescido"))) is not None
    ]
    days_extended = [
        days
        for term in terms
        if (days := _parse_int(term.get("prazoAditadoDias"))) is not None
    ]
    term_types = sorted(
        {
            str(term["tipoTermoContratoNome"])
            for term in terms
            if term.get("tipoTermoContratoNome")
        }
    )

    return TermFlags(
        f_value_amendment_terms=f_value_amendment_terms,
        f_term_extension_terms=f_term_extension_terms,
        terms_count=len(terms),
        total_value_increase=float(sum(increases)),
        total_days_extended=sum(days_extended),
        term_types=term_types,
    )
