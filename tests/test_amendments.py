"""Unit tests for the contract amendment flags module (battery D-05).

The reference semantics is declared in docs/preregistrations/PR-D-05.md
(section 3): the flags are computed from the bronze observation sequence
of a contract, ordered by ingestion date; the last observation is
sovereign, equality never fires and missing/malformed data is NULL.

``TestTermFlags`` guards the plan-B semantics of PR-D-05b (section 3):
flags computed from the contract's registered terms, with the planted
cases B1-B6 — reajuste is never a flag, supressão never fires the value
flag, HTTP 204 computes 0 and a failed query computes NULL.
"""

from __future__ import annotations

from capiba.detection.amendments import compute_amendment_flags, compute_term_flags


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

    def test_value_ratio_tiny_but_positive_is_not_rounded_to_zero(self) -> None:
        # Real-data domain violation (P7, PR-D-05): valorInicial huge with a
        # tiny valorAcumulado yields a ratio below 5e-5; rounding to 4
        # decimals reported 0.0, outside the declared domain (> 0 when
        # present). The descriptor keeps full precision.
        flags = compute_amendment_flags(
            [_obs("2026-02-01", initial=100_000_000.0, accumulated=1_000.0)]
        )
        assert flags.value_ratio is not None
        assert 0 < flags.value_ratio < 0.0001

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


def _term(
    tipo: object = "Termo Aditivo",
    acrescimo_supressao: object = None,
    valor_acrescido: object = None,
    vigencia: object = None,
    prazo_dias: object = None,
    reajuste: object = None,
) -> dict[str, object]:
    term: dict[str, object] = {}
    if tipo is not None:
        term["tipoTermoContratoNome"] = tipo
    if acrescimo_supressao is not None:
        term["qualificacaoAcrescimoSupressao"] = acrescimo_supressao
    if valor_acrescido is not None:
        term["valorAcrescido"] = valor_acrescido
    if vigencia is not None:
        term["qualificacaoVigencia"] = vigencia
    if prazo_dias is not None:
        term["prazoAditadoDias"] = prazo_dias
    if reajuste is not None:
        term["qualificacaoReajuste"] = reajuste
    return term


class TestTermFlags:
    """Flags from the registered terms (PR-D-05b § 3, planted cases B1-B6)."""

    def test_b1_no_terms_is_zero_zero(self) -> None:
        flags = compute_term_flags([])
        assert (flags.f_value_amendment_terms, flags.f_term_extension_terms) == (0, 0)
        assert flags.terms_count == 0
        assert flags.total_value_increase == 0.0
        assert flags.total_days_extended == 0
        assert flags.term_types == []

    def test_b2_value_amendment_fires_value_flag(self) -> None:
        flags = compute_term_flags(
            [_term(acrescimo_supressao=True, valor_acrescido=6840.88)]
        )
        assert (flags.f_value_amendment_terms, flags.f_term_extension_terms) == (1, 0)
        assert flags.total_value_increase == 6840.88

    def test_b3_term_extension_fires_term_flag(self) -> None:
        flags = compute_term_flags([_term(vigencia=True, prazo_dias=180)])
        assert (flags.f_value_amendment_terms, flags.f_term_extension_terms) == (0, 1)
        assert flags.total_days_extended == 180

    def test_b4_pure_reajuste_never_fires(self) -> None:
        # Reajuste por índice é atualização legal de preço, não aditivo.
        reajuste_named = _term(
            tipo="Termo de Reajuste", reajuste=True, valor_acrescido=5000.0
        )
        aditivo_reajuste_only = _term(reajuste=True)
        flags = compute_term_flags([reajuste_named, aditivo_reajuste_only])
        assert (flags.f_value_amendment_terms, flags.f_term_extension_terms) == (0, 0)
        assert flags.terms_count == 2
        assert flags.term_types == ["Termo Aditivo", "Termo de Reajuste"]

    def test_b5_supressao_does_not_fire_value_flag(self) -> None:
        flags = compute_term_flags(
            [_term(acrescimo_supressao=True, valor_acrescido=-10_000.0)]
        )
        assert flags.f_value_amendment_terms == 0
        assert flags.total_value_increase == -10_000.0

    def test_b6_failed_query_is_null_null(self) -> None:
        flags = compute_term_flags(None)
        assert flags.f_value_amendment_terms is None
        assert flags.f_term_extension_terms is None
        assert flags.terms_count is None
        assert flags.total_value_increase is None
        assert flags.total_days_extended is None
        assert flags.term_types is None

    def test_non_amendment_type_with_positive_increase_does_not_fire(self) -> None:
        # The type gate: only "Termo Aditivo" fires, whatever the fields say.
        flags = compute_term_flags(
            [
                _term(
                    tipo="Termo de Apostilamento",
                    acrescimo_supressao=True,
                    valor_acrescido=1000.0,
                    vigencia=True,
                    prazo_dias=90,
                )
            ]
        )
        assert (flags.f_value_amendment_terms, flags.f_term_extension_terms) == (0, 0)

    def test_malformed_amounts_do_not_fire_or_crash(self) -> None:
        flags = compute_term_flags(
            [_term(acrescimo_supressao=True, valor_acrescido="abc", prazo_dias="n/a",
                   vigencia=True)]
        )
        assert (flags.f_value_amendment_terms, flags.f_term_extension_terms) == (0, 0)
        assert flags.total_value_increase == 0.0
        assert flags.total_days_extended == 0

    def test_zero_increase_does_not_fire(self) -> None:
        # valorAcrescido must be strictly positive.
        flags = compute_term_flags([_term(acrescimo_supressao=True, valor_acrescido=0)])
        assert flags.f_value_amendment_terms == 0
