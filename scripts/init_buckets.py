#!/usr/bin/env python3
"""Creates the Capiba bucket layout in MinIO and the Iceberg warehouses.

Buckets (medallion layout plus infrastructure):

- ``capiba-bronze`` — raw payloads from public APIs (partitioned by
  source and date), bronze Iceberg tables and evidence files
  (evidence/<type>/<source>/)
- ``capiba-silver`` — silver Iceberg tables (normalized contracts)
- ``capiba-gold`` — per-run quality/lineage/persistence reports and gold
  Iceberg marts (dbt)
- ``capiba-artifacts`` — code/DAG artifacts synced by Airflow
- ``capiba-airflow-logs`` — Airflow remote task logs
- ``capiba-backups`` — logical database backups (CronJob)
- ``capiba-public`` — public batch export of the LGPD-cleared gold marts
  (CSV/Parquet under marts/<mart>/dt=<date>/). The public-read bucket
  policy is a deploy decision (charts/values), deliberately NOT set here.

Iceberg warehouses (Lakekeeper REST catalog), one per medallion bucket:

- ``bronze`` → ``capiba-bronze``
- ``silver`` → ``capiba-silver``
- ``gold`` → ``capiba-gold``

Requires MinIO reachable at ``MINIO_ENDPOINT`` (from .env); warehouse
provisioning requires the catalog reachable at ``ICEBERG_CATALOG_URI``.
For the local cluster, start the port-forwards first
(``scripts/port-forward.sh``).

Usage:
    python scripts/init_buckets.py
"""

from __future__ import annotations

import logging

import requests
from minio import Minio

from capiba import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BUCKETS = [
    "capiba-bronze",
    "capiba-silver",
    "capiba-gold",
    "capiba-artifacts",
    "capiba-airflow-logs",
    "capiba-backups",
    "capiba-public",
]

# Iceberg warehouse name -> MinIO bucket backing it.
WAREHOUSES = {
    config.ICEBERG_WAREHOUSE_BRONZE: config.LAKE_BUCKET_BRONZE,
    config.ICEBERG_WAREHOUSE_SILVER: config.LAKE_BUCKET_SILVER,
    config.ICEBERG_WAREHOUSE_GOLD: config.LAKE_BUCKET_GOLD,
}


def _management_url() -> str:
    """Derives the Lakekeeper management API base from the catalog URI."""
    return config.ICEBERG_CATALOG_URI.removesuffix("/catalog") + "/management/v1"


def _auth_headers() -> dict[str, str]:
    """Returns an Authorization header when OAuth2 credentials are configured.

    Fetches a token via the client_credentials grant (Keycloak client
    "capiba-services" in the cluster). Empty when auth is disabled (local
    unauthenticated catalog).
    """
    if not (config.ICEBERG_OAUTH2_CLIENT_ID and config.ICEBERG_OAUTH2_CLIENT_SECRET):
        return {}
    response = requests.post(
        config.ICEBERG_OAUTH2_SERVER_URI,
        data={
            "grant_type": "client_credentials",
            "client_id": config.ICEBERG_OAUTH2_CLIENT_ID,
            "client_secret": config.ICEBERG_OAUTH2_CLIENT_SECRET,
        },
        timeout=30,
    )
    response.raise_for_status()
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def init_buckets() -> None:
    """Creates the MinIO bucket layout (idempotent)."""
    client = Minio(
        config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY,
        secure=config.MINIO_SECURE,
    )
    for bucket in BUCKETS:
        if client.bucket_exists(bucket):
            logger.info("Bucket %s already exists", bucket)
        else:
            client.make_bucket(bucket)
            logger.info("Created bucket %s", bucket)


def init_warehouses() -> None:
    """Creates the Lakekeeper warehouses over MinIO (idempotent)."""
    base = _management_url()
    headers = _auth_headers()
    # Bootstraps the default project (no-op once bootstrapped).
    response = requests.post(
        f"{base}/bootstrap",
        json={"accept-terms-of-use": True},
        headers=headers,
        timeout=30,
    )
    if response.ok:
        logger.info("Lakekeeper default project bootstrapped")

    existing = {
        w["name"]: w
        for w in requests.get(f"{base}/warehouse", headers=headers, timeout=10).json()[
            "warehouses"
        ]
    }
    for name, bucket in WAREHOUSES.items():
        if name in existing:
            profile = existing[name].get("storage-profile", {})
            current_endpoint = profile.get("endpoint", "")
            needs_fix = (
                profile.get("remote-signing-enabled")
                or not profile.get("sts-enabled")
                or current_endpoint != config.ICEBERG_STORAGE_ENDPOINT
            )
            if needs_fix:
                # STS credential vending: clients (pyiceberg, the Lakekeeper UI
                # preview via DuckDB-WASM) receive temporary MinIO credentials
                # scoped to the table. Remote signing stays OFF — duckdb-iceberg
                # (dbt) does not support the S3V4RestSigner protocol and uses its
                # own static MinIO secret instead.
                profile["remote-signing-enabled"] = False
                profile["sts-enabled"] = True
                profile["endpoint"] = config.ICEBERG_STORAGE_ENDPOINT
                response = requests.post(
                    f"{base}/warehouse/{existing[name]['id']}/storage",
                    json={
                        "storage-profile": profile,
                        "storage-credential": {
                            "type": "s3",
                            "credential-type": "access-key",
                            "aws-access-key-id": config.MINIO_ACCESS_KEY,
                            "aws-secret-access-key": config.MINIO_SECRET_KEY,
                        },
                    },
                    headers=headers,
                    timeout=30,
                )
                response.raise_for_status()
                logger.info(
                    "Warehouse %s: updated storage endpoint to %s",
                    name,
                    config.ICEBERG_STORAGE_ENDPOINT,
                )
            else:
                logger.info("Warehouse %s already exists", name)
            continue
        payload = {
            "warehouse-name": name,
            "storage-profile": {
                "type": "s3",
                "bucket": bucket,
                "region": config.ICEBERG_S3_REGION,
                "endpoint": config.ICEBERG_STORAGE_ENDPOINT,
                "path-style-access": True,
                "flavor": "minio",
                # STS credential vending: the catalog mints temporary MinIO
                # credentials per table for clients (pyiceberg, Lakekeeper UI
                # preview). Remote signing stays OFF — duckdb-iceberg (dbt)
                # does not support the S3V4RestSigner protocol.
                "sts-enabled": True,
                "remote-signing-enabled": False,
            },
            "storage-credential": {
                "type": "s3",
                "credential-type": "access-key",
                "aws-access-key-id": config.MINIO_ACCESS_KEY,
                "aws-secret-access-key": config.MINIO_SECRET_KEY,
            },
        }
        response = requests.post(
            f"{base}/warehouse", json=payload, headers=headers, timeout=30
        )
        response.raise_for_status()
        logger.info("Created warehouse %s -> s3://%s", name, bucket)


def main() -> None:
    init_buckets()
    try:
        init_warehouses()
    except requests.RequestException as e:
        logger.warning(
            "Skipping Iceberg warehouse provisioning (catalog unreachable at %s): %s",
            config.ICEBERG_CATALOG_URI,
            e,
        )


if __name__ == "__main__":
    main()
