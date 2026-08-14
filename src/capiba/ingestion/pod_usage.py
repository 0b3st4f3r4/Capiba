"""Pod resource-usage collector (metrics-server / kubectl fallback).

Chunk: pod_usage
Responsibility: Collect a point-in-time CPU/memory snapshot of the platform
pods from the Kubernetes metrics-server API when running in-cluster
(ServiceAccount token), falling back to parsed ``kubectl top`` output for
local dev runs outside the cluster. Backs the ``pod_usage`` source of the
declarative pipeline registry (hourly_pod_usage pipeline).

Dependencies: requests
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404  # fixed argv list, no shell, kubectl resolved via shutil.which
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)

SERVICEACCOUNT_DIR = Path("/var/run/secrets/kubernetes.io/serviceaccount")
KUBERNETES_API = "https://kubernetes.default.svc"

# Kubernetes quantity suffixes -> multiplier to the base unit.
_MEMORY_MULTIPLIERS = {
    "Ki": 1024,
    "Mi": 1024**2,
    "Gi": 1024**3,
    "Ti": 1024**4,
}
_CPU_MULTIPLIERS = {
    "n": 1e-6,  # nanocores -> millicores
    "u": 1e-3,  # microcores -> millicores
    "m": 1.0,  # already millicores
}


def parse_cpu_millicores(value: str) -> int:
    """Parses a Kubernetes CPU quantity into millicores.

    Handles the metrics-server/kubectl formats: ``250m``, ``1`` (cores),
    ``1500u`` and ``1234567n``.
    """
    for suffix, multiplier in _CPU_MULTIPLIERS.items():
        if value.endswith(suffix):
            return round(float(value[: -len(suffix)]) * multiplier)
    return round(float(value) * 1000)  # plain number = cores


def parse_memory_bytes(value: str) -> int:
    """Parses a Kubernetes memory quantity into bytes.

    Handles the binary suffixes used by metrics-server/kubectl
    (``Ki``/``Mi``/``Gi``/``Ti``); a plain number is bytes.
    """
    for suffix, multiplier in _MEMORY_MULTIPLIERS.items():
        if value.endswith(suffix):
            return int(value[: -len(suffix)]) * multiplier
    return int(value)


def parse_metrics_server_payload(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Parses a metrics-server pod list into flat usage records.

    Args:
        payload: JSON of ``/apis/metrics.k8s.io/v1beta1/namespaces/<ns>/pods``.

    Returns:
        One record per container: pod, container, cpu_millicores,
        memory_bytes.
    """
    records: list[dict[str, Any]] = []
    for item in payload.get("items", []):
        pod = item.get("metadata", {}).get("name", "")
        for container in item.get("containers", []):
            usage = container.get("usage", {})
            records.append(
                {
                    "pod": pod,
                    "container": container.get("name", ""),
                    "cpu_millicores": parse_cpu_millicores(usage.get("cpu", "0")),
                    "memory_bytes": parse_memory_bytes(usage.get("memory", "0")),
                }
            )
    return records


def parse_kubectl_top(output: str) -> list[dict[str, Any]]:
    """Parses ``kubectl top pods --no-headers [--containers]`` output.

    Lines are ``<pod> [<container>] <cpu> <memory>`` — the container column
    is present only with ``--containers``; pod-level lines get
    ``container=None``.
    """
    records: list[dict[str, Any]] = []
    for line in output.splitlines():
        parts = line.split()
        if len(parts) == 4:
            pod, container, cpu, memory = parts
        elif len(parts) == 3:
            pod, container, cpu, memory = parts[0], None, parts[1], parts[2]
        else:
            continue
        records.append(
            {
                "pod": pod,
                "container": container,
                "cpu_millicores": parse_cpu_millicores(cpu),
                "memory_bytes": parse_memory_bytes(memory),
            }
        )
    return records


def _fetch_from_metrics_server(namespace: str) -> list[dict[str, Any]]:
    """Fetches the pod metrics from the in-cluster metrics-server API."""
    token = (SERVICEACCOUNT_DIR / "token").read_text(encoding="utf-8").strip()
    ca_cert = SERVICEACCOUNT_DIR / "ca.crt"
    response = requests.get(
        f"{KUBERNETES_API}/apis/metrics.k8s.io/v1beta1/namespaces/{namespace}/pods",
        headers={"Authorization": f"Bearer {token}"},
        verify=str(ca_cert),
        timeout=10,
    )
    response.raise_for_status()
    return parse_metrics_server_payload(response.json())


def _fetch_from_kubectl(namespace: str) -> list[dict[str, Any]]:
    """Fetches pod metrics via ``kubectl top`` (local dev fallback)."""
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        raise FileNotFoundError("kubectl not found in PATH")
    completed = subprocess.run(  # nosec B603  # fixed argv list, no shell, absolute kubectl path
        [kubectl, "top", "pods", "-n", namespace, "--no-headers", "--containers"],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    return parse_kubectl_top(completed.stdout)


def fetch_pod_usage(namespace: str = "capiba") -> list[dict[str, Any]]:
    """Collects a CPU/memory snapshot of the namespace pods.

    Uses the metrics-server API when running in-cluster (ServiceAccount
    token present), otherwise falls back to ``kubectl top`` (local dev).
    Degrades gracefully: when neither path is available, logs a warning and
    returns an empty snapshot instead of failing the pipeline run.

    Args:
        namespace: Kubernetes namespace to collect from.

    Returns:
        Usage records with a ``collected_at`` UTC timestamp.
    """
    try:
        if (SERVICEACCOUNT_DIR / "token").exists():
            records = _fetch_from_metrics_server(namespace)
        else:
            records = _fetch_from_kubectl(namespace)
    except Exception as exc:
        logger.warning("Pod usage collection unavailable: %s", exc)
        return []

    collected_at = datetime.now(UTC).isoformat()
    for record in records:
        record["collected_at"] = collected_at
    logger.info(
        "Pod usage collected: %d container records in namespace '%s'",
        len(records),
        namespace,
    )
    return records
