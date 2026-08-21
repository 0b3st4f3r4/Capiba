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
from collections.abc import Collection, Iterator
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
from capiba.ingestion.cnpj import Company, Establishment, Partner, RfbMunicipality
from capiba.ingestion.geography import Municipality, municipality_rows
from capiba.ingestion.normalizer import Contract
from capiba.ingestion.sanctions import Sanction
from capiba.ingestion.tse import CampaignDonation, Candidacy
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

# Trino catalog bound to the gold warehouse — used for row counts that must
# not scan the whole table into memory (portal stats).
GOLD_TRINO_CATALOG = "gold"

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

# Iceberg schema of the silver ``sanctions`` table (CEIS/CNEP/CEAF lists of
# the Portal da Transparência), partitioned by the ingestion date ``dt``.
# ``masked_document`` carries the CEAF masked CPF (``***435151**``); the
# CEIS/CNEP lists keep full documents in ``cnpj``/``cpf``.
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
    NestedField(17, "masked_document", StringType(), required=False),
)

SANCTIONS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=15, field_id=1000, transform=IdentityTransform(), name="dt"
    )
)

# Iceberg schema of the silver ``campaign_donations`` table (TSE prestação
# de contas eleitorais — receitas de candidatos), partitioned by the
# ingestion date ``dt``. The donor documents are complete at the source and
# kept here for the deterministic match of the ``political_connection``
# signal; masking for publication is a gold mart concern (PR-D-08, LGPD).
CAMPAIGN_DONATIONS_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=True),
    NestedField(2, "election_year", LongType(), required=False),
    NestedField(3, "donor_document", StringType(), required=False),
    NestedField(4, "donor_name", StringType(), required=False),
    NestedField(5, "donor_origin_document", StringType(), required=False),
    NestedField(6, "donor_origin_name", StringType(), required=False),
    NestedField(7, "donation_date", DateType(), required=False),
    NestedField(8, "amount", DecimalType(38, 2), required=False),
    NestedField(9, "revenue_origin", StringType(), required=False),
    NestedField(10, "candidate_sequential", StringType(), required=False),
    NestedField(11, "candidate_name", StringType(), required=False),
    NestedField(12, "party", StringType(), required=False),
    NestedField(13, "office", StringType(), required=False),
    NestedField(14, "ue_name", StringType(), required=False),
    NestedField(15, "uf", StringType(), required=False),
    NestedField(16, "dt", DateType(), required=False),
    NestedField(17, "ingested_at", TimestamptzType(), required=False),
)

CAMPAIGN_DONATIONS_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=16, field_id=1000, transform=IdentityTransform(), name="dt"
    )
)

# Iceberg schema of the silver ``candidacies`` table (TSE consulta_cand —
# who ran and who was elected), partitioned by the ingestion date ``dt``.
# Feeds the elected-mayor gate of the ``political_connection`` signal
# (PR-D-08 §3) via ``totalization_status``.
CANDIDACIES_SCHEMA = Schema(
    NestedField(1, "id", StringType(), required=True),
    NestedField(2, "election_year", LongType(), required=False),
    NestedField(3, "candidate_sequential", StringType(), required=False),
    NestedField(4, "candidate_name", StringType(), required=False),
    NestedField(5, "party", StringType(), required=False),
    NestedField(6, "office", StringType(), required=False),
    NestedField(7, "ue_code", StringType(), required=False),
    NestedField(8, "ue_name", StringType(), required=False),
    NestedField(9, "uf", StringType(), required=False),
    NestedField(10, "totalization_status", StringType(), required=False),
    NestedField(11, "dt", DateType(), required=False),
    NestedField(12, "ingested_at", TimestamptzType(), required=False),
)

CANDIDACIES_PARTITION_SPEC = PartitionSpec(
    PartitionField(
        source_id=11, field_id=1000, transform=IdentityTransform(), name="dt"
    )
)

# Iceberg schema of the silver ``rfb_municipalities`` table: the TOM code
# -> municipality name reference shipped with the CNPJ dump
# (``Municipios.zip``), the missing link between
# ``establishments.municipio`` (a TOM code) and the geographic reference.
# Partitioned by the ingestion date ``dt``.
RFB_MUNICIPALITIES_SCHEMA = Schema(
    NestedField(1, "tom_code", StringType(), required=True),
    NestedField(2, "name", StringType(), required=False),
    NestedField(3, "dt", DateType(), required=False),
    NestedField(4, "ingested_at", TimestamptzType(), required=False),
)

RFB_MUNICIPALITIES_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=3, field_id=1000, transform=IdentityTransform(), name="dt")
)

# Iceberg schema of the silver ``municipalities`` table: the vendored
# Brazilian municipality reference (kelvins/Municipios-Brasileiros, MIT —
# ``capiba.ingestion.data``) with IBGE code, UF, SIAFI code and lat/long,
# loaded by ``load_municipalities``. Partitioned by the load date ``dt``.
MUNICIPALITIES_SCHEMA = Schema(
    NestedField(1, "ibge_code", StringType(), required=True),
    NestedField(2, "name", StringType(), required=False),
    NestedField(3, "uf", StringType(), required=False),
    NestedField(4, "siafi_code", StringType(), required=False),
    NestedField(5, "latitude", DoubleType(), required=False),
    NestedField(6, "longitude", DoubleType(), required=False),
    NestedField(7, "dt", DateType(), required=False),
    NestedField(8, "ingested_at", TimestamptzType(), required=False),
)

MUNICIPALITIES_PARTITION_SPEC = PartitionSpec(
    PartitionField(source_id=7, field_id=1000, transform=IdentityTransform(), name="dt")
)

# Silver entity tables: name -> (schema, partition spec, pydantic model).
ENTITY_TABLES: dict[str, tuple[Schema, PartitionSpec, type[BaseModel]]] = {
    "companies": (COMPANIES_SCHEMA, COMPANIES_PARTITION_SPEC, Company),
    "establishments": (ESTABLISHMENTS_SCHEMA, ESTABLISHMENTS_PARTITION_SPEC, Establishment),
    "partners": (PARTNERS_SCHEMA, PARTNERS_PARTITION_SPEC, Partner),
    "sanctions": (SANCTIONS_SCHEMA, SANCTIONS_PARTITION_SPEC, Sanction),
    "campaign_donations": (
        CAMPAIGN_DONATIONS_SCHEMA,
        CAMPAIGN_DONATIONS_PARTITION_SPEC,
        CampaignDonation,
    ),
    "candidacies": (CANDIDACIES_SCHEMA, CANDIDACIES_PARTITION_SPEC, Candidacy),
    "rfb_municipalities": (
        RFB_MUNICIPALITIES_SCHEMA,
        RFB_MUNICIPALITIES_PARTITION_SPEC,
        RfbMunicipality,
    ),
    "municipalities": (
        MUNICIPALITIES_SCHEMA,
        MUNICIPALITIES_PARTITION_SPEC,
        Municipality,
    ),
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
    """Creates the namespace/table if needed and returns the table handle.

    Existing tables gain any declared columns they lack (nullable schema
    evolution — e.g. ``sanctions.masked_document`` for the CEAF list), so
    appends with the new fields never fail on a table created by an older
    version of the code.
    """
    catalog = get_catalog(warehouse)
    with contextlib.suppress(NamespaceAlreadyExistsError):
        catalog.create_namespace(ICEBERG_NAMESPACE)
    table = catalog.create_table_if_not_exists(
        f"{ICEBERG_NAMESPACE}.{table_name}", schema=schema, partition_spec=spec
    )
    existing = {field.name for field in table.schema().fields}
    missing = [field for field in schema.fields if field.name not in existing]
    if missing:
        with table.update_schema() as update:
            for field in missing:
                update.add_column(field.name, field.field_type)
    return table


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


def list_all_bronze_files(source: str) -> list[str]:
    """Lists every raw file key of a source, across all run-date partitions.

    Counterpart of ``list_bronze_files`` for readers that need the full
    accumulated corpus (e.g. the ``notice_clone`` producer, whose rolling
    window spans partitions): keys live under ``<source>/files/``.

    Args:
        source: Source name (e.g. ``querido_diario``).

    Returns:
        Object keys (possibly empty), sorted for determinism.
    """
    prefix = f"{source}/files/"
    keys = [
        obj.object_name
        for obj in get_client().list_objects(
            LAKE_BUCKET_BRONZE, prefix=prefix, recursive=True
        )
        if obj.object_name is not None
    ]
    logger.info("Bronze files listed: %s (%d keys)", prefix, len(keys))
    return sorted(keys)


def list_bronze_objects(prefix: str) -> list[str]:
    """Lists every object key of the bronze bucket under an arbitrary prefix.

    Generic counterpart of ``list_bronze_files``/``list_all_bronze_files``
    for frozen anchors that live outside the ``<source>/files/dt=`` layout
    (e.g. ``tse/reference/``).

    Args:
        prefix: Object key prefix in the bronze bucket.

    Returns:
        Object keys (possibly empty), sorted for determinism.
    """
    keys = [
        obj.object_name
        for obj in get_client().list_objects(
            LAKE_BUCKET_BRONZE, prefix=prefix, recursive=True
        )
        if obj.object_name is not None
    ]
    logger.info("Bronze objects listed: %s (%d keys)", prefix, len(keys))
    return sorted(keys)


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
            # The Trino DELETE commits a new snapshot; without a refresh the
            # append commits against the stale one and the catalog rejects it
            # ("Branch or tag `main`'s snapshot has changed" — pinned by
            # tests/test_lake_integration.py).
            table = table.refresh()
        table.append(pa.Table.from_pylist(rows, schema=_arrow_schema(table)))
    logger.info("Silver Iceberg table appended: contracts (%d rows)", len(rows))
    return f"{ICEBERG_NAMESPACE}.contracts"


def silver_table_exists(name: str) -> bool:
    """Checks whether a silver Iceberg table exists in the catalog.

    Args:
        name: Table name inside the silver namespace (e.g. ``contracts``,
            ``campaign_donations``).

    Returns:
        True when the table is registered in the catalog.
    """
    catalog = get_catalog(ICEBERG_WAREHOUSE_SILVER)
    try:
        catalog.load_table(f"{ICEBERG_NAMESPACE}.{name}")
    except NoSuchTableError:
        return False
    return True


def read_silver_contracts() -> list[dict[str, Any]]:
    """Reads every row of the silver ``contracts`` Iceberg table.

    In the cluster the read goes through Trino: the upsert-by-id writes
    positional delete files whose encoding the pinned pyarrow cannot
    decode ("DecodeArrow of DictAccumulator for
    DeltaLengthByteArrayDecoder"), breaking the pyiceberg scan on the
    real silver (2026-08-21). Offline (SQLite catalog) there are no
    Trino-side deletes, so the local scan stands.

    Returns:
        Contract rows as dicts (empty when the table does not exist yet).
        Trino returns ROW fields as dicts and DECIMAL/date values as
        strings; the detection consumers coerce both.
    """
    catalog = get_catalog(ICEBERG_WAREHOUSE_SILVER)
    try:
        table = catalog.load_table(f"{ICEBERG_NAMESPACE}.contracts")
    except NoSuchTableError:
        logger.info("Silver contracts table not found; nothing to read")
        return []
    if not ICEBERG_CATALOG_URI.startswith("sqlite"):
        return [
            _coerce_struct_fields(row)
            for row in trino.run_query(
                f"SELECT * FROM {SILVER_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.contracts"  # nosec: B608
            )
        ]
    rows = table.scan().to_pandas().to_dict("records")
    return cast(list[dict[str, Any]], rows)


def _struct_field_names(field_name: str) -> list[str]:
    """Field order of a struct column of ``CONTRACTS_SCHEMA``."""
    field = next(f for f in CONTRACTS_SCHEMA.fields if f.name == field_name)
    return [nested.name for nested in cast(StructType, field.field_type).fields]


_CONTRACT_STRUCTS = {
    name: _struct_field_names(name) for name in ("buyer", "supplier")
}


def _coerce_struct_fields(row: dict[str, Any]) -> dict[str, Any]:
    """Maps positionally-encoded ROW fields back to dicts.

    Trino's JSON protocol serializes struct values as positional arrays
    (the minimal ``trino.run_query`` client does not apply the type
    signature), so ``buyer``/``supplier`` arrive as lists; the Iceberg
    schema gives the field order for the zip.
    """
    for name, fields in _CONTRACT_STRUCTS.items():
        value = row.get(name)
        if isinstance(value, list):
            row[name] = dict(zip(fields, value, strict=False))
    return row


def count_silver_contracts() -> int:
    """Counts rows of the silver ``contracts`` table without scanning it.

    Goes through Trino (``count(*)``) so the API never materializes the
    whole table in memory just for a number — the full scan OOMKilled the
    API pod on real PNCP volume (2026-08-20). With the offline SQLite
    catalog there is no Trino, so it degrades to a local scan (small data).

    Returns:
        Row count of the silver ``contracts`` table.
    """
    if ICEBERG_CATALOG_URI.startswith("sqlite"):
        return len(read_silver_contracts())
    rows = trino.run_query(
        f"SELECT count(*) AS n FROM {SILVER_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.contracts"  # nosec: B608
    )
    return int(rows[0]["n"]) if rows else 0


def delete_silver_entities_partition(
    entity: str, run_date: date, election_year: int | None = None
) -> None:
    """Deletes the entity's silver rows of one partition day through Trino.

    Idempotency half of the dump normalization (``task_normalize_dump``):
    a pod restart mid-dump re-runs the parse from scratch, and without
    this delete each retry would re-append the rows parsed so far (seen
    2026-08-20: repeated OOMKills duplicated the ``dt=2026-08-02`` entity
    partitions). Deleting the partition before parsing makes every retry
    start clean. A failure here propagates — no append is attempted, so
    rows are never duplicated.

    With ``election_year`` set (TSE multi-year ingestion), the delete is
    scoped to that year, so two election years sharing a partition day
    (or a retry of one year after another wrote to the partition) never
    delete each other's rows.

    No-op with the offline SQLite catalog (no Trino to DELETE through)
    and when the table does not exist yet (first load of the entity).

    Args:
        entity: Entity name (``companies``/``establishments``/``partners``/
            ``sanctions``); validated against ``ENTITY_TABLES``.
        run_date: Partition day to delete.
        election_year: Optional election year scope of the delete; only
            valid for entities whose schema has the ``election_year``
            column (TSE silvers).

    Raises:
        ValueError: If ``election_year`` is given for an entity without
            the column.
    """
    if entity not in ENTITY_TABLES:
        raise ValueError(f"Unknown silver entity '{entity}'")
    if election_year is not None and "election_year" not in {
        field.name for field in ENTITY_TABLES[entity][0].fields
    }:
        raise ValueError(
            f"Silver entity '{entity}' has no election_year column; "
            "the year-scoped delete only applies to the TSE silvers"
        )
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
    # The entity name is whitelisted against ENTITY_TABLES above and the
    # year is coerced to int; SQL literals cannot be parameterized over
    # the HTTP API.
    sql = (
        f"DELETE FROM {SILVER_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.{entity}"  # nosec: B608
        f" WHERE dt = DATE '{partition.isoformat()}'"
    )
    if election_year is not None:
        sql += f" AND election_year = {int(election_year)}"
    trino.run_query(sql)
    logger.info(
        "Silver %s partition dt=%s deleted (election_year=%s)",
        entity,
        partition,
        election_year,
    )


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


_TRINO_IN_BATCH = 5000  # CNPJs per IN clause (Trino literal batch); each
# batch is a full scan of the establishments table (~20s on 72M rows), so
# few large batches beat many small ones.


def read_establishments_for_cnpjs(cnpjs: Collection[str]) -> list[dict[str, Any]]:
    """Reads the silver ``establishments`` rows of the given CNPJs only.

    The full establishments table holds tens of millions of RFB rows;
    materializing it to resolve the supplier CNPJs of the contracts
    OOMKilled the Airflow pod on the first real detect run (2026-08-21,
    exit 137). The selective read goes through Trino (batched ``IN`` over
    digits-only literals); offline (SQLite catalog) it degrades to the
    streaming scan with a filter, bounded on small local data.

    Args:
        cnpjs: Supplier CNPJs (14 digits after normalization; other
            values are ignored).

    Returns:
        Establishment rows (``cnpj``, ``municipio``, ``uf``,
        ``is_matriz``) of the given CNPJs; empty when none qualifies or
        the table does not exist yet.
    """
    wanted = {re.sub(r"\D", "", str(cnpj or "")) for cnpj in cnpjs}
    wanted = {cnpj for cnpj in wanted if len(cnpj) == 14}
    if not wanted:
        return []
    if ICEBERG_CATALOG_URI.startswith("sqlite"):
        return [
            row
            for batch in read_silver_entities("establishments")
            for row in batch
            if re.sub(r"\D", "", str(row.get("cnpj") or "")) in wanted
        ]
    catalog = get_catalog(ICEBERG_WAREHOUSE_SILVER)
    try:
        catalog.load_table(f"{ICEBERG_NAMESPACE}.establishments")
    except NoSuchTableError:
        logger.info("Silver establishments table not found; nothing to read")
        return []
    rows: list[dict[str, Any]] = []
    ordered = sorted(wanted)
    for offset in range(0, len(ordered), _TRINO_IN_BATCH):
        chunk = ordered[offset : offset + _TRINO_IN_BATCH]
        # CNPJs are digits-only after the normalization above, so the
        # literals cannot break out of the IN clause.
        literal = ", ".join(f"'{cnpj}'" for cnpj in chunk)
        rows.extend(
            trino.run_query(
                "SELECT cnpj, municipio, uf, is_matriz"
                f" FROM {SILVER_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.establishments"
                f" WHERE cnpj IN ({literal})"  # nosec: B608
            )
        )
    return rows


def read_terms_pilot_cohort(
    include_flagged: bool = True,
    siafi_codes: Collection[str] = (),
) -> list[dict[str, Any]]:
    """Enumerates the PR-D-05b pilot cohort of contracts for the terms crawl.

    The pilot cut (declared, not the 205k universe — one terms-endpoint
    request per contract has a rate-limit cost): (a) every contract with
    ``f_value_amendment = 1`` by the proxy in the gold
    ``contract_amendments`` mart (the Q4 control cohort) and (b) the
    contracts of the pilot municipality (Recife, SIAFI 2531 — the same
    editorial cut of PR-D-08). Each record carries the control number and
    the cohorts it belongs to (``flagged``, ``pilot`` or both).

    Reads go through Trino (cluster-only: the cohort depends on the gold
    mart). A missing ``contract_amendments`` table (never built) degrades
    to the pilot-municipality cohort with a warning, like the TSE
    dependent-marts guard; offline (SQLite catalog) the cohort is empty.
    Deterministic: records are deduplicated and sorted by control number.

    Args:
        include_flagged: Include cohort (a) — proxy-flagged contracts.
        siafi_codes: SIAFI codes of cohort (b) — pilot municipalities
            (digits-only values; others are ignored).

    Returns:
        One record per contract: ``numeroControlePNCP`` and ``cohort``.

    Raises:
        ValueError: If neither cohort is selected (empty cut is a
            configuration error, not a successful empty crawl).
    """
    codes = {re.sub(r"\D", "", str(code or "")) for code in siafi_codes}
    codes = {code for code in codes if code}
    if not include_flagged and not codes:
        raise ValueError(
            "The pncp_contract_terms cohort is empty: set include_flagged "
            "and/or siafi_codes"
        )
    if ICEBERG_CATALOG_URI.startswith("sqlite"):
        logger.warning(
            "Offline catalog: the terms pilot cohort requires the Trino marts; "
            "returning an empty cohort"
        )
        return []

    cohorts: dict[str, set[str]] = {}
    if include_flagged:
        try:
            rows = trino.run_query(
                "SELECT contract_id"
                f" FROM {GOLD_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.contract_amendments"
                " WHERE f_value_amendment = 1"  # nosec: B608
            )
        except RuntimeError as exc:
            if "TABLE_NOT_FOUND" not in str(exc):
                raise
            logger.warning(
                "Gold contract_amendments mart not found; flagged cohort empty: %s",
                exc,
            )
            rows = []
        for row in rows:
            contract_id = str(row.get("contract_id") or "")
            if contract_id:
                cohorts.setdefault(contract_id, set()).add("flagged")
    if codes:
        # SIAFI codes are digits-only after the normalization above, so the
        # literals cannot break out of the IN clause.
        literal = ", ".join(f"'{code}'" for code in sorted(codes))
        rows = trino.run_query(
            "SELECT id"
            f" FROM {SILVER_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.contracts"
            f" WHERE buyer.siafi_code IN ({literal})"  # nosec: B608
        )
        for row in rows:
            contract_id = str(row.get("id") or "")
            if contract_id:
                cohorts.setdefault(contract_id, set()).add("pilot")

    return [
        {"numeroControlePNCP": contract_id, "cohort": "+".join(sorted(tags))}
        for contract_id, tags in sorted(cohorts.items())
    ]


def load_municipalities(run_date: date | None = None) -> str:
    """Loads the vendored municipality reference into the silver, idempotently.

    The rows come from the packaged CSV (``capiba.ingestion.geography``);
    the load is **idempotent by content**: ``ibge_code`` values already
    present in the partition are skipped, so a re-run never duplicates rows
    with either catalog (unlike the dump entities, there is no Trino
    DELETE involved — the reference is small and full-content comparable).

    Args:
        run_date: Partition date; defaults to today (UTC).

    Returns:
        The Iceberg table identifier written.
    """
    partition = _partition_day(run_date)
    existing = {
        str(row.get("ibge_code"))
        for batch in read_silver_entities("municipalities")
        for row in batch
        if row.get("dt") == partition
    }
    rows = [row for row in municipality_rows() if row["ibge_code"] not in existing]
    table_id = write_silver_entities("municipalities", rows, run_date=run_date)
    logger.info(
        "Municipalities reference loaded: %d new rows (%d already present)",
        len(rows),
        len(existing),
    )
    return table_id


def read_fraud_signals() -> list[dict[str, Any]]:
    """Reads every row of the gold ``fraud_signals`` Iceberg table.

    In the cluster the read goes through Trino: ``write_fraud_signals``
    deletes the day's partition before appending, and the positional
    delete files written by Trino break the pyiceberg scan on the pinned
    pyarrow ("DecodeArrow of DictAccumulator" — see
    ``read_silver_contracts``). Offline (SQLite catalog) there are no
    Trino-side deletes, so the local scan stands.

    Returns:
        Signal rows as dicts (empty when the table does not exist yet).
    """
    catalog = get_catalog(ICEBERG_WAREHOUSE_GOLD)
    try:
        table = catalog.load_table(f"{ICEBERG_NAMESPACE}.fraud_signals")
    except NoSuchTableError:
        logger.info("Gold fraud_signals table not found; nothing to read")
        return []
    if not ICEBERG_CATALOG_URI.startswith("sqlite"):
        return trino.run_query(
            f"SELECT * FROM {GOLD_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.fraud_signals"  # nosec: B608
        )
    rows = table.scan().to_pandas().to_dict("records")
    return cast(list[dict[str, Any]], rows)


def count_fraud_signals() -> int:
    """Counts rows of the gold ``fraud_signals`` table without scanning it.

    Same contract as ``count_silver_contracts``: Trino ``count(*)`` in the
    cluster, local scan with the offline SQLite catalog.

    Returns:
        Row count of the gold ``fraud_signals`` table.
    """
    if ICEBERG_CATALOG_URI.startswith("sqlite"):
        return len(read_fraud_signals())
    rows = trino.run_query(
        f"SELECT count(*) AS n FROM {GOLD_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.fraud_signals"  # nosec: B608
    )
    return int(rows[0]["n"]) if rows else 0


def delete_fraud_signals_partition(run_date: date) -> None:
    """Deletes the gold ``fraud_signals`` rows of one partition day (Trino).

    Idempotency half of ``write_fraud_signals``: a detect attempt that
    dies after writing (OOMKills of 2026-08-21) is requeued and would
    re-append the same signals on retry. Deleting the partition before
    appending makes every retry start clean. A failure here propagates —
    no append is attempted, so signals are never duplicated.

    No-op with the offline SQLite catalog (no Trino to DELETE through)
    and when the table does not exist yet (first detect run).

    Args:
        run_date: Partition day to delete.
    """
    if ICEBERG_CATALOG_URI.startswith("sqlite"):
        logger.info("Offline catalog; skipping fraud_signals partition delete")
        return
    catalog = get_catalog(ICEBERG_WAREHOUSE_GOLD)
    try:
        catalog.load_table(f"{ICEBERG_NAMESPACE}.fraud_signals")
    except NoSuchTableError:
        logger.info("Gold fraud_signals table not found; nothing to delete")
        return
    partition = _partition_day(run_date)
    trino.run_query(
        f"DELETE FROM {GOLD_TRINO_CATALOG}.{ICEBERG_NAMESPACE}.fraud_signals"  # nosec: B608
        f" WHERE dt = DATE '{partition.isoformat()}'"
    )
    logger.info("Gold fraud_signals partition dt=%s deleted", partition)


def write_fraud_signals(
    signals: list[dict[str, Any]], run_date: date | None = None
) -> str:
    """Appends detected fraud signals to the gold Iceberg table.

    The write replaces the day's partition: before appending, the rows of
    ``dt = run_date`` are deleted through Trino
    (``delete_fraud_signals_partition``), so re-runs and retried attempts
    of the same day replace the previous signals instead of duplicating
    them (same failure semantics as ``write_silver``: a DELETE failure
    aborts before any append). With the offline SQLite catalog there is
    no Trino, so the write degrades to a pure append.

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
        if not ICEBERG_CATALOG_URI.startswith("sqlite"):
            # Delete-half first: a failure here aborts before the append,
            # so rows are never duplicated (see the docstring).
            delete_fraud_signals_partition(partition)
            # The Trino DELETE commits a new snapshot; without a refresh the
            # append commits against the stale one and the catalog rejects it
            # ("Branch or tag `main`'s snapshot has changed", 2026-08-21).
            table = table.refresh()
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
