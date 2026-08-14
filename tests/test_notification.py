"""Tests for the notification module.

Responsibility: Validate the alert dispatcher.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import aiohttp

from capiba.notification.dispatcher import (
    NotificationAlert,
    NotificationChannel,
    NotificationDispatcher,
    Priority,
)


def _make_alert(**overrides: Any) -> NotificationAlert:
    """Build a default alert, overridable per test."""
    base: dict[str, Any] = {
        "title": "Alert test",
        "message": "Test message",
        "priority": Priority.HIGH,
        "channel": NotificationChannel.EMAIL,
        "recipients": ["test@example.org"],
        "metadata": {"dataset": "contracts", "score": 0.3},
    }
    base.update(overrides)
    return NotificationAlert(**base)


class _FakeResponse:
    """Minimal async context manager mimicking an aiohttp response."""

    def __init__(self, status: int) -> None:
        self.status = status

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False


class _FakeSession:
    """Minimal async context manager mimicking an aiohttp ClientSession."""

    def __init__(self, status: int = 200) -> None:
        self._status = status
        self.posts: list[dict[str, Any]] = []

    async def __aenter__(self) -> _FakeSession:
        return self

    async def __aexit__(self, *args: object) -> bool:
        return False

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        """Record the POST and return a fake response."""
        self.posts.append({"url": url, **kwargs})
        return _FakeResponse(self._status)


class TestDispatcher:
    """Tests for notification dispatch."""

    def test_create_alert(self) -> None:
        """Must create an alert with metadata."""
        alert = NotificationAlert(
            title="Alert test",
            message="Test message",
            priority=Priority.HIGH,
            channel=NotificationChannel.EMAIL,
            recipients=["test@example.org"],
            metadata={"dataset": "contracts", "score": 0.3},
        )

        assert alert.title == "Alert test"
        assert alert.priority == Priority.HIGH
        assert alert.recipients == ["test@example.org"]
        assert alert.metadata["dataset"] == "contracts"

    def test_alert_defaults_metadata_to_empty_dict(self) -> None:
        """Metadata must default to an empty dict."""
        alert = _make_alert(metadata=None)
        assert alert.metadata == {}

    def test_dispatcher_templates_loaded(self) -> None:
        """Dispatcher must load templates on init."""
        dispatcher = NotificationDispatcher()
        assert "quality_alert" in dispatcher._templates
        assert "detection_alert" in dispatcher._templates

    async def test_dispatch_unsupported_channel(self) -> None:
        """Must return False for an unsupported channel."""
        dispatcher = NotificationDispatcher()

        alert = NotificationAlert(
            title="Test",
            message="Test",
            priority=Priority.LOW,
            channel=NotificationChannel.SMS,
            recipients=["+5511999999999"],
        )

        result = await dispatcher.dispatch(alert)
        assert result is False

    async def test_dispatch_email_success(self) -> None:
        """Must send the e-mail and return True."""
        dispatcher = NotificationDispatcher()
        alert = _make_alert(
            title="Quality alert: contracts",
            metadata={
                "dataset": "contracts",
                "timestamp": "2026-08-17T00:00:00",
                "score": 0.3,
                "alerts": ["nulls above threshold"],
            },
        )

        with patch(
            "capiba.notification.dispatcher.aiosmtplib.send", new=AsyncMock()
        ) as mock_send:
            result = await dispatcher.dispatch(alert)

        assert result is True
        mock_send.assert_awaited_once()
        message = mock_send.await_args.args[0]
        assert "From: " in message
        assert "To: test@example.org" in message
        assert f"Subject: [{Priority.HIGH.upper()}] Quality alert: contracts" in message
        # Quality template must have been rendered into the body.
        assert "contracts" in message
        assert "nulls above threshold" in message

    async def test_dispatch_email_uses_detection_template(self) -> None:
        """Alerts carrying signals must use the detection template."""
        dispatcher = NotificationDispatcher()
        alert = _make_alert(
            title="Fraud signal detected",
            metadata={
                "entity": "Fornecedora Exemplo Ltda",
                "risk_index": 0.91,
                "signals": [],
            },
        )

        with patch(
            "capiba.notification.dispatcher.aiosmtplib.send", new=AsyncMock()
        ) as mock_send:
            result = await dispatcher.dispatch(alert)

        assert result is True
        message = mock_send.await_args.args[0]
        assert "[DETECTION] Fraud signal detected" in message
        assert "Fornecedora Exemplo Ltda" in message

    async def test_dispatch_email_report_without_template_vars(self) -> None:
        """Report alerts (scheduler) must render without domain metadata.

        Regression: the scheduler's report alerts carry only period/dates
        in metadata; the render context must supply title/message/priority
        and the templates must tolerate the missing optional variables.
        """
        dispatcher = NotificationDispatcher()
        alert = _make_alert(
            title="Weekly Report — 2026-08-03 to 2026-08-10",
            message="Weekly consolidated quality and detection report.",
            priority=Priority.MEDIUM,
            metadata={
                "period": "weekly",
                "start_date": "2026-08-03T00:00:00",
                "end_date": "2026-08-10T00:00:00",
                "timestamp": "2026-08-10T00:00:00",
            },
        )

        with patch(
            "capiba.notification.dispatcher.aiosmtplib.send", new=AsyncMock()
        ) as mock_send:
            result = await dispatcher.dispatch(alert)

        assert result is True
        message = mock_send.await_args.args[0]
        assert "[MEDIUM] Weekly Report" in message
        assert "Weekly consolidated quality and detection report." in message
        assert "2026-08-10T00:00:00" in message

    async def test_dispatch_email_without_metadata(self) -> None:
        """E-mail must be sent even when metadata is empty."""
        dispatcher = NotificationDispatcher()
        alert = _make_alert(metadata=None)

        with patch(
            "capiba.notification.dispatcher.aiosmtplib.send", new=AsyncMock()
        ) as mock_send:
            result = await dispatcher.dispatch(alert)

        assert result is True
        mock_send.assert_awaited_once()

    async def test_dispatch_email_failure(self) -> None:
        """Must return False when the SMTP send fails."""
        dispatcher = NotificationDispatcher()
        alert = _make_alert(title="Quality alert: contracts")

        with patch(
            "capiba.notification.dispatcher.aiosmtplib.send",
            new=AsyncMock(side_effect=OSError("smtp down")),
        ):
            result = await dispatcher.dispatch(alert)

        assert result is False

    async def test_dispatch_webhook_success(self, monkeypatch) -> None:
        """Must POST the payload to the webhook and return True."""
        dispatcher = NotificationDispatcher()
        session = _FakeSession(status=200)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda: session)

        alert = _make_alert(
            channel=NotificationChannel.WEBHOOK,
            recipients=[],
            metadata={
                "webhook_url": "https://hooks.example.org/capiba",
                "timestamp": "2026-08-17T00:00:00",
            },
        )

        result = await dispatcher.dispatch(alert)

        assert result is True
        assert len(session.posts) == 1
        post = session.posts[0]
        assert post["url"] == "https://hooks.example.org/capiba"
        assert post["json"]["title"] == alert.title
        assert post["json"]["priority"] == Priority.HIGH.value
        assert post["json"]["timestamp"] == "2026-08-17T00:00:00"

    async def test_dispatch_webhook_missing_url(self, monkeypatch) -> None:
        """Must return False when the webhook URL is not configured."""
        dispatcher = NotificationDispatcher()
        session = _FakeSession()
        monkeypatch.setattr(aiohttp, "ClientSession", lambda: session)

        alert = _make_alert(channel=NotificationChannel.WEBHOOK, metadata={})

        result = await dispatcher.dispatch(alert)

        assert result is False
        assert session.posts == []

    async def test_dispatch_webhook_non_200(self, monkeypatch) -> None:
        """Must return False when the webhook returns an error status."""
        dispatcher = NotificationDispatcher()
        session = _FakeSession(status=500)
        monkeypatch.setattr(aiohttp, "ClientSession", lambda: session)

        alert = _make_alert(
            channel=NotificationChannel.WEBHOOK,
            metadata={"webhook_url": "https://hooks.example.org/capiba"},
        )

        result = await dispatcher.dispatch(alert)
        assert result is False

    async def test_dispatch_webhook_failure(self, monkeypatch) -> None:
        """Must return False when the webhook request raises."""

        def _broken_session() -> _FakeSession:
            raise aiohttp.ClientConnectionError("connection refused")

        dispatcher = NotificationDispatcher()
        monkeypatch.setattr(aiohttp, "ClientSession", _broken_session)

        alert = _make_alert(
            channel=NotificationChannel.WEBHOOK,
            metadata={"webhook_url": "https://hooks.example.org/capiba"},
        )

        result = await dispatcher.dispatch(alert)
        assert result is False
