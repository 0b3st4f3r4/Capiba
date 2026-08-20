"""Unit tests for the graph detection operators.

Responsibility: Validate collusion, ownership and geography operators
with a mocked ArangoDB (no live infrastructure). Collusion/ownership
follow the adapted semantics of PR-D-02, section 3.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from capiba.detection.graphs import (
    anomalous_geography,
    collusion_eligibility,
    detect_collusion,
    pair_buyers_from_eligibility,
    pairs_from_eligibility,
    partners_of_buyer,
    trace_ownership,
)


def _fake_db(graph_name: str = "capiba_graph") -> MagicMock:
    """Returns a mocked ArangoDB whose graph() exposes the given name."""
    db = MagicMock()
    db.graph.return_value.name = graph_name
    return db


class TestCollusionEligibility:
    """Tests for collusion_eligibility (PR-D-03 eligibility snapshot)."""

    def test_rows_sorted_with_win_counts(self, monkeypatch) -> None:
        """Rows come back sorted by (buyer, supplier) with win counts."""
        db = _fake_db()
        execute = MagicMock(
            return_value=[
                {"buyer": "B2", "supplier": "S4", "wins": 3},
                {"buyer": "B1", "supplier": "S2", "wins": 4},
                {"buyer": "B1", "supplier": "S1", "wins": 5},
            ]
        )
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        rows = collusion_eligibility(db=db, min_wins=3)

        assert rows == [
            {"buyer": "B1", "supplier": "S1", "wins": 5},
            {"buyer": "B1", "supplier": "S2", "wins": 4},
            {"buyer": "B2", "supplier": "S4", "wins": 3},
        ]

    def test_query_groups_by_buyer_and_returns_wins(self, monkeypatch) -> None:
        """The AQL groups by buyer.siafi_code and returns the win count."""
        db = _fake_db()
        execute = MagicMock(return_value=[])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        collusion_eligibility(db=db, min_wins=5)

        query, bind_vars = execute.call_args.args[1], execute.call_args.args[2]
        assert "FOR c IN contracts" in query
        assert "c.buyer.siafi_code != null" in query
        assert "INBOUND c won" in query
        assert "LENGTH(wins) >= @minWins" in query
        assert "wins: LENGTH(wins)" in query
        assert bind_vars == {"minWins": 5}


class TestPairsFromEligibility:
    """Tests for pairs_from_eligibility (pure pair derivation)."""

    def test_pairs_grouped_by_buyer(self) -> None:
        """Eligible rows become pairs within each buyer, sorted."""
        rows = [
            {"buyer": "B1", "supplier": "S2", "wins": 3},
            {"buyer": "B1", "supplier": "S1", "wins": 3},
            {"buyer": "B1", "supplier": "S3", "wins": 3},
            {"buyer": "B2", "supplier": "S4", "wins": 3},
            {"buyer": "B2", "supplier": "S5", "wins": 3},
        ]
        assert pairs_from_eligibility(rows) == [
            {"S1", "S2"},
            {"S1", "S3"},
            {"S2", "S3"},
            {"S4", "S5"},
        ]

    def test_empty_rows_yield_no_pairs(self) -> None:
        """No eligible rows, no pairs."""
        assert pairs_from_eligibility([]) == []


class TestPairBuyersFromEligibility:
    """Tests for pair_buyers_from_eligibility (PR-D-03b co-occurrence)."""

    ROWS = [
        {"buyer": "B1", "supplier": "S1", "wins": 3},
        {"buyer": "B1", "supplier": "S2", "wins": 3},
        {"buyer": "B1", "supplier": "S3", "wins": 3},
        {"buyer": "B2", "supplier": "S1", "wins": 3},
        {"buyer": "B2", "supplier": "S2", "wins": 3},
        {"buyer": "B3", "supplier": "S4", "wins": 3},
        {"buyer": "B3", "supplier": "S5", "wins": 3},
    ]

    def test_min_buyers_one_matches_plain_pairs(self) -> None:
        """min_buyers=1 reduces exactly to the D-03 pair semantics."""
        assert [set(pair) for pair, _ in pair_buyers_from_eligibility(self.ROWS)] == (
            pairs_from_eligibility(self.ROWS)
        )

    def test_min_buyers_two_filters_single_buyer_pairs(self) -> None:
        """Only pairs eligible in >= 2 distinct buyers survive at n=2."""
        assert pair_buyers_from_eligibility(self.ROWS, min_buyers=2) == [
            (("S1", "S2"), ["B1", "B2"]),
        ]

    def test_buyers_annotation_sorted(self) -> None:
        """Each pair carries the sorted buyers where it is eligible."""
        assert pair_buyers_from_eligibility(self.ROWS, min_buyers=1)[0] == (
            ("S1", "S2"),
            ["B1", "B2"],
        )

    def test_empty_rows_yield_no_pairs(self) -> None:
        """No eligible rows, no pairs."""
        assert pair_buyers_from_eligibility([], min_buyers=2) == []


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
    """Tests for trace_ownership (adapted semantics: simple ownership paths)."""

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

    def test_full_cnpj_is_normalized_to_cnpj_basico(self, monkeypatch) -> None:
        """A 14-digit CNPJ is keyed by its cnpj_basico (8 digits)."""
        db = _fake_db()
        execute = MagicMock(return_value=[])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        trace_ownership("12345678000195", max_depth=3, db=db)

        assert execute.call_args.args[2]["cnpj"] == "12345678"

    def test_query_blocks_cycles_and_uses_ownership_collection(self, monkeypatch) -> None:
        """The AQL traverses the ownership collection with uniqueVertices path."""
        db = _fake_db()
        execute = MagicMock(return_value=[])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        trace_ownership("E001", max_depth=3, db=db)

        query = execute.call_args.args[1]
        assert "OUTBOUND" in query
        assert 'CONCAT("companies/", @cnpj) ownership' in query
        assert 'uniqueVertices: "path"' in query
        assert "GRAPH" not in query

    def test_isolated_vertex_returns_empty(self, monkeypatch) -> None:
        """A vertex with no outbound ownership edges yields no paths."""
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


class TestPartnersOfBuyer:
    """Tests for partners_of_buyer (O4 acceptance: sócios de fornecedores)."""

    def test_returns_sorted_rows(self, monkeypatch) -> None:
        """Rows are deduplicated by AQL and sorted deterministically."""
        db = _fake_db()
        execute = MagicMock(
            return_value=[
                {
                    "supplier_cnpj": "99888777000166",
                    "company": "99888777",
                    "edge": "ownership",
                    "partner_key": "p2",
                    "partner_schema": "Person",
                    "partner_name": "MARIA",
                },
                {
                    "supplier_cnpj": "11222333000144",
                    "company": "11222333",
                    "edge": "directorship",
                    "partner_key": "p1",
                    "partner_schema": "Person",
                    "partner_name": "JOAO",
                },
            ]
        )
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        rows = partners_of_buyer("900000", db=db)

        assert [r["supplier_cnpj"] for r in rows] == [
            "11222333000144",
            "99888777000166",
        ]
        assert execute.call_args.args[2] == {"siafiCode": "900000"}

    def test_query_traverses_ownership_and_directorship_inbound(self, monkeypatch) -> None:
        """The AQL goes contract → supplier cnpj_basico → inbound FtM edges."""
        db = _fake_db()
        execute = MagicMock(return_value=[])
        monkeypatch.setattr("capiba.detection.graphs.execute_aql", execute)

        assert partners_of_buyer("900000", db=db) == []

        query = execute.call_args.args[1]
        assert "c.buyer.siafi_code == @siafiCode" in query
        assert "INBOUND" in query
        assert "ownership, directorship" in query
        assert 'CONCAT("companies/", LEFT(supplierCnpj, 8))' in query


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
