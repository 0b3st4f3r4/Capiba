#!/usr/bin/env python3
"""Publishes Capiba artifacts (source code, DAGs and the dbt project) to MinIO.

Packages ``src/``, ``dags/`` and ``dbt/`` as tarballs and uploads them to the
artifacts bucket, where the Airflow deployment syncs them from at pod
startup (and periodically via a sidecar). This allows code and DAG
changes to reach the cluster without rebuilding the Airflow image.

Requires MinIO to be reachable at ``MINIO_ENDPOINT`` (from .env);
for the local cluster, start the port-forwards first
(``scripts/port-forward.sh``).

Usage:
    python scripts/publish_artifacts.py
"""

from __future__ import annotations

import io
import logging
import os
import tarfile
from pathlib import Path

from minio import Minio

from capiba import config

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
ARTIFACTS_PREFIX = "airflow"

# (local directory, top-level name inside the tarball, artifact file name)
ARTIFACTS = [
    (PROJECT_ROOT / "src", "src", "code.tar.gz"),
    (PROJECT_ROOT / "dags", "dags", "dags.tar.gz"),
    (PROJECT_ROOT / "dbt", "dbt", "dbt.tar.gz"),
]


def _build_tarball(source_dir: Path, arcname: str) -> bytes:
    """Packages a directory as .tar.gz, skipping caches and local state."""

    def _skip_caches(info: tarfile.TarInfo) -> tarfile.TarInfo | None:
        name = Path(info.name).name
        if name == "__pycache__" or name.endswith((".pyc", ".pyo")):
            return None
        return info

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        tar.add(source_dir, arcname=arcname, filter=_skip_caches)
    return buffer.getvalue()


def main() -> None:
    bucket = os.getenv("ARTIFACTS_BUCKET", "capiba-artifacts")
    client = Minio(
        config.MINIO_ENDPOINT,
        access_key=config.MINIO_ACCESS_KEY,
        secret_key=config.MINIO_SECRET_KEY,
        secure=config.MINIO_SECURE,
    )

    if not client.bucket_exists(bucket):
        client.make_bucket(bucket)
        logger.info("Created bucket %s", bucket)

    for source_dir, arcname, filename in ARTIFACTS:
        data = _build_tarball(source_dir, arcname)
        key = f"{ARTIFACTS_PREFIX}/{filename}"
        result = client.put_object(
            bucket,
            key,
            io.BytesIO(data),
            length=len(data),
            content_type="application/gzip",
        )
        logger.info(
            "Uploaded s3://%s/%s (%d bytes, etag=%s)",
            bucket,
            key,
            len(data),
            result.etag,
        )

    logger.info(
        "Artifacts published. DAG changes are picked up by the sync sidecar; "
        "for code changes, restart the deployment: "
        "kubectl rollout restart deploy/capiba-airflow -n capiba"
    )


if __name__ == "__main__":
    main()
