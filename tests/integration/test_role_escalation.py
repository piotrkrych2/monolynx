"""Testy ochrony przed eskalacją uprawnień przez zarządzanie rolami (MON-243).

Kontrakt (kanały: MCP `create_role`/`update_role`/`assign_role`/`invite_member` oraz dashboard
`member_role`/`member_add`/`role_create`/`role_edit`; API v2 w `api_v2/test_projects.py`):

1. "nie nadasz więcej, niż masz": uprawnienia roli tworzonej, edytowanej, przypisywanej albo nadawanej przy
   zaproszeniu muszą być podzbiorem uprawnień wykonującego; superuser i owner nie mają tego ograniczenia,
2. zmienić rolę członka-ownera albo przypisać systemową rolę Owner może tylko owner albo superuser; owner to członek,
   którego `role_id` wskazuje rolę `is_system=True` o nazwie "Owner", a bez `role_id` - legacy `member.role == "owner"`,
3. `permissions` ról systemowych są niemodyfikowalne dla wszystkich (także ownera i superusera),
4. nikt nie przypisze sobie roli z większą liczbą uprawnień,
5. odpowiedź MCP `invite_member` nie zawiera tokenu zaproszenia (mail dostaje token z bazy).

Odmowa w MCP to `ValueError("Brak uprawnienia ...")`; w dashboardzie `member_role`/`member_add` kończą się flashem
i redirectem 303, a `role_create`/`role_edit` ponownym renderem formularza z błędem. Każdy test odmowy asertuje stan bazy
zapytaniem o kolumny (autoflush w sesji współdzielonej z requestem pokazuje też zapis dokonany przed rzuceniem wyjątku),
a każdy test sukcesu - faktyczną zmianę w bazie. Sesja testowa jest współdzielona, więc testy nie dowodzą commita
(`db-commit.md`).
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx import mcp_server
from monolynx.constants import DEFAULT_ROLE_PERMISSIONS, PERMISSION_ACTIONS, PERMISSION_MODULES
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from tests.conftest import login_session
from tests.integration.api_v2.conftest import ApiV2Actor, make_actor, make_project

pytestmark = pytest.mark.integration

_ADMIN_PERMS = DEFAULT_ROLE_PERMISSIONS["admin"]
_OWNER_PERMS = DEFAULT_ROLE_PERMISSIONS["owner"]
_MEMBER_PERMS = DEFAULT_ROLE_PERMISSIONS["member"]
_DENIAL = "Brak uprawnienia"


# ---------------------------------------------------------------------------
# Środowisko
# ---------------------------------------------------------------------------


@dataclass
class Seat:
    """Użytkownik testowy z członkostwem w projekcie świata."""

    actor: ApiV2Actor
    member: ProjectMember

    @property
    def user(self) -> User:
        return self.actor.user

    @property
    def email(self) -> str:
        return self.actor.user.email


@dataclass
class World:
    db: AsyncSession
    project: Project
    owner_role: Role
    admin_role: Role
    member_role: Role
    mail: MagicMock

    async def join(self, user: User, legacy_role: str = "member", role: Role | None = None) -> ProjectMember:
        member = ProjectMember(project_id=self.project.id, user_id=user.id, role=legacy_role, role_id=role.id if role else None)
        self.db.add(member)
        await self.db.flush()
        return member

    async def seat(self, tag: str, legacy_role: str = "member", role: Role | None = None, *, is_superuser: bool = False) -> Seat:
        actor = await make_actor(self.db, f"esc-{tag}", is_superuser=is_superuser)
        return Seat(actor, await self.join(actor.user, legacy_role, role))

    async def custom_role(self, name: str, perms: dict[str, list[str]]) -> Role:
        role = Role(name=name, project_id=self.project.id, permissions=perms, is_system=False)
        self.db.add(role)
        await self.db.flush()
        return role

    async def member_state(self, seat: Seat) -> tuple[str | None, uuid.UUID | None]:
        row = (
            await self.db.execute(
                select(ProjectMember.role, ProjectMember.role_id).where(
                    ProjectMember.project_id == self.project.id, ProjectMember.user_id == seat.user.id
                )
            )
        ).one()
        return row.role, row.role_id

    async def role_state(self, role: Role) -> tuple[str, dict[str, list[str]]]:
        row = (await self.db.execute(select(Role.name, Role.permissions).where(Role.id == role.id))).one()
        return row.name, row.permissions

    async def role_by_name(self, name: str) -> Role | None:
        return (await self.db.execute(select(Role).where(Role.project_id == self.project.id, Role.name == name))).scalar_one_or_none()

    async def has_membership(self, user: User) -> bool:
        found = (
            await self.db.execute(select(ProjectMember.id).where(ProjectMember.project_id == self.project.id, ProjectMember.user_id == user.id))
        ).scalar_one_or_none()
        return found is not None


def _member_plus_users_write() -> dict[str, list[str]]:
    """Uprawnienia systemowej roli "member" plus users:write - nic ponad to, w tym nic z admina."""
    perms = {module: list(actions) for module, actions in _MEMBER_PERMS.items()}
    perms["users"] = ["read", "write"]
    return perms


def _ctx(actor: ApiV2Actor) -> MagicMock:
    token = actor.headers["Authorization"].removeprefix("Bearer ")
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


async def _seed_system_roles(db: AsyncSession, project: Project) -> tuple[Role, Role, Role]:
    roles = [
        Role(name=name, project_id=project.id, permissions=DEFAULT_ROLE_PERMISSIONS[key], is_system=True)
        for name, key in (("Owner", "owner"), ("Admin", "admin"), ("Member", "member"))
    ]
    db.add_all(roles)
    await db.flush()
    return roles[0], roles[1], roles[2]


@pytest.fixture
async def world(db_session, monkeypatch) -> World:
    @asynccontextmanager
    async def session_factory():
        yield db_session

    mail = MagicMock()
    monkeypatch.setattr(mcp_server, "async_session_factory", session_factory)
    monkeypatch.setattr(mcp_server, "send_invitation_email", mail)

    project = await make_project(db_session, "esc")
    owner_role, admin_role, member_role = await _seed_system_roles(db_session, project)
    return World(db=db_session, project=project, owner_role=owner_role, admin_role=admin_role, member_role=member_role, mail=mail)


async def _assign(world: World, caller: Seat, target: Seat, role: Role) -> dict[str, Any]:
    return await mcp_server.assign_role(_ctx(caller.actor), world.project.slug, target.email, str(role.id))


async def _assert_assign_denied(world: World, caller: Seat, target: Seat, role: Role) -> None:
    with pytest.raises(ValueError, match=_DENIAL):
        await _assign(world, caller, target, role)


# ---------------------------------------------------------------------------
# MCP assign_role
# ---------------------------------------------------------------------------


class TestAssignRoleEscalation:
    async def test_admin_cannot_assign_system_owner_role_to_self(self, world):
        admin = await world.seat("admin", "admin")

        await _assert_assign_denied(world, admin, admin, world.owner_role)

        assert await world.member_state(admin) == ("admin", None)

    async def test_admin_cannot_assign_system_owner_role_to_another_member(self, world):
        admin = await world.seat("admin", "admin")
        target = await world.seat("target", "member")

        await _assert_assign_denied(world, admin, target, world.owner_role)

        assert await world.member_state(target) == ("member", None)

    async def test_admin_cannot_change_role_of_legacy_owner_to_system_member(self, world):
        admin = await world.seat("admin", "admin")
        boss = await world.seat("boss", "owner")

        await _assert_assign_denied(world, admin, boss, world.member_role)

        assert await world.member_state(boss) == ("owner", None)

    async def test_admin_cannot_change_role_of_legacy_owner_to_harmless_custom_role(self, world):
        admin = await world.seat("admin", "admin")
        boss = await world.seat("boss", "owner")
        harmless = await world.custom_role("Czytelnik", {"scrum": ["read"]})

        await _assert_assign_denied(world, admin, boss, harmless)

        assert await world.member_state(boss) == ("owner", None)

    async def test_admin_cannot_change_role_of_member_holding_system_owner_role_even_with_stale_legacy_string(self, world):
        admin = await world.seat("admin", "admin")
        boss = await world.seat("boss", "member", world.owner_role)

        await _assert_assign_denied(world, admin, boss, world.member_role)

        assert await world.member_state(boss) == ("member", world.owner_role.id)

    async def test_admin_cannot_assign_custom_role_above_own_permissions(self, world):
        admin = await world.seat("admin", "admin")
        target = await world.seat("target", "member")
        above = await world.custom_role("Ponad admina", {"scrum": ["read"], "settings": ["read", "write", "delete"]})

        await _assert_assign_denied(world, admin, target, above)

        assert await world.member_state(target) == ("member", None)

    async def test_admin_can_assign_custom_role_within_own_permissions(self, world):
        admin = await world.seat("admin", "admin")
        target = await world.seat("target", "member")
        within = await world.custom_role("Reviewer", {"scrum": ["read", "write"], "wiki": ["read"]})

        result = await _assign(world, admin, target, within)

        assert result["role_id"] == str(within.id)
        assert await world.member_state(target) == ("reviewer", within.id)

    async def test_admin_can_assign_custom_role_equal_to_own_permissions(self, world):
        admin = await world.seat("admin", "admin")
        target = await world.seat("target", "member")
        equal = await world.custom_role("Jak admin", {module: list(actions) for module, actions in _ADMIN_PERMS.items()})

        await _assign(world, admin, target, equal)

        assert await world.member_state(target) == ("jak admin", equal.id)

    async def test_admin_can_assign_system_member_role_to_plain_member(self, world):
        admin = await world.seat("admin", "admin")
        target = await world.seat("target", "member")

        await _assign(world, admin, target, world.member_role)

        assert await world.member_state(target) == ("member", world.member_role.id)


class TestSelfPromotion:
    async def test_member_with_custom_role_cannot_assign_self_role_with_more_permissions(self, world):
        mine = await world.custom_role("Moja", {"settings": ["read", "write"]})
        bigger = await world.custom_role("Większa", {"settings": ["read", "write"], "scrum": ["read", "write"]})
        me = await world.seat("me", "member", mine)

        await _assert_assign_denied(world, me, me, bigger)

        assert await world.member_state(me) == ("member", mine.id)

    async def test_member_with_custom_role_can_assign_self_role_with_equal_permissions(self, world):
        mine = await world.custom_role("Moja", {"settings": ["read", "write"]})
        equal = await world.custom_role("Równa", {"settings": ["read", "write"]})
        me = await world.seat("me", "member", mine)

        await _assign(world, me, me, equal)

        assert await world.member_state(me) == ("równa", equal.id)

    async def test_member_with_custom_role_can_assign_self_role_with_fewer_permissions(self, world):
        mine = await world.custom_role("Moja", {"settings": ["read", "write"], "wiki": ["read"]})
        lower = await world.custom_role("Niższa", {"settings": ["read"]})
        me = await world.seat("me", "member", mine)

        await _assign(world, me, me, lower)

        assert await world.member_state(me) == ("niższa", lower.id)


# ---------------------------------------------------------------------------
# Rozpoznawanie ownera
# ---------------------------------------------------------------------------


class TestOwnerRecognition:
    async def test_member_with_legacy_owner_string_but_custom_role_is_not_protected(self, world):
        """Z `role_id` liczy się rola, nie stary napis `member.role`."""
        admin = await world.seat("admin", "admin")
        custom = await world.custom_role("Zwykła", {"scrum": ["read"]})
        not_owner = await world.seat("notowner", "owner", custom)

        await _assign(world, admin, not_owner, world.member_role)

        assert await world.member_state(not_owner) == ("member", world.member_role.id)

    async def test_actor_holding_system_owner_role_is_owner_even_with_stale_legacy_string(self, world):
        owner_by_role = await world.seat("ownerbyrole", "member", world.owner_role)
        target = await world.seat("target", "member")

        await _assign(world, owner_by_role, target, world.owner_role)

        assert await world.member_state(target) == ("owner", world.owner_role.id)

    async def test_actor_with_full_permission_custom_role_and_legacy_owner_string_is_not_owner(self, world):
        full = await world.custom_role("Wszystko", {module: list(PERMISSION_ACTIONS) for module in PERMISSION_MODULES})
        fake = await world.seat("fake", "owner", full)
        target = await world.seat("target", "member")

        await _assert_assign_denied(world, fake, target, world.owner_role)

        assert await world.member_state(target) == ("member", None)

    async def test_non_system_role_named_owner_does_not_make_actor_an_owner(self, world, db_session):
        """Rola niesystemowa "Owner" (uprawnienia admina) nie zmieni roli legacy ownera (osobny projekt: nazwa jest unikalna per projekt)."""
        project = await make_project(db_session, "esc-fake")
        fake_owner_role = Role(name="Owner", project_id=project.id, permissions=_ADMIN_PERMS, is_system=False)
        harmless = Role(name="Inna", project_id=project.id, permissions={"scrum": ["read"]}, is_system=False)
        db_session.add_all([fake_owner_role, harmless])
        await db_session.flush()
        actor = await make_actor(db_session, "esc-fake-actor")
        boss = await make_actor(db_session, "esc-fake-boss")
        db_session.add_all(
            [
                ProjectMember(project_id=project.id, user_id=actor.user.id, role="member", role_id=fake_owner_role.id),
                ProjectMember(project_id=project.id, user_id=boss.user.id, role="owner"),
            ]
        )
        await db_session.flush()

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.assign_role(_ctx(actor), project.slug, boss.user.email, str(harmless.id))

        row = (await db_session.execute(select(ProjectMember.role, ProjectMember.role_id).where(ProjectMember.user_id == boss.user.id))).one()
        assert (row.role, row.role_id) == ("owner", None)

    async def test_member_holding_non_system_role_named_owner_is_not_protected(self, world, db_session):
        project = await make_project(db_session, "esc-fakevictim")
        fake_owner_role = Role(name="Owner", project_id=project.id, permissions=_ADMIN_PERMS, is_system=False)
        other = Role(name="Inna", project_id=project.id, permissions={"scrum": ["read"]}, is_system=False)
        db_session.add_all([fake_owner_role, other])
        await db_session.flush()
        admin = await make_actor(db_session, "esc-fakevictim-admin")
        victim = await make_actor(db_session, "esc-fakevictim-victim")
        db_session.add_all(
            [
                ProjectMember(project_id=project.id, user_id=admin.user.id, role="admin"),
                ProjectMember(project_id=project.id, user_id=victim.user.id, role="owner", role_id=fake_owner_role.id),
            ]
        )
        await db_session.flush()

        await mcp_server.assign_role(_ctx(admin), project.slug, victim.user.email, str(other.id))

        row = (await db_session.execute(select(ProjectMember.role, ProjectMember.role_id).where(ProjectMember.user_id == victim.user.id))).one()
        assert (row.role, row.role_id) == ("inna", other.id)


# ---------------------------------------------------------------------------
# MCP update_role: permissions ról systemowych niemodyfikowalne
# ---------------------------------------------------------------------------


class TestSystemRolePermissionsImmutable:
    @pytest.mark.parametrize("caller_kind", ["admin", "owner", "superuser"])
    @pytest.mark.parametrize("target", ["owner_role", "admin_role", "member_role"])
    async def test_update_role_permissions_of_system_role_is_denied_for_everyone(self, caller_kind, target, world):
        caller = await _caller(world, caller_kind)
        role = getattr(world, target)
        before = await world.role_state(role)

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.update_role(_ctx(caller.actor), world.project.slug, str(role.id), permissions={"scrum": ["read"]})

        assert await world.role_state(role) == before

    async def test_update_role_with_name_and_permissions_of_system_role_changes_neither(self, world):
        owner = await world.seat("owner", "owner")
        before = await world.role_state(world.admin_role)

        with pytest.raises(ValueError, match=rf"{_DENIAL}|Nie mozna zmienic nazwy roli systemowej"):
            await mcp_server.update_role(
                _ctx(owner.actor), world.project.slug, str(world.admin_role.id), name="Zmieniona", permissions={"scrum": ["read"]}
            )

        assert await world.role_state(world.admin_role) == before


async def _caller(world: World, kind: str) -> Seat:
    if kind == "admin":
        return await world.seat("caller-admin", "admin")
    if kind == "owner":
        return await world.seat("caller-owner", "owner")
    return await world.seat("caller-superuser", "member", is_superuser=True)


# ---------------------------------------------------------------------------
# MCP create_role / update_role: nie nadasz więcej, niż masz
# ---------------------------------------------------------------------------


class TestCreateRoleEscalation:
    @pytest.mark.parametrize(
        "permissions",
        [
            {"settings": ["read", "write", "delete"]},
            {"users": ["delete"]},
            {"rozliczenia": ["read", "write", "delete"]},
            {"scrum": ["read", "write"], "settings": ["delete"]},
        ],
        ids=["settings-delete", "users-delete", "rozliczenia-delete", "mixed-with-allowed"],
    )
    async def test_admin_cannot_create_role_with_permission_he_lacks(self, permissions, world):
        admin = await world.seat("admin", "admin")

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.create_role(_ctx(admin.actor), world.project.slug, "Eskalacja", permissions)

        assert await world.role_by_name("Eskalacja") is None

    async def test_admin_can_create_role_with_subset_of_own_permissions(self, world):
        admin = await world.seat("admin", "admin")

        result = await mcp_server.create_role(_ctx(admin.actor), world.project.slug, "Podzbiór", {"scrum": ["read", "write"], "wiki": ["read"]})

        created = await world.role_by_name("Podzbiór")
        assert created is not None
        assert str(created.id) == result["id"]
        assert created.permissions == {"scrum": ["read", "write"], "wiki": ["read"]}
        assert created.is_system is False

    async def test_admin_can_create_role_with_exactly_own_permissions(self, world):
        admin = await world.seat("admin", "admin")
        own = {module: list(actions) for module, actions in _ADMIN_PERMS.items()}

        await mcp_server.create_role(_ctx(admin.actor), world.project.slug, "Dokładnie admin", own)

        created = await world.role_by_name("Dokładnie admin")
        assert created is not None
        assert created.permissions == own

    @pytest.mark.parametrize("caller_kind", ["owner", "superuser"])
    async def test_owner_and_superuser_can_create_role_with_full_permissions(self, caller_kind, world):
        caller = await _caller(world, caller_kind)
        full = {module: list(PERMISSION_ACTIONS) for module in PERMISSION_MODULES}

        await mcp_server.create_role(_ctx(caller.actor), world.project.slug, "Pełna", full)

        created = await world.role_by_name("Pełna")
        assert created is not None
        assert created.permissions == full


class TestUpdateRoleEscalation:
    async def test_admin_cannot_add_permission_he_lacks_to_custom_role(self, world):
        admin = await world.seat("admin", "admin")
        role = await world.custom_role("Edytowana", {"scrum": ["read"]})
        before = await world.role_state(role)

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.update_role(_ctx(admin.actor), world.project.slug, str(role.id), permissions={"scrum": ["read"], "settings": ["delete"]})

        assert await world.role_state(role) == before

    async def test_admin_denied_update_does_not_apply_the_rename_either(self, world):
        admin = await world.seat("admin", "admin")
        role = await world.custom_role("Edytowana", {"scrum": ["read"]})
        before = await world.role_state(role)

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.update_role(_ctx(admin.actor), world.project.slug, str(role.id), name="Nowa nazwa", permissions={"users": ["delete"]})

        assert await world.role_state(role) == before

    async def test_admin_can_set_custom_role_permissions_to_subset_of_own(self, world):
        admin = await world.seat("admin", "admin")
        role = await world.custom_role("Edytowana", {"scrum": ["read"]})

        await mcp_server.update_role(_ctx(admin.actor), world.project.slug, str(role.id), permissions={"wiki": ["read", "write"]})

        assert await world.role_state(role) == ("Edytowana", {"wiki": ["read", "write"]})

    async def test_admin_can_rename_custom_role_without_touching_permissions(self, world):
        admin = await world.seat("admin", "admin")
        role = await world.custom_role("Stara nazwa", {"scrum": ["read"]})

        await mcp_server.update_role(_ctx(admin.actor), world.project.slug, str(role.id), name="Nowa nazwa")

        assert await world.role_state(role) == ("Nowa nazwa", {"scrum": ["read"]})

    @pytest.mark.parametrize("caller_kind", ["owner", "superuser"])
    async def test_owner_and_superuser_can_give_custom_role_full_permissions(self, caller_kind, world):
        caller = await _caller(world, caller_kind)
        role = await world.custom_role("Edytowana", {"scrum": ["read"]})
        full = {module: list(PERMISSION_ACTIONS) for module in PERMISSION_MODULES}

        await mcp_server.update_role(_ctx(caller.actor), world.project.slug, str(role.id), permissions=full)

        assert await world.role_state(role) == ("Edytowana", full)


# ---------------------------------------------------------------------------
# Owner i superuser: bez ograniczeń reguły podzbioru i ochrony ownera
# ---------------------------------------------------------------------------


class TestOwnerAndSuperuserAreNotRestricted:
    @pytest.mark.parametrize("caller_kind", ["owner", "superuser"])
    async def test_can_assign_system_owner_role_to_another_member(self, caller_kind, world):
        caller = await _caller(world, caller_kind)
        target = await world.seat("target", "member")

        await _assign(world, caller, target, world.owner_role)

        assert await world.member_state(target) == ("owner", world.owner_role.id)

    @pytest.mark.parametrize("caller_kind", ["owner", "superuser"])
    async def test_can_change_role_of_another_owner(self, caller_kind, world):
        caller = await _caller(world, caller_kind)
        boss = await world.seat("boss", "owner")

        await _assign(world, caller, boss, world.member_role)

        assert await world.member_state(boss) == ("member", world.member_role.id)

    @pytest.mark.parametrize("caller_kind", ["owner", "superuser"])
    async def test_can_assign_custom_role_with_full_permissions(self, caller_kind, world):
        caller = await _caller(world, caller_kind)
        target = await world.seat("target", "member")
        full = await world.custom_role("Pełna", {module: list(PERMISSION_ACTIONS) for module in PERMISSION_MODULES})

        await _assign(world, caller, target, full)

        assert await world.member_state(target) == ("pełna", full.id)


# ---------------------------------------------------------------------------
# MCP invite_member: brak tokenu w odpowiedzi, rola nie większa niż zapraszającego
# ---------------------------------------------------------------------------


class TestInviteMemberMcp:
    async def test_new_account_response_has_no_invitation_token_and_mail_carries_it(self, world):
        owner = await world.seat("owner", "owner")
        email = f"esc-invitee-{uuid.uuid4().hex[:8]}@example.com"

        result = await mcp_server.invite_member(_ctx(owner.actor), world.project.slug, email, role="member")

        stored = (await world.db.execute(select(User.invitation_token).where(User.email == email))).scalar_one()
        assert stored is not None
        assert "invitation_id" not in result
        assert str(stored) not in json.dumps(result, default=str)
        assert "expires_at" in result
        world.mail.assert_called_once_with(email, "", stored)

    async def test_custom_role_with_only_users_write_cannot_invite_admin_new_account(self, world):
        only_users = await world.custom_role("Tylko users", {"users": ["read", "write"]})
        inviter = await world.seat("inviter", "member", only_users)
        email = f"esc-new-admin-{uuid.uuid4().hex[:8]}@example.com"

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.invite_member(_ctx(inviter.actor), world.project.slug, email, role="admin")

        assert (await world.db.execute(select(User).where(User.email == email))).scalar_one_or_none() is None
        world.mail.assert_not_called()

    async def test_custom_role_with_only_users_write_cannot_invite_admin_existing_account(self, world):
        only_users = await world.custom_role("Tylko users", {"users": ["read", "write"]})
        inviter = await world.seat("inviter", "member", only_users)
        existing = await make_actor(world.db, "esc-existing")

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.invite_member(_ctx(inviter.actor), world.project.slug, existing.user.email, role="admin")

        assert await world.has_membership(existing.user) is False
        world.mail.assert_not_called()

    async def test_custom_role_with_only_users_write_cannot_invite_member_role_with_more_permissions(self, world):
        only_users = await world.custom_role("Tylko users", {"users": ["read", "write"]})
        inviter = await world.seat("inviter", "member", only_users)
        existing = await make_actor(world.db, "esc-existing-member")

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.invite_member(_ctx(inviter.actor), world.project.slug, existing.user.email, role="member")

        assert await world.has_membership(existing.user) is False

    async def test_inviter_with_all_member_permissions_plus_users_write_can_invite_member_role(self, world):
        """Rola zapraszającego = wszystko z "member" + users:write, nic ponad to: granica dla roli "member"."""
        inviter = await world.seat("inviter", "member", await world.custom_role("Member plus", _member_plus_users_write()))
        email = f"esc-member-ok-{uuid.uuid4().hex[:8]}@example.com"

        await mcp_server.invite_member(_ctx(inviter.actor), world.project.slug, email, role="member")

        invited = (await world.db.execute(select(User).where(User.email == email))).scalar_one()
        assert await world.has_membership(invited) is True
        world.mail.assert_called_once_with(email, "", invited.invitation_token)

    async def test_inviter_with_all_member_permissions_plus_users_write_can_add_existing_account_as_member(self, world):
        inviter = await world.seat("inviter", "member", await world.custom_role("Member plus", _member_plus_users_write()))
        existing = await make_actor(world.db, "esc-existing-member-ok")

        await mcp_server.invite_member(_ctx(inviter.actor), world.project.slug, existing.user.email, role="member")

        assert await world.has_membership(existing.user) is True

    async def test_inviter_with_all_member_permissions_plus_users_write_cannot_invite_admin_role(self, world):
        """Brakuje tylko uprawnień ponad "member": odmowa pada wyłącznie przez porównanie z rolą z zaproszenia."""
        inviter = await world.seat("inviter", "member", await world.custom_role("Member plus", _member_plus_users_write()))
        email = f"esc-member-admin-{uuid.uuid4().hex[:8]}@example.com"
        existing = await make_actor(world.db, "esc-existing-member-admin")

        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.invite_member(_ctx(inviter.actor), world.project.slug, email, role="admin")
        with pytest.raises(ValueError, match=_DENIAL):
            await mcp_server.invite_member(_ctx(inviter.actor), world.project.slug, existing.user.email, role="admin")

        assert (await world.db.execute(select(User).where(User.email == email))).scalar_one_or_none() is None
        assert await world.has_membership(existing.user) is False
        world.mail.assert_not_called()

    async def test_admin_can_invite_admin(self, world):
        admin = await world.seat("admin", "admin")
        existing = await make_actor(world.db, "esc-existing-admin")

        await mcp_server.invite_member(_ctx(admin.actor), world.project.slug, existing.user.email, role="admin")

        row = (
            await world.db.execute(
                select(ProjectMember.role).where(ProjectMember.project_id == world.project.id, ProjectMember.user_id == existing.user.id)
            )
        ).scalar_one()
        assert row == "admin"


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------


async def _login(client, world: World, tag: str, legacy_role: str, role: Role | None = None) -> Seat:
    """Zwykły (nie-superuser) użytkownik zalogowany w sesji dashboardu, z członkostwem w projekcie świata."""
    email = f"esc-dash-{tag}-{uuid.uuid4().hex[:8]}@example.com"
    await login_session(client, world.db, email=email, is_superuser=False)
    user = (await world.db.execute(select(User).where(User.email == email))).scalar_one()
    member = await world.join(user, legacy_role, role)
    return Seat(ApiV2Actor(user=user, headers={}), member)


def _perm_form(perms: dict[str, list[str]]) -> dict[str, str]:
    return {f"perm_{module}_{action}": "on" for module, actions in perms.items() for action in actions}


class TestDashboardMemberRole:
    async def test_admin_cannot_set_system_owner_role_on_self(self, client, world):
        admin = await _login(client, world, "admin", "admin")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/{admin.member.id}/role",
            data={"role_id": str(world.owner_role.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.member_state(admin) == ("admin", None)

    async def test_denial_is_reported_as_flash_on_the_settings_page(self, client, world):
        admin = await _login(client, world, "admin", "admin")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/{admin.member.id}/role",
            data={"role_id": str(world.owner_role.id)},
            follow_redirects=False,
        )
        page = await client.get(resp.headers["location"])

        assert page.status_code == 200
        assert _DENIAL in page.text

    async def test_admin_cannot_change_role_of_owner_member(self, client, world):
        await _login(client, world, "admin", "admin")
        boss = await world.seat("boss", "owner")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/{boss.member.id}/role",
            data={"role_id": str(world.member_role.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.member_state(boss) == ("owner", None)

    async def test_admin_cannot_give_member_custom_role_above_own_permissions(self, client, world):
        await _login(client, world, "admin", "admin")
        target = await world.seat("target", "member")
        above = await world.custom_role("Ponad admina", {"settings": ["delete"]})

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/{target.member.id}/role",
            data={"role_id": str(above.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.member_state(target) == ("member", None)

    async def test_admin_can_give_member_custom_role_within_own_permissions(self, client, world):
        await _login(client, world, "admin", "admin")
        target = await world.seat("target", "member")
        within = await world.custom_role("Reviewer", {"scrum": ["read", "write"]})

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/{target.member.id}/role",
            data={"role_id": str(within.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.member_state(target) == ("reviewer", within.id)

    async def test_owner_can_set_system_owner_role_on_another_member(self, client, world):
        await _login(client, world, "owner", "owner")
        target = await world.seat("target", "member")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/{target.member.id}/role",
            data={"role_id": str(world.owner_role.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.member_state(target) == ("owner", world.owner_role.id)

    async def test_owner_can_change_role_of_another_owner(self, client, world):
        await _login(client, world, "owner", "owner")
        boss = await world.seat("boss", "owner")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/{boss.member.id}/role",
            data={"role_id": str(world.member_role.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.member_state(boss) == ("member", world.member_role.id)


class TestDashboardMemberAdd:
    async def test_admin_cannot_add_member_with_system_owner_role(self, client, world):
        await _login(client, world, "admin", "admin")
        newcomer = await make_actor(world.db, "esc-newcomer")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/add",
            data={"email": newcomer.user.email, "role_id": str(world.owner_role.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.has_membership(newcomer.user) is False

    async def test_admin_cannot_add_member_with_custom_role_above_own_permissions(self, client, world):
        await _login(client, world, "admin", "admin")
        newcomer = await make_actor(world.db, "esc-newcomer-above")
        above = await world.custom_role("Ponad admina", {"settings": ["delete"]})

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/add",
            data={"email": newcomer.user.email, "role_id": str(above.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.has_membership(newcomer.user) is False

    async def test_actor_narrower_than_member_role_cannot_add_member_without_role_id(self, client, world):
        """Bez `role_id` członek dostaje domyślną rolę "member"; aktor z samym settings:read/write nie nada jej."""
        narrow = await world.custom_role("Wąska", {"settings": ["read", "write"]})
        await _login(client, world, "narrow", "member", narrow)
        newcomer = await make_actor(world.db, "esc-newcomer-narrow")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/add",
            data={"email": newcomer.user.email},
            follow_redirects=False,
        )
        page = await client.get(resp.headers["location"])

        assert resp.status_code == 303
        assert await world.has_membership(newcomer.user) is False
        assert _DENIAL in page.text

    async def test_actor_with_all_member_permissions_can_add_member_without_role_id(self, client, world):
        perms = {module: list(actions) for module, actions in _MEMBER_PERMS.items()}
        perms["settings"] = ["read", "write"]
        equal = await world.custom_role("Member plus settings", perms)
        await _login(client, world, "equal", "member", equal)
        newcomer = await make_actor(world.db, "esc-newcomer-equal")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/add",
            data={"email": newcomer.user.email},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        row = (
            await world.db.execute(
                select(ProjectMember.role, ProjectMember.role_id).where(
                    ProjectMember.project_id == world.project.id, ProjectMember.user_id == newcomer.user.id
                )
            )
        ).one()
        assert (row.role, row.role_id) == ("member", None)

    async def test_admin_can_add_member_with_role_within_own_permissions(self, client, world):
        await _login(client, world, "admin", "admin")
        newcomer = await make_actor(world.db, "esc-newcomer-ok")
        within = await world.custom_role("Reviewer", {"scrum": ["read"]})

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/add",
            data={"email": newcomer.user.email, "role_id": str(within.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        row = (
            await world.db.execute(
                select(ProjectMember.role, ProjectMember.role_id).where(
                    ProjectMember.project_id == world.project.id, ProjectMember.user_id == newcomer.user.id
                )
            )
        ).one()
        assert (row.role, row.role_id) == ("reviewer", within.id)

    async def test_owner_can_add_member_with_system_owner_role(self, client, world):
        await _login(client, world, "owner", "owner")
        newcomer = await make_actor(world.db, "esc-newcomer-owner")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/members/add",
            data={"email": newcomer.user.email, "role_id": str(world.owner_role.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        row = (
            await world.db.execute(
                select(ProjectMember.role, ProjectMember.role_id).where(
                    ProjectMember.project_id == world.project.id, ProjectMember.user_id == newcomer.user.id
                )
            )
        ).one()
        assert (row.role, row.role_id) == ("owner", world.owner_role.id)


class TestDashboardRoleCrud:
    async def test_admin_cannot_create_role_with_permission_he_lacks(self, client, world):
        await _login(client, world, "admin", "admin")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/create",
            data={"name": "Eskalacja", **_perm_form({"scrum": ["read"], "settings": ["delete"]})},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert _DENIAL in resp.text
        assert await world.role_by_name("Eskalacja") is None

    async def test_admin_can_create_role_with_subset_of_own_permissions(self, client, world):
        await _login(client, world, "admin", "admin")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/create",
            data={"name": "Podzbiór", **_perm_form({"scrum": ["read", "write"], "wiki": ["read"]})},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        created = await world.role_by_name("Podzbiór")
        assert created is not None
        assert created.permissions == {"scrum": ["read", "write"], "wiki": ["read"]}

    async def test_owner_can_create_role_with_full_permissions(self, client, world):
        await _login(client, world, "owner", "owner")

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/create",
            data={"name": "Pełna", **_perm_form(_OWNER_PERMS)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        created = await world.role_by_name("Pełna")
        assert created is not None
        assert created.permissions == _OWNER_PERMS

    @pytest.mark.parametrize("target", ["admin_role", "member_role", "owner_role"])
    async def test_admin_cannot_edit_permissions_of_system_role(self, target, client, world):
        await _login(client, world, "admin", "admin")
        role = getattr(world, target)
        before = await world.role_state(role)

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/{role.id}/edit",
            data=_perm_form({"scrum": ["read"]}),
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert _DENIAL in resp.text
        assert await world.role_state(role) == before

    async def test_owner_cannot_edit_permissions_of_system_role_either(self, client, world):
        await _login(client, world, "owner", "owner")
        before = await world.role_state(world.member_role)

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/{world.member_role.id}/edit",
            data=_perm_form({"scrum": ["read"]}),
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert _DENIAL in resp.text
        assert await world.role_state(world.member_role) == before

    async def test_admin_cannot_add_permission_he_lacks_to_custom_role_and_rename_is_not_applied(self, client, world):
        await _login(client, world, "admin", "admin")
        role = await world.custom_role("Edytowana", {"scrum": ["read"]})
        before = await world.role_state(role)

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/{role.id}/edit",
            data={"name": "Zmieniona", **_perm_form({"scrum": ["read"], "settings": ["delete"]})},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert _DENIAL in resp.text
        assert await world.role_state(role) == before

    async def test_admin_can_edit_custom_role_to_subset_of_own_permissions(self, client, world):
        await _login(client, world, "admin", "admin")
        role = await world.custom_role("Edytowana", {"scrum": ["read"]})

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/{role.id}/edit",
            data={"name": "Edytowana", **_perm_form({"wiki": ["read", "write"]})},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.role_state(role) == ("Edytowana", {"wiki": ["read", "write"]})

    async def test_owner_can_edit_custom_role_to_full_permissions(self, client, world):
        await _login(client, world, "owner", "owner")
        role = await world.custom_role("Edytowana", {"scrum": ["read"]})

        resp = await client.post(
            f"/dashboard/{world.project.slug}/settings/roles/{role.id}/edit",
            data={"name": "Edytowana", **_perm_form(_OWNER_PERMS)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert await world.role_state(role) == ("Edytowana", _OWNER_PERMS)


def test_member_default_permissions_are_a_subset_of_admin_default_permissions():
    """Założenie testów powyżej: admin może przypisać systemową rolę Member (jego uprawnienia nie przekraczają admina)."""
    for module, actions in _MEMBER_PERMS.items():
        assert set(actions) <= set(_ADMIN_PERMS[module])
