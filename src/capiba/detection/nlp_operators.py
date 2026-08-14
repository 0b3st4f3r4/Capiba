"""NLP operators for analyzing notices and terms of reference.

Chunks: semantic_gap, notice_clone, exclusivity_violated
Responsibility: Detect scope under-declaration, notice cloning
and exclusivity violation via linguistic processing.

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


def detect_clone(
    new_notice: str,
    historical_notices: list[str],
    threshold: float = 0.85,
) -> list[tuple[str, float]]:
    """Detects notice cloning via embedding similarity.

    Args:
        new_notice: Text of the notice to check.
        historical_notices: List of texts from previous notices.
        threshold: Similarity threshold to flag as a clone.

    Returns:
        List of tuples (similar_notice, score) above the threshold.
    """
    encoder = _get_encoder()

    new_emb = encoder.encode(new_notice, convert_to_tensor=True)
    historical_embs = encoder.encode(historical_notices, convert_to_tensor=True)

    similarities = util.cos_sim(new_emb, historical_embs)[0]

    clones = [
        (historical_notices[i], float(sim))
        for i, sim in enumerate(similarities)
        if sim > threshold
    ]

    clones.sort(key=lambda x: x[1], reverse=True)
    return clones
