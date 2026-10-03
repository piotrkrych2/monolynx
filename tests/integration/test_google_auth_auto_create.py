"""Testy integracyjne -- Google OAuth: przelacznik GOOGLE_AUTO_CREATE_USERS i wymog email_verified (MON-235)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.config import Settings, settings
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.integration.test_google_oauth import _make_google_userinfo, _patch_google_enabled, _patch_google_oauth

MSG_NO_ACCOUNT = "Konto nie istnieje. Poproś administratora o zaproszenie."
MSG_UNVERIFIED = "Adres e-mail konta Google nie jest zweryfikowany."


def _auto_create(value: bool):
    return patch.object(settings, "GOOGLE_AUTO_CREATE_USERS", value)


async def _callback(client, userinfo: dict, auto_create: bool | None = None):
    """Wywoluje callback Google; auto_create=None zostawia domyslna wartosc settings."""
    with _patch_google_enabled(), _patch_google_oauth(userinfo):
        if auto_create is None:
            return await client.get("/auth/google/callback", follow_redirects=False)
        with _auto_create(auto_create):
            return await client.get("/auth/google/callback", follow_redirects=False)


async def _find_user(db_session, email: str) -> User | None:
    result = await db_session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def _assert_logged_in(client) -> None:
    resp = await client.get("/dashboard/", follow_redirects=False)
    assert resp.status_code == 200


async def _assert_not_logged_in(client) -> None:
    resp = await client.get("/dashboard/", follow_redirects=False)
    assert resp.status_code == 303
    assert "/auth/login" in resp.headers["location"]


async def _assert_denied(client, resp, message: str) -> None:
    """Odmowa = 302 na /auth/login + dokladny komunikat na stronie logowania + brak sesji."""
    assert resp.status_code == 302
    assert resp.headers["location"] == "/auth/login"
    page = await client.get("/auth/login", follow_redirects=False)
    assert page.status_code == 200
    assert message in page.text
    await _assert_not_logged_in(client)


def _invited_user(email: str) -> User:
    return User(
        email=email,
        password_hash=None,
        invitation_token=uuid.uuid4(),
        invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
    )


@pytest.mark.integration
class TestGoogleAutoCreateDefault:
    """Tryb domyslny (bez patchowania settings) -- zachowanie jak przed MON-235."""

    async def test_default_setting_is_true(self):
        """Wartosc domyslna z definicji pola (nie z settings zaladowanych ze srodowiska kontenera) to True."""
        assert Settings.model_fields["GOOGLE_AUTO_CREATE_USERS"].default is True

    async def test_default_mode_new_email_creates_account_and_logs_in(self, client, db_session):
        """Bez zmiany ustawien nowy e-mail tworzy konto i loguje (zabija mutanta: domyslnie False)."""
        email = "mon235-default-new@test.com"
        google_id = uuid.uuid4().hex

        resp = await _callback(client, _make_google_userinfo(email, google_id))

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"
        user = await _find_user(db_session, email)
        assert user is not None
        assert user.is_active is True
        assert user.google_id == google_id
        await _assert_logged_in(client)

    async def test_auto_create_true_new_email_creates_account(self, client, db_session):
        """Jawne True: nowy e-mail -> konto utworzone, zalogowany."""
        email = "mon235-true-new@test.com"

        resp = await _callback(client, _make_google_userinfo(email), auto_create=True)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"
        assert await _find_user(db_session, email) is not None
        await _assert_logged_in(client)

    async def test_default_mode_invited_user_logs_in(self, client, db_session):
        """Tryb domyslny: zaproszony user bez hasla loguje sie przez Google (laczenie po e-mailu)."""
        email = "mon235-default-invited@test.com"
        google_id = uuid.uuid4().hex
        user = _invited_user(email)
        db_session.add(user)
        await db_session.flush()

        resp = await _callback(client, _make_google_userinfo(email, google_id))

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"
        await db_session.refresh(user)
        assert user.google_id == google_id
        await _assert_logged_in(client)


@pytest.mark.integration
class TestGoogleAutoCreateDisabled:
    """GOOGLE_AUTO_CREATE_USERS=false -- Google loguje tylko konta istniejace w systemie."""

    async def test_new_email_does_not_create_account(self, client, db_session):
        """Nowy e-mail: brak User w bazie, 302 na login, komunikat o braku konta, brak sesji."""
        email = "mon235-false-new@test.com"

        resp = await _callback(client, _make_google_userinfo(email), auto_create=False)

        assert await _find_user(db_session, email) is None
        await _assert_denied(client, resp, MSG_NO_ACCOUNT)

    async def test_new_email_denial_does_not_use_unverified_message(self, client, db_session):
        """Odmowa braku konta (email_verified True) nie pokazuje komunikatu o niezweryfikowanym e-mailu."""
        email = "mon235-false-new-msg@test.com"

        resp = await _callback(client, _make_google_userinfo(email), auto_create=False)
        assert resp.status_code == 302
        page = await client.get("/auth/login", follow_redirects=False)

        assert MSG_NO_ACCOUNT in page.text
        assert MSG_UNVERIFIED not in page.text

    async def test_existing_account_with_password_logs_in_and_links(self, client, db_session):
        """Istniejace konto z haslem: zalogowany, google_id polaczone."""
        email = "mon235-false-existing@test.com"
        google_id = uuid.uuid4().hex
        user = User(email=email, password_hash=hash_password("pass12345"))
        db_session.add(user)
        await db_session.flush()

        resp = await _callback(client, _make_google_userinfo(email, google_id), auto_create=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"
        await db_session.refresh(user)
        assert user.google_id == google_id
        await _assert_logged_in(client)

    async def test_invited_user_without_password_logs_in(self, client, db_session):
        """Zaproszony user (password_hash None, wazny invitation_token) loguje sie przez Google."""
        email = "mon235-false-invited@test.com"
        google_id = uuid.uuid4().hex
        user = _invited_user(email)
        db_session.add(user)
        await db_session.flush()

        resp = await _callback(client, _make_google_userinfo(email, google_id), auto_create=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"
        await db_session.refresh(user)
        assert user.google_id == google_id
        await _assert_logged_in(client)

    async def test_returning_google_user_logs_in(self, client, db_session):
        """Powracajacy user z google_id loguje sie bez tworzenia duplikatu."""
        email = "mon235-false-return@test.com"
        google_id = uuid.uuid4().hex
        user = User(email=email, google_id=google_id, is_active=True)
        db_session.add(user)
        await db_session.flush()

        resp = await _callback(client, _make_google_userinfo(email, google_id), auto_create=False)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"
        await _assert_logged_in(client)
        result = await db_session.execute(select(User).where(User.email == email))
        assert len(result.scalars().all()) == 1


@pytest.mark.integration
class TestGoogleEmailVerified:
    """Wymog email_verified == True przy laczeniu po e-mailu i tworzeniu konta."""

    async def test_unverified_email_does_not_link_existing_account(self, client, db_session):
        """email_verified=False + istniejace konto: google_id zostaje None, brak sesji, komunikat."""
        email = "mon235-unverified-existing@test.com"
        user = User(email=email, password_hash=hash_password("pass12345"))
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email, email_verified=False)
        resp = await _callback(client, userinfo)

        await db_session.refresh(user)
        assert user.google_id is None
        await _assert_denied(client, resp, MSG_UNVERIFIED)

    async def test_missing_email_verified_key_does_not_link_existing_account(self, client, db_session):
        """Brak klucza email_verified traktowany jak niezweryfikowany: bez laczenia i bez sesji."""
        email = "mon235-nokey-existing@test.com"
        user = User(email=email, password_hash=hash_password("pass12345"))
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email)
        del userinfo["email_verified"]
        resp = await _callback(client, userinfo)

        await db_session.refresh(user)
        assert user.google_id is None
        await _assert_denied(client, resp, MSG_UNVERIFIED)

    async def test_unverified_email_does_not_link_in_disabled_mode(self, client, db_session):
        """Niezweryfikowany e-mail jest odrzucany takze przy GOOGLE_AUTO_CREATE_USERS=false."""
        email = "mon235-unverified-false-existing@test.com"
        user = _invited_user(email)
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email, email_verified=False)
        resp = await _callback(client, userinfo, auto_create=False)

        await db_session.refresh(user)
        assert user.google_id is None
        await _assert_denied(client, resp, MSG_UNVERIFIED)

    async def test_unverified_email_new_account_not_created_in_default_mode(self, client, db_session):
        """email_verified=False + nowy e-mail + tryb domyslny: brak User w bazie, brak sesji."""
        email = "mon235-unverified-new@test.com"

        resp = await _callback(client, _make_google_userinfo(email, email_verified=False))

        assert await _find_user(db_session, email) is None
        await _assert_denied(client, resp, MSG_UNVERIFIED)

    async def test_returning_user_with_unverified_email_still_logs_in(self, client, db_session):
        """Powracajacy user znaleziony po google_id nie jest dodatkowo sprawdzany pod katem email_verified."""
        email = "mon235-return-unverified@test.com"
        google_id = uuid.uuid4().hex
        user = User(email=email, google_id=google_id, is_active=True)
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email, google_id, email_verified=False)
        resp = await _callback(client, userinfo)

        assert resp.status_code == 302
        assert resp.headers["location"] == "/dashboard/"
        await _assert_logged_in(client)

    async def test_string_email_verified_does_not_link_existing_account(self, client, db_session):
        """email_verified jako string "true" nie jest True: bez laczenia, odmowa, brak sesji."""
        email = "mon235-string-verified-existing@test.com"
        user = User(email=email, password_hash=hash_password("pass12345"))
        db_session.add(user)
        await db_session.flush()

        userinfo = _make_google_userinfo(email)
        userinfo["email_verified"] = "true"
        resp = await _callback(client, userinfo)

        await db_session.refresh(user)
        assert user.google_id is None
        await _assert_denied(client, resp, MSG_UNVERIFIED)


@pytest.mark.integration
class TestGoogleDenialMessageIsOneShot:
    """Komunikat odmowy jest jednorazowy."""

    async def test_denial_message_shown_only_once(self, client, db_session):
        """Po odmowie pierwszy GET /auth/login pokazuje komunikat, drugi juz nie."""
        email = "mon235-oneshot@test.com"
        resp = await _callback(client, _make_google_userinfo(email), auto_create=False)
        assert resp.status_code == 302

        first = await client.get("/auth/login", follow_redirects=False)
        second = await client.get("/auth/login", follow_redirects=False)

        assert MSG_NO_ACCOUNT in first.text
        assert MSG_NO_ACCOUNT not in second.text
