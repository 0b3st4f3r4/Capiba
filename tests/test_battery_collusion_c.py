"""Tests for the blocked collusion battery (bateria D-03c).

Responsibility: Validate the Part A-stress generator, the pure blocking
anchors (projections and pair sets computed straight from the synthetic
spec), the deterministic ordered emission, the evaluation of the
pre-registered predictions R1-R5 (synthetic) and R6-R9 (real sweep)
offline, the blocked measurement flow with a mocked ArangoDB, the full
Part A/A-stress/C flow offline end-to-end (plant mocked over the real
generated documents) and against live ArangoDB (integration + slow).

Pre-registration: docs/preregistrations/PR-D-03c.md.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.detection import battery_collusion
from capiba.detection.graphs import (
    blocked_projection,
    pair_buyers_from_eligibility,
    pair_buyers_from_eligibility_blocked,
    projected_pair_count,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-03c.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())
CONFIG_D03B: dict[str, Any] = json.loads(
    (REPO_ROOT / "experiments" / "detect" / "D-03b.json").read_text()
)
REFERENCE = date(2026, 8, 21)

X_PAIR = ["95000000000001", "95000000000002"]
Y_PAIR = ["96000000000001", "96000000000002"]


def _eligibility_rows(include_stress: bool = False) -> list[dict[str, Any]]:
    """Eligibility rows built straight from the synthetic spec (pure)."""
    tuples = battery_collusion._supplier_buyer_wins(CONFIG)
    if include_stress:
        tuples = tuples + battery_collusion._stress_tuples(CONFIG)
    return [
        {"buyer": buyer, "supplier": supplier, "wins": wins}
        for supplier, buyer, wins, _recent in tuples
    ]


def _point(
    pairs: list[list[str]],
    projection_unblocked: int,
    projection_blocked: int,
) -> dict[str, Any]:
    """Builds a control-point record with both paths in agreement."""
    return {
        "unblocked_pairs": pairs,
        "blocked_pairs": pairs,
        "unblocked_buyers": {},
        "blocked_buyers": {},
        "equivalent": True,
        "projection_unblocked": projection_unblocked,
        "projection_blocked": projection_blocked,
        "incidences_blocked": projection_blocked,
    }


def _perfect_record(seed: int) -> dict[str, Any]:
    """Builds a record that satisfies every synthetic prediction R1-R5."""
    exp = CONFIG["expectations"]
    return {
        "seed": seed,
        "control_points": {
            "3:1": _point([X_PAIR] * 5, 6, 6),
            "3:2": _point([list(p) for p in exp["pairs_min3_buyers2_exact"]], 6, 2),
            "4:2": _point([], 3, 0),
            "2:2": _point(
                [list(p) for p in exp["pairs_min2_buyers2_control_exact"]], 13, 4
            ),
        },
        "evidence": {
            "signals": 1,
            "matches": [True],
            "blocked_matches": [True],
            "tampered": {
                "signal_key": "supplier:95000000000001+95000000000002:collusion_network",
                "expected": 1.0,
                "actual": None,
                "integrity": False,
                "match": False,
            },
        },
        "stress": {
            "projection_unblocked": exp["stress_unblocked_projection_min3_exact"],
            "projection_blocked": exp["stress_blocked_projection_min3_buyers2_exact"],
            "unblocked_pairs": [list(p) for p in exp["stress_pairs_min3_buyers2_exact"]],
            "blocked_pairs": [list(p) for p in exp["stress_pairs_min3_buyers2_exact"]],
            "time_unblocked_seconds": 0.05,
            "time_blocked_seconds": 0.001,
            "peak_unblocked_bytes": 1_000_000,
            "peak_blocked_bytes": 100_000,
            "elapsed_seconds": 1.0,
        },
    }


class TestBlockedDetection:
    """The blocked mode is detected from the calibration block."""

    def test_d03c_config_is_blocked(self) -> None:
        assert battery_collusion.is_blocked(CONFIG) is True

    def test_d03b_config_is_not_blocked(self) -> None:
        assert battery_collusion.is_blocked(CONFIG_D03B) is False

    def test_equivalence_points_from_config(self) -> None:
        assert battery_collusion._equivalence_points(CONFIG) == [
            (3, 1),
            (3, 2),
            (4, 2),
            (2, 2),
        ]


class TestGenerateStress:
    """Tests for the Part A-stress generator (offline)."""

    def test_stress_population_counts(self) -> None:
        """200 exclusive suppliers x 3 wins: 600 contracts, 600 edges."""
        graph = battery_collusion.generate_stress(CONFIG, 7, REFERENCE)
        assert len(graph["suppliers"]) == 200
        assert len(graph["contracts"]) == 600
        assert len(graph["won"]) == 600

    def test_stress_supplier_ids_exclusive(self) -> None:
        """The stress suppliers do not collide with the base population."""
        base = battery_collusion.generate(CONFIG, 7, REFERENCE)
        stress = battery_collusion.generate_stress(CONFIG, 7, REFERENCE)
        base_ids = {s["_key"] for s in base["suppliers"]}
        stress_ids = {s["_key"] for s in stress["suppliers"]}
        assert base_ids.isdisjoint(stress_ids)
        contract_ids = {c["_key"] for c in base["contracts"]}
        assert contract_ids.isdisjoint({c["_key"] for c in stress["contracts"]})

    def test_stress_contracts_outside_the_window(self) -> None:
        """The stress wins fall before the increment window (declared)."""
        graph = battery_collusion.generate_stress(CONFIG, 7, REFERENCE)
        window = CONFIG["calibration"]["increment_window_days"]
        cutoff = date.fromordinal(REFERENCE.toordinal() - window).isoformat()
        assert all(c["signature_date"] < cutoff for c in graph["contracts"])
        assert all(c["buyer"]["siafi_code"] == "BIG-B" for c in graph["contracts"])

    def test_deterministic_per_seed(self) -> None:
        assert battery_collusion.generate_stress(CONFIG, 7, REFERENCE) == (
            battery_collusion.generate_stress(CONFIG, 7, REFERENCE)
        )

    def test_base_population_unchanged(self) -> None:
        """generate() keeps the D-03b population: 59 contracts/seed."""
        graph = battery_collusion.generate(CONFIG, 7, REFERENCE)
        assert len(graph["contracts"]) == 59
        assert len(graph["suppliers"]) == 15

    def test_no_stress_block_yields_no_tuples(self) -> None:
        assert battery_collusion._stress_tuples(CONFIG_D03B) == []


class TestStressAnchorsOffline:
    """R1/R2/R5 anchors computed from the pure spec (no ArangoDB)."""

    def test_unblocked_projections_exact(self) -> None:
        """R2: w=3 -> 6, w=4 -> 3, w=2 -> 13 over the base population."""
        rows = _eligibility_rows()
        exp = CONFIG["expectations"]["unblocked_projection_exact"]
        for w, expected in exp.items():
            eligible = [r for r in rows if r["wins"] >= int(w)]
            assert projected_pair_count(eligible) == expected

    def test_blocked_projections_exact(self) -> None:
        """R2: (3,1) -> 6, (3,2) -> 2, (4,2) -> 0, (2,2) -> 4."""
        rows = _eligibility_rows()
        exp = CONFIG["expectations"]["blocked_projection_exact"]
        for point, expected in exp.items():
            w, n = (int(v) for v in point.split(":"))
            eligible = [r for r in rows if r["wins"] >= w]
            assert blocked_projection(eligible, n) == expected, point

    def test_blocked_equivalence_at_every_control_point(self) -> None:
        """R1: blocked == unblocked derivation over the same snapshot."""
        rows = _eligibility_rows()
        for w, n in battery_collusion._equivalence_points(CONFIG):
            eligible = [r for r in rows if r["wins"] >= w]
            assert pair_buyers_from_eligibility_blocked(eligible, n) == (
                pair_buyers_from_eligibility(eligible, n)
            ), (w, n)

    def test_blocked_pairs_exact_at_control_points(self) -> None:
        """R1 anchors: {X1,X2} at (3,2); empty at (4,2); X and Y at (2,2)."""
        rows = _eligibility_rows()
        exp = CONFIG["expectations"]
        eligible3 = [r for r in rows if r["wins"] >= 3]
        eligible2 = [r for r in rows if r["wins"] >= 2]
        pairs32 = pair_buyers_from_eligibility_blocked(eligible3, 2)
        assert [list(p) for p, _ in pairs32] == exp["pairs_min3_buyers2_exact"]
        assert pairs32[0][1] == ["IT-B1", "IT-B2"]
        assert pair_buyers_from_eligibility_blocked(
            [r for r in rows if r["wins"] >= 4], 2
        ) == []
        pairs22 = pair_buyers_from_eligibility_blocked(eligible2, 2)
        assert [list(p) for p, _ in pairs22] == (
            exp["pairs_min2_buyers2_control_exact"]
        )

    def test_stress_anchors_exact(self) -> None:
        """R5: base + BIG-B -> unblocked 19906, blocked 2, pairs {X1,X2}."""
        rows = [r for r in _eligibility_rows(include_stress=True) if r["wins"] >= 3]
        exp = CONFIG["expectations"]
        assert projected_pair_count(rows) == exp["stress_unblocked_projection_min3_exact"]
        assert blocked_projection(rows, 2) == (
            exp["stress_blocked_projection_min3_buyers2_exact"]
        )
        pairs = pair_buyers_from_eligibility_blocked(rows, 2)
        assert [list(p) for p, _ in pairs] == exp["stress_pairs_min3_buyers2_exact"]
        assert pairs == pair_buyers_from_eligibility(rows, 2)


class TestRankedPairEmission:
    """Tests for the deterministic ordered emission (PR-D-03c section 5)."""

    ROWS = [
        {"buyer": "B1", "supplier": "S1", "wins": 5},
        {"buyer": "B1", "supplier": "S2", "wins": 3},
        {"buyer": "B2", "supplier": "S1", "wins": 4},
        {"buyer": "B2", "supplier": "S2", "wins": 4},
        {"buyer": "B1", "supplier": "S3", "wins": 9},
        {"buyer": "B3", "supplier": "S4", "wins": 3},
        {"buyer": "B3", "supplier": "S5", "wins": 3},
    ]

    def test_ranking_keys(self) -> None:
        """buyer_count desc, then wins_sum desc, then pair asc."""
        pair_buyers = pair_buyers_from_eligibility(self.ROWS, 1)
        emission = battery_collusion.ranked_pair_emission(pair_buyers, self.ROWS)
        # ("S1","S2"): 2 buyers, wins_sum 16; ("S1","S3"): 1 buyer, 14;
        # ("S2","S3"): 1 buyer, 12; ("S4","S5"): 1 buyer, 6.
        assert [d["pair"] for d in emission] == [
            ["S1", "S2"],
            ["S1", "S3"],
            ["S2", "S3"],
            ["S4", "S5"],
        ]
        assert emission[0]["buyer_count"] == 2
        assert emission[0]["wins_sum"] == 5 + 3 + 4 + 4

    def test_byte_identical_reruns(self) -> None:
        """R9 shape: two runs produce byte-identical canonical JSON."""
        pair_buyers = pair_buyers_from_eligibility(self.ROWS, 1)
        blob1 = json.dumps(
            battery_collusion.ranked_pair_emission(pair_buyers, self.ROWS),
            sort_keys=True,
        )
        blob2 = json.dumps(
            battery_collusion.ranked_pair_emission(pair_buyers, self.ROWS),
            sort_keys=True,
        )
        assert blob1 == blob2

    def test_top_k_descriptor_is_a_prefix(self) -> None:
        """The top-k descriptor truncates nothing: it is a prefix."""
        pair_buyers = pair_buyers_from_eligibility(self.ROWS, 1)
        emission = battery_collusion.ranked_pair_emission(pair_buyers, self.ROWS)
        top_k = CONFIG["calibration"]["ranking"]["top_k_descriptor"]
        assert emission[:top_k] == emission[: len(emission)]


class TestEvaluateSyntheticBlocked:
    """Offline tests of the R1-R5 evaluator (records in memory)."""

    def test_all_predictions_pass(self) -> None:
        records = [_perfect_record(seed) for seed in CONFIG["seeds"]]
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, records)
        for name, prediction in summary["predictions"].items():
            assert prediction["verdict"] == "success", (name, prediction)
        assert summary["invariants"]["monotonicity"]["verdict"] == "success"
        assert summary["verdict"] == "success"

    def test_divergence_refutes_r1(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:2"]["equivalent"] = False
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R1"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_wrong_anchor_count_refutes_r1(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:1"]["blocked_pairs"] = [X_PAIR] * 4
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R1"]["verdict"] == "refuted"

    def test_wrong_projection_refutes_r2(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:2"]["projection_blocked"] = 3
        record["control_points"]["3:2"]["incidences_blocked"] = 3
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R2"]["verdict"] == "refuted"

    def test_incidence_mismatch_refutes_r3(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["4:2"]["incidences_blocked"] = 1
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R3"]["verdict"] == "refuted"

    def test_failed_reproduction_refutes_r4(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["blocked_matches"] = [False]
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R4"]["verdict"] == "refuted"

    def test_intact_tampered_package_refutes_r4(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["tampered"]["integrity"] = True
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R4"]["verdict"] == "refuted"

    def test_wrong_stress_projection_refutes_r5(self) -> None:
        record = _perfect_record(7)
        record["stress"]["projection_unblocked"] = 19900
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R5"]["verdict"] == "refuted"

    def test_slower_blocked_derivation_refutes_r5(self) -> None:
        record = _perfect_record(7)
        record["stress"]["time_blocked_seconds"] = 1.0
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R5"]["verdict"] == "refuted"

    def test_stress_timeout_refutes_r5(self) -> None:
        record = _perfect_record(7)
        record["stress"]["elapsed_seconds"] = 60.0
        summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, [record])
        assert summary["predictions"]["R5"]["verdict"] == "refuted"

    def test_monotonicity_violation_refutes_battery(self) -> None:
        record = _perfect_record(7)
        # blocked(4:2)=3 > blocked(3:2)=2: violates monotonicity over
        # min_wins; the R1/R2 anchors are adjusted so only the invariant
        # fails.
        record["control_points"]["4:2"]["projection_blocked"] = 3
        record["control_points"]["4:2"]["incidences_blocked"] = 3
        config = copy.deepcopy(CONFIG)
        config["expectations"]["blocked_projection_exact"]["4:2"] = 3
        summary = battery_collusion.evaluate_synthetic_blocked(config, [record])
        assert summary["predictions"]["R1"]["verdict"] == "success"
        assert summary["invariants"]["monotonicity"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"


class TestMeasureBlocked:
    """Tests for the real-sweep blocked measurement with a mocked ArangoDB."""

    def _mock_db(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        rows = _eligibility_rows()
        total_wins = sum(r["wins"] for r in rows)
        db = MagicMock()
        db.collection.return_value.count.return_value = total_wins

        def fake_aql(_db: Any, query: str, bind_vars: dict[str, Any]) -> Any:
            if "@cutoff" in query:
                return [
                    {**r, "recent_wins": 0, "robust_wins": 0} for r in rows
                ]
            return [total_wins]  # coverage query

        def fake_eligibility(
            _db: Any = None, min_wins: int = 3
        ) -> list[dict[str, Any]]:
            return [r for r in rows if r["wins"] >= min_wins]

        monkeypatch.setattr(battery_collusion, "execute_aql", fake_aql)
        monkeypatch.setattr(
            battery_collusion, "collusion_eligibility", fake_eligibility
        )
        return db

    def test_measurement_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Equivalence holds at all 6 grid points; emission is stable."""
        db = self._mock_db(monkeypatch)

        measurement = battery_collusion.measure_blocked(db, CONFIG, REFERENCE)

        assert measurement["grid_order"] == [
            "3:2",
            "4:2",
            "5:2",
            "3:3",
            "4:3",
            "5:3",
        ]
        assert measurement["traced_point"] == "3:2"
        for key, point in measurement["points"].items():
            assert point["equivalent"], key
            assert point["projection_blocked"] == point["incidences_blocked"], key
        traced = measurement["points"]["3:2"]
        assert traced["peak_blocked_bytes"] > 0
        assert traced["peak_unblocked_bytes"] > 0
        assert measurement["points"]["3:2"]["pair_count"] == 1
        assert measurement["points"]["3:2"]["projection_unblocked"] == 6
        assert measurement["points"]["3:2"]["projection_blocked"] == 2
        assert measurement["graph_stable"] is True
        assert measurement["siafi_coverage"] == 1.0
        assert measurement["emission"]["deterministic"] is True
        assert measurement["emission"]["size"] == 1
        assert measurement["elapsed_seconds"] >= 0

    def test_unstable_graph_flagged(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A changed won count between the boundaries marks the run."""
        db = self._mock_db(monkeypatch)
        db.collection.return_value.count.side_effect = [59, 60, 60]

        measurement = battery_collusion.measure_blocked(db, CONFIG, REFERENCE)

        assert measurement["graph_stable"] is False


class TestEvaluateRealBlocked:
    """Offline tests of the R6-R9 evaluator (measurement in memory)."""

    def _measurement(self) -> dict[str, Any]:
        point = {
            "projection_unblocked": 100,
            "projection_blocked": 10,
            "incidences_blocked": 10,
            "pair_count": 3,
            "equivalent": True,
            "time_unblocked_seconds": 1.0,
            "time_blocked_seconds": 0.1,
            "peak_unblocked_bytes": 10_000_000,
            "peak_blocked_bytes": 1_000_000,
        }
        return {
            "reference_date": "2026-08-21",
            "elapsed_seconds": 120.0,
            "candidates_min_wins": [3, 4, 5],
            "candidates_min_buyers": [2, 3],
            "grid_order": ["3:2", "4:2", "5:2", "3:3", "4:3", "5:3"],
            "traced_point": "3:2",
            "exported_rows": 19,
            "histogram": {"3": 7},
            "histogram_sum_wins": 59,
            "total_won_edges": 59,
            "eligible_won_edges": 59,
            "siafi_coverage": 1.0,
            "graph_stable": True,
            "points": {key: dict(point) for key in ["3:2", "4:2", "5:2", "3:3", "4:3", "5:3"]},
            "emission": {
                "deterministic": True,
                "top_k": 500,
                "top_k_is_prefix": True,
                "size": 3,
            },
            "memory_budget_bytes": 256 * 1024 * 1024,
            "max_pairs_guard": 1_000_000,
            "time_budget_seconds": 600.0,
        }

    def test_success(self) -> None:
        summary = battery_collusion.evaluate_real_blocked(CONFIG, self._measurement())
        assert summary["verdict"] == "success"
        assert summary["regime"]["verdict"] == "ok"

    def test_divergence_refutes_r6(self) -> None:
        measurement = self._measurement()
        measurement["points"]["4:3"]["equivalent"] = False
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["predictions"]["R6"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_incidence_mismatch_refutes_r7(self) -> None:
        measurement = self._measurement()
        measurement["points"]["3:2"]["incidences_blocked"] = 11
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["predictions"]["R7"]["verdict"] == "refuted"

    def test_guard_breach_refutes_r8(self) -> None:
        measurement = self._measurement()
        measurement["points"]["3:2"]["projection_blocked"] = 1_000_000
        measurement["points"]["3:2"]["incidences_blocked"] = 1_000_000
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["predictions"]["R8"]["verdict"] == "refuted"

    def test_memory_breach_refutes_r8(self) -> None:
        measurement = self._measurement()
        measurement["points"]["3:2"]["peak_blocked_bytes"] = 300 * 1024 * 1024
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["predictions"]["R8"]["verdict"] == "refuted"

    def test_blocked_slower_than_unblocked_refutes_r8(self) -> None:
        measurement = self._measurement()
        measurement["points"]["3:2"]["peak_blocked_bytes"] = 20_000_000
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["predictions"]["R8"]["verdict"] == "refuted"

    def test_nondeterministic_emission_refutes_r9(self) -> None:
        measurement = self._measurement()
        measurement["emission"]["deterministic"] = False
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["predictions"]["R9"]["verdict"] == "refuted"

    def test_unstable_graph_is_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["graph_stable"] = False
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["verdict"] == "inconclusive"
        assert summary["regime"]["verdict"] == "degraded"

    def test_low_coverage_is_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["siafi_coverage"] = 0.5
        summary = battery_collusion.evaluate_real_blocked(CONFIG, measurement)
        assert summary["verdict"] == "inconclusive"


class TestRunBatteryBlockedOffline:
    """Offline blocked run_battery flow with a mocked ArangoDB.

    The ``plant`` mock applies the real generated documents to an
    in-memory win index, so the whole Part A/A-stress/C flow — including
    the evidence package build/reproduction (pure) — runs end-to-end.
    """

    def test_flow_end_to_end(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        wins: dict[tuple[str, str], int] = {}

        def fake_plant(_db: Any, graph: dict[str, Any]) -> None:
            buyer_by_contract = {
                c["_key"]: c["buyer"]["siafi_code"] for c in graph["contracts"]
            }
            for edge in graph["won"]:
                supplier = edge["_from"].split("/")[1]
                buyer = buyer_by_contract[edge["_to"].split("/")[1]]
                wins[(buyer, supplier)] = wins.get((buyer, supplier), 0) + 1

        def fake_eligibility(
            _db: Any = None, min_wins: int = 3
        ) -> list[dict[str, Any]]:
            return [
                {"buyer": buyer, "supplier": supplier, "wins": w}
                for (buyer, supplier), w in sorted(wins.items())
                if w >= min_wins
            ]

        sys_db = MagicMock()
        sys_db.has_database.return_value = False
        client = MagicMock()
        db = MagicMock()
        db.collection.return_value.truncate.side_effect = wins.clear
        client.db.return_value = db
        monkeypatch.setattr(battery_collusion, "get_system_db", lambda: sys_db)
        monkeypatch.setattr(battery_collusion, "get_arango_client", lambda: client)
        monkeypatch.setattr(battery_collusion, "ensure_collections", lambda _db: None)
        monkeypatch.setattr(battery_collusion, "plant", fake_plant)
        monkeypatch.setattr(
            battery_collusion, "collusion_eligibility", fake_eligibility
        )

        records = battery_collusion.run_battery(CONFIG, tmp_path)

        db_name = battery_collusion.battery_database_name(CONFIG)
        assert db_name == "capiba_d03c_battery"
        sys_db.create_database.assert_called_once_with(db_name)
        sys_db.delete_database.assert_called_once_with(db_name)
        assert len(records) == len(CONFIG["seeds"])
        for seed in CONFIG["seeds"]:
            lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
            assert len(lines) == 3  # control points, evidence, stress
        summary = json.loads((tmp_path / "summary_synthetic.json").read_text())
        assert summary["battery"] == "D-03c"
        assert summary["verdict"] == "success", json.dumps(summary, indent=2)
        merged = json.loads((tmp_path / "summary.json").read_text())
        assert merged["real"] == "pending"
        assert merged["verdict"] == "success"


@pytest.mark.integration
@pytest.mark.slow
def test_battery_blocked_against_live_arangodb(tmp_path: Path) -> None:
    """Parts A/A-stress/C against real ArangoDB: R1-R5 on every seed."""
    from capiba.db.arangodb import get_system_db

    records = battery_collusion.run_battery(CONFIG, tmp_path)
    summary = battery_collusion.evaluate_synthetic_blocked(CONFIG, records)
    assert summary["verdict"] == "success", json.dumps(summary, indent=2)
    db_name = battery_collusion.battery_database_name(CONFIG)
    assert not get_system_db().has_database(db_name)
