"""Tests for the dump download task (file_dump formula).

Responsibility: Validate the per-file bronze upload and the resume
semantics of ``task_download_source`` — files already present in the
bronze layer for the run date are skipped and re-entered into the
manifest, so a retried task does not restart a multi-GB dump.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.pipeline import lake


def _spec_file(tmp_path: Path) -> Path:
    """Writes a minimal file_dump spec with two federal_revenue files."""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        """\
name: fr_task
window: previous_month
sources:
  - name: federal_revenue
    params:
      files: ["Empresas0.zip", "Cnaes.zip"]
formula: file_dump
destinations: [lake_bronze]
""",
        encoding="utf-8",
    )
    return spec_path


@pytest.fixture
def mocked_lake(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    """Replaces the lake bronze functions used by the download task."""
    mocks = {
        "list_bronze_files": MagicMock(return_value=[]),
        "write_bronze_file": MagicMock(
            side_effect=lambda source, name, data, run_date=None: (
                f"{source}/files/dt={run_date}/{name}"
            )
        ),
        "write_bronze": MagicMock(),
        "write_bronze_table": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(lake, name, mock)
    return mocks


def _fake_download(files: dict[str, bytes]) -> Any:
    """A registry-shaped downloader honoring skip/on_file."""

    def download(
        destination: Path,
        reference_month: str,
        skip: set[str] | None = None,
        on_file: Any = None,
        **params: Any,
    ) -> list[Path]:
        destination.mkdir(parents=True, exist_ok=True)
        downloaded: list[Path] = []
        for name, data in files.items():
            if skip and name in skip:
                continue
            path = destination / name
            path.write_bytes(data)
            downloaded.append(path)
            if on_file is not None:
                on_file(path)
        return downloaded

    return download


class TestTaskDownloadSource:
    """Per-file upload and resume semantics of task_download_source."""

    def test_fresh_download_uploads_each_file_immediately(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mocked_lake: dict[str, MagicMock],
    ) -> None:
        """Each downloaded file goes to the bronze layer right away."""
        from capiba.pipeline.registry import SOURCE_REGISTRY, SourceDef
        from capiba.pipeline.tasks import task_download_source

        monkeypatch.setitem(
            SOURCE_REGISTRY,
            "federal_revenue",
            SourceDef(
                download=_fake_download(
                    {"Empresas0.zip": b"PK-empresas", "Cnaes.zip": b"PK-cnaes"}
                )
            ),
        )
        ti = MagicMock()

        payload = task_download_source(
            "federal_revenue", str(_spec_file(tmp_path)), ti=ti, ds="2026-08-02"
        )

        assert payload["reference_month"] == "2026-07"
        by_name = {f["file"]: f for f in payload["files"]}
        assert set(by_name) == {"Empresas0.zip", "Cnaes.zip"}
        assert by_name["Cnaes.zip"]["sha256"] == hashlib.sha256(b"PK-cnaes").hexdigest()
        assert by_name["Cnaes.zip"]["bytes"] == len(b"PK-cnaes")
        assert (
            by_name["Cnaes.zip"]["lake_key"]
            == "federal_revenue/files/dt=2026-08-02/Cnaes.zip"
        )
        assert mocked_lake["write_bronze_file"].call_count == 2
        ti.xcom_push.assert_called_once_with(
            key="manifest_federal_revenue", value=payload
        )

    def test_retry_skips_files_already_in_bronze(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mocked_lake: dict[str, MagicMock],
    ) -> None:
        """A retry downloads only the missing files and keeps the full manifest."""
        from capiba.pipeline.registry import SOURCE_REGISTRY, SourceDef
        from capiba.pipeline.tasks import task_download_source

        mocked_lake["list_bronze_files"].return_value = [
            "federal_revenue/files/dt=2026-08-02/Empresas0.zip"
        ]
        download = MagicMock(
            side_effect=_fake_download({"Empresas0.zip": b"PK-e", "Cnaes.zip": b"PK-c"})
        )
        monkeypatch.setitem(
            SOURCE_REGISTRY, "federal_revenue", SourceDef(download=download)
        )
        ti = MagicMock()

        payload = task_download_source(
            "federal_revenue", str(_spec_file(tmp_path)), ti=ti, ds="2026-08-02"
        )

        # The downloader received the skip set and only fetched Cnaes.zip.
        assert download.call_args.kwargs["skip"] == {"Empresas0.zip"}
        by_name = {f["file"]: f for f in payload["files"]}
        assert set(by_name) == {"Empresas0.zip", "Cnaes.zip"}
        # The bronze-kept file re-enters the manifest without re-download.
        assert by_name["Empresas0.zip"]["sha256"] is None
        assert (
            by_name["Empresas0.zip"]["lake_key"]
            == "federal_revenue/files/dt=2026-08-02/Empresas0.zip"
        )
        assert by_name["Cnaes.zip"]["sha256"] is not None
        # Only the newly downloaded file was uploaded.
        assert mocked_lake["write_bronze_file"].call_count == 1

    def test_all_files_already_in_bronze_is_a_noop_download(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mocked_lake: dict[str, MagicMock],
    ) -> None:
        """A fully checkpointed dump does not fail on the empty download."""
        from capiba.pipeline.registry import SOURCE_REGISTRY, SourceDef
        from capiba.pipeline.tasks import task_download_source

        mocked_lake["list_bronze_files"].return_value = [
            "federal_revenue/files/dt=2026-08-02/Empresas0.zip",
            "federal_revenue/files/dt=2026-08-02/Cnaes.zip",
        ]
        monkeypatch.setitem(
            SOURCE_REGISTRY, "federal_revenue", SourceDef(download=_fake_download({}))
        )
        ti = MagicMock()

        payload = task_download_source(
            "federal_revenue", str(_spec_file(tmp_path)), ti=ti, ds="2026-08-02"
        )

        assert {f["file"] for f in payload["files"]} == {"Empresas0.zip", "Cnaes.zip"}
        mocked_lake["write_bronze_file"].assert_not_called()
