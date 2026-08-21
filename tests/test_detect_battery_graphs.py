"""Tests for the graph operators battery runner (bateria D-02).

Responsibility: Validate the synthetic graph generator (determinism,
population counts, planted patterns), the evaluation of the
pre-registered predictions P1-P6 (docs/preregistrations/PR-D-02.md)
offline (pure, no ArangoDB) and the full battery against live ArangoDB
(integration marker).
"""

from __future__ import annotations

import copy
import json
from itertools import combinations
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from batteries import battery_graphs

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-02.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


def _perfect_record(seed: int, config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Builds a record that satisfies every pre-registered prediction."""
    config = config or CONFIG
    exp = config["expectations"]
    collusion = config["collusion"]

    min3 = [sorted(pair) for pair in exp["collusion_pairs_min3_exact"]]

    planted = collusion["planted_buyer"]
    eligible_plant = sorted(
        [s["id"] for s in planted["suppliers"]] + [planted["boundary_supplier"]["id"]]
    )
    control = collusion["control_same_buyer"]
    min2 = min3 + [
        sorted(pair)
        for pair in combinations(eligible_plant, 2)
        if planted["boundary_supplier"]["id"] in pair
    ]
    min2 += [sorted(pair) for pair in combinations(control["suppliers"], 2)]
    min2.sort()

    return {
        "seed": seed,
        "collusion_min3": min3,
        "collusion_min2": min2,
        "ownership_chain_depth3": exp["ownership_paths_depth3_exact"],
        "ownership_chain_depth2": exp["ownership_paths_depth3_exact"][:2],
        "ownership_isolated": [],
        "ownership_cycle": exp["ownership_cycle_paths_exact"],
    }


class TestGenerate:
    """Tests for the synthetic graph generator (offline)."""

    def test_deterministic_per_seed(self) -> None:
        """The same seed reproduces the same graph, bit a bit."""
        assert battery_graphs.generate(CONFIG, seed=7) == battery_graphs.generate(
            CONFIG, seed=7
        )

    def test_seed_variation(self) -> None:
        """Different seeds randomize the neutral fields."""
        assert battery_graphs.generate(CONFIG, seed=7) != battery_graphs.generate(
            CONFIG, seed=17
        )

    def test_population_counts(self) -> None:
        """Population sizes match the pre-registered design."""
        graph = battery_graphs.generate(CONFIG, seed=7)
        assert len(graph["contracts"]) == 36  # 14 PLANT-B + 6 CTRL-B1 + 16 solo
        assert len(graph["won"]) == 36
        assert len(graph["suppliers"]) == 11  # 4 planted + 3 control + 4 solo
        assert len(graph["companies"]) == 7  # C-A..C-G
        assert len(graph["ownership"]) == 5  # 3 chain + 2 cycle

    def test_wins_planted_as_registered(self) -> None:
        """Wins per (buyer, supplier) match the collusion spec."""
        graph = battery_graphs.generate(CONFIG, seed=7)
        wins: dict[tuple[str, str], int] = {}
        contract_buyer = {
            c["_key"]: c["buyer"]["siafi_code"] for c in graph["contracts"]
        }
        for edge in graph["won"]:
            supplier = edge["_from"].split("/")[1]
            buyer = contract_buyer[edge["_to"].split("/")[1]]
            wins[(buyer, supplier)] = wins.get((buyer, supplier), 0) + 1
        assert wins[("PLANT-B", "91000000000001")] == 4
        assert wins[("PLANT-B", "91000000000004")] == 2
        assert wins[("CTRL-B1", "92000000000001")] == 2
        assert wins[("SOLO-B4", "93000000000004")] == 4

    def test_ownership_structure(self) -> None:
        """The ownership edges are the chain plus the cycle; C-E is isolated."""
        graph = battery_graphs.generate(CONFIG, seed=7)
        edges = {(e["_from"], e["_to"]) for e in graph["ownership"]}
        assert edges == {
            ("companies/C-A", "companies/C-B"),
            ("companies/C-B", "companies/C-C"),
            ("companies/C-C", "companies/C-D"),
            ("companies/C-F", "companies/C-G"),
            ("companies/C-G", "companies/C-F"),
        }


class TestEvaluate:
    """Offline tests of the P1-P6 evaluator (records in memory)."""

    def test_all_predictions_pass(self) -> None:
        """Perfect records yield success on P1-P6 and the invariant."""
        records = [_perfect_record(seed) for seed in CONFIG["seeds"]]
        summary = battery_graphs.evaluate(CONFIG, records)
        for name, prediction in summary["predictions"].items():
            assert prediction["verdict"] == "success", (name, prediction)
        assert summary["invariants"]["monotonicity"]["verdict"] == "success"
        assert summary["verdict"] == "success"

    def test_missing_planted_pair_refutes_p1(self) -> None:
        """P1 is refuted when a planted pair is absent."""
        record = _perfect_record(7)
        record["collusion_min3"] = record["collusion_min3"][1:]
        record["collusion_min2"] = record["collusion_min2"][1:]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P1"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_control_false_positive_refutes_p2(self) -> None:
        """P2 is refuted by any extra pair at min_wins (control FP)."""
        record = _perfect_record(7)
        record["collusion_min3"] = [
            *record["collusion_min3"],
            ["92000000000001", "92000000000002"],
        ]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P2"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_wrong_min2_count_refutes_p3(self) -> None:
        """P3 is refuted when the min_wins-1 pair set diverges."""
        record = _perfect_record(7)
        record["collusion_min2"] = record["collusion_min2"][:-1]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P3"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_wrong_chain_refutes_p4(self) -> None:
        """P4 is refuted when the depth-3 chain diverges."""
        record = _perfect_record(7)
        record["ownership_chain_depth3"] = record["ownership_chain_depth3"][:2]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P4"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_deep_vertex_at_depth2_refutes_p5(self) -> None:
        """P5 is refuted when C-D appears at depth 2."""
        record = _perfect_record(7)
        record["ownership_chain_depth2"] = [
            *record["ownership_chain_depth2"],
            ["C-A", "C-B", "C-C", "C-D"],
        ]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P5"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_wrong_depth2_count_refutes_p5(self) -> None:
        """P5 is refuted when the depth-2 count diverges."""
        record = _perfect_record(7)
        record["ownership_chain_depth2"] = record["ownership_chain_depth2"][:1]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P5"]["verdict"] == "refuted"

    def test_cycle_with_two_paths_refutes_p6(self) -> None:
        """P6 is refuted when the cycle yields more than the simple path."""
        record = _perfect_record(7)
        record["ownership_cycle"] = [
            *record["ownership_cycle"],
            ["C-F", "C-G", "C-F"],
        ]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P6"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_isolated_with_paths_refutes_p6(self) -> None:
        """P6 is refuted when the isolated company yields any path."""
        record = _perfect_record(7)
        record["ownership_isolated"] = [["C-E", "C-A"]]
        summary = battery_graphs.evaluate(CONFIG, [record])
        assert summary["predictions"]["P6"]["verdict"] == "refuted"

    def test_monotonicity_violation_refutes_battery(self) -> None:
        """The min_wins pair set must be a subset of the min_wins-1 set."""
        record = _perfect_record(7)
        record["collusion_min3"] = [
            *record["collusion_min3"],
            ["92000000000001", "93000000000001"],
        ]
        # P2 also fails; isolate the invariant by fixing P2's expectation
        config = copy.deepcopy(CONFIG)
        config["expectations"]["collusion_control_fp_min3"] = 1
        summary = battery_graphs.evaluate(config, [record])
        assert summary["predictions"]["P2"]["verdict"] == "success"
        assert summary["invariants"]["monotonicity"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"


class TestRunBatteryOffline:
    """Offline run_battery flow with a mocked ArangoDB (no live infra)."""

    def test_flow_creates_and_drops_database(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The disposable database is dropped at start and at the end."""
        sys_db = MagicMock()
        sys_db.has_database.return_value = False
        db = MagicMock()
        client = MagicMock()
        client.db.return_value = db
        monkeypatch.setattr(
            "batteries.battery_graphs.get_system_db", lambda: sys_db
        )
        monkeypatch.setattr(
            "batteries.battery_graphs.get_arango_client", lambda: client
        )
        monkeypatch.setattr(
            "batteries.battery_graphs.ensure_collections", lambda _db: None
        )
        perfect = _perfect_record(0)

        def fake_collusion(_db: Any, min_wins: int) -> list[set[str]]:
            key = "collusion_min3" if min_wins == 3 else "collusion_min2"
            return [set(pair) for pair in perfect[key]]

        def fake_trace(cnpj: str, max_depth: int, _db: Any) -> list[list[str]]:
            mapping = {
                ("C-A", 3): "ownership_chain_depth3",
                ("C-A", 2): "ownership_chain_depth2",
                ("C-E", 3): "ownership_isolated",
                ("C-F", 3): "ownership_cycle",
            }
            return perfect[mapping[(cnpj, max_depth)]]

        monkeypatch.setattr(
            "batteries.battery_graphs.detect_collusion", fake_collusion
        )
        monkeypatch.setattr(
            "batteries.battery_graphs.trace_ownership", fake_trace
        )

        records = battery_graphs.run_battery(CONFIG, tmp_path)

        db_name = battery_graphs.battery_database_name(CONFIG)
        assert db_name == "capiba_d02_battery"
        sys_db.create_database.assert_called_once_with(db_name)
        sys_db.delete_database.assert_called_once_with(db_name)
        assert len(records) == len(CONFIG["seeds"])
        for seed in CONFIG["seeds"]:
            lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
            assert len(lines) == 6  # one line per operator invocation
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["battery"] == "D-02"
        assert summary["verdict"] == "success"


@pytest.mark.integration
def test_battery_against_live_arangodb(tmp_path: Path) -> None:
    """Full battery against real ArangoDB: every prediction must hold."""
    from capiba.db.arangodb import get_system_db

    records = battery_graphs.run_battery(CONFIG, tmp_path)
    summary = battery_graphs.evaluate(CONFIG, records)
    assert summary["verdict"] == "success", json.dumps(summary, indent=2)
    db_name = battery_graphs.battery_database_name(CONFIG)
    assert not get_system_db().has_database(db_name)
