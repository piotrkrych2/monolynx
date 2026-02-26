"""Testy integracyjne -- ustawienia projektu (edycja, usuwanie, czlonkowie)."""

import secrets
import uuid

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
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
            data={"role": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        await db_session.refresh(member)
        assert member.role == "admin"

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
