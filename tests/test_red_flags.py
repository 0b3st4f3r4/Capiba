"""Unit tests for the contract red flags module (battery D-04 semantics).

The reference semantics is declared in docs/preregistrations/PR-D-04.md
(section 3, as amended): flags are 1 (suspect), 0 (not suspect) or None
(insufficient data); the CRI is the mean of the non-null flags, rounded
to 4 decimals, None when every flag is null.
"""

from __future__ import annotations

from capiba.detection.red_flags import compute_red_flags


def _payload(
    opened: str | None = "2026-01-01T09:00:00",
    closed: str | None = "2026-01-11T17:00:00",
    estimated: object = 100_000.0,
    homologated: object = 90_000.0,
) -> dict[str, object]:
    payload: dict[str, object] = {}
    if opened is not None:
        payload["dataAberturaProposta"] = opened
    if closed is not None:
        payload["dataEncerramentoProposta"] = closed
    if estimated is not None:
        payload["valorInicialCompra"] = estimated
    if homologated is not None:
        payload["valorTotalHomologado"] = homologated
    return payload


class TestNonCompetitive:
    def test_competitive_modality_is_zero(self) -> None:
        flags = compute_red_flags(_payload(), "pregao")
        assert flags.f_non_competitive == 0

    def test_dispensa_is_one(self) -> None:
        flags = compute_red_flags(_payload(), "dispensa de licitação")
        assert flags.f_non_competitive == 1

    def test_inexigibilidade_is_one(self) -> None:
        flags = compute_red_flags(_payload(), "Inexigibilidade")
        assert flags.f_non_competitive == 1

    def test_unknown_modality_is_null(self) -> None:
        for modality in (None, "", "not_informed"):
            flags = compute_red_flags(_payload(), modality)
            assert flags.f_non_competitive is None, modality


class TestShortWindow:
    def test_ten_day_window_is_zero(self) -> None:
        flags = compute_red_flags(_payload(), "pregao")
        assert flags.f_short_window == 0

    def test_seven_day_boundary_is_zero(self) -> None:
        flags = compute_red_flags(
            _payload(closed="2026-01-08T17:00:00"), "pregao"
        )
        assert flags.f_short_window == 0

    def test_six_day_window_is_one(self) -> None:
        flags = compute_red_flags(
            _payload(closed="2026-01-07T17:00:00"), "pregao"
        )
        assert flags.f_short_window == 1

    def test_negative_window_is_one(self) -> None:
        flags = compute_red_flags(
            _payload(closed="2025-12-30T17:00:00"), "pregao"
        )
        assert flags.f_short_window == 1

    def test_missing_date_is_null(self) -> None:
        assert compute_red_flags(_payload(opened=None), "pregao").f_short_window is None
        assert compute_red_flags(_payload(closed=None), "pregao").f_short_window is None

    def test_malformed_date_is_null(self) -> None:
        flags = compute_red_flags(_payload(opened="n/a"), "pregao")
        assert flags.f_short_window is None

    def test_date_with_timezone(self) -> None:
        flags = compute_red_flags(
            _payload(
                opened="2026-01-01T00:00:00-03:00",
                closed="2026-01-06T23:59:59-03:00",
            ),
            "pregao",
        )
        assert flags.f_short_window == 1

    def test_custom_threshold(self) -> None:
        flags = compute_red_flags(_payload(), "pregao", short_window_days=15)
        assert flags.f_short_window == 1


class TestPriceRatio:
    def test_below_estimate_is_zero(self) -> None:
        assert compute_red_flags(_payload(), "pregao").f_price_ratio == 0

    def test_equal_to_estimate_is_zero(self) -> None:
        flags = compute_red_flags(_payload(homologated=100_000.0), "pregao")
        assert flags.f_price_ratio == 0

    def test_above_estimate_is_one(self) -> None:
        flags = compute_red_flags(_payload(homologated=101_000.0), "pregao")
        assert flags.f_price_ratio == 1

    def test_non_positive_values_are_null(self) -> None:
        assert compute_red_flags(_payload(estimated=0), "pregao").f_price_ratio is None
        assert (
            compute_red_flags(_payload(homologated=0), "pregao").f_price_ratio is None
        )

    def test_missing_or_malformed_values_are_null(self) -> None:
        assert compute_red_flags(_payload(estimated=None), "pregao").f_price_ratio is None
        assert compute_red_flags(_payload(estimated="abc"), "pregao").f_price_ratio is None
        assert compute_red_flags({}, "pregao").f_price_ratio is None


class TestCri:
    def test_mean_of_non_null_flags(self) -> None:
        # (0, None, 1) -> 0.5
        flags = compute_red_flags(
            _payload(opened=None, closed=None, homologated=101_000.0), "pregao"
        )
        assert flags.cri == 0.5

    def test_rounded_to_four_decimals(self) -> None:
        # (0, 1, 0) -> 1/3
        flags = compute_red_flags(
            _payload(closed="2026-01-07T17:00:00"), "pregao"
        )
        assert flags.cri == 0.3333

    def test_all_null_flags_yield_null_cri(self) -> None:
        flags = compute_red_flags({}, "not_informed")
        assert flags.cri is None

    def test_cri_bounds(self) -> None:
        assert compute_red_flags(_payload(), "pregao").cri == 0.0
        flags = compute_red_flags(
            _payload(closed="2026-01-07T17:00:00", homologated=120_000.0),
            "dispensa",
        )
        assert flags.cri == 1.0
