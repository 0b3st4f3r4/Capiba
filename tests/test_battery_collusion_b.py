"""Tests for the refined collusion calibration battery (bateria D-03b).

Responsibility: Validate the refined synthetic generator (itinerant and
boundary pairs), the pure (min_wins, min_buyers) grid helpers, the
evaluation of the pre-registered predictions Q1-Q5 (synthetic) and Q6-Q9
(real sweep) offline, the refined measurement flow with a mocked ArangoDB
and the full Part A/C battery against live ArangoDB (integration marker).

Pre-registration: docs/preregistrations/PR-D-03b.md.
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

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-03b.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())
CONFIG_D03: dict[str, Any] = json.loads(
    (REPO_ROOT / "experiments" / "detect" / "D-03.json").read_text()
)
REFERENCE = date(2026, 8, 19)

RECENT_BUYERS = {"PLANT-B", "IT-B1", "BD-B1"}


def _export_rows() -> list[dict[str, Any]]:
    """Export-shaped rows matching the refined synthetic population."""
    rows: list[dict[str, Any]] = []

    def add(buyer: str, supplier: str, wins: int, recent: int) -> None:
        rows.append(
            {
                "buyer": buyer,
                "supplier": supplier,
                "wins": wins,
                "recent_wins": recent,
                "robust_wins": recent,
            }
        )

    for supplier in ["91000000000001", "91000000000002", "91000000000003"]:
        add("PLANT-B", supplier, 4, 4)
    add("PLANT-B", "91000000000004", 2, 2)
    for supplier in ["95000000000001", "95000000000002"]:
        add("IT-B1", supplier, 3, 3)
        add("IT-B2", supplier, 3, 0)
    add("BD-B1", "96000000000001", 3, 3)
    add("BD-B1", "96000000000002", 3, 3)
    add("BD-B2", "96000000000001", 3, 0)
    add("BD-B2", "96000000000002", 2, 0)
    for supplier in ["92000000000001", "92000000000002", "92000000000003"]:
        add("CTRL-B1", supplier, 2, 0)
    for i, supplier in enumerate(
        ["93000000000001", "93000000000002", "93000000000003", "93000000000004"]
    ):
        add(f"SOLO-B{i + 1}", supplier, 4, 0)
    return rows


def _perfect_record(seed: int) -> dict[str, Any]:
    """Builds a record that satisfies every synthetic prediction Q1-Q5."""
    exp = copy.deepcopy(CONFIG["expectations"])
    target_pairs = [list(p) for p in exp["pairs_min3_buyers2_exact"]]
    control_pairs = [list(p) for p in exp["pairs_min2_buyers2_control_exact"]]
    return {
        "seed": seed,
        "histogram": {"2": 5, "3": 7, "4": 7},
        "target_point": "3:2",
        "control_points": {
            "3:1": {
                "count": exp["pairs_min3_buyers1_exact_count"],
                "pairs": [],
                "buyers": {},
            },
            "3:2": {
                "count": 1,
                "pairs": target_pairs,
                "buyers": copy.deepcopy(exp["pair_buyers_min3_buyers2_exact"]),
            },
            "4:2": {
                "count": exp["pairs_min4_buyers2_exact_count"],
                "pairs": [],
                "buyers": {},
            },
            "2:2": {"count": 2, "pairs": control_pairs, "buyers": {}},
        },
        "pairs_full_target": 1,
        "recent_pairs_target": exp["recent_pairs_min3_buyers2_exact"],
        "siafi_coverage": 1.0,
        "evidence": {
            "signals": 1,
            "matches": [True],
            "tampered": {
                "signal_key": (
                    "supplier:95000000000001+95000000000002:collusion_network"
                ),
                "expected": 1.0,
                "actual": None,
                "integrity": False,
                "match": False,
            },
        },
    }


class TestRefinedDetection:
    """The refined mode is detected from the calibration block."""

    def test_d03b_config_is_refined(self) -> None:
        assert battery_collusion.is_refined(CONFIG) is True

    def test_d03_config_is_not_refined(self) -> None:
        assert battery_collusion.is_refined(CONFIG_D03) is False


class TestGenerateRefined:
    """Tests for the refined synthetic graph generator (offline)."""

    def test_deterministic_per_seed(self) -> None:
        """The same seed and reference date reproduce the same graph."""
        assert battery_collusion.generate(CONFIG, 7, REFERENCE) == (
            battery_collusion.generate(CONFIG, 7, REFERENCE)
        )

    def test_seed_variation(self) -> None:
        """Different seeds randomize the neutral fields."""
        assert battery_collusion.generate(CONFIG, 7, REFERENCE) != (
            battery_collusion.generate(CONFIG, 17, REFERENCE)
        )

    def test_population_counts(self) -> None:
        """59 contracts/seed: 14 PLANT-B + 12 itinerant + 11 boundary + 22."""
        graph = battery_collusion.generate(CONFIG, 7, REFERENCE)
        assert len(graph["contracts"]) == 59
        assert len(graph["won"]) == 59
        assert len(graph["suppliers"]) == 15

    def test_date_windows_planted(self) -> None:
        """PLANT-B/IT-B1/BD-B1 contracts are recent; the rest are older."""
        graph = battery_collusion.generate(CONFIG, 7, REFERENCE)
        window = CONFIG["calibration"]["increment_window_days"]
        cutoff = date.fromordinal(REFERENCE.toordinal() - window).isoformat()
        for contract in graph["contracts"]:
            recent = contract["buyer"]["siafi_code"] in RECENT_BUYERS
            in_window = contract["signature_date"] >= cutoff
            assert in_window == recent, contract


class TestPureHelpersRefined:
    """Tests for the pure (w, n) grid counting and decision helpers."""

    def test_grid_order_min_buyers_outer(self) -> None:
        """The decision order walks min_buyers outer, min_wins inner."""
        assert battery_collusion.grid_order([3, 4, 5], [2, 3]) == [
            (3, 2),
            (4, 2),
            (5, 2),
            (3, 3),
            (4, 3),
            (5, 3),
        ]

    def test_pair_counts_grid(self) -> None:
        """Pair counts per grid point follow the co-occurrence semantics."""
        counts = battery_collusion.pair_counts_grid(_export_rows(), [3, 4, 5], [2, 3])
        assert counts == {
            "3:2": 1,
            "4:2": 0,
            "5:2": 0,
            "3:3": 0,
            "4:3": 0,
            "5:3": 0,
        }
        assert battery_collusion._pair_buyer_count(_export_rows(), 3, 1) == 5
        assert battery_collusion._pair_buyer_count(_export_rows(), 2, 2) == 2

    def test_increments_grid_excludes_the_window(self) -> None:
        """The 30-day increment at (3,2) reflects the recent IT-B1 wins."""
        daily = battery_collusion.increments_grid(_export_rows(), [3], [2], 30)
        assert daily["3:2"] == pytest.approx(1 / 30)

    def test_decide_grid_picks_first_in_preregistered_order(self) -> None:
        """(5,2) is decided before (3,3): min_buyers is the outer loop."""
        budget = {"backlog_max_pairs": 10, "daily_max_pairs": 20}
        counts = {"3:2": 100, "4:2": 50, "5:2": 5, "3:3": 2, "4:3": 0, "5:3": 0}
        daily = dict.fromkeys(counts, 0.0)
        assert battery_collusion.decide_grid(counts, daily, [3, 4, 5], [2, 3], budget) == (
            5,
            2,
        )

    def test_decide_grid_returns_none_when_nothing_fits(self) -> None:
        """No grid point within budget means an inconclusive battery."""
        budget = {"backlog_max_pairs": 1, "daily_max_pairs": 20}
        counts = {"3:2": 100, "4:2": 50, "5:2": 5, "3:3": 2, "4:3": 2, "5:3": 2}
        daily = dict.fromkeys(counts, 0.0)
        assert (
            battery_collusion.decide_grid(counts, daily, [3, 4, 5], [2, 3], budget)
            is None
        )

    def test_decide_grid_respects_daily_budget(self) -> None:
        """A grid point over the daily budget is skipped for the next."""
        budget = {"backlog_max_pairs": 500, "daily_max_pairs": 1}
        counts = {"3:2": 3, "4:2": 2, "3:3": 0}
        daily = {"3:2": 5.0, "4:2": 0.5, "3:3": 0.0}
        assert battery_collusion.decide_grid(counts, daily, [3, 4], [2, 3], budget) == (
            4,
            2,
        )


class TestMeasureRefined:
    """Tests for the refined sweep measurement with a mocked ArangoDB."""

    def _mock_db(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        rows = _export_rows()
        db = MagicMock()
        db.collection.return_value.count.return_value = sum(r["wins"] for r in rows)

        def fake_aql(_db: Any, query: str, bind_vars: dict[str, Any]) -> Any:
            if "@cutoff" in query:
                return list(rows)
            return [sum(r["wins"] for r in rows)]  # coverage query

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
        """Both counting paths agree; (3,2) calibrates; 1 pair materialized."""
        db = self._mock_db(monkeypatch)

        measurement = battery_collusion.measure_refined(db, CONFIG, REFERENCE)

        assert measurement["histogram"] == {"2": 5, "3": 7, "4": 7}
        assert measurement["histogram_sum_wins"] == 59
        assert measurement["pairs_grid_python"] == {
            "3:2": 1,
            "4:2": 0,
            "5:2": 0,
            "3:3": 0,
            "4:3": 0,
            "5:3": 0,
        }
        assert measurement["pairs_grid_aql"] == measurement["pairs_grid_python"]
        assert measurement["increment_daily_grid"]["3:2"] == pytest.approx(
            1 / 30, abs=1e-4
        )
        assert measurement["control"] == {
            "min_wins": 2,
            "min_buyers": 2,
            "pairs_full": 2,
            "recent_pairs": 2,
        }
        assert measurement["siafi_coverage"] == 1.0
        assert measurement["calibrated"] == {"min_wins": 3, "min_buyers": 2}
        assert measurement["materialized_pairs"] == 1
        assert measurement["elapsed_seconds"] >= 0

    def test_no_materialization_without_calibration(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An impossible daily budget leaves the battery inconclusive."""
        db = self._mock_db(monkeypatch)
        config = copy.deepcopy(CONFIG)
        config["calibration"]["budget"]["daily_max_pairs"] = -1

        measurement = battery_collusion.measure_refined(db, config, REFERENCE)

        assert measurement["calibrated"] is None
        assert measurement["materialized_pairs"] is None

    def test_empty_graph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty graph yields zero counts and full coverage (vacuous)."""
        db = MagicMock()
        db.collection.return_value.count.return_value = 0
        monkeypatch.setattr(
            battery_collusion, "execute_aql", MagicMock(return_value=[])
        )
        monkeypatch.setattr(
            battery_collusion, "collusion_eligibility", MagicMock(return_value=[])
        )

        measurement = battery_collusion.measure_refined(db, CONFIG, REFERENCE)

        assert measurement["histogram"] == {}
        assert measurement["siafi_coverage"] == 1.0
        assert measurement["calibrated"] == {"min_wins": 3, "min_buyers": 2}
        assert measurement["materialized_pairs"] == 0


class TestEvaluateSyntheticRefined:
    """Offline tests of the Q1-Q5 evaluator (records in memory)."""

    def test_all_predictions_pass(self) -> None:
        """Perfect records yield success on Q1-Q5 and the invariant."""
        records = [_perfect_record(seed) for seed in CONFIG["seeds"]]
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, records)
        for name, prediction in summary["predictions"].items():
            assert prediction["verdict"] == "success", (name, prediction)
        assert summary["invariants"]["monotonicity"]["verdict"] == "success"
        assert summary["verdict"] == "success"

    def test_wrong_degenerate_count_refutes_q1(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:1"]["count"] = 6
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q1"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_wrong_pairs_refute_q2(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:2"]["pairs"] = [["96000000000001", "96000000000002"]]
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q2"]["verdict"] == "refuted"

    def test_wrong_buyers_annotation_refutes_q2(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:2"]["buyers"] = {
            "95000000000001+95000000000002": ["IT-B1"]
        }
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q2"]["verdict"] == "refuted"

    def test_nonempty_boundary_refutes_q3(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["4:2"]["count"] = 1
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q3"]["verdict"] == "refuted"

    def test_wrong_control_pairs_refute_q3(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["2:2"]["pairs"] = []
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q3"]["verdict"] == "refuted"

    def test_wrong_increment_refutes_q4(self) -> None:
        record = _perfect_record(7)
        record["recent_pairs_target"] = 2
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q4"]["verdict"] == "refuted"

    def test_failed_reproduction_refutes_q5(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["matches"] = [False]
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q5"]["verdict"] == "refuted"

    def test_intact_tampered_package_refutes_q5(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["tampered"]["integrity"] = True
        summary = battery_collusion.evaluate_synthetic_refined(CONFIG, [record])
        assert summary["predictions"]["Q5"]["verdict"] == "refuted"

    def test_monotonicity_violation_refutes_battery(self) -> None:
        record = _perfect_record(7)
        # pairs(4:2)=2 > pairs(3:2)=1: violates monotonicity over min_wins;
        # the Q3 expectation is adjusted so only the invariant fails.
        record["control_points"]["4:2"]["count"] = 2
        config = copy.deepcopy(CONFIG)
        config["expectations"]["pairs_min4_buyers2_exact_count"] = 2
        summary = battery_collusion.evaluate_synthetic_refined(config, [record])
        assert summary["predictions"]["Q3"]["verdict"] == "success"
        assert summary["invariants"]["monotonicity"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"


class TestEvaluateRealRefined:
    """Offline tests of the Q6-Q9 evaluator (measurement in memory)."""

    def _measurement(self) -> dict[str, Any]:
        zeros = {"3:2": 1, "4:2": 0, "5:2": 0, "3:3": 0, "4:3": 0, "5:3": 0}
        return {
            "reference_date": "2026-08-19",
            "elapsed_seconds": 12.5,
            "candidates_min_wins": [3, 4, 5],
            "candidates_min_buyers": [2, 3],
            "grid_order": ["3:2", "4:2", "5:2", "3:3", "4:3", "5:3"],
            "exported_rows": 19,
            "histogram": {"2": 5, "3": 7, "4": 7},
            "histogram_sum_wins": 59,
            "pairs_grid_python": dict(zeros),
            "pairs_grid_aql": dict(zeros),
            "increment_daily_grid": dict.fromkeys(zeros, 0.0),
            "increment_daily_robust_grid": dict.fromkeys(zeros, 0.0),
            "control": {
                "min_wins": 2,
                "min_buyers": 2,
                "pairs_full": 2,
                "recent_pairs": 2,
            },
            "total_won_edges": 59,
            "eligible_won_edges": 59,
            "siafi_coverage": 1.0,
            "calibrated": {"min_wins": 3, "min_buyers": 2},
            "materialized_pairs": 1,
        }

    def test_success(self) -> None:
        summary = battery_collusion.evaluate_real_refined(CONFIG, self._measurement())
        assert summary["verdict"] == "success"
        assert summary["predictions"]["Q9"]["calibrated"] == {
            "min_wins": 3,
            "min_buyers": 2,
        }

    def test_double_count_divergence_refutes_q6(self) -> None:
        measurement = self._measurement()
        measurement["pairs_grid_aql"]["3:2"] = 2
        summary = battery_collusion.evaluate_real_refined(CONFIG, measurement)
        assert summary["predictions"]["Q6"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_low_coverage_makes_q7_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["siafi_coverage"] = 0.5
        summary = battery_collusion.evaluate_real_refined(CONFIG, measurement)
        assert summary["predictions"]["Q7"]["verdict"] == "inconclusive"
        assert summary["verdict"] == "inconclusive"

    def test_histogram_mismatch_makes_q7_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["eligible_won_edges"] = 58
        summary = battery_collusion.evaluate_real_refined(CONFIG, measurement)
        assert summary["predictions"]["Q7"]["verdict"] == "inconclusive"

    def test_slow_sweep_refutes_q8(self) -> None:
        measurement = self._measurement()
        measurement["elapsed_seconds"] = 3600.0
        summary = battery_collusion.evaluate_real_refined(CONFIG, measurement)
        assert summary["predictions"]["Q8"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_materialization_divergence_refutes_q8(self) -> None:
        measurement = self._measurement()
        measurement["materialized_pairs"] = 0
        summary = battery_collusion.evaluate_real_refined(CONFIG, measurement)
        assert summary["predictions"]["Q8"]["verdict"] == "refuted"

    def test_no_grid_point_fits_makes_q9_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["calibrated"] = None
        measurement["materialized_pairs"] = None
        summary = battery_collusion.evaluate_real_refined(CONFIG, measurement)
        assert summary["predictions"]["Q9"]["verdict"] == "inconclusive"
        assert summary["verdict"] == "inconclusive"


class TestRunBatteryRefinedOffline:
    """Offline refined run_battery flow with a mocked ArangoDB."""

    def test_flow_creates_and_drops_database(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """The disposable database is dropped at start and at the end."""
        rows = _export_rows()
        sys_db = MagicMock()
        sys_db.has_database.return_value = False
        db = MagicMock()
        db.collection.return_value.count.return_value = sum(r["wins"] for r in rows)
        client = MagicMock()
        client.db.return_value = db
        monkeypatch.setattr(battery_collusion, "get_system_db", lambda: sys_db)
        monkeypatch.setattr(battery_collusion, "get_arango_client", lambda: client)
        monkeypatch.setattr(battery_collusion, "ensure_collections", lambda _db: None)
        monkeypatch.setattr(battery_collusion, "plant", lambda _db, _graph: None)

        def fake_aql(_db: Any, query: str, bind_vars: dict[str, Any]) -> Any:
            if "@cutoff" in query:
                return list(rows)
            return [sum(r["wins"] for r in rows)]  # coverage query

        def fake_eligibility(
            _db: Any = None, min_wins: int = 3
        ) -> list[dict[str, Any]]:
            return [r for r in rows if r["wins"] >= min_wins]

        monkeypatch.setattr(battery_collusion, "execute_aql", fake_aql)
        monkeypatch.setattr(
            battery_collusion, "collusion_eligibility", fake_eligibility
        )

        records = battery_collusion.run_battery(CONFIG, tmp_path)

        db_name = battery_collusion.battery_database_name(CONFIG)
        assert db_name == "capiba_d03b_battery"
        sys_db.create_database.assert_called_once_with(db_name)
        sys_db.delete_database.assert_called_once_with(db_name)
        assert len(records) == len(CONFIG["seeds"])
        for seed in CONFIG["seeds"]:
            lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
            assert len(lines) == 4  # histogram, control points, increment, evidence
        summary = json.loads((tmp_path / "summary_synthetic.json").read_text())
        assert summary["battery"] == "D-03b"
        assert summary["verdict"] == "success", json.dumps(summary, indent=2)


@pytest.mark.integration
def test_battery_refined_against_live_arangodb(tmp_path: Path) -> None:
    """Part A/C against real ArangoDB: Q1-Q5 must hold on every seed."""
    from capiba.db.arangodb import get_system_db

    records = battery_collusion.run_battery(CONFIG, tmp_path)
    summary = battery_collusion.evaluate_synthetic_refined(CONFIG, records)
    assert summary["verdict"] == "success", json.dumps(summary, indent=2)
    db_name = battery_collusion.battery_database_name(CONFIG)
    assert not get_system_db().has_database(db_name)
