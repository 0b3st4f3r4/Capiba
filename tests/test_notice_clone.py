"""Unit tests for the notice_clone signal (PR-D-10).

Responsibility: Validate the production semantics of
``capiba.detection.notice_clone`` — strict threshold comparison,
reedition veto, null discipline, rolling window, same-territory gate,
deterministic identity/triage keys — with injected stub encoders (no
model downloads, no external infrastructure).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pytest

from capiba.detection.notice_clone import (
    Notice,
    candidate_pairs,
    cosine_similarity,
    notice_clone_signals,
    notice_id,
    triage_key,
    valid_notices,
)

_RUN_DATE = date(2026, 8, 21)
_TERRITORY = "2611606"

_FILLER = (
    "A Prefeitura torna público que realizará licitação para o objeto, "
    "conforme as especificações do projeto básico e o cronograma "
    "físico-financeiro anexo. O valor estimado é de R$ 100.000,00. "
    "O certame ocorrerá no dia 10/09/2026, às 10 horas. "
)


def _notice(
    name: str,
    text: str,
    when: date = _RUN_DATE,
    territory: str = _TERRITORY,
) -> Notice:
    """Builds a notice with the deterministic production id."""
    index = sum(name.encode())  # stable across runs (no PYTHONHASHSEED)
    return Notice(
        notice_id=notice_id(territory, when, "ED-TEST", index),
        territory_id=territory,
        date=when,
        text=text,
    )


def _dict_encoder(vectors: dict[str, tuple[float, ...]]):
    """Encoder stub: fixed vectors per exact text (deterministic)."""

    def encode(texts: list[str]) -> list[tuple[float, ...]]:
        return [vectors[text] for text in texts]

    return encode


class TestCosineSimilarity:
    """Tests for the pure cosine helper."""

    def test_identical_vectors_score_one(self) -> None:
        """The N0 anchor shape: a vector with itself has cosine 1.0."""
        assert cosine_similarity([0.3, 0.5, 0.8], [0.3, 0.5, 0.8]) == pytest.approx(
            1.0, abs=1e-9
        )

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)

    def test_zero_vector_never_signals(self) -> None:
        """An uncomputable embedding (zero vector) has cosine 0.0."""
        assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


class TestIdentity:
    """Tests for the deterministic notice id and triage key."""

    def test_notice_id_is_deterministic(self) -> None:
        assert notice_id("2611606", _RUN_DATE, "ED-1", 3) == notice_id(
            "2611606", _RUN_DATE, "ED-1", 3
        )
        assert notice_id("2611606", _RUN_DATE, "ED-1", 3) != notice_id(
            "2611606", _RUN_DATE, "ED-1", 4
        )

    def test_triage_key_is_order_independent(self) -> None:
        assert triage_key("aaa", "bbb") == triage_key("bbb", "aaa")
        assert triage_key("aaa", "bbb") != triage_key("aaa", "ccc")


class TestValidNotices:
    """Tests for the null discipline of the analysis unit."""

    def test_below_min_chars_is_excluded(self) -> None:
        """A notice below 200 chars of running text is not analyzable."""
        short = _notice("short", "EDITAL Nº 1/2026\nObjeto: cadeiras.")
        long = _notice("long", "EDITAL Nº 2/2026\n" + _FILLER)
        assert valid_notices([short, long], 200) == [long]

    def test_missing_territory_is_excluded(self) -> None:
        notice = _notice("x", "EDITAL Nº 1/2026\n" + _FILLER, territory="")
        assert valid_notices([notice], 200) == []

    def test_output_sorted_by_date_then_id(self) -> None:
        older = _notice("b", "EDITAL Nº 1/2026\n" + _FILLER, date(2026, 1, 1))
        newer = _notice("a", "EDITAL Nº 2/2026\n" + _FILLER, date(2026, 2, 1))
        assert valid_notices([newer, older], 200) == [older, newer]


class TestCandidatePairs:
    """Tests for the pair semantics: territory, window, veto."""

    def _corpus(self) -> tuple[Notice, Notice]:
        new = _notice("new", "EDITAL Nº 1/2026\n" + _FILLER + "Processo nº 11111.111111/2026-10.")
        historical = _notice(
            "hist",
            "EDITAL Nº 2/2026\n" + _FILLER + "Processo nº 22222.222222/2025-20.",
            _RUN_DATE - timedelta(days=30),
        )
        return new, historical

    def test_same_territory_within_window_pairs(self) -> None:
        new, historical = self._corpus()
        pairs = candidate_pairs([new, historical], reference_date=_RUN_DATE)
        assert pairs == [(new, historical)]

    def test_cross_territory_never_pairs(self) -> None:
        new, historical = self._corpus()
        foreign = Notice(
            historical.notice_id, "1100424", historical.date, historical.text
        )
        assert candidate_pairs([new, foreign], reference_date=_RUN_DATE) == []

    def test_outside_the_window_never_pairs(self) -> None:
        new, historical = self._corpus()
        old = Notice(
            historical.notice_id,
            historical.territory_id,
            _RUN_DATE - timedelta(days=366),
            historical.text,
        )
        assert (
            candidate_pairs([new, old], reference_date=_RUN_DATE, window_days=365)
            == []
        )

    def test_reedition_veto_same_process_number(self) -> None:
        """Same extractable process number on both sides -> never a pair."""
        new, _ = self._corpus()
        reedition = Notice(
            "h" * 16,
            _TERRITORY,
            _RUN_DATE - timedelta(days=30),
            "EDITAL Nº 9/2026\nTexto alterado na retificação. "
            + _FILLER
            + "Processo nº 11111.111111/2026-10.",
        )
        assert candidate_pairs([new, reedition], reference_date=_RUN_DATE) == []

    def test_process_missing_on_one_side_is_no_veto(self) -> None:
        """Process number absent on one side: no veto, no evidence."""
        new, _ = self._corpus()
        without_process = Notice(
            "h" * 16,
            _TERRITORY,
            _RUN_DATE - timedelta(days=30),
            "EDITAL Nº 9/2026\n" + _FILLER,
        )
        pairs = candidate_pairs([new, without_process], reference_date=_RUN_DATE)
        assert pairs == [(new, without_process)]

    def test_reference_date_selects_the_new_notices(self) -> None:
        """Without a reference date every notice pairs with all older ones."""
        new, historical = self._corpus()
        assert candidate_pairs([new, historical]) == [(new, historical)]
        # The historical is not "new" when the reference date is the run date.
        pairs = candidate_pairs([new, historical], reference_date=historical.date)
        assert pairs == []


class TestNoticeCloneSignals:
    """Tests for the emission semantics (strict threshold, details)."""

    def _signal_pair(
        self,
        historical_vector: tuple[float, ...],
        threshold: float = 0.85,
    ) -> tuple[list[dict[str, Any]], Notice, Notice]:
        new = _notice("new", "EDITAL Nº 1/2026\n" + _FILLER + "Objeto novo.")
        historical = _notice(
            "hist",
            "EDITAL Nº 2/2026\n" + _FILLER + "Objeto histórico.",
            _RUN_DATE - timedelta(days=30),
        )
        encode = _dict_encoder({new.text: (1.0, 0.0), historical.text: historical_vector})
        signals = notice_clone_signals(
            [new, historical],
            encode=encode,
            threshold=threshold,
            reference_date=_RUN_DATE,
        )
        return signals, new, historical

    def test_exact_copy_scores_one(self) -> None:
        """N0 anchor: identical texts -> score 1.0000."""
        text = "EDITAL Nº 1/2026\n" + _FILLER + "Objeto sem processo extraível."
        new = _notice("new", text)
        historical = _notice("hist", text, _RUN_DATE - timedelta(days=30))
        encode = _dict_encoder({text: (0.6, 0.8)})
        signals = notice_clone_signals(
            [new, historical], encode=encode, reference_date=_RUN_DATE
        )
        assert len(signals) == 1
        assert signals[0]["score"] == 1.0
        assert signals[0]["signal_type"] == "notice_clone"
        assert signals[0]["entity_type"] == "notice"
        assert signals[0]["entity_id"] == triage_key(
            new.notice_id, historical.notice_id
        )

    def test_strict_threshold_comparison(self) -> None:
        """Similarity exactly at the threshold does NOT signal (strict >)."""
        # (3, 4) has norm exactly 5.0, so the cosine with (1, 0) is the
        # float 0.6 exactly — no representation ambiguity.
        signals, _, _ = self._signal_pair((3.0, 4.0), threshold=0.6)
        assert signals == []
        signals, _, _ = self._signal_pair((3.0, 4.0), threshold=0.5)
        assert len(signals) == 1

    def test_details_ground_the_signal(self) -> None:
        signals, new, historical = self._signal_pair((0.9, 0.1))
        details = signals[0]["details"]
        assert new.notice_id in details
        assert historical.notice_id in details
        assert historical.date.isoformat() in details
        assert '"threshold": 0.85' in details

    def test_score_is_the_rounded_similarity(self) -> None:
        signals, _, _ = self._signal_pair((0.912345678, 0.4), threshold=0.5)
        expected = round(cosine_similarity((1.0, 0.0), (0.912345678, 0.4)), 4)
        assert signals[0]["score"] == expected

    def test_reedition_never_signals_even_with_max_similarity(self) -> None:
        """N4 discipline: same process number vetoes even identical texts."""
        text = (
            "EDITAL Nº 1/2026\n"
            + _FILLER
            + "Processo nº 12345.123456/2026-01."
        )
        new = _notice("new", text)
        historical = _notice("hist", text, _RUN_DATE - timedelta(days=30))
        encode = _dict_encoder({text: (0.6, 0.8)})
        assert (
            notice_clone_signals([new, historical], encode=encode, reference_date=_RUN_DATE)
            == []
        )

    def test_determinism_bit_a_bit(self) -> None:
        """Same input reproduces the same signal rows, bit a bit (P7)."""
        first, _, _ = self._signal_pair((0.9, 0.1))
        second, _, _ = self._signal_pair((0.9, 0.1))
        assert first == second
