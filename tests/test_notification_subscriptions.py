"""Tests for the municipal subscription notifications (O12).

Responsibility: Validate the signal → municipality match (details payload
and contracts fallback), the confirmation e-mail payload and the
best-effort broadcast to the confirmed subscribers of a published signal.
Offline: the dispatcher and the ArangoDB reads are mocked.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import patch

from capiba.db import subscriptions as db_subscriptions
from capiba.ingestion import geography
from capiba.notification import subscriptions as alerts
from capiba.notification.dispatcher import NotificationChannel, Priority


def _signal(
    signal_type: str = "single_bid",
    entity_type: str = "supplier",
    entity_id: str = "12345678000199",
    score: float = 0.8,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Builds a triage-shaped published signal."""
    return {
        "entity_type": entity_type,
        "entity_id": entity_id,
        "signal_type": signal_type,
        "score": score,
        "details": json.dumps(details or {}),
    }


def _contract(city: str, uf: str, cnpj: str = "12345678000199", siafi: str = "2531") -> dict:
    """Silver-shaped contract row (nested buyer/supplier)."""
    return {
        "buyer": {"siafi_code": siafi, "city": city, "uf": uf},
        "supplier": {"cnpj": cnpj},
    }


class TestResolveSignalIbge:
    """Tests for the signal → municipality match."""

    def test_details_city_uf_wins(self) -> None:
        """city/uf in the details payload resolve directly."""
        signal = _signal(details={"city": "Recife", "uf": "pe"})
        assert alerts.resolve_signal_ibge(signal) == "2611606"

    def test_contracts_fallback_most_frequent_pair(self) -> None:
        """Without details, the most frequent buyer (city, UF) resolves."""
        signal = _signal()
        contracts = [
            _contract("Recife", "PE"),
            _contract("recife", "PE"),
            _contract("Olinda", "PE"),
        ]
        assert alerts.resolve_signal_ibge(signal, contracts) == "2611606"

    def test_buyer_entity_matches_by_siafi(self) -> None:
        """Buyer signals match contracts by buyer.siafi_code."""
        signal = _signal(signal_type="concentration", entity_type="buyer", entity_id="2531")
        contracts = [_contract("Recife", "PE"), _contract("Olinda", "PE", siafi="9999")]
        assert alerts.resolve_signal_ibge(signal, contracts) == "2611606"

    def test_collusion_pair_matches_any_supplier(self) -> None:
        """The ``+``-joined collusion entity id matches any of its CNPJs."""
        signal = _signal(signal_type="collusion_network", entity_id="111+12345678000199")
        assert alerts.resolve_signal_ibge(signal, [_contract("Recife", "PE")]) == "2611606"

    def test_unresolvable_returns_none(self) -> None:
        """Unknown municipalities and empty contexts do not resolve."""
        assert alerts.resolve_signal_ibge(_signal()) is None
        assert (
            alerts.resolve_signal_ibge(_signal(), [_contract("Cidade Inexistente", "XX")])
            is None
        )
        # Supplier mismatch: the contract belongs to another supplier.
        assert (
            alerts.resolve_signal_ibge(_signal(), [_contract("Recife", "PE", cnpj="0")])
            is None
        )

    def test_injectable_lookup(self) -> None:
        """The resolver honors an injected (name, uf) lookup."""
        fake = geography.Municipality(ibge_code="0000000", name="X", uf="XX")
        signal = _signal(details={"city": "X", "uf": "XX"})
        assert alerts.resolve_signal_ibge(signal, lookup=lambda n, u: fake) == "0000000"


class TestSendConfirmation:
    """Tests for the confirmation e-mail."""

    def test_payload_carries_token_links(self) -> None:
        """The confirmation e-mail carries the confirm/unsubscribe links."""
        municipality = geography.lookup_by_ibge("2611606")
        assert municipality is not None
        with patch.object(alerts, "_dispatch", return_value=True) as mock_dispatch:
            assert alerts.send_confirmation("ana@example.org", municipality, "tok123") is True

        alert = mock_dispatch.call_args.args[0]
        assert alert.recipients == ["ana@example.org"]
        assert alert.channel is NotificationChannel.EMAIL
        assert alert.metadata["template"] == "subscription"
        assert "token=tok123" in alert.metadata["confirm_url"]
        assert "token=tok123" in alert.metadata["unsubscribe_url"]
        assert alert.metadata["municipality"] == "Recife"


class TestNotifyPublishedSignal:
    """Tests for the published-signal broadcast."""

    def test_notifies_confirmed_subscribers_individually(self) -> None:
        """One e-mail per confirmed subscriber, linking the evidence package."""
        signal = _signal(details={"city": "Recife", "uf": "PE"})
        with (
            patch.object(
                db_subscriptions,
                "confirmed_emails_by_ibge",
                return_value=["ana@example.org", "bruno@example.org"],
            ),
            patch.object(alerts, "_dispatch", return_value=True) as mock_dispatch,
        ):
            sent = alerts.notify_published_signal(db=None, signal=signal)

        assert sent == 2
        assert mock_dispatch.call_count == 2
        recipients = [c.args[0].recipients for c in mock_dispatch.call_args_list]
        assert recipients == [["ana@example.org"], ["bruno@example.org"]]
        alert = mock_dispatch.call_args.args[0]
        assert alert.priority is Priority.HIGH
        assert (
            alert.metadata["evidence_url"]
            .endswith("/v1/signals/supplier%3A12345678000199%3Asingle_bid/evidence")
        )
        assert alert.metadata["municipality"] == "Recife"

    def test_no_subscribers_sends_nothing(self) -> None:
        """A resolvable municipality without subscribers dispatches nothing."""
        signal = _signal(details={"city": "Recife", "uf": "PE"})
        with (
            patch.object(db_subscriptions, "confirmed_emails_by_ibge", return_value=[]),
            patch.object(alerts, "_dispatch") as mock_dispatch,
        ):
            assert alerts.notify_published_signal(db=None, signal=signal) == 0
        mock_dispatch.assert_not_called()

    def test_unresolvable_municipality_falls_back_to_db(self) -> None:
        """Without details, the contracts are fetched from ArangoDB."""
        signal = _signal()
        with (
            patch.object(
                alerts,
                "fetch_signal_contracts",
                return_value=[_contract("Recife", "PE")],
            ) as mock_fetch,
            patch.object(
                db_subscriptions,
                "confirmed_emails_by_ibge",
                return_value=["ana@example.org"],
            ),
            patch.object(alerts, "_dispatch", return_value=True) as mock_dispatch,
        ):
            sent = alerts.notify_published_signal(db=object(), signal=signal)

        assert sent == 1
        mock_fetch.assert_called_once()
        assert mock_dispatch.call_count == 1

    def test_unresolvable_everywhere_is_counted_and_skipped(self) -> None:
        """A signal with no resolvable municipality notifies nobody."""
        signal = _signal()
        with (
            patch.object(alerts, "fetch_signal_contracts", return_value=[]),
            patch.object(alerts, "_dispatch") as mock_dispatch,
        ):
            assert alerts.notify_published_signal(db=object(), signal=signal) == 0
        mock_dispatch.assert_not_called()

    def test_never_raises(self) -> None:
        """Persistence/dispatch failures are swallowed (best-effort)."""
        signal = _signal(details={"city": "Recife", "uf": "PE"})
        with patch.object(
            db_subscriptions,
            "confirmed_emails_by_ibge",
            side_effect=RuntimeError("arango down"),
        ):
            assert alerts.notify_published_signal(db=None, signal=signal) == 0


class TestDispatch:
    """Tests for the synchronous wrapper over the async dispatcher."""

    def _alert(self) -> Any:
        from capiba.notification.dispatcher import NotificationAlert

        return NotificationAlert(
            title="t",
            message="m",
            priority=Priority.MEDIUM,
            channel=NotificationChannel.EMAIL,
            recipients=["ana@example.org"],
        )

    def test_dispatch_runs_the_async_dispatcher(self) -> None:
        """A successful async dispatch returns True."""

        class _OkDispatcher:
            async def dispatch(self, alert: Any) -> bool:
                return True

        with patch.object(alerts, "NotificationDispatcher", return_value=_OkDispatcher()):
            assert alerts._dispatch(self._alert()) is True

    def test_dispatch_never_raises(self) -> None:
        """A dispatcher failure is logged and reported as False."""

        class _DownDispatcher:
            async def dispatch(self, alert: Any) -> bool:
                raise RuntimeError("smtp down")

        with patch.object(alerts, "NotificationDispatcher", return_value=_DownDispatcher()):
            assert alerts._dispatch(self._alert()) is False


class TestDetailsPayload:
    """Tests for the details parsing (dict passthrough, invalid JSON)."""

    def test_dict_details_pass_through(self) -> None:
        payload = {"city": "Recife", "uf": "PE"}
        assert alerts._details_payload(payload) is payload

    def test_invalid_json_is_empty(self) -> None:
        assert alerts._details_payload("{not json") == {}
        assert alerts._details_payload(None) == {}

    def test_dict_details_resolve_the_signal(self) -> None:
        """A signal whose details are already a mapping still resolves."""
        signal = _signal()
        signal["details"] = {"city": "Recife", "uf": "PE"}
        assert alerts.resolve_signal_ibge(signal) == "2611606"


class TestFetchSignalContracts:
    """Tests for the ArangoDB contract fetch used by the geo fallback."""

    def test_empty_entity_id_fetches_nothing(self) -> None:
        with patch.object(alerts, "execute_aql") as mock_aql:
            assert alerts.fetch_signal_contracts(object(), _signal(entity_id="")) == []
        mock_aql.assert_not_called()

    def test_buyer_entity_filters_by_siafi(self) -> None:
        signal = _signal(entity_type="buyer", entity_id="2531")
        with patch.object(alerts, "execute_aql", return_value=[]) as mock_aql:
            assert alerts.fetch_signal_contracts(object(), signal) == []
        query = mock_aql.call_args.args[1]
        assert "c.buyer.siafi_code IN @ids" in query
        assert mock_aql.call_args.args[2] == {"ids": ["2531"]}

    def test_supplier_entity_filters_by_documents(self) -> None:
        signal = _signal(entity_id="111+12345678000199")
        with patch.object(alerts, "execute_aql", return_value=[]) as mock_aql:
            alerts.fetch_signal_contracts(object(), signal)
        query = mock_aql.call_args.args[1]
        assert "c.supplier.cnpj IN @ids" in query
        assert mock_aql.call_args.args[2] == {"ids": ["111", "12345678000199"]}
