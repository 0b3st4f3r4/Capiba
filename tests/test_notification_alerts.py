"""Tests for the pipeline alert helpers.

Responsibility: Validate the detection/validation alert wrappers
(recipients gate, thresholds, priority, payload shape) and their
wiring into the pipeline tasks. Offline: the dispatcher/async layer
is mocked or has its SMTP send patched.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from capiba.notification import alerts
from capiba.notification.alerts import notify_fraud_signals, notify_validation_failure
from capiba.notification.dispatcher import Priority

RECIPIENTS = ["data-stewards@example.org"]


def _signal(score: float, signal_type: str = "concentration") -> dict[str, Any]:
    """Builds a pipeline-shaped fraud signal."""
    return {
        "entity_type": "supplier",
        "entity_id": "12345678000195",
        "signal_type": signal_type,
        "score": score,
        "details": '{"contracts": 3}',
    }


@pytest.fixture(autouse=True)
def recipients(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: enables notifications with a fixed recipient list."""
    monkeypatch.setattr(alerts, "NOTIFICATION_RECIPIENTS", RECIPIENTS)


class TestNotifyFraudSignals:
    """Tests for the detection alert."""

    def test_no_recipients_is_noop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Empty recipients must disable notifications."""
        monkeypatch.setattr(alerts, "NOTIFICATION_RECIPIENTS", [])

        with patch.object(alerts, "_dispatch") as mock_dispatch:
            assert notify_fraud_signals([_signal(0.99)], date(2026, 1, 1)) is False

        mock_dispatch.assert_not_called()

    def test_below_threshold_is_noop(self) -> None:
        """Signals below the alert score must not trigger an alert."""
        with patch.object(alerts, "_dispatch") as mock_dispatch:
            assert notify_fraud_signals([_signal(0.5)], date(2026, 1, 1)) is False

        mock_dispatch.assert_not_called()

    def test_above_threshold_dispatches_high_priority(self) -> None:
        """A signal above the threshold must dispatch a HIGH alert."""
        with patch.object(alerts, "_dispatch", return_value=True) as mock_dispatch:
            assert notify_fraud_signals([_signal(0.8)], date(2026, 1, 1)) is True

        alert = mock_dispatch.call_args.args[0]
        assert alert.priority is Priority.HIGH
        assert alert.recipients == RECIPIENTS
        # Payload adapted to the detection template (type/score/evidence).
        assert alert.metadata["signals"] == [
            {"type": "concentration", "score": 0.8, "evidence": '{"contracts": 3}'}
        ]
        assert alert.metadata["risk_index"] == 0.8
        assert alert.metadata["run_date"] == "2026-01-01"

    def test_score_at_or_above_critical_escalates(self) -> None:
        """A maximum score >= 0.9 must escalate the alert to CRITICAL."""
        with patch.object(alerts, "_dispatch", return_value=True) as mock_dispatch:
            notify_fraud_signals([_signal(0.8), _signal(0.95)], None)

        alert = mock_dispatch.call_args.args[0]
        assert alert.priority is Priority.CRITICAL
        assert len(alert.metadata["signals"]) == 2

    def test_dispatch_failure_never_raises(self) -> None:
        """Dispatcher failures must be swallowed with a warning."""
        with patch.object(alerts, "_dispatch", side_effect=RuntimeError("boom")):
            assert notify_fraud_signals([_signal(0.99)], None) is False

    def test_detection_template_renders_adapted_signals(self) -> None:
        """The adapted payload must render in the detection e-mail template."""
        with patch(
            "capiba.notification.dispatcher.aiosmtplib.send", new=AsyncMock()
        ) as mock_send:
            assert notify_fraud_signals([_signal(0.8)], date(2026, 1, 1)) is True

        message = mock_send.await_args.args[0]
        assert "[DETECTION]" in message
        assert "concentration" in message
        assert "12345678000195" in message


class TestNotifyValidationFailure:
    """Tests for the quality alert."""

    def _report(self, **overrides: Any) -> dict[str, Any]:
        report: dict[str, Any] = {
            "total": 100,
            "duplicates": 0,
            "duplicate_ids": [],
            "normalization_errors": 0,
            "valid": True,
        }
        report.update(overrides)
        return report

    def test_valid_report_is_noop(self) -> None:
        """A valid report with a low error rate must not alert."""
        with patch.object(alerts, "_dispatch") as mock_dispatch:
            assert notify_validation_failure(self._report(), "daily_ingestion") is False

        mock_dispatch.assert_not_called()

    def test_invalid_report_dispatches_critical(self) -> None:
        """Duplicates (valid: false) must dispatch a CRITICAL alert."""
        report = self._report(duplicates=2, duplicate_ids=["C1", "C2"], valid=False)

        with patch.object(alerts, "_dispatch", return_value=True) as mock_dispatch:
            assert notify_validation_failure(report, "daily_ingestion") is True

        alert = mock_dispatch.call_args.args[0]
        assert alert.priority is Priority.CRITICAL
        assert alert.metadata["dataset"] == "daily_ingestion"
        assert any("duplicate" in p for p in alert.metadata["alerts"])

    def test_high_normalization_error_rate_alerts(self) -> None:
        """An error rate above 5% must alert even with a valid report."""
        report = self._report(normalization_errors=6)

        with patch.object(alerts, "_dispatch", return_value=True) as mock_dispatch:
            assert notify_validation_failure(report, "daily_ingestion") is True

        alert = mock_dispatch.call_args.args[0]
        assert any("normalization error rate" in p for p in alert.metadata["alerts"])

    def test_error_rate_at_threshold_is_noop(self) -> None:
        """An error rate of exactly 5% must not alert."""
        with patch.object(alerts, "_dispatch") as mock_dispatch:
            assert (
                notify_validation_failure(
                    self._report(normalization_errors=5), "daily_ingestion"
                )
                is False
            )

        mock_dispatch.assert_not_called()

    def test_dispatch_failure_never_raises(self) -> None:
        """Dispatcher failures must be swallowed with a warning."""
        with patch.object(alerts, "_dispatch", side_effect=RuntimeError("boom")):
            assert notify_validation_failure(self._report(valid=False), "p") is False


class TestTaskWiring:
    """Tests for the alert calls inside the pipeline tasks."""

    @patch("capiba.pipeline.tasks.notify_fraud_signals")
    @patch("capiba.pipeline.tasks.lake")
    @patch("capiba.pipeline.tasks.collusion_eligibility")
    @patch("capiba.pipeline.tasks.get_capiba_db")
    def test_task_detect_notifies_signals(
        self,
        mock_get_db: MagicMock,
        mock_eligibility: MagicMock,
        mock_lake: MagicMock,
        mock_notify: MagicMock,
    ) -> None:
        """task_detect must notify the computed signals (best-effort).

        The graph DB and the eligibility AQL are mocked: without this the
        test escapes to the real ArangoDB whenever a port-forward is up and
        the pair derivation explodes combinatorially on real data.
        """
        from capiba.pipeline.tasks import task_detect

        mock_eligibility.return_value = []

        mock_lake.read_silver_contracts.return_value = [
            {
                "id": f"C{i:03d}",
                "amount": 1000.0,
                "validity_start": "2026-01-01",
                "validity_end": "2026-01-31",
                "modality": "pregao_eletronico",
                "buyer": {"siafi_code": "123456"},
                "supplier": {"cnpj": f"1111111100019{i}"},
            }
            for i in range(3)
        ]

        summary = task_detect(ds="2026-01-01")

        assert summary["signals"] >= 1
        mock_notify.assert_called_once()
        signals, run_date = mock_notify.call_args.args
        assert len(signals) == summary["signals"]
        assert run_date == date(2026, 1, 1)

    @patch("capiba.pipeline.tasks.notify_validation_failure")
    @patch("capiba.pipeline.tasks._load_spec")
    def test_task_validate_pipeline_notifies(
        self, mock_load: MagicMock, mock_notify: MagicMock
    ) -> None:
        """task_validate_pipeline must notify the computed report."""
        from capiba.pipeline.tasks import task_validate_pipeline

        spec = MagicMock()
        spec.name = "daily_ingestion"
        spec.validation = None
        mock_load.return_value = spec

        ti = MagicMock()
        ti.xcom_pull.side_effect = [
            [{"id": "C001"}, {"id": "C001"}],  # normalized_contracts
            0,  # normalization_errors
        ]

        report = task_validate_pipeline("spec.yaml", ti=ti, ds="2026-01-01")

        assert report["valid"] is False
        mock_notify.assert_called_once()
        notified_report, pipeline = mock_notify.call_args.args
        assert notified_report is report
        assert pipeline == "daily_ingestion"
