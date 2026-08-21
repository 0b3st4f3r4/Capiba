"""Fast (non-slow) tests for the notice_clone battery runner (D-10).

Responsibility: Exercise the runner end to end — synthetic corpus
generation, segmentation chain, prediction evaluation, success/refutation
verdicts and raw-output writing — with a deterministic stub encoder and a
minimal inline config, so the fast suite covers the module without the
real sentence encoder (the slow regime test reads
``experiments/detect/D-10.json`` and loads the pinned model).

The stub is a hashing bag-of-words: exact copies score 1.0 (the N0
anchor is encoder-independent) and the reedition veto / segmentation /
determinism disciplines do not depend on the encoder either. The
encoder-dependent bands (P2 scores, P3/P4) are set to stub-appropriate
values here; the official validation is the slow battery.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import numpy as np

from capiba.detection import battery_notice_clone

# Minimal config with the full case structure at reduced counts; the
# bands suit the stub encoder (structural minutas score high under a
# bag-of-words, so the P4 band is permissive here).
FAST_CONFIG: dict[str, Any] = {
    "id": "D-10-fast",
    "seeds": [7, 11],
    "territory_id": "2611606",
    "segmentation_markers": ["EDITAL", "AVISO DE LICITACAO", "EXTRATO", "PROCESSO"],
    "encoder": {"model": "stub", "device": "cpu"},
    "thresholds": {
        "notice_clone_threshold": 0.5,
        "min_notice_chars": 200,
        "max_unit_bytes": 51200,
        "window_days": 365,
        "score_decimals": 4,
        "anchor_tolerance": 1e-9,
    },
    "synthetic": {
        "base_notices": 30,
        "cases": [
            {"id": "N0", "count": 1, "expected": {"signal": True, "score": 1.0, "rank": 1}},
            {"id": "N1", "count": 2, "expected": {"signal": True, "min_score": 0.5, "rank": 1}},
            {"id": "N2", "count": 2, "expected": {"signal": True}},
            {"id": "N3", "count": 2, "expected": {"signal": False}},
            {"id": "N4", "count": 1, "expected": {"signal": False}},
            {"id": "N5", "count": "remaining", "expected": {"signal": False}},
            {"id": "N6", "count": 1, "expected": {"units": 12}},
        ],
    },
    "bands": {"p3_min_recall": 0.5, "p4_max_fp_rate": 1.0},
}


def stub_encode(texts: list[str]) -> np.ndarray:
    """Deterministic hashing bag-of-words encoder (offline stub)."""
    dim = 128
    vectors = np.zeros((len(texts), dim))
    for i, text in enumerate(texts):
        for token in re.findall(r"\w+", text.lower()):
            digest = hashlib.sha256(token.encode()).digest()
            vectors[i, int.from_bytes(digest[:4], "little") % dim] += 1.0
    return vectors


def test_run_battery_success_end_to_end(tmp_path: Path) -> None:
    """The minimal config runs the full pipeline to a success verdict."""
    records = battery_notice_clone.run_battery(FAST_CONFIG, tmp_path, stub_encode)

    assert len(records) == 2
    for record in records:
        assert record["repeat_divergences"] == 0
        assert record["n6_units"] == 12  # N6 segmentation anchor
        n0 = [r for r in record["ranks"] if r["case"] == "N0"]
        assert len(n0) == 1
        assert n0[0]["similarity"] == 1.0  # N0 exact-copy anchor
        assert n0[0]["rank"] == 1

    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-10-fast"
    assert summary["verdict"] == "success", summary
    for name in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
        assert summary["predictions"][name]["verdict"] == "success"

    for seed in FAST_CONFIG["seeds"]:
        lines = (tmp_path / f"seed_{seed}.jsonl").read_text().strip().splitlines()
        signals = [json.loads(line) for line in lines]
        assert all(s["signal_type"] == "notice_clone" for s in signals)
        # The N0 exact copy always signals with score 1.0.
        assert any(s["score"] == 1.0 for s in signals)


def test_generate_population_deterministic_per_seed() -> None:
    """The same seed reproduces the same corpus, bit a bit."""
    first = battery_notice_clone.generate_population(FAST_CONFIG, seed=17)
    second = battery_notice_clone.generate_population(FAST_CONFIG, seed=17)
    assert first == second


def test_generate_population_seed_variation() -> None:
    """Different seeds randomize the neutral fields (slots)."""
    first = battery_notice_clone.generate_population(FAST_CONFIG, seed=17)
    second = battery_notice_clone.generate_population(FAST_CONFIG, seed=27)
    assert first != second


def test_generate_population_structure() -> None:
    """The planted case structure follows the declared counts."""
    population = battery_notice_clone.generate_population(FAST_CONFIG, seed=17)
    cases = [p["case"] for p in population["meta"]["pairs"]]
    assert cases.count("N0") == 1
    assert cases.count("N1") == 2
    assert cases.count("N2") == 2
    assert cases.count("N3") == 2
    assert cases.count("N4") == 1
    # N5 = remaining base notices: 30 - (1 + 2 + 2) historical sources.
    n6 = [e for e in population["editions"] if e["is_n6"]]
    assert len(n6) == 1


def test_evaluate_refutes_on_dropped_anchor() -> None:
    """Dropping the N0 signal refutes P1 and the battery."""
    record = battery_notice_clone.run_seed(FAST_CONFIG, 7, stub_encode)
    n0 = next(r for r in record["ranks"] if r["case"] == "N0")
    record["signals"] = [
        s
        for s in record["signals"]
        if json.loads(s["details"])["new_notice_id"] != n0["new_id"]
    ]

    summary = battery_notice_clone.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P1"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_reedition_signal() -> None:
    """A forged signal over the N4 pair refutes P5."""
    record = battery_notice_clone.run_seed(FAST_CONFIG, 7, stub_encode)
    n4 = next(m for m in record["meta_pairs"] if m["case"] == "N4")
    record["signals"] = record["signals"] + [
        {
            "entity_type": "notice",
            "entity_id": "forged",
            "signal_type": "notice_clone",
            "score": 1.0,
            "details": json.dumps(
                {
                    "new_notice_id": n4["new_id"],
                    "historical_notice_id": n4["hist_id"],
                }
            ),
        }
    ]

    summary = battery_notice_clone.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P5"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_segmentation_count() -> None:
    """A segmentation count off the N6 anchor refutes P6."""
    record = battery_notice_clone.run_seed(FAST_CONFIG, 7, stub_encode)
    record["n6_units"] = 11

    summary = battery_notice_clone.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P6"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_on_repeat_divergence() -> None:
    """A non-deterministic run refutes P7."""
    record = battery_notice_clone.run_seed(FAST_CONFIG, 7, stub_encode)
    record["repeat_divergences"] = 1

    summary = battery_notice_clone.evaluate(FAST_CONFIG, [record])

    assert summary["predictions"]["P7"]["verdict"] == "refuted"
    assert summary["verdict"] == "refuted"


def test_evaluate_refutes_when_bands_missing() -> None:
    """Without the exploratory bands, P3/P4 are not verifiable."""
    config = {**FAST_CONFIG}
    config.pop("bands")
    record = battery_notice_clone.run_seed(config, 7, stub_encode)

    summary = battery_notice_clone.evaluate(config, [record])

    assert summary["predictions"]["P3"]["verdict"] == "refuted"
    assert summary["predictions"]["P4"]["verdict"] == "refuted"


def test_evaluate_refutes_when_band_exceeded() -> None:
    """Bands stricter than the measured values refute P3 and P4."""
    config = {
        **FAST_CONFIG,
        "bands": {"p3_min_recall": 1.1, "p4_max_fp_rate": -0.1},
    }
    record = battery_notice_clone.run_seed(config, 7, stub_encode)

    summary = battery_notice_clone.evaluate(config, [record])

    assert summary["predictions"]["P3"]["verdict"] == "refuted"
    assert summary["predictions"]["P4"]["verdict"] == "refuted"
