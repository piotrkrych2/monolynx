"""Testy integracyjne -- profil uzytkownika (tokeny API, MCP guide)."""

import uuid

import pytest
from sqlalchemy import select

from open_sentry.models.user_api_token import UserApiToken
from open_sentry.services.mcp_auth import generate_api_token
from tests.conftest import login_session


@pytest.mark.integration
class TestTokensList:
    async def test_tokens_list_requires_auth(self, client):
        """GET /dashboard/profile/tokens bez sesji redirectuje na login."""
        resp = await client.get(
            "/dashboard/profile/tokens", follow_redirects=False
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_tokens_list_empty(self, client, db_session):
        """GET /dashboard/profile/tokens z sesja pokazuje pusta liste."""
        await login_session(client, db_session, email="prof_tokens_1@test.com")
        resp = await client.get("/dashboard/profile/tokens")
        assert resp.status_code == 200

    async def test_tokens_list_shows_token(self, client, db_session):
        """GET /dashboard/profile/tokens wyswietla istniejacy token."""
        await login_session(client, db_session, email="prof_tokens_2@test.com")

        # Tworzymy token przez endpoint
        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "Moj token testowy"},
        )
        assert resp.status_code == 200

        # Sprawdzamy ze lista zawiera token
        resp = await client.get("/dashboard/profile/tokens")
        assert resp.status_code == 200
        assert "Moj token testowy" in resp.text


@pytest.mark.integration
class TestTokenCreate:
    async def test_token_create_requires_auth(self, client):
        """POST /dashboard/profile/tokens/create bez sesji redirectuje na login."""
        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_token_create_success(self, client, db_session):
        """POST z nazwa tworzy token i wyswietla raw token."""
        await login_session(client, db_session, email="prof_tokens_3@test.com")

        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "CI Token"},
        )
        assert resp.status_code == 200
        # Endpoint renderuje strone z new_token (raw token)
        assert "osk_" in resp.text
        assert "CI Token" in resp.text

        # Sprawdzamy w bazie
        result = await db_session.execute(select(UserApiToken))
        tokens = result.scalars().all()
        matching = [t for t in tokens if t.name == "CI Token"]
        assert len(matching) == 1
        assert matching[0].is_active is True

    async def test_token_create_empty_name_redirects(self, client, db_session):
        """POST bez nazwy redirectuje z bledem flash."""
        await login_session(client, db_session, email="prof_tokens_4@test.com")

        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]

    async def test_token_create_whitespace_name_redirects(self, client, db_session):
        """POST z nazwa skladajaca sie z samych spacji redirectuje z bledem."""
        await login_session(client, db_session, email="prof_tokens_5@test.com")

        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "   "},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]


@pytest.mark.integration
class TestTokenRevoke:
    async def test_token_revoke_requires_auth(self, client):
        """POST /dashboard/profile/tokens/{id}/revoke bez sesji redirectuje na login."""
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/profile/tokens/{fake_id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_token_revoke_success(self, client, db_session):
        """POST dezaktywuje aktywny token."""
        await login_session(client, db_session, email="prof_tokens_6@test.com")

        # Tworzymy token
        create_resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "Do revoke"},
        )
        assert create_resp.status_code == 200

        # Pobieramy token z bazy
        result = await db_session.execute(
            select(UserApiToken).where(UserApiToken.name == "Do revoke")
        )
        token = result.scalar_one()
        assert token.is_active is True

        # Revoke
        resp = await client.post(
            f"/dashboard/profile/tokens/{token.id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]

        # Sprawdzamy w bazie ze token jest nieaktywny
        await db_session.refresh(token)
        assert token.is_active is False

    async def test_token_revoke_nonexistent(self, client, db_session):
        """POST z nieistniejacym token_id redirectuje z bledem."""
        await login_session(client, db_session, email="prof_tokens_7@test.com")

        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/profile/tokens/{fake_id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]

    async def test_token_revoke_other_user(self, client, db_session):
        """Uzytkownik nie moze dezaktywowac tokenu innego uzytkownika."""
        # Logujemy pierwszego uzytkownika i tworzymy token
        await login_session(client, db_session, email="prof_tokens_8@test.com")
        create_resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "Cudzy token"},
        )
        assert create_resp.status_code == 200

        result = await db_session.execute(
            select(UserApiToken).where(UserApiToken.name == "Cudzy token")
        )
        other_token = result.scalar_one()

        # Logujemy drugiego uzytkownika
        await login_session(client, db_session, email="prof_tokens_9@test.com")

        # Proba revoke tokenu pierwszego uzytkownika
        resp = await client.post(
            f"/dashboard/profile/tokens/{other_token.id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Token powinien pozostac aktywny
        await db_session.refresh(other_token)
        assert other_token.is_active is True


@pytest.mark.integration
class TestMcpGuide:
    async def test_mcp_guide_requires_auth(self, client):
        """GET /dashboard/profile/mcp-guide bez sesji redirectuje na login."""
        resp = await client.get(
            "/dashboard/profile/mcp-guide", follow_redirects=False
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_mcp_guide_loads(self, client, db_session):
        """GET /dashboard/profile/mcp-guide z sesja zwraca 200 z trescia MCP."""
        await login_session(client, db_session, email="prof_tokens_10@test.com")
        resp = await client.get("/dashboard/profile/mcp-guide")
        assert resp.status_code == 200
        assert "MCP" in resp.text
