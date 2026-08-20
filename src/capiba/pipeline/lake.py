"""Medallion lake writer (bronze/silver/gold).

Responsibility: write ingestion pipeline outputs to the medallion
MinIO buckets (``capiba-bronze``, ``capiba-silver``, ``capiba-gold``).

Two storage shapes coexist by design:

- **Raw audit copies**: gzip-compressed JSON objects partitioned by
  ingestion date (``dt=YYYY-MM-DD``), preserving the exact source payload.
- **Iceberg tables** (Parquet files): typed, queryable tables managed by
  the Iceberg REST catalog (Lakekeeper in the cluster; a SQLite catalog
  with a local warehouse is used when ``ICEBERG_CATALOG_URI`` starts with
  ``sqlite``, for offline runs and tests).

Buckets and warehouses are provisioned by ``scripts/init_buckets.py``;
functions here may raise and callers should treat writes as best-effort.
"""

from __future__ import annotations

import contextlib
import gzip
import io
import json
import logging
import os
import re
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any, cast

import pyarrow as pa
from minio import Minio
from pyiceberg.catalog import Catalog, load_catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.io.pyarrow import schema_to_pyarrow
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.transforms import IdentityTransform
from pyiceberg.types import (
    BooleanType,
    DateType,
    DecimalType,
    DoubleType,
    LongType,
    NestedField,
    StringType,
    StructType,
    TimestamptzType,
)

from capiba.config import (
    ICEBERG_CATALOG_URI,
    ICEBERG_LOCAL_WAREHOUSE,
    ICEBERG_OAUTH2_CLIENT_ID,
    ICEBERG_OAUTH2_CLIENT_SECRET,
    ICEBERG_OAUTH2_SERVER_URI,
    ICEBERG_S3_REGION,
    ICEBERG_WAREHOUSE_BRONZE,
    ICEBERG_WAREHOUSE_GOLD,
    ICEBERG_WAREHOUSE_SILVER,
    LAKE_BUCKET_BRONZE,
    LAKE_BUCKET_GOLD,
    MINIO_ACCESS_KEY,
    MINIO_ENDPOINT,
    MINIO_SECRET_KEY,
    MINIO_SECURE,
)
from capiba.ingestion.cnpj import Company, Establishment, Partner
from capiba.ingestion.normalizer import Contract
from capiba.ingestion.sanctions import Sanction
from capiba.pipeline import trino

if TYPE_CHECKING:
    from pydantic import BaseModel

    from capiba.pipeline.runner import PipelineReport

logger = logging.getLogger(__name__)

# Namespace shared by all Capiba tables inside each Iceberg warehouse.
ICEBERG_NAMESPACE = "capiba"

# Trino catalog bound to the silver warehouse (``silver.properties`` in
# ``charts/capiba/templates/trino/configmap.yaml``) — used for the
# delete-half of the silver contracts upsert.
SILVER_TRINO_CATALOG = "silver"

# Ids per DELETE statement of the silver contracts upsert (keeps the
# generated SQL comfortably below Trino's query length limits).
UPSERT_DELETE_CHUNK_SIZE = 500

# Iceberg schema of the silver ``contracts`` table (flat structs for the
# buyer/supplier entities, partitioned by the ingestion date ``dt``).
CONTRACTS_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=True),
    NestedField(2, "process_number", StringType(), required=False),
    NestedField(3, "subject", StringType(), required=False),
    NestedField(4, "amount", DecimalType(38, 10), required=False),
    NestedField(5, "signature_date", DateType(), required=False),
    NestedField(6, "validity_start", DateType(), required=False),
    NestedField(7, "validity_end", DateType(), required=False),
    NestedField(
        8,
        "buyer",
        StructType(
            NestedField(9, "siafi_code", StringType(), required=False),
            NestedField(10, "name", StringType(), required=False),
            NestedField(11, "government_level", StringType(), required=False),
            NestedField(12, "uf", StringType(), required=False),
            NestedField(13, "city", StringType(), required=False),
        ),
        required=False,
    ),
    NestedField(
        14,
        "supplier",
        StructType(
            NestedField(15, "cnpj", StringType(), required=False),
            NestedField(16, "cpf", StringType(), required=False),
            NestedField(17, "legal_name", StringType(), required=False),
            NestedField(18, "trade_name", StringType(), required=False),
            NestedField(19, "primary_cnae", StringType(), required=False),
            NestedField(20, "state", StringType(), required=False),
            NestedField(21, "city", StringType(), required=False),
        ),
        required=False,
    ),
    NestedField(22, "modality", StringType(), required=False),
    NestedField(23, "status", StringType(), required=False),
    NestedField(24, "dt", DateType(), required=False),
    NestedField(25, "ingested_at", TimestamptzType(), required=False),
)

CONTRACTS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=24, field_id=1000, transform=IdentityTransform(), name="dt"
    )
)

# Iceberg schemas of the silver CNPJ entity tables (Federal Revenue dump),
# partitioned by the ingestion date ``dt`` like the contracts table.
COMPANIES_SCHEMA = Schema(
    NestedField(1, "cnpj_basico", StringType(), required=True),
    NestedField(2, "razao_social", StringType(), required=False),
    NestedField(3, "natureza_juridica", StringType(), required=False),
    NestedField(4, "qualificacao_responsavel", StringType(), required=False),
    NestedField(5, "capital_social", DecimalType(38, 2), required=False),
    NestedField(6, "porte_empresa", StringType(), required=False),
    NestedField(7, "ente_federativo", StringType(), required=False),
    NestedField(8, "dt", DateType(), required=False),
    NestedField(9, "ingested_at", TimestamptzType(), required=False),
)

COMPANIES_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=8, field_id=1000, transform=IdentityTransform(), name="dt")
)

ESTABLISHMENTS_SCHEMA = Schema(
    NestedField(1, "cnpj", StringType(), required=True),
    NestedField(2, "cnpj_basico", StringType(), required=False),
    NestedField(3, "is_matriz", BooleanType(), required=False),
    NestedField(4, "nome_fantasia", StringType(), required=False),
    NestedField(5, "situacao_cadastral", StringType(), required=False),
    NestedField(6, "data_situacao_cadastral", DateType(), required=False),
    NestedField(7, "data_inicio_atividade", DateType(), required=False),
    NestedField(8, "cnae_principal", StringType(), required=False),
    NestedField(9, "uf", StringType(), required=False),
    NestedField(10, "municipio", StringType(), required=False),
    NestedField(11, "cep", StringType(), required=False),
    NestedField(12, "email", StringType(), required=False),
    NestedField(13, "dt", DateType(), required=False),
    NestedField(14, "ingested_at", TimestamptzType(), required=False),
)

ESTABLISHMENTS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=13, field_id=1000, transform=IdentityTransform(), name="dt"
    )
)

PARTNERS_SCHEMA = Schema(
    NestedField(1, "partner_id", StringType(), required=True),
    NestedField(2, "cnpj_basico", StringType(), required=False),
    NestedField(3, "identificador", StringType(), required=False),
    NestedField(4, "nome", StringType(), required=False),
    NestedField(5, "qualificacao", StringType(), required=False),
    NestedField(6, "data_entrada", DateType(), required=False),
    NestedField(7, "faixa_etaria", StringType(), required=False),
    NestedField(8, "dt", DateType(), required=False),
    NestedField(9, "ingested_at", TimestamptzType(), required=False),
    NestedField(10, "cnpj_cpf_socio", StringType(), required=False),
    NestedField(11, "pais", StringType(), required=False),
    NestedField(12, "representante_legal", StringType(), required=False),
    NestedField(13, "nome_representante", StringType(), required=False),
    NestedField(14, "qualificacao_representante_legal", StringType(), required=False),
)

PARTNERS_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=8, field_id=1000, transform=IdentityTransform(), name="dt")
)

# Iceberg schema of the silver ``sanctions`` table (CEIS/CNEP lists of the
# Portal da Transparência), partitioned by the ingestion date ``dt``.
SANCTIONS_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=True),
    NestedField(2, "list_name", StringType(), required=True),
    NestedField(3, "cnpj", StringType(), required=False),
    NestedField(4, "cpf", StringType(), required=False),
    NestedField(5, "sanctioned_name", StringType(), required=False),
    NestedField(6, "uf", StringType(), required=False),
    NestedField(7, "sanctioning_body", StringType(), required=False),
    NestedField(8, "sanction_type", StringType(), required=False),
    NestedField(9, "legal_basis", StringType(), required=False),
    NestedField(10, "process_number", StringType(), required=False),
    NestedField(11, "start_date", DateType(), required=False),
    NestedField(12, "end_date", DateType(), required=False),
    NestedField(13, "publication_date", DateType(), required=False),
    NestedField(14, "fine_amount", DecimalType(38, 2), required=False),
    NestedField(15, "dt", DateType(), required=False),
    NestedField(16, "ingested_at", TimestamptzType(), required=False),
)

SANCTIONS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=15, field_id=1000, transform=IdentityTransform(), name="dt"
    )
)

# Silver entity tables: name -> (schema, partition spec, pydantic model).
ENTITY_TABLES: dict[str, tuple[Schema, PartitionSpec, type[BaseModel]]] = {
    "companies": (COMPANIES_SCHEMA, COMPANIES_PARTITION_SPEC, Company),
    "establishments": (ESTABLISHMENTS_SCHEMA, ESTABLISHMENTS_PARTITION_SPEC, Establishment),
    "partners": (PARTNERS_SCHEMA, PARTNERS_PARTITION_SPEC, Partner),
    "sanctions": (SANCTIONS_SCHEMA, SANCTIONS_PARTITION_SPEC, Sanction),
}

# Iceberg schema of the bronze ``raw_<source>`` tables: the full payload kept
# as a JSON string, one row per crawl run.
RAW_SCHEMA = Schema(
    NestedField(1, "dt", DateType(), required=False),
    NestedField(2, "ingested_at", TimestamptzType(), required=False),
    NestedField(3, "payload_json", StringType(), required=False),
)

RAW_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="dt")
)

# Iceberg schema of the gold ``fraud_signals`` table: one row per entity and
# signal computed by the detect task over the silver contracts.
FRAUD_SIGNALS_SCHEMA = Schema(
    NestedField(1, "dt", DateType(), required=False),
    NestedField(2, "computed_at", TimestamptzType(), required=False),
    NestedField(3, "entity_type", StringType(), required=False),
    NestedField(4, "entity_id", StringType(), required=False),
    NestedField(5, "signal_type", StringType(), required=False),
    NestedField(6, "score", DoubleType(), required=False),
    NestedField(7, "details", StringType(), required=False),
)

FRAUD_SIGNALS_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="dt")
)

# Iceberg schema of the gold ``platform_metrics`` table: one row per step of
# each declarative pipeline run (see ``capiba.pipeline.runner.PipelineReport``)
# — the observability datasource of the ingestion dashboards. ``dt`` is kept
# as a string (YYYY-MM-DD) to simplify the Trino/Grafana queries.
PLATFORM_METRICS_SCHEMA = Schema(
    NestedField(1, "dt", StringType(), required=False),
    NestedField(2, "run_id", StringType(), required=False),
    NestedField(3, "pipeline", StringType(), required=False),
    NestedField(4, "step", StringType(), required=False),
    NestedField(5, "duration_s", DoubleType(), required=False),
    NestedField(6, "rows_in", LongType(), required=False),
    NestedField(7, "rows_out", LongType(), required=False),
    NestedField(8, "validation_errors", LongType(), required=False),
)

PLATFORM_METRICS_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=IdentityTransform(), name="dt")
)

# Note: pyiceberg reassigns field IDs when a table is created
# (``assign_fresh_schema_ids``), so the PyArrow schema used in ``append``
# must be derived from the loaded table — see ``_arrow_schema``.

_client: Minio | None = None
_catalogs: dict[str, Catalog] = {}


def get_client() -> Minio:
    """Returns the MinIO client for the lake, creating it lazily."""
    global _client
    if _client is None:
        _client = Minio(
            MINIO_ENDPOINT,
            access_key=MINIO_ACCESS_KEY,
            secret_key=MINIO_SECRET_KEY,
            secure=MINIO_SECURE,
        )
    return _client


def get_catalog(warehouse: str) -> Catalog:
    """Returns the Iceberg catalog bound to a warehouse, creating it lazily.

    A ``sqlite`` catalog URI selects a local SQL catalog with a filesystem
    warehouse (offline runs/tests); anything else targets the Lakekeeper
    REST catalog with explicit MinIO S3 credentials.
    """
    if warehouse in _catalogs:
        return _catalogs[warehouse]

    if ICEBERG_CATALOG_URI.startswith("sqlite"):
        if not ICEBERG_LOCAL_WAREHOUSE:
            raise ValueError(
                "ICEBERG_LOCAL_WAREHOUSE must be set when using a SQLite catalog"
            )
        warehouse_dir = os.path.join(ICEBERG_LOCAL_WAREHOUSE, warehouse)
        os.makedirs(warehouse_dir, exist_ok=True)
        catalog = load_catalog(
            "capiba",
            **{
                "type": "sql",
                "uri": ICEBERG_CATALOG_URI,
                "warehouse": f"file://{warehouse_dir}",
            },
        )
    else:
        scheme = "https" if MINIO_SECURE else "http"
        config: dict[str, Any] = {
            "type": "rest",
            "uri": ICEBERG_CATALOG_URI,
            "warehouse": warehouse,
            "s3.endpoint": f"{scheme}://{MINIO_ENDPOINT}",
            "s3.access-key-id": MINIO_ACCESS_KEY,
            "s3.secret-access-key": MINIO_SECRET_KEY,
            "s3.region": ICEBERG_S3_REGION,
            "s3.path-style-access": "true",
        }
        # OAuth2 (client_credentials) when the catalog requires auth — the
        # Keycloak "capiba-services" client in the cluster.
        if ICEBERG_OAUTH2_CLIENT_ID and ICEBERG_OAUTH2_CLIENT_SECRET:
            config["credential"] = (
                f"{ICEBERG_OAUTH2_CLIENT_ID}:{ICEBERG_OAUTH2_CLIENT_SECRET}"
            )
            config["oauth2-server-uri"] = ICEBERG_OAUTH2_SERVER_URI
        catalog = load_catalog("capiba", **config)
    _catalogs[warehouse] = catalog
    return catalog


def _ensure_table(
    warehouse: str, table_name: str, schema: Schema, spec: PartitionSpec
) -> Table:
    """Creates the namespace/table if needed and returns the table handle."""
    catalog = get_catalog(warehouse)
    with contextlib.suppress(NamespaceAlreadyExistsError):
        catalog.create_namespace(ICEBERG_NAMESPACE)
    return catalog.create_table_if_not_exists(
        f"{ICEBERG_NAMESPACE}.{table_name}", schema=schema, partition_spec=spec
    )


def _arrow_schema(table: Table) -> pa.Schema:
    """Returns the PyArrow schema of a loaded table (with Iceberg field IDs).

    pyiceberg reassigns field IDs when the table is created, so schemas for
    ``Table.append`` must come from the table itself, not from the declared
    creation schema.
    """
    return schema_to_pyarrow(table.schema())


def _put_object(bucket: str, key: str, data: bytes) -> None:
    """Uploads a gzip-compressed JSON payload to the lake."""
    get_client().put_object(
        bucket,
        key,
        io.BytesIO(data),
        len(data),
        content_type="application/json",
        metadata={"x-amz-meta-content-encoding": "gzip"},
    )
    logger.info("Lake object written: %s/%s (%d bytes)", bucket, key, len(data))


def _partition_day(run_date: date | None) -> date:
    """Returns the partition date for a run date (defaults to today, UTC)."""
    return run_date or datetime.now(UTC).date()


def _timestamp() -> str:
    """Returns a compact UTC timestamp for object names."""
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%S")


def write_bronze(source: str, payload: Any, run_date: date | None = None) -> str:
    """Writes a raw source payload to the bronze layer (audit copy).

    Args:
        source: Source name (e.g. ``pncp``, ``transparency``).
        payload: Raw payload (any JSON-serializable object).
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The object key written.
    """
    suffix = f"{_timestamp()}-{uuid.uuid4().hex[:8]}"
    key = f"{source}/dt={_partition_day(run_date).isoformat()}/{suffix}.json.gz"
    data = gzip.compress(json.dumps(payload, default=str).encode())
    _put_object(LAKE_BUCKET_BRONZE, key, data)
    return key


def write_bronze_file(
    source: str, filename: str, data: bytes, run_date: date | None = None
) -> str:
    """Uploads a raw source file (zip/csv dump) to the bronze layer.

    Large binary payloads (e.g. Federal Revenue dumps) are kept as objects
    under ``<source>/files/dt=YYYY-MM-DD/`` instead of the JSON audit copies.

    Args:
        source: Source name (e.g. ``federal_revenue``).
        filename: File name kept in the object key.
        data: File contents.
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The object key written.
    """
    key = f"{source}/files/dt={_partition_day(run_date).isoformat()}/{filename}"
    get_client().put_object(LAKE_BUCKET_BRONZE, key, io.BytesIO(data), len(data))
    logger.info(
        "Lake file written: %s/%s (%d bytes)", LAKE_BUCKET_BRONZE, key, len(data)
    )
    return key


def list_bronze_files(source: str, run_date: date | None = None) -> list[str]:
    """Lists the object keys of a source's raw file uploads for a run date.

    Counterpart of ``write_bronze_file``: keys live under
    ``<source>/files/dt=YYYY-MM-DD/`` in the bronze bucket.

    Args:
        source: Source name (e.g. ``federal_revenue``).
        run_date: Partition date; defaults to today (UTC).

    Returns:
        Object keys (possibly empty).
    """
    prefix = f"{source}/files/dt={_partition_day(run_date).isoformat()}/"
    keys = [
        obj.object_name
        for obj in get_client().list_objects(
            LAKE_BUCKET_BRONZE, prefix=prefix, recursive=True
        )
        if obj.object_name is not None
    ]
    logger.info("Bronze files listed: %s (%d keys)", prefix, len(keys))
    return keys


def read_bronze_file(key: str) -> bytes:
    """Reads back a raw file uploaded with ``write_bronze_file``.

    Args:
        key: Object key in the bronze bucket.

    Returns:
        The raw file contents.
    """
    response = get_client().get_object(LAKE_BUCKET_BRONZE, key)
    try:
        data = response.read()
    finally:
        response.close()
        response.release_conn()
    logger.info("Lake file read: %s/%s (%d bytes)", LAKE_BUCKET_BRONZE, key, len(data))
    return data


def write_bronze_page(
    source: str,
    page: int,
    records: list[dict[str, Any]],
    run_date: date | None = None,
) -> str:
    """Writes one crawled page to the bronze layer (incremental checkpoint).

    Paginated snapshot sources (e.g. the CEIS/CNEP sanction lists) persist
    each page as it lands under ``<source>/pages/dt=YYYY-MM-DD/`` so a
    retried task can resume from the next unpersisted page instead of
    restarting the whole walk (see ``task_crawl_entities``). The key is
    deterministic per (source, run date, page): rewrites overwrite.

    Args:
        source: Source name (e.g. ``ceis``).
        page: Page number (1-based).
        records: Raw records of the page.
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The object key written.
    """
    key = f"{source}/pages/dt={_partition_day(run_date).isoformat()}/page-{page:05d}.json.gz"
    data = gzip.compress(json.dumps(records, default=str).encode())
    _put_object(LAKE_BUCKET_BRONZE, key, data)
    return key


def list_bronze_pages(source: str, run_date: date | None = None) -> dict[int, str]:
    """Lists the persisted page checkpoints of a source for a run date.

    Counterpart of ``write_bronze_page``: maps page number to object key
    under ``<source>/pages/dt=YYYY-MM-DD/``.

    Args:
        source: Source name (e.g. ``ceis``).
        run_date: Partition date; defaults to today (UTC).

    Returns:
        Mapping of page number to object key (possibly empty).
    """
    prefix = f"{source}/pages/dt={_partition_day(run_date).isoformat()}/"
    pages: dict[int, str] = {}
    for obj in get_client().list_objects(LAKE_BUCKET_BRONZE, prefix=prefix, recursive=True):
        if obj.object_name is None:
            continue
        match = re.search(r"page-(\d+)\.json\.gz$", obj.object_name)
        if match:
            pages[int(match.group(1))] = obj.object_name
    logger.info("Bronze pages listed: %s (%d pages)", prefix, len(pages))
    return pages


def read_bronze_page(key: str) -> list[dict[str, Any]]:
    """Reads back a page checkpoint written with ``write_bronze_page``.

    Args:
        key: Object key in the bronze bucket.

    Returns:
        The raw records of the page.
    """
    return cast(list[dict[str, Any]], json.loads(gzip.decompress(read_bronze_file(key))))


def write_bronze_table(source: str, payload: Any, run_date: date | None = None) -> str:
    """Appends a raw source payload to the bronze Iceberg table.

    One row per run in the ``raw_<source>`` table, with the full payload
    kept as a JSON string.

    Args:
        source: Source name (e.g. ``pncp``, ``transparency``).
        payload: Raw payload (any JSON-serializable object).
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The Iceberg table identifier written.
    """
    table_name = f"raw_{source}"
    table = _ensure_table(
        ICEBERG_WAREHOUSE_BRONZE, table_name, RAW_SCHEMA, RAW_PARTITION_SPEC
    )
    row = {
        "dt": _partition_day(run_date),
        "ingested_at": datetime.now(UTC),
        "payload_json": json.dumps(payload, default=str),
    }
    table.append(pa.Table.from_pylist([row], schema=_arrow_schema(table)))
    logger.info("Bronze Iceberg table appended: %s", table_name)
    return f"{ICEBERG_NAMESPACE}.{table_name}"


def _delete_silver_contracts(ids: list[str]) -> None:
    """Deletes silver contract rows by id through Trino (upsert delete-half).

    Runs one ``DELETE`` per ``UPSERT_DELETE_CHUNK_SIZE`` ids, with single
    quotes in the ids escaped by doubling. Only valid against the cluster
    (Trino over the Lakekeeper catalog); the offline SQLite catalog has no
    Trino and callers must skip this step.

    Args:
        ids: Contract ids whose existing rows must be removed.
    """
    for offset in range(0, len(ids), UPSERT_DELETE_CHUNK_SIZE):
        chunk = ids[offset : offset + UPSERT_DELETE_CHUNK_SIZE]
        escaped = ", ".join(f"'{i.replace("'", "''")}'" for i in chunk)
        # Ids come from validated Contract records and are quote-escaped
        # above; SQL literals cannot be parameterized over the HTTP API.
        trino.run_query(
            f"DELETE FROM {SILVER_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.contracts"  # nosec: B608
            f" WHERE id IN ({escaped})"
        )
    logger.info("Silver contracts upsert: deleted ids %d", len(ids))


def write_silver(records: list[dict[str, Any]], run_date: date | None = None) -> str:
    """Upserts normalized contract records into the silver Iceberg table.

    Records are revalidated against the ``Contract`` schema so the table
    stays typed (dates, decimals and entity structs). The write is an
    **upsert by id**: before appending, the rows with the same ids are
    deleted through Trino (``_delete_silver_contracts``), so re-runs of the
    same window replace the previous rows instead of duplicating them.

    Failure semantics (this is what makes retries safe):

    - If the DELETE fails, the exception propagates and **no append is
      attempted** — the old rows stay, never duplicated.
    - If the append fails after the DELETE, the exception propagates too;
      a re-run restores the rows (idempotent by construction, at the cost
      of a temporary gap until the retry).

    With the offline SQLite catalog (``ICEBERG_CATALOG_URI`` starting with
    ``sqlite``) there is no Trino to DELETE through, so the write degrades
    to a pure append.

    Args:
        records: Serializable normalized contracts.
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The Iceberg table identifier written.
    """
    table = _ensure_table(
        ICEBERG_WAREHOUSE_SILVER,
        "contracts",
        CONTRACTS_SCHEMA,
        CONTRACTS_PARTITION_SPEC,
    )

    partition = _partition_day(run_date)
    ingested_at = datetime.now(UTC)
    rows: list[dict[str, Any]] = []
    for record in records:
        try:
            contract = Contract.model_validate(record).model_dump()
        except Exception as e:
            logger.warning("Skipping invalid contract for the silver table: %s", e)
            continue
        rows.append({**contract, "dt": partition, "ingested_at": ingested_at})

    if rows:
        if not ICEBERG_CATALOG_URI.startswith("sqlite"):
            # Delete-half of the upsert first: a failure here aborts before
            # the append, so rows are never duplicated (see the docstring).
            _delete_silver_contracts(list(dict.fromkeys(row["id"] for row in rows)))
        table.append(pa.Table.from_pylist(rows, schema=_arrow_schema(table)))
    logger.info("Silver Iceberg table appended: contracts (%d rows)", len(rows))
    return f"{ICEBERG_NAMESPACE}.contracts"


def read_silver_contracts() -> list[dict[str, Any]]:
    """Reads every row of the silver ``contracts`` Iceberg table.

    Returns:
        Contract rows as dicts (empty when the table does not exist yet).
    """
    catalog = get_catalog(ICEBERG_WAREHOUSE_SILVER)
    try:
        table = catalog.load_table(f"{ICEBERG_NAMESPACE}.contracts")
    except NoSuchTableError:
        logger.info("Silver contracts table not found; nothing to read")
        return []
    rows = table.scan().to_pandas().to_dict("records")
    return cast(list[dict[str, Any]], rows)


def delete_silver_entities_partition(entity: str, run_date: date) -> None:
    """Deletes the entity's silver rows of one partition day through Trino.

    Idempotency half of the dump normalization (``task_normalize_dump``):
    a pod restart mid-dump re-runs the parse from scratch, and without
    this delete each retry would re-append the rows parsed so far (seen
    2026-08-20: repeated OOMKills duplicated the ``dt=2026-08-02`` entity
    partitions). Deleting the partition before parsing makes every retry
    start clean. A failure here propagates — no append is attempted, so
    rows are never duplicated.

    No-op with the offline SQLite catalog (no Trino to DELETE through)
    and when the table does not exist yet (first load of the entity).

    Args:
        entity: Entity name (``companies``/``establishments``/``partners``/
            ``sanctions``); validated against ``ENTITY_TABLES``.
        run_date: Partition day to delete.
    """
    if entity not in ENTITY_TABLES:
        raise ValueError(f"Unknown silver entity '{entity}'")
    if ICEBERG_CATALOG_URI.startswith("sqlite"):
        logger.info("Offline catalog; skipping %s partition delete", entity)
        return
    catalog = get_catalog(ICEBERG_WAREHOUSE_SILVER)
    try:
        catalog.load_table(f"{ICEBERG_NAMESPACE}.{entity}")
    except NoSuchTableError:
        logger.info("Silver %s table not found; nothing to delete", entity)
        return
    partition = _partition_day(run_date)
    # The entity name is whitelisted against ENTITY_TABLES above; SQL
    # literals cannot be parameterized over the HTTP API.
    trino.run_query(
        f"DELETE FROM {SILVER_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.{entity}"  # nosec: B608
        f" WHERE dt = DATE '{partition.isoformat()}'"
    )
    logger.info("Silver %s partition dt=%s deleted", entity, partition)


def write_silver_entities(
    entity: str, rows: list[dict[str, Any]], run_date: date | None = None
) -> str:
    """Appends entity records to the entity's silver Iceberg table.

    Safe to call many times per run (one append per parsed chunk, so the
    dump never materializes in memory). Records are revalidated against
    the entity model, like ``write_silver`` does with ``Contract``.

    Args:
        entity: Entity name (``companies``/``establishments``/``partners``/
            ``sanctions``).
        rows: Serializable entity records (one parsed chunk).
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The Iceberg table identifier written.
    """
    if entity not in ENTITY_TABLES:
        raise ValueError(f"Unknown silver entity '{entity}'")
    schema, spec, model = ENTITY_TABLES[entity]
    table = _ensure_table(ICEBERG_WAREHOUSE_SILVER, entity, schema, spec)

    partition = _partition_day(run_date)
    ingested_at = datetime.now(UTC)
    valid_rows: list[dict[str, Any]] = []
    for row in rows:
        try:
            record = model.model_validate(row).model_dump()
        except Exception as e:
            logger.warning("Skipping invalid %s row for the silver table: %s", entity, e)
            continue
        valid_rows.append({**record, "dt": partition, "ingested_at": ingested_at})

    if valid_rows:
        table.append(pa.Table.from_pylist(valid_rows, schema=_arrow_schema(table)))
    logger.info("Silver Iceberg table appended: %s (%d rows)", entity, len(valid_rows))
    return f"{ICEBERG_NAMESPACE}.{entity}"


def read_silver_entities(entity: str) -> Iterator[list[dict[str, Any]]]:
    """Reads the silver table of an entity in batches.

    Streams Arrow record batches (``to_arrow_batch_reader``) so graph loads
    over the large CNPJ tables do not exhaust memory — ``to_arrow()``
    materialized the whole table first and OOMKilled the Airflow pod on
    the first full ``companies`` load (2026-08-20). A missing table yields
    nothing (logged), like the other silver/gold readers.

    Args:
        entity: Entity name (``companies``/``establishments``/``partners``/
            ``sanctions``).

    Yields:
        Lists of entity rows as dicts (one list per Arrow record batch).
    """
    if entity not in ENTITY_TABLES:
        raise ValueError(f"Unknown silver entity '{entity}'")
    catalog = get_catalog(ICEBERG_WAREHOUSE_SILVER)
    try:
        table = catalog.load_table(f"{ICEBERG_NAMESPACE}.{entity}")
    except NoSuchTableError:
        logger.info("Silver %s table not found; nothing to read", entity)
        return
    for batch in table.scan().to_arrow_batch_reader():
        yield batch.to_pylist()


def read_fraud_signals() -> list[dict[str, Any]]:
    """Reads every row of the gold ``fraud_signals`` Iceberg table.

    Returns:
        Signal rows as dicts (empty when the table does not exist yet).
    """
    catalog = get_catalog(ICEBERG_WAREHOUSE_GOLD)
    try:
        table = catalog.load_table(f"{ICEBERG_NAMESPACE}.fraud_signals")
    except NoSuchTableError:
        logger.info("Gold fraud_signals table not found; nothing to read")
        return []
    rows = table.scan().to_pandas().to_dict("records")
    return cast(list[dict[str, Any]], rows)


def write_fraud_signals(
    signals: list[dict[str, Any]], run_date: date | None = None
) -> str:
    """Appends detected fraud signals to the gold Iceberg table.

    Args:
        signals: Signal rows (entity_type, entity_id, signal_type, score,
            details); ``dt``/``computed_at`` are filled here.
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The Iceberg table identifier written.
    """
    table = _ensure_table(
        ICEBERG_WAREHOUSE_GOLD,
        "fraud_signals",
        FRAUD_SIGNALS_SCHEMA,
        FRAUD_SIGNALS_PARTITION_SPEC,
    )

    partition = _partition_day(run_date)
    computed_at = datetime.now(UTC)
    rows = [
        {**signal, "dt": partition, "computed_at": computed_at} for signal in signals
    ]
    if rows:
        table.append(pa.Table.from_pylist(rows, schema=_arrow_schema(table)))
    logger.info("Gold Iceberg table appended: fraud_signals (%d rows)", len(rows))
    return f"{ICEBERG_NAMESPACE}.fraud_signals"


def write_platform_metrics(report: PipelineReport, run_date: date | None = None) -> str:
    """Appends per-step run metrics to the gold ``platform_metrics`` table.

    One row per pipeline step (duration, rows in/out, validation errors) —
    the datasource of the ingestion observability dashboards. Best-effort
    like the other writers: callers should catch and log failures.

    Args:
        report: The pipeline run report.
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The Iceberg table identifier written.
    """
    table = _ensure_table(
        ICEBERG_WAREHOUSE_GOLD,
        "platform_metrics",
        PLATFORM_METRICS_SCHEMA,
        PLATFORM_METRICS_PARTITION_SPEC,
    )

    dt = _partition_day(run_date).isoformat()
    run_id = f"{report.pipeline}-{report.started_at.strftime('%Y%m%dT%H%M%S')}"
    rows = [
        {
            "dt": dt,
            "run_id": run_id,
            "pipeline": report.pipeline,
            "step": step.name,
            "duration_s": step.duration_seconds,
            "rows_in": step.rows_in,
            "rows_out": step.rows_out,
            "validation_errors": step.errors,
        }
        for step in report.steps
    ]
    if rows:
        table.append(pa.Table.from_pylist(rows, schema=_arrow_schema(table)))
    logger.info("Gold Iceberg table appended: platform_metrics (%d rows)", len(rows))
    return f"{ICEBERG_NAMESPACE}.platform_metrics"


def write_gold(
    report: dict[str, Any], report_name: str, run_date: date | None = None
) -> str:
    """Writes a pipeline report to the gold layer.

    Run reports stay as gzip JSON objects (small, non-tabular); analytical
    gold data lives in Iceberg marts built by the dbt project.

    Args:
        report: Serializable report payload.
        report_name: Report name (e.g. ``daily_ingestion``).
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The object key written.
    """
    key = (
        f"reports/{report_name}/dt={_partition_day(run_date).isoformat()}"
        f"/{_timestamp()}.json.gz"
    )
    data = gzip.compress(json.dumps(report, default=str).encode())
    _put_object(LAKE_BUCKET_GOLD, key, data)
    return key
