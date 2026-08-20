"""Tests for the collusion calibration battery runner (bateria D-03).

Responsibility: Validate the synthetic generator (determinism, population
counts, planted date windows), the pure counting/decision helpers, the
evaluation of the pre-registered predictions P1-P4 (synthetic) and P5-P8
(real sweep) offline, the measurement flow with a mocked ArangoDB and the
full Part A/C battery against live ArangoDB (integration marker).

Pre-registration: docs/preregistrations/PR-D-03.md.
"""

from __future__ import annotations

import copy
import json
from datetime import date
from itertools import combinations
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.detection import battery_collusion

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-03.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())
REFERENCE = date(2026, 8, 19)


def _export_rows() -> list[dict[str, Any]]:
    """Export-shaped rows matching the planted synthetic population."""
    rows = []
    for supplier in ["91000000000001", "91000000000002", "91000000000003"]:
        rows.append(
            {
                "buyer": "PLANT-B",
                "supplier": supplier,
                "wins": 4,
                "recent_wins": 4,
                "robust_wins": 4,
            }
        )
    rows.append(
        {
            "buyer": "PLANT-B",
            "supplier": "91000000000004",
            "wins": 2,
            "recent_wins": 2,
            "robust_wins": 2,
        }
    )
    for supplier in ["92000000000001", "92000000000002", "92000000000003"]:
        rows.append(
            {
                "buyer": "CTRL-B1",
                "supplier": supplier,
                "wins": 2,
                "recent_wins": 0,
                "robust_wins": 0,
            }
        )
    for i, supplier in enumerate(
        ["93000000000001", "93000000000002", "93000000000003", "93000000000004"]
    ):
        rows.append(
            {
                "buyer": f"SOLO-B{i + 1}",
                "supplier": supplier,
                "wins": 4,
                "recent_wins": 0,
                "robust_wins": 0,
            }
        )
    return rows


def _perfect_record(seed: int) -> dict[str, Any]:
    """Builds a record that satisfies every synthetic prediction P1-P4."""
    exp = copy.deepcopy(CONFIG["expectations"])
    return {
        "seed": seed,
        "histogram": exp["histogram_exact"],
        "pairs_by_candidate": copy.deepcopy(exp["pairs_by_candidate_exact"]),
        "pairs_by_candidate_python": copy.deepcopy(exp["pairs_by_candidate_exact"]),
        "increment_daily": {
            "3": exp["recent_pairs_min3_exact"] / 30,
            "4": exp["recent_pairs_min3_exact"] / 30,
            "5": 0.0,
            "6": 0.0,
            "8": 0.0,
            "10": 0.0,
        },
        "control_min2": {
            "pairs_full": 9,
            "recent_pairs": exp["recent_pairs_min2_control_exact"],
        },
        "siafi_coverage": 1.0,
        "evidence": {
            "signals": 3,
            "matches": [True, True, True],
            "tampered": {
                "signal_key": "supplier:91000000000001+91000000000002:collusion_network",
                "expected": 1.0,
                "actual": None,
                "integrity": False,
                "match": False,
            },
        },
    }


class TestGenerate:
    """Tests for the synthetic graph generator (offline)."""

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
        """Population sizes match the pre-registered D-02 layout."""
        graph = battery_collusion.generate(CONFIG, 7, REFERENCE)
        assert len(graph["contracts"]) == 36  # 14 PLANT-B + 6 CTRL-B1 + 16 solo
        assert len(graph["won"]) == 36
        assert len(graph["suppliers"]) == 11

    def test_date_windows_planted(self) -> None:
        """PLANT-B contracts fall in the last 30 days; controls before it."""
        graph = battery_collusion.generate(CONFIG, 7, REFERENCE)
        window = CONFIG["calibration"]["increment_window_days"]
        cutoff = date.fromordinal(REFERENCE.toordinal() - window).isoformat()
        for contract in graph["contracts"]:
            recent = contract["buyer"]["siafi_code"] == "PLANT-B"
            in_window = contract["signature_date"] >= cutoff
            assert in_window == recent, contract


class TestPureHelpers:
    """Tests for the pure counting and decision helpers."""

    def test_histogram(self) -> None:
        """The histogram groups rows by win count."""
        assert battery_collusion.histogram_from_rows(_export_rows()) == {2: 4, 4: 7}

    def test_pair_counts_arithmetic(self) -> None:
        """Pair counts are C(eligible, 2) per buyer, without materializing."""
        counts = battery_collusion.pair_counts(_export_rows(), [2, 3, 4, 5])
        assert counts == {2: 9, 3: 3, 4: 3, 5: 0}

    def test_increments_exclude_the_window(self) -> None:
        """The 30-day increment subtracts pairs without the recent contracts."""
        daily = battery_collusion.increments(_export_rows(), [2, 3], 30)
        assert daily[3] == pytest.approx(0.1)  # 3 recent pairs / 30 days
        assert daily[2] == pytest.approx(0.2)  # 6 recent pairs / 30 days

    def test_decide_picks_smallest_fitting_candidate(self) -> None:
        """The rule picks the smallest candidate within both budgets."""
        budget = {"backlog_max_pairs": 500, "daily_max_pairs": 20}
        counts = {3: 3, 4: 3, 5: 0}
        daily = {3: 0.1, 4: 0.1, 5: 0.0}
        assert battery_collusion.decide(counts, daily, [3, 4, 5], budget) == 3

    def test_decide_returns_none_when_nothing_fits(self) -> None:
        """No candidate within budget means an inconclusive battery."""
        budget = {"backlog_max_pairs": 2, "daily_max_pairs": 20}
        counts = {3: 3, 4: 3, 5: 10}
        daily = {3: 0.1, 4: 0.1, 5: 0.0}
        assert battery_collusion.decide(counts, daily, [3, 4, 5], budget) is None

    def test_decide_respects_daily_budget(self) -> None:
        """A candidate over the daily budget is skipped for a higher one."""
        budget = {"backlog_max_pairs": 500, "daily_max_pairs": 1}
        counts = {3: 3, 4: 2, 5: 0}
        daily = {3: 5.0, 4: 0.5, 5: 0.0}
        assert battery_collusion.decide(counts, daily, [3, 4, 5], budget) == 4


class TestMeasure:
    """Tests for the sweep measurement with a mocked ArangoDB."""

    def _measure(
        self,
    ) -> tuple[list[dict[str, Any]], MagicMock, Any, Any, Any]:
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

        def fake_pairs(eligible: list[dict[str, Any]]) -> list[set[str]]:
            buyers: dict[str, list[str]] = {}
            for row in eligible:
                buyers.setdefault(row["buyer"], []).append(row["supplier"])
            return [
                set(pair)
                for suppliers in buyers.values()
                for pair in combinations(sorted(suppliers), 2)
            ]

        return rows, db, fake_aql, fake_eligibility, fake_pairs

    def test_measurement_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Both counting paths agree; w*=3; materialization under budget."""
        _rows, db, fake_aql, fake_eligibility, fake_pairs = self._measure()
        monkeypatch.setattr(battery_collusion, "execute_aql", fake_aql)
        monkeypatch.setattr(
            battery_collusion, "collusion_eligibility", fake_eligibility
        )
        monkeypatch.setattr(battery_collusion, "pairs_from_eligibility", fake_pairs)

        measurement = battery_collusion.measure(db, CONFIG, REFERENCE)

        assert measurement["histogram"] == {"2": 4, "4": 7}
        assert measurement["histogram_sum_wins"] == 36
        assert measurement["pairs_by_candidate_python"] == {
            "3": 3,
            "4": 3,
            "5": 0,
            "6": 0,
            "8": 0,
            "10": 0,
        }
        assert measurement["pairs_by_candidate_aql"] == (
            measurement["pairs_by_candidate_python"]
        )
        assert measurement["increment_daily"]["3"] == pytest.approx(0.1)
        assert measurement["control_min2"] == {"pairs_full": 9, "recent_pairs": 6}
        assert measurement["siafi_coverage"] == 1.0
        assert measurement["calibrated_min_wins"] == 3
        assert measurement["materialized_pairs"] == 3
        assert measurement["elapsed_seconds"] >= 0

    def test_calibrated_w_materializes_under_budget(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The calibrated pair set is materialized only within the budget."""
        _rows, db, fake_aql, fake_eligibility, fake_pairs = self._measure()
        monkeypatch.setattr(battery_collusion, "execute_aql", fake_aql)
        monkeypatch.setattr(
            battery_collusion, "collusion_eligibility", fake_eligibility
        )
        monkeypatch.setattr(battery_collusion, "pairs_from_eligibility", fake_pairs)
        config = copy.deepcopy(CONFIG)
        config["calibration"]["budget"]["backlog_max_pairs"] = 2

        measurement = battery_collusion.measure(db, config, REFERENCE)

        # w=3 and w=4 (3 pairs) exceed the backlog; w=5 (0 pairs) calibrates.
        assert measurement["calibrated_min_wins"] == 5
        assert measurement["materialized_pairs"] == 0

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

        measurement = battery_collusion.measure(db, CONFIG, REFERENCE)

        assert measurement["histogram"] == {}
        assert measurement["siafi_coverage"] == 1.0
        assert measurement["calibrated_min_wins"] == 3
        assert measurement["materialized_pairs"] == 0


class TestEvaluateSynthetic:
    """Offline tests of the P1-P4 evaluator (records in memory)."""

    def test_all_predictions_pass(self) -> None:
        """Perfect records yield success on P1-P4 and the invariant."""
        records = [_perfect_record(seed) for seed in CONFIG["seeds"]]
        summary = battery_collusion.evaluate_synthetic(CONFIG, records)
        for name, prediction in summary["predictions"].items():
            assert prediction["verdict"] == "success", (name, prediction)
        assert summary["invariants"]["monotonicity"]["verdict"] == "success"
        assert summary["verdict"] == "success"

    def test_wrong_histogram_refutes_p1(self) -> None:
        record = _perfect_record(7)
        record["histogram"] = {"4": 6, "2": 4}
        summary = battery_collusion.evaluate_synthetic(CONFIG, [record])
        assert summary["predictions"]["P1"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_wrong_counts_refute_p2(self) -> None:
        record = _perfect_record(7)
        record["pairs_by_candidate"]["3"] = 4
        summary = battery_collusion.evaluate_synthetic(CONFIG, [record])
        assert summary["predictions"]["P2"]["verdict"] == "refuted"

    def test_python_aql_divergence_refutes_p2(self) -> None:
        record = _perfect_record(7)
        record["pairs_by_candidate_python"]["3"] = 2
        summary = battery_collusion.evaluate_synthetic(CONFIG, [record])
        assert summary["predictions"]["P2"]["verdict"] == "refuted"

    def test_wrong_increment_refutes_p3(self) -> None:
        record = _perfect_record(7)
        record["increment_daily"]["3"] = 0.2
        summary = battery_collusion.evaluate_synthetic(CONFIG, [record])
        assert summary["predictions"]["P3"]["verdict"] == "refuted"

    def test_wrong_control_increment_refutes_p3(self) -> None:
        record = _perfect_record(7)
        record["control_min2"]["recent_pairs"] = 5
        summary = battery_collusion.evaluate_synthetic(CONFIG, [record])
        assert summary["predictions"]["P3"]["verdict"] == "refuted"

    def test_failed_reproduction_refutes_p4(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["matches"][0] = False
        summary = battery_collusion.evaluate_synthetic(CONFIG, [record])
        assert summary["predictions"]["P4"]["verdict"] == "refuted"

    def test_intact_tampered_package_refutes_p4(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["tampered"]["integrity"] = True
        summary = battery_collusion.evaluate_synthetic(CONFIG, [record])
        assert summary["predictions"]["P4"]["verdict"] == "refuted"

    def test_monotonicity_violation_refutes_battery(self) -> None:
        record = _perfect_record(7)
        # pairs(5)=4 > pairs(4)=3: violates monotonicity; expectations
        # adjusted so P2 passes and only the invariant fails.
        record["pairs_by_candidate"]["5"] = 4
        record["pairs_by_candidate_python"]["5"] = 4
        config = copy.deepcopy(CONFIG)
        config["expectations"]["pairs_by_candidate_exact"]["5"] = 4
        summary = battery_collusion.evaluate_synthetic(config, [record])
        assert summary["predictions"]["P2"]["verdict"] == "success"
        assert summary["invariants"]["monotonicity"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"


class TestEvaluateReal:
    """Offline tests of the P5-P8 evaluator (measurement in memory)."""

    def _measurement(self) -> dict[str, Any]:
        return {
            "reference_date": "2026-08-19",
            "elapsed_seconds": 12.5,
            "candidates": [3, 4, 5, 6, 8, 10],
            "exported_rows": 11,
            "histogram": {"2": 4, "4": 7},
            "histogram_sum_wins": 36,
            "pairs_by_candidate_python": {
                "3": 3,
                "4": 3,
                "5": 0,
                "6": 0,
                "8": 0,
                "10": 0,
            },
            "pairs_by_candidate_aql": {
                "3": 3,
                "4": 3,
                "5": 0,
                "6": 0,
                "8": 0,
                "10": 0,
            },
            "increment_daily": {"3": 0.1, "4": 0.1, "5": 0.0},
            "increment_daily_robust": {"3": 0.05, "4": 0.05, "5": 0.0},
            "control_min2": {"pairs_full": 9, "recent_pairs": 6},
            "total_won_edges": 36,
            "eligible_won_edges": 36,
            "siafi_coverage": 1.0,
            "calibrated_min_wins": 3,
            "materialized_pairs": 3,
        }

    def test_success(self) -> None:
        summary = battery_collusion.evaluate_real(CONFIG, self._measurement())
        assert summary["verdict"] == "success"
        assert summary["predictions"]["P8"]["calibrated_min_wins"] == 3

    def test_double_count_divergence_refutes_p5(self) -> None:
        measurement = self._measurement()
        measurement["pairs_by_candidate_aql"]["3"] = 2
        summary = battery_collusion.evaluate_real(CONFIG, measurement)
        assert summary["predictions"]["P5"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_low_coverage_makes_p6_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["siafi_coverage"] = 0.5
        measurement["total_won_edges"] = 72
        summary = battery_collusion.evaluate_real(CONFIG, measurement)
        assert summary["predictions"]["P6"]["verdict"] == "inconclusive"
        assert summary["verdict"] == "inconclusive"

    def test_histogram_mismatch_makes_p6_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["eligible_won_edges"] = 35
        summary = battery_collusion.evaluate_real(CONFIG, measurement)
        assert summary["predictions"]["P6"]["verdict"] == "inconclusive"

    def test_slow_sweep_refutes_p7(self) -> None:
        measurement = self._measurement()
        measurement["elapsed_seconds"] = 3600.0
        summary = battery_collusion.evaluate_real(CONFIG, measurement)
        assert summary["predictions"]["P7"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_materialization_divergence_refutes_p7(self) -> None:
        measurement = self._measurement()
        measurement["materialized_pairs"] = 2
        summary = battery_collusion.evaluate_real(CONFIG, measurement)
        assert summary["predictions"]["P7"]["verdict"] == "refuted"

    def test_no_candidate_fits_makes_p8_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["calibrated_min_wins"] = None
        measurement["materialized_pairs"] = None
        summary = battery_collusion.evaluate_real(CONFIG, measurement)
        assert summary["predictions"]["P8"]["verdict"] == "inconclusive"
        assert summary["verdict"] == "inconclusive"


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
        monkeypatch.setattr(battery_collusion, "get_system_db", lambda: sys_db)
        monkeypatch.setattr(battery_collusion, "get_arango_client", lambda: client)
        monkeypatch.setattr(battery_collusion, "ensure_collections", lambda _db: None)
        monkeypatch.setattr(battery_collusion, "plant", lambda _db, _graph: None)
        monkeypatch.setattr(
            battery_collusion,
            "measure",
            lambda _db, _config, _date: {
                "candidates": [3, 4, 5, 6, 8, 10],
                "histogram": {"4": 7, "2": 4},
                "pairs_by_candidate_aql": copy.deepcopy(
                    CONFIG["expectations"]["pairs_by_candidate_exact"]
                ),
                "pairs_by_candidate_python": copy.deepcopy(
                    CONFIG["expectations"]["pairs_by_candidate_exact"]
                ),
                "increment_daily": {"3": 0.1},
                "control_min2": {"pairs_full": 9, "recent_pairs": 6},
                "siafi_coverage": 1.0,
            },
        )
        monkeypatch.setattr(
            battery_collusion,
            "_evidence_check",
            lambda _db, _w: {
                "signals": 3,
                "matches": [True, True, True],
                "tampered": {"integrity": False, "match": False},
            },
        )

        records = battery_collusion.run_battery(CONFIG, tmp_path)

        db_name = battery_collusion.battery_database_name(CONFIG)
        assert db_name == "capiba_d03_battery"
        sys_db.create_database.assert_called_once_with(db_name)
        sys_db.delete_database.assert_called_once_with(db_name)
        assert len(records) == len(CONFIG["seeds"])
        for seed in CONFIG["seeds"]:
            lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
            assert len(lines) == 4  # histogram, counts, increment, evidence
        summary = json.loads((tmp_path / "summary_synthetic.json").read_text())
        assert summary["battery"] == "D-03"
        assert summary["verdict"] == "success"


@pytest.mark.integration
def test_battery_against_live_arangodb(tmp_path: Path) -> None:
    """Part A/C against real ArangoDB: P1-P4 must hold on every seed."""
    from capiba.db.arangodb import get_system_db

    records = battery_collusion.run_battery(CONFIG, tmp_path)
    summary = battery_collusion.evaluate_synthetic(CONFIG, records)
    assert summary["verdict"] == "success", json.dumps(summary, indent=2)
    db_name = battery_collusion.battery_database_name(CONFIG)
    assert not get_system_db().has_database(db_name)
