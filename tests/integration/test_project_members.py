"""Testy integracyjne -- zarzadzanie czlonkami projektu."""

import secrets

import pytest

from open_sentry.models.project import Project
from open_sentry.models.project_member import ProjectMember
from open_sentry.models.user import User
from open_sentry.services.auth import hash_password
from tests.conftest import login_session


@pytest.mark.integration
class TestMemberAdd:
    async def test_add_member_success(self, client, db_session):
        project = Project(
            name="MA Succ",
            slug="ma-succ",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)

        member_user = User(
            email="member-succ@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(member_user)
        await db_session.flush()

        await login_session(client, db_session, email="admin-ma-succ@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "member-succ@test.com", "role": "member"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings" in resp.headers["location"]

    async def test_add_nonexistent_user(self, client, db_session):
        project = Project(
            name="MA NonEx",
            slug="ma-nonex",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="admin-ma-nonex@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "noone@nowhere.com", "role": "member"},
        )
        assert resp.status_code == 200
        assert "nie istnieje" in resp.text

    async def test_add_duplicate_member(self, client, db_session):
        project = Project(
            name="MA Dup",
            slug="ma-dup",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)

        member_user = User(
            email="dup-member@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(member_user)
        await db_session.flush()

        existing = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role="member",
        )
        db_session.add(existing)
        await db_session.flush()

        await login_session(client, db_session, email="admin-ma-dup@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/add",
            data={"email": "dup-member@test.com", "role": "member"},
        )
        assert resp.status_code == 200
        assert "juz czlonkiem" in resp.text


@pytest.mark.integration
class TestMemberRemove:
    async def test_remove_member(self, client, db_session):
        project = Project(
            name="MR Rem",
            slug="mr-rem",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)

        member_user = User(
            email="rem-member@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(member_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role="member",
        )
        db_session.add(member)
        await db_session.flush()

        await login_session(client, db_session, email="admin-mr-rem@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/remove",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/settings" in resp.headers["location"]


@pytest.mark.integration
class TestMemberRole:
    async def test_change_role(self, client, db_session):
        project = Project(
            name="MR Role",
            slug="mr-role",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)

        member_user = User(
            email="role-member@test.com",
            password_hash=hash_password("pass123"),
        )
        db_session.add(member_user)
        await db_session.flush()

        member = ProjectMember(
            project_id=project.id,
            user_id=member_user.id,
            role="member",
        )
        db_session.add(member)
        await db_session.flush()

        await login_session(client, db_session, email="admin-mr-role@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/settings/members/{member.id}/role",
            data={"role": "admin"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

    async def test_settings_shows_members(self, client, db_session):
        project = Project(
            name="MR Show",
            slug="mr-show",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)

        member_user = User(
            email="show-member@test.com",
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

        await login_session(client, db_session, email="admin-mr-show@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/settings")
        assert resp.status_code == 200
        assert "show-member@test.com" in resp.text
        assert "Czlonkowie projektu" in resp.text
