"""Extract declared dependencies from pyproject.toml into a requirements file.

Usage:
    python scripts/extract_requirements.py requirements.txt
    python scripts/extract_requirements.py --extra airflow requirements.txt
"""

from __future__ import annotations

import argparse
import tomllib


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract dependencies from pyproject.toml"
    )
    parser.add_argument("output", help="Path to write the requirements file")
    parser.add_argument(
        "--extra",
        action="append",
        default=[],
        help="Optional dependency extra to include (can be repeated)",
    )
    args = parser.parse_args()

    with open("pyproject.toml", "rb") as file:
        cfg = tomllib.load(file)

    deps: list[str] = list(cfg["project"]["dependencies"])
    extras = cfg["project"].get("optional-dependencies", {})
    for extra in args.extra:
        deps.extend(extras.get(extra, []))

    with open(args.output, "w", encoding="utf-8") as out:
        out.write("\n".join(deps))
        out.write("\n")


if __name__ == "__main__":
    main()
