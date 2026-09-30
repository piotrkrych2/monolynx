"""Testy integracyjne GET /api/v2/me (MON-125).

Specyfikacja: src/monolynx/api/v2/me.py definiuje MeResponse(id, email) chroniony
przez get_api_user (Bearer token, wspoldzielony z MCP -- osk_* i OAuth access
token). Endpoint jeszcze nie istnieje w chwili pisania tych testow (TDD, red
phase). Kontrakt: patrz interface.md ticketa MON-125 (sekcja 3) i
tests/integration/test_api_v2.py (wzorce tworzenia usera/tokenu dla dependency
get_api_user, ktore ten test reuzywa 1:1).
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from monolynx.models.oauth import OAuthAccessToken
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.services.auth import hash_password
from monolynx.services.mcp_auth import generate_api_token

pytestmark = pytest.mark.integration


async def _create_user(db_session, email: str) -> User:
    user = User(email=email, password_hash=hash_password("testpass123"))
    db_session.add(user)
    await db_session.flush()
    return user


class TestMeSuccess:
    async def test_osk_token_returns_id_and_email_of_that_user(self, client, db_session):
        user = await _create_user(db_session, "mon125-osk@example.com")
        other = await _create_user(db_session, "mon125-osk-other@example.com")
        raw_token, token_hash = generate_api_token()
        db_session.add(
            UserApiToken(
                user_id=user.id,
                token_hash=token_hash,
                token_prefix=raw_token[:8],
                name="MON-125 me token",
            )
        )
        await db_session.flush()

        response = await client.get("/api/v2/me", headers={"Authorization": f"Bearer {raw_token}"})

        assert response.status_code == 200
        body = response.json()
        assert body == {"id": str(user.id), "email": user.email}
        assert body["id"] != str(other.id)

    async def test_oauth_access_token_returns_id_and_email_of_that_user(self, client, db_session):
        user = await _create_user(db_session, "mon125-oauth@example.com")
        raw_access = "oauth-raw-" + uuid.uuid4().hex
        db_session.add(
            OAuthAccessToken(
                token_hash=hashlib.sha256(raw_access.encode()).hexdigest(),
                client_id="mlx_test_client",
                user_id=user.id,
                scope=None,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db_session.flush()

        response = await client.get("/api/v2/me", headers={"Authorization": f"Bearer {raw_access}"})

        assert response.status_code == 200
        assert response.json() == {"id": str(user.id), "email": user.email}


class TestMeUnauthorized:
    async def test_missing_authorization_header_returns_401_problem_json(self, client):
        response = await client.get("/api/v2/me")

        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_invalid_token_returns_401_problem_json(self, client):
        response = await client.get("/api/v2/me", headers={"Authorization": "Bearer osk_nieprawidlowy"})

        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["www-authenticate"] == "Bearer"
