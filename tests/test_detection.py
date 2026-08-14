"""Tests for the detection vertical slice.

Responsibility: Validate statistical, ML, graph and NLP operators.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from capiba.detection.statistical import (
    benford_score,
    duration_outlier,
    hhi_index,
    single_bid_rate,
)
from capiba.pipeline.tasks import detect_fraud_signals


class TestStatistical:
    """Tests for statistical operators."""

    def test_benford_score_conformant(self) -> None:
        """Values following Benford should have a high score."""
        values = [
            123.45,
            234.56,
            345.67,
            456.78,
            567.89,
            678.90,
            789.01,
            890.12,
            901.23,
            112.34,
            223.45,
            334.56,
            445.67,
            556.78,
            667.89,
            778.90,
            889.01,
            990.12,
            101.23,
            212.34,
        ]
        series = pd.Series(values)
        score = benford_score(series)
        assert score > 0.5  # p-value > 0.05 indicates conformance

    def test_benford_score_empty(self) -> None:
        """Empty series must return NaN."""
        score = benford_score(pd.Series([]))
        assert np.isnan(score)

    def test_benford_score_invalid(self) -> None:
        """Values <= 0 must return NaN."""
        score = benford_score(pd.Series([-10, 0, -5]))
        assert np.isnan(score)

    def test_single_bid_rate(self) -> None:
        """Rate must be 2/4 = 0.5 for the sample fixture."""
        bids = [
            {"id": "L001", "num_participants": 1, "amount": 10000.00},
            {"id": "L002", "num_participants": 3, "amount": 25000.00},
            {"id": "L003", "num_participants": 1, "amount": 5000.00},
            {"id": "L004", "num_participants": 5, "amount": 100000.00},
        ]
        df = pd.DataFrame(bids)
        rate = single_bid_rate(df)
        assert rate == pytest.approx(0.5, abs=0.01)

    def test_single_bid_empty(self) -> None:
        """Empty DataFrame must return 0.0."""
        rate = single_bid_rate(pd.DataFrame())
        assert rate == 0.0

    def test_hhi_concentration(self) -> None:
        """HHI for a buyer with 2 suppliers."""
        df = pd.DataFrame(
            [
                {"buyer_id": "C001", "supplier_id": "F001", "amount": 10000},
                {"buyer_id": "C001", "supplier_id": "F002", "amount": 10000},
            ]
        )
        hhi = hhi_index("C001", df)
        # Market shares: 0.5, 0.5 → HHI = 0.25 + 0.25 = 0.5
        assert hhi == pytest.approx(0.5, abs=0.01)

    def test_duration_outlier_iqr(self) -> None:
        """Must detect outlier via IQR."""
        df = pd.DataFrame(
            {
                "duration_days": [10, 12, 11, 13, 100],  # 100 is an outlier
            }
        )
        outliers = duration_outlier(df, method="iqr")
        assert outliers.iloc[-1]
        assert outliers.iloc[:-1].sum() == 0

    def test_duration_outlier_zscore(self) -> None:
        """Must detect outlier via z-score."""
        df = pd.DataFrame(
            {
                "duration_days": [
                    10,
                    10,
                    10,
                    10,
                    10,
                    10,
                    10,
                    10,
                    10,
                    10,
                    100,
                ],  # 100 is an outlier
            }
        )
        outliers = duration_outlier(df, method="zscore")
        assert outliers.iloc[-1]


class TestMLModels:
    """Tests for machine learning models."""

    def test_train_rf_basic(self) -> None:
        """Random Forest must be trainable with synthetic data."""
        from capiba.detection.ml_models import train_rf

        features = pd.DataFrame(
            {
                "feature_a": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                "feature_b": [10, 9, 8, 7, 6, 5, 4, 3, 2, 1],
            }
        )
        y = pd.Series([0, 0, 0, 0, 0, 1, 1, 1, 1, 1])

        model = train_rf(features, y, n_estimators=10)
        assert model is not None
        assert hasattr(model, "predict")

    def test_train_if_basic(self) -> None:
        """Isolation Forest must be trainable with synthetic data."""
        from capiba.detection.ml_models import train_if

        features = pd.DataFrame(
            {
                "feature_a": [1, 2, 3, 4, 5, 100],  # 100 is an anomaly
                "feature_b": [1, 2, 3, 4, 5, 100],
            }
        )

        model = train_if(features, contamination=0.1, n_estimators=10)
        assert model is not None
        assert hasattr(model, "predict")


@pytest.mark.integration
class TestGraphs:
    """Tests for network analysis via ArangoDB."""

    @pytest.fixture
    def graphs_db(self):
        """Prepares an ArangoDB database with test data."""
        from capiba.db.arangodb import (
            ensure_collections,
            ensure_database,
            ensure_graph,
            upsert_edge,
            upsert_vertex,
        )

        db = ensure_database()
        ensure_collections(db)
        ensure_graph(db)

        # Test data for collusion
        for name in ("A", "B", "C"):
            upsert_vertex(db, "suppliers", name, {"name": name})
        for name in ("L1", "L2"):
            upsert_vertex(db, "bids", name, {"subject": name})

        # A and B won two bids together
        upsert_edge(db, "won", "suppliers/A", "bids/L1")
        upsert_edge(db, "won", "suppliers/B", "bids/L1")
        upsert_edge(db, "won", "suppliers/A", "bids/L2")
        upsert_edge(db, "won", "suppliers/B", "bids/L2")

        # Test data for ownership chain
        upsert_vertex(db, "companies", "E001", {"name": "Holding"})
        upsert_vertex(db, "companies", "E002", {"name": "Subsidiary"})
        upsert_edge(db, "owns", "companies/E001", "companies/E002")

        return db

    def test_detect_collusion(self, graphs_db) -> None:
        """Must detect a collusion pattern between suppliers."""
        from capiba.detection.graphs import detect_collusion

        suspects = detect_collusion(db=graphs_db, min_wins=1)
        assert len(suspects) > 0
        assert {"A", "B"} in suspects

    def test_trace_ownership(self, graphs_db) -> None:
        """Must trace the ownership chain."""
        from capiba.detection.graphs import trace_ownership

        paths = trace_ownership("E001", max_depth=2, db=graphs_db)
        assert len(paths) > 0
        assert "E002" in paths[0]


@pytest.mark.integration
class TestVectorStore:
    """Tests for the vector store in ArangoDB."""

    def test_upsert_and_search_similar(self) -> None:
        """Must store and retrieve vectors by similarity."""
        from capiba.db.vectors import (
            delete_vector,
            search_similar,
            upsert_vector,
        )

        collection_name = "test_documents"
        upsert_vector(
            "v1",
            [1.0, 0.0, 0.0],
            {"title": "Doc 1"},
            collection_name=collection_name,
        )
        upsert_vector(
            "v2",
            [0.9, 0.1, 0.0],
            {"title": "Doc 2"},
            collection_name=collection_name,
        )
        upsert_vector(
            "v3",
            [0.0, 1.0, 0.0],
            {"title": "Doc 3"},
            collection_name=collection_name,
        )

        results = search_similar(
            [1.0, 0.0, 0.0],
            top_k=2,
            collection_name=collection_name,
        )

        assert len(results) == 2
        assert results[0]["external_id"] == "v1"
        assert results[0]["similarity"] == pytest.approx(1.0, abs=0.01)

        # Cleanup
        for vid in ("v1", "v2", "v3"):
            assert delete_vector(vid, collection_name=collection_name)

    def test_delete_vector(self) -> None:
        """Must remove a vector by identifier."""
        from capiba.db.vectors import delete_vector, upsert_vector

        collection_name = "test_delete"
        upsert_vector("del1", [1.0, 0.0], collection_name=collection_name)
        assert delete_vector("del1", collection_name=collection_name)
        assert not delete_vector("missing", collection_name=collection_name)


@pytest.mark.integration
class TestFullTextSearch:
    """Tests for full-text search in ArangoDB (ArangoSearch)."""

    def test_index_and_search_document(self) -> None:
        """Must index and find documents by text."""
        import time

        from capiba.db.search import (
            delete_document,
            ensure_search_view,
            index_document,
            search_text,
        )

        collection_name = "test_search_documents"
        view_name = "test_search_view"

        # Ensures the view exists before indexing the documents
        ensure_search_view(db=None, collection=collection_name, view=view_name)

        index_document(
            "doc1",
            "Edital de licitação para compra de equipamentos",
            title="Edital 001",
            db=None,
            collection=collection_name,
        )
        index_document(
            "doc2",
            "Contrato de prestação de serviços",
            title="Contrato 002",
            db=None,
            collection=collection_name,
        )

        # Waits for the view to reflect the changes
        time.sleep(2)

        results = search_text(
            "licitação",
            top_k=5,
            db=None,
            view=view_name,
            collection=collection_name,
        )

        assert len(results) == 1
        assert results[0]["external_id"] == "doc1"

        for doc_id in ("doc1", "doc2"):
            assert delete_document(doc_id, db=None, collection=collection_name)

    def test_delete_search_document(self) -> None:
        """Must remove a document from the index."""
        from capiba.db.search import delete_document, index_document

        collection_name = "test_search_delete"
        index_document("del1", "Texto de teste", db=None, collection=collection_name)
        assert delete_document("del1", db=None, collection=collection_name)
        assert not delete_document("missing", db=None, collection=collection_name)


class TestComputeCRI:
    """Tests for the Composite Risk Index."""

    @staticmethod
    def _contract() -> pd.Series:
        """Contract with the five CRI signal features."""
        return pd.Series(
            {
                "single_bid": 1,
                "short_submission_window": 0,
                "irregular_timeline": 1,
                "non_competitive": 0,
                "high_concentration": 1,
            }
        )

    def test_returns_rf_fraud_probability(self) -> None:
        """CRI must be the RF probability of the fraud class, rounded."""
        from unittest.mock import MagicMock

        from capiba.detection.ml_models import compute_cri

        rf = MagicMock()
        rf.predict_proba.return_value = np.array([[0.25, 0.75678]])

        cri = compute_cri(self._contract(), {"random_forest": rf})

        assert cri == pytest.approx(0.7568)
        features = rf.predict_proba.call_args.args[0]
        assert features.shape == (1, 5)

    def test_missing_rf_raises(self) -> None:
        """Missing Random Forest model must raise ValueError."""
        from capiba.detection.ml_models import compute_cri

        with pytest.raises(ValueError, match="Random Forest model not found"):
            compute_cri(self._contract(), {})


class TestFraudSignals:
    """Tests for the batch signal computation over silver contracts."""

    @staticmethod
    def _contracts(n: int = 12) -> list[dict]:
        """Synthetic contracts: one supplier, one buyer, skewed amounts."""
        return [
            {
                "id": f"c{i}",
                "amount": 9000.0 + i,  # leading digit always 9 (anti-Benford)
                "signature_date": "2026-01-10",
                "validity_start": "2026-01-10",
                "validity_end": "2026-07-10",
                "buyer": {"siafi_code": "26000", "name": "Agency"},
                "supplier": {"cnpj": "12345678000199", "legal_name": "ACME"},
            }
            for i in range(n)
        ]

    def test_empty_contracts(self) -> None:
        """No contracts must produce no signals."""
        assert detect_fraud_signals([]) == []

    def test_emits_supplier_and_buyer_signals(self) -> None:
        """Benford deviation, concentration and duration signals are emitted."""
        signals = detect_fraud_signals(self._contracts())
        kinds = {(s["entity_type"], s["signal_type"]) for s in signals}

        assert ("supplier", "benford_deviation") in kinds
        assert ("buyer", "supplier_concentration") in kinds

        benford = next(s for s in signals if s["signal_type"] == "benford_deviation")
        assert benford["entity_id"] == "12345678000199"
        assert benford["score"] > 0.5  # skewed leading digits -> suspicious

        hhi = next(s for s in signals if s["signal_type"] == "supplier_concentration")
        assert hhi["entity_id"] == "26000"
        assert hhi["score"] == 1.0  # single supplier -> total concentration

    def test_duration_outlier_flagged(self) -> None:
        """A contract with an anomalous duration raises the outlier share."""
        contracts = self._contracts()
        for i, c in enumerate(contracts):
            c["validity_end"] = "2026-02-10" if i else "2036-01-10"

        signals = detect_fraud_signals(contracts)
        shares = [s for s in signals if s["signal_type"] == "duration_outlier_share"]

        assert len(shares) == 1
        assert 0 < shares[0]["score"] <= 1.0
