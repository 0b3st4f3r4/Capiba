"""Temporal window resolution for declarative pipelines.

Responsibility: turn the window names declared in the YAML specs
(``previous_day``, ``current_month``, ``previous_month``, ``all``) into a
concrete ``DateRange`` for a given execution date. This logic used to be
hardcoded per task in ``capiba.pipeline.tasks`` (PNCP crawled the previous
day, the Transparency Portal the current month, the Federal Revenue dump
the previous month).

Dependencies: none
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal

WindowKind = Literal["previous_day", "current_month", "previous_month", "all"]

WINDOW_KINDS: tuple[str, ...] = (
    "previous_day",
    "current_month",
    "previous_month",
    "all",
)


@dataclass(frozen=True)
class DateRange:
    """Half-open crawl window [start, end).

    ``None`` bounds mean unbounded (window ``all``); record sources treat a
    ``None`` bound by falling back to the API defaults, dump sources only
    support month-aligned windows.
    """

    start: date | None
    end: date | None


def _next_month(day: date) -> date:
    """Returns the first day of the month after ``day``'s month."""
    if day.month == 12:
        return date(day.year + 1, 1, 1)
    return date(day.year, day.month + 1, 1)


def resolve_window(window: WindowKind, execution_date: date) -> DateRange:
    """Resolves a declared window into a concrete date range.

    Args:
        window: Window name from the pipeline spec.
        execution_date: Reference date of the run (Airflow logical date).

    Returns:
        The resolved ``DateRange``:

        - ``previous_day``: [execution_date - 1 day, execution_date).
        - ``current_month``: [1st of the current month, 1st of the next).
        - ``previous_month``: [1st of the previous month, 1st of the current).
        - ``all``: unbounded (``None``/``None``).
    """
    month_start = execution_date.replace(day=1)
    if window == "previous_day":
        return DateRange(start=execution_date - timedelta(days=1), end=execution_date)
    if window == "current_month":
        return DateRange(start=month_start, end=_next_month(month_start))
    if window == "previous_month":
        previous_month_end = month_start
        previous_month_start = (month_start - timedelta(days=1)).replace(day=1)
        return DateRange(start=previous_month_start, end=previous_month_end)
    return DateRange(start=None, end=None)
