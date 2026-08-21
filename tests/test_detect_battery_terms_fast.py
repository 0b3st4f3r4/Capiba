"""Fast tests for the contract-terms battery runner (bateria D-05b).

Responsibility: Cover the pure parts of ``capiba.detection.battery_terms``
(term payload builder, case generator, matcher, seed runner, evaluator and
the offline ``run_battery`` flow) in the quick suite — no infra, no
``slow`` marker. The regime battery (pre-registered predictions Q1/Q2 over
the shipped config) lives in ``tests/test_detect_battery_terms.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery_terms

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG: dict[str, Any] = json.loads(
    (REPO_ROOT / "experiments" / "detect" / "D-05b.json").read_text()
)

# Minimal config with the Q2 anchor case ids (evaluate reads B1/B4/B6 by
# name) plus one planted term list.
MINI_CONFIG: dict[str, Any] = {
    "id": "D-05b-mini",
    "seeds": [7],
    "cases": [
        {
            "id": "B1",
            "terms": [],
            "expected": {
                "f_value_amendment_terms": 0,
                "f_term_extension_terms": 0,
                "terms_count": 0,
                "total_value_increase": 0.0,
                "total_days_extended": 0,
                "term_types": [],
            },
        },
        {
            "id": "C1",
            "terms": [{"kind": "value_increase", "valorAcrescido": 100.0}],
            "expected": {
                "f_value_amendment_terms": 1,
                "f_term_extension_terms": 0,
                "terms_count": 1,
                "total_value_increase": 100.0,
                "total_days_extended": 0,
                "term_types": ["Termo Aditivo"],
            },
        },
        {
            "id": "B4",
            "terms": [{"kind": "pure_reajuste"}],
            "expected": {
                "f_value_amendment_terms": 0,
                "f_term_extension_terms": 0,
                "terms_count": 1,
                "total_value_increase": 0.0,
                "total_days_extended": 0,
                "term_types": ["Termo Aditivo"],
            },
        },
        {
            "id": "B6",
            "query_failed": True,
            "expected": {
                "f_value_amendment_terms": None,
                "f_term_extension_terms": None,
                "terms_count": None,
                "total_value_increase": None,
                "total_days_extended": None,
                "term_types": None,
            },
        },
    ],
}


class TestTermBuilder:
    """Tests for the planted term payload builder."""

    def test_each_kind_sets_its_qualification(self) -> None:
        """Each planted kind flips only its own qualification flag."""
        value = battery_terms._term(
            {"kind": "value_increase", "valorAcrescido": 10.0}, "CN", 1, battery_terms._BASE
        )
        assert value["qualificacaoAcrescimoSupressao"] is True
        assert value["valorAcrescido"] == 10.0
        assert value["qualificacaoVigencia"] is False

        extension = battery_terms._term(
            {"kind": "term_extension", "prazoAditadoDias": 90}, "CN", 1, battery_terms._BASE
        )
        assert extension["qualificacaoVigencia"] is True
        assert extension["prazoAditadoDias"] == 90

        reajuste = battery_terms._term({"kind": "pure_reajuste"}, "CN", 1, battery_terms._BASE)
        assert reajuste["qualificacaoReajuste"] is True
        assert "valorAcrescido" not in reajuste

        suppression = battery_terms._term(
            {"kind": "suppression", "valorAcrescido": -5.0}, "CN", 1, battery_terms._BASE
        )
        assert suppression["qualificacaoAcrescimoSupressao"] is True
        assert suppression["valorAcrescido"] == -5.0

    def test_unknown_kind_fails_loudly(self) -> None:
        """A kind outside the planted vocabulary is a config error."""
        with pytest.raises(ValueError, match="Unknown planted term kind"):
            battery_terms._term({"kind": "alquimia"}, "CN", 1, battery_terms._BASE)


class TestMatches:
    """Tests for the float-tolerant expectation matcher."""

    def test_float_within_tolerance(self) -> None:
        assert battery_terms._matches(1.2000000001, 1.2) is True
        assert battery_terms._matches(1.21, 1.2) is False

    def test_non_float_is_exact(self) -> None:
        assert battery_terms._matches(1, 1) is True
        assert battery_terms._matches(["Termo Aditivo"], ["Termo Aditivo"]) is True
        assert battery_terms._matches(None, None) is True
        assert battery_terms._matches(0, None) is False


class TestGenerateCasesFast:
    """Fast coverage of the case generator (structure and nulls)."""

    def test_structure_and_null_discipline(self) -> None:
        """query_failed carries terms None; planted kinds keep their values."""
        cases = battery_terms.generate_cases(MINI_CONFIG, seed=7)
        assert [c["case"] for c in cases] == ["B1", "C1", "B4", "B6"]
        assert cases[0]["terms"] == []  # HTTP 204
        assert cases[3]["terms"] is None  # failed query
        term = cases[1]["terms"][0]
        assert term["numeroControlePNCP"] == "SYN-D05b-7-C1"
        assert term["sequencialTermo"] == 1
        assert term["valorAcrescido"] == 100.0

    def test_seeds_only_randomize_neutral_fields(self) -> None:
        """Planted amounts survive seed variation; control numbers do not."""
        first = battery_terms.generate_cases(CONFIG, seed=13)
        second = battery_terms.generate_cases(CONFIG, seed=23)
        b2_first = next(c for c in first if c["case"] == "B2")
        b2_second = next(c for c in second if c["case"] == "B2")
        assert b2_first["terms"][0]["valorAcrescido"] == 6840.88
        assert b2_second["terms"][0]["valorAcrescido"] == 6840.88
        assert (
            b2_first["terms"][0]["numeroControlePNCP"]
            != b2_second["terms"][0]["numeroControlePNCP"]
        )


class TestRunSeedFast:
    """Fast coverage of the per-seed runner."""

    def test_zero_repeat_divergences(self) -> None:
        """The same seed computes the same vectors twice."""
        record = battery_terms.run_seed(MINI_CONFIG, seed=7)
        assert record["seed"] == 7
        assert record["repeat_divergences"] == 0
        assert [r["case"] for r in record["results"]] == ["B1", "C1", "B4", "B6"]
        assert record["results"][1]["f_value_amendment_terms"] == 1
        assert record["results"][3]["f_value_amendment_terms"] is None


class TestEvaluateFast:
    """Fast coverage of the prediction evaluator (success and refutations)."""

    def test_success_on_matching_records(self) -> None:
        record = battery_terms.run_seed(MINI_CONFIG, seed=7)
        summary = battery_terms.evaluate(MINI_CONFIG, [record])
        assert summary["verdict"] == "success"
        assert summary["battery"] == "D-05b-mini"

    def test_q1_refuted_by_vector_divergence(self) -> None:
        record = battery_terms.run_seed(MINI_CONFIG, seed=7)
        record["results"][1]["total_days_extended"] = 5  # C1 expects 0
        summary = battery_terms.evaluate(MINI_CONFIG, [record])
        assert summary["predictions"]["Q1"]["verdict"] == "refuted"
        assert summary["verdict"] == "refuted"

    def test_q1_refuted_by_repeat_divergences(self) -> None:
        record = battery_terms.run_seed(MINI_CONFIG, seed=7)
        record["repeat_divergences"] = 1
        summary = battery_terms.evaluate(MINI_CONFIG, [record])
        assert summary["predictions"]["Q1"]["verdict"] == "refuted"

    def test_q2_anchors_on_the_real_config(self) -> None:
        """Q2 reads B1/B4/B6: tampering any of them refutes it."""
        record = battery_terms.run_seed(CONFIG, seed=13)
        b4 = next(r for r in record["results"] if r["case"] == "B4")
        b4["f_value_amendment_terms"] = 1  # pure reajuste must never fire
        summary = battery_terms.evaluate(CONFIG, [record])
        assert summary["predictions"]["Q2"]["verdict"] == "refuted"


class TestRunBatteryFast:
    """Offline end-to-end flow: per-seed outputs plus summary on disk."""

    def test_writes_raw_outputs_and_summary(self, tmp_path: Path) -> None:
        records = battery_terms.run_battery(MINI_CONFIG, tmp_path)

        assert len(records) == 1
        lines = (tmp_path / "seed_7.jsonl").read_text().strip().splitlines()
        assert len(lines) == len(MINI_CONFIG["cases"])
        payload = json.loads(lines[3])
        assert payload["case"] == "B6"
        assert payload["payload"] is None  # failed query persisted as null
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["battery"] == "D-05b-mini"
        assert summary["verdict"] == "success"

    def test_shipped_config_passes_offline(self, tmp_path: Path) -> None:
        """The reference D-05b config runs green without any infra."""
        battery_terms.run_battery(CONFIG, tmp_path)
        summary = json.loads((tmp_path / "summary.json").read_text())
        assert summary["battery"] == "D-05b"
        assert summary["verdict"] == "success"
