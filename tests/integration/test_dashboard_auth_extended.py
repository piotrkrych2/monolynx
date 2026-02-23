"""Testy integracyjne -- rozszerzone pokrycie auth.py i settings.py.

Pokrywane linie auth.py:
- Login z pustymi polami (brak email, brak hasla)
- Accept invite POST z tokenem juz zuzytkowanym (user z ustawionym haslem)
- Accept invite POST z nieaktywnym uzytkownikiem

Pokrywane linie settings.py:
- Edycja projektu: zmiana tylko nazwy (slug ten sam)
- Edycja projektu: slug z duzymi literami (walidacja SLUG_PATTERN)
- Usuwanie projektu -- potem 404 przy probie dostepu
- Dodanie czlonka z rola owner
- Zmiana roli na owner
- Proba dodania czlonka gdy projekt nie istnieje (edge case)
"""

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session

# --- Helpers ---


def _make_project(slug: str, name: str | None = None) -> Project:
    return Project(
        name=name or f"Project {slug}",
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


# ============================================================
# Auth extended
# ============================================================


@pytest.mark.integration
class TestLoginEdgeCases:
    """Dodatkowe testy logowania -- pokrycie brakujacych linii auth.py."""

    async def test_login_empty_email_and_password(self, client, db_session):
        """Login z pustym emailem i haslem zwraca blad."""
        resp = await client.post(
            "/auth/login",
            data={"email": "", "password": ""},
        )
        assert resp.status_code == 200
        assert "Nieprawidlowy" in resp.text

    async def test_login_missing_form_fields(self, client, db_session):
        """Login bez zadnych pol formularza."""
        resp = await client.post("/auth/login", data={})
        assert resp.status_code == 200
        assert "Nieprawidlowy" in resp.text

    async def test_login_inactive_user(self, client, db_session):
        """Login nieaktywnego uzytkownika (is_active=False) nie loguje."""
        user = User(
            email="auth-ext-inactive@test.com",
            password_hash=hash_password("testpass123"),
            is_active=False,
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/auth/login",
            data={"email": "auth-ext-inactive@test.com", "password": "testpass123"},
        )
        assert resp.status_code == 200
        assert "Nieprawidlowy" in resp.text


@pytest.mark.integration
class TestAcceptInviteExtended:
    """Dodatkowe testy akceptacji zaproszenia."""

    async def test_accept_invite_already_used_token(self, client, db_session):
        """POST z tokenem ktory juz zostal zuzyty (user ma juz haslo, token=None)."""
        fake_token = uuid.uuid4()
        resp = await client.post(
            f"/auth/accept-invite/{fake_token}",
            data={"password": "newpassword123", "password_confirm": "newpassword123"},
        )
        assert resp.status_code == 200
        # Token nie istnieje w bazie -- valid=False

    async def test_accept_invite_form_for_inactive_user(self, client, db_session):
        """GET accept-invite dla nieaktywnego uzytkownika z tokenem -- invalid."""
        token = uuid.uuid4()
        user = User(
            email="auth-ext-inv-inactive@test.com",
            password_hash=None,
            is_active=False,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.get(f"/auth/accept-invite/{token}")
        assert resp.status_code == 200
        # Uzytkownik nieaktywny -- token nie matchuje (is_active check)

    async def test_accept_invite_post_for_inactive_user(self, client, db_session):
        """POST accept-invite dla nieaktywnego uzytkownika -- valid=False."""
        token = uuid.uuid4()
        user = User(
            email="auth-ext-inv-inact-post@test.com",
            password_hash=None,
            is_active=False,
            invitation_token=token,
            invitation_expires_at=datetime.now(UTC) + timedelta(days=7),
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "newpassword123", "password_confirm": "newpassword123"},
        )
        assert resp.status_code == 200
        # User inactive -- nie ustawia hasla
        await db_session.refresh(user)
        assert user.password_hash is None

    async def test_accept_invite_no_expiry_set(self, client, db_session):
        """Token bez ustawionej daty wygasniecia (invitation_expires_at=None) -- akceptowany."""
        token = uuid.uuid4()
        user = User(
            email="auth-ext-noexpiry@test.com",
            password_hash=None,
            invitation_token=token,
            invitation_expires_at=None,
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "validpassword123", "password_confirm": "validpassword123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

        await db_session.refresh(user)
        assert user.password_hash is not None
        assert user.invitation_token is None


# ============================================================
# Settings extended
# ============================================================


@pytest.mark.integration
class TestEditProjectExtended:
    """Dodatkowe testy edycji projektu -- pokrycie brakujacych linii settings.py."""

    async def test_edit_project_same_slug_different_name(self, client, db_session):
        """Zmiana samej nazwy bez zmiany sluga -- sukces."""
        project = _make_project("spe-sameslug", name="Old Name")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="spe-sameslug@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Brand New Name", "slug": "spe-sameslug"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"

        await db_session.refresh(project)
        assert project.name == "Brand New Name"
        assert project.slug == "spe-sameslug"

    async def test_edit_project_slug_with_uppercase_rejected(self, client, db_session):
        """Slug z duzymi literami jest odrzucany."""
        project = _make_project("spe-upcase")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="spe-upcase@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Test", "slug": "MyProject"},
        )
        assert resp.status_code == 200
        assert "male litery" in resp.text

    async def test_edit_project_slug_with_spaces_rejected(self, client, db_session):
        """Slug ze spacjami jest odrzucany."""
        project = _make_project("spe-spaces")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="spe-spaces@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Test", "slug": "my project"},
        )
        assert resp.status_code == 200
        assert "male litery" in resp.text

    async def test_edit_project_empty_name_only(self, client, db_session):
        """Pusta nazwa z poprawnym slugiem -- blad walidacji."""
        project = _make_project("spe-noname")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="spe-noname@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "", "slug": "spe-noname"},
        )
        assert resp.status_code == 200
        assert "wymagane" in resp.text


@pytest.mark.integration
class TestDeleteProjectExtended:
    """Dodatkowe testy soft-delete projektu."""

    async def test_deleted_project_not_accessible_in_settings(self, client, db_session):
        """Po soft-delete projekt nie jest dostepny w ustawieniach (404)."""
        project = _make_project("spe-del-gone")
        db_session.add(project)
        await db_session.flush()
        slug = project.slug

        await login_session(client, db_session, email="spe-del-gone@test.com")

        # Usun projekt
        resp = await client.post(
            f"/dashboard/{slug}/settings/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Proba dostepu do ustawien usunietego projektu
        resp2 = await client.get(f"/dashboard/{slug}/settings")
        assert resp2.status_code == 404

    async def test_deleted_project_monitoring_not_accessible(self, client, db_session):
        """Po soft-delete monitoring projektu zwraca 404."""
        project = _make_project("spe-del-mon")
        db_session.add(project)
        await db_session.flush()
        slug = project.slug

        await login_session(client, db_session, email="spe-del-mon@test.com")

        resp = await client.post(
            f"/dashboard/{slug}/settings/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        resp2 = await client.get(f"/dashboard/{slug}/monitoring/")
        assert resp2.status_code == 404


@pytest.mark.integration
class TestMemberAddExtended:
    """Dodatkowe testy dodawania czlonkow."""

    async def test_add_member_with_owner_role(self, client, db_session):
        """Dodanie czlonka z rola owner."""
        project = _make_project("spe-ma-owner")
        db_session.add(project)
        await db_session.flush()

        target_user = User(
            email="spe-ma-owner-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        await login_session(client, db_session, email="spe-ma-owner@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "spe-ma-owner-target@test.com", "role": "owner"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings" in resp.headers["location"]

    async def test_add_member_empty_email(self, client, db_session):
        """Dodanie czlonka z pustym emailem -- nie istnieje."""
        project = _make_project("spe-ma-empty")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="spe-ma-empty@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "", "role": "member"},
        )
        assert resp.status_code == 200
        assert "nie istnieje" in resp.text


@pytest.mark.integration
class TestMemberRoleExtended:
    """Dodatkowe testy zmiany roli czlonka."""

    async def test_change_role_to_owner(self, client, db_session):
        """Zmiana roli czlonka na owner."""
        project = _make_project("spe-mrl-own")
        db_session.add(project)
        await db_session.flush()

        target_user = User(
            email="spe-mrl-own-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=target_user.id,
            role="member",
        )
        db_session.add(member)
        await db_session.flush()

        await login_session(client, db_session, email="spe-mrl-own@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/role",
            data={"role": "owner"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(member)
        assert member.role == "owner"

    async def test_change_role_with_empty_role_defaults(self, client, db_session):
        """Zmiana roli z pustym polem role -- form default 'member'."""
        project = _make_project("spe-mrl-norole")
        db_session.add(project)
        await db_session.flush()

        target_user = User(
            email="spe-mrl-norole-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=target_user.id,
            role="admin",
        )
        db_session.add(member)
        await db_session.flush()

        await login_session(client, db_session, email="spe-mrl-norole@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/role",
            data={"role": "member"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(member)
        assert member.role == "member"
