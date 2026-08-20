"""Centralized Capiba configuration.

All environment variables read by the code are defined
here. No configuration should be scattered across other files.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

_ = load_dotenv()

# =============================================================================
# Object storage (MinIO/S3)
# =============================================================================

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "capiba")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "capiba-secret")
MINIO_SECURE = os.getenv("MINIO_SECURE", "false").lower() == "true"

# =============================================================================
# Multi-model database (ArangoDB)
# =============================================================================

ARANGODB_HOST = os.getenv("ARANGODB_HOST", "localhost")
ARANGODB_PORT = int(os.getenv("ARANGODB_PORT", "8529"))
ARANGODB_ROOT_PASSWORD = os.getenv("ARANGODB_ROOT_PASSWORD", "capiba-arangodb")
ARANGODB_DATABASE = os.getenv("ARANGODB_DATABASE", "capiba")
ARANGODB_GRAPH_NAME = os.getenv("ARANGODB_GRAPH_NAME", "capiba_graph")
ARANGODB_USE_TLS = os.getenv("ARANGODB_USE_TLS", "false").lower() == "true"

# =============================================================================
# Cache (Redis)
# =============================================================================

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
REDIS_TTL_DEFAULT = int(os.getenv("REDIS_TTL_DEFAULT", "3600"))
# TTLs per API hot path (seconds): risk signals per CNPJ and municipal
# ranking aggregation (see capiba.api.cache).
REDIS_TTL_SIGNALS = int(os.getenv("REDIS_TTL_SIGNALS", "300"))
REDIS_TTL_RANKING = int(os.getenv("REDIS_TTL_RANKING", "600"))

# =============================================================================
# Data lake (medallion layout in MinIO)
# =============================================================================

LAKE_BUCKET_BRONZE = os.getenv("LAKE_BUCKET_BRONZE", "capiba-bronze")
LAKE_BUCKET_SILVER = os.getenv("LAKE_BUCKET_SILVER", "capiba-silver")
LAKE_BUCKET_GOLD = os.getenv("LAKE_BUCKET_GOLD", "capiba-gold")

# Iceberg REST catalog (Lakekeeper) and warehouse names. Each warehouse maps
# to a medallion bucket in MinIO; tables are addressed as
# "<namespace>.<table>" inside a warehouse.
ICEBERG_CATALOG_URI = os.getenv("ICEBERG_CATALOG_URI", "http://localhost:8181/catalog")
ICEBERG_WAREHOUSE_BRONZE = os.getenv("ICEBERG_WAREHOUSE_BRONZE", "bronze")
ICEBERG_WAREHOUSE_SILVER = os.getenv("ICEBERG_WAREHOUSE_SILVER", "silver")
ICEBERG_WAREHOUSE_GOLD = os.getenv("ICEBERG_WAREHOUSE_GOLD", "gold")
ICEBERG_S3_REGION = os.getenv("ICEBERG_S3_REGION", "us-east-1")
# OAuth2 client credentials for the Lakekeeper REST catalog (Keycloak client
# "capiba-services", client_credentials grant). Empty disables auth (offline
# runs against a SQLite catalog or an unauthenticated catalog).
ICEBERG_OAUTH2_CLIENT_ID = os.getenv("ICEBERG_OAUTH2_CLIENT_ID", "")
ICEBERG_OAUTH2_CLIENT_SECRET = os.getenv("ICEBERG_OAUTH2_CLIENT_SECRET", "")
# Token endpoint used by the REST catalog client. Defaults to the in-cluster
# Keycloak service (plain HTTP; the issuer is pinned to the ingress host).
ICEBERG_OAUTH2_SERVER_URI = os.getenv(
    "ICEBERG_OAUTH2_SERVER_URI",
    "http://capiba-keycloak:8080/realms/capiba/protocol/openid-connect/token",
)
# Local filesystem warehouse used when ICEBERG_CATALOG_URI points to a SQLite
# catalog (offline runs and tests, no MinIO/Lakekeeper needed).
ICEBERG_LOCAL_WAREHOUSE = os.getenv("ICEBERG_LOCAL_WAREHOUSE", "")
# S3 endpoint stored in the Lakekeeper storage profiles. Use the HTTPS
# ingress so the Lakekeeper UI (loaded over HTTPS) can read table data files
# without mixed-content blocking. In-cluster clients reach the same host via
# the CoreDNS rewrite in scripts/cluster.sh.
ICEBERG_STORAGE_ENDPOINT = os.getenv(
    "ICEBERG_STORAGE_ENDPOINT", "https://s3.capiba.local:8443"
)

# dbt lakehouse project (gold marts). In the cluster, the artifacts sync
# places it under /synced/dbt; locally it defaults to the repo's dbt/ dir.
DBT_PROJECT_DIR = os.getenv(
    "DBT_PROJECT_DIR",
    str(Path(__file__).resolve().parents[2] / "dbt"),
)

# Trino SQL gateway over the Iceberg catalog (used for lake maintenance
# and ad-hoc queries; port-forwarded to localhost:8081 outside the cluster).
TRINO_URL = os.getenv("TRINO_URL", "http://localhost:8081")
TRINO_USER = os.getenv("TRINO_USER", "capiba")
TRINO_PASSWORD = os.getenv("TRINO_PASSWORD", "capiba-trino")

# =============================================================================
# Public export
# =============================================================================

# MinIO bucket that receives the public batch export of the LGPD-cleared
# gold marts (CSV/Parquet, versioned marts/<mart>/dt=<YYYY-MM-DD>/). The
# public-read bucket policy is a deploy decision (charts/values) — the
# export never touches buckets outside this one.
PUBLIC_EXPORT_BUCKET = os.getenv("PUBLIC_EXPORT_BUCKET", "capiba-public")

# Expiry (in seconds) of the presigned download URLs issued by the public
# API (GET /v1/public/marts/{name}/{csv|parquet}).
PUBLIC_EXPORT_PRESIGN_EXPIRY_S = int(
    os.getenv("PUBLIC_EXPORT_PRESIGN_EXPIRY_S", "3600")
)

# =============================================================================
# Public APIs
# =============================================================================

PNCP_API_URL = os.getenv(
    "PNCP_API_URL",
    "https://pncp.gov.br/api/consulta",
)
TRANSPARENCY_API_URL = os.getenv(
    "TRANSPARENCY_API_URL",
    "https://api.portaldatransparencia.gov.br/api-de-dados",
)
TRANSPARENCY_API_KEY = os.getenv("TRANSPARENCY_API_KEY", "")
TRANSPARENCY_AGENCY_CODES = [
    c.strip()
    for c in os.getenv("TRANSPARENCY_AGENCY_CODES", "").split(",")
    if c.strip()
]

QUERIDO_DIARIO_API_URL = os.getenv(
    "QUERIDO_DIARIO_API_URL",
    "https://api.queridodiario.ok.org.br",
)

# Registration data (Federal Revenue — CNPJ)
FEDERAL_REVENUE_BASE_URL = os.getenv(
    "FEDERAL_REVENUE_BASE_URL",
    "https://arquivos.receitafederal.gov.br/public.php/dav/files/YggdBLfdninEJX9",
)
# Files downloaded by the monthly DAG: the small reference tables by
# default (the full Empresas/Estabelecimentos/Socios dumps are GBs and
# should be enabled explicitly, comma-separated).
FEDERAL_REVENUE_FILES = [
    f.strip()
    for f in os.getenv(
        "FEDERAL_REVENUE_FILES",
        "Cnaes.zip,Motivos.zip,Municipios.zip,Naturezas.zip,Paises.zip,Qualificacoes.zip",
    ).split(",")
    if f.strip()
]

# Electoral data (TSE — campaign finance / prestação de contas)
TSE_BASE_URL = os.getenv(
    "TSE_BASE_URL",
    "https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas",
)
# The candidates dump (consulta_cand — who was elected) lives in a sibling
# directory of the CDN.
TSE_CANDIDATES_BASE_URL = os.getenv(
    "TSE_CANDIDATES_BASE_URL",
    "https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand",
)
# Election year of the fixed prestação de contas snapshot (2024 municipal
# elections); the dump is not month-indexed, so the pipeline's
# reference_month does not apply (see dags/pipelines/monthly_tse.yaml).
TSE_ELECTION_YEAR = int(os.getenv("TSE_ELECTION_YEAR", "2024"))

# =============================================================================
# Evidence (files in MinIO/S3)
# =============================================================================

# Evidence storage; by default uses the same main MinIO
EVIDENCE_MINIO_ENDPOINT = os.getenv("EVIDENCE_MINIO_ENDPOINT", MINIO_ENDPOINT)
EVIDENCE_MINIO_ACCESS_KEY = os.getenv("EVIDENCE_MINIO_ACCESS_KEY", MINIO_ACCESS_KEY)
EVIDENCE_MINIO_SECRET_KEY = os.getenv("EVIDENCE_MINIO_SECRET_KEY", MINIO_SECRET_KEY)
EVIDENCE_MINIO_SECURE = (
    os.getenv("EVIDENCE_MINIO_SECURE", str(MINIO_SECURE)).lower() == "true"
)

# Evidence files live in the bronze bucket, under evidence/<type>/<source>/
EVIDENCE_BUCKET = os.getenv("EVIDENCE_BUCKET", LAKE_BUCKET_BRONZE)

# Required metadata per file
EVIDENCE_REQUIRED_METADATA = [
    "contract_id",
    "entity_cnpj",
    "evidence_type",
    "captured_at",
    "source",
    "hash_sha256",
    "captured_by",
]

# Accepted formats
EVIDENCE_FORMATS_IMAGE = os.getenv(
    "EVIDENCE_FORMATS_IMAGE",
    "jpg,jpeg,png,tiff,tif,webp,heic,raw,dng",
).split(",")
EVIDENCE_FORMATS_DOCUMENT = os.getenv(
    "EVIDENCE_FORMATS_DOCUMENT",
    "pdf,doc,docx,txt,rtf,odt,xls,xlsx,csv,json,xml,html",
).split(",")
EVIDENCE_FORMATS_AUDIO = os.getenv(
    "EVIDENCE_FORMATS_AUDIO",
    "mp3,wav,ogg,flac,aac,m4a,wma",
).split(",")
EVIDENCE_FORMATS_VIDEO = os.getenv(
    "EVIDENCE_FORMATS_VIDEO",
    "mp4,avi,mkv,mov,wmv,flv,webm,mpeg,mpg",
).split(",")

# Size limits (bytes)
EVIDENCE_MAX_SIZE_IMAGE = int(
    os.getenv("EVIDENCE_MAX_SIZE_IMAGE", str(100 * 1024 * 1024))
)  # 100MB
EVIDENCE_MAX_SIZE_DOCUMENT = int(
    os.getenv("EVIDENCE_MAX_SIZE_DOCUMENT", str(500 * 1024 * 1024))
)  # 500MB
EVIDENCE_MAX_SIZE_AUDIO = int(
    os.getenv("EVIDENCE_MAX_SIZE_AUDIO", str(500 * 1024 * 1024))
)  # 500MB
EVIDENCE_MAX_SIZE_VIDEO = int(
    os.getenv("EVIDENCE_MAX_SIZE_VIDEO", str(2 * 1024 * 1024 * 1024))
)  # 2GB

# =============================================================================
# Detection
# =============================================================================

# Minimum wins per (buyer, supplier) for a pair to be flagged as a suspected
# collusion network in the detect post step. Calibration placeholder validated
# by battery D-02; PR-D-03 will calibrate on real volume.
DETECTION_COLLUSION_MIN_WINS = int(os.getenv("DETECTION_COLLUSION_MIN_WINS", "3"))

# Minimum distinct buyers in which the supplier pair must co-occur (PR-D-03b
# refinement; 1 = the single-buyer semantics of D-03). Stays at the default
# until battery D-03b calibrates the (min_wins, min_buyers) grid on real
# volume and a human decision promotes the calibrated values.
DETECTION_COLLUSION_MIN_BUYERS = int(os.getenv("DETECTION_COLLUSION_MIN_BUYERS", "1"))

# Merge threshold for the same_as edges written by the entity resolution
# after the CNPJ graph load. Calibrated by batteries D-07/D-07b
# (PR-D-07/PR-D-07b); lowering or raising it requires a new pre-registration
# (monotonicity invariant, PR-D-07 section 6).
DETECTION_ENTITY_THRESHOLD = float(os.getenv("DETECTION_ENTITY_THRESHOLD", "0.85"))

# Gates of the political_connection signal: minimum total donated to the
# elected candidate's campaign, minimum share of the donor-supplier in the
# buyer's contracted value within the mandate window, and the share that
# saturates the score (min(1.0, share / reference)). Pre-registered
# calibration placeholders (PR-D-08); changing them requires PR-D-08b.
DETECTION_POLITICAL_MIN_DONATION = float(
    os.getenv("DETECTION_POLITICAL_MIN_DONATION", "1000")
)
DETECTION_POLITICAL_MIN_SHARE = float(
    os.getenv("DETECTION_POLITICAL_MIN_SHARE", "0.05")
)
DETECTION_POLITICAL_SCORE_REFERENCE = float(
    os.getenv("DETECTION_POLITICAL_SCORE_REFERENCE", "0.25")
)

# Gates of the anomalous_geography signal: strict distance gate (km)
# between the supplier's and the buyer's municipality seats, and the
# distance that saturates the score (min(1.0, distance / reference)).
# Pre-registered calibration placeholders (PR-D-09); changing them
# requires PR-D-09b.
DETECTION_GEOGRAPHY_MAX_DISTANCE_KM = float(
    os.getenv("DETECTION_GEOGRAPHY_MAX_DISTANCE_KM", "100")
)
DETECTION_GEOGRAPHY_SCORE_REFERENCE = float(
    os.getenv("DETECTION_GEOGRAPHY_SCORE_REFERENCE", "1000")
)

# =============================================================================
# Notification (SMTP)
# =============================================================================

NOTIFICATION_EMAIL_HOST = os.getenv("NOTIFICATION_EMAIL_HOST", "smtp.gmail.com")
NOTIFICATION_EMAIL_PORT = int(os.getenv("NOTIFICATION_EMAIL_PORT", "587"))
NOTIFICATION_EMAIL_USER = os.getenv("NOTIFICATION_EMAIL_USER", "")
NOTIFICATION_EMAIL_PASSWORD = os.getenv("NOTIFICATION_EMAIL_PASSWORD", "")
NOTIFICATION_EMAIL_FROM = os.getenv("NOTIFICATION_EMAIL_FROM", "capiba@example.org")
NOTIFICATION_EMAIL_TLS = os.getenv("NOTIFICATION_EMAIL_TLS", "true").lower() == "true"

# Alert recipients (comma-separated e-mails). Empty disables pipeline
# notifications entirely (no-op with a debug log).
NOTIFICATION_RECIPIENTS = [
    r.strip() for r in os.getenv("NOTIFICATION_RECIPIENTS", "").split(",") if r.strip()
]
# Minimum signal score that triggers a detection alert (same threshold as
# _ALERT_THRESHOLD in capiba.api.services).
NOTIFICATION_ALERT_SCORE = float(os.getenv("NOTIFICATION_ALERT_SCORE", "0.7"))

# =============================================================================
# Portal dashboard (SSO via Keycloak)
# =============================================================================

SSO_ENABLED = os.getenv("SSO_ENABLED", "false").lower() == "true"
# Local dev default; override in any shared environment.
PORTAL_SESSION_SECRET = os.getenv("PORTAL_SESSION_SECRET", "capiba-dev-session-secret")
PORTAL_DOMAIN = os.getenv("PORTAL_DOMAIN", "capiba.local")
KEYCLOAK_ISSUER = os.getenv("KEYCLOAK_ISSUER", "")
# Public Keycloak URL served by the Traefik ingress (HTTPS/8443). Used for the
# browser authorization redirect; backchannel metadata fetching still uses
# KEYCLOAK_ISSUER (plain HTTP/8088) because pods do not trust the self-signed cert.
KEYCLOAK_PUBLIC_ISSUER = os.getenv(
    "KEYCLOAK_PUBLIC_ISSUER",
    f"https://keycloak.{PORTAL_DOMAIN}:8443/realms/capiba",
)
KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "")
KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "")

# =============================================================================
# Municipal alert subscriptions
# =============================================================================

# Public base URL of the API, used to build the confirmation, unsubscribe
# and evidence links embedded in the subscription e-mails.
PUBLIC_API_URL = os.getenv("PUBLIC_API_URL", f"https://api.{PORTAL_DOMAIN}:8443")

# =============================================================================
# Logging
# =============================================================================

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT = os.getenv("LOG_FORMAT", "json")
