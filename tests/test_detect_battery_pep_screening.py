"""Tests for the PEP-screening battery runner (bateria D-12).

Responsibility: Validate the planted-population generator (determinism,
case structure), the evaluation of the pre-registered synthetic
prediction P2 (exact adapter behavior, yente stubbed) and the OS Pairs
plumbing (fixture stream, no network), using the declarative config
``experiments/detect/D-12.json``. The real ``logic-v2`` benchmark (P3/P4)
runs under the pinned yente venv — without ``nomenklatura`` it is
reported as ``skipped`` here.
Pre-registration: docs/preregistrations/PR-D-12.md.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from capiba.detection import battery_entities, battery_pep_screening

# Battery/regime tests, not unit tests. Skipped by default; run with
# CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-12.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())

_PAIRS_FIXTURE = [
    {
        "judgement": "positive",
        "left": {"properties": {"name": ["John Smith"]}},
        "right": {"properties": {"name": ["John Smith"]}},
    },
    # Homonym: the name-only regime predicts a merge and is wrong — the
    # documented failure mode of name-only matching (PR-D-12 § 2).
    {
        "judgement": "negative",
        "left": {"properties": {"name": ["Maria Souza"]}},
        "right": {"properties": {"name": ["Maria Souza"]}},
    },
    {
        "judgement": "negative",
        "left": {"properties": {"name": ["Maria Souza"]}},
        "right": {"properties": {"name": ["Joao Silva"]}},
    },
]


@pytest.fixture
def records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Runs the battery (fixture OS Pairs stream) into a temp dir."""
    monkeypatch.setattr(
        battery_entities, "_stream_pairs", lambda url: iter(_PAIRS_FIXTURE)
    )
    return battery_pep_screening.run_battery(CONFIG, tmp_path)


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same population, bit a bit."""
    first = battery_pep_screening.generate_population(CONFIG, seed=61)
    second = battery_pep_screening.generate_population(CONFIG, seed=61)
    assert first == second


def test_generate_population_seed_variation() -> None:
    """Different seeds randomize the neutral fields."""
    first = battery_pep_screening.generate_population(CONFIG, seed=61)
    second = battery_pep_screening.generate_population(CONFIG, seed=67)
    assert first != second


def test_generate_population_structure() -> None:
    """Cases and controls planted as declared."""
    population = battery_pep_screening.generate_population(CONFIG, seed=61)
    meta = population["meta"]
    assert sorted(meta) == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]
    n_controls = sum(1 for c in population["contracts"] if "CTRL" in c["id"])
    assert n_controls == CONFIG["synthetic"]["control_suppliers"]
    # Q7: the same supplier in three contracts.
    q7 = [c for c in population["contracts"] if "-Q7-" in c["id"]]
    assert len(q7) == 3
    assert len({json.dumps(c["supplier"], sort_keys=True) for c in q7}) == 1


def test_battery_p2_passes(records: list[dict[str, Any]]) -> None:
    """P2 (exact adapter, yente stubbed) holds on the reference config."""
    summary = battery_pep_screening.evaluate(CONFIG, records)
    assert summary["predictions"]["P2"]["verdict"] == "success", summary["predictions"][
        "P2"
    ]
    assert summary["verdict"] == "success"


def test_battery_writes_raw_outputs(
    records: list[dict[str, Any]], tmp_path: Path
) -> None:
    """Raw per-seed signals and queries, the sample cache and the summary."""
    cases = CONFIG["synthetic"]["cases"]
    n_signals = len(cases["signal_cases"])
    n_queries = 5 + CONFIG["synthetic"]["control_suppliers"]
    for seed in CONFIG["seeds"]:
        signals = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(signals) == n_signals
        queries = (
            (tmp_path / f"seed_{seed}_queries.jsonl").read_text().strip().splitlines()
        )
        assert len(queries) == n_queries
    assert (tmp_path / "pairs_sample.jsonl").exists()
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-12"
    # Fixture, local control (name-only 0.95): the identical-name positive
    # is a tp; the homonym negative is a false positive; the disjoint
    # negative is a true negative.
    control = summary["os_pairs_local_control"]
    assert control["tp"] == 1
    assert control["fp"] == 1
    assert control["tn"] == 1
    # Without the pinned yente install the logic-v2 benchmark is skipped.
    if "skipped" in summary["os_pairs_yente"]:
        assert summary["predictions"]["P3"]["verdict"] == "skipped"


def test_evaluate_detects_refutation() -> None:
    """A tampered signal set refutes P2 and the battery."""
    record = battery_pep_screening.run_seed(CONFIG, seed=61)
    record["signals"] = record["signals"][:-1]  # drop a signal
    summary = battery_pep_screening.evaluate(CONFIG, [record])
    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_os_pairs_yente_refutes_on_low_precision() -> None:
    """Precision below the pre-registered floor refutes P3 (when yente runs)."""
    metrics = battery_pep_screening.evaluate_os_pairs_yente(CONFIG, _PAIRS_FIXTURE)
    if "skipped" in metrics:
        pytest.skip("nomenklatura unavailable in this venv")
    assert metrics["precision"] == 0.0
    assert metrics["p3"]["verdict"] == "refuted"
