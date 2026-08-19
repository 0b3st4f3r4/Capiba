"""Unit tests for the graph detection operators.

Responsibility: Validate collusion, ownership and geography operators
with a mocked ArangoDB (no live infrastructure). Collusion/ownership
follow the adapted semantics of PR-D-02, section 3.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from capiba.detection.graphs import (
    anomalous_geography,
    detect_collusion,
    trace_ownership,
)


def _fake_db(graph_name: str = "capiba_graph") -> MagicMock:
    """Returns a mocked ArangoDB whose graph() exposes the given name."""
    db = MagicMock()
    db.graph.return_value.name = graph_name
    return db


class TestDetectCollusion:
    """Tests for detect_collusion (adapted semantics: pairs per buyer)."""

    def test_pairs_grouped_by_buyer(self, monkeypatch) -> None:
        """Eligible (buyer, supplier) rows become pairs within each buyer."""
        db = _fake_db()
        execute = MagicMock(
            return_value=[
                {"buyer": "B1", "supplier": "S2"},
                {"buyer": "B1", "supplier": "S1"},
                {"buyer": "B1", "supplier": "S3"},
                {"buyer": "B2", "supplier": "S4"},
                {"buyer": "B2", "supplier": "S5"},
            ]
        )
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        suspects = detect_collusion(db=db, min_wins=3)

        assert suspects == [
            {"S1", "S2"},
            {"S1", "S3"},
            {"S2", "S3"},
            {"S4", "S5"},
        ]

    def test_single_supplier_per_buyer_yields_no_pairs(self, monkeypatch) -> None:
        """A buyer with one eligible supplier cannot form a pair."""
        db = _fake_db()
        execute = MagicMock(
            return_value=[
                {"buyer": "B1", "supplier": "S1"},
                {"buyer": "B2", "supplier": "S2"},
            ]
        )
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        assert detect_collusion(db=db, min_wins=3) == []

    def test_output_is_deterministic(self, monkeypatch) -> None:
        """Row order does not affect the sorted output."""
        db = _fake_db()
        rows = [
            {"buyer": "B2", "supplier": "S4"},
            {"buyer": "B1", "supplier": "S2"},
            {"buyer": "B2", "supplier": "S5"},
            {"buyer": "B1", "supplier": "S1"},
        ]
        execute = MagicMock(return_value=list(reversed(rows)))
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        assert detect_collusion(db=db, min_wins=3) == [
            {"S1", "S2"},
            {"S4", "S5"},
        ]

    def test_query_uses_buyer_attribute_and_min_wins(self, monkeypatch) -> None:
        """The AQL groups by buyer.siafi_code with the min_wins threshold."""
        db = _fake_db()
        execute = MagicMock(return_value=[])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        detect_collusion(db=db, min_wins=5)

        query, bind_vars = execute.call_args.args[1], execute.call_args.args[2]
        assert "FOR c IN contracts" in query
        assert "c.buyer.siafi_code != null" in query
        assert "INBOUND c won" in query
        assert "LENGTH(wins) >= @minWins" in query
        assert bind_vars == {"minWins": 5}

    def test_creates_default_connection(self, monkeypatch) -> None:
        """When db is None, get_capiba_db must provide the connection."""
        db = _fake_db()
        get_db = MagicMock(return_value=db)
        monkeypatch.setattr("capiba.detection.graphs.get_capiba_db", get_db)
        monkeypatch.setattr(
            "capiba.detection.graphs.execute_aql", MagicMock(return_value=[])
        )

        assert detect_collusion() == []
        get_db.assert_called_once_with()


class TestTraceOwnership:
    """Tests for trace_ownership (adapted semantics: simple owns paths)."""

    def test_returns_sorted_paths(self, monkeypatch) -> None:
        """AQL results must be returned as sorted lists of vertex keys."""
        db = _fake_db()
        execute = MagicMock(
            return_value=[["E001", "E003"], ["E001", "E002"]]
        )
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        result = trace_ownership("E001", max_depth=2, db=db)

        assert result == [["E001", "E002"], ["E001", "E003"]]
        bind_vars = execute.call_args.args[2]
        assert bind_vars == {"cnpj": "E001", "maxDepth": 2}

    def test_query_blocks_cycles_and_uses_owns_collection(self, monkeypatch) -> None:
        """The AQL traverses the owns collection with uniqueVertices path."""
        db = _fake_db()
        execute = MagicMock(return_value=[])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        trace_ownership("E001", max_depth=3, db=db)

        query = execute.call_args.args[1]
        assert "OUTBOUND" in query
        assert 'CONCAT("companies/", @cnpj) owns' in query
        assert 'uniqueVertices: "path"' in query
        assert "GRAPH" not in query

    def test_isolated_vertex_returns_empty(self, monkeypatch) -> None:
        """A vertex with no outbound owns edges yields no paths."""
        db = _fake_db()
        execute = MagicMock(return_value=[])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        assert trace_ownership("E-ISOLATED", max_depth=3, db=db) == []

    def test_creates_default_connection(self, monkeypatch) -> None:
        """When db is None, get_capiba_db must provide the connection."""
        db = _fake_db()
        get_db = MagicMock(return_value=db)
        monkeypatch.setattr("capiba.detection.graphs.get_capiba_db", get_db)
        monkeypatch.setattr(
            "capiba.detection.graphs.execute_aql", MagicMock(return_value=[])
        )

        assert trace_ownership("E001") == []
        get_db.assert_called_once_with()


class TestAnomalousGeography:
    """Tests for anomalous_geography."""

    def test_returns_anomalies(self, monkeypatch) -> None:
        """Anomaly documents must be returned as-is."""
        db = _fake_db()
        anomalies = [{"supplier": "F1", "bid": "L1", "distance_km": 250.0}]
        execute = MagicMock(return_value=anomalies)
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        result = anomalous_geography(db=db, max_distance_km=50.0)

        assert result == anomalies
        bind_vars = execute.call_args.args[2]
        assert bind_vars == {"graphName": "capiba_graph", "maxDistance": 50.0}

    def test_creates_default_connection(self, monkeypatch) -> None:
        """When db is None, get_capiba_db must provide the connection."""
        db = _fake_db()
        get_db = MagicMock(return_value=db)
        monkeypatch.setattr("capiba.detection.graphs.get_capiba_db", get_db)
        monkeypatch.setattr(
            "capiba.detection.graphs.execute_aql", MagicMock(return_value=[])
        )

        assert anomalous_geography() == []
        get_db.assert_called_once_with()
