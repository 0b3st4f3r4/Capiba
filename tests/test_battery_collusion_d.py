"""Tests for the top-K emission collusion battery (bateria D-03d).

Responsibility: Validate the pure ``ranked_emission`` operator (ordering,
declared truncation, prefix equivalence, determinism), the graph_batch
evidence package with declared ``top_k`` (reproduction, tampering, legacy
retrocompatibility), the evaluation of the pre-registered predictions
T1-T4 (synthetic) and T5-T9 (real sweep) offline, the emission
measurement flow with a mocked ArangoDB, the full Part A/A-stress/C flow
offline end-to-end (plant mocked over the real generated documents) and
against live ArangoDB (integration + slow).

Pre-registration: docs/preregistrations/PR-D-03d.md.
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
    pair_buyers_from_eligibility,
    pair_buyers_from_eligibility_blocked,
    ranked_emission,
)
from capiba.evidence import packages as evidence_packages

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-03d.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())
CONFIG_D03C: dict[str, Any] = json.loads(
    (REPO_ROOT / "experiments" / "detect" / "D-03c.json").read_text()
)
REFERENCE = date(2026, 8, 21)

X_PAIR = ["95000000000001", "95000000000002"]
Y_PAIR = ["96000000000001", "96000000000002"]

ROWS = [
    {"buyer": "B1", "supplier": "S1", "wins": 5},
    {"buyer": "B1", "supplier": "S2", "wins": 3},
    {"buyer": "B2", "supplier": "S1", "wins": 4},
    {"buyer": "B2", "supplier": "S2", "wins": 4},
    {"buyer": "B1", "supplier": "S3", "wins": 9},
    {"buyer": "B3", "supplier": "S4", "wins": 3},
    {"buyer": "B3", "supplier": "S5", "wins": 3},
]


def _eligibility_rows(include_stress: bool = False) -> list[dict[str, Any]]:
    """Eligibility rows built straight from the synthetic spec (pure)."""
    tuples = battery_collusion._supplier_buyer_wins(CONFIG)
    if include_stress:
        tuples = tuples + battery_collusion._stress_tuples(CONFIG)
    return [
        {"buyer": buyer, "supplier": supplier, "wins": wins}
        for supplier, buyer, wins, _recent in tuples
    ]


class TestEmissionDetection:
    """The emission mode is detected from the calibration block."""

    def test_d03d_config_is_emission(self) -> None:
        assert battery_collusion.is_emission(CONFIG) is True

    def test_d03c_config_is_not_emission(self) -> None:
        assert battery_collusion.is_emission(CONFIG_D03C) is False

    def test_emission_takes_precedence_over_blocking(self) -> None:
        """D-03d declares both blocks; the emission mode wins."""
        assert battery_collusion.is_blocked(CONFIG) is True
        assert battery_collusion.is_emission(CONFIG) is True

    def test_emission_points_from_config(self) -> None:
        assert battery_collusion._emission_points(CONFIG) == [
            (3, 1),
            (3, 2),
            (4, 2),
            (2, 2),
        ]


class TestRankedEmission:
    """Pure tests of the top-K emission operator (PR-D-03d section 4)."""

    def test_ordering_keys(self) -> None:
        """buyer_count desc, then wins_sum desc, then pair asc."""
        pair_buyers = pair_buyers_from_eligibility(ROWS, 1)
        emission = ranked_emission(pair_buyers, ROWS)
        assert [d["pair"] for d in emission["emission"]] == [
            ["S1", "S2"],
            ["S1", "S3"],
            ["S2", "S3"],
            ["S4", "S5"],
        ]
        assert emission["qualified_count"] == 4
        assert emission["top_k"] is None
        assert emission["coverage"] == 1.0

    def test_truncation_and_descriptor(self) -> None:
        """K=1 emits the top pair and declares the coverage loss."""
        pair_buyers = pair_buyers_from_eligibility(ROWS, 1)
        emission = ranked_emission(pair_buyers, ROWS, top_k=1)
        assert [d["pair"] for d in emission["emission"]] == [["S1", "S2"]]
        assert emission["qualified_count"] == 4
        assert emission["top_k"] == 1
        assert emission["coverage"] == 0.25

    def test_truncation_is_exact_prefix(self) -> None:
        """The top-K emission is bit-a-bit the prefix of the full set."""
        pair_buyers = pair_buyers_from_eligibility(ROWS, 1)
        full = ranked_emission(pair_buyers, ROWS)["emission"]
        for k in (0, 1, 2, 3, 4, 500):
            assert ranked_emission(pair_buyers, ROWS, top_k=k)["emission"] == full[:k]

    def test_byte_identical_reruns(self) -> None:
        """T9 shape: two runs produce byte-identical canonical JSON."""
        pair_buyers = pair_buyers_from_eligibility(ROWS, 1)
        blob1 = json.dumps(ranked_emission(pair_buyers, ROWS, 2), sort_keys=True)
        blob2 = json.dumps(ranked_emission(pair_buyers, ROWS, 2), sort_keys=True)
        assert blob1 == blob2

    def test_empty_qualified_set(self) -> None:
        emission = ranked_emission([], ROWS, top_k=500)
        assert emission["emission"] == []
        assert emission["qualified_count"] == 0
        assert emission["coverage"] == 1.0

    def test_synthetic_anchors_pure(self) -> None:
        """T1/T2 anchors computed straight from the spec (no ArangoDB)."""
        rows = _eligibility_rows()
        exp = CONFIG["expectations"]
        eligible2 = [r for r in rows if r["wins"] >= 2]
        top1_22 = ranked_emission(
            pair_buyers_from_eligibility_blocked(eligible2, 2), eligible2, 1
        )
        assert [d["pair"] for d in top1_22["emission"]] == (
            exp["emission_top1_min2_buyers2_exact"]
        )
        assert top1_22["qualified_count"] == 2
        assert top1_22["coverage"] == 0.5
        eligible3 = [r for r in rows if r["wins"] >= 3]
        top1_31 = ranked_emission(
            pair_buyers_from_eligibility_blocked(eligible3, 1), eligible3, 1
        )
        assert [d["pair"] for d in top1_31["emission"]] == (
            exp["emission_top1_min3_buyers1_exact"]
        )
        assert top1_31["qualified_count"] == 5


class TestEvidenceTopK:
    """Graph batch package with declared top_k (PR-D-03d section 4, C)."""

    def _package(self, top_k: int | None) -> dict[str, Any]:
        pair_buyers = pair_buyers_from_eligibility(ROWS, 1)
        emission = ranked_emission(pair_buyers, ROWS, top_k)
        emitted = emission["emission"]
        signals = [
            {
                "entity_type": "supplier",
                "entity_id": "+".join(entry["pair"]),
                "signal_type": "collusion_network",
                "score": 1.0,
                "details": {},
            }
            for entry in emitted
        ]
        return evidence_packages.build_graph_batch_package(
            ROWS,
            signals,
            3,
            None,
            1,
            top_k=top_k,
            qualified_count=emission["qualified_count"] if top_k is not None else None,
        )

    def test_reproduction_matches_with_top_k(self) -> None:
        package = self._package(top_k=2)
        assert package["reproduction"]["top_k"] == 2
        assert package["reproduction"]["qualified_count"] == 4
        for signal in package["signals"]:
            key = f"{signal['entity_type']}:{signal['entity_id']}:{signal['signal_type']}"
            outcome = evidence_packages.reproduce_signal(package, key)
            assert outcome["match"] is True, outcome

    def test_truncated_pair_does_not_reappear(self) -> None:
        """A pair beyond the prefix is absent from the reproduction."""
        package = self._package(top_k=2)
        outcome = evidence_packages.reproduce_signal(
            package, "supplier:S2+S3:collusion_network"
        )
        assert outcome["expected"] is None
        assert outcome["actual"] is None
        assert outcome["match"] is False

    def test_tampered_snapshot_breaks_integrity(self) -> None:
        package = self._package(top_k=2)
        tampered = copy.deepcopy(package)
        tampered["snapshot_rows"] = tampered["snapshot_rows"][1:]
        key = "supplier:S1+S2:collusion_network"
        outcome = evidence_packages.reproduce_signal(tampered, key)
        assert outcome["integrity"] is False
        assert outcome["match"] is False

    def test_legacy_package_without_top_k_unchanged(self) -> None:
        """top_k=null reproduces the untruncated semantics (retrocompat)."""
        package = self._package(top_k=None)
        assert package["reproduction"]["top_k"] is None
        for signal in package["signals"]:
            key = f"{signal['entity_type']}:{signal['entity_id']}:{signal['signal_type']}"
            assert evidence_packages.reproduce_signal(package, key)["match"] is True


def _point(
    emission: list[list[str]],
    projection_blocked: int,
    truncated: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds a control-point record with both paths in agreement."""
    return {
        "top_k": 500,
        "qualified_count": len(emission),
        "coverage": 1.0,
        "emission": emission,
        "equivalent": True,
        "projection_blocked": projection_blocked,
        "incidences_blocked": projection_blocked,
        **({"truncated": truncated} if truncated is not None else {}),
    }


def _perfect_record(seed: int) -> dict[str, Any]:
    """Builds a record that satisfies every synthetic prediction T1-T4."""
    exp = CONFIG["expectations"]
    return {
        "seed": seed,
        "control_points": {
            "3:1": _point(
                [X_PAIR] * 5,
                6,
                truncated={
                    "top_k": 1,
                    "qualified_count": 5,
                    "coverage": 0.2,
                    "emission": exp["emission_top1_min3_buyers1_exact"],
                    "is_prefix": True,
                },
            ),
            "3:2": _point([list(p) for p in exp["emission_full_min3_buyers2_exact"]], 2),
            "4:2": _point([], 0),
            "2:2": _point(
                [list(p) for p in exp["emission_full_min2_buyers2_exact"]],
                4,
                truncated={
                    "top_k": 1,
                    "qualified_count": 2,
                    "coverage": 0.5,
                    "emission": exp["emission_top1_min2_buyers2_exact"],
                    "is_prefix": True,
                },
            ),
        },
        "evidence": {
            "signals": 1,
            "matches": [True],
            "legacy_matches": [True],
            "tampered": {
                "signal_key": "supplier:95000000000001+95000000000002:collusion_network",
                "expected": 1.0,
                "actual": None,
                "integrity": False,
                "match": False,
            },
        },
        "stress": {
            "projection_blocked": exp["stress_blocked_projection_min3_buyers2_exact"],
            "emission_min_buyers": [
                list(p) for p in exp["stress_emission_min3_buyers2_exact"]
            ],
            "emission_min_buyers_1_count": exp[
                "stress_emission_min3_buyers1_top500_count_exact"
            ],
            "emission_min_buyers_1_first": exp["stress_emission_min3_buyers1_first_exact"],
            "qualified_min_buyers_1": exp["stress_qualified_min3_buyers1_exact"],
            "prefix_min_buyers_1": True,
            "elapsed_seconds": 1.0,
        },
    }


class TestEvaluateSyntheticEmission:
    """Offline tests of the T1-T4 evaluator (records in memory)."""

    def test_all_predictions_pass(self) -> None:
        records = [_perfect_record(seed) for seed in CONFIG["seeds"]]
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, records)
        for name, prediction in summary["predictions"].items():
            assert prediction["verdict"] == "success", (name, prediction)
        assert summary["invariants"]["monotonicity"]["verdict"] == "success"
        assert summary["verdict"] == "success"

    def test_wrong_top1_refutes_t1(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["2:2"]["truncated"]["emission"] = [Y_PAIR]
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["predictions"]["T1"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_wrong_qualified_count_refutes_t2(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:1"]["truncated"]["qualified_count"] = 4
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["predictions"]["T2"]["verdict"] == "refuted"

    def test_broken_prefix_refutes_t3(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["3:1"]["truncated"]["is_prefix"] = False
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["predictions"]["T3"]["verdict"] == "refuted"

    def test_intact_tampered_package_refutes_t3(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["tampered"]["integrity"] = True
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["predictions"]["T3"]["verdict"] == "refuted"

    def test_legacy_mismatch_refutes_t3(self) -> None:
        record = _perfect_record(7)
        record["evidence"]["legacy_matches"] = [False]
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["predictions"]["T3"]["verdict"] == "refuted"

    def test_wrong_stress_count_refutes_t4(self) -> None:
        record = _perfect_record(7)
        record["stress"]["emission_min_buyers_1_count"] = 499
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["predictions"]["T4"]["verdict"] == "refuted"

    def test_stress_timeout_refutes_t4(self) -> None:
        record = _perfect_record(7)
        record["stress"]["elapsed_seconds"] = 60.0
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["predictions"]["T4"]["verdict"] == "refuted"

    def test_monotonicity_violation_refutes_battery(self) -> None:
        record = _perfect_record(7)
        record["control_points"]["4:2"]["projection_blocked"] = 3
        record["control_points"]["4:2"]["incidences_blocked"] = 3
        summary = battery_collusion.evaluate_synthetic_emission(CONFIG, [record])
        assert summary["invariants"]["monotonicity"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"


class TestMeasureEmission:
    """Tests for the real-sweep emission measurement with a mocked ArangoDB."""

    def _mock_db(self, monkeypatch: pytest.MonkeyPatch) -> MagicMock:
        rows = _eligibility_rows()
        total_wins = sum(r["wins"] for r in rows)
        db = MagicMock()
        db.collection.return_value.count.return_value = total_wins

        def fake_aql(_db: Any, query: str, bind_vars: dict[str, Any]) -> Any:
            if "@cutoff" in query:
                return [{**r, "recent_wins": 0, "robust_wins": 0} for r in rows]
            return [total_wins]  # coverage query

        def fake_eligibility(
            _db: Any = None, min_wins: int = 3
        ) -> list[dict[str, Any]]:
            return [r for r in rows if r["wins"] >= min_wins]

        monkeypatch.setattr(battery_collusion, "execute_aql", fake_aql)
        monkeypatch.setattr(battery_collusion, "collusion_eligibility", fake_eligibility)
        return db

    def test_measurement_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The first grid point is selected; the emission is stable."""
        db = self._mock_db(monkeypatch)

        measurement = battery_collusion.measure_emission(db, CONFIG, REFERENCE)

        assert measurement["grid_order"] == [
            "3:2",
            "4:2",
            "5:2",
            "3:3",
            "4:3",
            "5:3",
        ]
        assert measurement["selected_point"] == {"min_wins": 3, "min_buyers": 2}
        # Non-materialization guard: the grid points carry projections only.
        for point in measurement["points"].values():
            assert set(point) == {"projection_unblocked", "projection_blocked"}
        emission = measurement["emission"]
        assert emission["point"] == "3:2"
        assert emission["emitted_count"] == 1
        assert emission["qualified_count"] == 1
        assert emission["coverage"] == 1.0
        assert emission["prefix_ok"] is True
        assert emission["deterministic"] is True
        assert emission["incidences_blocked"] == 2
        assert emission["peak_blocked_bytes"] > 0
        assert emission["increment_daily"] == 0.0
        assert emission["emitted_pairs"] == [X_PAIR]
        assert measurement["graph_stable"] is True
        assert measurement["siafi_coverage"] == 1.0
        assert measurement["elapsed_seconds"] >= 0

    def test_no_operable_point(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """No derivation is materialized when every projection bursts."""
        db = self._mock_db(monkeypatch)
        monkeypatch.setattr(
            battery_collusion, "blocked_projection", lambda rows, n: 2_000_000
        )

        measurement = battery_collusion.measure_emission(db, CONFIG, REFERENCE)

        assert measurement["selected_point"] is None
        assert measurement["emission"] is None

    def test_increment_counts_new_pairs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Recent wins promoting a pair into the prefix count as delta."""
        rows = _eligibility_rows()
        db = MagicMock()
        total_wins = sum(r["wins"] for r in rows)
        db.collection.return_value.count.return_value = total_wins

        def fake_aql(_db: Any, query: str, bind_vars: dict[str, Any]) -> Any:
            if "@cutoff" in query:
                # The X pair's IT-B2 wins are recent: the old snapshot
                # drops the pair below min_buyers at every grid point.
                return [
                    {
                        **r,
                        "recent_wins": 3 if r["buyer"] == "IT-B2" else 0,
                        "robust_wins": 3 if r["buyer"] == "IT-B2" else 0,
                    }
                    for r in rows
                ]
            return [total_wins]

        def fake_eligibility(
            _db: Any = None, min_wins: int = 3
        ) -> list[dict[str, Any]]:
            return [r for r in rows if r["wins"] >= min_wins]

        monkeypatch.setattr(battery_collusion, "execute_aql", fake_aql)
        monkeypatch.setattr(battery_collusion, "collusion_eligibility", fake_eligibility)

        measurement = battery_collusion.measure_emission(db, CONFIG, REFERENCE)

        emission = measurement["emission"]
        assert emission["increment_daily"] == round(1 / 30, 4)
        assert emission["increment_daily_robust"] == round(1 / 60, 4)


class TestEvaluateRealEmission:
    """Offline tests of the T5-T9 evaluator (measurement in memory)."""

    def _measurement(self) -> dict[str, Any]:
        points = {
            key: {"projection_unblocked": 100, "projection_blocked": 10}
            for key in ["3:2", "4:2", "5:2", "3:3", "4:3", "5:3"]
        }
        return {
            "reference_date": "2026-08-21",
            "elapsed_seconds": 120.0,
            "candidates_min_wins": [3, 4, 5],
            "candidates_min_buyers": [2, 3],
            "grid_order": ["3:2", "4:2", "5:2", "3:3", "4:3", "5:3"],
            "exported_rows": 19,
            "histogram": {"3": 7},
            "histogram_sum_wins": 59,
            "total_won_edges": 59,
            "eligible_won_edges": 59,
            "siafi_coverage": 1.0,
            "graph_stable": True,
            "points": points,
            "selected_point": {"min_wins": 3, "min_buyers": 2},
            "emission": {
                "point": "3:2",
                "top_k": 500,
                "emitted_count": 500,
                "qualified_count": 126_827,
                "coverage": 500 / 126_827,
                "prefix_ok": True,
                "deterministic": True,
                "incidences_blocked": 10,
                "peak_blocked_bytes": 1_000_000,
                "increment_daily": 3.5,
                "increment_daily_robust": 2.1,
                "emitted_pairs": [X_PAIR],
            },
            "memory_budget_bytes": 256 * 1024 * 1024,
            "max_pairs_guard": 1_000_000,
            "time_budget_seconds": 600.0,
            "budget": {"backlog_max_pairs": 500, "daily_max_pairs": 20},
        }

    def test_success(self) -> None:
        summary = battery_collusion.evaluate_real_emission(CONFIG, self._measurement())
        assert summary["verdict"] == "success"
        assert summary["regime"]["verdict"] == "ok"
        for name in ("T5", "T6", "T7", "T8", "T9"):
            assert summary["predictions"][name]["verdict"] == "success"

    def test_no_operable_point_refutes_t5(self) -> None:
        measurement = self._measurement()
        measurement["selected_point"] = None
        measurement["emission"] = None
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["predictions"]["T5"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_memory_breach_refutes_t6(self) -> None:
        measurement = self._measurement()
        measurement["emission"]["peak_blocked_bytes"] = 300 * 1024 * 1024
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["predictions"]["T6"]["verdict"] == "refuted"

    def test_incidence_mismatch_refutes_t6(self) -> None:
        measurement = self._measurement()
        measurement["emission"]["incidences_blocked"] = 11
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["predictions"]["T6"]["verdict"] == "refuted"

    def test_increment_breach_refutes_t7(self) -> None:
        measurement = self._measurement()
        measurement["emission"]["increment_daily"] = 21.0
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["predictions"]["T7"]["verdict"] == "refuted"

    def test_broken_prefix_refutes_t8(self) -> None:
        measurement = self._measurement()
        measurement["emission"]["prefix_ok"] = False
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["predictions"]["T8"]["verdict"] == "refuted"

    def test_nondeterministic_emission_refutes_t9(self) -> None:
        measurement = self._measurement()
        measurement["emission"]["deterministic"] = False
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["predictions"]["T9"]["verdict"] == "refuted"

    def test_unstable_graph_is_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["graph_stable"] = False
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["verdict"] == "inconclusive"
        assert summary["regime"]["verdict"] == "degraded"

    def test_low_coverage_is_inconclusive(self) -> None:
        measurement = self._measurement()
        measurement["siafi_coverage"] = 0.5
        summary = battery_collusion.evaluate_real_emission(CONFIG, measurement)
        assert summary["verdict"] == "inconclusive"


class TestRunBatteryEmissionOffline:
    """Offline emission run_battery flow with a mocked ArangoDB.

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
        monkeypatch.setattr(battery_collusion, "collusion_eligibility", fake_eligibility)

        records = battery_collusion.run_battery(CONFIG, tmp_path)

        db_name = battery_collusion.battery_database_name(CONFIG)
        assert db_name == "capiba_d03d_battery"
        sys_db.create_database.assert_called_once_with(db_name)
        sys_db.delete_database.assert_called_once_with(db_name)
        assert len(records) == len(CONFIG["seeds"])
        for seed in CONFIG["seeds"]:
            lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
            assert len(lines) == 3  # control points, evidence, stress
        summary = json.loads((tmp_path / "summary_synthetic.json").read_text())
        assert summary["battery"] == "D-03d"
        assert summary["verdict"] == "success", json.dumps(summary, indent=2)
        merged = json.loads((tmp_path / "summary.json").read_text())
        assert merged["real"] == "pending"
        assert merged["verdict"] == "success"


@pytest.mark.integration
@pytest.mark.slow
def test_battery_emission_against_live_arangodb(tmp_path: Path) -> None:
    """Parts A/A-stress/C against real ArangoDB: T1-T4 on every seed."""
    from capiba.db.arangodb import get_system_db

    records = battery_collusion.run_battery(CONFIG, tmp_path)
    summary = battery_collusion.evaluate_synthetic_emission(CONFIG, records)
    assert summary["verdict"] == "success", json.dumps(summary, indent=2)
    db_name = battery_collusion.battery_database_name(CONFIG)
    assert not get_system_db().has_database(db_name)
