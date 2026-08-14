"""Minimal Trino HTTP client.

Responsibility: execute SQL statements against the Trino gateway (Iceberg
lake maintenance, ad-hoc queries from tasks) through the ``/v1/statement``
API, following ``nextUri`` pages until the query finishes. No extra
dependency: plain ``requests`` over HTTP, identified by ``X-Trino-User``.

Auth note: Trino 483 refuses passwords over insecure HTTP — insecure
requests authenticate via the ``X-Trino-User`` header only. Basic auth
(user/password) is therefore sent only when ``TRINO_URL`` is HTTPS (the
ingress path).
"""

from __future__ import annotations

import logging
import re
from typing import Any

import requests

from capiba.config import TRINO_PASSWORD, TRINO_URL, TRINO_USER

_CATALOG_RE = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")

logger = logging.getLogger(__name__)

_REQUEST_TIMEOUT = 60  # seconds per HTTP page (Trino long-polls each page)


def _auth() -> tuple[str, str] | None:
    if not TRINO_PASSWORD or not TRINO_URL.startswith("https://"):
        return None
    return (TRINO_USER, TRINO_PASSWORD)


def _headers() -> dict[str, str]:
    return {"X-Trino-User": TRINO_USER}


def _raise_on_error(payload: dict[str, Any]) -> None:
    """Raises when a Trino response page carries a query error."""
    error = payload.get("error")
    if error:
        raise RuntimeError(
            f"Trino query failed: {error.get('errorName')}: {error.get('message')}"
        )


def run_query(sql: str) -> list[dict[str, Any]]:
    """Runs a SQL statement and returns the result rows as dicts.

    Args:
        sql: SQL statement to execute.

    Returns:
        Result rows (empty for statements without a result set).
    """
    logger.info("Trino: %s", sql)
    resp = requests.post(
        f"{TRINO_URL}/v1/statement",
        data=sql,
        headers=_headers(),
        auth=_auth(),
        timeout=_REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    payload = resp.json()

    columns: list[str] = []
    rows: list[dict[str, Any]] = []
    while True:
        _raise_on_error(payload)
        if "columns" in payload:
            columns = [c["name"] for c in payload["columns"]]
        for row in payload.get("data", []):
            rows.append(dict(zip(columns, row, strict=True)))
        next_uri = payload.get("nextUri")
        if not next_uri:
            break
        page = requests.get(next_uri, headers=_headers(), auth=_auth(), timeout=_REQUEST_TIMEOUT)
        page.raise_for_status()
        payload = page.json()

    return rows


def list_iceberg_tables(catalog: str) -> list[str]:
    """Lists the Iceberg tables of a catalog as ``<schema>.<table>`` names."""
    if not _CATALOG_RE.match(catalog):
        raise ValueError(f"invalid catalog name: {catalog!r}")
    rows = run_query(
        # Catalog name is validated above; SQL identifiers cannot be
        # parameterized, and the remaining clauses are static literals.
        f"SELECT table_schema, table_name FROM {catalog}.information_schema.tables"  # nosec: B608
        # the connector's system schema holds metadata views, which do not
        # support table procedures (optimize/expire_snapshots)
        " WHERE table_schema NOT IN ('information_schema', 'system')"
    )
    return [f"{r['table_schema']}.{r['table_name']}" for r in rows]
