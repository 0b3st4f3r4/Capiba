#!/usr/bin/env python3
"""Runs a detection battery from its declarative config.

The battery must be pre-registered (docs/preregistrations/PR-D-*.md)
before execution. Raw per-seed outputs are written to
``results/detect/<id>/`` and the verdict summary to ``summary.json``.

Usage:
    python scripts/detect_battery.py experiments/detect/D-01.json [--out DIR]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from capiba.detection import (  # noqa: E402
    battery,
    battery_amendments,
    battery_flags,
    battery_graphs,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="Battery config (JSON)")
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output dir (default: results/detect/<id>)",
    )
    args = parser.parse_args()

    config = json.loads(args.config.read_text())
    out_dir = args.out or Path("results") / "detect" / config["id"]

    runner_name = config.get("runner")
    if runner_name == "flags":
        runner = battery_flags
    elif runner_name == "amendments":
        runner = battery_amendments
    elif config.get("requires_infra") == "arangodb":
        runner = battery_graphs
    else:
        runner = battery
    runner.run_battery(config, out_dir)
    summary = json.loads((out_dir / "summary.json").read_text())

    print(f"Bateria {summary['battery']}: {summary['verdict']}")
    for name, prediction in summary["predictions"].items():
        details = {
            k: v for k, v in prediction.items() if k not in ("verdict", "failures")
        }
        print(f"  {name}: {prediction['verdict']} {details or ''}")
        for failure in prediction.get("failures", []):
            print(f"    - {failure}")
    return 0 if summary["verdict"] == "success" else 1


if __name__ == "__main__":
    sys.exit(main())
