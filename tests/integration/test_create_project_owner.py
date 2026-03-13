"""Testy integracyjne -- tworca projektu automatycznie dodawany jako owner."""

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from tests.conftest import login_session


@pytest.mark.integration
class TestCreateProjectOwner:
    """Tworzenie projektu automatycznie dodaje twórce jako ownera."""

    async def test_creator_added_as_owner(self, client, db_session):
        """Po stworzeniu projektu tworca jest czlonkiem z rola owner."""
        user = User(
            email="owner-auto@test.com",
            password_hash=hash_password("testpass123"),
        )
        db_session.add(user)
        await db_session.flush()

        await client.post(
            "/auth/login",
            data={"email": "owner-auto@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Owner Test", "slug": "owner-test", "code": "OWT"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Project).where(Project.slug == "owner-test"))
        project = result.scalar_one()

        result = await db_session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == user.id,
            )
        )
        member = result.scalar_one()
        assert member.role == "owner"

    async def test_creator_sees_project_as_non_superuser(self, client, db_session):
        """Zwykly uzytkownik (non-superuser) widzi swoj projekt na liscie."""
        user = User(
            email="owner-sees@test.com",
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        await client.post(
            "/auth/login",
            data={"email": "owner-sees@test.com", "password": "testpass123"},
            follow_redirects=False,
        )

        await client.post(
            "/dashboard/create-project",
            data={"name": "Visible Project", "slug": "visible-proj", "code": "VIS"},
            follow_redirects=False,
        )

        resp = await client.get("/dashboard/")
        assert resp.status_code == 200
        assert "Visible Project" in resp.text

    async def test_only_one_member_after_create(self, client, db_session):
        """Po stworzeniu projektu jest dokladnie 1 czlonek (owner)."""
        client = await login_session(client, db_session, email="owner-one@test.com")

        await client.post(
            "/dashboard/create-project",
            data={"name": "Single Member", "slug": "single-member", "code": "SGL"},
            follow_redirects=False,
        )

        result = await db_session.execute(select(Project).where(Project.slug == "single-member"))
        project = result.scalar_one()

        result = await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id))
        members = result.scalars().all()
        assert len(members) == 1
        assert members[0].role == "owner"

    async def test_duplicate_slug_shows_error(self, client, db_session):
        """Proba stworzenia projektu z duplikatem sluga pokazuje blad."""
        client = await login_session(client, db_session, email="owner-dup@test.com")

        # Stworz pierwszy projekt
        resp1 = await client.post(
            "/dashboard/create-project",
            data={"name": "First", "slug": "dup-owner", "code": "DUO"},
            follow_redirects=False,
        )
        assert resp1.status_code == 303

        # Proba z duplikatem
        resp = await client.post(
            "/dashboard/create-project",
            data={"name": "Second", "slug": "dup-owner", "code": "DU2"},
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert "juz istnieje" in resp.text
