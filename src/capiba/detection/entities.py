"""Entity resolution matchers (O5).

Chunk: detection
Responsibility: Score whether two records refer to the same real-world
entity — partners (sócios PF) across companies and the deterministic
supplier↔company link — under the pre-registered semantics of
``docs/preregistrations/PR-D-07.md``.

Conservative by design: the score is a plain sum of satisfied feature
weights (name 0.6, document 0.3, age range 0.1), so a name alone caps at
0.6 and never reaches the merge threshold (0.85). Weights and threshold
live in the battery config, never only in code.

Dependencies: none beyond the stdlib.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

logger = logging.getLogger(__name__)

# Pre-registered defaults (PR-D-07, section 3); the battery config carries
# the authoritative values.
WEIGHT_NAME = 0.6
WEIGHT_DOCUMENT = 0.3
WEIGHT_AGE_RANGE = 0.1
DEFAULT_THRESHOLD = 0.85


def normalize_name(name: str | None) -> str:
    """Normalizes a name: uppercase, no accents/punctuation, sorted tokens."""
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Z0-9 ]", " ", text.upper())
    return " ".join(sorted(text.split()))


def name_similarity(a: str | None, b: str | None) -> float:
    """SequenceMatcher ratio over the normalized names (0.0-1.0)."""
    na, nb = normalize_name(a), normalize_name(b)
    if not na or not nb:
        return 0.0
    return SequenceMatcher(None, na, nb).ratio()


def _document_digits(document: Any) -> str:
    """Keeps only the visible digits of a (possibly masked) document."""
    return re.sub(r"\D", "", str(document or ""))


def documents_match(a: Any, b: Any) -> bool:
    """Whether two documents are compatible.

    The public dump masks documents (``***123456**``): the visible digits
    of the shorter one must appear in the longer one (a masked CPF matches
    the full CNPJ/CPF carrying those digits). Empty documents never match.
    """
    da, db = _document_digits(a), _document_digits(b)
    if not da or not db:
        return False
    shorter, longer = (da, db) if len(da) <= len(db) else (db, da)
    return shorter in longer


def _field(record: dict[str, Any], *names: str) -> Any:
    """Reads the first non-empty field among the aliases."""
    for name in names:
        value = record.get(name)
        if value not in (None, ""):
            return value
    return None


def score_person_pair(
    a: dict[str, Any],
    b: dict[str, Any],
    *,
    name_weight: float = WEIGHT_NAME,
    document_weight: float = WEIGHT_DOCUMENT,
    age_range_weight: float = WEIGHT_AGE_RANGE,
) -> float:
    """Scores a person pair under the pre-registered features (PR-D-07).

    Field aliases cover both regimes: the silver partner row
    (``nome``/``cnpj_cpf_socio``/``faixa_etaria``) and the OpenSanctions
    FtM entity flattened by the battery (``name``/``id_number``).

    Returns:
        Score in [0, 1]: name similarity × 0.6 + document match × 0.3 +
        equal age range × 0.1.
    """
    score = name_weight * name_similarity(
        _field(a, "nome", "name"), _field(b, "nome", "name")
    )
    if documents_match(
        _field(a, "cnpj_cpf_socio", "id_number"),
        _field(b, "cnpj_cpf_socio", "id_number"),
    ):
        score += document_weight
    age_a, age_b = _field(a, "faixa_etaria"), _field(b, "faixa_etaria")
    if age_a is not None and age_a == age_b:
        score += age_range_weight
    return round(score, 6)


def is_merge(score: float, threshold: float = DEFAULT_THRESHOLD) -> bool:
    """Whether a score clears the pre-registered merge threshold."""
    return score >= threshold


def link_supplier_company(supplier_cnpj: str | None, cnpj_basico: str | None) -> float:
    """Deterministic supplier↔company link: 1.0 on document match.

    A 14-digit supplier CNPJ matches the company when its first 8 digits
    equal the ``cnpj_basico``; a supplier without a full CNPJ never links
    in this slice (PR-D-07, E8).
    """
    digits = _document_digits(supplier_cnpj)
    if len(digits) == 14 and cnpj_basico and digits[:8] == cnpj_basico:
        return 1.0
    return 0.0


def resolve_entities(
    db: Any | None = None,
    threshold: float = DEFAULT_THRESHOLD,
) -> dict[str, Any]:
    """Writes ``same_as`` edges between person vertices above the threshold.

    Reads the ``persons`` vertices, blocks the candidates by shared
    visible document digits and by the first normalized name token (to
    avoid the O(n²) cross-product) and scores the pairs within each block.
    Pairs at or above ``threshold`` become a ``same_as`` edge carrying the
    ``score`` and the matched fields (``details``) — a computed,
    reversible hypothesis: no vertex is ever collapsed (PR-D-07, P8).

    Args:
        db: ArangoDB connection. If None, creates a new one.
        threshold: Merge threshold (pre-registered default 0.85).

    Returns:
        Summary ``{persons, candidate_pairs, same_as, threshold}``.
    """
    import json
    from itertools import combinations

    from capiba.db.arangodb import execute_aql, get_capiba_db, upsert_edge

    if db is None:
        db = get_capiba_db()

    rows = execute_aql(
        db,
        """
        FOR p IN persons
            RETURN {
                _key: p._key,
                nome: p.nome,
                cnpj_cpf_socio: p.cnpj_cpf_socio,
                faixa_etaria: p.faixa_etaria
            }
        """,
    )
    persons = {row["_key"]: row for row in rows}

    blocks: dict[str, list[str]] = {}
    for row in persons.values():
        keys = []
        digits = _document_digits(row.get("cnpj_cpf_socio"))
        if digits:
            keys.append(f"doc:{digits}")
        tokens = normalize_name(row.get("nome")).split()
        if tokens:
            keys.append(f"tok:{tokens[0]}")
        for key in keys:
            blocks.setdefault(key, []).append(row["_key"])

    candidate_pairs = 0
    edges = 0
    seen: set[tuple[str, str]] = set()
    for block in blocks.values():
        for key_a, key_b in combinations(sorted(set(block)), 2):
            if (key_a, key_b) in seen:
                continue
            seen.add((key_a, key_b))
            candidate_pairs += 1
            a, b = persons[key_a], persons[key_b]
            score = score_person_pair(a, b)
            if not is_merge(score, threshold):
                continue
            details = json.dumps(
                {
                    "score": score,
                    "source_rows": [key_a, key_b],
                    "features": {
                        "name": name_similarity(a.get("nome"), b.get("nome")),
                        "document": documents_match(
                            a.get("cnpj_cpf_socio"), b.get("cnpj_cpf_socio")
                        ),
                        "age_range": (
                            a.get("faixa_etaria") is not None
                            and a.get("faixa_etaria") == b.get("faixa_etaria")
                        ),
                    },
                }
            )
            upsert_edge(
                db,
                "same_as",
                f"persons/{key_a}",
                f"persons/{key_b}",
                {"score": score, "details": details},
            )
            edges += 1

    summary = {
        "persons": len(persons),
        "candidate_pairs": candidate_pairs,
        "same_as": edges,
        "threshold": threshold,
    }
    logger.info("Entity resolution finished: %s", summary)
    return summary
