"""Unit tests for the contract amendment flags module (battery D-05).

The reference semantics is declared in docs/preregistrations/PR-D-05.md
(section 3): the flags are computed from the bronze observation sequence
of a contract, ordered by ingestion date; the last observation is
sovereign, equality never fires and missing/malformed data is NULL.
"""

from __future__ import annotations

from capiba.detection.amendments import compute_amendment_flags


def _obs(
    observed_on: str,
    initial: object = 100_000.0,
    accumulated: object = 100_000.0,
    validity_end: object = "2026-12-31",
    rectifications: object = 0,
) -> dict[str, object]:
    obs: dict[str, object] = {"observed_on": observed_on}
    if initial is not None:
        obs["valorInicial"] = initial
    if accumulated is not None:
        obs["valorAcumulado"] = accumulated
    if validity_end is not None:
        obs["dataVigenciaFim"] = validity_end
    if rectifications is not None:
        obs["numeroRetificacao"] = rectifications
    return obs


class TestValueAmendment:
    def test_a1_single_snapshot_equal_values_is_zero(self) -> None:
        flags = compute_amendment_flags([_obs("2026-02-01")])
        assert flags.f_value_amendment == 0
        assert flags.f_term_extension == 0

    def test_a2_accumulated_above_initial_is_one(self) -> None:
        flags = compute_amendment_flags([_obs("2026-02-01", accumulated=120_000.0)])
        assert flags.f_value_amendment == 1
        assert flags.f_term_extension == 0

    def test_a4_equality_after_two_observations_is_zero(self) -> None:
        flags = compute_amendment_flags(
            [_obs("2026-02-01"), _obs("2026-06-01", rectifications=1)]
        )
        assert flags.f_value_amendment == 0

    def test_a6_missing_accumulated_is_null(self) -> None:
        flags = compute_amendment_flags(
            [_obs("2026-02-01", accumulated=None), _obs("2026-06-01", accumulated=None)]
        )
        assert flags.f_value_amendment is None

    def test_a7_missing_or_zero_initial_is_null(self) -> None:
        assert compute_amendment_flags([_obs("2026-02-01", initial=None)]).f_value_amendment is None
        assert compute_amendment_flags([_obs("2026-02-01", initial=0)]).f_value_amendment is None

    def test_a9_last_observation_is_sovereign(self) -> None:
        # First observation high, last one back to the initial value.
        flags = compute_amendment_flags(
            [_obs("2026-02-01", accumulated=150_000.0), _obs("2026-06-01")]
        )
        assert flags.f_value_amendment == 0

    def test_value_ratio_descriptor(self) -> None:
        flags = compute_amendment_flags([_obs("2026-02-01", accumulated=120_000.0)])
        assert flags.value_ratio == 1.2

    def test_non_positive_accumulated_does_not_count(self) -> None:
        flags = compute_amendment_flags([_obs("2026-02-01", accumulated=0)])
        assert flags.f_value_amendment is None


class TestTermExtension:
    def test_a3_extended_validity_is_one(self) -> None:
        flags = compute_amendment_flags(
            [
                _obs("2026-02-01", validity_end="2026-12-31"),
                _obs("2026-06-01", validity_end="2027-06-30"),
            ]
        )
        assert flags.f_term_extension == 1
        assert flags.f_value_amendment == 0

    def test_a5_value_and_term_amended(self) -> None:
        flags = compute_amendment_flags(
            [
                _obs("2026-02-01"),
                _obs("2026-06-01", accumulated=130_000.0, validity_end="2027-06-30"),
            ]
        )
        assert (flags.f_value_amendment, flags.f_term_extension) == (1, 1)

    def test_single_observation_is_zero(self) -> None:
        flags = compute_amendment_flags([_obs("2026-02-01")])
        assert flags.f_term_extension == 0

    def test_missing_validity_end_is_null(self) -> None:
        flags = compute_amendment_flags([_obs("2026-02-01", validity_end=None)])
        assert flags.f_term_extension is None

    def test_shortened_validity_is_zero(self) -> None:
        flags = compute_amendment_flags(
            [
                _obs("2026-02-01", validity_end="2026-12-31"),
                _obs("2026-06-01", validity_end="2026-06-30"),
            ]
        )
        assert flags.f_term_extension == 0


class TestRobustness:
    def test_a8_malformed_fields_are_null_without_error(self) -> None:
        flags = compute_amendment_flags(
            [_obs("2026-02-01", initial="abc", accumulated="n/a", validity_end="n/a")]
        )
        assert flags.f_value_amendment is None
        assert flags.f_term_extension is None

    def test_empty_sequence_is_all_null(self) -> None:
        flags = compute_amendment_flags([])
        assert flags.f_value_amendment is None
        assert flags.f_term_extension is None
        assert flags.observations == 0

    def test_ordering_by_ingestion_date_not_read_order(self) -> None:
        # Same observations, shuffled read order: same flags (§ 6 invariant).
        older = _obs("2026-02-01", validity_end="2026-12-31")
        newer = _obs("2026-06-01", accumulated=130_000.0, validity_end="2027-06-30")
        assert compute_amendment_flags([newer, older]) == compute_amendment_flags(
            [older, newer]
        )

    def test_rectifications_descriptor_is_max(self) -> None:
        flags = compute_amendment_flags(
            [_obs("2026-02-01", rectifications=2), _obs("2026-06-01", rectifications=1)]
        )
        assert flags.max_rectifications == 2
