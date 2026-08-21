"""Tests for the Capiba dashboard portal.

Responsibility: Validate the landing page, the SSO redirect flow
(Keycloak mocked) and the stats degradation path (no real
Keycloak, lake or database).
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from capiba import config
from capiba.api import portal, services
from capiba.api.main import app
from capiba.pipeline import lake

USERINFO = {"preferred_username": "capiba", "email": "capiba@capiba.local"}
_FAKE_KEYCLOAK_ISSUER = "http://keycloak.capiba.local:8088/realms/capiba"
_FAKE_KEYCLOAK_METADATA: dict[str, Any] = {
    "issuer": _FAKE_KEYCLOAK_ISSUER,
    "authorization_endpoint": (f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/auth"),
    "token_endpoint": f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/token",
    "userinfo_endpoint": (f"{_FAKE_KEYCLOAK_ISSUER}/protocol/openid-connect/userinfo"),
}


class _FakeMetadataResponse:
    """Minimal httpx response stub for the OIDC metadata endpoint."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


@pytest.fixture
def client() -> TestClient:
    """Fixture: API test client (keeps session cookies across requests)."""
    return TestClient(app)


@pytest.fixture
def keycloak_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: fake Keycloak OIDC metadata so register_keycloak needs no network."""
    monkeypatch.setattr(config, "KEYCLOAK_ISSUER", _FAKE_KEYCLOAK_ISSUER)
    monkeypatch.setattr(
        httpx,
        "get",
        lambda _url, **_kwargs: _FakeMetadataResponse(_FAKE_KEYCLOAK_METADATA),
    )


@pytest.fixture
def sso_enabled(keycloak_metadata: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: SSO enabled with a registered Keycloak client."""
    monkeypatch.setattr(config, "SSO_ENABLED", True)
    portal.register_keycloak(portal.oauth)


@pytest.fixture
def keycloak_token(keycloak_metadata: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: Keycloak access-token exchange returning a fixed userinfo."""

    async def fake_authorize_access_token(request: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"userinfo": USERINFO}

    portal.register_keycloak(portal.oauth)
    monkeypatch.setattr(
        portal.oauth.keycloak,
        "authorize_access_token",
        fake_authorize_access_token,
    )


def _mock_stats_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mocks every portal stats collector with deterministic values."""
    monkeypatch.setattr(lake, "count_silver_contracts", lambda: 3)
    monkeypatch.setattr(lake, "count_fraud_signals", lambda: 7)
    monkeypatch.setattr(portal, "count_political_connections", lambda: 2)
    monkeypatch.setattr(portal, "count_high_cri_contracts", lambda: 1)
    monkeypatch.setattr(portal, "count_contracts_last_30d", lambda: 30)
    monkeypatch.setattr(portal, "latest_duplicate_ids", lambda: 0)
    monkeypatch.setattr(portal, "latest_cpu_idle_ratio", lambda: 55.5)
    monkeypatch.setattr(portal, "latest_memory_idle_ratio", lambda: 62.3)
    monkeypatch.setattr(portal, "energy_last_24h_wh", lambda: 120.5)


@pytest.fixture
def stats_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: lake stats available with deterministic counts."""
    _mock_stats_ok(monkeypatch)


@pytest.fixture
def stats_down(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fixture: every stats source raises (lake down)."""

    def _raise() -> Any:
        raise ConnectionError("lake is down")

    monkeypatch.setattr(lake, "count_silver_contracts", _raise)
    monkeypatch.setattr(lake, "count_fraud_signals", _raise)
    monkeypatch.setattr(portal, "count_political_connections", _raise)
    monkeypatch.setattr(portal, "count_high_cri_contracts", _raise)
    monkeypatch.setattr(portal, "count_contracts_last_30d", _raise)
    monkeypatch.setattr(portal, "latest_duplicate_ids", _raise)
    monkeypatch.setattr(portal, "latest_cpu_idle_ratio", _raise)
    monkeypatch.setattr(portal, "latest_memory_idle_ratio", _raise)
    monkeypatch.setattr(portal, "energy_last_24h_wh", _raise)


@pytest.mark.usefixtures("stats_ok")
class TestPortalWithoutSso:
    """Tests for the portal with SSO disabled (default)."""

    def test_portal_ok(self, client: TestClient) -> None:
        """The landing page must render with the service links and stats."""
        response = client.get("/")
        assert response.status_code == 200
        body = response.text
        assert "Capiba" in body
        for service in portal.SERVICES:
            assert service["name"] in body
            assert service["url"] in body
        assert ">3<" in body
        assert ">7<" in body
        for group in ("Detecção", "Ingestão", "Plataforma"):
            assert group in body
        assert ">120.5 Wh<" in body
        assert ">55.5%<" in body

    def test_login_redirects_home_when_sso_disabled(self, client: TestClient) -> None:
        """/auth/login is a no-op redirect when SSO is disabled."""
        response = client.get("/auth/login", follow_redirects=False)
        assert response.status_code == 307
        assert response.headers["location"] == "/"

    def test_services_circle(self, client: TestClient) -> None:
        """Services must render as circle segments with tooltip data."""
        response = client.get("/")
        body = response.text
        assert 'class="services-circle"' in body
        assert body.count('class="seg"') == len(portal.SERVICES)
        assert 'id="seg-tip"' in body
        for service in portal.SERVICES:
            assert service["description"] in body


@pytest.mark.usefixtures("sso_enabled", "stats_ok")
class TestPortalWithSso:
    """Tests for the portal with SSO enabled."""

    def test_anonymous_redirected_to_login(self, client: TestClient) -> None:
        """An anonymous user must be redirected to /auth/login."""
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    def test_callback_logs_user_in(
        self, client: TestClient, keycloak_token: None
    ) -> None:
        """After the OIDC callback the page must show the logged username."""
        response = client.get("/auth/callback")
        assert response.status_code == 200
        assert "Logado como" in response.text
        assert USERINFO["preferred_username"] in response.text

    def test_logout_clears_session(
        self, client: TestClient, keycloak_token: None
    ) -> None:
        """Logout must clear the session and redirect back to the login."""
        client.get("/auth/callback")
        response = client.get("/auth/logout", follow_redirects=False)
        assert response.status_code == 307
        response = client.get("/", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"


@pytest.mark.usefixtures("stats_down")
class TestPortalStatsFailure:
    """Stats sources down must degrade to 'indisponível', never break."""

    def test_page_still_renders(self, client: TestClient) -> None:
        """The page must render 200 with unavailable stats."""
        response = client.get("/")
        assert response.status_code == 200
        assert "indisponível" in response.text


class TestStatsHelpers:
    """Unit tests for the stats collection helpers."""

    def test_count_fraud_signals(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The fraud-signals count must use the ArangoDB read path."""
        monkeypatch.setattr(services, "get_capiba_db", lambda: object())
        monkeypatch.setattr(portal, "execute_aql", lambda db, query, b=None: [42])
        assert portal._count_fraud_signals() == 42

    def test_gold_scalar_returns_int_for_counts(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Gold scalars normalize integral floats to ints (display)."""
        monkeypatch.setattr(portal.trino, "run_query", lambda sql: [{"n": 42.0}])
        assert portal.count_political_connections() == 42

    def test_gold_scalar_none_on_empty(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """An empty result set degrades the metric to None."""
        monkeypatch.setattr(portal.trino, "run_query", lambda sql: [])
        assert portal.count_high_cri_contracts() is None

    def test_latest_duplicate_ids_queries_latest_day(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The quality metric reads only the latest partition."""
        captured: dict[str, str] = {}

        def _run_query(sql: str) -> list[dict[str, Any]]:
            captured["sql"] = sql
            return [{"n": 3}]

        monkeypatch.setattr(portal.trino, "run_query", _run_query)
        assert portal.latest_duplicate_ids() == 3
        assert "ORDER BY dt DESC LIMIT 1" in captured["sql"]

    def test_energy_last_24h_wh_parses_prometheus(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The energy card parses the instant-vector Prometheus response."""

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, Any]:
                return {"data": {"result": [{"value": [0, "432.87"]}]}}

        monkeypatch.setattr(portal.httpx, "get", lambda *a, **k: _Resp())
        assert portal.energy_last_24h_wh() == 432.9

    def test_energy_last_24h_wh_none_on_empty(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No Kepler series degrades the energy card to None."""

        class _Resp:
            def raise_for_status(self) -> None:
                pass

            def json(self) -> dict[str, Any]:
                return {"data": {"result": []}}

        monkeypatch.setattr(portal.httpx, "get", lambda *a, **k: _Resp())
        assert portal.energy_last_24h_wh() is None

    def test_collect_stats_partial_failure(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A single failing source must degrade only its own stat."""

        def _raise() -> Any:
            raise ConnectionError("down")

        _mock_stats_ok(monkeypatch)
        monkeypatch.setattr(lake, "count_silver_contracts", _raise)
        monkeypatch.setattr(lake, "count_fraud_signals", lambda: 5)

        stats = portal.collect_stats()
        assert stats["ingestion"]["contracts"] is None
        assert stats["detection"]["fraud_signals"] == 5
        assert stats["platform"]["energy_wh"] == 120.5

    def test_register_keycloak_idempotent(
        self, keycloak_metadata: None, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Re-registering the Keycloak client must not raise."""
        monkeypatch.setattr(
            config, "KEYCLOAK_PUBLIC_ISSUER", config.KEYCLOAK_PUBLIC_ISSUER
        )
        portal.register_keycloak(portal.oauth)
        assert portal.oauth.keycloak is not None


class _FakeCollection:
    """Dict-backed stand-in for an ArangoDB collection."""

    def __init__(self) -> None:
        self.docs: dict[str, dict[str, Any]] = {}

    def get(self, key: str) -> dict[str, Any] | None:
        return self.docs.get(key)

    def insert(self, doc: dict[str, Any], silent: bool = False) -> dict[str, Any]:
        self.docs[doc["_key"]] = dict(doc)
        return dict(doc)

    def update(self, doc: dict[str, Any]) -> dict[str, Any]:
        self.docs[doc["_key"]].update(doc)
        return dict(self.docs[doc["_key"]])


class _FakeDb:
    """Minimal ArangoDB stand-in wired to a single fake collection."""

    def __init__(self) -> None:
        self.col = _FakeCollection()

    def has_collection(self, name: str) -> bool:
        return True

    def create_collection(self, name: str) -> None:
        pass

    def collection(self, name: str) -> _FakeCollection:
        return self.col


_TRIAGE_SIGNAL = {
    "entity_type": "supplier",
    "entity_id": "12345678000199",
    "signal_type": "single_bid",
    "score": 0.8,
    "details": '{"contracts": 4}',
}
_TRIAGE_KEY = "supplier:12345678000199:single_bid"


@pytest.fixture
def triage_db(monkeypatch: pytest.MonkeyPatch) -> _FakeDb:
    """Fixture: fake ArangoDB wired to the portal triage read/write paths."""
    from capiba.db import triage

    db = _FakeDb()
    monkeypatch.setattr(services, "get_db", lambda: db)
    monkeypatch.setattr(triage, "execute_aql", _fake_triage_aql)
    return db


def _fake_triage_aql(
    db: _FakeDb, query: str, bind_vars: dict[str, Any] | None = None
) -> list[Any]:
    """Applies the listing/count AQL semantics (filters, sort, limit) to
    the fake collection, mirroring what ArangoDB would do server-side."""
    bind_vars = bind_vars or {}
    docs = list(db.col.docs.values())
    if "status" in bind_vars:
        docs = [d for d in docs if d.get("status") == bind_vars["status"]]
    if "signal_type" in bind_vars:
        docs = [d for d in docs if d.get("signal_type") == bind_vars["signal_type"]]
    if "min_score" in bind_vars:
        docs = [d for d in docs if (d.get("score") or 0) >= bind_vars["min_score"]]
    if "COLLECT WITH COUNT" in query:
        return [len(docs)]
    docs.sort(
        key=lambda d: (d.get("score") or 0, str(d.get("last_seen") or "")),
        reverse=True,
    )
    offset = bind_vars.get("offset", 0)
    limit = bind_vars.get("limit", len(docs))
    return docs[offset : offset + limit]


@pytest.mark.usefixtures("stats_ok")
class TestPortalTriage:
    """Tests for the /triage editorial page (SSO disabled)."""

    def test_triage_lists_pending_signals(
        self, client: TestClient, triage_db: _FakeDb
    ) -> None:
        """The queue must render the pending signals and the reviewer bar."""
        from capiba.db import triage

        triage.register_signals(triage_db, [_TRIAGE_SIGNAL])

        response = client.get("/triage")

        assert response.status_code == 200
        assert "single_bid" in response.text
        assert "12345678000199" in response.text
        assert 'id="reviewer-input"' in response.text

    def test_triage_db_down_degrades(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """ArangoDB down must render the page with an unavailable notice."""

        def _raise() -> Any:
            raise ConnectionError("arango down")

        monkeypatch.setattr(services, "get_db", _raise)

        response = client.get("/triage")

        assert response.status_code == 200
        assert "indisponível" in response.text

    def test_triage_filters_by_signal_type_and_min_score(
        self, client: TestClient, triage_db: _FakeDb
    ) -> None:
        """The UI filters must narrow the queue server-side."""
        from capiba.db import triage

        triage.register_signals(
            triage_db,
            [
                _TRIAGE_SIGNAL,
                {
                    **_TRIAGE_SIGNAL,
                    "signal_type": "concentration",
                    "entity_id": "99999999000100",
                    "score": 0.3,
                },
            ],
        )

        response = client.get(
            "/triage?signal_type=concentration&min_score=0.2"
        )

        assert response.status_code == 200
        assert "concentration" in response.text
        assert "99999999000100" in response.text
        assert "12345678000199" not in response.text

    def test_triage_paginates_over_real_total(
        self, client: TestClient, triage_db: _FakeDb
    ) -> None:
        """Page 2 must exist when the filtered total exceeds the page size."""
        from capiba.db import triage

        triage.register_signals(
            triage_db,
            [
                {**_TRIAGE_SIGNAL, "entity_id": f"{i:014d}", "score": 0.5 + i / 1000}
                for i in range(150)
            ],
        )

        page1 = client.get("/triage")
        page2 = client.get("/triage?page=2")

        assert page1.status_code == 200
        assert "(150)" in page1.text
        assert "Próxima" in page1.text
        assert page2.status_code == 200
        assert "Página 2 de 2" in page2.text
        assert "Anterior" in page2.text

    def test_review_uses_form_reviewer(
        self, client: TestClient, triage_db: _FakeDb
    ) -> None:
        """The reviewer from the form must be recorded on the transition."""
        from capiba.db import triage

        triage.register_signals(triage_db, [_TRIAGE_SIGNAL])

        response = client.post(
            "/triage/review",
            data={
                "key": _TRIAGE_KEY,
                "status": "confirmed",
                "reviewer": "ana",
                "filter": "pending_review",
            },
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/triage?status=pending_review"
        doc = triage_db.col.docs[_TRIAGE_KEY]
        assert doc["status"] == "confirmed"
        assert doc["reviewed_by"] == "ana"

    def test_review_without_reviewer_shows_error(
        self, client: TestClient, triage_db: _FakeDb
    ) -> None:
        """A transition without reviewer must bounce back with an error."""
        from capiba.db import triage

        triage.register_signals(triage_db, [_TRIAGE_SIGNAL])

        response = client.post(
            "/triage/review",
            data={"key": _TRIAGE_KEY, "status": "confirmed", "reviewer": " "},
        )

        assert response.status_code == 200
        assert 'class="banner"' in response.text
        assert triage_db.col.docs[_TRIAGE_KEY]["status"] == "pending_review"

    def test_review_unknown_key_shows_error(
        self, client: TestClient, triage_db: _FakeDb
    ) -> None:
        """An unknown signal key must bounce back with an error."""
        response = client.post(
            "/triage/review",
            data={"key": "supplier:0:single_bid", "status": "confirmed",
                  "reviewer": "ana"},
        )

        assert response.status_code == 200
        assert 'class="banner"' in response.text

    def test_triage_metrics_render(
        self, client: TestClient, triage_db: _FakeDb
    ) -> None:
        """The precision report must render per operator."""
        from capiba.db import triage

        triage.register_signals(triage_db, [_TRIAGE_SIGNAL])
        triage.apply_review(
            triage_db, _TRIAGE_KEY, triage.TriageStatus.CONFIRMED, "ana"
        )

        response = client.get("/triage?status=confirmed")

        assert response.status_code == 200
        assert "Precisão por operador" in response.text
        assert "100%" in response.text


@pytest.mark.usefixtures("sso_enabled", "stats_ok")
class TestPortalTriageSso:
    """Tests for the /triage page with SSO enabled."""

    def test_anonymous_redirected_to_login(self, client: TestClient) -> None:
        """An anonymous user must be redirected to /auth/login."""
        response = client.get("/triage", follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    def test_review_falls_back_to_session_user(
        self,
        client: TestClient,
        keycloak_token: None,
        triage_db: _FakeDb,
    ) -> None:
        """Without a form reviewer, the SSO username is used."""
        from capiba.db import triage

        triage.register_signals(triage_db, [_TRIAGE_SIGNAL])
        client.get("/auth/callback")

        response = client.post(
            "/triage/review",
            data={"key": _TRIAGE_KEY, "status": "confirmed", "reviewer": ""},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert triage_db.col.docs[_TRIAGE_KEY]["reviewed_by"] == "capiba"
