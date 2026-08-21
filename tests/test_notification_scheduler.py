"""Tests for the notification scheduler.

Responsibility: Validate periodic report jobs (daily,
weekly and monthly) without a running apscheduler.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capiba.notification.dispatcher import (
    NotificationChannel,
    Priority,
)
from capiba.notification.scheduler import (
    NotificationScheduler,
    start_notification_scheduler,
)


@pytest.fixture
def scheduler() -> NotificationScheduler:
    """Scheduler with apscheduler, dispatcher and monitor mocked out."""
    with patch("capiba.notification.scheduler.QualityMonitor"):
        instance = NotificationScheduler()
    instance.scheduler = MagicMock()
    instance.dispatcher = AsyncMock()
    instance.monitor = MagicMock()
    instance.monitor.list_datasets.return_value = []
    return instance


class TestNotificationScheduler:
    """Tests for the periodic report scheduler."""

    def test_init_creates_components(self) -> None:
        """Init must wire dispatcher, apscheduler and quality monitor."""
        with patch("capiba.notification.scheduler.QualityMonitor"):
            instance = NotificationScheduler()
        assert instance.dispatcher is not None
        assert instance.scheduler is not None
        assert instance.monitor is not None

    def test_configure_reports_adds_three_jobs(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Must register daily, weekly and monthly jobs."""
        recipients = ["team@example.org"]
        scheduler.configure_reports(recipients)

        assert scheduler.scheduler.add_job.call_count == 3
        job_ids = {
            call.kwargs["id"] for call in scheduler.scheduler.add_job.call_args_list
        }
        assert job_ids == {"daily_report", "weekly_report", "monthly_report"}
        for call in scheduler.scheduler.add_job.call_args_list:
            assert call.kwargs["args"] == [recipients]
            assert call.kwargs["replace_existing"] is True

    def test_configure_reports_uses_custom_crons(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Custom cron expressions must be accepted."""
        scheduler.configure_reports(
            ["team@example.org"],
            daily_frequency="0 7 * * *",
            weekly_frequency="0 8 * * 2",
            monthly_frequency="0 9 2 * *",
        )
        assert scheduler.scheduler.add_job.call_count == 3

    async def test_daily_report_dispatches_email(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Daily report must dispatch a low-priority e-mail."""
        recipients = ["team@example.org"]
        await scheduler._daily_report(recipients)

        scheduler.dispatcher.dispatch.assert_awaited_once()
        alert = scheduler.dispatcher.dispatch.await_args.args[0]
        yesterday = (datetime.now(UTC)).date().isoformat()
        assert alert.channel == NotificationChannel.EMAIL
        assert alert.priority == Priority.LOW
        assert alert.recipients == recipients
        assert alert.metadata["period"] == "daily"
        assert alert.title.startswith("Daily Quality Report — ")
        assert yesterday >= alert.metadata["start_date"][:10]

    async def test_weekly_report_dispatches_email(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Weekly report must dispatch a medium-priority e-mail."""
        recipients = ["team@example.org"]
        await scheduler._weekly_report(recipients)

        scheduler.dispatcher.dispatch.assert_awaited_once()
        alert = scheduler.dispatcher.dispatch.await_args.args[0]
        assert alert.channel == NotificationChannel.EMAIL
        assert alert.priority == Priority.MEDIUM
        assert alert.recipients == recipients
        assert alert.metadata["period"] == "weekly"
        assert alert.title.startswith("Weekly Report — ")

    async def test_monthly_report_dispatches_email(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Monthly report must dispatch a medium-priority e-mail."""
        recipients = ["team@example.org"]
        await scheduler._monthly_report(recipients)

        scheduler.dispatcher.dispatch.assert_awaited_once()
        alert = scheduler.dispatcher.dispatch.await_args.args[0]
        assert alert.channel == NotificationChannel.EMAIL
        assert alert.priority == Priority.MEDIUM
        assert alert.recipients == recipients
        assert alert.metadata["period"] == "monthly"
        assert alert.title.startswith("Executive Monthly Report — ")
        assert alert.metadata["start_date"][:10].endswith("-01")

    def test_start_starts_apscheduler(self, scheduler: NotificationScheduler) -> None:
        """Start must delegate to the apscheduler instance."""
        scheduler.start()
        scheduler.scheduler.start.assert_called_once()

    def test_stop_shuts_down_apscheduler(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Stop must shut down the apscheduler instance."""
        scheduler.stop()
        scheduler.scheduler.shutdown.assert_called_once()

    def test_stop_noop_when_not_started(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Stop must not shut down a scheduler that never started."""
        scheduler.scheduler.running = False
        scheduler.stop()
        scheduler.scheduler.shutdown.assert_not_called()


class TestReportMetrics:
    """Reports must include the real metrics read from the QualityMonitor."""

    async def test_daily_report_without_metrics_says_no_data(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Without recorded batches the report states there is no data."""
        await scheduler._daily_report(["team@example.org"])

        alert = scheduler.dispatcher.dispatch.await_args.args[0]
        assert alert.metadata["quality_metrics"] == {}
        assert "No quality data recorded in the period" in alert.message

    async def test_daily_report_aggregates_monitor_metrics(
        self, scheduler: NotificationScheduler
    ) -> None:
        """Recorded batches must be summed into the report metadata."""
        scheduler.monitor.list_datasets.return_value = ["pipeline:daily_ingestion"]
        scheduler.monitor.get_metrics.return_value = [
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "total": 10,
                "duplicates": 1,
                "normalization_errors": 2,
                "quality_rule_failures": {"error": 1},
            },
            {
                "timestamp": datetime.now(UTC).isoformat(),
                "total": 5,
                "duplicates": 0,
                "normalization_errors": 0,
                "quality_rule_failures": {"warning": 3},
            },
        ]

        await scheduler._daily_report(["team@example.org"])

        alert = scheduler.dispatcher.dispatch.await_args.args[0]
        metrics = alert.metadata["quality_metrics"]
        assert metrics["pipeline:daily_ingestion"] == {
            "batches": 2,
            "total_records": 15,
            "duplicates": 1,
            "normalization_errors": 2,
            "quality_rule_failures": {"error": 1, "warning": 3},
        }
        assert "pipeline:daily_ingestion" in alert.message
        assert "15 records" in alert.message

    async def test_weekly_report_includes_metrics(
        self, scheduler: NotificationScheduler
    ) -> None:
        """The weekly report must also carry the monitor metrics."""
        scheduler.monitor.list_datasets.return_value = ["pipeline:daily_ingestion"]
        scheduler.monitor.get_metrics.return_value = [
            {"timestamp": datetime.now(UTC).isoformat(), "total": 3}
        ]

        await scheduler._weekly_report(["team@example.org"])

        alert = scheduler.dispatcher.dispatch.await_args.args[0]
        assert alert.metadata["quality_metrics"]["pipeline:daily_ingestion"][
            "total_records"
        ] == 3


class TestStartNotificationScheduler:
    """The API lifespan entrypoint must be a no-op without recipients."""

    def test_no_recipients_returns_none(self) -> None:
        """Empty recipient list must not start anything."""
        assert start_notification_scheduler([]) is None

    def test_starts_scheduler_with_recipients(self) -> None:
        """Configured recipients must configure and start the scheduler."""
        with patch(
            "capiba.notification.scheduler.NotificationScheduler"
        ) as mock_cls:
            instance = mock_cls.return_value
            result = start_notification_scheduler(["team@example.org"])

        assert result is instance
        instance.configure_reports.assert_called_once_with(["team@example.org"])
        instance.start.assert_called_once()
