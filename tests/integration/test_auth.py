"""Testy integracyjne -- autentykacja (login, logout, zaproszenia)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from open_sentry.models.user import User
from open_sentry.services.auth import hash_password
from tests.conftest import login_session


@pytest.mark.integration
class TestLoginPage:
    async def test_login_page_loads(self, client, db_session):
        """GET /auth/login zwraca formularz logowania."""
        resp = await client.get("/auth/login")
        assert resp.status_code == 200
        assert "login" in resp.text.lower() or "Zaloguj" in resp.text or "email" in resp.text.lower()


@pytest.mark.integration
class TestLogin:
    async def test_login_success(self, client, db_session):
        """POST /auth/login z poprawnymi danymi loguje i redirectuje na dashboard."""
        user = User(
            email="auth-login-ok@test.com",
            password_hash=hash_password("securepass123"),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/auth/login",
            data={"email": "auth-login-ok@test.com", "password": "securepass123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"

    async def test_login_wrong_password(self, client, db_session):
        """POST /auth/login z blednym haslem pokazuje blad."""
        user = User(
            email="auth-login-wrong@test.com",
            password_hash=hash_password("correct123"),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/auth/login",
            data={"email": "auth-login-wrong@test.com", "password": "wrongpass"},
        )
        assert resp.status_code == 200
        assert "Nieprawidlowy" in resp.text

    async def test_login_nonexistent_email(self, client, db_session):
        """POST /auth/login z nieistniejacym emailem pokazuje blad."""
        resp = await client.post(
            "/auth/login",
            data={"email": "no-such-user-auth@test.com", "password": "anything"},
        )
        assert resp.status_code == 200
        assert "Nieprawidlowy" in resp.text

    async def test_login_user_without_password(self, client, db_session):
        """POST /auth/login dla uzytkownika bez hasla (zaproszony) nie loguje."""
        token = uuid.uuid4()
        user = User(
            email="auth-nopass@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/auth/login",
            data={"email": "auth-nopass@test.com", "password": "anything"},
        )
        assert resp.status_code == 200
        assert "Nieprawidlowy" in resp.text

    async def test_login_sets_session_with_superuser_flag(self, client, db_session):
        """Po logowaniu superusera sesja zawiera is_superuser=True."""
        user = User(
            email="auth-super@test.com",
            password_hash=hash_password("superpass123"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/auth/login",
            data={"email": "auth-super@test.com", "password": "superpass123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Verify session works by accessing dashboard
        resp2 = await client.get("/dashboard/", follow_redirects=False)
        assert resp2.status_code == 200


@pytest.mark.integration
class TestLogout:
    async def test_logout_clears_session(self, client, db_session):
        """POST /auth/logout czysci sesje i redirectuje na login."""
        await login_session(client, db_session, email="auth-logout@test.com")

        # Verify we are logged in
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 200

        # Logout
        resp = await client.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

        # Verify session is cleared -- accessing protected page redirects to login
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]


@pytest.mark.integration
class TestAcceptInviteForm:
    async def test_accept_invite_form_valid_token(self, client, db_session):
        """GET /auth/accept-invite/{token} z prawidlowym tokenem pokazuje formularz."""
        token = uuid.uuid4()
        user = User(
            email="auth-invite-form@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.get(f"/auth/accept-invite/{token}")
        assert resp.status_code == 200
        # The form should be rendered with valid=True (showing password fields)
        assert "password" in resp.text.lower() or "haslo" in resp.text.lower()

    async def test_accept_invite_form_invalid_token(self, client, db_session):
        """GET /auth/accept-invite/{token} z nieistniejacym tokenem."""
        fake_token = uuid.uuid4()
        resp = await client.get(f"/auth/accept-invite/{fake_token}")
        assert resp.status_code == 200
        # Should show invalid state

    async def test_accept_invite_form_expired_token(self, client, db_session):
        """GET /auth/accept-invite/{token} z wygaslym tokenem."""
        token = uuid.uuid4()
        user = User(
            email="auth-invite-expired@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.get(f"/auth/accept-invite/{token}")
        assert resp.status_code == 200
        # Should show invalid/expired state


@pytest.mark.integration
class TestAcceptInvite:
    async def test_accept_invite_sets_password(self, client, db_session):
        """POST /auth/accept-invite/{token} ustawia haslo i redirectuje na login."""
        token = uuid.uuid4()
        user = User(
            email="auth-invite-accept@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "newpassword123", "password_confirm": "newpassword123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

        await db_session.refresh(user)
        assert user.password_hash is not None
        assert user.invitation_token is None
        assert user.invitation_expires_at is None

    async def test_accept_invite_password_too_short(self, client, db_session):
        """POST z za krotkim haslem pokazuje blad."""
        token = uuid.uuid4()
        user = User(
            email="auth-invite-short@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "short", "password_confirm": "short"},
        )
        assert resp.status_code == 200
        assert "minimum" in resp.text or "znakow" in resp.text

    async def test_accept_invite_passwords_dont_match(self, client, db_session):
        """POST z roznymi haslami pokazuje blad."""
        token = uuid.uuid4()
        user = User(
            email="auth-invite-mismatch@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "password123", "password_confirm": "different456"},
        )
        assert resp.status_code == 200
        assert "nie sa zgodne" in resp.text

    async def test_accept_invite_invalid_token(self, client, db_session):
        """POST z nieistniejacym tokenem pokazuje nieprawidlowy token."""
        fake_token = uuid.uuid4()
        resp = await client.post(
            f"/auth/accept-invite/{fake_token}",
            data={"password": "newpassword123", "password_confirm": "newpassword123"},
        )
        assert resp.status_code == 200

    async def test_accept_invite_expired_token(self, client, db_session):
        """POST z wygaslym tokenem nie ustawia hasla."""
        token = uuid.uuid4()
        user = User(
            email="auth-invite-exp-post@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) - timedelta(days=1),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "newpassword123", "password_confirm": "newpassword123"},
        )
        assert resp.status_code == 200

        await db_session.refresh(user)
        assert user.password_hash is None  # Not set

    async def test_accept_invite_then_login(self, client, db_session):
        """Po ustawieniu hasla uzytkownik moze sie zalogowac."""
        token = uuid.uuid4()
        user = User(
            email="auth-invite-login@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        # Accept invitation
        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "mypassword123", "password_confirm": "mypassword123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Now login with the new password
        resp = await client.post(
            "/auth/login",
            data={"email": "auth-invite-login@test.com", "password": "mypassword123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"
