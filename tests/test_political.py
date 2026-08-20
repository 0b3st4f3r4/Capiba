"""Unit tests for the political_connection signal (PR-D-08, section 3).

Each gate of the pre-registered contract is covered by a dedicated case
mirroring the battery cases E1-E10 of ``experiments/detect/D-08.json``;
the battery (slice 4) replays them against the real TSE dumps.
"""

import json
import random
from datetime import date

from capiba.detection.political import (
    _is_elected_mayor,
    _normalize_city,
    political_connection_signals,
)
from capiba.detection.signals import SignalType

YEAR = 2024
SEQ = "90001"
CITY = "Recife"
UF = "PE"
SUPPLIER_CNPJ = "11.111.111/0001-11"
SUPPLIER_DOC = "11111111000111"


def _candidacy(
    sequential: str = SEQ,
    office: str = "Prefeito",
    status: str = "Eleito",
    city: str = CITY,
    uf: str = UF,
    year: int = YEAR,
) -> dict:
    return {
        "election_year": year,
        "candidate_sequential": sequential,
        "candidate_name": "Candidato Teste",
        "party": "XX",
        "office": office,
        "ue_code": "25313",
        "ue_name": city,
        "uf": uf,
        "totalization_status": status,
    }


def _donation(
    document: str | None,
    amount: float,
    sequential: str = SEQ,
    origin_document: str | None = None,
    year: int = YEAR,
) -> dict:
    return {
        "election_year": year,
        "donor_document": document,
        "donor_name": "Doador Teste",
        "donor_origin_document": origin_document,
        "donation_date": date(2024, 8, 1),
        "amount": amount,
        "candidate_sequential": sequential,
    }


def _contract(
    contract_id: str,
    document: str | None,
    amount: float,
    signed: str = "2025-03-01",
    city: str = CITY,
    uf: str = UF,
) -> dict:
    supplier = {"name": "Fornecedor Teste"}
    if document and len(document.replace(".", "").replace("/", "").replace("-", "")) == 14:
        supplier["cnpj"] = document
    elif document:
        supplier["cpf"] = document
    return {
        "id": contract_id,
        "buyer": {
            "siafi_code": "2650",
            "name": "Municipio de Recife",
            "city": city,
            "uf": uf,
        },
        "supplier": supplier,
        "signature_date": signed,
        "amount": amount,
    }


def _run(donations, contracts, candidacies=None):
    return political_connection_signals(
        donations, contracts, candidacies or [_candidacy()]
    )


class TestGates:
    """The five pre-registered gates, one case each (D-08 E1-E10)."""

    def test_e1_pj_donor_supplier_signals_with_capped_score(self):
        signals = _run(
            [_donation(SUPPLIER_CNPJ, 50_000.0)],
            [
                _contract("c1", SUPPLIER_CNPJ, 50_000.0),
                _contract("c2", "22.222.222/0001-22", 75_000.0),
            ],
        )
        assert len(signals) == 1
        signal = signals[0]
        assert signal["entity_type"] == "supplier"
        assert signal["entity_id"] == SUPPLIER_DOC
        assert signal["signal_type"] == SignalType.POLITICAL_CONNECTION
        assert signal["score"] == 1.0  # share 0.40 > 0.25 saturates
        details = json.loads(signal["details"])
        assert details["match"] == "document"
        assert details["share"] == 0.4
        assert details["donation_total_brl"] == 50_000.0
        assert details["donor_document"] == SUPPLIER_DOC
        assert details["candidate"]["party"] == "XX"
        assert details["mandate_start"] == "2025-01-01"
        assert details["mandate_end"] == "2028-12-31"

    def test_e2_contract_before_inauguration_never_signals(self):
        signals = _run(
            [_donation(SUPPLIER_CNPJ, 50_000.0)],
            [
                _contract("c1", SUPPLIER_CNPJ, 50_000.0, signed="2024-12-15"),
                _contract("c2", "22.222.222/0001-22", 75_000.0, signed="2024-12-15"),
            ],
        )
        assert signals == []

    def test_e3_defeated_candidate_never_signals(self):
        signals = _run(
            [_donation(SUPPLIER_CNPJ, 50_000.0)],
            [_contract("c1", SUPPLIER_CNPJ, 50_000.0)],
            candidacies=[_candidacy(status="Não eleito")],
        )
        assert signals == []

    def test_e4_share_below_concentration_gate_never_signals(self):
        signals = _run(
            [_donation(SUPPLIER_CNPJ, 50_000.0)],
            [
                _contract("c1", SUPPLIER_CNPJ, 1_000.0),
                _contract("c2", "22.222.222/0001-22", 99_000.0),
            ],
        )
        assert signals == []  # share 0.01 < 0.05

    def test_e5_pf_donor_score_anchor(self):
        cpf = "123.456.789-09"
        signals = _run(
            [_donation(cpf, 20_000.0)],
            [
                _contract("c1", cpf, 50_000.0),
                _contract("c2", "22.222.222/0001-22", 350_000.0),
            ],
        )
        assert len(signals) == 1
        assert signals[0]["entity_id"] == "12345678909"
        assert signals[0]["score"] == 0.5  # share 0.125 / 0.25

    def test_e6_donation_below_floor_never_signals(self):
        signals = _run(
            [_donation(SUPPLIER_CNPJ, 500.0)],
            [_contract("c1", SUPPLIER_CNPJ, 50_000.0)],
        )
        assert signals == []

    def test_e7_elected_councillor_never_signals(self):
        signals = _run(
            [_donation(SUPPLIER_CNPJ, 50_000.0)],
            [_contract("c1", SUPPLIER_CNPJ, 50_000.0)],
            candidacies=[_candidacy(office="Vereador")],
        )
        assert signals == []

    def test_e8_same_name_divergent_document_never_signals(self):
        signals = _run(
            [_donation("99.999.999/0001-99", 50_000.0)],
            [_contract("c1", SUPPLIER_CNPJ, 50_000.0)],
        )
        assert signals == []  # a name is never evidence

    def test_e9_party_donation_matches_origin_donor(self):
        signals = _run(
            [
                _donation(
                    "44.444.444/0001-44",  # party document
                    30_000.0,
                    origin_document=SUPPLIER_CNPJ,
                )
            ],
            [
                _contract("c1", SUPPLIER_CNPJ, 30_000.0),
                _contract("c2", "22.222.222/0001-22", 70_000.0),
            ],
        )
        assert len(signals) == 1
        assert signals[0]["entity_id"] == SUPPLIER_DOC
        assert signals[0]["score"] == 1.0  # share 0.30 > 0.25

    def test_e10_share_at_inclusive_boundary_scores_anchor(self):
        signals = _run(
            [_donation(SUPPLIER_CNPJ, 5_000.0)],
            [
                _contract("c1", SUPPLIER_CNPJ, 5_000.0),
                _contract("c2", "22.222.222/0001-22", 95_000.0),
            ],
        )
        assert len(signals) == 1
        assert signals[0]["score"] == 0.2  # share 0.05 / 0.25


class TestEdgeCases:
    def test_donor_without_document_never_signals(self):
        signals = _run(
            [_donation(None, 50_000.0)],
            [_contract("c1", SUPPLIER_CNPJ, 50_000.0)],
        )
        assert signals == []

    def test_city_match_is_case_and_accent_insensitive(self):
        signals = political_connection_signals(
            [_donation(SUPPLIER_CNPJ, 50_000.0)],
            [_contract("c1", SUPPLIER_CNPJ, 50_000.0, city="sao paulo", uf="SP")],
            candidacies=[_candidacy(city="São Paulo", uf="SP")],
        )
        assert len(signals) == 1

    def test_donation_floor_aggregates_multiple_donations(self):
        signals = _run(
            [
                _donation(SUPPLIER_CNPJ, 600.0),
                _donation(SUPPLIER_CNPJ, 500.0),
            ],
            [_contract("c1", SUPPLIER_CNPJ, 50_000.0)],
        )
        assert len(signals) == 1  # 1100 >= 1000 only after aggregation

    def test_elected_by_quotient_variants_qualify(self):
        assert _is_elected_mayor(_candidacy(status="Eleito por QP"))
        assert _is_elected_mayor(_candidacy(status="Eleito por média"))
        assert _is_elected_mayor(_candidacy(status="Eleito"))
        assert not _is_elected_mayor(_candidacy(status="2º turno"))
        assert not _is_elected_mayor(_candidacy(status="Suplente"))

    def test_contract_without_buyer_city_is_skipped(self):
        contract = _contract("c1", SUPPLIER_CNPJ, 50_000.0)
        contract["buyer"] = {"siafi_code": "2650"}
        signals = _run([_donation(SUPPLIER_CNPJ, 50_000.0)], [contract])
        assert signals == []

    def test_deterministic_under_input_shuffle(self):
        donations = [
            _donation(SUPPLIER_CNPJ, 40_000.0),
            _donation("33.333.333/0001-33", 20_000.0),
        ]
        contracts = [
            _contract("c1", SUPPLIER_CNPJ, 50_000.0),
            _contract("c2", "33.333.333/0001-33", 40_000.0),
            _contract("c3", "22.222.222/0001-22", 10_000.0),
        ]
        baseline = _run(donations, contracts)
        rng = random.Random(42)
        for _ in range(5):
            shuffled_donations = donations[:]
            shuffled_contracts = contracts[:]
            rng.shuffle(shuffled_donations)
            rng.shuffle(shuffled_contracts)
            assert _run(shuffled_donations, shuffled_contracts) == baseline
        assert len(baseline) == 2


class TestHelpers:
    def test_normalize_city_strips_accents_and_punctuation(self):
        assert _normalize_city("São Paulo") == "SAO PAULO"
        assert _normalize_city("Maceió") == "MACEIO"
        assert _normalize_city("Ribeirão Preto") == "RIBEIRAO PRETO"
        assert _normalize_city("") == ""
        assert _normalize_city(None) == ""

    def test_no_signal_when_no_contracts_in_window(self):
        signals = _run([_donation(SUPPLIER_CNPJ, 50_000.0)], [])
        assert signals == []
