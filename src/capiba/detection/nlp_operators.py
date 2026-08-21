"""NLP operators for analyzing notices and terms of reference.

Chunks: semantic_gap, exclusivity_violated
Responsibility: Detect scope under-declaration and exclusivity violation
via linguistic processing.

The notice-cloning prototype (``detect_clone``) was replaced by the
production signal ``capiba.detection.notice_clone`` under the PR-D-10
semantics (strict threshold, reedition veto, null discipline) — see
docs/preregistrations/PR-D-10.md.

Dependencies: spacy, sentence-transformers
"""

from __future__ import annotations

import logging

import spacy
from sentence_transformers import SentenceTransformer, util
from spacy.language import Language

logger = logging.getLogger(__name__)

# Load models (lazy loading)
_nlp: Language | None = None
_encoder: SentenceTransformer | None = None


def _get_nlp() -> Language:
    global _nlp
    if _nlp is None:
        _nlp = spacy.load("pt_core_news_lg")
    return _nlp


def _get_encoder() -> SentenceTransformer:
    global _encoder
    if _encoder is None:
        _encoder = SentenceTransformer(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )
    return _encoder


def semantic_gap(
    reference_term: str,
    executed_contract: str,
    threshold: float = 0.7,
) -> float:
    """Computes the semantic gap between term of reference and contract.

    Detects scope under-declaration: what was promised
    in the notice vs. what was effectively delivered.

    Args:
        reference_term: Text of the term of reference/notice.
        executed_contract: Text of the executed contract/deliverable.
        threshold: Similarity threshold (below = gap detected).

    Returns:
        Similarity score (0-1). Values < threshold indicate a gap.
    """
    encoder = _get_encoder()

    emb1 = encoder.encode(reference_term, convert_to_tensor=True)
    emb2 = encoder.encode(executed_contract, convert_to_tensor=True)

    similarity = util.cos_sim(emb1, emb2).item()

    if similarity < threshold:
        logger.warning("Semantic gap detected: %.3f < %.3f", similarity, threshold)

    return round(similarity, 4)
