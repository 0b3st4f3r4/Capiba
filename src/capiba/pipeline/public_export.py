"""Public batch export of the gold marts.

Responsibility: export the LGPD-cleared gold marts to the public MinIO
bucket (``PUBLIC_EXPORT_BUCKET``, default ``capiba-public``) in CSV and
Parquet, versioned by the run date::

    marts/<mart>/dt=<YYYY-MM-DD>/<mart>.csv
    marts/<mart>/dt=<YYYY-MM-DD>/<mart>.parquet
    marts/<mart>/dt=<YYYY-MM-DD>/manifest.json

The mart set is **fail-closed**: only names in ``PUBLIC_MARTS`` are
exported, and every mart of the dbt project must be classified either
there or in ``EXCLUDED_MARTS`` (with rationale) — a new unclassified mart
fails the guard test (``tests/test_public_export.py``) and is never
exported by accident. LGPD classification rationale lives next to each
entry and is republished by the ``/v1/public/methodology`` endpoint.

Reads go through the Trino gateway (``capiba.pipeline.trino``), writes
through the lake MinIO client. The public-read bucket policy is a deploy
decision (charts/values), out of scope here — see AGENTS.md.

Dependencies: capiba.pipeline.trino, capiba.pipeline.lake, pyarrow
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
from datetime import UTC, date, datetime
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq

from capiba.config import PUBLIC_EXPORT_BUCKET
from capiba.pipeline import lake, trino

logger = logging.getLogger(__name__)

# Gold marts cleared for public export (LGPD classification).
# Rationale per entry is republished in the methodology document.
PUBLIC_MARTS: dict[str, str] = {
    "contracts_by_agency": "agregado por órgão comprador; sem dado pessoal",
    "contracts_daily": "agregado diário de volumes; sem dado pessoal",
    "supplier_stats": (
        "fornecedores PJ/PF agregados; identificador CNPJ/CPF é dado já "
        "público nas fontes (PNCP/Transparência) e necessário ao reuso"
    ),
    "amendments_by_agency": "agregado de aditivos por órgão; sem dado pessoal",
    "amendments_by_supplier": (
        "agregado de aditivos por fornecedor; identificador público na origem"
    ),
    "contract_amendments": (
        "flags de aditivo por contrato público; sem identificador pessoal "
        "direto (contract_id apenas)"
    ),
    "red_flags_by_agency": "agregado de red flags por órgão; sem dado pessoal",
    "red_flags_by_supplier": (
        "agregado de red flags por fornecedor; identificador público na origem"
    ),
    "political_connections": (
        "mart editorial já mascarado na origem (CPF padrão CEAF, CNPJ "
        "público mantido, chave signal_id sha256) — ver PR-D-08, fatia 3"
    ),
}

# Gold marts excluded from the public export, with rationale (fail-closed:
# an unlisted mart is neither exported nor explained — it fails the guard).
EXCLUDED_MARTS: dict[str, str] = {
    "pod_usage_hourly": "telemetria interna da plataforma, sem interesse público",
    "platform_cost_daily": "telemetria interna da plataforma, sem interesse público",
    "data_quality_daily": "métrica operacional interna da qualidade de dados",
    "contract_red_flags": (
        "supplier_id por contrato pode ser CPF completo (pessoa física) sem "
        "mascaramento — excluído até existir mart derivado com LGPD aplicada"
    ),
}

_EXPORT_FORMATS = ("csv", "parquet")

_MART_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def classify_marts(available: list[str]) -> tuple[list[str], list[str]]:
    """Splits the available gold marts into exportable and blocked.

    Args:
        available: Mart names (e.g. the dbt models of ``dbt/models/marts/``).

    Returns:
        ``(exportable, blocked)``: exportable keeps ``PUBLIC_MARTS`` order;
        blocked holds the names without any classification.
    """
    exportable = [name for name in PUBLIC_MARTS if name in available]
    blocked = [
        name
        for name in available
        if name not in PUBLIC_MARTS and name not in EXCLUDED_MARTS
    ]
    return exportable, blocked


def rows_to_csv(rows: list[dict[str, Any]]) -> bytes:
    """Serializes rows (list of dicts) to UTF-8 CSV with a header."""
    if not rows:
        return b""
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode("utf-8")


def rows_to_parquet(rows: list[dict[str, Any]]) -> bytes:
    """Serializes rows to a Parquet payload (string-typed columns).

    The Trino client returns loosely typed values; coercing every column to
    string keeps the export schema-stable across runs (the CSV carries the
    same text), at the cost of typed numerics — accepted for a publishing
    export whose source of truth is the gold table.
    """
    buffer = io.BytesIO()
    if not rows:
        return b""
    table = pa.table(
        {key: [str(row.get(key)) for row in rows] for key in rows[0]}
    )
    pq.write_table(table, buffer)
    return buffer.getvalue()


def export_object_key(mart: str, run_date: date, filename: str) -> str:
    """Object key of an export artifact: marts/<mart>/dt=<date>/<filename>."""
    if not _MART_RE.match(mart):
        raise ValueError(f"invalid mart name: {mart!r}")
    return f"marts/{mart}/dt={run_date.isoformat()}/{filename}"


def export_mart(
    mart: str,
    run_date: date,
    *,
    run_query: Any = None,
    client: Any = None,
) -> dict[str, Any]:
    """Exports one allowlisted mart to the public bucket (CSV + Parquet).

    Args:
        mart: Mart name (must be in ``PUBLIC_MARTS``).
        run_date: Run date of the export (the ``dt=`` partition).
        run_query: SQL runner (defaults to ``trino.run_query``).
        client: MinIO client (defaults to the lake client).

    Returns:
        Export summary (mart, rows, object keys and sha256 per format).

    Raises:
        ValueError: If the mart is not in the public allowlist.
    """
    if mart not in PUBLIC_MARTS:
        raise ValueError(f"mart {mart!r} is not in the public export allowlist")
    if not _MART_RE.match(mart):
        raise ValueError(f"invalid mart name: {mart!r}")
    if run_query is None:
        run_query = trino.run_query
    if client is None:
        client = lake.get_client()

    # Identifiers cannot be parameterized; the allowlist + regex above make
    # the interpolation safe.
    rows = run_query(f"SELECT * FROM gold.capiba.{mart}")  # nosec: B608
    exported_at = datetime.now(UTC).isoformat()
    files: dict[str, dict[str, Any]] = {}
    for fmt in _EXPORT_FORMATS:
        data = rows_to_csv(rows) if fmt == "csv" else rows_to_parquet(rows)
        key = export_object_key(mart, run_date, f"{mart}.{fmt}")
        client.put_object(
            PUBLIC_EXPORT_BUCKET,
            key,
            io.BytesIO(data),
            len(data),
            content_type="text/csv" if fmt == "csv" else "application/octet-stream",
        )
        files[fmt] = {"key": key, "bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        logger.info("Public export written: %s/%s (%d rows)", PUBLIC_EXPORT_BUCKET, key, len(rows))

    manifest = {
        "mart": mart,
        "run_date": run_date.isoformat(),
        "exported_at": exported_at,
        "rows": len(rows),
        "columns": list(rows[0]) if rows else [],
        "files": files,
        "lgpd_classification": PUBLIC_MARTS[mart],
    }
    manifest_key = export_object_key(mart, run_date, "manifest.json")
    payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")
    client.put_object(
        PUBLIC_EXPORT_BUCKET,
        manifest_key,
        io.BytesIO(payload),
        len(payload),
        content_type="application/json",
    )
    return {"mart": mart, "rows": len(rows), "files": files, "manifest": manifest_key}


def export_public_marts(
    run_date: date | None = None,
    *,
    run_query: Any = None,
    client: Any = None,
) -> dict[str, Any]:
    """Exports every allowlisted gold mart to the public bucket.

    Args:
        run_date: Run date of the export (defaults to today, UTC).
        run_query: SQL runner (tests inject a fake).
        client: MinIO client (tests inject a fake).

    Returns:
        Summary with the per-mart export results.
    """
    day = run_date or datetime.now(UTC).date()
    exports = [
        export_mart(mart, day, run_query=run_query, client=client)
        for mart in PUBLIC_MARTS
    ]
    return {
        "bucket": PUBLIC_EXPORT_BUCKET,
        "run_date": day.isoformat(),
        "marts": len(exports),
        "exports": exports,
    }


def task_export_public_marts(**context: Any) -> dict[str, Any]:
    """Task: export the public gold marts after the dbt rebuild.

    Last step of the ``gold_detection`` DAG; reads the marts just rebuilt
    by ``dbt_run`` through Trino and publishes CSV/Parquet to the public
    bucket, versioned by the run date.

    Args:
        context: Airflow context.

    Returns:
        Export summary (bucket, run date, per-mart results).
    """
    run_date = lake._partition_day(_lake_ds(context))
    logger.info("Exporting public gold marts (dt=%s)", run_date)
    return export_public_marts(run_date)


def _lake_ds(context: dict[str, Any]) -> date | None:
    """Resolves the Airflow logical date (``ds``) of the run."""
    ds = context.get("ds")
    if not ds:
        return None
    return date.fromisoformat(str(ds)[:10])
