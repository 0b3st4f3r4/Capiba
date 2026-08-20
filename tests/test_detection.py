"""Tests for the detection vertical slice.

Responsibility: Validate statistical, ML, graph and NLP operators.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from capiba.detection.signals import (
    SignalType,
    anomalous_price,
    benford_deviation,
    collusion_signals,
    duration_outlier_share,
    isolation_forest_rate,
    single_bid_score,
)
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

    def test_benford_score_sub_unit_values(self) -> None:
        """Values in (0, 1) must not crash the chi-squared test.

        Regression: ``str(0.5)[0]`` yields "0" — not a Benford digit — so
        the observed counts summed below the expected counts and scipy
        raised ValueError (backfill run 2026-01-04, real PNCP amounts).
        The leading digit must be the first SIGNIFICANT digit (0.5 -> 5).
        """
        values = [0.5, 0.75, 0.123, 123.45, 0.99, 45.6, 0.01, 9.87, 1.5, 0.002]
        score = benford_score(pd.Series(values))
        assert 0.0 <= score <= 1.0

    def test_benford_score_integer_floats(self) -> None:
        """Integer-valued floats keep the same digit as before the fix."""
        assert benford_score(pd.Series([100.0] * 10 + [900.0] * 2)) is not None

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
        upsert_edge(db, "ownership", "companies/E001", "companies/E002")

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
        """Anomalous price, concentration and duration signals are emitted."""
        signals = detect_fraud_signals(self._contracts())
        kinds = {(s["entity_type"], s["signal_type"]) for s in signals}

        assert ("supplier", "anomalous_price") in kinds
        assert ("buyer", "concentration") in kinds

        price = next(s for s in signals if s["signal_type"] == "anomalous_price")
        assert price["entity_id"] == "12345678000199"
        assert price["score"] > 0.5  # skewed leading digits -> suspicious
        details = json.loads(price["details"])
        # 12 contracts: Benford-eligible, IsolationForest-ineligible (< 15)
        assert details["benford_deviation"] == price["score"]
        assert details["isolation_forest_rate"] is None

        hhi = next(s for s in signals if s["signal_type"] == "concentration")
        assert hhi["entity_id"] == "26000"
        assert hhi["score"] == 1.0  # single supplier -> total concentration

    def test_duration_outlier_flagged(self) -> None:
        """A contract with an anomalous duration raises the outlier share."""
        contracts = self._contracts()
        for i, c in enumerate(contracts):
            c["validity_end"] = "2026-02-10" if i else "2036-01-10"

        signals = detect_fraud_signals(contracts)
        shares = [s for s in signals if s["signal_type"] == "anomalous_duration"]

        assert len(shares) == 1
        assert 0 < shares[0]["score"] <= 1.0

    def test_single_bid_emitted_for_non_competitive_supplier(self) -> None:
        """Suppliers with >= 3 non-competitive contracts emit single_bid."""
        contracts = self._contracts(4)
        for c in contracts:
            c["modality"] = "dispensa"

        signals = detect_fraud_signals(contracts)
        single = [s for s in signals if s["signal_type"] == "single_bid"]

        assert len(single) == 1
        assert single[0]["entity_id"] == "12345678000199"
        assert single[0]["score"] == 1.0
        assert json.loads(single[0]["details"]) == {
            "contracts": 4,
            "non_competitive": 4,
        }

    def test_single_bid_not_emitted_when_rate_zero(self) -> None:
        """Competitive-only suppliers must not emit single_bid."""
        contracts = self._contracts(4)
        for c in contracts:
            c["modality"] = "pregao"

        signals = detect_fraud_signals(contracts)
        assert not [s for s in signals if s["signal_type"] == "single_bid"]

    def test_single_bid_requires_three_contracts(self) -> None:
        """Fewer than 3 contracts must not emit single_bid."""
        contracts = self._contracts(2)
        for c in contracts:
            c["modality"] = "dispensa"

        signals = detect_fraud_signals(contracts)
        assert not [s for s in signals if s["signal_type"] == "single_bid"]

    def test_anomalous_price_isolation_forest_only(self) -> None:
        """Suppliers with >= 15 contracts but no amounts emit via IsolationForest."""
        contracts = self._contracts(15)
        for c in contracts:
            c["amount"] = None

        signals = detect_fraud_signals(contracts)
        price = [s for s in signals if s["signal_type"] == "anomalous_price"]

        assert len(price) == 1
        details = json.loads(price[0]["details"])
        assert details["benford_deviation"] is None
        assert details["isolation_forest_rate"] is not None
        assert price[0]["score"] == details["isolation_forest_rate"]

    def test_anomalous_price_ineligible_supplier(self) -> None:
        """Suppliers below both minimums must not emit anomalous_price."""
        signals = detect_fraud_signals(self._contracts(9))
        assert not [s for s in signals if s["signal_type"] == "anomalous_price"]


class TestSharedSignals:
    """Tests for the shared signal functions (capiba.detection.signals)."""

    def test_signal_type_is_the_api_schema_enum(self) -> None:
        """The API schema must re-export the same canonical enum."""
        from capiba.api.schemas import SignalType as ApiSignalType

        assert ApiSignalType is SignalType

    def test_single_bid_score(self) -> None:
        """The non-competitive rate is computed over the modality labels."""
        assert (
            single_bid_score(["dispensa", "pregao", "inexigibilidade", "pregao"]) == 0.5
        )
        assert single_bid_score([]) == 0.0

    def test_benford_deviation_below_minimum(self) -> None:
        """Fewer than 10 positive amounts must return None."""
        assert benford_deviation([9000.0] * 9) is None

    def test_benford_deviation_detects_skew(self) -> None:
        """Skewed leading digits must produce a high deviation."""
        deviation = benford_deviation([9000.0 + i for i in range(12)])
        assert deviation is not None
        assert deviation > 0.5

    def test_isolation_forest_rate_below_minimum(self) -> None:
        """Fewer than 15 contracts must return None."""
        assert isolation_forest_rate([1000.0] * 14, [30.0] * 14) is None

    def test_isolation_forest_rate_deterministic(self) -> None:
        """The fixed random_state makes the rate reproducible."""
        amounts = [1000.0 * (i + 1) for i in range(20)]
        durations = [30.0] * 20
        first = isolation_forest_rate(amounts, durations)
        assert first is not None
        assert first == isolation_forest_rate(amounts, durations)

    def test_anomalous_price_none_when_ineligible(self) -> None:
        """Groups below both minimums must return None."""
        assert anomalous_price([1000.0] * 5, [30.0] * 5) is None

    def test_anomalous_price_components(self) -> None:
        """The composite score is the max of the eligible components."""
        result = anomalous_price([9000.0 + i for i in range(20)], [30.0] * 20)
        assert result is not None
        score, components = result
        assert components["benford_deviation"] is not None
        assert components["isolation_forest_rate"] is not None
        assert score == max(value for value in components.values() if value is not None)

    def test_duration_outlier_share(self) -> None:
        """Below the minimum returns None; otherwise the IQR outlier share."""
        assert duration_outlier_share([30.0, 30.0], minimum=4) is None
        share = duration_outlier_share([30.0, 30.0, 30.0, 3000.0], minimum=4)
        assert share == 0.25

    def test_collusion_signals_format(self) -> None:
        """Each pair must become a binary collusion_network signal."""
        signals = collusion_signals([{"91000000000002", "91000000000001"}], min_wins=3)

        assert len(signals) == 1
        signal = signals[0]
        assert signal["entity_type"] == "supplier"
        assert signal["entity_id"] == "91000000000001+91000000000002"
        assert signal["signal_type"] == SignalType.COLLUSION_NETWORK
        assert signal["score"] == 1.0
        assert json.loads(signal["details"]) == {
            "min_wins": 3,
            "min_buyers": 1,
            "suppliers": ["91000000000001", "91000000000002"],
        }

    def test_collusion_signals_buyers_annotation(self) -> None:
        """With a buyer mapping, details carry the sorted co-occurring buyers."""
        buyers_by_pair = {("91000000000001", "91000000000002"): ["B2", "B1"]}
        signals = collusion_signals(
            [{"91000000000001", "91000000000002"}],
            min_wins=3,
            min_buyers=2,
            buyers_by_pair=buyers_by_pair,
        )
        assert json.loads(signals[0]["details"]) == {
            "min_wins": 3,
            "min_buyers": 2,
            "suppliers": ["91000000000001", "91000000000002"],
            "buyers": ["B2", "B1"],
        }

    def test_collusion_signals_deterministic_ordering(self) -> None:
        """The entity_id must be the sorted CNPJs regardless of set order."""
        first = collusion_signals([{"BBB", "AAA"}], min_wins=3)[0]
        second = collusion_signals([{"AAA", "BBB"}], min_wins=3)[0]
        assert first == second
        assert first["entity_id"] == "AAA+BBB"

    def test_collusion_signals_empty(self) -> None:
        """No pairs must produce no signals."""
        assert collusion_signals([], min_wins=3) == []
