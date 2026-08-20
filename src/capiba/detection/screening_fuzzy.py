"""Fuzzy sanction screening by name + masked document (battery D-06b, O3).

Responsibility: emit the ``sanctioned_name_match`` signal for suppliers of
silver contracts whose **name** matches a silver ``sanctions`` record
vigent at the contract's signature date, under the semantics declared in
``docs/preregistrations/PR-D-06b.md`` (section 3):

- **Document veto**: a sanction document (full or masked) that
  contradicts the supplier's full document forbids the signal, regardless
  of name similarity; a missing document is not a contradiction.
- **Doc-assisted regime** (masked sanction document compatible with the
  supplier's): score = 0.6 * name_sim + 0.4 * doc_match, signal at >=
  0.85.
- **Name-only regime** (no document evidence on either side): score =
  name_sim, signal at >= 0.95.
- **Factual priority**: a supplier with an exact document match on a
  sanction (the ``sanctioned_supplier`` path, PR-D-06) never gets a fuzzy
  signal for that same sanction.

Unlike the factual signal, the fuzzy match is a computed hypothesis (the
score is the similarity, not binary) and always goes through human triage
(O10). Weights and thresholds live in the battery config
(``experiments/detect/D-06b.json``), never only in code.

Dependencies: capiba.detection.entities, capiba.detection.signals
"""

from __future__ import annotations

import json
from typing import Any

from capiba.detection.entities import documents_match, name_similarity
from capiba.detection.screening import _as_date, _vigent_at
from capiba.detection.signals import SignalType

# Pre-registered defaults (PR-D-06b, section 3); the battery config carries
# the authoritative values.
WEIGHT_NAME = 0.6
WEIGHT_MASKED_DOCUMENT = 0.4
DOC_ASSISTED_THRESHOLD = 0.85
NAME_ONLY_THRESHOLD = 0.95


def _full_document(record: dict[str, Any]) -> str | None:
    """The full document of a sanction or supplier row, if any."""
    document = record.get("cnpj") or record.get("cpf")
    return str(document) if document else None


def _documents_contradict(sanction: dict[str, Any], supplier: dict[str, Any]) -> bool:
    """Whether the sanction document contradicts the supplier's full document.

    PR-D-06b § 3: a full sanction document different from the supplier's,
    or a masked sanction document whose visible digits are not contained in
    the supplier's document, vetoes the fuzzy signal. A missing document
    on either side is not a contradiction.
    """
    supplier_doc = _full_document(supplier)
    if supplier_doc is None:
        return False
    sanction_doc = _full_document(sanction)
    if sanction_doc is not None:
        return sanction_doc != supplier_doc
    masked = sanction.get("masked_document")
    if masked:
        return not documents_match(str(masked), supplier_doc)
    return False


def _doc_assisted(sanction: dict[str, Any], supplier: dict[str, Any]) -> bool:
    """Whether the masked sanction document matches the supplier's (PR-D-06b)."""
    masked = sanction.get("masked_document")
    supplier_doc = _full_document(supplier)
    return bool(masked) and supplier_doc is not None and documents_match(
        str(masked), supplier_doc
    )


def fuzzy_match_score(
    sanction: dict[str, Any],
    supplier: dict[str, Any],
    name_weight: float = WEIGHT_NAME,
    document_weight: float = WEIGHT_MASKED_DOCUMENT,
    doc_assisted_threshold: float = DOC_ASSISTED_THRESHOLD,
    name_only_threshold: float = NAME_ONLY_THRESHOLD,
) -> float | None:
    """Scores a (sanction, supplier) pair; None when the pair never signals.

    Returns the fuzzy score when it reaches the threshold of its regime
    (doc-assisted or name-only), or None when the document veto applies,
    the threshold is not met, or a name is missing.
    """
    if _documents_contradict(sanction, supplier):
        return None
    name_sim = name_similarity(
        sanction.get("sanctioned_name"), supplier.get("legal_name")
    )
    if name_sim == 0.0:
        return None
    if _doc_assisted(sanction, supplier):
        score = name_weight * name_sim + document_weight
        return score if score >= doc_assisted_threshold else None
    # Name-only regime: only pairs without any document evidence on the
    # sanction side reach here with the supplier documentless too.
    return name_sim if name_sim >= name_only_threshold else None


def sanctioned_name_match_signals(
    contracts: list[dict[str, Any]],
    sanctions: list[dict[str, Any]],
    name_weight: float = WEIGHT_NAME,
    document_weight: float = WEIGHT_MASKED_DOCUMENT,
    doc_assisted_threshold: float = DOC_ASSISTED_THRESHOLD,
    name_only_threshold: float = NAME_ONLY_THRESHOLD,
) -> list[dict[str, Any]]:
    """Emits one ``sanctioned_name_match`` signal per supplier × list.

    Args:
        contracts: Silver contract rows (``supplier`` with legal_name and
            optional cnpj/cpf, ``signature_date``).
        sanctions: Silver sanction rows (``sanctioned_name``, optional
            ``cnpj``/``cpf``/``masked_document``, ``start_date``,
            ``end_date``, ``list_name``, ``id``).
        name_weight: Weight of the name feature (doc-assisted regime).
        document_weight: Weight of the masked-document feature.
        doc_assisted_threshold: Threshold of the doc-assisted regime.
        name_only_threshold: Threshold of the name-only regime.

    Returns:
        One signal per supplier × list with at least one contract signed
        under a vigent fuzzy-matched sanction; the score is the best match
        score and ``details`` carries the matched sanction ids, the matched
        fields and the affected contract count. Sorted by entity id (bit-
        for-bit determinism).
    """
    # Factual priority (PR-D-06b § 2): suppliers with an exact document
    # match on a sanction never get a fuzzy signal for that sanction.
    exact: set[tuple[str, str]] = set()
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        supplier_doc = _full_document(supplier)
        if supplier_doc is None:
            continue
        for sanction in sanctions:
            if _full_document(sanction) == supplier_doc:
                exact.add((supplier_doc, str(sanction["id"])))

    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        if not supplier.get("legal_name"):
            continue
        signed_on = _as_date(contract.get("signature_date"))
        if signed_on is None:
            continue
        entity_id = _full_document(supplier) or str(supplier["legal_name"])
        for sanction in sanctions:
            if not _vigent_at(sanction, signed_on):
                continue
            if (entity_id, str(sanction["id"])) in exact:
                continue
            score = fuzzy_match_score(
                sanction,
                supplier,
                name_weight=name_weight,
                document_weight=document_weight,
                doc_assisted_threshold=doc_assisted_threshold,
                name_only_threshold=name_only_threshold,
            )
            if score is None:
                continue
            key = (entity_id, str(sanction["list_name"]))
            hit = hits.setdefault(
                key, {"sanctions": set(), "contracts": set(), "score": 0.0}
            )
            hit["sanctions"].add(str(sanction["id"]))
            hit["contracts"].add(str(contract.get("id")))
            hit["score"] = max(hit["score"], score)

    return [
        {
            "entity_type": "supplier",
            "entity_id": entity_id,
            "signal_type": SignalType.SANCTIONED_NAME_MATCH,
            "score": round(hit["score"], 4),
            "details": json.dumps(
                {
                    "sanctions": sorted(hit["sanctions"]),
                    "lists": [list_name],
                    "contracts": len(hit["contracts"]),
                    "match": "fuzzy",
                },
                sort_keys=True,
            ),
        }
        for (entity_id, list_name), hit in sorted(hits.items())
    ]
