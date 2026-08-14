"""Tests for the pipeline temporal window resolution.

Responsibility: Validate the four declarative windows
(previous_day, current_month, previous_month, all), including month and
year boundaries.
"""

from __future__ import annotations

from datetime import date

from capiba.pipeline.window import DateRange, resolve_window


class TestResolveWindow:
    """Tests for resolve_window."""

    def test_previous_day(self) -> None:
        """Previous day window: [execution_date - 1, execution_date)."""
        result = resolve_window("previous_day", date(2026, 1, 15))

        assert result == DateRange(start=date(2026, 1, 14), end=date(2026, 1, 15))

    def test_previous_day_month_boundary(self) -> None:
        """Previous day window crosses month boundaries."""
        result = resolve_window("previous_day", date(2026, 3, 1))

        assert result == DateRange(start=date(2026, 2, 28), end=date(2026, 3, 1))

    def test_current_month(self) -> None:
        """Current month window: [1st of month, 1st of next month)."""
        result = resolve_window("current_month", date(2026, 1, 15))

        assert result == DateRange(start=date(2026, 1, 1), end=date(2026, 2, 1))

    def test_current_month_year_boundary(self) -> None:
        """Current month window crosses year boundaries in December."""
        result = resolve_window("current_month", date(2026, 12, 31))

        assert result == DateRange(start=date(2026, 12, 1), end=date(2027, 1, 1))

    def test_previous_month(self) -> None:
        """Previous month window: [1st of previous month, 1st of current)."""
        result = resolve_window("previous_month", date(2026, 2, 2))

        assert result == DateRange(start=date(2026, 1, 1), end=date(2026, 2, 1))

    def test_previous_month_year_boundary(self) -> None:
        """Previous month window crosses year boundaries in January."""
        result = resolve_window("previous_month", date(2026, 1, 2))

        assert result == DateRange(start=date(2025, 12, 1), end=date(2026, 1, 1))

    def test_all(self) -> None:
        """All window is unbounded (None/None)."""
        result = resolve_window("all", date(2026, 1, 15))

        assert result == DateRange(start=None, end=None)
