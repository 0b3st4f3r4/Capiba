"""Tests for the red-flags battery runner (bateria D-04).

Responsibility: Validate the planted-case generator (determinism, seed
variation) and the evaluation of the pre-registered predictions P1-P5
(docs/preregistrations/PR-D-04.md), using the declarative config
experiments/detect/D-04.json.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery_flags

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-04.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture
def records(tmp_path: Path) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir and returns the per-seed records."""
    return battery_flags.run_battery(CONFIG, tmp_path)


def test_generate_cases_deterministic_per_seed() -> None:
    """The same seed reproduces the same case payloads, bit a bit."""
    first = battery_flags.generate_cases(CONFIG, seed=7)
    second = battery_flags.generate_cases(CONFIG, seed=7)
    assert first == second


def test_generate_cases_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_flags.generate_cases(CONFIG, seed=7)
    second = battery_flags.generate_cases(CONFIG, seed=17)
    assert first != second


def test_generate_cases_structure() -> None:
    """One case per config entry, nulls planted as declared."""
    cases = battery_flags.generate_cases(CONFIG, seed=7)
    assert [c["case"] for c in cases] == [c["id"] for c in CONFIG["cases"]]
    by_case = {c["case"]: c for c in cases}
    # K7: no proposal dates; K8: no dates and no values
    assert "dataAberturaProposta" not in by_case["K7"]["payload"]
    assert "valorInicialCompra" not in by_case["K8"]["payload"]
    # K9: malformed fields instead of absent ones
    assert by_case["K9"]["payload"]["dataAberturaProposta"] == "n/a"
    assert by_case["K9"]["payload"]["valorInicialCompra"] == "abc"


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """All pre-registered predictions hold on the reference config."""
    summary = battery_flags.evaluate(CONFIG, records)
    for prediction in ("P1", "P2", "P3", "P4", "P5"):
        assert summary["predictions"][prediction]["verdict"] == "success", summary[
            "predictions"
        ][prediction]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(tmp_path: Path) -> None:
    """Raw per-seed outputs and the summary are persisted."""
    battery_flags.run_battery(CONFIG, tmp_path)
    for seed in CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(lines) == len(CONFIG["cases"])
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-04"


def test_evaluate_detects_refutation() -> None:
    """A tampered flag refutes P1 and the battery."""
    record = battery_flags.run_seed(CONFIG, seed=7)
    record["results"][0]["f_non_competitive"] = 1  # K1 expects 0
    summary = battery_flags.evaluate(CONFIG, [record])
    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"
