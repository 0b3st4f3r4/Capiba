"""Sanction screening by exact document match (battery D-06).

Responsibility: emit the ``sanctioned_supplier`` signal for suppliers of
silver contracts whose CNPJ/CPF matches a silver ``sanctions`` record
(CEIS/CNEP lists) vigent at the contract's signature date, in the
semantics declared in ``docs/preregistrations/PR-D-06.md`` (section 3):
inclusive bounds, NULL end date means open vigence, a missing start date
is not computable, and a name match is never a match. The score is
binary (1.0) — the match is factual, not probabilistic.

Dependencies: capiba.detection.signals
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any

from capiba.detection.signals import SignalType


def _as_date(value: Any) -> date | None:
    """Coerces an ISO string or date to a date; anything else is None."""
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


def _vigent_at(sanction: dict[str, Any], day: date) -> bool:
    """Whether the sanction was in force on the given day (PR-D-06 § 3)."""
    start = _as_date(sanction.get("start_date"))
    if start is None or start > day:
        return False
    end = _as_date(sanction.get("end_date"))
    return end is None or day <= end


def sanctioned_supplier_signals(
    contracts: list[dict[str, Any]], sanctions: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Emits one ``sanctioned_supplier`` signal per sanctioned supplier.

    Args:
        contracts: Silver contract rows (``supplier`` with cnpj/cpf,
            ``signature_date``).
        sanctions: Silver sanction rows (``cnpj``/``cpf``, ``start_date``,
            ``end_date``, ``list_name``, ``id``).

    Returns:
        One signal per supplier with at least one contract signed under a
        vigent sanction, sorted by entity id (bit-for-bit determinism).
    """
    by_document: dict[str, list[dict[str, Any]]] = {}
    for sanction in sanctions:
        for document in (sanction.get("cnpj"), sanction.get("cpf")):
            if document:
                by_document.setdefault(str(document), []).append(sanction)

    hits: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        document = supplier.get("cnpj") or supplier.get("cpf")
        if not document:
            continue
        candidates = by_document.get(str(document))
        if not candidates:
            continue
        signed_on = _as_date(contract.get("signature_date"))
        if signed_on is None:
            continue
        matched = [s for s in candidates if _vigent_at(s, signed_on)]
        if not matched:
            continue
        hit = hits.setdefault(
            str(document), {"sanctions": set(), "lists": set(), "contracts": set()}
        )
        hit["sanctions"].update(str(s["id"]) for s in matched)
        hit["lists"].update(str(s["list_name"]) for s in matched)
        hit["contracts"].add(str(contract.get("id")))

    return [
        {
            "entity_type": "supplier",
            "entity_id": document,
            "signal_type": SignalType.SANCTIONED_SUPPLIER,
            "score": 1.0,
            "details": json.dumps(
                {
                    "sanctions": sorted(hit["sanctions"]),
                    "lists": sorted(hit["lists"]),
                    "contracts": len(hit["contracts"]),
                },
                sort_keys=True,
            ),
        }
        for document, hit in sorted(hits.items())
    ]
