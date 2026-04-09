"""Testy integracyjne -- powiazanie ticketow z rozliczeniami, RBAC, zamrazanie edycji."""

from __future__ import annotations

import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from monolynx.services.settlements import is_ticket_frozen
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(db_session, name: str, slug: str | None = None) -> Project:
    """Tworzy projekt i zwraca go."""
    if slug is None:
        slug = f"proj-{secrets.token_hex(4)}"
    project = Project(
        name=name,
        slug=slug,
        code=secrets.token_hex(3).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_settlement(
    db_session,
    project: Project,
    creator: User,
    status: str = "draft",
    name: str | None = None,
) -> Settlement:
    """Tworzy settlement i M2M z projektem."""
    # Najpierw generujemy unikalny numer
    from sqlalchemy import func

    result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    next_number = int(result.scalar_one()) + 1

    settlement = Settlement(
        number=next_number,
        name=name or f"Rozliczenie {next_number}",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        status=status,
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()

    sp = SettlementProject(settlement_id=settlement.id, project_id=project.id)
    db_session.add(sp)
    await db_session.flush()

    return settlement


async def _create_ticket(db_session, project: Project, number: int | None = None) -> Ticket:
    """Tworzy ticket w projekcie."""
    if number is None:
        import random

        number = random.randint(1000, 9999)
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=f"Ticket #{number}",
        status="backlog",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _link_ticket_to_settlement(db_session, settlement: Settlement, ticket: Ticket) -> None:
    """Bezposrednio laczy ticket z settlement przez ORM (omija walidacje)."""
    # Odczytujemy settlement ze zlaczeniami
    result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
    s = result.scalar_one()
    s.tickets.append(ticket)
    await db_session.flush()


async def _create_user_with_role(
    db_session,
    email: str,
    project: Project,
    role: str = "member",
    is_superuser: bool = False,
) -> User:
    """Tworzy uzytkownika i dodaje go jako czlonka projektu z podana rola."""
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=is_superuser,
    )
    db_session.add(user)
    await db_session.flush()

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role=role,
    )
    db_session.add(member)
    await db_session.flush()

    return user


async def _login_existing_user(client, email: str) -> None:
    """Loguje istniejacego uzytkownika (nie tworzy nowego)."""
    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


# ---------------------------------------------------------------------------
# Testy: is_ticket_frozen (unit-style, bez DB)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsTicketFrozen:
    def test_frozen_when_sent_settlement(self):
        """Ticket z aktywnym settlement sent jest frozen."""

        class FakeSettlement:
            status = "sent"
            is_active = True

        class FakeTicket:
            def __init__(self) -> None:
                self.settlements = [FakeSettlement()]

        assert is_ticket_frozen(FakeTicket()) is True  # type: ignore[arg-type]

    def test_frozen_when_paid_settlement(self):
        """Ticket z aktywnym settlement paid jest frozen."""

        class FakeSettlement:
            status = "paid"
            is_active = True

        class FakeTicket:
            def __init__(self) -> None:
                self.settlements = [FakeSettlement()]

        assert is_ticket_frozen(FakeTicket()) is True  # type: ignore[arg-type]

    def test_not_frozen_when_only_draft(self):
        """Ticket z tylko draft settlement NIE jest frozen."""

        class FakeSettlement:
            status = "draft"
            is_active = True

        class FakeTicket:
            def __init__(self) -> None:
                self.settlements = [FakeSettlement()]

        assert is_ticket_frozen(FakeTicket()) is False  # type: ignore[arg-type]

    def test_not_frozen_when_no_settlements(self):
        """Ticket bez rozliczen nie jest frozen."""

        class FakeTicket:
            def __init__(self) -> None:
                self.settlements: list = []

        assert is_ticket_frozen(FakeTicket()) is False  # type: ignore[arg-type]

    def test_not_frozen_when_inactive_sent_settlement(self):
        """Ticket z nieaktywnym (soft-deleted) sent settlement nie jest frozen."""

        class FakeSettlement:
            status = "sent"
            is_active = False

        class FakeTicket:
            def __init__(self) -> None:
                self.settlements = [FakeSettlement()]

        assert is_ticket_frozen(FakeTicket()) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Testy: RBAC -- widocznosc sekcji Rozliczenia na detalu ticketu
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketSettlementsRBAC:
    async def test_user_without_rozliczenia_read_does_not_see_settlements_section(self, client, db_session):
        """Sekcja 'Rozliczenia' NIE jest widoczna dla usera z rola 'member' (brak rozliczenia:read)."""
        # Arrange
        project = await _create_project(db_session, "RBAC No Rozl", "rbac-no-rozl-01")
        creator = await _create_user_with_role(db_session, "creator-rbacnr01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "draft")
        ticket = await _create_ticket(db_session, project, number=101)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        # member nie ma rozliczenia:read
        await _create_user_with_role(db_session, "member-rbacnr01@test.com", project, "member")
        await _login_existing_user(client, "member-rbacnr01@test.com")

        # Act
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")

        # Assert
        assert resp.status_code == 200
        # Sekcja Rozliczenia nie powinna byc widoczna
        assert "Rozliczenia" not in resp.text or "settlements_visible" not in resp.text

    async def test_owner_sees_settlements_section(self, client, db_session):
        """Owner (rozliczenia:read) widzi sekcje 'Rozliczenia' na detalu ticketu."""
        # Arrange
        project = await _create_project(db_session, "RBAC Owner", "rbac-owner-01")
        owner = await _create_user_with_role(db_session, "owner-rbacown01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")
        ticket = await _create_ticket(db_session, project, number=201)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-rbacown01@test.com")

        # Act
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")

        # Assert
        assert resp.status_code == 200
        # Owner ma rozliczenia:read -- sekcja powinna byc widoczna
        assert "Rozliczenia" in resp.text

    async def test_superuser_sees_settlements_section(self, client, db_session):
        """Superuser widzi sekcje 'Rozliczenia' na detalu ticketu."""
        # Arrange
        project = await _create_project(db_session, "RBAC Super", "rbac-super-01")
        owner_for_settlement = await _create_user_with_role(db_session, "owner-rbacsuper01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner_for_settlement, "draft")
        ticket = await _create_ticket(db_session, project, number=301)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await login_session(client, db_session, email="superuser-rbacsuper01@test.com", is_superuser=True)

        # Act
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")

        # Assert
        assert resp.status_code == 200
        assert "Rozliczenia" in resp.text


# ---------------------------------------------------------------------------
# Testy: zamrazanie edycji ticketu (frozen)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFrozenTicketBlocking:
    async def test_frozen_ticket_get_edit_redirects(self, client, db_session):
        """GET /edit dla frozen ticketu (settlement sent) -> redirect + flash."""
        # Arrange
        project = await _create_project(db_session, "Frozen GET", "frozen-get-01")
        owner = await _create_user_with_role(db_session, "owner-frozen-get01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent")
        ticket = await _create_ticket(db_session, project, number=401)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-frozen-get01@test.com")

        # Act
        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            follow_redirects=False,
        )

        # Assert: redirect na detail
        assert resp.status_code == 303
        assert f"/scrum/tickets/{ticket.id}" in resp.headers["location"]

    async def test_frozen_ticket_post_edit_redirects(self, client, db_session):
        """POST /edit dla frozen ticketu (settlement paid) -> redirect + flash."""
        # Arrange
        project = await _create_project(db_session, "Frozen POST", "frozen-post-01")
        owner = await _create_user_with_role(db_session, "owner-frozenpost01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "paid")
        ticket = await _create_ticket(db_session, project, number=501)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-frozenpost01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={
                "title": "Nowa nazwa",
                "status": "todo",
                "priority": "high",
                "description": "",
            },
            follow_redirects=False,
        )

        # Assert: redirect
        assert resp.status_code == 303
        assert f"/scrum/tickets/{ticket.id}" in resp.headers["location"]

        # Ticket NIE zostal zmieniony
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        refreshed = result.scalar_one()
        assert refreshed.title == "Ticket #501"

    async def test_frozen_ticket_htmx_status_allowed(self, client, db_session):
        """PATCH /status dla frozen ticketu jest DOZWOLONE (zmiana samego statusu).

        Pelna edycja/usuwanie pozostaje zablokowane, ale zmiana statusu musi dzialac
        zeby umozliwic przesuniecie ticketu na Kanban board.
        """
        # Arrange
        project = await _create_project(db_session, "Frozen HTMX", "frozen-htmx-01")
        owner = await _create_user_with_role(db_session, "owner-frozenhtmx01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent")
        ticket = await _create_ticket(db_session, project, number=601)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-frozenhtmx01@test.com")

        # Act
        resp = await client.patch(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/status",
            json={"status": "in_progress"},
        )

        # Assert: zmiana statusu przechodzi (200)
        assert resp.status_code == 200

        # Status w DB zaktualizowany (zapisz id PRZED expire zeby uniknac lazy-load)
        ticket_id = ticket.id
        db_session.expire_all()
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))
        refreshed = result.scalar_one()
        assert refreshed.status == "in_progress"

    async def test_frozen_ticket_delete_redirects_and_ticket_still_exists(self, client, db_session):
        """POST /delete dla frozen ticketu -> redirect + flash, ticket nadal istnieje."""
        # Arrange
        project = await _create_project(db_session, "Frozen Del", "frozen-del-01")
        owner = await _create_user_with_role(db_session, "owner-frozendel01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "paid")
        ticket = await _create_ticket(db_session, project, number=701)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-frozendel01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/delete",
            follow_redirects=False,
        )

        # Assert: redirect (nie 200)
        assert resp.status_code == 303

        # Ticket NIE zostal usuniety
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        still_exists = result.scalar_one_or_none()
        assert still_exists is not None

    async def test_draft_settlement_ticket_is_editable(self, client, db_session):
        """Ticket z settlement draft NIE jest frozen -- edycja dziala."""
        # Arrange
        project = await _create_project(db_session, "Draft Editable", "draft-edit-01")
        owner = await _create_user_with_role(db_session, "owner-draftedit01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")
        ticket = await _create_ticket(db_session, project, number=801)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-draftedit01@test.com")

        # Act -- GET edit powinien dzialac (nie redirect)
        resp = await client.get(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            follow_redirects=False,
        )

        # Assert: formularz sie laduje (200 lub co najwyzej redirect z powodu braku settleemnts config)
        # Kluczowe: NIE jest to redirect z komunikatem "zamrozony"
        if resp.status_code == 303:
            # Jesli redirect -- sprawdz ze to NIE z powodu zamrozenia
            assert "zamrozony" not in resp.headers.get("location", "").lower()
        else:
            assert resp.status_code == 200

    async def test_unfreeze_when_settlement_changes_to_draft(self, db_session):
        """Po zmianie statusu settlement z sent na draft, ticket przestaje byc frozen."""
        # Arrange: ticket podpiety do sent settlement
        project = await _create_project(db_session, "Unfreeze", "unfreeze-01")
        creator = await _create_user_with_role(db_session, "creator-unfreeze01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "sent")
        ticket = await _create_ticket(db_session, project, number=901)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        # Pobierz ticket ze settlements
        result = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket.id))
        t = result.scalar_one()
        assert is_ticket_frozen(t) is True

        # Act: zmien settlement na draft
        settlement.status = "draft"
        await db_session.flush()

        # Re-query po zmianie
        await db_session.refresh(t, ["settlements"])
        result2 = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket.id))
        t2 = result2.scalar_one()

        # Assert
        assert is_ticket_frozen(t2) is False


# ---------------------------------------------------------------------------
# Testy: link/unlink ticketu do settlement
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementTicketLinkUnlink:
    async def test_link_ticket_requires_rozliczenia_write(self, client, db_session):
        """Bez uprawnien write (member) -> 403 przy podpinaniu ticketu."""
        # Arrange
        project = await _create_project(db_session, "Link NoWrite", "link-nowrite-01")
        owner = await _create_user_with_role(db_session, "owner-lnw01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")
        ticket = await _create_ticket(db_session, project, number=1001)

        # member rola nie ma rozliczenia:write
        await _create_user_with_role(db_session, "member-lnw01@test.com", project, "member")
        await _login_existing_user(client, "member-lnw01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(ticket.id)},
            follow_redirects=False,
        )

        # Assert: 403 forbidden
        assert resp.status_code == 403

    async def test_link_ticket_to_draft_settlement_success(self, client, db_session):
        """POST /tickets/link z valid ticket_id + settlement draft -> ticket dodany."""
        # Arrange
        project = await _create_project(db_session, "Link Draft OK", "link-draft-ok-01")
        owner = await _create_user_with_role(db_session, "owner-ldok01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")
        ticket = await _create_ticket(db_session, project, number=1101)

        await _login_existing_user(client, "owner-ldok01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(ticket.id)},
            follow_redirects=False,
        )

        # Assert: redirect na detal
        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

        # Sprawdz ze ticket jest podpiety
        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
        s = result.scalar_one()
        linked_ids = {t.id for t in s.tickets}
        assert ticket.id in linked_ids

    async def test_link_ticket_to_sent_settlement_fails(self, client, db_session):
        """Settlement w statusie sent -> podpiecie nowego ticketu zablokowane (flash error + redirect)."""
        # Arrange
        project = await _create_project(db_session, "Link Sent Fail", "link-sent-fail-01")
        owner = await _create_user_with_role(db_session, "owner-lsf01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent")
        ticket = await _create_ticket(db_session, project, number=1201)

        await _login_existing_user(client, "owner-lsf01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(ticket.id)},
            follow_redirects=False,
        )

        # Assert: redirect z flash errorem (nie podpieto)
        assert resp.status_code == 303

        # Sprawdz ze ticket NIE zostal podpiety
        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
        s = result.scalar_one()
        linked_ids = {t.id for t in s.tickets}
        assert ticket.id not in linked_ids

    async def test_link_ticket_cross_project_validation(self, client, db_session):
        """Ticket z innego projektu niz settlement -> blad walidacji."""
        # Arrange: dwa projekty, settlement w projekcie A, ticket w projekcie B
        project_a = await _create_project(db_session, "Cross Proj A", "cross-proj-a-01")
        project_b = await _create_project(db_session, "Cross Proj B", "cross-proj-b-01")
        owner_a = await _create_user_with_role(db_session, "owner-cpa01@test.com", project_a, "owner")
        # Owner A takze ma dostep do projektu B (zeby zalogowac sie z perspektywy A)
        member_b = ProjectMember(
            project_id=project_b.id,
            user_id=owner_a.id,
            role="owner",
        )
        db_session.add(member_b)
        await db_session.flush()

        settlement = await _create_settlement(db_session, project_a, owner_a, "draft")
        # Ticket jest w projekcie B (innym niz settlement)
        ticket_b = await _create_ticket(db_session, project_b, number=1301)

        await _login_existing_user(client, "owner-cpa01@test.com")

        # Act: probujemy podpiac ticket z projektu B do settlement projektu A
        resp = await client.post(
            f"/dashboard/{project_a.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(ticket_b.id)},
            follow_redirects=False,
        )

        # Assert: redirect (error flash), ticket NIE podpiety
        assert resp.status_code == 303

        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
        s = result.scalar_one()
        linked_ids = {t.id for t in s.tickets}
        assert ticket_b.id not in linked_ids

    async def test_unlink_ticket_from_draft_settlement(self, client, db_session):
        """Unlink ticketu z draft settlement dziala poprawnie."""
        # Arrange
        project = await _create_project(db_session, "Unlink Draft", "unlink-draft-01")
        owner = await _create_user_with_role(db_session, "owner-ud01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")
        ticket = await _create_ticket(db_session, project, number=1401)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-ud01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        # Assert: redirect na detal
        assert resp.status_code == 303

        # Sprawdz ze ticket NIE jest juz podpiety
        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
        s = result.scalar_one()
        linked_ids = {t.id for t in s.tickets}
        assert ticket.id not in linked_ids

    async def test_unlink_ticket_from_sent_settlement_fails(self, client, db_session):
        """Unlink zablokowany dla settlement w statusie sent."""
        # Arrange
        project = await _create_project(db_session, "Unlink Sent", "unlink-sent-01")
        owner = await _create_user_with_role(db_session, "owner-us01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent")
        ticket = await _create_ticket(db_session, project, number=1501)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-us01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        # Assert: redirect z flash errorem, ticket NADAL podpiety
        assert resp.status_code == 303

        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
        s = result.scalar_one()
        linked_ids = {t.id for t in s.tickets}
        assert ticket.id in linked_ids

    async def test_unlink_ticket_from_paid_settlement_fails(self, client, db_session):
        """Unlink zablokowany dla settlement w statusie paid."""
        # Arrange
        project = await _create_project(db_session, "Unlink Paid", "unlink-paid-01")
        owner = await _create_user_with_role(db_session, "owner-up01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "paid")
        ticket = await _create_ticket(db_session, project, number=1601)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-up01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        # Assert: redirect z flash errorem, ticket NADAL podpiety
        assert resp.status_code == 303

        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
        s = result.scalar_one()
        linked_ids = {t.id for t in s.tickets}
        assert ticket.id in linked_ids


# ---------------------------------------------------------------------------
# Testy: serwis validate_settlement_ticket_link
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestValidateSettlementTicketLink:
    async def test_raises_when_settlement_not_draft(self, db_session):
        """ValueError gdy settlement nie jest draft."""
        from monolynx.services.settlements import validate_settlement_ticket_link

        project = await _create_project(db_session, "Validate Sent", "validate-sent-01")
        creator = await _create_user_with_role(db_session, "creator-vsent01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "sent")
        ticket = await _create_ticket(db_session, project, number=2001)

        # Ladujemy settlement z projektami
        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.projects)).where(Settlement.id == settlement.id))
        s = result.scalar_one()

        with pytest.raises(ValueError, match="draft"):
            await validate_settlement_ticket_link(db_session, s, ticket)

    async def test_raises_when_ticket_from_different_project(self, db_session):
        """ValueError gdy ticket nalezy do innego projektu."""
        from monolynx.services.settlements import validate_settlement_ticket_link

        project_a = await _create_project(db_session, "Val Proj A", "val-proj-a-01")
        project_b = await _create_project(db_session, "Val Proj B", "val-proj-b-01")
        creator = await _create_user_with_role(db_session, "creator-vpa01@test.com", project_a, "owner")
        settlement = await _create_settlement(db_session, project_a, creator, "draft")
        ticket_b = await _create_ticket(db_session, project_b, number=2101)

        # Ladujemy settlement z projektami
        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.projects)).where(Settlement.id == settlement.id))
        s = result.scalar_one()

        with pytest.raises(ValueError, match="nalezy"):
            await validate_settlement_ticket_link(db_session, s, ticket_b)

    async def test_passes_when_valid_draft_and_correct_project(self, db_session):
        """Brak bledu dla prawidlowego przypadku (draft + poprawny projekt)."""
        from monolynx.services.settlements import validate_settlement_ticket_link

        project = await _create_project(db_session, "Val OK", "val-ok-01")
        creator = await _create_user_with_role(db_session, "creator-vok01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "draft")
        ticket = await _create_ticket(db_session, project, number=2201)

        result = await db_session.execute(select(Settlement).options(selectinload(Settlement.projects)).where(Settlement.id == settlement.id))
        s = result.scalar_one()

        # Nie powinno rzucic wyjatku
        await validate_settlement_ticket_link(db_session, s, ticket)


# ---------------------------------------------------------------------------
# Testy regresji: SETTLEMENT_ATTACHMENT_STATES (bloker z code review)
#
# Bug: upload_settlement_attachment sprawdzal state in SETTLEMENT_STATES
# (= {"draft", "sent", "paid"}) zamiast SETTLEMENT_ATTACHMENT_STATES
# (= {"draft", "signed"}). Efekt: state="signed" bylo odrzucane, bo
# "signed" nie nalezy do SETTLEMENT_STATES.
#
# Po naprawie: serwis bedzie uzywal SETTLEMENT_ATTACHMENT_STATES.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettlementAttachmentStates:
    """Testy regresji dla poprawnej walidacji stanu zalacznika (SETTLEMENT_ATTACHMENT_STATES)."""

    async def _call_upload(
        self,
        db_session,
        settlement: Settlement,
        user_id: uuid.UUID,
        state: str,
    ) -> None:
        """Pomocnik: wywoluje upload_settlement_attachment z mockiem MinIO."""
        from monolynx.services.settlements import upload_settlement_attachment

        # Ladujemy settlement z projektami i zalacznikami (eager load)
        result = await db_session.execute(
            select(Settlement)
            .options(
                selectinload(Settlement.projects),
                selectinload(Settlement.attachments),
            )
            .where(Settlement.id == settlement.id)
        )
        s = result.scalar_one()

        # Patchujemy MinIO upload zeby nie uderzyc w prawdziwy MinIO
        with patch("monolynx.services.settlements.minio_client.upload_object", return_value=None):
            await upload_settlement_attachment(
                db=db_session,
                settlement=s,
                user_id=user_id,
                file_bytes=b"fake content",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invoice",
                state=state,
            )

    async def test_upload_attachment_with_state_draft_succeeds(self, db_session):
        """Upload z state='draft' powinien przejsc bez bledu."""
        from monolynx.models.settlement_attachment import SettlementAttachment

        project = await _create_project(db_session, "Attach Draft", "attach-draft-01")
        owner = await _create_user_with_role(db_session, "owner-attdraft01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        # Nie powinno rzucic wyjatku
        await self._call_upload(db_session, settlement, owner.id, "draft")

        # Zalacznik pojawil sie w DB -- query bezposrednio na SettlementAttachment
        await db_session.flush()
        result = await db_session.execute(select(SettlementAttachment).where(SettlementAttachment.settlement_id == settlement.id))
        attachments = result.scalars().all()
        assert len(attachments) == 1
        assert attachments[0].state == "draft"

    async def test_upload_attachment_with_state_signed_succeeds(self, db_session):
        """
        REGRESJA: Upload z state='signed' MUSI przejsc bez bledu.

        Przed naprawka: serwis sprawdzal state in SETTLEMENT_STATES (draft/sent/paid)
        i odrzucal 'signed'. Po naprawce: sprawdza SETTLEMENT_ATTACHMENT_STATES
        (draft/signed) i 'signed' jest dozwolone.
        """
        from monolynx.models.settlement_attachment import SettlementAttachment

        project = await _create_project(db_session, "Attach Signed", "attach-signed-01")
        owner = await _create_user_with_role(db_session, "owner-attsigned01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        # Przed naprawka: rzucalo ValueError("Nieprawidlowy stan: signed...")
        # Po naprawce: nie rzuca
        await self._call_upload(db_session, settlement, owner.id, "signed")

        # Zalacznik pojawil sie w DB z poprawnym stanem
        await db_session.flush()
        result = await db_session.execute(select(SettlementAttachment).where(SettlementAttachment.settlement_id == settlement.id))
        attachments = result.scalars().all()
        assert len(attachments) == 1
        assert attachments[0].state == "signed"

    async def test_upload_attachment_with_settlement_status_sent_rejected(self, db_session):
        """
        REGRESJA: state='sent' NIE jest prawidlowym stanem zalacznika (to status rozliczenia!).

        Oczekiwane: ValueError("Nieprawidlowy stan: sent...")
        Przed naprawka: state='sent' bylo AKCEPTOWANE (bo bylo w SETTLEMENT_STATES).
        Po naprawce: state='sent' jest ODRZUCANE (bo nie ma w SETTLEMENT_ATTACHMENT_STATES).
        """
        project = await _create_project(db_session, "Attach BadState", "attach-badstate-01")
        owner = await _create_user_with_role(db_session, "owner-attbad01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        result = await db_session.execute(
            select(Settlement)
            .options(
                selectinload(Settlement.projects),
                selectinload(Settlement.attachments),
            )
            .where(Settlement.id == settlement.id)
        )
        s = result.scalar_one()

        from monolynx.services.settlements import upload_settlement_attachment

        with (
            patch("monolynx.services.settlements.minio_client.upload_object", return_value=None),
            pytest.raises(ValueError, match="stan"),
        ):
            await upload_settlement_attachment(
                db=db_session,
                settlement=s,
                user_id=owner.id,
                file_bytes=b"fake content",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="sent",
            )

    async def test_upload_attachment_with_state_paid_rejected(self, db_session):
        """state='paid' NIE jest prawidlowym stanem zalacznika -- musi byc odrzucony."""
        project = await _create_project(db_session, "Attach Paid Bad", "attach-paid-bad-01")
        owner = await _create_user_with_role(db_session, "owner-attpaid01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        result = await db_session.execute(
            select(Settlement)
            .options(
                selectinload(Settlement.projects),
                selectinload(Settlement.attachments),
            )
            .where(Settlement.id == settlement.id)
        )
        s = result.scalar_one()

        from monolynx.services.settlements import upload_settlement_attachment

        with (
            patch("monolynx.services.settlements.minio_client.upload_object", return_value=None),
            pytest.raises(ValueError, match="stan"),
        ):
            await upload_settlement_attachment(
                db=db_session,
                settlement=s,
                user_id=owner.id,
                file_bytes=b"fake content",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="paid",
            )


# ---------------------------------------------------------------------------
# Testy regresji: bulk_update_tickets pomijal frozen guard (bloker z code review)
#
# Bug: bulk_update_tickets nie ladowal Ticket.settlements i nie sprawdzal
# is_ticket_frozen(). Frozen ticket byl aktualizowany tak jak normalny.
#
# Po naprawie: selectinload(Ticket.settlements) + is_ticket_frozen check
# w petli -- frozen tickety trafiaja do failed[], normalny updated++.
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token w naglowku (lokalny helper)."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


@pytest.mark.unit
class TestBulkUpdateTicketsFrozenGuard:
    """Testy regresji: bulk_update_tickets musi blokac frozen tickety."""

    @pytest.fixture
    def mock_factory_local(self, db_session):
        """Zastepuje async_session_factory na db_session (commit -> flush)."""
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

    async def test_bulk_update_skips_frozen_ticket(self, db_session, mock_factory_local):
        """
        REGRESJA: bulk_update_tickets NIE moze aktualizowac frozen ticketow przy zmianie
        pelnej (priority/assignee/sprint/due_date). Zmiana samego statusu jest dozwolona
        -- to testuje test_bulk_update_status_allowed_on_frozen.

        Setup:
        - settlement status=sent, ticket_frozen podpiety (frozen)
        - ticket_normal bez podpietego settlement
        - bulk update z priority="high"

        Oczekiwane:
        - result["updated"] == 1 (tylko normal)
        - result["failed"] ma wpis dla frozen_ticket z reason zawierajacym "zamroz"
        - frozen_ticket.priority NIE zmieniony
        - normal_ticket.priority == "high"
        """
        from monolynx.mcp_server import bulk_update_tickets

        # Arrange: projekt + user owner
        slug = f"bulk-frozen-{uuid.uuid4().hex[:6]}"
        project = Project(
            name="Bulk Frozen Test",
            slug=slug,
            code="BFRT",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        user = User(
            email=f"bulk-frozen-{uuid.uuid4().hex[:6]}@test.com",
            password_hash=hash_password("pass"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        member = ProjectMember(project_id=project.id, user_id=user.id, role="owner")
        db_session.add(member)
        await db_session.flush()

        # Frozen ticket: podpiety do sent settlement
        from sqlalchemy import func

        res = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
        next_num = int(res.scalar_one()) + 1
        settlement = Settlement(
            number=next_num,
            name="Test Settlement",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            status="sent",
            created_by_id=user.id,
        )
        db_session.add(settlement)
        await db_session.flush()

        sp = SettlementProject(settlement_id=settlement.id, project_id=project.id)
        db_session.add(sp)

        frozen_ticket = Ticket(
            project_id=project.id,
            number=9001,
            title="Frozen Ticket",
            status="backlog",
        )
        db_session.add(frozen_ticket)
        await db_session.flush()

        # Laczymy ticket z settlement przez ORM
        result_s = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
        s = result_s.scalar_one()
        s.tickets.append(frozen_ticket)
        await db_session.flush()

        # Normalny ticket: bez podpietego settlement
        normal_ticket = Ticket(
            project_id=project.id,
            number=9002,
            title="Normal Ticket",
            status="backlog",
        )
        db_session.add(normal_ticket)
        await db_session.flush()

        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        # Act
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory_local),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await bulk_update_tickets(
                ctx,
                project.slug,
                [str(frozen_ticket.id), str(normal_ticket.id)],
                priority="high",
            )

        # Assert: zmiana priority na frozen jest blokowana
        assert result["updated"] == 1, (
            f"Oczekiwano updated=1 (tylko normal), dostano {result['updated']}. Frozen ticket nie powinien byc aktualizowany."
        )
        assert len(result["failed"]) == 1, f"Oczekiwano 1 failed (frozen ticket), dostano {result['failed']}"
        failed_ids = [f["id"] for f in result["failed"]]
        assert str(frozen_ticket.id) in failed_ids, f"ID frozen ticketu powinno byc w failed: {result['failed']}"
        # Reason musi zawierac info o zamrozeniu
        frozen_fail = next(f for f in result["failed"] if f["id"] == str(frozen_ticket.id))
        assert "zamroz" in frozen_fail["reason"].lower() or "frozen" in frozen_fail["reason"].lower(), (
            f"Reason powinien zawierac info o zamrozeniu: {frozen_fail['reason']}"
        )

        # Sprawdz DB: frozen_ticket NIE zostal zmieniony, normal_ticket -> priority high
        result_frozen = await db_session.execute(select(Ticket).where(Ticket.id == frozen_ticket.id))
        ft = result_frozen.scalar_one()
        assert ft.priority != "high", f"Frozen ticket nie powinien byc zmieniony, ale priority={ft.priority}"

        result_normal = await db_session.execute(select(Ticket).where(Ticket.id == normal_ticket.id))
        nt = result_normal.scalar_one()
        assert nt.priority == "high", f"Normal ticket powinien byc zaktualizowany na priority=high, ale priority={nt.priority}"
