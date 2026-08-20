"""Tests for the Airflow wrappers of the documents_collect formula (O7).

Responsibility: Validate the granular tasks ``download_<source>_texts``
(skip-existing resume, XCom contract) and ``validate`` (report shape,
declared ruleset) without a cluster — XCom via MagicMock, lake mocked.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from capiba.pipeline import lake, tasks
from capiba.pipeline.document_tasks import (
    task_download_document_texts,
    task_validate_documents,
)


def _spec_file(tmp_path: Path) -> Path:
    """Writes a minimal documents_collect spec for the task tests."""
    spec_path = tmp_path / "spec.yaml"
    spec_path.write_text(
        """\
name: documents_task
window: previous_day
sources:
  - name: querido_diario
    params:
      territory_id: "2611606"
formula: documents_collect
validate:
  ruleset: gazette_rules
destinations: [lake_bronze, gold_report]
""",
        encoding="utf-8",
    )
    return spec_path


def _record(n: int) -> dict[str, Any]:
    return {
        "territory_id": "2611606",
        "date": f"2026-08-{n:02d}",
        "url": f"https://data.queridodiario.ok.org.br/2611606/2026-08-{n:02d}/abc{n}.pdf",
        "txt_url": f"https://data.queridodiario.ok.org.br/2611606/2026-08-{n:02d}/abc{n}.txt",
    }


class TestDownloadDocumentTextsTask:
    """Tests for the download_<source>_texts task (skip-existing resume)."""

    @pytest.fixture
    def mocked_lake(self, monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
        """Replaces the lake file functions used by persist_document_texts."""
        mocks = {
            "list_bronze_files": MagicMock(return_value=[]),
            "write_bronze_file": MagicMock(return_value="key"),
        }
        for name, mock in mocks.items():
            monkeypatch.setattr(lake, name, mock)
        return mocks

    def test_downloads_and_enriches_the_records(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mocked_lake: dict[str, MagicMock],
    ) -> None:
        """Each record's text is uploaded and the record gains text_bronze_file."""
        monkeypatch.setattr(
            tasks, "download_gazette_text", MagicMock(return_value=b"plain text")
        )
        record = _record(17)
        ti = MagicMock()
        ti.xcom_pull.return_value = [record]

        summary = task_download_document_texts(
            "querido_diario", str(_spec_file(tmp_path)), ti=ti, ds="2026-08-18"
        )

        assert summary["texts_downloaded"] == 1
        assert summary["errors"] == 0
        assert record["text_bronze_file"] == tasks.text_file_name(record)
        mocked_lake["write_bronze_file"].assert_called_once()
        assert ti.xcom_push.call_count == 2

    def test_skips_texts_already_in_bronze(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        mocked_lake: dict[str, MagicMock],
    ) -> None:
        """A retry skips the text already persisted for the run date."""
        download = MagicMock()
        monkeypatch.setattr(tasks, "download_gazette_text", download)
        record = _record(17)
        mocked_lake["list_bronze_files"].return_value = [
            f"querido_diario/files/dt=2026-08-18/{tasks.text_file_name(record)}"
        ]
        ti = MagicMock()
        ti.xcom_pull.return_value = [record]

        summary = task_download_document_texts(
            "querido_diario", str(_spec_file(tmp_path)), ti=ti, ds="2026-08-18"
        )

        assert summary["texts_skipped"] == 1
        assert summary["texts_downloaded"] == 0
        download.assert_not_called()
        mocked_lake["write_bronze_file"].assert_not_called()


class TestValidateDocumentsTask:
    """Tests for the documents_collect validate task."""

    def test_report_shape_and_ruleset(self, tmp_path: Path) -> None:
        """The report carries duplicates, download errors and quality rules."""
        record = _record(17)
        texts_summary = {
            "source": "querido_diario",
            "texts_downloaded": 1,
            "texts_skipped": 0,
            "errors": 0,
        }
        ti = MagicMock()
        ti.xcom_pull.side_effect = lambda task_ids=None, key=None: (
            [record] if key == "raw_querido_diario" else texts_summary
        )

        report = task_validate_documents(
            str(_spec_file(tmp_path)), ti=ti, ds="2026-08-18"
        )

        assert report["total"] == 1
        assert report["duplicates"] == 0
        assert report["valid"] is True
        assert {r["rule"] for r in report["quality_rules"]} == {
            "valid_territory",
            "date_present",
            "file_url_present",
            "text_url_present",
        }
        ti.xcom_push.assert_called_once_with(key="validation_report", value=report)

    def test_duplicate_urls_invalidate_the_report(self, tmp_path: Path) -> None:
        """Two gazettes sharing the file url are flagged as duplicates."""
        ti = MagicMock()
        ti.xcom_pull.side_effect = lambda task_ids=None, key=None: (
            [_record(17), _record(17)] if key == "raw_querido_diario" else {"errors": 2}
        )

        report = task_validate_documents(
            str(_spec_file(tmp_path)), ti=ti, ds="2026-08-18"
        )

        assert report["valid"] is False
        assert report["duplicates"] == 1
        assert report["normalization_errors"] == 2
