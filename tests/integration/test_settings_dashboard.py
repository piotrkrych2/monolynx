"""Testy integracyjne -- ustawienia projektu (edycja, usuwanie, czlonkowie)."""

import secrets
import uuid

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session


async def _create_project(db_session, name="Settings Proj", slug=None):
    """Tworzy projekt w bazie i zwraca go."""
    if slug is None:
        slug = f"sp-{secrets.token_hex(4)}"
    project = Project(
        name=name,
        slug=slug,
        code="P" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.mark.integration
class TestSettingsPage:
    async def test_settings_requires_auth(self, client, db_session):
        """GET /dashboard/{slug}/settings bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-noauth")
        resp = await client.get(
            f"/dashboard/{project.slug}/settings",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_settings_loads_for_logged_in_user(self, client, db_session):
        """GET /dashboard/{slug}/settings wyswietla formularz ustawien."""
        project = await _create_project(db_session, name="Ustawienia Test", slug="sp-loads")
        await login_session(client, db_session, email="sp-loads@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/settings")
        assert resp.status_code == 200
        assert "Ustawienia Test" in resp.text
        assert "sp-loads" in resp.text

    async def test_settings_nonexistent_project_returns_404(self, client, db_session):
        """GET /dashboard/{slug}/settings dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="sp-noproj@test.com")

        resp = await client.get("/dashboard/no-such-project-xyz/settings")
        assert resp.status_code == 404

    async def test_settings_shows_members_list(self, client, db_session):
        """GET /dashboard/{slug}/settings wyswietla liste czlonkow."""
        project = await _create_project(db_session, slug="sp-memlist")
        member_user = User(
            email="memlist-user@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(member_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role="admin",
        )
        db_session.add(member)
        await db_session.flush()

        await login_session(client, db_session, email="sp-memlist@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings")
        assert resp.status_code == 200
        assert "memlist-user@test.com" in resp.text


@pytest.mark.integration
class TestEditProject:
    async def test_edit_project_success(self, client, db_session):
        """POST z poprawnymi danymi zmienia nazwe i slug projektu."""
        project = await _create_project(db_session, name="Stara nazwa", slug="sp-edit-ok")
        await login_session(client, db_session, email="sp-edit-ok@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Nowa Nazwa Edycja", "slug": "sp-edit-ok-new", "code": "SPE"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"

        await db_session.refresh(project)
        assert project.name == "Nowa Nazwa Edycja"
        assert project.slug == "sp-edit-ok-new"

    async def test_edit_project_requires_auth(self, client, db_session):
        """POST /dashboard/{slug}/settings bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-edit-noauth")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "X", "slug": "x", "code": "XX"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_edit_project_nonexistent_returns_404(self, client, db_session):
        """POST /dashboard/{slug}/settings dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="sp-edit-nop@test.com")
        resp = await client.post(
            "/dashboard/no-such-proj-edit/settings",
            data={"name": "X", "slug": "x", "code": "XX"},
        )
        assert resp.status_code == 404

    async def test_edit_project_empty_fields(self, client, db_session):
        """POST z pustymi polami pokazuje blad walidacji."""
        project = await _create_project(db_session, slug="sp-edit-empty")
        await login_session(client, db_session, email="sp-edit-empty@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "", "slug": "", "code": ""},
        )
        assert resp.status_code == 200
        assert "wymagane" in resp.text

    async def test_edit_project_invalid_slug_format(self, client, db_session):
        """POST z nieprawidlowym formatem sluga pokazuje blad."""
        project = await _create_project(db_session, slug="sp-edit-badslug")
        await login_session(client, db_session, email="sp-edit-badslug@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Test", "slug": "INVALID SLUG!", "code": "TST"},
        )
        assert resp.status_code == 200
        assert "male litery" in resp.text

    async def test_edit_project_duplicate_slug(self, client, db_session):
        """POST z istniejacym slugiem pokazuje blad."""
        await _create_project(db_session, slug="sp-dup-target")
        project = await _create_project(db_session, slug="sp-dup-source")
        await login_session(client, db_session, email="sp-dup@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Kopia", "slug": "sp-dup-target", "code": "KOP"},
        )
        assert resp.status_code == 200
        assert "juz istnieje" in resp.text


@pytest.mark.integration
class TestDeleteProject:
    async def test_delete_project_soft_deletes(self, client, db_session):
        """POST ustawia is_active=False i redirectuje do listy."""
        project = await _create_project(db_session, slug="sp-del-ok")
        await login_session(client, db_session, email="sp-del-ok@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert resp.headers["location"] == "/dashboard/"

        await db_session.refresh(project)
        assert project.is_active is False

    async def test_delete_project_requires_auth(self, client, db_session):
        """POST /dashboard/{slug}/settings/delete bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-del-noauth")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_nonexistent_project_returns_404(self, client, db_session):
        """POST /dashboard/{slug}/settings/delete dla nieistniejacego projektu."""
        await login_session(client, db_session, email="sp-del-nop@test.com")
        resp = await client.post(
            "/dashboard/no-such-proj-del/settings/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMemberAdd:
    async def test_add_member_success(self, client, db_session):
        """Dodanie istniejacego uzytkownika do projektu."""
        project = await _create_project(db_session, slug="sp-ma-ok")
        target_user = User(
            email="sp-ma-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        await login_session(client, db_session, email="sp-ma-ok@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "sp-ma-target@test.com", "role": "member"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings" in resp.headers["location"]

    async def test_add_member_as_admin_role(self, client, db_session):
        """Dodanie czlonka z rola admin."""
        project = await _create_project(db_session, slug="sp-ma-admin")
        target_user = User(
            email="sp-ma-admin-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        await login_session(client, db_session, email="sp-ma-admin@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "sp-ma-admin-target@test.com", "role": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_add_member_with_invalid_role_defaults_to_member(self, client, db_session):
        """Nieprawidlowa rola jest zamieniana na 'member'."""
        project = await _create_project(db_session, slug="sp-ma-badrole")
        target_user = User(
            email="sp-ma-badrole-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        await login_session(client, db_session, email="sp-ma-badrole@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "sp-ma-badrole-target@test.com", "role": "superadmin"},
            follow_redirects=False,
        )
        # Should still succeed -- role defaults to "member"
        assert resp.status_code == 303

    async def test_add_member_nonexistent_user(self, client, db_session):
        """Dodanie nieistniejacego uzytkownika -- blad."""
        project = await _create_project(db_session, slug="sp-ma-nouser")
        await login_session(client, db_session, email="sp-ma-nouser@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "nobody-here@test.com", "role": "member"},
        )
        assert resp.status_code == 200
        assert "nie istnieje" in resp.text

    async def test_add_member_already_exists(self, client, db_session):
        """Dodanie uzytkownika ktory juz jest czlonkiem -- blad."""
        project = await _create_project(db_session, slug="sp-ma-dup")
        target_user = User(
            email="sp-ma-dup-target@test.com",
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

        await login_session(client, db_session, email="sp-ma-dup@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "sp-ma-dup-target@test.com", "role": "member"},
        )
        assert resp.status_code == 200
        assert "juz czlonkiem" in resp.text

    async def test_add_member_requires_auth(self, client, db_session):
        """POST /members/add bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-ma-noauth")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "x@test.com", "role": "member"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_add_member_nonexistent_project_returns_404(self, client, db_session):
        """POST /members/add dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="sp-ma-noproj@test.com")
        resp = await client.post(
            "/dashboard/no-such-proj-ma/settings/members/add",
            data={"email": "x@test.com", "role": "member"},
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMemberRemove:
    async def test_remove_member_success(self, client, db_session):
        """Usuniecie czlonka z projektu."""
        project = await _create_project(db_session, slug="sp-mr-ok")
        target_user = User(
            email="sp-mr-target@test.com",
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

        await login_session(client, db_session, email="sp-mr-ok@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/remove",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings" in resp.headers["location"]

    async def test_remove_member_requires_auth(self, client, db_session):
        """POST /members/{id}/remove bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-mr-noauth")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{fake_id}/remove",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_remove_nonexistent_member_returns_404(self, client, db_session):
        """POST /members/{id}/remove dla nieistniejacego czlonka zwraca 404."""
        project = await _create_project(db_session, slug="sp-mr-noid")
        await login_session(client, db_session, email="sp-mr-noid@test.com")

        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{fake_id}/remove",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_remove_member_nonexistent_project_returns_404(self, client, db_session):
        """POST /members/{id}/remove dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="sp-mr-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/no-such-proj-mr/settings/members/{fake_id}/remove",
            follow_redirects=False,
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestMemberRole:
    async def test_change_role_success(self, client, db_session):
        """Zmiana roli czlonka projektu."""
        project = await _create_project(db_session, slug="sp-mrl-ok")

        admin_role = Role(name="Admin", project_id=project.id, permissions={"settings": ["read", "write"]}, is_system=True)
        db_session.add(admin_role)
        await db_session.flush()

        target_user = User(
            email="sp-mrl-target@test.com",
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

        await login_session(client, db_session, email="sp-mrl-ok@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/role",
            data={"role_id": str(admin_role.id)},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(member)
        assert member.role == "admin"
        assert member.role_id == admin_role.id

    async def test_change_role_invalid_role_ignored(self, client, db_session):
        """Nieprawidlowa rola nie zmienia aktualnej roli."""
        project = await _create_project(db_session, slug="sp-mrl-bad")
        target_user = User(
            email="sp-mrl-bad-target@test.com",
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

        await login_session(client, db_session, email="sp-mrl-bad@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/role",
            data={"role": "superadmin"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(member)
        assert member.role == "member"  # Not changed

    async def test_change_role_requires_auth(self, client, db_session):
        """POST /members/{id}/role bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-mrl-noauth")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{fake_id}/role",
            data={"role": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_change_role_nonexistent_member_returns_404(self, client, db_session):
        """POST /members/{id}/role dla nieistniejacego czlonka zwraca 404."""
        project = await _create_project(db_session, slug="sp-mrl-noid")
        await login_session(client, db_session, email="sp-mrl-noid@test.com")

        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{fake_id}/role",
            data={"role": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_change_role_nonexistent_project_returns_404(self, client, db_session):
        """POST /members/{id}/role dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="sp-mrl-noproj@test.com")
        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/no-such-proj-mrl/settings/members/{fake_id}/role",
            data={"role": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Testy RBAC: member z role bez uprawnien (403)
# ---------------------------------------------------------------------------


async def _create_member_with_role(db_session, project, user, permissions: dict):
    """Tworzy Role z podanymi uprawnieniami i przypisuje czlonka."""
    role = Role(
        name=f"testrole-{secrets.token_hex(4)}",
        project_id=project.id,
        permissions=permissions,
        is_system=False,
    )
    db_session.add(role)
    await db_session.flush()

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role=role.name.lower(),
        role_id=role.id,
    )
    db_session.add(member)
    await db_session.flush()
    return member, role


async def _login_existing_user(client, email: str):
    """Loguje istniejacego usera (bez tworzenia - user musi juz byc w DB)."""
    resp = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303
    return client


@pytest.mark.integration
class TestSettingsRBAC:
    """Testy 403 dla usera z rola bez wymaganych uprawnien."""

    async def test_get_settings_member_without_read_returns_403(self, client, db_session):
        """GET /settings dla usera z rola bez settings:read zwraca 403."""
        project = await _create_project(db_session, slug="sp-rbac-get")
        user = User(
            email="sp-rbac-get@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={})

        await _login_existing_user(client, "sp-rbac-get@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings")
        assert resp.status_code == 403

    async def test_post_settings_member_without_write_returns_403(self, client, db_session):
        """POST /settings dla usera z rola bez settings:write zwraca 403."""
        project = await _create_project(db_session, slug="sp-rbac-post")
        user = User(
            email="sp-rbac-post@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read"]})

        await _login_existing_user(client, "sp-rbac-post@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "X", "slug": project.slug, "code": "TST"},
        )
        assert resp.status_code == 403

    async def test_delete_project_member_without_delete_returns_403(self, client, db_session):
        """POST /settings/delete dla usera z rola bez settings:delete zwraca 403."""
        project = await _create_project(db_session, slug="sp-rbac-del")
        user = User(
            email="sp-rbac-del@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read", "write"]})

        await _login_existing_user(client, "sp-rbac-del@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 403

    async def test_add_member_without_write_returns_403(self, client, db_session):
        """POST /members/add dla usera bez settings:write zwraca 403."""
        project = await _create_project(db_session, slug="sp-rbac-add")
        user = User(
            email="sp-rbac-add@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read"]})

        await _login_existing_user(client, "sp-rbac-add@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "anyone@test.com", "role_id": ""},
        )
        assert resp.status_code == 403

    async def test_remove_member_without_delete_returns_403(self, client, db_session):
        """POST /members/{id}/remove dla usera bez settings:delete zwraca 403."""
        project = await _create_project(db_session, slug="sp-rbac-rm")
        user = User(
            email="sp-rbac-rm@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read", "write"]})

        fake_member_id = uuid.uuid4()
        await _login_existing_user(client, "sp-rbac-rm@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{fake_member_id}/remove",
            follow_redirects=False,
        )
        assert resp.status_code == 403

    async def test_change_role_without_write_returns_403(self, client, db_session):
        """POST /members/{id}/role dla usera bez settings:write zwraca 403."""
        project = await _create_project(db_session, slug="sp-rbac-chgrole")
        user = User(
            email="sp-rbac-chgrole@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read"]})

        fake_member_id = uuid.uuid4()
        await _login_existing_user(client, "sp-rbac-chgrole@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{fake_member_id}/role",
            data={"role_id": str(uuid.uuid4())},
            follow_redirects=False,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Testy soft-delete projektu
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSoftDeleteProject:
    async def test_deleted_project_not_accessible_via_get_settings(self, client, db_session):
        """Projekt z is_active=False nie jest dostepny przez GET /settings."""
        project = await _create_project(db_session, slug="sp-softdel-get")
        project.is_active = False
        await db_session.flush()

        await login_session(client, db_session, email="sp-softdel-get@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings")
        assert resp.status_code == 404

    async def test_deleted_project_not_accessible_via_post_settings(self, client, db_session):
        """Projekt z is_active=False nie jest dostepny przez POST /settings."""
        project = await _create_project(db_session, slug="sp-softdel-post")
        project.is_active = False
        await db_session.flush()

        await login_session(client, db_session, email="sp-softdel-post@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "X", "slug": "sp-softdel-post", "code": "TST"},
        )
        assert resp.status_code == 404

    async def test_delete_already_inactive_project_returns_404(self, client, db_session):
        """POST /settings/delete dla projektu z is_active=False zwraca 404."""
        project = await _create_project(db_session, slug="sp-softdel-del")
        project.is_active = False
        await db_session.flush()

        await login_session(client, db_session, email="sp-softdel-del@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Testy edit project - dodatkowe sciezki walidacji
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEditProjectValidation:
    async def test_edit_project_invalid_code_pattern(self, client, db_session):
        """POST z nieprawidlowym kodem projektu pokazuje blad walidacji."""
        project = await _create_project(db_session, slug="sp-editv-badcode")
        await login_session(client, db_session, email="sp-editv-badcode@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Test", "slug": "sp-editv-badcode", "code": "123INVALID"},
        )
        assert resp.status_code == 200
        assert "Kod musi" in resp.text

    async def test_edit_project_duplicate_code_shows_error(self, client, db_session):
        """POST z kodem zajętym przez inny projekt pokazuje blad."""
        other = await _create_project(db_session, name="Other", slug="sp-editv-dup-other")
        project = await _create_project(db_session, slug="sp-editv-dup-src")
        await login_session(client, db_session, email="sp-editv-dup@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings",
            data={"name": "Test", "slug": "sp-editv-dup-src", "code": other.code},
        )
        assert resp.status_code == 200
        assert "juz istnieje" in resp.text


# ---------------------------------------------------------------------------
# Testy add member - dodatkowe sciezki
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMemberAddExtra:
    async def test_add_member_inactive_user_shows_error(self, client, db_session):
        """Dodanie nieaktywnego uzytkownika (is_active=False) pokazuje blad."""
        project = await _create_project(db_session, slug="sp-ma-inactive")
        inactive_user = User(
            email="sp-ma-inactive-target@test.com",
            password_hash=hash_password("pass123"),
            is_active=False,
        )
        db_session.add(inactive_user)
        await db_session.flush()

        await login_session(client, db_session, email="sp-ma-inactive@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "sp-ma-inactive-target@test.com", "role_id": ""},
        )
        assert resp.status_code == 200
        assert "nie istnieje" in resp.text

    async def test_add_member_with_invalid_uuid_role_id_shows_error(self, client, db_session):
        """POST z nieprawidlowym UUID jako role_id pokazuje blad."""
        project = await _create_project(db_session, slug="sp-ma-baduuid")
        target_user = User(
            email="sp-ma-baduuid-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        await login_session(client, db_session, email="sp-ma-baduuid@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "sp-ma-baduuid-target@test.com", "role_id": "not-a-uuid"},
        )
        assert resp.status_code == 200
        assert "Niepoprawny identyfikator roli" in resp.text

    async def test_add_member_with_role_from_other_project_shows_error(self, client, db_session):
        """POST z role_id z innego projektu pokazuje blad."""
        project = await _create_project(db_session, slug="sp-ma-wrongproj")
        other_project = await _create_project(db_session, slug="sp-ma-wrongproj-other")
        foreign_role = Role(
            name="ForeignRole",
            project_id=other_project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(foreign_role)
        await db_session.flush()

        target_user = User(
            email="sp-ma-wrongproj-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        await login_session(client, db_session, email="sp-ma-wrongproj@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={
                "email": "sp-ma-wrongproj-target@test.com",
                "role_id": str(foreign_role.id),
            },
        )
        assert resp.status_code == 200
        assert "nie istnieje w tym projekcie" in resp.text

    async def test_add_member_with_custom_role_sets_role_id(self, client, db_session):
        """Dodanie czlonka z custom Role ustawia role_id i role=nazwa_roli."""
        project = await _create_project(db_session, slug="sp-ma-customrole")
        custom_role = Role(
            name="Tester",
            project_id=project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(custom_role)
        await db_session.flush()

        target_user = User(
            email="sp-ma-customrole-target@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(target_user)
        await db_session.flush()

        await login_session(client, db_session, email="sp-ma-customrole@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={
                "email": "sp-ma-customrole-target@test.com",
                "role_id": str(custom_role.id),
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        from sqlalchemy import select as sa_select

        from monolynx.models.project_member import ProjectMember

        result = await db_session.execute(
            sa_select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_user.id,
            )
        )
        member = result.scalar_one_or_none()
        assert member is not None
        assert member.role_id == custom_role.id
        assert member.role == "tester"


# ---------------------------------------------------------------------------
# Testy zmiana roli - dodatkowe sciezki
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMemberRoleExtra:
    async def test_change_role_invalid_uuid_redirects_with_flash(self, client, db_session):
        """POST /members/{id}/role z nieprawidlowym UUID redirectuje z komunikatem."""
        project = await _create_project(db_session, slug="sp-mrl-baduuid")
        target_user = User(
            email="sp-mrl-baduuid-target@test.com",
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

        await login_session(client, db_session, email="sp-mrl-baduuid@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/role",
            data={"role_id": "not-a-valid-uuid"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings" in resp.headers["location"]

    async def test_change_role_to_role_from_other_project_redirects_with_flash(self, client, db_session):
        """POST /members/{id}/role z role_id z innego projektu redirectuje z komunikatem."""
        project = await _create_project(db_session, slug="sp-mrl-wrongproj")
        other_project = await _create_project(db_session, slug="sp-mrl-wrongproj-other")
        foreign_role = Role(
            name="ForeignRole",
            project_id=other_project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(foreign_role)

        target_user = User(
            email="sp-mrl-wrongproj-target@test.com",
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

        await login_session(client, db_session, email="sp-mrl-wrongproj@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/role",
            data={"role_id": str(foreign_role.id)},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Testy roles list
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRolesList:
    async def test_roles_list_requires_auth(self, client, db_session):
        """GET /settings/roles bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-rl-noauth")
        resp = await client.get(
            f"/dashboard/{project.slug}/settings/roles",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_roles_list_renders_for_logged_user(self, client, db_session):
        """GET /settings/roles renderuje liste rol."""
        project = await _create_project(db_session, slug="sp-rl-ok")
        role = Role(
            name="DevRole",
            project_id=project.id,
            permissions={"scrum": ["read", "write"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-rl-ok@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings/roles")
        assert resp.status_code == 200
        assert "DevRole" in resp.text

    async def test_roles_list_shows_member_count(self, client, db_session):
        """GET /settings/roles pokazuje liczbe czlonkow przypisanych do roli."""
        project = await _create_project(db_session, slug="sp-rl-count")
        role = Role(
            name="CountRole",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        member_user = User(
            email="sp-rl-count-member@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(member_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role="countrole",
            role_id=role.id,
        )
        db_session.add(member)
        await db_session.flush()

        await login_session(client, db_session, email="sp-rl-count@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings/roles")
        assert resp.status_code == 200
        assert "CountRole" in resp.text
        # Strona zawiera liczbe czlonkow (1) przypisanych do roli
        assert "1" in resp.text

    async def test_roles_list_nonexistent_project_returns_404(self, client, db_session):
        """GET /settings/roles dla nieistniejacego projektu zwraca 404."""
        await login_session(client, db_session, email="sp-rl-noproj@test.com")
        resp = await client.get("/dashboard/no-such-proj-rl/settings/roles")
        assert resp.status_code == 404

    async def test_roles_list_member_without_read_returns_403(self, client, db_session):
        """GET /settings/roles dla usera z rola bez settings:read zwraca 403."""
        project = await _create_project(db_session, slug="sp-rl-noread")
        user = User(
            email="sp-rl-noread@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={})

        await _login_existing_user(client, "sp-rl-noread@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings/roles")
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Testy create role
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRoleCreate:
    async def test_role_create_form_renders(self, client, db_session):
        """GET /settings/roles/create renderuje formularz."""
        project = await _create_project(db_session, slug="sp-rc-form")
        await login_session(client, db_session, email="sp-rc-form@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings/roles/create")
        assert resp.status_code == 200

    async def test_role_create_form_requires_auth(self, client, db_session):
        """GET /settings/roles/create bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-rc-noauth")
        resp = await client.get(
            f"/dashboard/{project.slug}/settings/roles/create",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_role_create_success(self, client, db_session):
        """POST /settings/roles/create tworzy role i redirectuje."""
        project = await _create_project(db_session, slug="sp-rc-ok")
        await login_session(client, db_session, email="sp-rc-ok@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/create",
            data={
                "name": "Developer",
                "perm_scrum_read": "on",
                "perm_scrum_write": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/roles" in resp.headers["location"]

    async def test_role_create_empty_name_shows_error(self, client, db_session):
        """POST /settings/roles/create z pusta nazwa pokazuje blad."""
        project = await _create_project(db_session, slug="sp-rc-emptyname")
        await login_session(client, db_session, email="sp-rc-emptyname@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/create",
            data={"name": ""},
        )
        assert resp.status_code == 200
        assert "wymagana" in resp.text

    async def test_role_create_name_too_long_shows_error(self, client, db_session):
        """POST /settings/roles/create z nazwa >50 znakow pokazuje blad."""
        project = await _create_project(db_session, slug="sp-rc-toolong")
        await login_session(client, db_session, email="sp-rc-toolong@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/create",
            data={"name": "A" * 51},
        )
        assert resp.status_code == 200
        assert "50 znakow" in resp.text

    async def test_role_create_duplicate_name_shows_error(self, client, db_session):
        """POST /settings/roles/create z nazwa juz istniejaca w projekcie pokazuje blad."""
        project = await _create_project(db_session, slug="sp-rc-dupname")
        existing_role = Role(
            name="UniqueRole",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(existing_role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-rc-dupname@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/create",
            data={"name": "UniqueRole"},
        )
        assert resp.status_code == 200
        assert "juz istnieje" in resp.text

    async def test_role_create_with_permissions(self, client, db_session):
        """POST /settings/roles/create z checkboxami uprawnien zapisuje permissions."""
        project = await _create_project(db_session, slug="sp-rc-perms")
        await login_session(client, db_session, email="sp-rc-perms@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/create",
            data={
                "name": "Reviewer",
                "perm_scrum_read": "on",
                "perm_wiki_read": "on",
                "perm_wiki_write": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        from sqlalchemy import select as sa_select

        result = await db_session.execute(sa_select(Role).where(Role.project_id == project.id, Role.name == "Reviewer"))
        role = result.scalar_one_or_none()
        assert role is not None
        assert "scrum" in role.permissions
        assert role.permissions["scrum"] == ["read"]
        assert "wiki" in role.permissions
        assert "write" in role.permissions["wiki"]

    async def test_role_create_member_without_write_returns_403(self, client, db_session):
        """POST /settings/roles/create dla usera bez settings:write zwraca 403."""
        project = await _create_project(db_session, slug="sp-rc-403")
        user = User(
            email="sp-rc-403@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read"]})

        await _login_existing_user(client, "sp-rc-403@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/create",
            data={"name": "TestRole"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Testy edit role
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRoleEdit:
    async def test_role_edit_form_renders(self, client, db_session):
        """GET /settings/roles/{id}/edit renderuje formularz z istniejaca rola."""
        project = await _create_project(db_session, slug="sp-re-form")
        role = Role(
            name="EditableRole",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-re-form@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings/roles/{role.id}/edit")
        assert resp.status_code == 200
        assert "EditableRole" in resp.text

    async def test_role_edit_form_nonexistent_role_returns_404(self, client, db_session):
        """GET /settings/roles/{id}/edit dla nieistniejacej roli zwraca 404."""
        project = await _create_project(db_session, slug="sp-re-noid")
        await login_session(client, db_session, email="sp-re-noid@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings/roles/{uuid.uuid4()}/edit")
        assert resp.status_code == 404

    async def test_role_edit_custom_role_name_changes(self, client, db_session):
        """POST /settings/roles/{id}/edit zmienia nazwe custom roli."""
        project = await _create_project(db_session, slug="sp-re-rename")
        role = Role(
            name="OldName",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-re-rename@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role.id}/edit",
            data={"name": "NewName", "perm_scrum_read": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(role)
        assert role.name == "NewName"

    async def test_role_edit_system_role_name_not_changed(self, client, db_session):
        """POST /settings/roles/{id}/edit nie zmienia nazwy roli systemowej."""
        project = await _create_project(db_session, slug="sp-re-sysrole")
        sys_role = Role(
            name="SystemRole",
            project_id=project.id,
            permissions={"settings": ["read", "write", "delete"]},
            is_system=True,
        )
        db_session.add(sys_role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-re-sysrole@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{sys_role.id}/edit",
            data={"name": "NewNameAttempt", "perm_scrum_read": "on"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(sys_role)
        assert sys_role.name == "SystemRole"

    async def test_role_edit_empty_name_shows_error(self, client, db_session):
        """POST /settings/roles/{id}/edit z pusta nazwa pokazuje blad dla custom roli."""
        project = await _create_project(db_session, slug="sp-re-emptyname")
        role = Role(
            name="SomeName",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-re-emptyname@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role.id}/edit",
            data={"name": ""},
        )
        assert resp.status_code == 200
        assert "wymagana" in resp.text

    async def test_role_edit_name_too_long_shows_error(self, client, db_session):
        """POST /settings/roles/{id}/edit z nazwa >50 znakow pokazuje blad."""
        project = await _create_project(db_session, slug="sp-re-toolong")
        role = Role(
            name="ValidName",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-re-toolong@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role.id}/edit",
            data={"name": "B" * 51},
        )
        assert resp.status_code == 200
        assert "50 znakow" in resp.text

    async def test_role_edit_duplicate_name_shows_error(self, client, db_session):
        """POST /settings/roles/{id}/edit z nazwa juz istniejaca pokazuje blad."""
        project = await _create_project(db_session, slug="sp-re-dupname")
        role_a = Role(
            name="RoleAlpha",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        role_b = Role(
            name="RoleBeta",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role_a)
        db_session.add(role_b)
        await db_session.flush()

        await login_session(client, db_session, email="sp-re-dupname@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role_b.id}/edit",
            data={"name": "RoleAlpha"},
        )
        assert resp.status_code == 200
        assert "juz istnieje" in resp.text

    async def test_role_edit_updates_permissions(self, client, db_session):
        """POST /settings/roles/{id}/edit aktualizuje permissions z checkboxow."""
        project = await _create_project(db_session, slug="sp-re-perms")
        role = Role(
            name="PermRole",
            project_id=project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-re-perms@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role.id}/edit",
            data={
                "name": "PermRole",
                "perm_wiki_read": "on",
                "perm_wiki_write": "on",
                "perm_wiki_delete": "on",
            },
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(role)
        assert "scrum" not in role.permissions
        assert role.permissions.get("wiki") == ["read", "write", "delete"]

    async def test_role_edit_member_without_write_returns_403(self, client, db_session):
        """POST /settings/roles/{id}/edit dla usera bez settings:write zwraca 403."""
        project = await _create_project(db_session, slug="sp-re-403")
        role = Role(
            name="SomeRole403",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        user = User(
            email="sp-re-403@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read"]})

        await _login_existing_user(client, "sp-re-403@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role.id}/edit",
            data={"name": "X"},
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Testy delete role
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestRoleDelete:
    async def test_role_delete_success(self, client, db_session):
        """POST /settings/roles/{id}/delete usuwa custom role bez czlonkow."""
        project = await _create_project(db_session, slug="sp-rd-ok")
        role = Role(
            name="DeleteMe",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()
        role_id = role.id

        await login_session(client, db_session, email="sp-rd-ok@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role_id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/roles" in resp.headers["location"]

    async def test_role_delete_system_role_redirects_with_flash(self, client, db_session):
        """POST /settings/roles/{id}/delete dla roli systemowej redirectuje z komunikatem."""
        project = await _create_project(db_session, slug="sp-rd-sys")
        sys_role = Role(
            name="SysRoleDel",
            project_id=project.id,
            permissions={"settings": ["read", "write", "delete"]},
            is_system=True,
        )
        db_session.add(sys_role)
        await db_session.flush()

        await login_session(client, db_session, email="sp-rd-sys@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{sys_role.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/roles" in resp.headers["location"]

        await db_session.refresh(sys_role)
        assert sys_role.id is not None

    async def test_role_delete_role_with_members_redirects_with_flash(self, client, db_session):
        """POST /settings/roles/{id}/delete dla roli z czlonkami redirectuje z komunikatem."""
        project = await _create_project(db_session, slug="sp-rd-members")
        role = Role(
            name="RoleWithMembers",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        member_user = User(
            email="sp-rd-members-user@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(member_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role=role.name.lower(),
            role_id=role.id,
        )
        db_session.add(member)
        await db_session.flush()

        await login_session(client, db_session, email="sp-rd-members@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings/roles" in resp.headers["location"]

        from sqlalchemy import select as sa_select

        result = await db_session.execute(sa_select(Role).where(Role.id == role.id))
        assert result.scalar_one_or_none() is not None

    async def test_role_delete_nonexistent_role_returns_404(self, client, db_session):
        """POST /settings/roles/{id}/delete dla nieistniejacej roli zwraca 404."""
        project = await _create_project(db_session, slug="sp-rd-noid")
        await login_session(client, db_session, email="sp-rd-noid@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404

    async def test_role_delete_requires_auth(self, client, db_session):
        """POST /settings/roles/{id}/delete bez sesji redirectuje na login."""
        project = await _create_project(db_session, slug="sp-rd-noauth")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_role_delete_member_without_delete_returns_403(self, client, db_session):
        """POST /settings/roles/{id}/delete dla usera bez settings:delete zwraca 403."""
        project = await _create_project(db_session, slug="sp-rd-403")
        role = Role(
            name="RoleToDel403",
            project_id=project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        user = User(
            email="sp-rd-403@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        await _create_member_with_role(db_session, project, user, permissions={"settings": ["read", "write"]})

        await _login_existing_user(client, "sp-rd-403@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/roles/{role.id}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Testy services/permissions.py
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestPermissionsService:
    async def test_superuser_has_all_permissions(self, db_session):
        """Superuser ma dostep do wszystkich modulow i akcji."""
        from monolynx.services.permissions import check_permission

        project = await _create_project(db_session, slug="sp-perm-su")
        user = User(
            email="sp-perm-su@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        assert await check_permission(db_session, user.id, project.id, "settings", "delete") is True
        assert await check_permission(db_session, user.id, project.id, "scrum", "read") is True

    async def test_user_without_membership_has_no_permissions(self, db_session):
        """User bez membership zwraca False dla kazdego uprawnienia."""
        from monolynx.services.permissions import check_permission

        project = await _create_project(db_session, slug="sp-perm-nomem")
        user = User(
            email="sp-perm-nomem@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        assert await check_permission(db_session, user.id, project.id, "settings", "read") is False

    async def test_member_with_role_obj_uses_role_permissions(self, db_session):
        """Member z role_obj uzywa uprawnien z roli (nie z DEFAULT_ROLE_PERMISSIONS)."""
        from monolynx.services.permissions import check_permission

        project = await _create_project(db_session, slug="sp-perm-roleobj")
        user = User(
            email="sp-perm-roleobj@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        role = Role(
            name="CustomPerm",
            project_id=project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="customperm",
            role_id=role.id,
        )
        db_session.add(member)
        await db_session.flush()

        assert await check_permission(db_session, user.id, project.id, "scrum", "read") is True
        assert await check_permission(db_session, user.id, project.id, "scrum", "write") is False
        assert await check_permission(db_session, user.id, project.id, "settings", "read") is False

    async def test_member_with_legacy_role_uses_default_permissions(self, db_session):
        """Member z legacy member.role (bez role_obj) uzywa DEFAULT_ROLE_PERMISSIONS."""
        from monolynx.services.permissions import check_permission

        project = await _create_project(db_session, slug="sp-perm-legacy")
        user = User(
            email="sp-perm-legacy@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="member",
            role_id=None,
        )
        db_session.add(member)
        await db_session.flush()

        assert await check_permission(db_session, user.id, project.id, "scrum", "read") is True
        assert await check_permission(db_session, user.id, project.id, "settings", "delete") is False

    async def test_member_with_owner_legacy_role_has_all_permissions(self, db_session):
        """Member z legacy role=owner ma pelne uprawnienia."""
        from monolynx.services.permissions import check_permission

        project = await _create_project(db_session, slug="sp-perm-owner")
        user = User(
            email="sp-perm-owner@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="owner",
            role_id=None,
        )
        db_session.add(member)
        await db_session.flush()

        assert await check_permission(db_session, user.id, project.id, "settings", "delete") is True
        assert await check_permission(db_session, user.id, project.id, "scrum", "write") is True

    async def test_invalid_module_returns_false(self, db_session):
        """check_permission z nieistniejacym modulem zwraca False."""
        from monolynx.services.permissions import check_permission

        project = await _create_project(db_session, slug="sp-perm-badmod")
        user = User(
            email="sp-perm-badmod@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        assert await check_permission(db_session, user.id, project.id, "nonexistent_module", "read") is False

    async def test_get_user_permissions_returns_empty_for_no_membership(self, db_session):
        """get_user_permissions zwraca {} dla usera bez membership."""
        from monolynx.services.permissions import get_user_permissions

        project = await _create_project(db_session, slug="sp-perm-getnomu")
        user = User(
            email="sp-perm-getnomu@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        perms = await get_user_permissions(db_session, user.id, project.id)
        assert perms == {}

    async def test_get_user_permissions_superuser_returns_all(self, db_session):
        """get_user_permissions dla superusera zwraca pelne uprawnienia."""
        from monolynx.constants import PERMISSION_ACTIONS, PERMISSION_MODULES
        from monolynx.services.permissions import get_user_permissions

        project = await _create_project(db_session, slug="sp-perm-getsu")
        user = User(
            email="sp-perm-getsu@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        perms = await get_user_permissions(db_session, user.id, project.id)
        for module in PERMISSION_MODULES:
            assert module in perms
            for action in PERMISSION_ACTIONS:
                assert action in perms[module]

    async def test_member_with_unknown_legacy_role_returns_empty_permissions(self, db_session):
        """Member z legacy role spoza DEFAULT_ROLE_PERMISSIONS i role_id=None -> {} (linia ~126).

        To inna sciezka niz 'brak membership' (member istnieje) oraz inna niz
        'legacy owner/admin/member' (rola nie ma wpisu w DEFAULT_ROLE_PERMISSIONS).
        Trafia ostatni 'return False' / 'return {}' w check_permission / get_user_permissions.
        """
        from monolynx.services.permissions import check_permission, get_user_permissions

        project = await _create_project(db_session, slug="sp-perm-unknownrole")
        user = User(
            email="sp-perm-unknownrole@test.com",
            password_hash=hash_password("pass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        # role="legacy_nonexistent" nie ma wpisu w DEFAULT_ROLE_PERMISSIONS
        # role_id=None wiec member.role_obj bedzie None
        member = ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role="legacy_nonexistent",
            role_id=None,
        )
        db_session.add(member)
        await db_session.flush()

        # get_user_permissions: member istnieje, brak role_obj, role nie w DEFAULT -> return {}
        perms = await get_user_permissions(db_session, user.id, project.id)
        assert perms == {}

        # check_permission: ta sama sciezka -> return False
        assert await check_permission(db_session, user.id, project.id, "scrum", "read") is False
        assert await check_permission(db_session, user.id, project.id, "settings", "delete") is False
