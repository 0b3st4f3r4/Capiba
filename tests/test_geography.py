"""Tests for the municipality geographic reference (O6, slice 1).

Responsibility: Validate the vendored CSV load (lazy, deterministic), the
(name, UF) and IBGE lookups, and the pure geo-enrichment resolution
functions — with no external infrastructure.
"""

from __future__ import annotations

from typing import Any

import pytest

from capiba.ingestion import geography
from capiba.ingestion.geography import (
    Municipality,
    build_supplier_geo_index,
    buyer_geo_fields,
    lookup_by_ibge,
    lookup_by_name,
    municipality_rows,
    normalize_municipality_name,
)


class TestNormalize:
    """Tests for the municipality name normalization."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("São Paulo", "SAO PAULO"),
            ("são paulo", "SAO PAULO"),
            ("  Governador   Valadares ", "GOVERNADOR VALADARES"),
            ("Maceió", "MACEIO"),
            ("Xanxerê", "XANXERE"),
            ("Sant'Ana do Livramento", "SANT ANA DO LIVRAMENTO"),
            ("", ""),
            (None, ""),
        ],
    )
    def test_normalization(self, raw: Any, expected: str) -> None:
        assert normalize_municipality_name(raw) == expected


class TestVendoredCsv:
    """Smoke tests over the real vendored CSV (integrity of the reference)."""

    def test_loads_full_ibge_grid(self) -> None:
        rows = municipality_rows()
        assert len(rows) > 5500
        assert len({row["ibge_code"] for row in rows}) == len(rows)
        assert all(row["uf"] and row["siafi_code"] for row in rows)

    def test_lazy_load_is_cached(self) -> None:
        assert geography._municipalities() is geography._municipalities()


class TestLookupByName:
    """Tests for the (name, UF) lookup."""

    def test_exact_name(self) -> None:
        municipality = lookup_by_name("Recife", "PE")
        assert municipality is not None
        assert municipality.ibge_code == "2611606"
        assert municipality.siafi_code == "2531"
        assert municipality.latitude == pytest.approx(-8.04666)
        assert municipality.longitude == pytest.approx(-34.8771)

    def test_case_and_accents_insensitive(self) -> None:
        municipality = lookup_by_name("são paulo", "sp")
        assert municipality is not None
        assert municipality.ibge_code == "3550308"

    def test_same_name_in_different_ufs(self) -> None:
        """Names repeat across UFs; the pair (name, UF) is deterministic."""
        pi = lookup_by_name("Bom Jesus", "PI")
        rs = lookup_by_name("Bom Jesus", "RS")
        assert pi is not None and rs is not None
        assert pi.ibge_code != rs.ibge_code
        assert pi.uf == "PI" and rs.uf == "RS"

    def test_unknown_returns_none(self) -> None:
        assert lookup_by_name("Recife", "SP") is None
        assert lookup_by_name("Cidade Inexistente", "PE") is None
        assert lookup_by_name("Recife", "") is None
        assert lookup_by_name(None, "PE") is None


class TestLookupByIbge:
    """Tests for the IBGE code lookup."""

    def test_known_code(self) -> None:
        municipality = lookup_by_ibge("2611606")
        assert municipality is not None
        assert municipality.name == "Recife"
        assert municipality.uf == "PE"

    def test_unknown_code_returns_none(self) -> None:
        assert lookup_by_ibge("0000000") is None
        assert lookup_by_ibge(None) is None


class TestBuyerGeoFields:
    """Tests for the buyer vertex geo resolution."""

    def test_resolved(self) -> None:
        fields = buyer_geo_fields("Belo Horizonte", "MG")
        assert fields is not None
        assert fields["ibge_code"] == "3106200"
        assert fields["latitude"] == pytest.approx(-19.9102)

    def test_unresolved_returns_none(self) -> None:
        assert buyer_geo_fields("Springfield", "XX") is None

    def test_injectable_lookup(self) -> None:
        fake = Municipality(
            ibge_code="1234567",
            name="Fake",
            uf="PE",
            siafi_code="0001",
            latitude=-1.0,
            longitude=-2.0,
        )
        fields = buyer_geo_fields("Qualquer", "PE", lookup=lambda n, u: fake)
        assert fields == {"ibge_code": "1234567", "latitude": -1.0, "longitude": -2.0}


class TestBuildSupplierGeoIndex:
    """Tests for the CNPJ -> lat/long index (TOM chain)."""

    RFB = [
        {"tom_code": "7107", "name": "SAO PAULO"},
        {"tom_code": "2531", "name": "RECIFE"},
    ]

    def test_resolves_through_tom_chain(self) -> None:
        index = build_supplier_geo_index(
            [
                {
                    "cnpj": "12345678000199",
                    "municipio": "2531",
                    "uf": "PE",
                    "is_matriz": True,
                }
            ],
            self.RFB,
        )
        assert index["12345678000199"]["latitude"] == pytest.approx(-8.04666)

    def test_matriz_wins_over_branch(self) -> None:
        """A repeated CNPJ keeps the matriz coordinates, not the branch's."""
        merged = build_supplier_geo_index(
            [
                {
                    "cnpj": "12345678000199",
                    "municipio": "2531",
                    "uf": "PE",
                    "is_matriz": False,
                },
                {
                    "cnpj": "12345678000199",
                    "municipio": "7107",
                    "uf": "SP",
                    "is_matriz": True,
                },
            ],
            self.RFB,
        )
        assert merged["12345678000199"]["latitude"] == pytest.approx(-23.5329)

    def test_unresolvable_rows_are_skipped(self) -> None:
        index = build_supplier_geo_index(
            [
                {"cnpj": "12345678000199", "municipio": "9999", "uf": "PE"},  # no TOM
                {"cnpj": "123", "municipio": "2531", "uf": "PE"},  # bad CNPJ
                {"cnpj": "87654321000110", "municipio": None, "uf": None},
            ],
            self.RFB,
        )
        assert index == {}

    def test_injectable_lookup(self) -> None:
        fake = Municipality(
            ibge_code="1234567", latitude=-3.0, longitude=-4.0
        )
        index = build_supplier_geo_index(
            [{"cnpj": "12345678000199", "municipio": "0001", "uf": "PE"}],
            [{"tom_code": "0001", "name": "QUALQUER"}],
            lookup=lambda n, u: fake,
        )
        assert index == {"12345678000199": {"latitude": -3.0, "longitude": -4.0}}
