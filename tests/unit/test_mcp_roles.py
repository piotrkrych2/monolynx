"""Testy integracyjne MCP tools do zarzadzania rolami RBAC.

Testy pokrywaja: list_roles, create_role, update_role, delete_role,
                 assign_role, get_member_permissions.

Wzorzec: mock_factory (commit→flush) + mock_verify (token→user),
         analogicznie do test_mcp_server.py.

Uwaga: owner_member fixture zapewnia settings:read/write/delete przez
       DEFAULT_ROLE_PERMISSIONS["owner"] (fallback bez role_obj).
"""

from __future__ import annotations

import secrets
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.mcp_server import (
    assign_role,
    create_role,
    delete_role,
    get_member_permissions,
    list_roles,
    update_role,
)
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token w naglowku."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


async def _make_project(db, suffix: str) -> Project:
    project = Project(
        name=f"Projekt {suffix}",
        slug=f"projekt-{suffix}",
        code=secrets.token_hex(3).upper(),
        api_key=secrets.token_urlsafe(16),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_user(db, email: str) -> User:
    user = User(
        email=email,
        password_hash=hash_password("testpass"),
        is_active=True,
    )
    db.add(user)
    await db.flush()
    return user


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def owner_user(db_session):
    return await _make_user(db_session, f"roles-owner-{uuid.uuid4().hex[:8]}@test.com")


@pytest.fixture
async def roles_project(db_session):
    slug = f"roles-proj-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="Roles Test Project",
        slug=slug,
        code=slug.replace("-", "").upper()[:5],
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.fixture
async def owner_member(db_session, owner_user, roles_project):
    """Czlonkostwo owner w projekcie — fallback DEFAULT_ROLE_PERMISSIONS["owner"]
    daje pelne settings:read/write/delete bez potrzeby tworzenia Role."""
    member = ProjectMember(
        project_id=roles_project.id,
        user_id=owner_user.id,
        role="owner",
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest.fixture
def mock_factory(db_session):
    """Podmienia commit() na flush() — izolacja transakcji testowych."""
    original_commit = db_session.commit

    async def _flush_instead():
        await db_session.flush()

    @asynccontextmanager
    async def _factory():
        db_session.commit = _flush_instead
        try:
            yield db_session
        finally:
            db_session.commit = original_commit

    return _factory


@pytest.fixture
def mock_verify(owner_user):
    return AsyncMock(return_value=owner_user)


# ---------------------------------------------------------------------------
# list_roles — zwraca str (tabela formatowana) lub "Brak..." gdy brak ról
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListRoles:
    async def test_zwraca_komunikat_gdy_brak_rol(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """list_roles zwraca komunikat tekstowy gdy projekt nie ma ról."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_roles(ctx, roles_project.slug)

        assert isinstance(result, str)
        assert "Brak" in result

    async def test_zwraca_tabele_z_rola_projektu(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """list_roles zwraca sformatowana tabele zawierajaca nazwy ról."""
        role = Role(
            name="Developer",
            project_id=roles_project.id,
            permissions={"scrum": ["read", "write"], "wiki": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_roles(ctx, roles_project.slug)

        assert isinstance(result, str)
        assert "Developer" in result

    async def test_tabela_zawiera_naglowki(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """list_roles sformatowana tabela zawiera naglowki Name | System | Permissions."""
        role = Role(
            name="TesterRole",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_roles(ctx, roles_project.slug)

        assert "Name" in result
        assert "System" in result

    async def test_tabela_zawiera_id_roli(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """list_roles tabela zawiera UUID roli."""
        role = Role(
            name="IDTestRole",
            project_id=roles_project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_roles(ctx, roles_project.slug)

        assert str(role.id) in result

    async def test_nie_zwraca_rol_innych_projektow(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """list_roles nie wyswietla rol z innych projektow."""
        other_project = await _make_project(db_session, f"other-{uuid.uuid4().hex[:6]}")
        role_other = Role(
            name="OtherProjectRole",
            project_id=other_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role_other)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_roles(ctx, roles_project.slug)

        assert "OtherProjectRole" not in result


# ---------------------------------------------------------------------------
# create_role — zwraca dict: {id, name, permissions, message}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateRole:
    async def test_tworzy_role_z_permissions(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """create_role tworzy nowa role z podanymi uprawnieniami i zwraca dict."""
        ctx = _make_ctx()
        permissions = {"scrum": ["read", "write"], "wiki": ["read"]}

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_role(ctx, roles_project.slug, "DevRole", permissions)

        assert result["name"] == "DevRole"
        assert "id" in result
        assert result["permissions"] == permissions
        assert "message" in result

    async def test_tworzy_role_z_pustymi_uprawnieniami(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """create_role akceptuje puste permissions dict."""
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_role(ctx, roles_project.slug, "EmptyRole", {})

        assert result["name"] == "EmptyRole"
        assert result["permissions"] == {}

    async def test_duplikat_nazwy_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """create_role z duplikatem nazwy w tym samym projekcie rzuca ValueError."""
        existing_role = Role(
            name="ExistingRole",
            project_id=roles_project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(existing_role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="juz istnieje"),
        ):
            await create_role(ctx, roles_project.slug, "ExistingRole", {})

    async def test_nieznany_modul_w_permissions_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """create_role z nieznanym modulem w permissions rzuca ValueError."""
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowe moduly"),
        ):
            await create_role(ctx, roles_project.slug, "BadRole", {"nieznany_modul": ["read"]})

    async def test_nieznana_akcja_w_permissions_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """create_role z nieznana akcja w permissions rzuca ValueError."""
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowe akcje"),
        ):
            await create_role(ctx, roles_project.slug, "BadRole2", {"scrum": ["fly"]})

    async def test_pusta_nazwa_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """create_role z pusta nazwa rzuca ValueError."""
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="pusta"),
        ):
            await create_role(ctx, roles_project.slug, "  ", {})

    async def test_zbyt_dluga_nazwa_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """create_role z nazwa dluzszą niz 50 znaków rzuca ValueError."""
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="dluzsza"),
        ):
            await create_role(ctx, roles_project.slug, "R" * 51, {})


# ---------------------------------------------------------------------------
# update_role — zwraca dict: {id, name, permissions, message}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateRole:
    async def test_aktualizuje_nazwe(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """update_role aktualizuje nazwe niestandardowej roli."""
        role = Role(
            name="OldName",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_role(ctx, roles_project.slug, str(role.id), name="NewName")

        assert result["name"] == "NewName"
        assert "message" in result

    async def test_aktualizuje_permissions(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """update_role aktualizuje permissions roli."""
        role = Role(
            name="PatchPerms",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        new_perms = {"scrum": ["read", "write"], "wiki": ["read", "write", "delete"]}
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_role(ctx, roles_project.slug, str(role.id), permissions=new_perms)

        assert result["permissions"] == new_perms

    async def test_zmiana_nazwy_roli_systemowej_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """update_role nie moze zmienic nazwy roli systemowej (is_system=True)."""
        system_role = Role(
            name="Owner",
            project_id=roles_project.id,
            permissions={"scrum": ["read", "write", "delete"]},
            is_system=True,
        )
        db_session.add(system_role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="systemowej"),
        ):
            await update_role(ctx, roles_project.slug, str(system_role.id), name="HackedOwner")

    async def test_nieistniejaca_rola_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """update_role z nieistniejacym role_id rzuca ValueError."""
        ctx = _make_ctx()
        fake_id = str(uuid.uuid4())

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await update_role(ctx, roles_project.slug, fake_id, name="Ghost")

    async def test_nieprawidlowy_uuid_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """update_role z nieprawidlowym formatem role_id rzuca ValueError."""
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="UUID"),
        ):
            await update_role(ctx, roles_project.slug, "not-a-uuid", name="Test")

    async def test_aktualizacja_permissions_roli_systemowej_jest_dozwolona(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """update_role moze zmieniac permissions roli systemowej (tylko nie nazwe)."""
        system_role = Role(
            name="Admin",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=True,
        )
        db_session.add(system_role)
        await db_session.flush()

        new_perms = {"scrum": ["read", "write"]}
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await update_role(ctx, roles_project.slug, str(system_role.id), permissions=new_perms)

        assert result["permissions"] == new_perms


# ---------------------------------------------------------------------------
# delete_role — zwraca dict: {message}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDeleteRole:
    async def test_usuwa_custom_role_i_zwraca_komunikat(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """delete_role usuwa niestandardowa role i zwraca message."""
        role = Role(
            name="ToDelete",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await delete_role(ctx, roles_project.slug, str(role.id))

        assert "message" in result
        assert "ToDelete" in result["message"]

    async def test_usuniecie_roli_systemowej_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """delete_role nie pozwala usunac roli systemowej (is_system=True)."""
        system_role = Role(
            name="Member",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=True,
        )
        db_session.add(system_role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="systemowej"),
        ):
            await delete_role(ctx, roles_project.slug, str(system_role.id))

    async def test_usuniecie_roli_z_przypisanymi_czlonkami_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """delete_role rzuca ValueError gdy rola ma przypisanych czlonkow projektu."""
        role = Role(
            name="ActiveRole",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        member_user = User(
            email=f"role-member-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(member_user)
        await db_session.flush()

        project_member = ProjectMember(
            project_id=roles_project.id,
            user_id=member_user.id,
            role="member",
            role_id=role.id,
        )
        db_session.add(project_member)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="przypisana"),
        ):
            await delete_role(ctx, roles_project.slug, str(role.id))

    async def test_usuniecie_nieistniejacek_roli_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """delete_role z nieistniejacym role_id rzuca ValueError."""
        ctx = _make_ctx()
        fake_id = str(uuid.uuid4())

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await delete_role(ctx, roles_project.slug, fake_id)


# ---------------------------------------------------------------------------
# assign_role — zwraca dict: {message, user_email, role_id, role_name}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAssignRole:
    async def test_przypisuje_role_czlonkowi(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """assign_role przypisuje role do czlonka projektu."""
        role = Role(
            name="Tester",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        target_user = User(
            email=f"assign-target-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(target_user)
        await db_session.flush()

        db_session.add(
            ProjectMember(
                project_id=roles_project.id,
                user_id=target_user.id,
                role="member",
            )
        )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await assign_role(ctx, roles_project.slug, target_user.email, str(role.id))

        assert result["user_email"] == target_user.email.lower()
        assert result["role_id"] == str(role.id)
        assert result["role_name"] == "Tester"
        assert "message" in result

    async def test_assign_roli_z_innego_projektu_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """assign_role nie moze przypisac roli nalezacej do innego projektu."""
        other_project = await _make_project(db_session, f"foreign-{uuid.uuid4().hex[:6]}")
        foreign_role = Role(
            name="ForeignRole",
            project_id=other_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(foreign_role)
        await db_session.flush()

        target_user = User(
            email=f"assign-foreign-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(target_user)
        await db_session.flush()

        db_session.add(
            ProjectMember(
                project_id=roles_project.id,
                user_id=target_user.id,
                role="member",
            )
        )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await assign_role(ctx, roles_project.slug, target_user.email, str(foreign_role.id))

    async def test_assign_roli_uzytkownikowi_spoza_projektu_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """assign_role rzuca ValueError gdy user nie jest czlonkiem projektu."""
        role = Role(
            name="SomeRole",
            project_id=roles_project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        outside_user = User(
            email=f"outside-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(outside_user)
        await db_session.flush()
        # Brak ProjectMember — outside_user nie nalezy do projektu

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie jest czlonkiem"),
        ):
            await assign_role(ctx, roles_project.slug, outside_user.email, str(role.id))

    async def test_assign_roli_nieznanemu_emailowi_rzuca_value_error(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """assign_role rzuca ValueError gdy email nie istnieje w systemie."""
        role = Role(
            name="AnotherRole",
            project_id=roles_project.id,
            permissions={},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await assign_role(ctx, roles_project.slug, "nonexistent@ghost.com", str(role.id))


# ---------------------------------------------------------------------------
# get_member_permissions — zwraca dict: {user_email, project_slug, permissions}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetMemberPermissions:
    async def test_zwraca_uprawnienia_z_przypisanej_roli(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """get_member_permissions zwraca permissions wynikajace z role_obj."""
        expected_perms = {"scrum": ["read", "write"], "wiki": ["read"]}
        role = Role(
            name="DevPerms",
            project_id=roles_project.id,
            permissions=expected_perms,
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        target_user = User(
            email=f"perms-target-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(target_user)
        await db_session.flush()

        db_session.add(
            ProjectMember(
                project_id=roles_project.id,
                user_id=target_user.id,
                role="member",
                role_id=role.id,
            )
        )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_member_permissions(ctx, roles_project.slug, target_user.email)

        assert "permissions" in result
        assert result["permissions"] == expected_perms

    async def test_odpowiedz_zawiera_user_email_i_project_slug(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """get_member_permissions zawiera user_email i project_slug w odpowiedzi."""
        role = Role(
            name="BasicRole",
            project_id=roles_project.id,
            permissions={"scrum": ["read"]},
            is_system=False,
        )
        db_session.add(role)
        await db_session.flush()

        target_user = User(
            email=f"perms-email-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(target_user)
        await db_session.flush()

        db_session.add(
            ProjectMember(
                project_id=roles_project.id,
                user_id=target_user.id,
                role="member",
                role_id=role.id,
            )
        )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_member_permissions(ctx, roles_project.slug, target_user.email)

        assert result["user_email"] == target_user.email.lower()
        assert result["project_slug"] == roles_project.slug

    async def test_uzytkownik_bez_role_obj_zwraca_uprawnienia_domyslne(
        self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify
    ):
        """get_member_permissions uzytkownika bez role_obj (role_id=None)
        uzywa fallbacku DEFAULT_ROLE_PERMISSIONS dla roli 'member'."""
        target_user = User(
            email=f"perms-legacy-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(target_user)
        await db_session.flush()

        db_session.add(
            ProjectMember(
                project_id=roles_project.id,
                user_id=target_user.id,
                role="member",
                role_id=None,
            )
        )
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_member_permissions(ctx, roles_project.slug, target_user.email)

        assert "permissions" in result
        perms = result["permissions"]
        # DEFAULT_ROLE_PERMISSIONS["member"] zawiera scrum z read
        assert "scrum" in perms
        assert "read" in perms["scrum"]

    async def test_nieznany_email_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """get_member_permissions rzuca ValueError gdy email nie istnieje w systemie."""
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie istnieje"),
        ):
            await get_member_permissions(ctx, roles_project.slug, "ghost@nowhere.com")

    async def test_uzytkownik_spoza_projektu_rzuca_value_error(self, db_session, owner_user, roles_project, owner_member, mock_factory, mock_verify):
        """get_member_permissions rzuca ValueError gdy user nie jest czlonkiem projektu."""
        outside_user = User(
            email=f"perms-outside-{uuid.uuid4().hex[:8]}@test.com",
            password_hash=hash_password("pass"),
            is_active=True,
        )
        db_session.add(outside_user)
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie jest czlonkiem"),
        ):
            await get_member_permissions(ctx, roles_project.slug, outside_user.email)
