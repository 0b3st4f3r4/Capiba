"""Resolution of the TSE campaign finance dumps (prestação de contas).

Chunk: tse
Responsibility: Resolve the prestação de contas eleitorais and the
consulta_cand (elected candidates) ZIPs of the configured election year
from the **frozen bronze anchor** (``tse/reference/``), never from the
TSE CDN.

The TSE CDN is unreachable from CLI clients (Akamai Bot Manager answers
403 — confirmed 2026-08-21), so the dumps are obtained once via a
Brazilian IP and uploaded manually to the bronze bucket under
``tse/reference/`` (contract of ``experiments/detect/D-08b.json``,
``reference_source.bronze_prefix``; obtention recorded in PR-D-08b).
The pipeline copies the anchor files of the election year into the run
partition (``tse/files/dt=<run>/``) through the registry downloader
contract.

The dump is a **fixed snapshot per election** (the 2024 municipal
elections by default) — it is not month-indexed, so the
``reference_month`` argument of the registry downloader contract is
accepted but ignored; the election year is a parameter (``year``).

Dependencies: capiba.pipeline.lake (lazy, bronze MinIO client)
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

from capiba.config import TSE_ELECTION_YEAR
from capiba.ingestion.crawler_federal_revenue import _is_valid_zip

logger = logging.getLogger(__name__)

# Frozen anchor of the official TSE dumps in the bronze bucket (uploaded
# once, manually, per election year — the CDN blocks CLI clients).
TSE_REFERENCE_PREFIX = "tse/reference/"


def tse_dump_filename(year: int) -> str:
    """Canonical file name of the candidates' prestação de contas dump."""
    return f"prestacao_de_contas_eleitorais_candidatos_{year}.zip"


def tse_candidates_filename(year: int) -> str:
    """Canonical file name of the candidacies dump (who was elected)."""
    return f"consulta_cand_{year}.zip"


def _available_reference_files() -> dict[str, str]:
    """Maps file name -> bronze key of every object in the frozen anchor."""
    from capiba.pipeline import lake

    return {
        key.rsplit("/", 1)[-1]: key
        for key in lake.list_bronze_objects(TSE_REFERENCE_PREFIX)
    }


def download_tse_dump(
    destination: Path,
    reference_month: str,
    year: int = TSE_ELECTION_YEAR,
    files: list[str] | None = None,
    skip: set[str] | None = None,
    on_file: Callable[[Path], None] | None = None,
) -> list[Path]:
    """Resolves the TSE dumps of an election year from the bronze anchor.

    Reads the frozen reference dumps (``tse/reference/``) of the bronze
    bucket and materializes them locally so the caller uploads them to the
    run partition — no HTTP call is made (the TSE CDN blocks CLI clients).

    Args:
        destination: Directory where the files will be saved.
        reference_month: Registry contract argument; **ignored** — the dump
            is a fixed snapshot per election, not a monthly partition.
        year: Election year of the snapshot (default: TSE_ELECTION_YEAR).
        files: File names to resolve. Default: the candidates' prestação
            de contas dump and the consulta_cand dump of ``year``.
        skip: File names to skip (already uploaded to the bronze layer by a
            previous attempt — resume semantics of the download task).
        on_file: Optional callback invoked with each resolved path right
            after it lands (lets the caller upload/remove it immediately).

    Returns:
        List of paths of the resolved ZIP files (excluding skipped ones).

    Raises:
        RuntimeError: If the anchor of ``year`` is incomplete — the message
            instructs the manual upload to ``tse/reference/`` (there is no
            automatic fallback, so a missing anchor never retries silently).
    """
    from capiba.pipeline import lake

    del reference_month  # fixed snapshot per election; documented above
    destination.mkdir(parents=True, exist_ok=True)
    files = (
        files
        if files is not None
        else [tse_dump_filename(year), tse_candidates_filename(year)]
    )
    skip = skip or set()

    available = _available_reference_files()
    wanted = [name for name in files if name not in skip]
    missing = [name for name in wanted if name not in available]
    if missing:
        raise RuntimeError(
            "TSE reference anchor incomplete in the bronze layer "
            f"({TSE_REFERENCE_PREFIX}): missing {missing}. The TSE CDN "
            "blocks CLI clients (Akamai 403), so download the dumps via a "
            "Brazilian IP and upload them manually — e.g. "
            "`mc cp <file> capiba/${CAPIBA_BUCKET_BRONZE:-capiba-bronze}/"
            f"{TSE_REFERENCE_PREFIX}<file>` (contract: "
            "experiments/detect/D-08b.json, reference_source.bronze_prefix)."
        )

    resolved: list[Path] = []
    for name in files:
        if name in skip:
            logger.info("Skipping %s (already uploaded to the bronze layer)", name)
            continue
        key = available[name]
        file_path = destination / name
        if file_path.exists():
            logger.info("File already exists: %s", file_path)
            resolved.append(file_path)
            continue

        logger.info("Resolving TSE dump from the bronze anchor: %s", key)
        data = lake.read_bronze_file(key)
        file_path.write_bytes(data)
        if not _is_valid_zip(file_path):
            # A corrupt anchor object is not data: fail loudly instead of
            # landing a broken snapshot in the run partition.
            file_path.unlink()
            raise RuntimeError(
                f"TSE reference object {key} is not a valid ZIP; "
                f"re-upload the dump to {TSE_REFERENCE_PREFIX}."
            )
        resolved.append(file_path)
        logger.info(
            "TSE dump resolved: %s (%d bytes)", file_path, file_path.stat().st_size
        )
        if on_file is not None:
            on_file(file_path)

    return resolved
