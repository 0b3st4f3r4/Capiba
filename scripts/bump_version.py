#!/usr/bin/env python3
"""Bumps the project version in every file that references it.

Usage: python scripts/bump_version.py <new-version>   (e.g. 0.2.0)

Updated locations:
  - pyproject.toml             ([project] version)
  - src/capiba/api/main.py     (FastAPI version)
  - charts/capiba/Chart.yaml   (top-level version + appVersion; dependency versions untouched)
  - charts/capiba/values.yaml  (api/airflow image tags)
  - Makefile                   (docker build tags)
  - uv.lock                    (regenerated via `uv lock`)
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = ROOT / "pyproject.toml"

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def current_version() -> str:
    match = re.search(r'^version = "([^"]+)"', PYPROJECT.read_text(), re.MULTILINE)
    if not match:
        sys.exit("Could not read the current version from pyproject.toml")
    return match.group(1)


def replace(path: Path, pattern: str, repl: str, count: int = 0, flags: int = 0) -> int:
    text = path.read_text()
    new_text, n = re.subn(pattern, repl, text, count=count, flags=flags)
    if n == 0:
        print(f"  WARN: no match for {pattern!r} in {path.relative_to(ROOT)}")
        return 0
    path.write_text(new_text)
    return n


def main() -> None:
    if len(sys.argv) != 2 or not SEMVER_RE.match(sys.argv[1]):
        sys.exit("Usage: bump_version.py <new-version>  (semver, e.g. 0.2.0)")

    new = sys.argv[1]
    old = current_version()
    if new == old:
        sys.exit(f"Version is already {new}")

    old_escaped = re.escape(old)
    print(f"Bumping {old} -> {new}")

    replace(
        PYPROJECT,
        rf'^version = "{old_escaped}"',
        f'version = "{new}"',
        count=1,
        flags=re.MULTILINE,
    )
    replace(
        ROOT / "src/capiba/api/main.py", rf'version="{old_escaped}"', f'version="{new}"'
    )
    # Top-level keys only: dependency versions in Chart.yaml are indented and keep their own values.
    replace(
        ROOT / "charts/capiba/Chart.yaml",
        rf"^version: {old_escaped}$",
        f"version: {new}",
        flags=re.MULTILINE,
    )
    replace(
        ROOT / "charts/capiba/Chart.yaml",
        rf'^appVersion: "{old_escaped}"',
        f'appVersion: "{new}"',
        flags=re.MULTILINE,
    )
    replace(
        ROOT / "charts/capiba/values.yaml", rf'tag: "{old_escaped}"', f'tag: "{new}"'
    )
    replace(ROOT / "Makefile", rf"capiba/api:{old_escaped}", f"capiba/api:{new}")
    replace(
        ROOT / "Makefile", rf"capiba/airflow:{old_escaped}", f"capiba/airflow:{new}"
    )

    try:
        subprocess.run(["uv", "lock"], cwd=ROOT, check=True, capture_output=True)
        print("  uv.lock regenerated")
    except (FileNotFoundError, subprocess.CalledProcessError) as exc:
        print(f"  WARN: could not regenerate uv.lock ({exc}); run `uv lock` manually")

    print(
        "Done. Rebuild the images (make build build-airflow) and upgrade the chart to publish it."
    )


if __name__ == "__main__":
    main()
