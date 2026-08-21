"""PEP screening adapter: Capiba suppliers -> yente FtM matching (battery D-12).

Responsibility: build the FollowTheMoney match query for each distinct
individual (``cpf``) supplier of silver contracts and reduce the yente
``/match/<dataset>`` response into ``pep_supplier_match`` signals, under the
semantics declared in ``docs/preregistrations/PR-D-12.md`` (section 3):

- Query-by-example FtM: ``{"schema": "Person", "properties": {"name":
  [legal_name], "idNumber": [cpf], "nationality": ["br"]}}`` — the same
  shape exported by ``capiba.db.ftm``. Individual suppliers without a CPF
  query name-only (no ``idNumber``); companies (``cnpj``) and nameless
  suppliers never query (a company is not a PEP).
- Emission: one signal per PF supplier x dataset with at least one
  candidate at or above the threshold; the score is the best candidate
  score and ``details`` carries the archived query, the OpenSanctions ids,
  the returned names/positions and the individual scores.
- PEP is a statutory condition, not an offense: the signal is a computed
  hypothesis and is always born ``pending_review`` (editorial triage).

The adapter is pure: the yente client is injected as ``match_fn(query) ->
candidates`` so the battery runs offline with a stubbed backend. Version,
algorithm, threshold and dataset live in ``experiments/detect/D-12.json``,
never only in code.

Dependencies: capiba.detection.signals
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from capiba.detection.signals import SignalType

# Pre-registered defaults (PR-D-12, section 3); the battery config carries
# the authoritative values.
DEFAULT_DATASET = "br_pep"
DEFAULT_THRESHOLD = 0.7

# A single supplier match: the injected client receives the FtM query and
# returns the candidate entities (``id``, ``score``, ``properties``) of one
# yente ``/match/<dataset>`` response.
MatchFn = Callable[[dict[str, Any]], list[dict[str, Any]]]


def build_match_query(supplier: dict[str, Any]) -> dict[str, Any] | None:
    """Builds the FtM match query for an individual supplier; None otherwise.

    Companies (supplier with ``cnpj``) and nameless suppliers return None —
    a company is not a PEP and a nameless query would match everything.
    """
    name = supplier.get("legal_name")
    if not name:
        return None
    if supplier.get("cnpj"):
        return None
    properties: dict[str, list[str]] = {"name": [str(name)], "nationality": ["br"]}
    cpf = supplier.get("cpf")
    if cpf:
        properties["idNumber"] = [str(cpf)]
    return {"schema": "Person", "properties": properties}


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    """The archived view of one yente candidate (id, name, position, score)."""
    properties = candidate.get("properties") or {}
    names = properties.get("name") or []
    positions = properties.get("position") or []
    summary: dict[str, Any] = {
        "id": str(candidate.get("id")),
        "score": float(candidate.get("score", 0.0)),
    }
    if names:
        summary["name"] = str(names[0])
    if positions:
        summary["positions"] = [str(position) for position in positions]
    return summary


def reduce_candidates(
    query: dict[str, Any],
    entity_id: str,
    candidates: list[dict[str, Any]],
    threshold: float = DEFAULT_THRESHOLD,
    dataset: str = DEFAULT_DATASET,
) -> dict[str, Any] | None:
    """Reduces one yente response into a signal; None below the threshold.

    The signal exists if and only if at least one candidate scored at or
    above the threshold (PR-D-12 § 6, composition invariant); the archived
    ``query`` in ``details`` makes the emission reproducible by re-running
    the archived query against the pinned dataset snapshot.
    """
    matched = [
        candidate
        for candidate in candidates
        if float(candidate.get("score", 0.0)) >= threshold
    ]
    if not matched:
        return None
    matched.sort(key=lambda candidate: float(candidate.get("score", 0.0)), reverse=True)
    details = {
        "dataset": dataset,
        "threshold": threshold,
        "query": query,
        "candidates": [_candidate_summary(candidate) for candidate in matched],
    }
    return {
        "entity_type": "supplier",
        "entity_id": entity_id,
        "signal_type": SignalType.PEP_SUPPLIER_MATCH,
        "score": round(float(matched[0].get("score", 0.0)), 4),
        "details": json.dumps(details, sort_keys=True),
    }


def pep_supplier_match_signals(
    contracts: list[dict[str, Any]],
    match_fn: MatchFn,
    threshold: float = DEFAULT_THRESHOLD,
    dataset: str = DEFAULT_DATASET,
) -> list[dict[str, Any]]:
    """Emits one ``pep_supplier_match`` signal per distinct PF supplier.

    Args:
        contracts: Silver contract rows (``supplier`` with ``legal_name``
            and optional ``cpf``/``cnpj``).
        match_fn: Injected yente client — receives the FtM query, returns
            the candidate entities of one ``/match/<dataset>`` response.
        threshold: Minimum candidate score for the signal (PR-D-12 § 3).
        dataset: OpenSanctions dataset screened against.

    Returns:
        One signal per distinct PF supplier with at least one candidate at
        or above the threshold. The same supplier in N contracts queries
        once and signals at most once (dedup by entity id). Sorted by
        entity id (bit-for-bit determinism).
    """
    suppliers: dict[str, dict[str, Any]] = {}
    for contract in contracts:
        supplier = contract.get("supplier") or {}
        query = build_match_query(supplier)
        if query is None:
            continue
        cpf = supplier.get("cpf")
        entity_id = str(cpf) if cpf else str(supplier["legal_name"])
        suppliers.setdefault(entity_id, supplier)

    signals: list[dict[str, Any]] = []
    for entity_id, supplier in sorted(suppliers.items()):
        query = build_match_query(supplier)
        if query is None:  # pragma: no cover — guarded above
            continue
        signal = reduce_candidates(
            query, entity_id, match_fn(query), threshold=threshold, dataset=dataset
        )
        if signal is not None:
            signals.append(signal)
    return signals
