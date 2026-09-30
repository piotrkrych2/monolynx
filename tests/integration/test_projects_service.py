"""Testy integracyjne -- services/projects.py (MON-137: wyekstrahowana logika MCP projekty/czlonkowie/etykiety).

Testy wolaja funkcje serwisu bezposrednio z fixture `db_session`. Skupiamy sie na:
_slugify/_code_from_slug, RBAC (settings:write/delete, users:write/delete), konflikty
slug/kodu, invite_member (nowy vs istniejacy user), kolejnosc list_members,
get_project_summary (agregaty na znanych danych, w tym pulapka "latest check"), oraz
etykiety.
"""

from __future__ import annotations

import re
import secrets
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from monolynx.constants import LABEL_COLOR_PALETTE
from monolynx.models.heartbeat import Heartbeat
from monolynx.models.issue import Issue
from monolynx.models.label import TicketLabel
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.services import projects as svc
from monolynx.services.auth import hash_password

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, tag: str, is_active: bool = True) -> User:
    user = User(email=f"pj_svc_{tag}_{uuid.uuid4().hex[:8]}@x.com", password_hash=hash_password("x"), is_active=is_active)
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str) -> Project:
    project = Project(
        name=f"Projects Svc {tag}",
        slug=f"pj-svc-{tag}-{uuid.uuid4().hex[:6]}",
        code=f"P{secrets.token_hex(3).upper()}",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _make_member(db, project: Project, user: User, role: str = "owner") -> ProjectMember:
    member = ProjectMember(project_id=project.id, user_id=user.id, role=role)
    db.add(member)
    await db.flush()
    return member


async def _setup(db, tag: str, role: str = "owner"):
    user = await _make_user(db, tag)
    project = await _make_project(db, tag)
    await _make_member(db, project, user, role=role)
    return user, project


# ---------------------------------------------------------------------------
# create_project -- slug/code generation
# ---------------------------------------------------------------------------


class TestCreateProject:
    async def test_generates_slug_and_code_from_name(self, db_session):
        user = await _make_user(db_session, "gen")

        project = await svc.create_project(db_session, user, name="Anna Miastkowska")

        assert project.slug == "anna-miastkowska"
        # _code_from_slug bierze pierwsza litere kazdego czlonu sluga (wieloczlonowy slug):
        # "anna-miastkowska" -> "a" + "m" -> "AM" (nie pelne inicjaly slowa).
        assert project.code == "AM"

    async def test_uses_provided_slug_and_code(self, db_session):
        user = await _make_user(db_session, "explicit")

        project = await svc.create_project(db_session, user, name="Cokolwiek", slug="custom-slug", code="cust")

        assert project.slug == "custom-slug"
        assert project.code == "CUST"

    async def test_empty_name_raises(self, db_session):
        user = await _make_user(db_session, "empty")
        with pytest.raises(ValueError, match=re.escape("Nazwa projektu nie moze byc pusta.")):
            await svc.create_project(db_session, user, name="   ")

    async def test_duplicate_slug_raises(self, db_session):
        user = await _make_user(db_session, "dupslug")
        await svc.create_project(db_session, user, name="Pierwszy", slug="dup-slug-test")

        with pytest.raises(ValueError, match=re.escape("Projekt ze slugiem 'dup-slug-test' juz istnieje. Podaj inny slug.")):
            await svc.create_project(db_session, user, name="Drugi", slug="dup-slug-test")

    async def test_invalid_slug_format_raises(self, db_session):
        user = await _make_user(db_session, "badslug")
        with pytest.raises(ValueError, match="Nieprawidlowy slug"):
            await svc.create_project(db_session, user, name="X", slug="Invalid Slug!")

    async def test_creator_added_as_owner_member(self, db_session):
        user = await _make_user(db_session, "owner-member")

        project = await svc.create_project(db_session, user, name="Wlasciciel Test")

        member = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
        ).scalar_one()
        assert member.role == "owner"

    async def test_code_conflict_appends_numeric_suffix(self, db_session):
        user = await _make_user(db_session, "codeconf")
        first = await svc.create_project(db_session, user, name="Pierwszy Kod", code="ZQX")
        assert first.code == "ZQX"

        second = await svc.create_project(db_session, user, name="Drugi Kod", slug=f"drugi-kod-{uuid.uuid4().hex[:6]}", code="ZQX")

        assert second.code == "ZQX2"


# ---------------------------------------------------------------------------
# update_project -- RBAC i konflikt slugu
# ---------------------------------------------------------------------------


class TestUpdateProject:
    async def test_member_without_settings_write_raises(self, db_session):
        user, project = await _setup(db_session, "upd-noperm", role="member")

        with pytest.raises(ValueError, match=r"Brak uprawnienia do edycji projektu \(settings:write\)"):
            await svc.update_project(db_session, user, project, name="Nowa nazwa")

    async def test_owner_updates_name_and_description(self, db_session):
        user, project = await _setup(db_session, "upd-ok", role="owner")

        result = await svc.update_project(db_session, user, project, name="Zaktualizowana", description="Nowy opis")

        assert result.name == "Zaktualizowana"
        assert result.description == "Nowy opis"

    async def test_slug_conflict_with_other_project_raises(self, db_session):
        user, project = await _setup(db_session, "upd-slugconf", role="owner")
        other = await _make_project(db_session, "upd-slugconf-other")

        with pytest.raises(ValueError, match=f"Slug '{other.slug}' jest juz zajety"):
            await svc.update_project(db_session, user, project, new_slug=other.slug)

    async def test_empty_name_raises(self, db_session):
        user, project = await _setup(db_session, "upd-emptyname", role="owner")
        with pytest.raises(ValueError, match=r"^Nazwa nie moze byc pusta$"):
            await svc.update_project(db_session, user, project, name="   ")


# ---------------------------------------------------------------------------
# delete_project -- RBAC i soft delete
# ---------------------------------------------------------------------------


class TestDeleteProject:
    async def test_member_without_settings_delete_raises(self, db_session):
        user, project = await _setup(db_session, "del-noperm", role="member")

        with pytest.raises(ValueError, match=r"Brak uprawnienia do usuniecia projektu \(settings:delete\)"):
            await svc.delete_project(db_session, user, project)

        assert project.is_active is True

    async def test_admin_without_settings_delete_raises(self, db_session):
        """Admin ma settings:['read','write'] ale NIE 'delete' -- tylko owner moze usunac projekt."""
        user, project = await _setup(db_session, "del-admin", role="admin")

        with pytest.raises(ValueError, match=r"settings:delete"):
            await svc.delete_project(db_session, user, project)

    async def test_owner_soft_deletes(self, db_session):
        user, project = await _setup(db_session, "del-ok", role="owner")

        await svc.delete_project(db_session, user, project)

        result = await db_session.execute(select(Project.is_active).where(Project.id == project.id))
        assert result.scalar_one() is False


# ---------------------------------------------------------------------------
# invite_member
# ---------------------------------------------------------------------------


class TestInviteMember:
    async def test_new_user_gets_invitation_token(self, db_session):
        user, project = await _setup(db_session, "inv-new", role="owner")
        email = f"nowy-{uuid.uuid4().hex[:8]}@example.com"

        result = await svc.invite_member(db_session, user, project, email=email, role="member")

        assert result.is_new_user is True
        assert result.invitation_token is not None
        assert result.expires_at is not None

        new_user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
        assert new_user.password_hash is None
        assert new_user.invitation_token == result.invitation_token

        member = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == new_user.id))
        ).scalar_one()
        assert member.role == "member"

    async def test_existing_active_user_added_without_token(self, db_session):
        user, project = await _setup(db_session, "inv-existing", role="owner")
        existing = await _make_user(db_session, "inv-existing-target")

        result = await svc.invite_member(db_session, user, project, email=existing.email, role="admin")

        assert result.is_new_user is False
        assert result.invitation_token is None

        member = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == existing.id))
        ).scalar_one()
        assert member.role == "admin"

    async def test_member_without_users_write_raises(self, db_session):
        user, project = await _setup(db_session, "inv-noperm", role="member")

        with pytest.raises(ValueError, match=r"users:write"):
            await svc.invite_member(db_session, user, project, email="ktos@example.com")

    async def test_duplicate_member_raises(self, db_session):
        user, project = await _setup(db_session, "inv-dup", role="owner")
        existing = await _make_user(db_session, "inv-dup-target")
        await _make_member(db_session, project, existing, role="member")

        with pytest.raises(ValueError, match="jest juz czlonkiem projektu"):
            await svc.invite_member(db_session, user, project, email=existing.email)


# ---------------------------------------------------------------------------
# remove_member
# ---------------------------------------------------------------------------


class TestRemoveMember:
    async def test_admin_without_users_delete_raises(self, db_session):
        """Admin ma users:['read','write'] ale nie 'delete' -- tylko owner usuwa czlonkow."""
        user, project = await _setup(db_session, "rm-admin", role="admin")
        target = await _make_user(db_session, "rm-admin-target")
        await _make_member(db_session, project, target, role="member")

        with pytest.raises(ValueError, match=r"users:delete"):
            await svc.remove_member(db_session, user, project, email=target.email)

    async def test_cannot_remove_owner(self, db_session):
        user, project = await _setup(db_session, "rm-owner-self", role="owner")

        with pytest.raises(ValueError, match="Nie mozna usunac ownera projektu"):
            await svc.remove_member(db_session, user, project, email=user.email)

    async def test_owner_removes_member(self, db_session):
        user, project = await _setup(db_session, "rm-ok", role="owner")
        target = await _make_user(db_session, "rm-ok-target")
        await _make_member(db_session, project, target, role="member")

        await svc.remove_member(db_session, user, project, email=target.email)

        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == target.id))
        ).scalar_one_or_none()
        assert remaining is None

    async def test_finds_account_with_mixed_case_email_stored_in_db(self, db_session):
        """MON-228 A4: konto zalozone z dashboardu moze miec e-mail z wielkimi literami (User.email
        jest case-sensitive), a serwis zamienia wejscie na lowercase - takie konto bylo nieusuwalne."""
        user, project = await _setup(db_session, "rm-mixed", role="owner")
        stored = f"Mixed.Case.{uuid.uuid4().hex[:8]}@Example.COM"
        target = User(email=stored, password_hash=hash_password("x"))
        db_session.add(target)
        await db_session.flush()
        await _make_member(db_session, project, target, role="member")

        await svc.remove_member(db_session, user, project, email=stored.lower())

        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == target.id))
        ).scalar_one_or_none()
        assert remaining is None

    async def test_finds_account_with_mixed_case_email_when_input_has_stored_case(self, db_session):
        user, project = await _setup(db_session, "rm-mixed-exact", role="owner")
        stored = f"Mixed.Exact.{uuid.uuid4().hex[:8]}@Example.COM"
        target = User(email=stored, password_hash=hash_password("x"))
        db_session.add(target)
        await db_session.flush()
        await _make_member(db_session, project, target, role="member")

        await svc.remove_member(db_session, user, project, email=f"  {stored}  ")

        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == target.id))
        ).scalar_one_or_none()
        assert remaining is None

    async def test_ambiguous_email_differing_only_by_case_raises_and_removes_nobody(self, db_session):
        user, project = await _setup(db_session, "rm-ambiguous", role="owner")
        local = f"dup.{uuid.uuid4().hex[:8]}"
        lower = User(email=f"{local}@example.com", password_hash=hash_password("x"))
        upper = User(email=f"{local.upper()}@EXAMPLE.COM", password_hash=hash_password("x"))
        db_session.add_all([lower, upper])
        await db_session.flush()
        await _make_member(db_session, project, lower, role="member")
        await _make_member(db_session, project, upper, role="member")

        with pytest.raises(ValueError):
            await svc.remove_member(db_session, user, project, email=f"{local}@example.com")

        rows = (await db_session.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))).scalars().all()
        assert {lower.id, upper.id} <= set(rows)

    async def test_commits_after_removal(self, db_session):
        """db-commit.md: usuniecie bez commit() ginie po zamknieciu sesji (fixture z rollbackiem tego nie zlapie)."""
        user, project = await _setup(db_session, "rm-commit", role="owner")
        target = await _make_user(db_session, "rm-commit-target")
        await _make_member(db_session, project, target, role="member")
        spy = AsyncMock(side_effect=db_session.commit)

        with patch.object(db_session, "commit", spy):
            await svc.remove_member(db_session, user, project, email=target.email)

        spy.assert_awaited_once()


class TestRemoveMemberByUserId:
    """MON-228 A4: wspolny rdzen usuwania po user_id (router API v2 nie zna e-maila czlonka)."""

    async def test_owner_removes_member_by_user_id(self, db_session):
        user, project = await _setup(db_session, "rmid-ok", role="owner")
        target = await _make_user(db_session, "rmid-ok-target")
        other = await _make_user(db_session, "rmid-ok-other")
        await _make_member(db_session, project, target, role="member")
        await _make_member(db_session, project, other, role="member")

        await svc.remove_member_by_user_id(db_session, user, project, target.id)

        rows = (await db_session.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))).scalars().all()
        assert target.id not in rows
        assert other.id in rows
        assert user.id in rows

    async def test_removes_account_with_mixed_case_email(self, db_session):
        user, project = await _setup(db_session, "rmid-mixed", role="owner")
        target = User(email=f"Mixed.Id.{uuid.uuid4().hex[:8]}@Example.COM", password_hash=hash_password("x"))
        db_session.add(target)
        await db_session.flush()
        await _make_member(db_session, project, target, role="member")

        await svc.remove_member_by_user_id(db_session, user, project, target.id)

        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == target.id))
        ).scalar_one_or_none()
        assert remaining is None

    async def test_removes_only_the_given_account_among_emails_differing_by_case(self, db_session):
        user, project = await _setup(db_session, "rmid-twin", role="owner")
        local = f"twin.{uuid.uuid4().hex[:8]}"
        lower = User(email=f"{local}@example.com", password_hash=hash_password("x"))
        upper = User(email=f"{local.upper()}@EXAMPLE.COM", password_hash=hash_password("x"))
        db_session.add_all([lower, upper])
        await db_session.flush()
        await _make_member(db_session, project, lower, role="member")
        await _make_member(db_session, project, upper, role="member")

        await svc.remove_member_by_user_id(db_session, user, project, upper.id)

        rows = (await db_session.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))).scalars().all()
        assert upper.id not in rows
        assert lower.id in rows

    async def test_admin_without_users_delete_raises_and_keeps_member(self, db_session):
        user, project = await _setup(db_session, "rmid-admin", role="admin")
        target = await _make_user(db_session, "rmid-admin-target")
        await _make_member(db_session, project, target, role="member")

        with pytest.raises(ValueError, match=r"users:delete"):
            await svc.remove_member_by_user_id(db_session, user, project, target.id)

        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == target.id))
        ).scalar_one_or_none()
        assert remaining is not None

    async def test_cannot_remove_owner(self, db_session):
        user, project = await _setup(db_session, "rmid-owner", role="owner")

        with pytest.raises(ValueError, match="Nie mozna usunac ownera projektu"):
            await svc.remove_member_by_user_id(db_session, user, project, user.id)

        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == user.id))
        ).scalar_one_or_none()
        assert remaining is not None

    async def test_user_who_is_not_a_member_raises_and_keeps_other_members(self, db_session):
        user, project = await _setup(db_session, "rmid-notmember", role="owner")
        member = await _make_user(db_session, "rmid-notmember-member")
        outsider = await _make_user(db_session, "rmid-notmember-outsider")
        await _make_member(db_session, project, member, role="member")

        with pytest.raises(ValueError):
            await svc.remove_member_by_user_id(db_session, user, project, outsider.id)

        rows = (await db_session.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))).scalars().all()
        assert member.id in rows

    async def test_unknown_user_id_raises(self, db_session):
        user, project = await _setup(db_session, "rmid-unknown", role="owner")

        with pytest.raises(ValueError):
            await svc.remove_member_by_user_id(db_session, user, project, uuid.uuid4())

    async def test_member_of_another_project_is_not_removed(self, db_session):
        user, project = await _setup(db_session, "rmid-isolation", role="owner")
        _other_owner, other_project = await _setup(db_session, "rmid-isolation-other", role="owner")
        shared = await _make_user(db_session, "rmid-isolation-shared")
        await _make_member(db_session, other_project, shared, role="member")

        with pytest.raises(ValueError):
            await svc.remove_member_by_user_id(db_session, user, project, shared.id)

        remaining = (
            await db_session.execute(select(ProjectMember).where(ProjectMember.project_id == other_project.id, ProjectMember.user_id == shared.id))
        ).scalar_one_or_none()
        assert remaining is not None

    async def test_commits_after_removal(self, db_session):
        """db-commit.md: spy na commit pada na mutancie 'commit -> flush'."""
        user, project = await _setup(db_session, "rmid-commit", role="owner")
        target = await _make_user(db_session, "rmid-commit-target")
        await _make_member(db_session, project, target, role="member")
        spy = AsyncMock(side_effect=db_session.commit)

        with patch.object(db_session, "commit", spy):
            await svc.remove_member_by_user_id(db_session, user, project, target.id)

        spy.assert_awaited_once()


# ---------------------------------------------------------------------------
# list_members -- kolejnosc (owner, admin, potem member alfabetycznie)
# ---------------------------------------------------------------------------


class TestListMembers:
    async def test_orders_owner_then_admin_then_members_alphabetically(self, db_session):
        owner, project = await _setup(db_session, "list-order", role="owner")
        owner.first_name, owner.last_name = "Zzz", "Owner"

        # Seed CELOWO w kolejnosci roznej od oczekiwanej (Zorro przed Anna) -- lapie brak ORDER BY.
        member_z = await _make_user(db_session, "list-order-z")
        member_z.first_name, member_z.last_name = "Zorro", "Czlonek"
        await _make_member(db_session, project, member_z, role="member")

        member_a = await _make_user(db_session, "list-order-a")
        member_a.first_name, member_a.last_name = "Anna", "Czlonek"
        await _make_member(db_session, project, member_a, role="member")

        admin = await _make_user(db_session, "list-order-admin")
        admin.first_name, admin.last_name = "Beata", "Admin"
        await _make_member(db_session, project, admin, role="admin")
        await db_session.flush()

        result = await svc.list_members(db_session, owner, project)

        names = [m.name for m in result]
        assert names == ["Zzz Owner", "Beata Admin", "Anna Czlonek", "Zorro Czlonek"]


# ---------------------------------------------------------------------------
# get_project_summary -- agregaty na znanych danych
# ---------------------------------------------------------------------------


async def _make_issue(db, project: Project, status: str, fingerprint: str) -> Issue:
    issue = Issue(project_id=project.id, fingerprint=fingerprint, title=f"Blad {fingerprint}", status=status)
    db.add(issue)
    await db.flush()
    return issue


async def _make_monitor(db, project: Project, is_active: bool = True) -> Monitor:
    monitor = Monitor(project_id=project.id, url="https://example.com", is_active=is_active)
    db.add(monitor)
    await db.flush()
    return monitor


async def _make_check(db, monitor: Monitor, is_success: bool, checked_at: datetime) -> MonitorCheck:
    check = MonitorCheck(monitor_id=monitor.id, is_success=is_success, checked_at=checked_at)
    db.add(check)
    await db.flush()
    return check


async def _make_heartbeat(db, project: Project, name: str) -> Heartbeat:
    heartbeat = Heartbeat(project_id=project.id, name=name, period=3600)
    db.add(heartbeat)
    await db.flush()
    return heartbeat


class TestGetProject:
    async def test_returns_details_scoped_to_project(self, db_session):
        """role/members_count/tickets_count/active_sprint/monitors_count/heartbeats_count.

        Seeduje IDENTYCZNE typy danych w DRUGIM projekcie z INNYMI (wiekszymi) licznosciami --
        gdyby ktorykolwiek licznik zgubil filtr project_id, wynik dla `project` bylby zawyzony.
        """
        user, project = await _setup(db_session, "get-detail", role="admin")
        other_member = await _make_user(db_session, "get-detail-member")
        await _make_member(db_session, project, other_member, role="member")

        sprint = Sprint(project_id=project.id, name="Aktywny", goal="Cel sprintu", start_date=date(2026, 1, 1), status="active")
        db_session.add(sprint)
        await db_session.flush()
        db_session.add(Ticket(project_id=project.id, number=1, title="T1", status="todo", sprint_id=sprint.id))
        db_session.add(Ticket(project_id=project.id, number=2, title="T2", status="done", sprint_id=sprint.id))
        db_session.add(Ticket(project_id=project.id, number=3, title="T3", status="done", sprint_id=sprint.id))
        db_session.add(Ticket(project_id=project.id, number=4, title="T4", status="backlog"))
        await db_session.flush()

        await _make_monitor(db_session, project)
        await _make_monitor(db_session, project)
        await _make_heartbeat(db_session, project, "hb-1")
        await _make_heartbeat(db_session, project, "hb-2")
        await _make_heartbeat(db_session, project, "hb-3")

        # Szum w DRUGIM projekcie -- inne (wieksze) licznosci, zeby zlapac brak filtra project_id.
        other_project = await _make_project(db_session, "get-detail-other")
        for i in range(5):
            other_target = await _make_user(db_session, f"get-detail-noise-{i}")
            await _make_member(db_session, other_project, other_target, role="member")
            db_session.add(Ticket(project_id=other_project.id, number=i + 1, title=f"Noise {i}", status="todo"))
        for _ in range(4):
            await _make_monitor(db_session, other_project)
        for i in range(2):
            await _make_heartbeat(db_session, other_project, f"other-hb-{i}")
        await db_session.flush()

        result = await svc.get_project(db_session, user, project)

        assert result.role == "admin"
        assert result.members_count == 2
        assert result.tickets_count == 4
        assert result.active_sprint == {
            "name": "Aktywny",
            "goal": "Cel sprintu",
            "tickets_by_status": {"todo": 1, "done": 2},
        }
        assert result.monitors_count == 2
        assert result.heartbeats_count == 3

    async def test_no_active_sprint_returns_none(self, db_session):
        user, project = await _setup(db_session, "get-detail-nosprint")

        result = await svc.get_project(db_session, user, project)

        assert result.active_sprint is None


class TestGetProjectSummary:
    async def test_aggregates_match_known_data(self, db_session):
        user, project = await _setup(db_session, "summary", role="owner")
        now = datetime.now(UTC)

        # Issues: 2 unresolved, 1 resolved (nie liczony)
        await _make_issue(db_session, project, "unresolved", "fp1")
        await _make_issue(db_session, project, "unresolved", "fp2")
        await _make_issue(db_session, project, "resolved", "fp3")

        # Monitor 1 (aktywny): starszy check FAIL, nowszy check OK -- liczy sie tylko
        # OSTATNI check, wiec ten monitor NIE powinien byc failing.
        monitor_ok_latest = await _make_monitor(db_session, project, is_active=True)
        await _make_check(db_session, monitor_ok_latest, is_success=False, checked_at=now - timedelta(hours=2))
        await _make_check(db_session, monitor_ok_latest, is_success=True, checked_at=now - timedelta(hours=1))

        # Monitor 2 (aktywny): jedyny check FAIL w oknie 24h -- liczy sie jako failing.
        monitor_failing = await _make_monitor(db_session, project, is_active=True)
        await _make_check(db_session, monitor_failing, is_success=False, checked_at=now - timedelta(minutes=30))
        # Stary check (25h temu) poza oknem uptime -- nie powinien wplywac na uptime_24h.
        await _make_check(db_session, monitor_failing, is_success=True, checked_at=now - timedelta(hours=25))

        # Monitor 3 (NIEAKTYWNY) z failing checkiem -- pomijany filtrem is_active.
        monitor_inactive = await _make_monitor(db_session, project, is_active=False)
        await _make_check(db_session, monitor_inactive, is_success=False, checked_at=now - timedelta(minutes=5))

        # Aktywny sprint z ticketami w roznych statusach
        sprint = Sprint(project_id=project.id, name="Aktywny", start_date=date(2026, 1, 1), status="active")
        db_session.add(sprint)
        await db_session.flush()
        db_session.add(Ticket(project_id=project.id, number=1, title="T1", status="todo", sprint_id=sprint.id))
        db_session.add(Ticket(project_id=project.id, number=2, title="T2", status="done", sprint_id=sprint.id))
        db_session.add(Ticket(project_id=project.id, number=3, title="T3", status="done", sprint_id=sprint.id))
        # Backlog (poza sprintem)
        db_session.add(Ticket(project_id=project.id, number=4, title="T4", status="backlog"))
        db_session.add(Ticket(project_id=project.id, number=5, title="T5", status="backlog"))
        await db_session.flush()

        result = await svc.get_project_summary(db_session, user, project)

        assert result.issues_unresolved == 2
        assert result.monitors_failing == 1
        # W oknie 24h: monitor_ok_latest 2 checki (1 fail, 1 ok) + monitor_failing 1 check (fail, w oknie)
        # = 3 checki, 1 sukces -> 1/3 = 33.3%. Stary check monitor_failing (25h) jest POZA oknem.
        assert result.uptime_24h == 33.3
        assert result.active_sprint == {
            "name": "Aktywny",
            "goal": None,
            "tickets_by_status": {"todo": 1, "done": 2},
            "total_tickets": 3,
        }
        assert result.backlog_count == 2


# ---------------------------------------------------------------------------
# Etykiety
# ---------------------------------------------------------------------------


class TestLabels:
    async def test_create_label_calls_commit(self, db_session):
        """create_label() musi wywolac db.commit() -- test pada na mutancie 'usun await db.commit()'.

        Jak w tests/integration/test_tickets_service.py::TestCommitBehavior -- `db_session`
        wiaze sie z polaczeniem w otwartej zewnetrznej transakcji rollbackowanej na koncu
        testu, wiec sprawdzanie widocznosci wiersza z osobnego polaczenia nic by nie dowiodlo.
        Szpiegujemy samo wywolanie commit() (wraps=... -- realny commit nadal sie wykonuje).
        """
        user, project = await _setup(db_session, "labels-commit")

        with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            label = await svc.create_label(db_session, user, project, name="Commit sprawdzian")

        assert label.id is not None
        commit_spy.assert_awaited()

    async def test_list_labels_orders_alphabetically_with_counts(self, db_session):
        user, project = await _setup(db_session, "labels-order")
        # Seed w kolejnosci odwrotnej do alfabetu -- lapie brak ORDER BY Label.name.
        await svc.create_label(db_session, user, project, name="Zeta")
        alpha = await svc.create_label(db_session, user, project, name="Alpha")

        ticket = Ticket(project_id=project.id, number=1, title="T1", status="todo")
        db_session.add(ticket)
        await db_session.flush()

        db_session.add(TicketLabel(ticket_id=ticket.id, label_id=alpha.id))
        await db_session.flush()

        result = await svc.list_labels(db_session, user, project)

        assert [r.name for r in result] == ["Alpha", "Zeta"]
        by_name = {r.name: r.tickets_count for r in result}
        assert by_name["Alpha"] == 1
        assert by_name["Zeta"] == 0

    async def test_create_label_duplicate_raises(self, db_session):
        user, project = await _setup(db_session, "labels-dup")
        await svc.create_label(db_session, user, project, name="Bug")

        with pytest.raises(ValueError, match=re.escape("Etykieta o nazwie 'Bug' juz istnieje w tym projekcie.")):
            await svc.create_label(db_session, user, project, name="Bug")

    async def test_create_label_without_color_uses_palette(self, db_session):
        user, project = await _setup(db_session, "labels-color")

        label = await svc.create_label(db_session, user, project, name="Bez koloru")

        assert label.color in LABEL_COLOR_PALETTE

    async def test_create_label_explicit_color(self, db_session):
        user, project = await _setup(db_session, "labels-explicit-color")

        label = await svc.create_label(db_session, user, project, name="Z kolorem", color="#112233")

        assert label.color == "#112233"

    async def test_create_label_invalid_color_raises(self, db_session):
        user, project = await _setup(db_session, "labels-badcolor")
        with pytest.raises(ValueError, match="Kolor musi byc w formacie hex"):
            await svc.create_label(db_session, user, project, name="Zly kolor", color="czerwony")
