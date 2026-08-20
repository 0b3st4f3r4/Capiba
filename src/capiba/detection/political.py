"""Political connection signal: campaign donors x suppliers of the supported entity.

Responsibility: emit the ``political_connection`` signal (O8) when a
campaign donor of an elected mayor becomes a supplier of that mayor's
municipality, under the five gates pre-registered in
``docs/preregistrations/PR-D-08.md`` (section 3):

- **Exact document match**: the effective donor document (the origin donor
  when the donation came via a party, else the direct donor) equals the
  supplier's CPF/CNPJ. A name is never evidence.
- **Elected gate**: the donation recipient was elected mayor (executive
  only — a city councillor does not control the buyer).
- **Temporal gate**: the contract's signature date falls inside the
  mandate window (inclusive) — a donation alone is not evidence, so a
  contract signed before the inauguration never signals.
- **Donation floor**: the donor's total donations to the elected
  campaign reach ``min_donation_brl``.
- **Concentration gate**: the supplier's share of the buyer's contracted
  value within the window reaches ``min_supplier_share``; the score is
  ``min(1.0, share / score_share_reference)``.

The municipality of the urn (UE) matches the contract buyer by the
normalized (city, UF) pair; the SIAFI crosswalk seed of the gold mart
(PR-D-08, slice 3) refines this link for publication. Thresholds live in
the battery config (``experiments/detect/D-08.json``), never only in code.

Dependencies: capiba.detection.screening, capiba.detection.signals
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import date
from typing import Any

from capiba.detection.screening import _as_date
from capiba.detection.signals import SignalType

# Pre-registered defaults (PR-D-08, section 3); the battery config carries
# the authoritative values.
MIN_DONATION_BRL = 1000.0
MIN_SUPPLIER_SHARE = 0.05
SCORE_SHARE_REFERENCE = 0.25
MANDATE_START = date(2025, 1, 1)  # municipal mandate elected in 2024
MANDATE_END = date(2028, 12, 31)
ELECTIVE_OFFICE = "prefeito"


def _normalize_city(name: Any) -> str:
    """Normalizes a municipality name: uppercase, no accents/punctuation.

    Unlike ``entities.normalize_name`` the tokens are **not** sorted: city
    names are compared as full strings, not as person-name bags.
    """
    if not name:
        return ""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Z0-9 ]", " ", text.upper())
    return " ".join(text.split())


def _digits(value: Any) -> str | None:
    """Keeps only the digits of a document; empty means no document."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits or None


def _amount(value: Any) -> float:
    """Coerces a silver amount (Decimal/str/float) to float; None -> 0."""
    if value is None:
        return 0.0
    return float(value)


def _is_elected_mayor(
    candidacy: dict[str, Any], office: str = ELECTIVE_OFFICE
) -> bool:
    """Whether the candidacy is an elected executive (mayor).

    The totalization status prefix covers "Eleito", "Eleito por QP" and
    "Eleito por média"; "2º turno"/"Não eleito"/"Suplente" do not qualify.
    """
    if (candidacy.get("office") or "").strip().casefold() != office:
        return False
    status = (candidacy.get("totalization_status") or "").strip().casefold()
    return status.startswith("eleito")


def political_connection_signals(
    donations: list[dict[str, Any]],
    contracts: list[dict[str, Any]],
    candidacies: list[dict[str, Any]],
    min_donation_brl: float = MIN_DONATION_BRL,
    min_supplier_share: float = MIN_SUPPLIER_SHARE,
    score_share_reference: float = SCORE_SHARE_REFERENCE,
    mandate_start: date = MANDATE_START,
    mandate_end: date = MANDATE_END,
    office: str = ELECTIVE_OFFICE,
) -> list[dict[str, Any]]:
    """Emits one ``political_connection`` signal per (buyer, supplier, elected).

    Args:
        donations: Silver ``campaign_donations`` rows (``donor_document``,
            ``donor_origin_document``, ``amount``, ``candidate_sequential``,
            ``election_year``).
        contracts: Silver contract rows (``supplier`` with optional
            cnpj/cpf, ``buyer`` with city/uf, ``signature_date``,
            ``amount``).
        candidacies: Silver ``candidacies`` rows (``candidate_sequential``,
            ``office``, ``totalization_status``, ``ue_name``, ``uf``,
            ``election_year``).
        min_donation_brl: Donation floor per donor x elected campaign.
        min_supplier_share: Concentration gate (supplier share of the
            buyer's contracted value within the mandate window).
        score_share_reference: Share that saturates the score at 1.0.
        mandate_start: First signature date of the window (inclusive).
        mandate_end: Last signature date of the window (inclusive).
        office: Elective office of the gate (executive only in v1).

    Returns:
        One signal per (buyer municipality, supplier document, elected
        candidate) passing all five gates; ``details`` carries the amounts
        and the share that motivate the signal. Sorted by (entity id,
        municipality, candidate) for bit-for-bit determinism.
    """
    # Elected gate: (election_year, sequential) -> candidate info.
    elected: dict[tuple[Any, str], dict[str, Any]] = {}
    for candidacy in candidacies:
        sequential = candidacy.get("candidate_sequential")
        if not sequential or not _is_elected_mayor(candidacy, office):
            continue
        elected[(candidacy.get("election_year"), str(sequential))] = {
            "candidate_name": candidacy.get("candidate_name"),
            "party": candidacy.get("party"),
            "ue_name": candidacy.get("ue_name"),
            "uf": candidacy.get("uf"),
        }

    # Donation floor: effective donor (origin donor has priority) x elected.
    municipality_donors: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    totals: dict[tuple[Any, str], dict[str, float]] = {}
    for donation in donations:
        key = (
            donation.get("election_year"),
            str(donation.get("candidate_sequential") or ""),
        )
        if key not in elected:
            continue
        document = _digits(
            donation.get("donor_origin_document") or donation.get("donor_document")
        )
        if document is None:
            continue
        per_donor = totals.setdefault(key, {})
        per_donor[document] = per_donor.get(document, 0.0) + _amount(
            donation.get("amount")
        )
    for key, per_donor in totals.items():
        info = elected[key]
        mun_key = (_normalize_city(info.get("ue_name")), str(info.get("uf") or ""))
        donors = municipality_donors.setdefault(mun_key, {})
        for document, total in per_donor.items():
            if total < min_donation_brl:
                continue
            donors.setdefault(
                document, {"donation_total": 0.0, "candidates": []}
            )
            donors[document]["donation_total"] += total
            donors[document]["candidates"].append(
                {"sequential": key[1], "election_year": key[0], **info}
            )

    # Temporal gate + concentration base: contracts inside the mandate window.
    buyer_totals: dict[tuple[str, str], float] = {}
    supplier_totals: dict[tuple[str, str, str], float] = {}
    supplier_contracts: dict[tuple[str, str, str], set[str]] = {}
    buyer_info: dict[tuple[str, str], dict[str, Any]] = {}
    for contract in contracts:
        signed_on = _as_date(contract.get("signature_date"))
        if signed_on is None or not (mandate_start <= signed_on <= mandate_end):
            continue
        buyer = contract.get("buyer") or {}
        mun_key = (_normalize_city(buyer.get("city")), str(buyer.get("uf") or ""))
        if not mun_key[0] or not mun_key[1]:
            continue
        buyer_info.setdefault(
            mun_key,
            {
                "siafi_code": buyer.get("siafi_code"),
                "name": buyer.get("name"),
                "city": buyer.get("city"),
                "uf": buyer.get("uf"),
            },
        )
        amount = _amount(contract.get("amount"))
        buyer_totals[mun_key] = buyer_totals.get(mun_key, 0.0) + amount
        supplier = contract.get("supplier") or {}
        document = _digits(supplier.get("cnpj") or supplier.get("cpf"))
        if document is None:
            continue
        sup_key = (mun_key[0], mun_key[1], document)
        supplier_totals[sup_key] = supplier_totals.get(sup_key, 0.0) + amount
        supplier_contracts.setdefault(sup_key, set()).add(str(contract.get("id")))

    signals: list[dict[str, Any]] = []
    for (city, uf, document), supplier_total in sorted(supplier_totals.items()):
        mun_key = (city, uf)
        mun_donors = municipality_donors.get(mun_key)
        if mun_donors is None or document not in mun_donors:
            continue
        buyer_total = buyer_totals.get(mun_key, 0.0)
        if buyer_total <= 0:
            continue
        share = supplier_total / buyer_total
        if share < min_supplier_share:
            continue
        donor = mun_donors[document]
        for candidate in donor["candidates"]:
            signals.append(
                {
                    "entity_type": "supplier",
                    "entity_id": document,
                    "signal_type": SignalType.POLITICAL_CONNECTION,
                    "score": round(min(1.0, share / score_share_reference), 4),
                    "details": json.dumps(
                        {
                            "buyer": buyer_info[mun_key],
                            "candidate": candidate,
                            "contracts": len(supplier_contracts[(city, uf, document)]),
                            "contracts_total_brl": round(supplier_total, 2),
                            "buyer_total_brl": round(buyer_total, 2),
                            "donation_total_brl": round(donor["donation_total"], 2),
                            "donor_document": document,
                            "election_year": candidate.get("election_year"),
                            "mandate_start": mandate_start.isoformat(),
                            "mandate_end": mandate_end.isoformat(),
                            "match": "document",
                            "share": round(share, 4),
                        },
                        sort_keys=True,
                    ),
                }
            )
    return signals
