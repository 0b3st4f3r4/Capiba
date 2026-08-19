"""Scheduler for periodic notifications.

Chunk: scheduler
Responsibility: Send periodic quality and detection
reports to configured stakeholders.

Dependencies: apscheduler, asyncio
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from apscheduler.schedulers.asyncio import (  # pyright: ignore[reportMissingTypeStubs]
    AsyncIOScheduler,
)
from apscheduler.triggers.cron import (  # pyright: ignore[reportMissingTypeStubs]
    CronTrigger,
)

from capiba.config import NOTIFICATION_RECIPIENTS
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

    def _quality_summary(self, since: datetime) -> dict[str, Any]:
        """Aggregates the batch metrics recorded by the monitor since ``since``.

        Reads every dataset tracked by the ``QualityMonitor`` (validation
        batches recorded by the pipelines) and sums the period totals.
        Empty when Redis is unavailable or nothing was recorded.
        """
        summary: dict[str, Any] = {}
        for dataset in self.monitor.list_datasets():
            entries = self.monitor.get_metrics(dataset, since=since)
            if not entries:
                continue
            failures: dict[str, int] = {}
            for entry in entries:
                for severity, count in (
                    entry.get("quality_rule_failures") or {}
                ).items():
                    failures[severity] = failures.get(severity, 0) + int(count)
            summary[dataset] = {
                "batches": len(entries),
                "total_records": sum(int(e.get("total", 0)) for e in entries),
                "duplicates": sum(int(e.get("duplicates", 0)) for e in entries),
                "normalization_errors": sum(
                    int(e.get("normalization_errors", 0)) for e in entries
                ),
                "quality_rule_failures": failures,
            }
        return summary

    @staticmethod
    def _report_message(base: str, metrics: dict[str, Any]) -> str:
        """Builds the report body from the aggregated quality metrics."""
        if not metrics:
            return f"{base} No quality data recorded in the period."
        parts = [
            f"{dataset}: {m['batches']} batches, {m['total_records']} records, "
            f"{m['duplicates']} duplicates, {m['normalization_errors']} "
            f"normalization errors"
            for dataset, m in metrics.items()
        ]
        return f"{base} " + "; ".join(parts) + "."

    async def _daily_report(self, recipients: list[str]) -> None:
        """Generate and send the daily quality report."""
        now = datetime.now(UTC)
        yesterday = now - timedelta(days=1)
        metrics = self._quality_summary(yesterday)

        alert = NotificationAlert(
            title=f"Daily Quality Report — {yesterday.strftime('%Y-%m-%d')}",
            message=self._report_message(
                "Daily summary of data quality metrics.", metrics
            ),
            priority=Priority.LOW,
            channel=NotificationChannel.EMAIL,
            recipients=recipients,
            metadata={
                "period": "daily",
                "start_date": yesterday.isoformat(),
                "end_date": now.isoformat(),
                "timestamp": now.isoformat(),
                "quality_metrics": metrics,
            },
        )

        await self.dispatcher.dispatch(alert)

    async def _weekly_report(self, recipients: list[str]) -> None:
        """Generate and send the consolidated weekly report."""
        now = datetime.now(UTC)
        last_week = now - timedelta(days=7)
        metrics = self._quality_summary(last_week)

        alert = NotificationAlert(
            title=f"Weekly Report — {last_week.strftime('%Y-%m-%d')} to {now.strftime('%Y-%m-%d')}",
            message=self._report_message(
                "Weekly consolidated quality and detection report.", metrics
            ),
            priority=Priority.MEDIUM,
            channel=NotificationChannel.EMAIL,
            recipients=recipients,
            metadata={
                "period": "weekly",
                "start_date": last_week.isoformat(),
                "end_date": now.isoformat(),
                "timestamp": now.isoformat(),
                "quality_metrics": metrics,
            },
        )

        await self.dispatcher.dispatch(alert)

    async def _monthly_report(self, recipients: list[str]) -> None:
        """Generate and send the executive monthly report."""
        now = datetime.now(UTC)
        last_month = now.replace(day=1) - timedelta(days=1)
        metrics = self._quality_summary(last_month.replace(day=1))

        alert = NotificationAlert(
            title=f"Executive Monthly Report — {last_month.strftime('%B/%Y')}",
            message=self._report_message(
                "Monthly executive report with trends and recommendations.",
                metrics,
            ),
            priority=Priority.MEDIUM,
            channel=NotificationChannel.EMAIL,
            recipients=recipients,
            metadata={
                "period": "monthly",
                "start_date": last_month.replace(day=1).isoformat(),
                "end_date": last_month.isoformat(),
                "timestamp": now.isoformat(),
                "quality_metrics": metrics,
            },
        )

        await self.dispatcher.dispatch(alert)

    def start(self) -> None:
        """Start the notification scheduler."""
        self.scheduler.start()
        logger.info("Notification scheduler started")

    def stop(self) -> None:
        """Stop the scheduler (no-op when it was never started)."""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("Notification scheduler stopped")


def start_notification_scheduler(
    recipients: list[str] | None = None,
) -> NotificationScheduler | None:
    """Starts the periodic report scheduler when recipients are configured.

    Isolated from the API lifespan so it can be mocked in tests. No-op
    (returns None) when ``NOTIFICATION_RECIPIENTS`` is empty.

    Args:
        recipients: Recipient e-mails; defaults to the configured
            ``NOTIFICATION_RECIPIENTS``.

    Returns:
        The running scheduler, or None when disabled.
    """
    recipients = NOTIFICATION_RECIPIENTS if recipients is None else recipients
    if not recipients:
        logger.debug("Notification scheduler disabled (no recipients)")
        return None

    scheduler = NotificationScheduler()
    scheduler.configure_reports(recipients)
    scheduler.start()
    logger.info("Notification scheduler active for %d recipients", len(recipients))
    return scheduler
