"""Tests for the contract-terms battery runner (bateria D-05b).

Responsibility: Validate the planted term-list generator (determinism,
seed variation, planted structure) and the evaluation of the
pre-registered predictions Q1-Q2 (docs/preregistrations/PR-D-05b.md),
using the declarative config experiments/detect/D-05b.json. The real-data
probes Q3-Q5 (pilot cut) are outside this runner.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batteries import battery_terms

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-05b.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def records(tmp_path: Path) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir and returns the per-seed records."""
    return battery_terms.run_battery(CONFIG, tmp_path)


def test_generate_cases_deterministic_per_seed() -> None:
    """The same seed reproduces the same term lists, bit a bit."""
    first = battery_terms.generate_cases(CONFIG, seed=13)
    second = battery_terms.generate_cases(CONFIG, seed=13)
    assert first == second


def test_generate_cases_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_terms.generate_cases(CONFIG, seed=13)
    second = battery_terms.generate_cases(CONFIG, seed=23)
    assert first != second


def test_generate_cases_structure() -> None:
    """One case per config entry; the planted structure holds."""
    cases = battery_terms.generate_cases(CONFIG, seed=13)
    assert [c["case"] for c in cases] == [c["id"] for c in CONFIG["cases"]]
    by_case = {c["case"]: c for c in cases}
    # B1: empty list (HTTP 204); B6: failed query (None, not [])
    assert by_case["B1"]["terms"] == []
    assert by_case["B6"]["terms"] is None
    # B2: amendment with a positive increase; B4: reajuste-only qualification
    b2 = by_case["B2"]["terms"][0]
    assert b2["tipoTermoContratoNome"] == "Termo Aditivo"
    assert b2["qualificacaoAcrescimoSupressao"] is True
    assert b2["valorAcrescido"] == 6840.88
    b4 = by_case["B4"]["terms"][0]
    assert b4["qualificacaoReajuste"] is True
    assert "valorAcrescido" not in b4
    # B5: suppression carries a negative increase
    assert by_case["B5"]["terms"][0]["valorAcrescido"] == -1500.0


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered predictions hold on the reference config."""
    summary = battery_terms.evaluate(CONFIG, records)
    for prediction in ("Q1", "Q2"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(tmp_path: Path) -> None:
    """Raw per-seed outputs and the summary are persisted."""
    battery_terms.run_battery(CONFIG, tmp_path)
    for seed in CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == len(CONFIG["cases"])
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-05b"


def test_evaluate_detects_refutation() -> None:
    """A tampered flag refutes Q1 and the battery."""
    record = battery_terms.run_seed(CONFIG, seed=13)
    record["results"][0]["f_value_amendment_terms"] = 1  # B1 expects 0
    summary = battery_terms.evaluate(CONFIG, [record])
    assert summary["predictions"]["Q1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_detects_null_discipline_inversion() -> None:
    """A failed query computing 0 instead of NULL refutes Q2."""
    record = battery_terms.run_seed(CONFIG, seed=13)
    b6 = next(r for r in record["results"] if r["case"] == "B6")
    b6["f_value_amendment_terms"] = 0
    summary = battery_terms.evaluate(CONFIG, [record])
    assert summary["predictions"]["Q2"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
