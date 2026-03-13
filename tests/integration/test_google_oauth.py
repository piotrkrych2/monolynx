"""Testy integracyjne -- Google OAuth callback (tworzenie konta, linkowanie)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from monolynx.models.user import User
from monolynx.services.auth import hash_password


def _make_google_userinfo(
    email: str,
    google_id: str | None = None,
    given_name: str = "Jan",
    family_name: str = "Kowalski",
) -> dict:
    return {
        "sub": google_id or uuid.uuid4().hex,
        "email": email,
        "given_name": given_name,
        "family_name": family_name,
    }


def _patch_google_oauth(userinfo: dict):
    """Patch OAuth google.authorize_access_token aby zwrocic userinfo.

    Uzywa create=True bo oauth.google nie istnieje gdy brak GOOGLE_CLIENT_ID (np. w CI).
    """
    mock_token = {"userinfo": userinfo}
    mock_google = AsyncMock()
    mock_google.authorize_access_token = AsyncMock(return_value=mock_token)
    from monolynx.dashboard.auth import oauth

    return patch.object(oauth, "google", mock_google, create=True)


def _patch_google_enabled():
    return patch("monolynx.dashboard.auth._google_enabled", return_value=True)


@pytest.mark.integration
class TestGoogleOAuthNewAccount:
    """Google OAuth -- tworzenie nowego konta."""

    async def test_new_account_is_active(self, client, db_session):
        """Nowe konto utworzone przez Google powinno byc aktywne (is_active=True)."""
        email = "google-new-active@test.com"
        google_id = uuid.uuid4().hex
        userinfo = _make_google_userinfo(email, google_id)

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            resp = await client.get("/auth/google/callback", follow_redirects=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"

        result = await db_session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        assert user.is_active is True
        assert user.google_id == google_id
        assert user.first_name == "Jan"
        assert user.last_name == "Kowalski"

    async def test_new_account_sets_session(self, client, db_session):
        """Po utworzeniu konta przez Google sesja jest ustawiona -- mozna wejsc na dashboard."""
        email = "google-new-session@test.com"
        userinfo = _make_google_userinfo(email)

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            resp = await client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302

        # Sesja aktywna -- dashboard powinien zwrocic 200
        resp2 = await client.get("/dashboard/", follow_redirects=False)
        assert resp2.status_code == 200

    async def test_first_user_becomes_superuser(self, client, db_session):
        """Pierwszy uzytkownik w systemie staje sie superuserem."""
        # Usun wszystkich uzytkownikow (transakcja rollback i tak to cofnie)
        all_users = await db_session.execute(select(User))
        for u in all_users.scalars():
            await db_session.delete(u)
        await db_session.flush()

        email = "google-first-super@test.com"
        userinfo = _make_google_userinfo(email)

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            await client.get("/auth/google/callback", follow_redirects=False)

        result = await db_session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        assert user.is_superuser is True

    async def test_second_user_is_not_superuser(self, client, db_session):
        """Kolejni uzytkownicy nie sa superuserami."""
        # Upewnij sie ze istnieje juz jakis uzytkownik
        existing = User(email="google-existing-for-super@test.com", password_hash=hash_password("pass123"))
        db_session.add(existing)
        await db_session.flush()

        email = "google-second-nosuper@test.com"
        userinfo = _make_google_userinfo(email)

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            await client.get("/auth/google/callback", follow_redirects=False)

        result = await db_session.execute(select(User).where(User.email == email))
        user = result.scalar_one()
        assert user.is_superuser is False


@pytest.mark.integration
class TestGoogleOAuthLinkExisting:
    """Google OAuth -- linkowanie do istniejacego konta."""

    async def test_link_google_to_existing_email(self, client, db_session):
        """Istniejacy uzytkownik z tym samym emailem -- linkuje google_id."""
        email = "google-link@test.com"
        google_id = uuid.uuid4().hex
        user = User(email=email, password_hash=hash_password("pass123"))
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email, google_id, given_name="Piotr", family_name="Nowak")

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            resp = await client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302

        await db_session.refresh(user)
        assert user.google_id == google_id

    async def test_link_fills_empty_names(self, client, db_session):
        """Linkowanie uzupelnia puste imie/nazwisko z Google."""
        email = "google-link-names@test.com"
        user = User(email=email, password_hash=hash_password("pass123"), first_name="", last_name="")
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email, given_name="Adam", family_name="Nowak")

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            await client.get("/auth/google/callback", follow_redirects=False)

        await db_session.refresh(user)
        assert user.first_name == "Adam"
        assert user.last_name == "Nowak"

    async def test_link_does_not_overwrite_existing_names(self, client, db_session):
        """Linkowanie NIE nadpisuje istniejacego imienia/nazwiska."""
        email = "google-link-keep-names@test.com"
        user = User(email=email, password_hash=hash_password("pass123"), first_name="Marek", last_name="Zieliński")
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email, given_name="Inny", family_name="Inny")

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            await client.get("/auth/google/callback", follow_redirects=False)

        await db_session.refresh(user)
        assert user.first_name == "Marek"
        assert user.last_name == "Zieliński"

    async def test_returning_google_user(self, client, db_session):
        """Istniejacy uzytkownik z google_id -- loguje bez tworzenia nowego."""
        email = "google-return@test.com"
        google_id = uuid.uuid4().hex
        user = User(email=email, google_id=google_id, is_active=True)
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email, google_id)

        with _patch_google_enabled(), _patch_google_oauth(userinfo):
            resp = await client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302

        # Nie utworzono nowego uzytkownika
        result = await db_session.execute(select(User).where(User.email == email))
        users = result.scalars().all()
        assert len(users) == 1


@pytest.mark.integration
class TestGoogleOAuthErrors:
    """Google OAuth -- obsluga bledow."""

    async def test_google_disabled_redirects(self, client, db_session):
        """Gdy Google OAuth jest wylaczony, callback redirectuje na login."""
        with patch("monolynx.dashboard.auth._google_enabled", return_value=False):
            resp = await client.get("/auth/google/callback", follow_redirects=False)
        assert resp.status_code == 302
        assert resp.headers["location"] == "/auth/login"

    async def test_oauth_error_shows_login(self, client, db_session):
        """Blad z Google OAuth pokazuje formularz logowania z bledem."""
        from monolynx.dashboard.auth import oauth

        mock_google = AsyncMock()
        mock_google.authorize_access_token = AsyncMock(side_effect=Exception("OAuth error"))

        with _patch_google_enabled(), patch.object(oauth, "google", mock_google, create=True):
            resp = await client.get("/auth/google/callback")
        assert resp.status_code == 200
        assert "Blad logowania przez Google" in resp.text

    async def test_missing_email_shows_error(self, client, db_session):
        """Brak emaila w userinfo pokazuje blad."""
        from monolynx.dashboard.auth import oauth

        mock_google = AsyncMock()
        mock_google.authorize_access_token = AsyncMock(return_value={"userinfo": {"sub": "123"}})

        with _patch_google_enabled(), patch.object(oauth, "google", mock_google, create=True):
            resp = await client.get("/auth/google/callback")
        assert resp.status_code == 200
        assert "Nie udalo sie pobrac danych" in resp.text
