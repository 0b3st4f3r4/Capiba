"""Tests for the API vertical slice.

Responsibility: Validate the signals and ranking REST endpoints,
with the ArangoDB access layer mocked (no live database).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import numpy as np
import pytest
from authlib.integrations.starlette_client import OAuth
from fastapi.testclient import TestClient

from capiba import config
from capiba.api import portal, services
from capiba.api.main import app
from capiba.detection import signals as signal_ops

CNPJ = "12345678000195"


@pytest.fixture
def client() -> TestClient:
    """Fixture: API test client."""
    return TestClient(app)


def _doc(
    id_: str,
    amount: str,
    modality: str,
    siafi: str = "123456",
    city: str = "Belo Horizonte",
    uf: str = "MG",
    validity_start: str = "2026-02-01",
    validity_end: str = "2026-12-31",
) -> dict[str, Any]:
    """Contract document in the format persisted in ArangoDB."""
    return {
        "_key": id_,
        "id": id_,
        "process_number": f"P{id_}/2026",
        "subject": "Aquisição de material de escritório",
        "amount": amount,
        "signature_date": "2026-02-01",
        "validity_start": validity_start,
        "validity_end": validity_end,
        "buyer": {
            "siafi_code": siafi,
            "name": "Prefeitura Municipal de Exemplo",
            "government_level": "municipal",
            "uf": uf,
            "city": city,
        },
        "supplier": {
            "cnpj": CNPJ,
            "legal_name": "Fornecedora Exemplo Ltda",
        },
        "modality": modality,
        "status": "concluido",
    }


@pytest.fixture
def contracts_by_cnpj() -> list[dict[str, Any]]:
    """Fixture: suspicious contracts (dispensa only, single buyer)."""
    amounts = ["15000.00", "23000.00", "48000.00", "90000.00"]
    return [
        _doc(f"C{i:03d}", amount, "dispensa") for i, amount in enumerate(amounts, 1)
    ]


def _fake_execute_aql(
    contracts: list[dict[str, Any]],
    ranking_rows: list[dict[str, Any]] | None = None,
) -> Callable[[Any, str, dict[str, Any] | None], list[dict[str, Any]]]:
    """Factory for an execute_aql fake routed by bind_vars."""

    def fake(
        db: Any,
        query: str,
        bind_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        bind_vars = bind_vars or {}
        if "cnpj" in bind_vars:
            return [c for c in contracts if c["supplier"]["cnpj"] == bind_vars["cnpj"]]
        if "siafi_code" in bind_vars:
            return [
                c
                for c in contracts
                if c["buyer"]["siafi_code"] == bind_vars["siafi_code"]
            ]
        return ranking_rows or []

    return fake


@pytest.fixture
def db_with_signals(
    monkeypatch: pytest.MonkeyPatch,
    contracts_by_cnpj: list[dict[str, Any]],
) -> None:
    """Fixture: mocked database with suspicious contracts for the CNPJ."""
    monkeypatch.setattr(services, "get_capiba_db", lambda: object())
    monkeypatch.setattr(services, "execute_aql", _fake_execute_aql(contracts_by_cnpj))


@pytest.fixture
def db_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: mocked database without contracts."""
    monkeypatch.setattr(services, "get_capiba_db", lambda: object())
    monkeypatch.setattr(services, "execute_aql", _fake_execute_aql([]))


@pytest.fixture
def db_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: unavailable database (connection failure)."""

    def _connection_failure() -> Any:
        raise ConnectionError("ArangoDB is down")

    monkeypatch.setattr(services, "get_capiba_db", _connection_failure)


@pytest.fixture
def db_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: mocked database with ranking aggregations."""
    rows = [
        {
            "municipality": "Belo Horizonte",
            "uf": "MG",
            "total_contracts": 10,
            "total_value": 100000.0,
            "non_competitive_count": 8,
            "hhi": 0.9,
        },
        {
            "municipality": "Contagem",
            "uf": "MG",
            "total_contracts": 20,
            "total_value": 50000.0,
            "non_competitive_count": 2,
            "hhi": 0.2,
        },
    ]
    monkeypatch.setattr(services, "get_capiba_db", lambda: object())
    monkeypatch.setattr(services, "execute_aql", _fake_execute_aql([], rows))


class TestHealth:
    """Tests for the health check endpoint."""

    def test_health_ok(self, client: TestClient) -> None:
        """Health check must return 200."""
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


class TestLifespan:
    """Tests for the API lifespan (notification scheduler activation)."""

    def test_scheduler_started_and_stopped_with_app(self) -> None:
        """The lifespan must start the scheduler on boot and stop it cleanly."""
        scheduler = MagicMock()
        with (
            patch(
                "capiba.api.main.start_notification_scheduler",
                return_value=scheduler,
            ) as mock_start,
            TestClient(app) as client,
        ):
            response = client.get("/health")
            assert response.status_code == 200
            mock_start.assert_called_once_with()
        scheduler.stop.assert_called_once()

    def test_no_scheduler_is_a_noop(self) -> None:
        """Without recipients (None) the lifespan must not stop anything."""
        with (
            patch(
                "capiba.api.main.start_notification_scheduler", return_value=None
            ),
            TestClient(app) as client,
        ):
            assert client.get("/health").status_code == 200


@pytest.mark.usefixtures("db_with_signals")
class TestSignalsWithData:
    """Tests for the /v1/signals/{cnpj} endpoint with suspicious contracts."""

    def test_signals_detected(self, client: TestClient) -> None:
        """Operators must run and the index must be greater than zero."""
        response = client.get(f"/v1/signals/{CNPJ}")
        assert response.status_code == 200
        data = response.json()
        assert data["entity"] == CNPJ
        assert data["risk_index"] > 0
        assert data["alert"] is True
        types = {s["type"] for s in data["signals"]}
        assert "single_bid" in types
        assert "concentration" in types
        for signal in data["signals"]:
            assert 0.0 <= signal["score"] <= 1.0


@pytest.mark.usefixtures("db_empty")
class TestSignalsWithoutData:
    """Tests for the /v1/signals/{cnpj} endpoint without contracts."""

    def test_signals_empty(self, client: TestClient) -> None:
        """A CNPJ without contracts must return 200 with an empty response."""
        response = client.get(f"/v1/signals/{CNPJ}")
        assert response.status_code == 200
        data = response.json()
        assert data["entity"] == CNPJ
        assert data["signals"] == []
        assert data["risk_index"] == 0.0
        assert data["alert"] is False


class TestSignalsErrors:
    """Error tests for the /v1/signals/{cnpj} endpoint."""

    @pytest.mark.usefixtures("db_unavailable")
    def test_signals_database_unavailable(self, client: TestClient) -> None:
        """Unavailable database must return 503 with a clear detail."""
        response = client.get(f"/v1/signals/{CNPJ}")
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]

    def test_signals_invalid_cnpj(self, client: TestClient) -> None:
        """Invalid CNPJ must return a validation error."""
        response = client.get("/v1/signals/123")
        assert response.status_code == 422


@pytest.mark.usefixtures("db_ranking")
class TestRanking:
    """Tests for the /v1/ranking/municipalities endpoint."""

    def test_ranking_sorted_by_risk(self, client: TestClient) -> None:
        """Ranking must come sorted by descending risk."""
        response = client.get("/v1/ranking/municipalities")
        assert response.status_code == 200
        data = response.json()
        assert "period_start" in data
        assert "period_end" in data
        ranking = data["ranking"]
        assert len(ranking) == 2
        indices = [item["risk_index"] for item in ranking]
        assert indices == sorted(indices, reverse=True)
        first = ranking[0]
        assert first["municipality"] == "Belo Horizonte"
        assert first["uf"] == "MG"
        assert first["total_contracts"] == 10
        assert float(first["total_value"]) == 100000.0
        assert 0.0 <= first["risk_index"] <= 1.0

    def test_ranking_with_uf(self, client: TestClient) -> None:
        """UF filter must work."""
        response = client.get("/v1/ranking/municipalities?uf=MG")
        assert response.status_code == 200

    def test_ranking_with_limit(self, client: TestClient) -> None:
        """The limit parameter must truncate the result."""
        response = client.get("/v1/ranking/municipalities?limit=1")
        assert response.status_code == 200
        assert len(response.json()["ranking"]) == 1


class TestRankingErrors:
    """Error tests for the /v1/ranking/municipalities endpoint."""

    @pytest.mark.usefixtures("db_unavailable")
    def test_ranking_database_unavailable(self, client: TestClient) -> None:
        """Unavailable database must return 503 with a clear detail."""
        response = client.get("/v1/ranking/municipalities")
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]


@pytest.mark.usefixtures("db_ranking")
class TestRankingValidation:
    """Validation tests for the /v1/ranking/municipalities query parameters."""

    def test_ranking_limit_too_low(self, client: TestClient) -> None:
        """Limit below 1 must be rejected."""
        response = client.get("/v1/ranking/municipalities?limit=0")
        assert response.status_code == 422

    def test_ranking_limit_too_high(self, client: TestClient) -> None:
        """Limit above 1000 must be rejected."""
        response = client.get("/v1/ranking/municipalities?limit=1001")
        assert response.status_code == 422

    def test_ranking_limit_invalid_type(self, client: TestClient) -> None:
        """Non-integer limit must be rejected."""
        response = client.get("/v1/ranking/municipalities?limit=abc")
        assert response.status_code == 422

    def test_ranking_period_invalid_date(self, client: TestClient) -> None:
        """Invalid date format must be rejected."""
        response = client.get(
            "/v1/ranking/municipalities?period_start=not-a-date&period_end=2026-01-01"
        )
        assert response.status_code == 422


def _buyer_pool(siafi: str = "123456", suppliers: int = 5) -> list[dict[str, Any]]:
    """Buyer contracts evenly split across suppliers (HHI below threshold)."""
    docs = []
    for i in range(suppliers):
        doc = _doc(f"B{i:03d}", "1000.00", "pregao_eletronico", siafi=siafi)
        doc["supplier"] = {"cnpj": f"99999999000{i:03d}", "legal_name": f"Supplier {i}"}
        docs.append(doc)
    return docs


@pytest.fixture
def db_competitive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: mocked database where no contract reaches a signal threshold."""
    supplier_contracts = [
        _doc(f"C{i:03d}", f"{1000 * i}.00", "pregao_eletronico") for i in range(1, 4)
    ]
    pool = _buyer_pool()

    def fake(
        db: Any,
        query: str,
        bind_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        bind_vars = bind_vars or {}
        if "cnpj" in bind_vars:
            return supplier_contracts
        if "siafi_code" in bind_vars:
            return pool
        return []

    monkeypatch.setattr(services, "get_capiba_db", lambda: object())
    monkeypatch.setattr(services, "execute_aql", fake)


@pytest.fixture
def db_benford_amounts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: mocked database with amounts deviating from Benford's Law."""
    contracts = [
        _doc(f"C{i:03d}", f"9{i:03d}.00", "pregao_eletronico") for i in range(1, 13)
    ]
    monkeypatch.setattr(services, "get_capiba_db", lambda: object())
    monkeypatch.setattr(services, "execute_aql", _fake_execute_aql(contracts))


@pytest.fixture
def db_duration_outlier(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: mocked database with one anomalous contract validity."""
    short = [
        _doc(
            f"C{i:03d}",
            "1000.00",
            "pregao_eletronico",
            validity_start="2026-01-01",
            validity_end="2026-01-31",
        )
        for i in range(1, 5)
    ]
    outlier = _doc(
        "C005",
        "1000.00",
        "pregao_eletronico",
        validity_start="2026-01-01",
        validity_end="2027-05-06",
    )
    contracts = [*short, outlier]
    pool = _buyer_pool()

    def fake(
        db: Any,
        query: str,
        bind_vars: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        bind_vars = bind_vars or {}
        if "cnpj" in bind_vars:
            return contracts
        if "siafi_code" in bind_vars:
            return pool
        return []

    monkeypatch.setattr(services, "get_capiba_db", lambda: object())
    monkeypatch.setattr(services, "execute_aql", fake)


@pytest.fixture
def db_query_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: database connects but every AQL query fails."""
    monkeypatch.setattr(services, "get_capiba_db", lambda: object())

    def _fail(db: Any, query: str, bind_vars: dict[str, Any] | None = None) -> Any:
        raise RuntimeError("AQL execution failed")

    monkeypatch.setattr(services, "execute_aql", _fail)


@pytest.mark.usefixtures("db_competitive")
class TestSignalsBelowThresholds:
    """Tests for contracts that do not reach any signal threshold."""

    def test_no_signals_zero_index(self, client: TestClient) -> None:
        """Competitive contracts with low HHI must return an empty response."""
        response = client.get(f"/v1/signals/{CNPJ}")
        assert response.status_code == 200
        data = response.json()
        assert data["entity"] == CNPJ
        assert data["signals"] == []
        assert data["risk_index"] == 0.0
        assert data["alert"] is False


@pytest.mark.usefixtures("db_benford_amounts")
class TestSignalsAnomalousPrice:
    """Tests for the Benford deviation branch of the price signal."""

    def test_benford_detected(self, client: TestClient) -> None:
        """Amounts deviating from Benford's Law must emit anomalous_price."""
        response = client.get(f"/v1/signals/{CNPJ}")
        assert response.status_code == 200
        signals = {s["type"]: s for s in response.json()["signals"]}
        assert "anomalous_price" in signals
        assert signals["anomalous_price"]["score"] > 0
        assert "Benford" in signals["anomalous_price"]["evidence"]


@pytest.mark.usefixtures("db_duration_outlier")
class TestSignalsAnomalousDuration:
    """Tests for the IQR outlier branch of the duration signal."""

    def test_duration_outlier_detected(self, client: TestClient) -> None:
        """A validity outlier must emit the anomalous_duration signal."""
        response = client.get(f"/v1/signals/{CNPJ}")
        assert response.status_code == 200
        data = response.json()
        assert {s["type"] for s in data["signals"]} == {"anomalous_duration"}
        assert data["risk_index"] == 0.2
        assert data["alert"] is False


@pytest.mark.usefixtures("db_query_failure")
class TestQueryFailure:
    """Tests for AQL failures with an available database connection."""

    def test_signals_query_failure_returns_503(self, client: TestClient) -> None:
        """AQL failures must be converted into HTTP 503."""
        response = client.get(f"/v1/signals/{CNPJ}")
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]

    def test_ranking_query_failure_returns_503(self, client: TestClient) -> None:
        """AQL failures on the ranking endpoint must also return 503."""
        response = client.get("/v1/ranking/municipalities")
        assert response.status_code == 503
        assert "unavailable" in response.json()["detail"]


class TestPortalAuth:
    """Tests for the portal OIDC flow and session handling."""

    _FAKE_ISSUER = "http://keycloak.capiba.local:8088/realms/capiba"
    _FAKE_METADATA: dict[str, Any] = {
        "issuer": _FAKE_ISSUER,
        "authorization_endpoint": f"{_FAKE_ISSUER}/protocol/openid-connect/auth",
        "token_endpoint": f"{_FAKE_ISSUER}/protocol/openid-connect/token",
        "userinfo_endpoint": f"{_FAKE_ISSUER}/protocol/openid-connect/userinfo",
    }

    @pytest.fixture
    def sso_enabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Enables SSO and registers a fake Keycloak client."""
        monkeypatch.setattr(config, "SSO_ENABLED", True)
        monkeypatch.setattr(config, "KEYCLOAK_ISSUER", self._FAKE_ISSUER)
        monkeypatch.setattr(config, "KEYCLOAK_PUBLIC_ISSUER", self._FAKE_ISSUER)
        monkeypatch.setattr(config, "KEYCLOAK_CLIENT_ID", "capiba-portal")
        monkeypatch.setattr(config, "KEYCLOAK_CLIENT_SECRET", "secret")

        metadata = self._FAKE_METADATA

        class _FakeResponse:
            @staticmethod
            def json() -> dict[str, Any]:
                return metadata

        monkeypatch.setattr(httpx, "get", lambda _url, **_kwargs: _FakeResponse())
        # Swap the module-level OAuth client so routes use the fake IdP.
        oauth_client = OAuth()
        oauth_client.register(
            name="keycloak",
            client_id=config.KEYCLOAK_CLIENT_ID,
            client_secret=config.KEYCLOAK_CLIENT_SECRET,
            authorize_url=self._FAKE_METADATA["authorization_endpoint"],
            client_kwargs={"scope": "openid profile email"},
        )
        monkeypatch.setattr(portal, "oauth", oauth_client)

    @pytest.mark.usefixtures("sso_enabled")
    def test_login_redirects_to_keycloak(self, client: TestClient) -> None:
        """When SSO is on, /auth/login must redirect to the IdP."""
        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 302
        location = response.headers["location"]
        assert self._FAKE_ISSUER in location

    def test_login_sso_disabled_redirects_home(self, client: TestClient) -> None:
        """When SSO is off, /auth/login must redirect to the portal root."""
        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code in {302, 307}
        assert response.headers["location"] == "/"

    @pytest.mark.usefixtures("sso_enabled")
    def test_callback_stores_user_and_redirects(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Callback must validate the token, store userinfo and redirect home."""
        userinfo = {"sub": "u1", "email": "dev@capiba.local"}

        async def _fake_authorize(
            _request: Any, claims_options: dict[str, Any] | None = None
        ) -> dict[str, Any]:
            return {"userinfo": userinfo}

        monkeypatch.setattr(
            portal.oauth.keycloak, "authorize_access_token", _fake_authorize
        )

        response = client.get("/auth/callback", follow_redirects=False)
        assert response.status_code in {302, 307}
        assert response.headers["location"] == "/"

    def test_logout_clears_session(self, client: TestClient) -> None:
        """Logout must clear the session and redirect to the portal."""
        response = client.get("/auth/logout", follow_redirects=False)
        assert response.status_code in {302, 307}
        assert response.headers["location"] == "/"


class TestPortalStats:
    """Tests for the portal statistics helpers."""

    def test_count_fraud_signals_returns_length(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """_count_fraud_signals must return the value from ArangoDB."""
        monkeypatch.setattr(services, "get_capiba_db", lambda: object())

        def _fake_execute(
            _db: Any, _query: str, _bind_vars: dict[str, Any] | None = None
        ) -> list[Any]:
            return [42]

        monkeypatch.setattr(portal, "execute_aql", _fake_execute)
        assert portal._count_fraud_signals() == 42


class TestKeycloakRegistration:
    """Tests for the Keycloak OIDC client registration."""

    def test_register_keycloak_rewrites_public_authorization_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The registered client must use the public HTTPS authorization URL."""
        internal_issuer = "http://keycloak-internal:8080/realms/capiba"
        public_issuer = "https://keycloak.capiba.local:8443/realms/capiba"
        metadata: dict[str, Any] = {
            "issuer": internal_issuer,
            "authorization_endpoint": f"{internal_issuer}/protocol/openid-connect/auth",
            "token_endpoint": f"{internal_issuer}/protocol/openid-connect/token",
        }

        class _FakeResponse:
            @staticmethod
            def json() -> dict[str, Any]:
                return metadata

        monkeypatch.setattr(httpx, "get", lambda _url, **_kwargs: _FakeResponse())
        monkeypatch.setattr(config, "KEYCLOAK_ISSUER", internal_issuer)
        monkeypatch.setattr(config, "KEYCLOAK_PUBLIC_ISSUER", public_issuer)

        oauth_client = OAuth()
        portal.register_keycloak(oauth_client)

        client = oauth_client.create_client("keycloak")
        assert client is not None
        assert client.authorize_url == f"{public_issuer}/protocol/openid-connect/auth"

    def test_register_keycloak_unchanged_when_endpoint_does_not_match(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If the advertised endpoint does not match the internal issuer, keep it."""
        internal_issuer = "http://keycloak-internal:8080/realms/capiba"
        external_endpoint = "https://idp.example.com/auth"
        metadata: dict[str, Any] = {
            "issuer": internal_issuer,
            "authorization_endpoint": external_endpoint,
            "token_endpoint": f"{internal_issuer}/protocol/openid-connect/token",
        }

        class _FakeResponse:
            @staticmethod
            def json() -> dict[str, Any]:
                return metadata

        monkeypatch.setattr(httpx, "get", lambda _url, **_kwargs: _FakeResponse())
        monkeypatch.setattr(config, "KEYCLOAK_ISSUER", internal_issuer)
        monkeypatch.setattr(
            config, "KEYCLOAK_PUBLIC_ISSUER", "https://keycloak.capiba.local:8443/realms/capiba"
        )

        oauth_client = OAuth()
        portal.register_keycloak(oauth_client)

        client = oauth_client.create_client("keycloak")
        assert client is not None
        assert client.authorize_url == external_endpoint


class TestSignalHelpers:
    """Unit tests for the private helpers of the service layer."""

    def test_float_amount_invalid_returns_zero(self) -> None:
        """Non-numeric amounts must fall back to 0.0."""
        assert services._float_amount({"amount": "not-a-number"}) == 0.0
        assert services._float_amount({"amount": {"value": 1}}) == 0.0

    def test_float_amount_valid(self) -> None:
        """Numeric strings and missing amounts must convert cleanly."""
        assert services._float_amount({"amount": "1234.56"}) == 1234.56
        assert services._float_amount({}) == 0.0

    def test_duration_days_invalid_returns_none(self) -> None:
        """Missing or malformed validity dates must return None."""
        assert services._duration_days({}) is None
        assert (
            services._duration_days(
                {"validity_start": "nope", "validity_end": "2026-01-01"}
            )
            is None
        )

    def test_duration_days_valid(self) -> None:
        """Valid ISO dates must produce the duration in days."""
        doc = {"validity_start": "2026-01-01", "validity_end": "2026-01-31"}
        assert services._duration_days(doc) == 30.0

    def test_anomalous_price_isolation_forest(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An IsolationForest anomaly rate above the threshold emits a signal."""
        contracts = [
            _doc(f"C{i:03d}", f"{1000 + i}.00", "pregao_eletronico")
            for i in range(1, 10)
        ]
        contracts += [
            _doc(f"C{i:03d}", "0", "pregao_eletronico") for i in range(10, 16)
        ]

        class FakeModel:
            """Model stub flagging 20% of the rows as anomalies."""

            def predict(self, features: Any) -> Any:
                return np.array([-1, -1, -1] + [1] * 12)

        monkeypatch.setattr(signal_ops, "train_if", lambda features: FakeModel())

        signal = services._signal_anomalous_price(contracts)

        assert signal is not None
        assert signal.score == 0.2
        assert "IsolationForest" in (signal.evidence or "")

    def test_anomalous_duration_below_min_returns_none(self) -> None:
        """Fewer than _MIN_DURATION valid durations must not emit a signal."""
        contracts = [
            _doc(f"C{i:03d}", "1000.00", "pregao_eletronico") for i in range(3)
        ]
        assert services._signal_anomalous_duration(contracts) is None
