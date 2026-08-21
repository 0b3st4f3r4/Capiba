"""Unit tests for the NLP detection operators.

Responsibility: Validate the semantic gap with mocked
spaCy/sentence-transformers models (no model downloads). The
notice-cloning prototype (``detect_clone``) was replaced by
``capiba.detection.notice_clone`` (PR-D-10), tested in
``tests/test_notice_clone.py``.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from capiba.detection import nlp_operators


@pytest.fixture(autouse=True)
def _reset_model_cache(monkeypatch) -> None:
    """Resets the module-level lazy model cache between tests."""
    monkeypatch.setattr(nlp_operators, "_nlp", None)
    monkeypatch.setattr(nlp_operators, "_encoder", None)


@pytest.fixture
def mock_encoder(monkeypatch) -> MagicMock:
    """Mocks the SentenceTransformer and returns the mocked instance."""
    instance = MagicMock()
    factory = MagicMock(return_value=instance)
    monkeypatch.setattr(nlp_operators, "SentenceTransformer", factory)
    return instance


class TestLazyLoading:
    """Tests for the lazy model loaders."""

    def test_get_nlp_loads_once(self, monkeypatch) -> None:
        """spaCy model must be loaded only on the first call."""
        nlp = MagicMock()
        load = MagicMock(return_value=nlp)
        monkeypatch.setattr(nlp_operators.spacy, "load", load)

        assert nlp_operators._get_nlp() is nlp
        assert nlp_operators._get_nlp() is nlp
        load.assert_called_once_with("pt_core_news_lg")

    def test_get_encoder_loads_once(self, monkeypatch) -> None:
        """SentenceTransformer must be instantiated only on the first call."""
        encoder = MagicMock()
        factory = MagicMock(return_value=encoder)
        monkeypatch.setattr(nlp_operators, "SentenceTransformer", factory)

        assert nlp_operators._get_encoder() is encoder
        assert nlp_operators._get_encoder() is encoder
        factory.assert_called_once_with(
            "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        )


class TestSemanticGap:
    """Tests for semantic_gap."""

    def _patch_cos_sim(self, monkeypatch, value: float) -> None:
        """Makes util.cos_sim return the given scalar similarity."""
        result = MagicMock()
        result.item.return_value = value
        monkeypatch.setattr(
            nlp_operators.util, "cos_sim", MagicMock(return_value=result)
        )

    def test_gap_detected_below_threshold(self, monkeypatch, mock_encoder) -> None:
        """Similarity below the threshold is returned rounded to 4 places."""
        self._patch_cos_sim(monkeypatch, 0.456789)

        score = nlp_operators.semantic_gap("term", "contract", threshold=0.7)

        assert score == pytest.approx(0.4568)
        assert mock_encoder.encode.call_count == 2

    def test_no_gap_above_threshold(self, monkeypatch, mock_encoder) -> None:
        """Similarity above the threshold is returned unchanged."""
        self._patch_cos_sim(monkeypatch, 0.95)

        score = nlp_operators.semantic_gap("term", "contract")

        assert score == pytest.approx(0.95)
