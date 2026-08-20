"""Download of the TSE campaign finance dump (prestação de contas).

Chunk: tse
Responsibility: Download the prestação de contas eleitorais and the
consulta_cand (elected candidates) ZIPs of the configured election year
from the TSE open data CDN.

The dump is a **fixed snapshot per election** (the 2024 municipal
elections by default), republished while the accounts are judged — it is
not month-indexed, so the ``reference_month`` argument of the registry
downloader contract is accepted but ignored; the election year and base
URL are parameters (``year``/``base_url``).

Dependencies: requests
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import requests

from capiba.config import TSE_BASE_URL, TSE_CANDIDATES_BASE_URL, TSE_ELECTION_YEAR
from capiba.ingestion.crawler_federal_revenue import _is_valid_zip

logger = logging.getLogger(__name__)


def tse_dump_filename(year: int) -> str:
    """Canonical file name of the candidates' prestação de contas dump."""
    return f"prestacao_de_contas_eleitorais_candidatos_{year}.zip"


def tse_candidates_filename(year: int) -> str:
    """Canonical file name of the candidacies dump (who was elected)."""
    return f"consulta_cand_{year}.zip"


def _base_url_for(name: str, base_url: str, candidates_base_url: str) -> str:
    """The CDN directory of a dump file (the two dumps are siblings)."""
    if name.startswith("consulta_cand_"):
        return candidates_base_url.rstrip("/")
    return base_url.rstrip("/")


def download_tse_dump(
    destination: Path,
    reference_month: str,
    year: int = TSE_ELECTION_YEAR,
    base_url: str = TSE_BASE_URL,
    candidates_base_url: str = TSE_CANDIDATES_BASE_URL,
    files: list[str] | None = None,
    skip: set[str] | None = None,
    on_file: Callable[[Path], None] | None = None,
) -> list[Path]:
    """Downloads the TSE dumps of an election year.

    Args:
        destination: Directory where the files will be saved.
        reference_month: Registry contract argument; **ignored** — the dump
            is a fixed snapshot per election, not a monthly partition.
        year: Election year of the snapshot (default: TSE_ELECTION_YEAR).
        base_url: Base URL of the prestação de contas directory on the TSE
            CDN.
        candidates_base_url: Base URL of the consulta_cand directory (the
            elected-candidates gate of the political_connection signal).
        files: File names to download. Default: the candidates' prestação
            de contas dump and the consulta_cand dump of ``year``.
        skip: File names to skip (already uploaded to the bronze layer by a
            previous attempt — resume semantics of the download task).
        on_file: Optional callback invoked with each downloaded path right
            after it lands (lets the caller upload/remove it immediately).

    Returns:
        List of paths of the downloaded ZIP files (excluding skipped ones).
    """
    del reference_month  # fixed snapshot per election; documented above
    destination.mkdir(parents=True, exist_ok=True)
    files = (
        files
        if files is not None
        else [tse_dump_filename(year), tse_candidates_filename(year)]
    )
    skip = skip or set()
    downloaded: list[Path] = []

    for name in files:
        if name in skip:
            logger.info("Skipping %s (already uploaded to the bronze layer)", name)
            continue
        base = _base_url_for(name, base_url, candidates_base_url)
        url = f"{base}/{name}"
        file_path = destination / name

        if file_path.exists():
            logger.info("File already exists: %s", file_path)
            downloaded.append(file_path)
            continue

        logger.info("Downloading TSE dump: %s", url)
        try:
            with requests.get(url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with open(file_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            if not _is_valid_zip(file_path):
                # The CDN can answer 200/403 with an HTML error page instead
                # of the ZIP (Akamai geo-restriction): not data.
                logger.warning("Invalid ZIP payload for %s (CDN error page?)", name)
                file_path.unlink()
                continue
            downloaded.append(file_path)
            logger.info(
                "Download finished: %s (%d bytes)", file_path, file_path.stat().st_size
            )
            if on_file is not None:
                on_file(file_path)
        except requests.RequestException as exc:
            logger.warning("Failed to download %s: %s", name, exc)

    return downloaded
