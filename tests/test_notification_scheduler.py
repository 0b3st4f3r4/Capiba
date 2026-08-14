"""Tests for the notification scheduler.

Responsibility: Validate periodic report jobs (daily,
weekly and monthly) without a running apscheduler.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from capiba.notification.dispatcher import (
    NotificationChannel,
    Priority,
)
from capiba.notification.scheduler import NotificationScheduler


@pytest.fixture
def scheduler() -> NotificationScheduler:
    """Scheduler with apscheduler and dispatcher mocked out."""
    instance = NotificationScheduler()
    instance.scheduler = MagicMock()
    instance.dispatcher = AsyncMock()
    return instance


class TestNotificationScheduler:
    """Tests for the periodic report scheduler."""

    def test_init_creates_components(self) -> None:
        """Init must wire dispatcher, apscheduler and quality monitor."""
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
