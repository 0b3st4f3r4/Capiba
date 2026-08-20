"""Public read-only endpoints.

Chunk: public
Responsibility: Expose the public batch export of the LGPD-cleared gold
marts (list and presigned download) and the auto-generated methodology
document, without authentication.

The export lives in the public MinIO bucket under
``marts/<mart>/dt=<YYYY-MM-DD>/`` (CSV + Parquet + manifest), written by
the ``export_public_marts`` task of the ``gold_detection`` DAG. Only
marts in the declarative allowlist
(``capiba.pipeline.public_export.PUBLIC_MARTS``) are ever served —
fail-closed: an unlisted mart is a 404.

Dependencies: fastapi, capiba.pipeline.public_export, minio
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import yaml
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from minio import Minio

from capiba.config import (
    DBT_PROJECT_DIR,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
    PUBLIC_EXPORT_BUCKET,
    PUBLIC_EXPORT_PRESIGN_EXPIRY_S,
)
from capiba.pipeline.public_export import EXCLUDED_MARTS, PUBLIC_MARTS

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/public", tags=["public"])

_HTTP_503_DETAIL = "Public export storage unavailable"

_KEY_RE = re.compile(r"^marts/(?P<mart>[a-z0-9_]+)/dt=(?P<dt>\d{4}-\d{2}-\d{2})/(?P<file>[^/]+)$")

# dags/ and dbt/ are not shipped in the API image (Dockerfile copies src/
# only); the methodology endpoint degrades to the sections it can build.
_PIPELINES_DIR = Path(__file__).resolve().parents[4] / "dags" / "pipelines"
_MARTS_YML = Path(DBT_PROJECT_DIR) / "models" / "marts" / "_marts.yml"


def get_public_storage() -> Minio:
    """Instantiates the MinIO client lazily (connects at request time).

    Deferred so the app imports and starts offline; tests override this
    dependency with a fake.

    Raises:
        HTTPException 503: If the storage is unavailable.
    """
    try:
        return Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
    except Exception as exc:
        logger.warning("Public export storage unavailable: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc


def _list_exports(client: Minio) -> dict[str, dict[str, set[str]]]:
    """Indexes the public bucket: mart -> date -> export formats."""
    exports: dict[str, dict[str, set[str]]] = {}
    for obj in client.list_objects(PUBLIC_EXPORT_BUCKET, prefix="marts/", recursive=True):
        match = _KEY_RE.match(obj.object_name or "")
        if not match or match.group("file") == "manifest.json":
            continue
        fmt = match.group("file").rsplit(".", 1)[-1]
        exports.setdefault(match.group("mart"), {}).setdefault(
            match.group("dt"), set()
        ).add(fmt)
    return exports


@router.get("/marts")
async def list_public_marts(
    storage: Annotated[Minio, Depends(get_public_storage)],
) -> dict[str, Any]:
    """Lists the exported marts and their available dates/formats.

    Raises:
        HTTPException 503: If the storage is unavailable.
    """
    try:
        exports = _list_exports(storage)
    except Exception as exc:
        logger.warning("Public export listing failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return {
        "bucket": PUBLIC_EXPORT_BUCKET,
        "marts": [
            {
                "name": mart,
                "dates": sorted(dates, reverse=True),
                "lgpd_classification": PUBLIC_MARTS.get(mart),
            }
            for mart, dates in sorted(exports.items())
        ],
    }


@router.get("/marts/{name}/{fmt}")
async def download_public_mart(
    name: str,
    fmt: str,
    storage: Annotated[Minio, Depends(get_public_storage)],
    dt: str | None = None,
) -> RedirectResponse:
    """Redirects (302) to a presigned URL of the mart export.

    Downloads the latest exported date by default; ``?dt=YYYY-MM-DD``
    pins a specific export date.

    Raises:
        HTTPException 404: If the mart is not public (fail-closed) or the
            requested format/date has no export.
        HTTPException 503: If the storage is unavailable.
    """
    if name not in PUBLIC_MARTS or fmt not in ("csv", "parquet"):
        raise HTTPException(status_code=404, detail="Public mart not found")
    try:
        dates = _list_exports(storage).get(name, {})
    except Exception as exc:
        logger.warning("Public export lookup failed: %s", exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    available = sorted((day for day, fmts in dates.items() if fmt in fmts), reverse=True)
    day = dt or (available[0] if available else "")
    if day not in dates or fmt not in dates.get(day, set()):
        raise HTTPException(status_code=404, detail="Export not found for this date/format")
    key = f"marts/{name}/dt={day}/{name}.{fmt}"
    try:
        url = storage.presigned_get_object(
            PUBLIC_EXPORT_BUCKET,
            key,
            expires=timedelta(seconds=PUBLIC_EXPORT_PRESIGN_EXPIRY_S),
        )
    except Exception as exc:
        logger.warning("Presign failed for %s: %s", key, exc)
        raise HTTPException(status_code=503, detail=_HTTP_503_DETAIL) from exc
    return RedirectResponse(url, status_code=302)


def _methodology_marts() -> list[dict[str, Any]]:
    """Mart documentation from the dbt schema file (empty when absent)."""
    if not _MARTS_YML.exists():
        return []
    data = yaml.safe_load(_MARTS_YML.read_text(encoding="utf-8")) or {}
    return [
        {
            "name": model.get("name"),
            "description": model.get("description"),
            "columns": [
                {"name": col.get("name"), "description": col.get("description")}
                for col in model.get("columns", [])
            ],
            "public": model.get("name") in PUBLIC_MARTS,
        }
        for model in data.get("models", [])
    ]


def _methodology_pipelines() -> list[dict[str, Any]]:
    """Ingestion pipeline documentation from the declarative YAML specs."""
    if not _PIPELINES_DIR.is_dir():
        return []
    pipelines = []
    for path in sorted(_PIPELINES_DIR.glob("*.yaml")):
        spec = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        pipelines.append(
            {
                "name": spec.get("name"),
                "schedule": spec.get("schedule"),
                "window": spec.get("window"),
                "formula": spec.get("formula"),
                "sources": [s.get("name") for s in spec.get("sources", [])],
                "destinations": spec.get("destinations", []),
            }
        )
    return pipelines


@router.get("/methodology")
async def public_methodology() -> dict[str, Any]:
    """Methodology document generated from the dbt schema and pipeline specs.

    Self-documenting publication: what each mart means, which pipelines
    feed the lake, and the LGPD classification that gates the public
    export (fail-closed allowlist).
    """
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "project": "Capiba — Cruzamento e Análise de Padrões e Indícios em Bases Abertas",
        "export": {
            "bucket": PUBLIC_EXPORT_BUCKET,
            "versioning": "marts/<mart>/dt=<YYYY-MM-DD>/",
            "formats": ["csv", "parquet"],
        },
        "lgpd_classification": {
            "exported": PUBLIC_MARTS,
            "excluded": EXCLUDED_MARTS,
        },
        "marts": _methodology_marts(),
        "pipelines": _methodology_pipelines(),
    }
