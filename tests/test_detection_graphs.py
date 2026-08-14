"""Unit tests for the graph detection operators.

Responsibility: Validate collusion, ownership and geography operators
with a mocked ArangoDB (no live infrastructure).
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
    """Tests for detect_collusion."""

    def test_returns_sets_of_cnpjs(self, monkeypatch) -> None:
        """AQL pairs must be converted into sets of CNPJs."""
        db = _fake_db()
        execute = MagicMock(return_value=[["A", "B"], ["B", "C"]])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        suspects = detect_collusion(db=db, min_wins=5)

        assert suspects == [{"A", "B"}, {"B", "C"}]
        query, bind_vars = execute.call_args.args[1], execute.call_args.args[2]
        assert "FOR bid IN bids" in query
        assert bind_vars == {"graphName": "capiba_graph", "minWins": 5}

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
    """Tests for trace_ownership."""

    def test_returns_paths(self, monkeypatch) -> None:
        """AQL results must be returned as lists of vertex keys."""
        db = _fake_db()
        paths = [["E001", "E002"], ["E001", "E003"]]
        execute = MagicMock(return_value=paths)
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        result = trace_ownership("E001", max_depth=2, db=db)

        assert result == paths
        bind_vars = execute.call_args.args[2]
        assert bind_vars == {
            "cnpj": "E001",
            "maxDepth": 2,
            "graphName": "capiba_graph",
        }

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
