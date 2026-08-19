"""Download of CNPJ dumps from the Federal Revenue Service.

Chunk: federal_revenue
Responsibility: Download and process CNPJ, corporate structure
and CNAE data from the Federal Revenue open data portal.

Dependencies: requests, pandas
"""

from __future__ import annotations

import logging
import re
import zipfile
from pathlib import Path
from typing import Any

import pandas as pd
import requests

from capiba.config import FEDERAL_REVENUE_BASE_URL

logger = logging.getLogger(__name__)

# Known sources (check availability):
# - SERPRO+ (official): https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9
#   (downloads via the public DAV endpoint /public.php/dav/files/<token>/<month>/<file>)
# - Federal Revenue Open Data (when available): https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj
# The default URL points to the SERPRO+ DAV endpoint; the path and file names
# must be adjusted according to the reference month's structure.

# Typical files of a CNPJ open data month.
FEDERAL_REVENUE_DEFAULT_FILES = [
    "Cnaes.zip",
    "Empresas0.zip",
    "Empresas1.zip",
    "Empresas2.zip",
    "Empresas3.zip",
    "Empresas4.zip",
    "Empresas5.zip",
    "Empresas6.zip",
    "Empresas7.zip",
    "Empresas8.zip",
    "Empresas9.zip",
    "Estabelecimentos0.zip",
    "Estabelecimentos1.zip",
    "Estabelecimentos2.zip",
    "Estabelecimentos3.zip",
    "Estabelecimentos4.zip",
    "Estabelecimentos5.zip",
    "Estabelecimentos6.zip",
    "Estabelecimentos7.zip",
    "Estabelecimentos8.zip",
    "Estabelecimentos9.zip",
    "Motivos.zip",
    "Municipios.zip",
    "Naturezas.zip",
    "Paises.zip",
    "Qualificacoes.zip",
    "Simples.zip",
    "Socios0.zip",
    "Socios1.zip",
    "Socios2.zip",
    "Socios3.zip",
    "Socios4.zip",
    "Socios5.zip",
    "Socios6.zip",
    "Socios7.zip",
    "Socios8.zip",
    "Socios9.zip",
]


def _dav_base_url(base_url: str) -> str:
    """Derives the public WebDAV endpoint from a Nextcloud share URL.

    The ``/index.php/s/<token>/download?path=...&files=...`` route redirects
    to the DAV endpoint dropping the ``path`` parameter (the reply is an
    empty 200 page), so downloads must address the DAV endpoint directly.

    Args:
        base_url: Share URL, either ``.../index.php/s/<token>[/download]``
            or already the public DAV endpoint ``.../public.php/dav/files/<token>``.

    Returns:
        Public DAV base URL (without trailing slash).
    """
    if "/public.php/dav/files/" in base_url:
        return base_url.rstrip("/")
    match = re.search(r"/index\.php/s/([^/]+)", base_url)
    if match:
        host = base_url.split("/index.php")[0]
        return f"{host}/public.php/dav/files/{match.group(1)}"
    return base_url.rstrip("/")


def _build_download_url(base_url: str, path: str, filename: str) -> str:
    """Builds a file download URL for a Nextcloud public share.

    Args:
        base_url: Base URL of the share (either the ``index.php/s/<token>``
            form or the public DAV endpoint).
        path: Relative path within the share (e.g. "/2025-01/").
        filename: File name.

    Returns:
        Full URL.
    """
    dav = _dav_base_url(base_url)
    normalized_path = path.strip("/")
    if normalized_path:
        return f"{dav}/{normalized_path}/{filename}"
    return f"{dav}/{filename}"


def _is_valid_zip(file_path: Path) -> bool:
    """Checks the ZIP magic bytes (PK\\x03\\x04).

    The Federal Revenue shares are flaky: they can answer 200 with an empty
    body or an HTML error page instead of the ZIP, so the HTTP status alone
    is not enough to consider the download successful.
    """
    with open(file_path, "rb") as f:
        return f.read(4)[:2] == b"PK"


def download_cnpj_dump(
    destination: Path,
    reference_month: str,
    base_url: str = FEDERAL_REVENUE_BASE_URL,
    files: list[str] | None = None,
) -> list[Path]:
    """Downloads the monthly CNPJ dump from the Federal Revenue Service.

    Args:
        destination: Directory where the files will be saved.
        reference_month: Month in YYYY-MM format (typical SERPRO+ directory).
        base_url: Base URL of the file share.
        files: List of files to download. Default: FEDERAL_REVENUE_DEFAULT_FILES.

    Returns:
        List of paths of the downloaded ZIP files.
    """
    destination.mkdir(parents=True, exist_ok=True)
    files = files if files is not None else FEDERAL_REVENUE_DEFAULT_FILES
    downloaded: list[Path] = []

    for name in files:
        url = _build_download_url(base_url, f"/{reference_month}/", name)
        file_path = destination / name

        if file_path.exists():
            logger.info("File already exists: %s", file_path)
            downloaded.append(file_path)
            continue

        logger.info("Downloading CNPJ: %s", url)
        try:
            with requests.get(url, stream=True, timeout=120) as response:
                response.raise_for_status()
                with open(file_path, "wb") as f:
                    for chunk in response.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)
            if not _is_valid_zip(file_path):
                # The share answered 200 with an empty body or HTML error
                # page: treat as a failed download, not as data.
                logger.warning("Invalid ZIP payload for %s (share error page?)", name)
                file_path.unlink()
                continue
            downloaded.append(file_path)
            logger.info(
                "Download finished: %s (%d bytes)", file_path, file_path.stat().st_size
            )
        except requests.RequestException as exc:
            logger.warning("Failed to download %s: %s", name, exc)

    return downloaded


def extract_cnpj_zip(zip_path: Path, destination: Path | None = None) -> list[Path]:
    """Extracts CSVs from a CNPJ ZIP file.

    Args:
        zip_path: Path of the ZIP file.
        destination: Extraction directory. Default: the ZIP's own directory.

    Returns:
        List of paths of the extracted CSVs.
    """
    destination = destination or zip_path.parent
    destination.mkdir(parents=True, exist_ok=True)

    extracted: list[Path] = []
    with zipfile.ZipFile(zip_path, "r") as zf:
        for name in zf.namelist():
            if name.lower().endswith(".csv"):
                zf.extract(name, destination)
                extracted.append(destination / name)
    return extracted


def parse_cnpj_csv(csv_file: Path) -> Any:
    """Parses a CNPJ CSV file into a DataFrame.

    Args:
        csv_file: Path of the CSV file.

    Returns:
        DataFrame with CNPJ data.
    """
    return pd.read_csv(
        csv_file,
        sep=";",
        encoding="latin1",
        dtype=str,
        header=None,
        low_memory=False,
    )
