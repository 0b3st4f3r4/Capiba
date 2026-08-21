"""Unit tests for the gazette edition segmentation (PR-D-10).

Responsibility: Validate the structural-marker segmentation and the
process-number extraction of ``capiba.ingestion.gazette_segments`` over
synthetic editions — including the N6 anchor shape (an edition with N
planted notices and markers must recover exactly N units).
"""

from __future__ import annotations

from capiba.ingestion.gazette_segments import (
    DEFAULT_MARKERS,
    extract_process_number,
    segment_edition,
)

_PREAMBLE = "DIÁRIO OFICIAL DO MUNICÍPIO\nPoder Executivo\nRecife, 21 de agosto de 2026"


def _notice(header: str, body: str) -> str:
    return f"{header}\n{body}"


def _body(label: str) -> str:
    return (
        f"A Prefeitura torna público que realizará licitação para {label}, "
        "conforme as especificações do projeto básico e o cronograma "
        "físico-financeiro anexo. O valor estimado é de R$ 100.000,00. "
        "O certame ocorrerá no dia 10/09/2026, às 10 horas."
    )


class TestSegmentEdition:
    """Tests for segment_edition."""

    def test_recovers_exactly_the_planted_units(self) -> None:
        """N6 anchor shape: 12 planted notices + preamble -> 12 units."""
        notices = [
            _notice(f"EDITAL Nº {k}/2026", _body(f"o objeto {k}")) for k in range(12)
        ]
        edition = _PREAMBLE + "\n" + "\n".join(notices)

        segments = segment_edition(edition)

        assert segments == notices  # verbatim, bit a bit

    def test_text_without_markers_yields_no_units(self) -> None:
        """An edition without structural markers produces nothing."""
        assert segment_edition(_PREAMBLE) == []

    def test_marker_matching_is_case_and_accent_insensitive(self) -> None:
        """Lowercase/accented headers are recognized after normalization."""
        notices = [
            _notice("edital nº 1/2026", _body("obras")),
            _notice("Aviso de Licitação", _body("saúde")),
            _notice("EXTRATO DE CONTRATO", _body("TI")),
        ]

        assert segment_edition("\n".join(notices)) == notices

    def test_process_header_is_a_declared_marker(self) -> None:
        """A line starting with PROCESSO opens a unit (declared marker)."""
        notices = [
            _notice("EDITAL Nº 1/2026", _body("obras")),
            _notice("PROCESSO Nº 12345.000001/2026-01", _body("andamento")),
        ]

        assert segment_edition("\n".join(notices)) == notices

    def test_long_marker_line_is_prose_not_header(self) -> None:
        """A line over the header limit mentioning a marker is not split."""
        long_line = "EXTRATO " + " ".join(["de contrato administrativo"] * 10)
        notice = _notice("EDITAL Nº 1/2026", f"{_body('obras')}\n{long_line}")

        assert segment_edition(notice) == [notice]

    def test_custom_markers_are_normalized(self) -> None:
        """Custom marker sets are normalized before matching."""
        notice = _notice("Dispensa de Licitação", _body("objeto"))

        assert segment_edition(notice, markers=["DISPENSA"]) == [notice]
        assert segment_edition(notice, markers=DEFAULT_MARKERS) == []

    def test_empty_text(self) -> None:
        """Empty input yields no units."""
        assert segment_edition("") == []


class TestExtractProcessNumber:
    """Tests for extract_process_number (digit-normalized)."""

    def test_canonical_nup(self) -> None:
        """The canonical NUP is normalized to digits only."""
        text = "Processo nº 12345.12345678/2026-01."
        assert extract_process_number(text) == "1234512345678202601"

    def test_punctuated_variation(self) -> None:
        """Spaces around the separators compare equal to the canonical form."""
        text = "Processo n. 12345 . 12345678 / 2026 - 01"
        assert extract_process_number(text) == "1234512345678202601"

    def test_labeled_simple_number(self) -> None:
        """A simple labeled reference (NNNN/YYYY) is extracted."""
        assert extract_process_number("Processo nº 1234/2026") == "12342026"

    def test_absent_returns_none(self) -> None:
        """No process number -> None (no veto, no evidence)."""
        assert extract_process_number(_body("obras")) is None

    def test_implausible_number_returns_none(self) -> None:
        """A labeled number too short to be a process reference is ignored."""
        assert extract_process_number("Processo nº 12/26 de andamento") is None

    def test_first_plausible_wins(self) -> None:
        """The first plausible NUP in the text is returned, deterministically."""
        text = (
            "Processo nº 11111.111111/2025-10. "
            "Cotejado com o processo nº 22222.222222/2025-20."
        )
        assert extract_process_number(text) == "11111111111202510"
