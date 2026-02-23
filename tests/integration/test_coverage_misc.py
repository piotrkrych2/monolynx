"""Testy integracyjne -- dodatkowe pokrycie brakujacych sciezek.

Pokrywane moduly:
- dashboard/auth.py -- login_page, logout, accept_invite (GET/POST), edge cases
- dashboard/profile.py -- tokens list, create, revoke, MCP guide (z weryfikacja kontekstu)
- dashboard/projects.py -- project list z projektami, formularz, tworzenie, walidacje
- api/issues.py -- PATCH status: success, invalid, not found, no body
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.services.auth import hash_password
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_project(slug: str, name: str | None = None) -> Project:
    return Project(
        name=name or f"Project {slug}",
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


def _make_invited_user(
    email: str,
    *,
    expired: bool = False,
    no_expiry: bool = False,
    is_active: bool = True,
) -> tuple[User, uuid.UUID]:
    """Tworzy usera z zaproszeniem. Zwraca (user, raw_token)."""
    token = uuid.uuid4()
    if no_expiry:
        expires = None
    elif expired:
        expires = datetime.now(UTC) - timedelta(days=1)
    else:
        expires = datetime.now(UTC) + timedelta(days=7)

    user = User(
        email=email,
        password_hash=None,
        invitation_token=token,
        invitation_expires_at=expires,
        is_active=is_active,
    )
    return user, token


# ============================================================
# AUTH -- dashboard/auth.py
# ============================================================


@pytest.mark.integration
class TestAuthLoginPage:
    """GET /auth/login -- renderuje formularz logowania."""

    async def test_login_page_returns_200(self, client, db_session):
        resp = await client.get("/auth/login")
        assert resp.status_code == 200

    async def test_login_page_contains_form_elements(self, client, db_session):
        """Formularz zawiera pola email i password."""
        resp = await client.get("/auth/login")
        assert resp.status_code == 200
        body = resp.text.lower()
        assert "email" in body
        assert "password" in body or "haslo" in body


@pytest.mark.integration
class TestAuthLogout:
    """POST /auth/logout -- czysci sesje, redirect na login."""

    async def test_logout_redirects_to_login(self, client, db_session):
        await login_session(client, db_session, email="cov-logout-1@test.com")
        resp = await client.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

    async def test_logout_clears_session_completely(self, client, db_session):
        """Po wylogowaniu dashboard wymaga ponownego logowania."""
        await login_session(client, db_session, email="cov-logout-2@test.com")

        # Zalogowany -- dashboard dostepny
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 200

        # Wyloguj
        await client.post("/auth/logout", follow_redirects=False)

        # Dashboard wymaga logowania
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_logout_without_session(self, client, db_session):
        """Logout bez aktywnej sesji tez redirectuje (nie crashuje)."""
        resp = await client.post("/auth/logout", follow_redirects=False)
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"


@pytest.mark.integration
class TestAuthAcceptInviteFormCoverage:
    """GET /auth/accept-invite/{token} -- dodatkowe pokrycie."""

    async def test_valid_token_renders_password_fields(self, client, db_session):
        """Prawidlowy token -> formularz z polami hasla (valid=True)."""
        user, token = _make_invited_user("cov-inv-form-1@test.com")
        db_session.add(user)
        await db_session.flush()

        resp = await client.get(f"/auth/accept-invite/{token}")
        assert resp.status_code == 200
        body = resp.text.lower()
        # Formularz z polami hasla
        assert "password" in body or "haslo" in body

    async def test_expired_token_shows_invalid_page(self, client, db_session):
        """Wygasly token -> strona z informacja o blednym tokenie (valid=False)."""
        user, token = _make_invited_user("cov-inv-form-2@test.com", expired=True)
        db_session.add(user)
        await db_session.flush()

        resp = await client.get(f"/auth/accept-invite/{token}")
        assert resp.status_code == 200

    async def test_nonexistent_token_shows_invalid_page(self, client, db_session):
        """Nieistniejacy token -> strona z informacja o blednym tokenie."""
        fake = uuid.uuid4()
        resp = await client.get(f"/auth/accept-invite/{fake}")
        assert resp.status_code == 200


@pytest.mark.integration
class TestAuthAcceptInvitePostCoverage:
    """POST /auth/accept-invite/{token} -- pokrycie wszystkich sciezek."""

    async def test_success_sets_password_and_clears_token(self, client, db_session):
        """Prawidlowy token + zgodne hasla -> ustawia haslo, czysci token, redirect."""
        user, token = _make_invited_user("cov-inv-ok@test.com")
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "securePass99", "password_confirm": "securePass99"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

        await db_session.refresh(user)
        assert user.password_hash is not None
        assert user.invitation_token is None
        assert user.invitation_expires_at is None

    async def test_success_user_can_login_after(self, client, db_session):
        """Po ustawieniu hasla user moze sie zalogowac."""
        user, token = _make_invited_user("cov-inv-login@test.com")
        db_session.add(user)
        await db_session.flush()

        await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "loginAfter1", "password_confirm": "loginAfter1"},
            follow_redirects=False,
        )

        resp = await client.post(
            "/auth/login",
            data={"email": "cov-inv-login@test.com", "password": "loginAfter1"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"

    async def test_password_too_short(self, client, db_session):
        """Haslo krotsze niz 8 znakow -> blad walidacji."""
        user, token = _make_invited_user("cov-inv-short@test.com")
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "abc", "password_confirm": "abc"},
        )
        assert resp.status_code == 200
        assert "minimum" in resp.text or "znakow" in resp.text

        # Haslo nie zostalo ustawione
        await db_session.refresh(user)
        assert user.password_hash is None

    async def test_passwords_dont_match(self, client, db_session):
        """Rozne hasla -> blad walidacji."""
        user, token = _make_invited_user("cov-inv-mismatch@test.com")
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "password123", "password_confirm": "different99"},
        )
        assert resp.status_code == 200
        assert "nie sa zgodne" in resp.text

        await db_session.refresh(user)
        assert user.password_hash is None

    async def test_expired_token_post(self, client, db_session):
        """POST z wygaslym tokenem -> valid=False, haslo nie ustawione."""
        user, token = _make_invited_user("cov-inv-exp@test.com", expired=True)
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "validPass88", "password_confirm": "validPass88"},
        )
        assert resp.status_code == 200

        await db_session.refresh(user)
        assert user.password_hash is None

    async def test_nonexistent_token_post(self, client, db_session):
        """POST z nieistniejacym tokenem -> valid=False."""
        fake = uuid.uuid4()
        resp = await client.post(
            f"/auth/accept-invite/{fake}",
            data={"password": "validPass88", "password_confirm": "validPass88"},
        )
        assert resp.status_code == 200

    async def test_password_exactly_8_chars_accepted(self, client, db_session):
        """Haslo dokladnie 8 znakow (granica) -- akceptowane."""
        user, token = _make_invited_user("cov-inv-8chars@test.com")
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "exactly8", "password_confirm": "exactly8"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/auth/login"

        await db_session.refresh(user)
        assert user.password_hash is not None

    async def test_password_7_chars_rejected(self, client, db_session):
        """Haslo 7 znakow (ponizej granicy) -- odrzucone."""
        user, token = _make_invited_user("cov-inv-7chars@test.com")
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            f"/auth/accept-invite/{token}",
            data={"password": "seven77", "password_confirm": "seven77"},
        )
        assert resp.status_code == 200
        assert "minimum" in resp.text or "znakow" in resp.text


# ============================================================
# PROFILE -- dashboard/profile.py
# ============================================================


@pytest.mark.integration
class TestProfileTokensListCoverage:
    """GET /dashboard/profile/tokens -- lista tokenow API."""

    async def test_unauthenticated_redirects_to_login(self, client, db_session):
        resp = await client.get("/dashboard/profile/tokens", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_empty_tokens_list(self, client, db_session):
        """Zalogowany user bez tokenow -- strona laduje sie poprawnie."""
        await login_session(client, db_session, email="cov-prof-list-1@test.com")
        resp = await client.get("/dashboard/profile/tokens")
        assert resp.status_code == 200

    async def test_tokens_list_shows_existing_tokens(self, client, db_session):
        """Po utworzeniu tokenu -- lista go wyswietla."""
        await login_session(client, db_session, email="cov-prof-list-2@test.com")

        # Stworz token
        await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "Profil list test"},
        )

        resp = await client.get("/dashboard/profile/tokens")
        assert resp.status_code == 200
        assert "Profil list test" in resp.text


@pytest.mark.integration
class TestProfileTokenCreateCoverage:
    """POST /dashboard/profile/tokens/create -- generowanie tokenu."""

    async def test_unauthenticated_redirects_to_login(self, client, db_session):
        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_token_success_shows_raw_token(self, client, db_session):
        """Sukces: strona zawiera raw token (osk_...) i nazwe."""
        await login_session(client, db_session, email="cov-prof-create-1@test.com")

        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "Moj nowy token"},
        )
        assert resp.status_code == 200
        assert "osk_" in resp.text
        assert "Moj nowy token" in resp.text

    async def test_create_token_persisted_in_db(self, client, db_session):
        """Token zapisywany w bazie z poprawnym hash i prefixem."""
        await login_session(client, db_session, email="cov-prof-create-2@test.com")

        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "DB check token"},
        )
        assert resp.status_code == 200

        result = await db_session.execute(select(UserApiToken).where(UserApiToken.name == "DB check token"))
        token = result.scalar_one()
        assert token.is_active is True
        assert token.token_prefix.startswith("osk_")
        assert len(token.token_hash) == 64  # SHA256 hex

    async def test_create_token_empty_name_redirects(self, client, db_session):
        """Pusta nazwa -> redirect z flash error."""
        await login_session(client, db_session, email="cov-prof-create-3@test.com")

        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": ""},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]

    async def test_create_token_whitespace_name_redirects(self, client, db_session):
        """Nazwa z samych spacji -> redirect z flash error."""
        await login_session(client, db_session, email="cov-prof-create-4@test.com")

        resp = await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "   "},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]


@pytest.mark.integration
class TestProfileTokenRevokeCoverage:
    """POST /dashboard/profile/tokens/{id}/revoke -- dezaktywacja tokenu."""

    async def test_unauthenticated_redirects_to_login(self, client, db_session):
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/profile/tokens/{fake_id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_revoke_success(self, client, db_session):
        """Dezaktywacja wlasnego tokenu -- is_active=False w bazie."""
        await login_session(client, db_session, email="cov-prof-revoke-1@test.com")

        # Stworz token
        await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "Token do revoke"},
        )

        result = await db_session.execute(select(UserApiToken).where(UserApiToken.name == "Token do revoke"))
        token = result.scalar_one()
        assert token.is_active is True

        # Revoke
        resp = await client.post(
            f"/dashboard/profile/tokens/{token.id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]

        await db_session.refresh(token)
        assert token.is_active is False

    async def test_revoke_nonexistent_token(self, client, db_session):
        """Revoke nieistniejacego tokenu -> redirect z flash error."""
        await login_session(client, db_session, email="cov-prof-revoke-2@test.com")

        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/profile/tokens/{fake_id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/dashboard/profile/tokens" in resp.headers["location"]

    async def test_revoke_other_users_token_fails(self, client, db_session):
        """Nie mozna dezaktywowac tokenu innego uzytkownika."""
        # User 1 tworzy token
        await login_session(client, db_session, email="cov-prof-revoke-3a@test.com")
        await client.post(
            "/dashboard/profile/tokens/create",
            data={"name": "Cudzy token cov"},
        )

        result = await db_session.execute(select(UserApiToken).where(UserApiToken.name == "Cudzy token cov"))
        other_token = result.scalar_one()

        # User 2 probuje revoke
        await login_session(client, db_session, email="cov-prof-revoke-3b@test.com")
        resp = await client.post(
            f"/dashboard/profile/tokens/{other_token.id}/revoke",
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Token user 1 nadal aktywny
        await db_session.refresh(other_token)
        assert other_token.is_active is True


@pytest.mark.integration
class TestProfileMcpGuideCoverage:
    """GET /dashboard/profile/mcp-guide -- strona instrukcji MCP."""

    async def test_unauthenticated_redirects_to_login(self, client, db_session):
        resp = await client.get("/dashboard/profile/mcp-guide", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_mcp_guide_loads_with_content(self, client, db_session):
        """Zalogowany user -- strona MCP guide laduje sie i zawiera 'MCP'."""
        await login_session(client, db_session, email="cov-prof-mcp-1@test.com")
        resp = await client.get("/dashboard/profile/mcp-guide")
        assert resp.status_code == 200
        assert "MCP" in resp.text


# ============================================================
# PROJECTS -- dashboard/projects.py
# ============================================================


@pytest.mark.integration
class TestProjectListCoverage:
    """GET /dashboard/ -- lista projektow."""

    async def test_unauthenticated_redirects_to_login(self, client, db_session):
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_project_list_empty(self, client, db_session):
        """Zalogowany user bez projektow -- strona 200."""
        await login_session(client, db_session, email="cov-proj-list-1@test.com")
        resp = await client.get("/dashboard/")
        assert resp.status_code == 200

    async def test_project_list_shows_projects_for_member(self, client, db_session):
        """User widzi projekty do ktorych nalezy."""
        user = User(
            email="cov-proj-list-2@test.com",
            password_hash=hash_password("testpass123"),
        )
        db_session.add(user)
        await db_session.flush()

        project = _make_project("cov-proj-member")
        db_session.add(project)
        await db_session.flush()

        membership = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="member",
        )
        db_session.add(membership)
        await db_session.flush()

        # Logujemy
        await client.post(
            "/auth/login",
            data={"email": "cov-proj-list-2@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.get("/dashboard/")
        assert resp.status_code == 200
        assert "cov-proj-member" in resp.text or "Project cov-proj-member" in resp.text

    async def test_project_list_superuser_sees_all(self, client, db_session):
        """Superuser widzi wszystkie aktywne projekty (bez wymagania membership)."""
        project = _make_project("cov-proj-super")
        db_session.add(project)
        await db_session.flush()

        user = User(
            email="cov-proj-list-3@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        await client.post(
            "/auth/login",
            data={"email": "cov-proj-list-3@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.get("/dashboard/")
        assert resp.status_code == 200
        assert "cov-proj-super" in resp.text or "Project cov-proj-super" in resp.text

    async def test_project_list_excludes_inactive_projects(self, client, db_session):
        """Nieaktywne projekty (soft-deleted) nie sa widoczne."""
        inactive_project = Project(
            name="Deleted Project",
            slug="cov-proj-inactive",
            api_key=secrets.token_urlsafe(32),
            is_active=False,
        )
        db_session.add(inactive_project)
        await db_session.flush()

        user = User(
            email="cov-proj-list-4@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        await client.post(
            "/auth/login",
            data={"email": "cov-proj-list-4@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.get("/dashboard/")
        assert resp.status_code == 200
        assert "cov-proj-inactive" not in resp.text


@pytest.mark.integration
class TestCreateProjectFormCoverage:
    """GET /dashboard/create-project -- formularz tworzenia projektu."""

    async def test_unauthenticated_redirects_to_login(self, client, db_session):
        resp = await client.get("/dashboard/create-project", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_form_renders_successfully(self, client, db_session):
        """Zalogowany user -- formularz laduje sie (200)."""
        await login_session(client, db_session, email="cov-proj-form-1@test.com")
        resp = await client.get("/dashboard/create-project")
        assert resp.status_code == 200


@pytest.mark.integration
class TestCreateProjectPostCoverage:
    """POST /dashboard/create-project -- tworzenie projektu."""

    async def test_unauthenticated_redirects_to_login(self, client, db_session):
        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Test", "slug": "test"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_project_success(self, client, db_session):
        """Poprawne dane -> projekt w bazie, redirect na dashboard."""
        await login_session(client, db_session, email="cov-proj-post-1@test.com")

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Coverage Projekt", "slug": "cov-proj-new"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"

        result = await db_session.execute(select(Project).where(Project.slug == "cov-proj-new"))
        project = result.scalar_one_or_none()
        assert project is not None
        assert project.name == "Coverage Projekt"
        assert project.api_key is not None
        assert project.is_active is True

    async def test_create_project_empty_name(self, client, db_session):
        """Pusta nazwa -> blad walidacji 'wymagane'."""
        await login_session(client, db_session, email="cov-proj-post-2@test.com")

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "", "slug": "cov-proj-noname"},
        )
        assert resp.status_code == 200
        assert "wymagane" in resp.text

    async def test_create_project_empty_slug(self, client, db_session):
        """Pusty slug -> blad walidacji 'wymagane'."""
        await login_session(client, db_session, email="cov-proj-post-3@test.com")

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Has Name", "slug": ""},
        )
        assert resp.status_code == 200
        assert "wymagane" in resp.text

    async def test_create_project_both_empty(self, client, db_session):
        """Oba pola puste -> blad walidacji."""
        await login_session(client, db_session, email="cov-proj-post-4@test.com")

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "", "slug": ""},
        )
        assert resp.status_code == 200
        assert "wymagane" in resp.text

    async def test_create_project_invalid_slug_uppercase(self, client, db_session):
        """Slug z duzymi literami -> blad walidacji formatu."""
        await login_session(client, db_session, email="cov-proj-post-5@test.com")

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Test", "slug": "MyProject"},
        )
        assert resp.status_code == 200
        assert "male litery" in resp.text

    async def test_create_project_invalid_slug_spaces(self, client, db_session):
        """Slug ze spacjami -> blad walidacji formatu."""
        await login_session(client, db_session, email="cov-proj-post-6@test.com")

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Test", "slug": "my project"},
        )
        assert resp.status_code == 200
        assert "male litery" in resp.text

    async def test_create_project_invalid_slug_special_chars(self, client, db_session):
        """Slug ze znakami specjalnymi -> blad walidacji formatu."""
        await login_session(client, db_session, email="cov-proj-post-7@test.com")

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Test", "slug": "my_project!"},
        )
        assert resp.status_code == 200
        assert "male litery" in resp.text

    async def test_create_project_duplicate_slug(self, client, db_session):
        """Duplikat sluga -> blad 'juz istnieje'."""
        await login_session(client, db_session, email="cov-proj-post-8@test.com")

        # Pierwszy projekt
        await client.post(
            "/dashboard/create-project",
            data={"name": "First", "slug": "cov-dup-slug"},
            follow_redirects=False,
        )

        # Proba z tym samym slugiem
        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Second", "slug": "cov-dup-slug"},
        )
        assert resp.status_code == 200
        assert "juz istnieje" in resp.text


# ============================================================
# ISSUES API -- api/issues.py
# ============================================================


@pytest.mark.integration
class TestUpdateIssueStatusCoverage:
    """PATCH /api/v1/issues/{id}/status -- zmiana statusu issue."""

    async def test_update_status_success_resolved(self, client, db_session):
        """Zmiana statusu na 'resolved' -- sukces."""
        project = _make_project("cov-iss-resolved")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="abc123resolved",
            title="ValueError: test",
            status="unresolved",
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.patch(
            f"/api/v1/issues/{issue.id}/status",
            json={"status": "resolved"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "resolved"

        await db_session.refresh(issue)
        assert issue.status == "resolved"

    async def test_update_status_success_ignored(self, client, db_session):
        """Zmiana statusu na 'ignored' -- sukces."""
        project = _make_project("cov-iss-ignored")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="abc123ignored",
            title="TypeError: test",
            status="unresolved",
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.patch(
            f"/api/v1/issues/{issue.id}/status",
            json={"status": "ignored"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"

    async def test_update_status_success_unresolved(self, client, db_session):
        """Zmiana statusu z powrotem na 'unresolved'."""
        project = _make_project("cov-iss-unresolve")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="abc123unresolve",
            title="RuntimeError: test",
            status="resolved",
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.patch(
            f"/api/v1/issues/{issue.id}/status",
            json={"status": "unresolved"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "unresolved"

    async def test_update_status_invalid_status(self, client, db_session):
        """Nieprawidlowy status -> 422."""
        project = _make_project("cov-iss-invalid")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="abc123invalid",
            title="KeyError: test",
            status="unresolved",
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.patch(
            f"/api/v1/issues/{issue.id}/status",
            json={"status": "closed"},
        )
        assert resp.status_code == 422
        assert "Invalid status" in resp.json()["detail"]

    async def test_update_status_not_found(self, client, db_session):
        """Nieistniejacy issue -> 404."""
        fake_id = uuid.uuid4()
        resp = await client.patch(
            f"/api/v1/issues/{fake_id}/status",
            json={"status": "resolved"},
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    async def test_update_status_no_body(self, client, db_session):
        """Brak body -> 422 (pydantic validation)."""
        fake_id = uuid.uuid4()
        resp = await client.patch(f"/api/v1/issues/{fake_id}/status")
        assert resp.status_code == 422

    async def test_update_status_empty_status(self, client, db_session):
        """Pusty status string -> 422 (invalid status)."""
        project = _make_project("cov-iss-empty")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="abc123empty",
            title="Error: empty",
            status="unresolved",
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.patch(
            f"/api/v1/issues/{issue.id}/status",
            json={"status": ""},
        )
        assert resp.status_code == 422

    async def test_update_status_preserves_other_fields(self, client, db_session):
        """Zmiana statusu nie zmienia innych pol issue."""
        project = _make_project("cov-iss-preserve")
        db_session.add(project)
        await db_session.flush()

        issue = Issue(
            project_id=project.id,
            fingerprint="abc123preserve",
            title="Original Title",
            culprit="views.py in handler",
            level="error",
            status="unresolved",
            event_count=5,
        )
        db_session.add(issue)
        await db_session.flush()

        resp = await client.patch(
            f"/api/v1/issues/{issue.id}/status",
            json={"status": "resolved"},
        )
        assert resp.status_code == 200

        await db_session.refresh(issue)
        assert issue.status == "resolved"
        assert issue.title == "Original Title"
        assert issue.culprit == "views.py in handler"
        assert issue.event_count == 5
