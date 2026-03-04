"""Testy integracyjne OAuth 2.1 -- pelen flow autoryzacji."""

import base64
import hashlib
import secrets
import uuid

import pytest

from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.services.auth import hash_password
from monolynx.services.mcp_auth import generate_api_token


def _generate_pkce() -> tuple[str, str]:
    """Generuj pare PKCE (code_verifier, code_challenge) dla S256."""
    code_verifier = secrets.token_urlsafe(48)
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    code_challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return code_verifier, code_challenge


@pytest.mark.integration
class TestOAuthMetadata:
    async def test_metadata_endpoint(self, client):
        """GET /.well-known/oauth-authorization-server zwraca poprawny JSON."""
        response = await client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        data = response.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data
        assert "registration_endpoint" in data
        assert data["response_types_supported"] == ["code"]
        assert "authorization_code" in data["grant_types_supported"]
        assert "refresh_token" in data["grant_types_supported"]
        assert data["code_challenge_methods_supported"] == ["S256"]
        assert data["token_endpoint_auth_methods_supported"] == ["none"]


@pytest.mark.integration
class TestClientRegistration:
    async def test_client_registration(self, client):
        """POST /register rejestruje klienta z poprawnymi redirect URIs."""
        response = await client.post(
            "/register",
            json={
                "client_name": "Test App",
                "redirect_uris": ["http://localhost:3000/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert "client_id" in data
        assert data["client_name"] == "Test App"
        assert data["redirect_uris"] == ["http://localhost:3000/callback"]

    async def test_client_registration_claude_ai(self, client):
        """POST /register z redirect_uri claude.ai -- powinno dzialac."""
        response = await client.post(
            "/register",
            json={
                "client_name": "Claude AI",
                "redirect_uris": ["https://claude.ai/api/mcp/auth_callback"],
                "grant_types": ["authorization_code", "refresh_token"],
            },
        )
        assert response.status_code == 201

    async def test_client_registration_invalid_redirect(self, client):
        """POST /register z niedozwolonym redirect_uri zwraca 400."""
        response = await client.post(
            "/register",
            json={
                "client_name": "Evil App",
                "redirect_uris": ["https://evil.com/callback"],
                "grant_types": ["authorization_code"],
            },
        )
        assert response.status_code == 400
        data = response.json()
        assert data["error"] == "invalid_client_metadata"

    async def test_client_registration_missing_redirect_uris(self, client):
        """POST /register bez redirect_uris zwraca 400."""
        response = await client.post(
            "/register",
            json={
                "client_name": "No Redirect App",
                "redirect_uris": [],
            },
        )
        assert response.status_code == 400


@pytest.mark.integration
class TestAuthorizeEndpoint:
    async def test_authorize_redirects_to_login(self, client, db_session):
        """GET /authorize bez sesji pokazuje formularz logowania."""
        # Najpierw zarejestruj klienta
        reg_response = await client.post(
            "/register",
            json={
                "client_name": "Login Test",
                "redirect_uris": ["http://localhost:9999/callback"],
                "grant_types": ["authorization_code"],
            },
        )
        assert reg_response.status_code == 201
        client_id = reg_response.json()["client_id"]

        _verifier, challenge = _generate_pkce()

        response = await client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost:9999/callback",
                "response_type": "code",
                "state": "test-state-123",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "",
            },
        )
        assert response.status_code == 200
        # Powinien zawierac formularz logowania
        assert "email" in response.text.lower()
        assert "haslo" in response.text.lower()

    async def test_authorize_unknown_client(self, client):
        """GET /authorize z nieznanym client_id zwraca 400."""
        _verifier, challenge = _generate_pkce()

        response = await client.get(
            "/authorize",
            params={
                "client_id": "nieznany_client",
                "redirect_uri": "http://localhost:9999/callback",
                "response_type": "code",
                "state": "test",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert response.status_code == 400


@pytest.mark.integration
class TestFullOAuthFlow:
    async def test_full_oauth_flow(self, client, db_session):
        """Pelny flow: register -> authorize (logowanie) -> token exchange."""
        email = f"oauth-{uuid.uuid4().hex[:8]}@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()

        # 1. Rejestracja klienta
        reg_response = await client.post(
            "/register",
            json={
                "client_name": "Full Flow Test",
                "redirect_uris": ["http://localhost:8888/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
            },
        )
        assert reg_response.status_code == 201
        client_id = reg_response.json()["client_id"]

        code_verifier, code_challenge = _generate_pkce()

        # 2. POST /authorize z logowaniem
        login_response = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:8888/callback",
                "state": "state-abc",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "email": email,
                "password": "testpass123",
                "action": "login",
            },
            follow_redirects=False,
        )
        # Po logowaniu -- redirect na GET /authorize (consent)
        assert login_response.status_code == 303
        redirect_url = login_response.headers["location"]
        assert "/authorize" in redirect_url

        # 3. GET /authorize (consent screen)
        consent_response = await client.get(redirect_url)
        assert consent_response.status_code == 200
        assert "Zezwol" in consent_response.text

        # 4. POST /authorize z consent
        consent_submit = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:8888/callback",
                "state": "state-abc",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "action": "consent",
            },
            follow_redirects=False,
        )
        assert consent_submit.status_code == 302
        callback_url = consent_submit.headers["location"]
        assert "http://localhost:8888/callback" in callback_url
        assert "code=" in callback_url
        assert "state=state-abc" in callback_url

        # Wyodrebnij code z URL
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(callback_url)
        params = parse_qs(parsed.query)
        auth_code = params["code"][0]

        # 5. POST /token -- wymiana code na tokeny
        token_response = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": auth_code,
                "code_verifier": code_verifier,
                "redirect_uri": "http://localhost:8888/callback",
            },
        )
        assert token_response.status_code == 200
        tokens = token_response.json()
        assert "access_token" in tokens
        assert "refresh_token" in tokens
        assert tokens["token_type"] == "bearer"
        assert tokens["expires_in"] == 2592000

    async def test_deny_access(self, client, db_session):
        """Odmowa dostepu -- redirect z error=access_denied."""
        email = f"oauth-deny-{uuid.uuid4().hex[:8]}@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()

        # Zaloguj uzytkownika
        await client.post(
            "/auth/login",
            data={"email": email, "password": "testpass123"},
            follow_redirects=False,
        )

        # Zarejestruj klienta
        reg_response = await client.post(
            "/register",
            json={
                "client_name": "Deny Test",
                "redirect_uris": ["http://localhost:7777/callback"],
                "grant_types": ["authorization_code"],
            },
        )
        client_id = reg_response.json()["client_id"]
        _verifier, challenge = _generate_pkce()

        # Odmow dostepu
        deny_response = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:7777/callback",
                "state": "deny-state",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "action": "deny",
            },
            follow_redirects=False,
        )
        assert deny_response.status_code == 302
        assert "error=access_denied" in deny_response.headers["location"]

    async def test_invalid_login(self, client, db_session):
        """Bledne logowanie w authorize -- formularz z bledem."""
        reg_response = await client.post(
            "/register",
            json={
                "client_name": "Bad Login Test",
                "redirect_uris": ["http://localhost:6666/callback"],
                "grant_types": ["authorization_code"],
            },
        )
        client_id = reg_response.json()["client_id"]
        _verifier, challenge = _generate_pkce()

        response = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:6666/callback",
                "state": "state-bad",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "email": "nonexistent@test.com",
                "password": "wrongpass",
                "action": "login",
            },
        )
        assert response.status_code == 200
        assert "Nieprawidlowy email lub haslo" in response.text


@pytest.mark.integration
class TestRefreshToken:
    async def test_refresh_token(self, client, db_session):
        """Odswiezenie access tokenu za pomoca refresh token."""
        email = f"oauth-refresh-{uuid.uuid4().hex[:8]}@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()

        # Rejestracja klienta
        reg = await client.post(
            "/register",
            json={
                "client_name": "Refresh Test",
                "redirect_uris": ["http://localhost:5555/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
            },
        )
        client_id = reg.json()["client_id"]
        code_verifier, code_challenge = _generate_pkce()

        # Logowanie
        await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:5555/callback",
                "state": "refresh-state",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "email": email,
                "password": "testpass123",
                "action": "login",
            },
            follow_redirects=False,
        )

        # Consent
        consent_response = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:5555/callback",
                "state": "refresh-state",
                "code_challenge": code_challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "action": "consent",
            },
            follow_redirects=False,
        )
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(consent_response.headers["location"])
        auth_code = parse_qs(parsed.query)["code"][0]

        # Token exchange
        token_response = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": auth_code,
                "code_verifier": code_verifier,
                "redirect_uri": "http://localhost:5555/callback",
            },
        )
        tokens = token_response.json()
        refresh_token = tokens["refresh_token"]

        # Refresh
        refresh_response = await client.post(
            "/token",
            data={
                "grant_type": "refresh_token",
                "client_id": client_id,
                "refresh_token": refresh_token,
            },
        )
        assert refresh_response.status_code == 200
        new_tokens = refresh_response.json()
        assert "access_token" in new_tokens
        assert "refresh_token" in new_tokens
        assert new_tokens["access_token"] != tokens["access_token"]
        assert new_tokens["refresh_token"] != tokens["refresh_token"]


@pytest.mark.integration
class TestTokenEndpointErrors:
    async def test_invalid_client_returns_401(self, client):
        """Nieznany client_id w /token zwraca 401."""
        response = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": "nieznany_client",
                "code": "fake_code",
                "code_verifier": "fake_verifier",
                "redirect_uri": "http://localhost/callback",
            },
        )
        assert response.status_code == 401
        assert response.json()["error"] == "invalid_client"

    async def test_missing_client_id(self, client):
        """Brak client_id w /token zwraca 401."""
        response = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "code": "fake",
            },
        )
        assert response.status_code == 401

    async def test_unsupported_grant_type(self, client, db_session):
        """Nieobslugiwany grant_type zwraca 400."""
        reg = await client.post(
            "/register",
            json={
                "client_name": "Grant Error Test",
                "redirect_uris": ["http://localhost:4444/callback"],
                "grant_types": ["authorization_code"],
            },
        )
        client_id = reg.json()["client_id"]

        response = await client.post(
            "/token",
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
            },
        )
        assert response.status_code == 400
        assert response.json()["error"] == "unsupported_grant_type"


@pytest.mark.integration
class TestLegacyTokenStillWorks:
    async def test_legacy_token_still_works(self, db_session):
        """Legacy osk_* token nadal dziala z MCP (verify_mcp_token)."""
        from monolynx.services.mcp_auth import verify_mcp_token

        user = User(
            email=f"legacy-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("testpass"),
        )
        db_session.add(user)
        await db_session.flush()

        raw_token, token_hash = generate_api_token()
        api_token = UserApiToken(
            user_id=user.id,
            token_hash=token_hash,
            token_prefix=raw_token[:8],
            name="Legacy Token",
        )
        db_session.add(api_token)
        await db_session.flush()

        result = await verify_mcp_token(raw_token, db_session)
        assert result is not None
        assert result.id == user.id

    async def test_invalid_token_returns_none(self, db_session):
        """Nieprawidlowy token zwraca None z obu metod walidacji."""
        from monolynx.services.mcp_auth import verify_mcp_token
        from monolynx.services.oauth import verify_oauth_access_token

        result_legacy = await verify_mcp_token("fake_token_12345", db_session)
        assert result_legacy is None

        result_oauth = await verify_oauth_access_token("fake_oauth_token", db_session)
        assert result_oauth is None


# ---------------------------------------------------------------------------
# Authorize GET -- walidacja parametrow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAuthorizeGetValidation:
    async def test_invalid_response_type(self, client, db_session):
        """response_type != 'code' zwraca 400."""
        reg = await client.post(
            "/register",
            json={"client_name": "RT Test", "redirect_uris": ["http://localhost:1111/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]
        _v, challenge = _generate_pkce()

        resp = await client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost:1111/cb",
                "response_type": "token",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 400
        assert "response_type" in resp.text.lower()

    async def test_invalid_code_challenge_method(self, client, db_session):
        """code_challenge_method != 'S256' zwraca 400."""
        reg = await client.post(
            "/register",
            json={"client_name": "CCM Test", "redirect_uris": ["http://localhost:2222/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]
        _v, challenge = _generate_pkce()

        resp = await client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost:2222/cb",
                "response_type": "code",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "plain",
            },
        )
        assert resp.status_code == 400
        assert "S256" in resp.text

    async def test_invalid_redirect_uri(self, client, db_session):
        """redirect_uri spoza allowlist klienta zwraca 400."""
        reg = await client.post(
            "/register",
            json={"client_name": "URI Test", "redirect_uris": ["http://localhost:3333/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]
        _v, challenge = _generate_pkce()

        resp = await client.get(
            "/authorize",
            params={
                "client_id": client_id,
                "redirect_uri": "http://localhost:9999/wrong",
                "response_type": "code",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            },
        )
        assert resp.status_code == 400
        assert "redirect_uri" in resp.text.lower()


# ---------------------------------------------------------------------------
# Authorize POST -- walidacja
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAuthorizePostValidation:
    async def test_unknown_client_id(self, client, db_session):
        """POST /authorize z nieznanym client_id zwraca 400."""
        _v, challenge = _generate_pkce()
        resp = await client.post(
            "/authorize",
            data={
                "client_id": "unknown_client_xyz",
                "redirect_uri": "http://localhost/cb",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "action": "consent",
            },
        )
        assert resp.status_code == 400

    async def test_invalid_redirect_uri_post(self, client, db_session):
        """POST /authorize z redirect_uri spoza allowlist klienta zwraca 400."""
        reg = await client.post(
            "/register",
            json={"client_name": "Post URI Test", "redirect_uris": ["http://localhost:4444/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]
        _v, challenge = _generate_pkce()

        resp = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:9999/wrong",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "action": "consent",
            },
        )
        assert resp.status_code == 400

    async def test_invalid_action(self, client, db_session):
        """POST /authorize z nieprawidlowym action zwraca 400."""
        reg = await client.post(
            "/register",
            json={"client_name": "Action Test", "redirect_uris": ["http://localhost:5550/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]
        _v, challenge = _generate_pkce()

        resp = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:5550/cb",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "action": "unknown_action",
            },
        )
        assert resp.status_code == 400


# ---------------------------------------------------------------------------
# Authorize -- state handling
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAuthorizeStateHandling:
    async def test_consent_without_state(self, client, db_session):
        """Consent bez state -- redirect bez parametru state."""
        email = f"oauth-nostate-{uuid.uuid4().hex[:8]}@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()

        await client.post("/auth/login", data={"email": email, "password": "testpass123"}, follow_redirects=False)

        reg = await client.post(
            "/register",
            json={"client_name": "NoState", "redirect_uris": ["http://localhost:6660/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]
        _v, challenge = _generate_pkce()

        resp = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:6660/cb",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "action": "consent",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "code=" in location
        assert "state=" not in location

    async def test_deny_without_state(self, client, db_session):
        """Deny bez state -- redirect bez parametru state."""
        email = f"oauth-deny-ns-{uuid.uuid4().hex[:8]}@test.com"
        user = User(email=email, password_hash=hash_password("testpass123"))
        db_session.add(user)
        await db_session.flush()
        await client.post("/auth/login", data={"email": email, "password": "testpass123"}, follow_redirects=False)

        reg = await client.post(
            "/register",
            json={"client_name": "DenyNoState", "redirect_uris": ["http://localhost:6670/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]
        _v, challenge = _generate_pkce()

        resp = await client.post(
            "/authorize",
            data={
                "client_id": client_id,
                "redirect_uri": "http://localhost:6670/cb",
                "state": "",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "scope": "",
                "action": "deny",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert "error=access_denied" in location
        assert "state=" not in location


# ---------------------------------------------------------------------------
# Token endpoint -- brakujace parametry
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTokenMissingParams:
    async def test_authorization_code_missing_params(self, client, db_session):
        """Brak code/code_verifier/redirect_uri w grant_type=authorization_code zwraca 400."""
        reg = await client.post(
            "/register",
            json={"client_name": "Missing Params", "redirect_uris": ["http://localhost:7770/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]

        resp = await client.post(
            "/token",
            data={"grant_type": "authorization_code", "client_id": client_id},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    async def test_refresh_token_missing_token(self, client, db_session):
        """Brak refresh_token w grant_type=refresh_token zwraca 400."""
        reg = await client.post(
            "/register",
            json={"client_name": "Missing RT", "redirect_uris": ["http://localhost:7780/cb"], "grant_types": ["authorization_code", "refresh_token"]},
        )
        client_id = reg.json()["client_id"]

        resp = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "client_id": client_id},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_request"

    async def test_refresh_token_invalid_token(self, client, db_session):
        """Nieprawidlowy refresh_token zwraca 400 invalid_grant."""
        reg = await client.post(
            "/register",
            json={"client_name": "Bad RT", "redirect_uris": ["http://localhost:7790/cb"], "grant_types": ["authorization_code", "refresh_token"]},
        )
        client_id = reg.json()["client_id"]

        resp = await client.post(
            "/token",
            data={"grant_type": "refresh_token", "client_id": client_id, "refresh_token": "invalid_refresh_token_xyz"},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"

    async def test_authorization_code_invalid_code(self, client, db_session):
        """Nieprawidlowy authorization code zwraca 400 invalid_grant."""
        reg = await client.post(
            "/register",
            json={"client_name": "Bad Code", "redirect_uris": ["http://localhost:7800/cb"], "grant_types": ["authorization_code"]},
        )
        client_id = reg.json()["client_id"]

        resp = await client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": client_id,
                "code": "invalid_code_xyz",
                "code_verifier": "some_verifier",
                "redirect_uri": "http://localhost:7800/cb",
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "invalid_grant"


# ---------------------------------------------------------------------------
# OAuth service -- edge cases
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestOAuthServiceErrors:
    async def test_register_client_unsupported_grant_type(self, db_session):
        """register_client z unsupported grant_type rzuca ValueError."""
        from monolynx.services.oauth import register_client

        with pytest.raises(ValueError, match="Nieobslugiwany grant_type"):
            await register_client("Test", ["http://localhost:9999/cb"], ["client_credentials"], db_session)

    async def test_create_auth_code_non_s256(self, db_session):
        """create_authorization_code z method != S256 rzuca ValueError."""
        from monolynx.services.oauth import create_authorization_code

        with pytest.raises(ValueError, match="S256"):
            await create_authorization_code(
                client_id="test",
                user_id="user-1",
                redirect_uri="http://localhost/cb",
                scope=None,
                code_challenge="challenge",
                code_challenge_method="plain",
                db=db_session,
            )
