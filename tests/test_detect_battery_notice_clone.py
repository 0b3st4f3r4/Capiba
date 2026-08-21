"""Tests for the notice_clone battery runner (bateria D-10b).

Responsibility: Run the pre-registered battery over the declarative
config experiments/detect/D-10b.json with the real pinned encoder
(sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, CPU) and
validate the predictions P1-P7 (docs/preregistrations/PR-D-10.md, with P2
recalibrated as P2b by PR-D-10b: signal + rank <= 4, no score floor
beyond the 0.85 threshold), including the exact anchors N0 (score
1.0000, rank 1) and N6 (exactly 12 segmented units). P6b/P8 (real pilot
sample) and P9 (post-integration) live outside the battery.

Battery/regime test, not a unit test. Skipped by default; run with
CAPIBA_SLOW=1 (the CI and `make test-cov`/`make test-slow` enable it).
Downloads the encoder model on the first run.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from batteries import battery_notice_clone

pytestmark = pytest.mark.slow

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "experiments" / "detect" / "D-10b.json"
CONFIG: dict[str, Any] = json.loads(CONFIG_PATH.read_text())


@pytest.fixture(scope="module")
def records(tmp_path_factory: pytest.TempPathFactory) -> list[dict[str, Any]]:
    """Runs the battery into a temp dir (real encoder, all seeds)."""
    out_dir = tmp_path_factory.mktemp("d10b")
    return battery_notice_clone.run_battery(CONFIG, out_dir)


def test_battery_predictions_pass(records: list[dict[str, Any]]) -> None:
    """The anchors and the fixed bands hold on all seeds — D-10b verdict.

    Pins the official D-10b outcome (PR-D-10b, 2026-08-21): P2 in its
    recalibrated P2b form (signal + rank <= 4) succeeds on all five
    seeds, and P1/P3/P4/P5/P6/P7 are confirmed unchanged. The legacy
    D-10 band (score >= 0.95, rank 1) stays refuted — D-10.json is
    preserved as the historical record of the refuted form.
    """
    summary = battery_notice_clone.evaluate(CONFIG, records)
    for name in ("P1", "P2", "P3", "P4", "P5", "P6", "P7"):
        assert summary["predictions"][name]["verdict"] == "success", summary[
            "predictions"
        ][name]
    assert summary["verdict"] == "success"


def test_n0_anchor_exact(records: list[dict[str, Any]]) -> None:
    """Every N0 exact copy scores 1.0000 (±1e-9) with rank 1."""
    for record in records:
        n0 = [r for r in record["ranks"] if r["case"] == "N0"]
        assert len(n0) == 2
        for rank in n0:
            assert rank["similarity"] == pytest.approx(1.0, abs=1e-9)
            assert rank["rank"] == 1


def test_n6_segmentation_anchor(records: list[dict[str, Any]]) -> None:
    """The N6 edition recovers exactly the 12 planted units."""
    for record in records:
        assert record["n6_units"] == 12


def test_determinism_per_seed(records: list[dict[str, Any]]) -> None:
    """Each seed reproduced its signals bit a bit on the repeat run."""
    for record in records:
        assert record["repeat_divergences"] == 0


def test_battery_writes_raw_outputs(tmp_path: Path) -> None:
    """Raw per-seed outputs, per-seed measures and the summary are written."""
    battery_notice_clone.run_battery({**CONFIG, "seeds": [13]}, tmp_path)
    assert (tmp_path / "seed_13.jsonl").exists()
    measures = json.loads((tmp_path / "measures_seed_13.json").read_text())
    assert measures["seed"] == 13
    assert len(measures["n1"]) == 8
    summary = json.loads((tmp_path / "summary.json").read_text())
    assert summary["battery"] == "D-10b"
