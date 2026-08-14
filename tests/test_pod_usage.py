"""Tests for the pod usage source (metrics-server / kubectl fallback).

Responsibility: Validate the Kubernetes quantity parsers, the
metrics-server payload and ``kubectl top`` output parsers, and the
graceful degradation when neither the in-cluster API nor kubectl is
available (no cluster, no infra).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.ingestion import pod_usage
from capiba.ingestion.pod_usage import (
    fetch_pod_usage,
    parse_cpu_millicores,
    parse_kubectl_top,
    parse_memory_bytes,
    parse_metrics_server_payload,
)

METRICS_SERVER_PAYLOAD: dict[str, Any] = {
    "kind": "PodMetricsList",
    "apiVersion": "metrics.k8s.io/v1beta1",
    "items": [
        {
            "metadata": {"name": "capiba-api-6f8d9abcde-x1y2z", "namespace": "capiba"},
            "timestamp": "2026-01-15T12:07:00Z",
            "containers": [
                {"name": "api", "usage": {"cpu": "42m", "memory": "231880Ki"}}
            ],
        },
        {
            "metadata": {"name": "capiba-trino-0", "namespace": "capiba"},
            "timestamp": "2026-01-15T12:07:00Z",
            "containers": [
                {"name": "trino", "usage": {"cpu": "1", "memory": "1Gi"}},
                {"name": "sidecar", "usage": {"cpu": "2500000n", "memory": "128Mi"}},
            ],
        },
    ],
}

KUBECTL_TOP_CONTAINERS = """\
capiba-api-6f8d9abcde-x1y2z   api       42m   226Mi
capiba-trino-0                trino     1500m 1Gi
"""

KUBECTL_TOP_PODS = """\
capiba-api-6f8d9abcde-x1y2z   42m   226Mi
capiba-trino-0                2     1152Mi
"""


class TestQuantityParsers:
    """Kubernetes CPU/memory quantity parsing."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("250m", 250),
            ("42m", 42),
            ("1", 1000),
            ("2", 2000),
            ("2500000n", 2),  # 2.5 millicores (Python round half-to-even)
            ("1500u", 2),  # 1.5 millicores (round half-to-even)
        ],
    )
    def test_cpu(self, value: str, expected: int) -> None:
        assert parse_cpu_millicores(value) == expected

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("128Mi", 128 * 1024**2),
            ("231880Ki", 231880 * 1024),
            ("1Gi", 1024**3),
            ("1024", 1024),
        ],
    )
    def test_memory(self, value: str, expected: int) -> None:
        assert parse_memory_bytes(value) == expected


class TestMetricsServerParser:
    """Parsing of the metrics-server PodMetricsList payload."""

    def test_parses_one_record_per_container(self) -> None:
        records = parse_metrics_server_payload(METRICS_SERVER_PAYLOAD)

        assert len(records) == 3
        assert records[0] == {
            "pod": "capiba-api-6f8d9abcde-x1y2z",
            "container": "api",
            "cpu_millicores": 42,
            "memory_bytes": 231880 * 1024,
        }
        assert records[1]["pod"] == "capiba-trino-0"
        assert records[1]["cpu_millicores"] == 1000
        assert records[2]["container"] == "sidecar"
        assert records[2]["cpu_millicores"] == 2
        assert records[2]["memory_bytes"] == 128 * 1024**2

    def test_empty_payload(self) -> None:
        assert parse_metrics_server_payload({}) == []


class TestKubectlTopParser:
    """Parsing of the kubectl top fallback output."""

    def test_per_container_lines(self) -> None:
        records = parse_kubectl_top(KUBECTL_TOP_CONTAINERS)

        assert len(records) == 2
        assert records[0]["pod"] == "capiba-api-6f8d9abcde-x1y2z"
        assert records[0]["container"] == "api"
        assert records[0]["cpu_millicores"] == 42
        assert records[0]["memory_bytes"] == 226 * 1024**2
        assert records[1]["cpu_millicores"] == 1500

    def test_pod_level_lines(self) -> None:
        records = parse_kubectl_top(KUBECTL_TOP_PODS)

        assert len(records) == 2
        assert records[0]["container"] is None
        assert records[1]["cpu_millicores"] == 2000

    def test_empty_output(self) -> None:
        assert parse_kubectl_top("") == []


class TestFetchPodUsage:
    """Fetch path selection and graceful degradation."""

    def _serviceaccount_dir(self, tmp_path: Path) -> Path:
        """Writes a fake ServiceAccount token/ca into a temp dir."""
        (tmp_path / "token").write_text("sa-token", encoding="utf-8")
        (tmp_path / "ca.crt").write_text("fake-ca", encoding="utf-8")
        return tmp_path

    def test_in_cluster_uses_metrics_server(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """With a ServiceAccount token, the metrics-server API is queried."""
        monkeypatch.setattr(
            pod_usage, "SERVICEACCOUNT_DIR", self._serviceaccount_dir(tmp_path)
        )
        response = MagicMock()
        response.json.return_value = METRICS_SERVER_PAYLOAD
        get = MagicMock(return_value=response)
        monkeypatch.setattr(pod_usage.requests, "get", get)

        records = fetch_pod_usage("capiba")

        assert len(records) == 3
        assert all("collected_at" in r for r in records)
        url = get.call_args.args[0]
        assert "/apis/metrics.k8s.io/v1beta1/namespaces/capiba/pods" in url
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer sa-token"

    def test_outside_cluster_uses_kubectl(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without a ServiceAccount token, kubectl top is parsed."""
        monkeypatch.setattr(pod_usage, "SERVICEACCOUNT_DIR", tmp_path)
        monkeypatch.setattr(pod_usage.shutil, "which", lambda _: "/usr/bin/kubectl")
        completed = MagicMock(stdout=KUBECTL_TOP_CONTAINERS)
        run = MagicMock(return_value=completed)
        monkeypatch.setattr(pod_usage.subprocess, "run", run)

        records = fetch_pod_usage("capiba")

        assert len(records) == 2
        command = run.call_args.args[0]
        assert command[:2] == ["/usr/bin/kubectl", "top"]
        assert "--containers" in command

    def test_no_cluster_no_kubectl_degrades_to_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """Without cluster credentials or kubectl, returns an empty snapshot."""
        monkeypatch.setattr(pod_usage, "SERVICEACCOUNT_DIR", tmp_path)
        monkeypatch.setattr(pod_usage.shutil, "which", lambda _: None)

        assert fetch_pod_usage("capiba") == []

    def test_metrics_server_failure_degrades_to_empty(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        """A failing metrics-server call does not raise — empty snapshot."""
        monkeypatch.setattr(
            pod_usage, "SERVICEACCOUNT_DIR", self._serviceaccount_dir(tmp_path)
        )

        def broken_get(*_args: Any, **_kwargs: Any) -> Any:
            raise ConnectionError("metrics-server down")

        monkeypatch.setattr(pod_usage.requests, "get", broken_get)

        assert fetch_pod_usage("capiba") == []
