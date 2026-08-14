"""Scheduler for periodic notifications.

Chunk: scheduler
Responsibility: Send periodic quality and detection
reports to configured stakeholders.

Dependencies: apscheduler, asyncio
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.asyncio import (  # pyright: ignore[reportMissingTypeStubs]
    AsyncIOScheduler,
)
from apscheduler.triggers.cron import (  # pyright: ignore[reportMissingTypeStubs]
    CronTrigger,
)

from capiba.notification.dispatcher import (
    NotificationAlert,
    NotificationChannel,
    NotificationDispatcher,
    Priority,
)
from capiba.quality.monitor import QualityMonitor

logger = logging.getLogger(__name__)


class NotificationScheduler:
    """Periodic report scheduler.

    Sends daily, weekly and monthly data quality and
    detection reports to a configured list of
    recipients.
    """

    def __init__(self) -> None:
        self.dispatcher = NotificationDispatcher()
        self.scheduler = AsyncIOScheduler()
        self.monitor = QualityMonitor()

    def configure_reports(
        self,
        recipients: list[str],
        daily_frequency: str = "0 8 * * *",  # 8 AM
        weekly_frequency: str = "0 9 * * 1",  # Monday 9 AM
        monthly_frequency: str = "0 10 1 * *",  # 1st day, 10 AM
    ) -> None:
        """Configure periodic report jobs.

        Args:
            recipients: List of recipient e-mails.
            daily_frequency: Cron for the daily report.
            weekly_frequency: Cron for the weekly report.
            monthly_frequency: Cron for the monthly report.
        """
        self.scheduler.add_job(
            self._daily_report,
            CronTrigger.from_crontab(daily_frequency),
            args=[recipients],
            id="daily_report",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._weekly_report,
            CronTrigger.from_crontab(weekly_frequency),
            args=[recipients],
            id="weekly_report",
            replace_existing=True,
        )

        self.scheduler.add_job(
            self._monthly_report,
            CronTrigger.from_crontab(monthly_frequency),
            args=[recipients],
            id="monthly_report",
            replace_existing=True,
        )

        logger.info("Reports configured: %d recipients", len(recipients))

    async def _daily_report(self, recipients: list[str]) -> None:
        """Generate and send the daily quality report."""
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)

        alert = NotificationAlert(
            title=f"Daily Quality Report — {yesterday.strftime('%Y-%m-%d')}",
            message="Daily summary of data quality metrics.",
            priority=Priority.LOW,
            channel=NotificationChannel.EMAIL,
            recipients=recipients,
            metadata={
                "period": "daily",
                "start_date": yesterday.isoformat(),
                "end_date": now.isoformat(),
                "timestamp": now.isoformat(),
            },
        )

        await self.dispatcher.dispatch(alert)

    async def _weekly_report(self, recipients: list[str]) -> None:
        """Generate and send the consolidated weekly report."""
        now = datetime.now(UTC)
        last_week = now - timedelta(days=7)

        alert = NotificationAlert(
            title=f"Weekly Report — {last_week.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            message="Weekly consolidated quality and detection report.",
            priority=Priority.MEDIUM,
            channel=NotificationChannel.EMAIL,
            recipients=recipients,
            metadata={
                "period": "weekly",
                "start_date": last_week.isoformat(),
                "end_date": now.isoformat(),
                "timestamp": now.isoformat(),
            },
        )

        await self.dispatcher.dispatch(alert)

    async def _monthly_report(self, recipients: list[str]) -> None:
        """Generate and send the executive monthly report."""
        now = datetime.now(UTC)
        last_month = now.replace(day=1) - timedelta(days=1)

        alert = NotificationAlert(
            title=f"Executive Monthly Report — {last_month.strftime('%B/%Y')}",
            message="Monthly executive report with trends and recommendations.",
            priority=Priority.MEDIUM,
            channel=NotificationChannel.EMAIL,
            recipients=recipients,
            metadata={
                "period": "monthly",
                "start_date": last_month.replace(day=1).isoformat(),
                "end_date": last_month.isoformat(),
                "timestamp": now.isoformat(),
            },
        )

        await self.dispatcher.dispatch(alert)

    def start(self) -> None:
        """Start the notification scheduler."""
        self.scheduler.start()
        logger.info("Notification scheduler started")

    def stop(self) -> None:
        """Stop the scheduler."""
        self.scheduler.shutdown()
        logger.info("Notification scheduler stopped")
