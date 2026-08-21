"""Fast (non-slow) tests for the PEP-screening battery runner (bateria D-12).

Responsibility: Exercise the runner end to end — population generation,
the exact-adapter evaluation P2 (success and refutation paths), the
paired local control over the OS Pairs fixture stream and the raw-output
writing — with a minimal inline config (few controls, two seeds) and the
yente backend stubbed, so the fast suite covers the module without the
pinned yente install (without ``nomenklatura`` the in-process ``logic-v2``
benchmark reports ``skipped`` — the same degraded behavior the slow
regime test documents). The full pre-registered battery (5 seeds,
``experiments/detect/D-12.json``) is guarded by
``tests/test_detect_battery_pep_screening.py`` (``@pytest.mark.slow``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batteries import battery_entities, battery_pep_screening

# Minimal config: all Q1-Q7 cases are always planted by the generator; the
# fast variant only shrinks the control suppliers and the seeds.
FAST_CONFIG: dict[str, Any] = {
    "id": "D-12-fast",
    "seeds": [7, 11],
    "yente": {
        "algorithm": "logic-v2",
        "threshold": 0.7,
        "dataset": "br_pep",
    },
    "synthetic": {
        "control_suppliers": 3,
        "cases": {
            "signal_cases": ["Q5"],
            "no_signal_cases": ["Q3", "Q4", "Q6"],
            "query_contract_cases": ["Q1", "Q2", "Q7"],
        },
    },
    "os_pairs": {
        "url": "fixture://pairs",
        "sample_positive": 2,
        "sample_negative": 2,
        "seed": 61,
        "paired_control": "local_matcher_d06b_name_only_threshold_0.95",
        "min_precision": 0.85,
        "min_recall": 0.55,
    },
}

_PAIRS_FIXTURE = [
    {
        "judgement": "positive",
        "left": {"properties": {"name": ["John Smith"]}},
        "right": {"properties": {"name": ["John Smith"]}},
    },
    # Disjoint positive: neither matcher predicts the merge -> fn.
    {
        "judgement": "positive",
        "left": {"properties": {"name": ["John Smith"]}},
        "right": {"properties": {"name": ["Joao Silva"]}},
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


def _stub_yente_modules(monkeypatch: pytest.MonkeyPatch) -> None:
    """Injects fake ``followthemoney``/``nomenklatura`` modules.

    The real comparator only exists in the pinned yente venv; the stub
    (score 1.0 iff the first names are equal) exercises the benchmark
    plumbing — entity building, threshold, confusion matrix and verdicts —
    offline, keeping the module importable without the dependency.
    """
    import sys
    import types

    ftm = types.ModuleType("followthemoney")

    class _FakeEntity:
        def __init__(self, schema: str) -> None:
            self.schema = schema
            self.props: dict[str, list[str]] = {}
            self.id: str | None = None

        def add(self, prop: str, values: list[str], quiet: bool = False) -> None:
            self.props[prop] = list(values)

    class _FakeModel:
        @staticmethod
        def make_entity(schema: str) -> _FakeEntity:
            return _FakeEntity(schema)

    ftm.model = _FakeModel  # type: ignore[attr-defined]

    nomenklatura = types.ModuleType("nomenklatura")
    matching = types.ModuleType("nomenklatura.matching")

    class ScoringConfig:
        @staticmethod
        def defaults() -> None:
            return None

    class _FakeResult:
        def __init__(self, score: float) -> None:
            self.score = score

    class _FakeAlgorithm:
        @staticmethod
        def compare(query: Any, result: Any, config: Any) -> _FakeResult:
            query_name = (query.props.get("name") or [""])[0]
            result_name = (result.props.get("name") or [""])[0]
            return _FakeResult(1.0 if query_name == result_name else 0.0)

    def get_algorithm(name: str) -> type[_FakeAlgorithm]:
        assert name == "logic-v2"
        return _FakeAlgorithm

    matching.ScoringConfig = ScoringConfig  # type: ignore[attr-defined]
    matching.get_algorithm = get_algorithm  # type: ignore[attr-defined]
    nomenklatura.matching = matching  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "followthemoney", ftm)
    monkeypatch.setitem(sys.modules, "nomenklatura", nomenklatura)
    monkeypatch.setitem(sys.modules, "nomenklatura.matching", matching)


@pytest.fixture
def records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Runs the fast battery (fixture OS Pairs stream) into a temp dir."""
    monkeypatch.setattr(
        battery_entities, "_stream_pairs", lambda url: iter(_PAIRS_FIXTURE)
    )
    return battery_pep_screening.run_battery(FAST_CONFIG, tmp_path)


def test_run_battery_success_end_to_end(
    records: list[dict[str, Any]], tmp_path: Path
) -> None:
    """The minimal config runs the full pipeline to a success verdict."""
    assert len(records) == 2
    for record in records:
        assert len(record["signals"]) == 1  # only Q5 signals
        assert record["repeat_divergences"] == 0

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-12-fast"
    assert summary["verdict"] == "success"
    assert summary["predictions"]["P2"]["verdict"] == "success"
    # Without the pinned yente install the logic-v2 benchmark is skipped.
    assert "skipped" in summary["os_pairs_yente"]
    assert summary["predictions"]["P3"]["verdict"] == "skipped"
    assert summary["predictions"]["P4"]["verdict"] == "skipped"
    # Fixture, local control (name-only 0.95): identical-name positive is a
    # tp, the disjoint positive a fn, the homonym negative a fp, the
    # disjoint negative a tn.
    control = summary["os_pairs_local_control"]
    assert control["tp"] == 1
    assert control["fn"] == 1
    assert control["fp"] == 1
    assert control["tn"] == 1

    n_queries = 5 + FAST_CONFIG["synthetic"]["control_suppliers"]
    for seed in FAST_CONFIG["seeds"]:
        signals = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        assert len(signals) == 1
        signal = json.loads(signals[0])
        assert signal["signal_type"] == "pep_supplier_match"
        queries = (
            (tmp_path / f"seed_{seed}_queries.jsonl").read_text().strip().splitlines()
        )
        assert len(queries) == n_queries
    assert (tmp_path / "pairs_sample.jsonl").exists()


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same population, bit a bit."""
    first = battery_pep_screening.generate_population(FAST_CONFIG, seed=7)
    second = battery_pep_screening.generate_population(FAST_CONFIG, seed=7)
    assert first == second
    third = battery_pep_screening.generate_population(FAST_CONFIG, seed=11)
    assert first != third


def test_generate_population_structure() -> None:
    """Cases and controls planted as declared."""
    population = battery_pep_screening.generate_population(FAST_CONFIG, seed=7)
    assert sorted(population["meta"]) == ["Q1", "Q2", "Q3", "Q4", "Q5", "Q6", "Q7"]
    n_controls = sum(1 for c in population["contracts"] if "CTRL" in c["id"])
    assert n_controls == FAST_CONFIG["synthetic"]["control_suppliers"]


def test_evaluate_refutes_on_dropped_signal() -> None:
    """Dropping the only expected signal (Q5) refutes P2."""
    record = battery_pep_screening.run_seed(FAST_CONFIG, seed=7)
    record["signals"] = []

    summary = battery_pep_screening.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_control_signal() -> None:
    """A forged signal on a control supplier refutes P2."""
    record = battery_pep_screening.run_seed(FAST_CONFIG, seed=7)
    record["signals"] = record["signals"] + [
        {
            "entity_type": "supplier",
            "entity_id": "controle-forjado",
            "signal_type": "pep_supplier_match",
            "score": 0.9,
            "details": "{}",
        }
    ]

    summary = battery_pep_screening.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert any("control signals" in f for f in summary["predictions"]["P2"]["failures"])


def test_evaluate_refutes_on_no_signal_case_signal() -> None:
    """A forged signal for Q6 (stub without candidates) refutes P2."""
    record = battery_pep_screening.run_seed(FAST_CONFIG, seed=7)
    meta = battery_pep_screening.generate_population(FAST_CONFIG, seed=7)["meta"]
    record["signals"] = record["signals"] + [
        {
            "entity_type": "supplier",
            "entity_id": meta["Q6"],
            "signal_type": "pep_supplier_match",
            "score": 0.9,
            "details": "{}",
        }
    ]

    summary = battery_pep_screening.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert any("Q6" in f for f in summary["predictions"]["P2"]["failures"])


def test_evaluate_refutes_on_repeat_divergence() -> None:
    """A non-deterministic run refutes P2."""
    record = battery_pep_screening.run_seed(FAST_CONFIG, seed=7)
    record["repeat_divergences"] = 1

    summary = battery_pep_screening.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert any("repeat diverged" in f for f in summary["predictions"]["P2"]["failures"])


def test_evaluate_refutes_on_missing_query() -> None:
    """A dropped FtM query (broken dedup or skipped case) refutes P2."""
    record = battery_pep_screening.run_seed(FAST_CONFIG, seed=7)
    expected_q1 = battery_pep_screening._expected_queries(FAST_CONFIG, seed=7)["Q1"]
    record["queries"] = [q for q in record["queries"] if q != expected_q1]

    summary = battery_pep_screening.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    failures = summary["predictions"]["P2"]["failures"]
    assert any("Q1: 0 queries != 1" in f for f in failures)
    assert any("queries !=" in f for f in failures)


def test_evaluate_refutes_on_q5_score_deviation() -> None:
    """A Q5 score off the best stub candidate refutes P2."""
    record = battery_pep_screening.run_seed(FAST_CONFIG, seed=7)
    meta = battery_pep_screening.generate_population(FAST_CONFIG, seed=7)["meta"]
    for signal in record["signals"]:
        if signal["entity_id"] == meta["Q5"]:
            signal["score"] = 0.1

    summary = battery_pep_screening.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    assert any("Q5: score" in f for f in summary["predictions"]["P2"]["failures"])


def test_evaluate_refutes_on_q5_details_deviation() -> None:
    """Tampered candidate ids or archived query in Q5 details refute P2."""
    record = battery_pep_screening.run_seed(FAST_CONFIG, seed=7)
    meta = battery_pep_screening.generate_population(FAST_CONFIG, seed=7)["meta"]
    for signal in record["signals"]:
        if signal["entity_id"] == meta["Q5"]:
            details = json.loads(signal["details"])
            details["candidates"] = details["candidates"][:1]
            details["query"] = {}
            signal["details"] = json.dumps(details, sort_keys=True)

    summary = battery_pep_screening.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P2"]["verdict"] == "refuted"
    failures = summary["predictions"]["P2"]["failures"]
    assert any("candidate ids" in f for f in failures)
    assert any("archived query" in f for f in failures)


def test_evaluate_os_pairs_local_metrics() -> None:
    """The paired local control computes the confusion matrix by hand."""
    metrics = battery_pep_screening.evaluate_os_pairs_local(FAST_CONFIG, _PAIRS_FIXTURE)
    assert metrics["tp"] == 1
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["tn"] == 1
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5


def test_evaluate_os_pairs_yente_with_stubbed_algorithm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stubbed comparator exercises the benchmark plumbing offline.

    Stub semantics (1.0 iff names equal): identical positive -> tp,
    disjoint positive -> fn, homonym negative -> fp, disjoint negative ->
    tn; precision 0.5 and recall 0.5 refute P3/P4 (floors 0.85/0.55).
    """
    _stub_yente_modules(monkeypatch)

    metrics = battery_pep_screening.evaluate_os_pairs_yente(FAST_CONFIG, _PAIRS_FIXTURE)

    assert metrics["tp"] == 1
    assert metrics["fp"] == 1
    assert metrics["fn"] == 1
    assert metrics["tn"] == 1
    assert metrics["precision"] == 0.5
    assert metrics["recall"] == 0.5
    assert metrics["p3"]["verdict"] == "refuted"
    assert metrics["p4"]["verdict"] == "refuted"


def test_run_battery_with_stubbed_yente_records_verdicts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the stubbed comparator the summary carries real P3/P4 verdicts."""
    _stub_yente_modules(monkeypatch)
    monkeypatch.setattr(
        battery_entities, "_stream_pairs", lambda url: iter(_PAIRS_FIXTURE)
    )

    battery_pep_screening.run_battery(FAST_CONFIG, tmp_path)

    summary = json.loads((tmp_path / "summary.json").read_text())
    # The stub refutes both floors: the battery verdict follows them.
    assert summary["predictions"]["P3"]["verdict"] == "refuted"
    assert summary["predictions"]["P3"]["precision"] == 0.5
    assert summary["predictions"]["P4"]["verdict"] == "refuted"
    assert summary["predictions"]["P4"]["recall"] == 0.5
    assert summary["verdict"] == "refuted"
    assert summary["os_pairs_yente"]["algorithm"] == "logic-v2"


def test_evaluate_os_pairs_yente_skipped_without_nomenklatura() -> None:
    """Without the pinned yente install the benchmark reports ``skipped``."""
    try:
        import nomenklatura  # noqa: F401
    except ImportError:
        metrics = battery_pep_screening.evaluate_os_pairs_yente(
            FAST_CONFIG, _PAIRS_FIXTURE
        )
        assert "skipped" in metrics
    else:
        pytest.skip("nomenklatura available — covered by the slow regime test")
