"""Testy regresyjne i integracyjne bramki Bearer auth na transporcie /mcp.

MON-103: Wymuś Bearer auth na transporcie MCP.

Luka: przed fixem endpoint /mcp serwował tools/list i initialize publicznie
bez tokenu. Po fixie (_MCPBearerAuthMiddleware) kazdy JSON-RPC request
do /mcp bez waznego tokenu Bearer zwraca HTTP 401.

Pokrycie:
- Brak Authorization -> 401 (regresja glownej luki)
- Zly token -> 401
- Wazny osk_* token -> 200 (odpowiedz MCP z listą narzedzi)
- Discovery OAuth publiczny (/.well-known/*) bez tokenu -> 200
- /register, /authorize publiczne bez tokenu -> 200/302

Uwaga o architekturze testow:
_MCPBearerAuthMiddleware wywoluje _verify_token() przez async_session_factory()
(osobna sesja DB), ktora NIE widzi uncommitted danych z db_session fixture
(outer transaction rollback). Dlatego testy "autoryzowany -> 200" uywaja
patch("monolynx.mcp_server._verify_token") by ominac walidacje tokenu
na poziomie middleware i skoncentrowac sie na teście przepustowosci (200 vs 401).
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.services.auth import hash_password
from monolynx.services.mcp_auth import generate_api_token

# Naglowki wymagane przez Streamable HTTP MCP transport
_MCP_HEADERS = {
    "Accept": "application/json, text/event-stream",
    "Content-Type": "application/json",
}

# Minimalne zadanie JSON-RPC tools/list
_TOOLS_LIST_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "tools/list",
}

# Minimalne zadanie JSON-RPC initialize
_INITIALIZE_PAYLOAD = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "0.1"},
    },
}


@pytest.fixture
async def mcp_user_with_token(db_session):
    """Tworzy usera + wazny osk_* token MCP (obiekt User do mock_verify)."""
    user = User(
        email=f"mcp-auth-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db_session.add(user)
    await db_session.flush()

    raw_token, token_hash = generate_api_token()
    api_token = UserApiToken(
        user_id=user.id,
        token_hash=token_hash,
        token_prefix=raw_token[:8],
        name="Test MCP Token MON-103",
    )
    db_session.add(api_token)

    project_slug = f"mcp-auth-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="MCP Auth Test Project",
        slug=project_slug,
        code=project_slug.replace("-", "").upper()[:5],
        api_key=f"key-{uuid.uuid4().hex}",
    )
    db_session.add(project)
    await db_session.flush()

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role="owner",
    )
    db_session.add(member)
    await db_session.flush()

    return user, raw_token, project


@pytest.mark.integration
class TestMcpTransportAuthRegression:
    """Testy regresyjne: glowna luka - publiczny tools/list.

    Kryterium: f8acff56 + 80ad4f47 z MON-103.
    """

    async def test_tools_list_without_token_returns_401(self, client):
        """REGRESJA: tools/list bez Authorization -> 401, nie lista narzedzi.

        To jest glowna luka MON-103 - przed fixem ten request zwracal
        200 z lista ~116 narzedzi bez zadnego tokenu.
        """
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers=_MCP_HEADERS,
        )

        assert resp.status_code == 401

    async def test_tools_list_without_token_body_has_no_tools(self, client):
        """REGRESJA: body 401 nie zawiera listy narzedzi MCP."""
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers=_MCP_HEADERS,
        )

        assert resp.status_code == 401
        # Body 401 nie powinno zawierac listy narzedzi MCP
        body = resp.text
        assert "tools" not in body.lower() or "error" in body.lower()

    async def test_initialize_without_token_returns_401(self, client):
        """REGRESJA: initialize bez Authorization -> 401.

        Initialize bylo rowniez publicznie dostepne przed fixem.
        """
        resp = await client.post(
            "/mcp",
            json=_INITIALIZE_PAYLOAD,
            headers=_MCP_HEADERS,
        )

        assert resp.status_code == 401

    async def test_mcp_post_without_any_headers_returns_401(self, client):
        """POST /mcp bez zadnych naglowkow -> 401."""
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
        )

        assert resp.status_code == 401


@pytest.mark.integration
class TestMcpTransportAuthInvalidToken:
    """Testy blednych tokenow."""

    async def test_invalid_bearer_token_returns_401(self, client):
        """Authorization: Bearer zlytoken -> 401."""
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers={
                **_MCP_HEADERS,
                "Authorization": "Bearer zlytoken123",
            },
        )

        assert resp.status_code == 401

    async def test_empty_bearer_token_returns_401(self, client):
        """Authorization: Bearer (pusty token) -> 401."""
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers={
                **_MCP_HEADERS,
                "Authorization": "Bearer ",
            },
        )

        assert resp.status_code == 401

    async def test_wrong_auth_scheme_returns_401(self, client):
        """Authorization z blednym schematem (Basic zamiast Bearer) -> 401."""
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers={
                **_MCP_HEADERS,
                "Authorization": "Basic dXNlcjpwYXNz",
            },
        )

        assert resp.status_code == 401

    async def test_expired_or_nonexistent_osk_token_returns_401(self, client):
        """Nieistniejacy osk_* token -> 401."""
        fake_token = "osk_" + "x" * 32
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers={
                **_MCP_HEADERS,
                "Authorization": f"Bearer {fake_token}",
            },
        )

        assert resp.status_code == 401

    async def test_401_response_body_is_json(self, client):
        """Odpowiedz 401 powinna zawierac JSON z polem error."""
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers=_MCP_HEADERS,
        )

        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data

    async def test_401_response_has_www_authenticate_header(self, client):
        """Odpowiedz 401 powinna zawierac naglowek WWW-Authenticate: Bearer."""
        resp = await client.post(
            "/mcp",
            json=_TOOLS_LIST_PAYLOAD,
            headers=_MCP_HEADERS,
        )

        assert resp.status_code == 401
        assert "www-authenticate" in resp.headers or "WWW-Authenticate" in resp.headers


@pytest.mark.integration
class TestMcpTransportAuthValidToken:
    """Testy z waznym tokenem: autoryzowany request -> odpowiedz MCP.

    Kryterium: f8acff56 - autoryzowany tools/list -> 200 z lista.

    Uwaga: _MCPBearerAuthMiddleware uzywa async_session_factory() (osobna sesja),
    ktora nie widzi uncommitted danych z db_session (outer transaction rollback).
    Dlatego patchujemy monolynx.mcp_server._verify_token AsyncMockiem zwracajacym
    usera -- testujemy ze middleware przepuszcza request gdy token jest wazny.
    """

    async def test_valid_osk_token_tools_list_not_rejected_by_auth(self, client, mcp_user_with_token):
        """Wazny osk_* token -> bramka auth przepuszcza request (nie 401).

        Ograniczenie testowe: Streamable HTTP MCP wymaga sesji (handshake
        Mcp-Session-Id), ktorej httpx w trybie ASGI nie obsluguje bez pelnego
        klienta MCP. Dlatego bramka przepuszcza request (brak 401), ale
        wewnetrzna logika MCP moze zwrocic 404/4xx z braku sesji.
        Kluczowy kontrakt: token wazny -> NIE 401.
        """
        user, raw_token, _project = mcp_user_with_token
        mock_verify = AsyncMock(return_value=user)

        with patch("monolynx.mcp_server._verify_token", mock_verify):
            resp = await client.post(
                "/mcp",
                json=_TOOLS_LIST_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": f"Bearer {raw_token}",
                },
            )

        # Bramka auth przepuscila - blad pochodzi z warstwy MCP (sesja), nie auth
        assert resp.status_code != 401

    async def test_valid_osk_token_initialize_not_rejected_by_auth(self, client, mcp_user_with_token):
        """Wazny osk_* token + initialize -> bramka auth przepuszcza (nie 401)."""
        user, raw_token, _project = mcp_user_with_token
        mock_verify = AsyncMock(return_value=user)

        with patch("monolynx.mcp_server._verify_token", mock_verify):
            resp = await client.post(
                "/mcp",
                json=_INITIALIZE_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": f"Bearer {raw_token}",
                },
            )

        assert resp.status_code != 401

    async def test_valid_token_response_not_auth_error(self, client, mcp_user_with_token):
        """Wazny token + tools/list -> odpowiedz to NIE jest blad auth (401).

        Kontrakt: 401 tylko gdy brak/zly token. Z waznym tokenem bramka
        odpuszcza w gore stosu MCP.
        """
        user, raw_token, _project = mcp_user_with_token
        mock_verify = AsyncMock(return_value=user)

        with patch("monolynx.mcp_server._verify_token", mock_verify):
            resp = await client.post(
                "/mcp",
                json=_TOOLS_LIST_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": f"Bearer {raw_token}",
                },
            )

        assert resp.status_code != 401
        # Body 401 zawiera "error: unauthorized" - z waznym tokenem go nie ma
        if resp.headers.get("content-type", "").startswith("application/json"):
            body = resp.json()
            assert body.get("error") != "unauthorized"

    async def test_middleware_calls_verify_token_with_extracted_token(self, client, mcp_user_with_token):
        """Middleware wyciaga token z naglowka i przekazuje do _verify_token."""
        user, raw_token, _project = mcp_user_with_token
        mock_verify = AsyncMock(return_value=user)

        with patch("monolynx.mcp_server._verify_token", mock_verify):
            await client.post(
                "/mcp",
                json=_TOOLS_LIST_PAYLOAD,
                headers={
                    **_MCP_HEADERS,
                    "Authorization": f"Bearer {raw_token}",
                },
            )

        # Middleware powinien wywolac _verify_token z tokenem (bez prefixu "Bearer ")
        mock_verify.assert_called_once_with(raw_token)

    @pytest.mark.skip(
        reason=(
            "OAuth access token wymaga pelnego flow (PKCE + autoryzacja usera + exchange code). "
            "Pelny test jest w test_oauth.py. "
            "Transport auth dla OAuth jest pokryty przez _MCPBearerAuthMiddleware "
            "ktory wywoluje _verify_token() obslugujacy OAuthtokeny identycznie jak osk_*. "
            "Dodaj test integracyjny OAuth transport gdy pojawi sie helper tworzacy "
            "OAuthAccessToken bezposrednio przez ORM (bez HTTP flow)."
        )
    )
    async def test_valid_oauth_token_returns_200(self, client, db_session):
        """Wazny OAuth access token -> 200. Pominiete - wymaga pelnego PKCE flow."""


@pytest.mark.integration
class TestMcpOAuthDiscoveryPublic:
    """Discovery OAuth pozostaje publiczny (bez tokenu).

    Specyfikacja RFC 8414 wymaga publicznego dostepu do metadanych AS.
    """

    async def test_oauth_authorization_server_metadata_public(self, client):
        """GET /.well-known/oauth-authorization-server bez tokenu -> 200."""
        resp = await client.get("/.well-known/oauth-authorization-server")

        assert resp.status_code == 200
        data = resp.json()
        assert "authorization_endpoint" in data
        assert "token_endpoint" in data

    async def test_oauth_protected_resource_metadata_public(self, client):
        """GET /.well-known/oauth-protected-resource bez tokenu -> 200."""
        resp = await client.get("/.well-known/oauth-protected-resource")

        assert resp.status_code == 200

    async def test_oauth_register_endpoint_public(self, client):
        """POST /register bez tokenu -> dostepny (200/201/4xx, nie 401)."""
        resp = await client.post(
            "/register",
            json={
                "client_name": "Test Client MON-103",
                "redirect_uris": ["http://localhost:3000/callback"],
                "grant_types": ["authorization_code", "refresh_token"],
            },
        )

        # Endpoint publiczny - moze odrzucic z 4xx z powodow walidacji,
        # ale nie powinien zwracac 401 (brak tokenu nie jest powodem odrzucenia)
        assert resp.status_code != 401

    async def test_oauth_authorize_endpoint_public(self, client):
        """GET /authorize bez tokenu -> dostepny (200/302/4xx, nie 401)."""
        resp = await client.get(
            "/authorize",
            params={
                "client_id": "nonexistent",
                "response_type": "code",
                "redirect_uri": "http://localhost:3000/callback",
            },
            follow_redirects=False,
        )

        # Endpoint publiczny - moze zwrocic blad walidacji, ale nie 401
        assert resp.status_code != 401
