"""Tests for the dbt runner.

Responsibility: Validate the in-process dbt execution wrapper
(working directory, HOME fallback, success/failure handling).
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from capiba.config import DBT_PROJECT_DIR
from capiba.pipeline.dbt_runner import run_dbt


@pytest.fixture(autouse=True)
def restore_cwd() -> Iterator[None]:
    """Restores the process working directory changed by run_dbt."""
    cwd = Path.cwd()
    yield
    os.chdir(cwd)


class TestRunDbt:
    """Tests for run_dbt."""

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_success(self, mock_runner_cls: MagicMock) -> None:
        """A successful dbt invocation must run from the target dir."""
        result = MagicMock(success=True)
        mock_runner_cls.return_value.invoke.return_value = result

        run_dbt("test")

        invoke = mock_runner_cls.return_value.invoke
        invoke.assert_called_once()
        args = invoke.call_args.args[0]
        assert args == [
            "test",
            "--project-dir",
            DBT_PROJECT_DIR,
            "--profiles-dir",
            DBT_PROJECT_DIR,
        ]
        assert Path.cwd() == Path(DBT_PROJECT_DIR) / "target"

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_default_command(self, mock_runner_cls: MagicMock) -> None:
        """The default dbt command must be ``run``."""
        result = MagicMock(success=True)
        mock_runner_cls.return_value.invoke.return_value = result

        run_dbt()

        args = mock_runner_cls.return_value.invoke.call_args.args[0]
        assert args[0] == "run"

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_with_select(self, mock_runner_cls: MagicMock) -> None:
        """A model selection must be forwarded to ``--select``."""
        result = MagicMock(success=True)
        mock_runner_cls.return_value.invoke.return_value = result

        run_dbt("run", select=["pod_usage_hourly", "platform_cost_daily"])

        args = mock_runner_cls.return_value.invoke.call_args.args[0]
        assert args[-3:] == ["--select", "pod_usage_hourly", "platform_cost_daily"]

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_with_exclude(self, mock_runner_cls: MagicMock) -> None:
        """A model exclusion must be forwarded to ``--exclude``."""
        result = MagicMock(success=True)
        mock_runner_cls.return_value.invoke.return_value = result

        run_dbt("run", exclude=["political_connections"])

        args = mock_runner_cls.return_value.invoke.call_args.args[0]
        assert args[-2:] == ["--exclude", "political_connections"]

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_failure(self, mock_runner_cls: MagicMock) -> None:
        """A non-success dbt result must raise RuntimeError."""
        result = MagicMock(success=False)
        result.exception = None
        mock_runner_cls.return_value.invoke.return_value = result

        with pytest.raises(RuntimeError, match="dbt run failed: see logs"):
            run_dbt()

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_failure_with_exception(self, mock_runner_cls: MagicMock) -> None:
        """The dbt exception must be surfaced in the error message."""
        result = MagicMock(success=False)
        result.exception = ValueError("boom")
        mock_runner_cls.return_value.invoke.return_value = result

        with pytest.raises(RuntimeError, match="dbt build failed: boom"):
            run_dbt("build")

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_sets_home_when_missing(
        self, mock_runner_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An empty HOME must fall back to /tmp for the DuckDB cache."""
        monkeypatch.delenv("HOME", raising=False)
        result = MagicMock(success=True)
        mock_runner_cls.return_value.invoke.return_value = result

        run_dbt()

        assert os.environ["HOME"] == "/tmp"

    @patch("dbt.cli.main.dbtRunner")
    def test_run_dbt_keeps_home(
        self, mock_runner_cls: MagicMock, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An existing HOME must be preserved."""
        monkeypatch.setenv("HOME", "/home/tester")
        result = MagicMock(success=True)
        mock_runner_cls.return_value.invoke.return_value = result

        run_dbt()

        assert os.environ["HOME"] == "/home/tester"
