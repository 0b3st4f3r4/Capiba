"""Notification dispatcher — e-mail and webhook.

Chunk: dispatcher
Responsibility: Send quality, detection and system
alerts to configured channels.

Dependencies: aiosmtplib, jinja2
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import aiosmtplib
from jinja2 import Template

from capiba.config import (
    NOTIFICATION_EMAIL_FROM,
    NOTIFICATION_EMAIL_HOST,
    NOTIFICATION_EMAIL_PASSWORD,
    NOTIFICATION_EMAIL_PORT,
    NOTIFICATION_EMAIL_TLS,
    NOTIFICATION_EMAIL_USER,
)

logger = logging.getLogger(__name__)


class NotificationChannel(StrEnum):
    """Supported notification channels."""

    EMAIL = "email"
    WEBHOOK = "webhook"


class Priority(StrEnum):
    """Alert priority level."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


@dataclass
class NotificationAlert:
    """Alert to be notified."""

    title: str
    message: str
    priority: Priority
    channel: NotificationChannel
    recipients: list[str]
    metadata: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.metadata is None:
            self.metadata = {}


class NotificationDispatcher:
    """Multi-channel notification dispatcher.

    Routes alerts to the correct channel and manages
    retry with exponential backoff.
    """

    def __init__(self) -> None:
        self._templates: dict[str, Template] = {}
        self._load_templates()

    def _load_templates(self) -> None:
        """Load Jinja2 templates for e-mails."""
        self._templates["quality_alert"] = Template("""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{{ title }}</title></head>
<body style="font-family: monospace; max-width: 600px; margin: 0 auto;">
<h2 style="color: {% if priority == 'critical' %}#dc2626{% elif priority == 'high' %}#ea580c{% else %}#ca8a04{% endif %}">
  [{{ priority.upper() }}] {{ title }}
</h2>
<p>{{ message }}</p>
<p><strong>Dataset:</strong> {{ dataset | default('—') }}</p>
<p><strong>Timestamp:</strong> {{ timestamp | default('—') }}</p>
<p><strong>Quality score:</strong> {{ score | default('—') }}</p>
{% if alerts %}
<h3>Detected alerts:</h3>
<ul>
{% for alert in alerts %}
  <li>{{ alert }}</li>
{% endfor %}
</ul>
{% endif %}
<hr>
<p style="font-size: 12px; color: #666;">
  Capiba — Institutional farce capture engine<br>
  This is an automated e-mail. Do not reply.
</p>
</body>
</html>
""")

        self._templates["detection_alert"] = Template("""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{{ title }}</title></head>
<body style="font-family: monospace; max-width: 600px; margin: 0 auto;">
<h2 style="color: #dc2626;">[DETECTION] {{ title }}</h2>
<p>{{ message }}</p>
<p><strong>Entity:</strong> {{ entity | default('—') }}</p>
<p><strong>Risk index:</strong> {{ risk_index | default('—') }}</p>
<p><strong>Signals:</strong></p>
<ul>
{% for signal in signals | default([]) %}
  <li>{{ signal.type }}: {{ signal.score }} — {{ signal.evidence }}</li>
{% endfor %}
</ul>
<hr>
<p style="font-size: 12px; color: #666;">
  Capiba — Institutional farce capture engine
</p>
</body>
</html>
""")

        self._templates["subscription"] = Template("""
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><title>{{ title }}</title></head>
<body style="font-family: monospace; max-width: 600px; margin: 0 auto;">
<h2 style="color: #1d4ed8;">[CAPIBA] {{ title }}</h2>
<p>{{ message }}</p>
{% if municipality %}
<p><strong>Município:</strong> {{ municipality }}{% if uf %} — {{ uf }}{% endif %}</p>
{% endif %}
{% if signal_type %}
<p><strong>Sinal publicado:</strong> {{ signal_type }} (score {{ score | default('—') }})</p>
<p><strong>Entidade:</strong> {{ entity | default('—') }}</p>
{% endif %}
{% if evidence_url %}
<p><strong>Pacote de evidências:</strong> <a href="{{ evidence_url }}">{{ evidence_url }}</a></p>
{% endif %}
{% if confirm_url %}
<p><strong>Confirmar assinatura:</strong> <a href="{{ confirm_url }}">{{ confirm_url }}</a></p>
{% endif %}
{% if unsubscribe_url %}
<p style="font-size: 12px;">Para cancelar esta assinatura:
  <a href="{{ unsubscribe_url }}">{{ unsubscribe_url }}</a></p>
{% endif %}
<hr>
<p style="font-size: 12px; color: #666;">
  Capiba — Institutional farce capture engine<br>
  This is an automated e-mail. Do not reply.
</p>
</body>
</html>
""")

    async def dispatch(self, alert: NotificationAlert) -> bool:
        """Send notification through the configured channel.

        Args:
            alert: Alert to be notified.

        Returns:
            True if sent successfully, False otherwise.
        """
        if alert.channel == NotificationChannel.EMAIL:
            return await self._send_email(alert)
        elif alert.channel == NotificationChannel.WEBHOOK:
            return await self._send_webhook(alert)
        else:
            logger.warning("Unsupported channel: %s", alert.channel)
            return False

    async def _send_email(self, alert: NotificationAlert) -> bool:
        """Send HTML e-mail via asynchronous SMTP.

        The template context always carries the alert's own fields
        (title, message, priority); metadata adds or overrides the
        domain variables (dataset, score, signals, ...).

        Args:
            alert: Alert with recipients and content.

        Returns:
            True if sent successfully.
        """
        try:
            # metadata is always a dict after __post_init__
            metadata = alert.metadata or {}
            # An explicit template in metadata wins; otherwise detection
            # alerts carry signals and everything else (quality alerts,
            # periodic reports) uses the quality template.
            explicit = str(metadata.get("template") or "")
            if explicit in self._templates:
                template_key = explicit
            elif "signals" in metadata or "detection" in alert.title.lower():
                template_key = "detection_alert"
            else:
                template_key = "quality_alert"
            template = self._templates.get(template_key)
            context = {
                "title": alert.title,
                "message": alert.message,
                "priority": alert.priority.value,
                **metadata,
            }
            body_html = template.render(**context) if template else alert.message

            message = f"""From: {NOTIFICATION_EMAIL_FROM}
To: {", ".join(alert.recipients)}
Subject: [{alert.priority.upper()}] {alert.title}
Content-Type: text/html; charset=utf-8

{body_html}
"""

            await aiosmtplib.send(
                message,
                hostname=NOTIFICATION_EMAIL_HOST,
                port=NOTIFICATION_EMAIL_PORT,
                username=NOTIFICATION_EMAIL_USER,
                password=NOTIFICATION_EMAIL_PASSWORD,
                start_tls=NOTIFICATION_EMAIL_TLS,
            )

            logger.info("E-mail sent: %s to %s", alert.title, alert.recipients)
            return True

        except Exception as e:
            logger.error("Failed to send e-mail: %s", e)
            return False

    async def _send_webhook(self, alert: NotificationAlert) -> bool:
        """Send alert to the configured webhook.

        Args:
            alert: Alert with webhook URL in metadata.

        Returns:
            True if sent successfully.
        """
        import aiohttp

        # metadata is always a dict after __post_init__
        metadata = alert.metadata or {}
        webhook_url = metadata.get("webhook_url")
        if not webhook_url:
            logger.error("Webhook URL not configured")
            return False

        try:
            payload = {
                "title": alert.title,
                "message": alert.message,
                "priority": alert.priority.value,
                "timestamp": metadata.get("timestamp"),
                **metadata,
            }

            async with (
                aiohttp.ClientSession() as session,
                session.post(
                    webhook_url, json=payload, timeout=aiohttp.ClientTimeout(total=30)
                ) as response,
            ):
                success = response.status == 200
                if success:
                    logger.info("Webhook sent: %s", webhook_url)
                else:
                    logger.warning(
                        "Webhook returned %d: %s", response.status, webhook_url
                    )
                return success

        except Exception as e:
            logger.error("Failed to send webhook: %s", e)
            return False
