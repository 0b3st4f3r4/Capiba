"""Tests for the filter_by_min_value transformation.

Responsibility: Validate the amount filter of the declarative pipeline
framework directly (today it is only covered indirectly via the runner).
"""

from __future__ import annotations

from capiba.transformations.filter_by_min_value import transform


class TestFilterByMinValue:
    """Direct tests for the transform() function."""

    def test_keeps_records_at_or_above_min(self) -> None:
        """Records with amount >= min_value are kept, order preserved."""
        records = [
            {"id": "a", "amount": "500"},
            {"id": "b", "amount": "1000"},
            {"id": "c", "amount": "1500.50"},
        ]

        kept = transform(records, min_value=1000)

        assert [r["id"] for r in kept] == ["b", "c"]

    def test_missing_or_invalid_amount_is_dropped(self) -> None:
        """Missing, null or non-numeric amounts count as zero."""
        records = [
            {"id": "a"},
            {"id": "b", "amount": None},
            {"id": "c", "amount": "not-a-number"},
            {"id": "d", "amount": "10"},
        ]

        kept = transform(records, min_value=1)

        assert [r["id"] for r in kept] == ["d"]

    def test_default_min_value_keeps_zero_amounts(self) -> None:
        """The default min_value=0 keeps records with zero/missing amounts."""
        records = [{"id": "a"}, {"id": "b", "amount": "0"}]

        assert len(transform(records)) == 2

    def test_empty_input(self) -> None:
        """An empty batch stays empty."""
        assert transform([], min_value=100) == []
