"""Fuzzy sanction screening by name + masked document (battery D-06b).

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
score is the similarity, not binary) and always goes through human triage.
Weights and thresholds live in the battery config
(``experiments/detect/D-06b.json``), never only in code.

Dependencies: capiba.detection.entities, capiba.detection.signals
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np

from capiba.detection.entities import documents_match, name_similarity, normalize_name
from capiba.detection.screening import _as_date, _vigent_at
from capiba.detection.signals import SignalType

# Pre-registered defaults (PR-D-06b, section 3); the battery config carries
# the authoritative values.
WEIGHT_NAME = 0.6
WEIGHT_MASKED_DOCUMENT = 0.4
DOC_ASSISTED_THRESHOLD = 0.85
NAME_ONLY_THRESHOLD = 0.95

# Exact-recall prefilter (character-multiset upper bound). The name feature
# is the SequenceMatcher ratio of the normalized names, 2M/(la+lb), and M
# is bounded by the character-multiset overlap (each matched char consumes
# one occurrence from both strings). A pair whose bound stays below the
# ratio its regime requires provably scores None, so it is skipped without
# ever running SequenceMatcher — the bound is vectorized with numpy over
# the candidate set. Without it the screening is O(suppliers x sanctions)
# at ~50 us per pair: 570M pairs on real volume (2026-08-21: 70k documented
# suppliers x 8k documentless sanctions) would take hours.
_BOUND_EPS = 1e-9
_CHAR_TO_IDX = (
    {chr(ord("A") + i): i for i in range(26)}
    | {str(d): 26 + d for d in range(10)}
    | {" ": 36}
)


def _name_vector(normalized: str) -> np.ndarray:
    """Character counts of a normalized name (only [A-Z0-9 ] survive it)."""
    vector = np.zeros(len(_CHAR_TO_IDX), dtype=np.int64)
    for char in normalized:
        vector[_CHAR_TO_IDX[char]] += 1
    return vector


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
    # match on a sanction never get a fuzzy signal for that sanction. The
    # lookup is indexed by document — the old contracts × sanctions scan
    # is O(N·M) and does not finish on real volume (205k × 37k).
    sanctions_by_document: dict[str, list[dict[str, Any]]] = {}
    docless_indexes_list: list[int] = []  # masked document or none
    for index, sanction in enumerate(sanctions):
        document = _full_document(sanction)
        if document is None:
            docless_indexes_list.append(index)
        else:
            sanctions_by_document.setdefault(document, []).append(sanction)

    exact: set[tuple[str, str]] = set()
    # entity_id -> variant key -> {"supplier", "contracts"}: the same
    # entity (document or bare name) can appear with different supplier
    # payloads across contracts, and the naive semantics scores each
    # contract with its own supplier row — variants keep that exact.
    suppliers: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        supplier_doc = _full_document(supplier)
        if supplier_doc is not None:
            for sanction in sanctions_by_document.get(supplier_doc, []):
                exact.add((supplier_doc, str(sanction["id"])))
        if not supplier.get("legal_name"):
            continue
        signed_on = _as_date(contract.get("signature_date"))
        if signed_on is None:
            continue
        entity_id = supplier_doc or str(supplier["legal_name"])
        variants = suppliers.setdefault(entity_id, {})
        variant = variants.setdefault(
            json.dumps(supplier, sort_keys=True, default=str),
            {"supplier": supplier, "contracts": []},
        )
        variant["contracts"].append((str(contract.get("id")), signed_on))

    # Candidate restriction (same result, no full cross product): when the
    # supplier has a full document, any sanction with a different full
    # document is a veto (PR-D-06b § 3) and the same document is an exact
    # match — only documentless sanctions (masked or none) can signal. A
    # documentless supplier is never vetoed, so every sanction is a
    # candidate. Within the candidate set, the character-multiset bound
    # (see top of module) drops the pairs that provably cannot reach the
    # ratio their regime requires — exact recall, no SequenceMatcher run.
    norm_names = [normalize_name(sanction.get("sanctioned_name")) for sanction in sanctions]
    name_vectors = (
        np.stack([_name_vector(name) for name in norm_names])
        if sanctions
        else np.zeros((0, len(_CHAR_TO_IDX)), dtype=np.int64)
    )
    name_lengths = np.array([len(name) for name in norm_names], dtype=np.int64)
    has_masked = np.array(
        [bool(sanction.get("masked_document")) for sanction in sanctions]
    )
    all_indexes = np.arange(len(sanctions))
    docless_indexes = np.array(docless_indexes_list, dtype=np.int64)
    # Doc-assisted pairs score 0.6 * name_sim + 0.4 and emit at >= 0.85, so
    # they require name_sim >= 0.75 with the default weights; every other
    # pair is name-only and requires name_sim >= name_only_threshold.
    required_doc = (
        max((doc_assisted_threshold - document_weight) / name_weight, 0.0)
        if name_weight > 0
        else 0.0
    )
    bound_cache: dict[tuple[str, bool], np.ndarray] = {}

    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for entity_id, variants in suppliers.items():
        for variant in variants.values():
            supplier = variant["supplier"]
            supplier_norm = normalize_name(supplier.get("legal_name"))
            if not supplier_norm:
                continue
            docless_supplier = _full_document(supplier) is None
            cache_key = (supplier_norm, docless_supplier)
            survivor_indexes = bound_cache.get(cache_key)
            if survivor_indexes is None:
                indexes = all_indexes if docless_supplier else docless_indexes
                bound = (
                    2.0
                    * np.minimum(_name_vector(supplier_norm), name_vectors[indexes]).sum(
                        axis=1
                    )
                    / (len(supplier_norm) + name_lengths[indexes])
                )
                if docless_supplier:
                    required = np.full(len(indexes), name_only_threshold)
                else:
                    required = np.where(
                        has_masked[indexes], required_doc, name_only_threshold
                    )
                survivor_indexes = indexes[bound >= required - _BOUND_EPS]
                bound_cache[cache_key] = survivor_indexes
            for index in survivor_indexes:
                sanction = sanctions[int(index)]
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
                matched = [
                    contract_id
                    for contract_id, signed_on in variant["contracts"]
                    if _vigent_at(sanction, signed_on)
                ]
                if not matched:
                    continue
                key = (entity_id, str(sanction["list_name"]))
                hit = hits.setdefault(
                    key, {"sanctions": set(), "contracts": set(), "score": 0.0}
                )
                hit["sanctions"].add(str(sanction["id"]))
                hit["contracts"].update(matched)
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
