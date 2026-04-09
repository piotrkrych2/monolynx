"""Testy integracyjne -- MON-63: workflow statusow rozliczen (draft -> sent -> paid, dwukierunkowe)."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from monolynx.services.settlements import ALLOWED_SETTLEMENT_TRANSITIONS, change_settlement_status, is_ticket_frozen

# ---------------------------------------------------------------------------
# Helpers (powielone z test_settlements_scrum_integration.py dla izolacji)
# ---------------------------------------------------------------------------


async def _create_project(db_session, name: str, slug: str | None = None) -> Project:
    if slug is None:
        slug = f"wf-{secrets.token_hex(4)}"
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
    sent_at: datetime | None = None,
    paid_at: datetime | None = None,
) -> Settlement:
    result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    next_number = int(result.scalar_one()) + 1

    settlement = Settlement(
        number=next_number,
        name=name or f"Rozliczenie WF {next_number}",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        status=status,
        sent_at=sent_at,
        paid_at=paid_at,
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()

    sp = SettlementProject(settlement_id=settlement.id, project_id=project.id)
    db_session.add(sp)
    await db_session.flush()

    return settlement


async def _create_ticket(db_session, project: Project, number: int | None = None) -> Ticket:
    if number is None:
        import random

        number = random.randint(10000, 99999)
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=f"Ticket WF #{number}",
        status="backlog",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _link_ticket_to_settlement(db_session, settlement: Settlement, ticket: Ticket) -> None:
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
    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def _get_settlement_fresh(db_session, settlement_id: uuid.UUID) -> Settlement:
    """Pobiera settlement z eager-loaded projects po ID."""
    result = await db_session.execute(
        select(Settlement)
        .options(
            selectinload(Settlement.projects),
            selectinload(Settlement.tickets),
        )
        .where(Settlement.id == settlement_id)
    )
    return result.scalar_one()


# ---------------------------------------------------------------------------
# Testy jednostkowe: ALLOWED_SETTLEMENT_TRANSITIONS
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAllowedSettlementTransitions:
    def test_draft_allows_only_sent(self):
        """Z draft mozna przejsc tylko do sent."""
        assert ALLOWED_SETTLEMENT_TRANSITIONS["draft"] == frozenset({"sent"})

    def test_sent_allows_paid_and_draft(self):
        """Z sent mozna przejsc do paid lub cofnac do draft."""
        assert ALLOWED_SETTLEMENT_TRANSITIONS["sent"] == frozenset({"paid", "draft"})

    def test_paid_allows_only_sent(self):
        """Z paid mozna cofnac tylko do sent."""
        assert ALLOWED_SETTLEMENT_TRANSITIONS["paid"] == frozenset({"sent"})


# ---------------------------------------------------------------------------
# Testy integracyjne: zmiana statusu przez endpoint POST /status
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementStatusWorkflow:
    """MON-63 -- workflow statusow draft -> sent -> paid (bidirectional)."""

    async def test_draft_to_sent_sets_sent_at(self, client, db_session):
        """draft -> sent ustawia sent_at na aktualny czas, paid_at pozostaje None."""
        # Arrange
        project = await _create_project(db_session, "WF DraftSent", "wf-draftsent-01")
        owner = await _create_user_with_role(db_session, "owner-wfdraftsent01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")
        before = datetime.now(UTC)

        await _login_existing_user(client, "owner-wfdraftsent01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "sent"},
            follow_redirects=False,
        )

        # Assert: redirect na detal
        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

        # DB assertion
        fresh = await _get_settlement_fresh(db_session, settlement.id)
        assert fresh.status == "sent"
        assert fresh.sent_at is not None
        assert fresh.sent_at >= before
        assert fresh.paid_at is None

    async def test_sent_to_paid_sets_paid_at_preserves_sent_at(self, client, db_session):
        """sent -> paid ustawia paid_at, zachowuje sent_at."""
        # Arrange
        known_sent_at = datetime(2026, 1, 15, 10, 0, 0, tzinfo=UTC)
        project = await _create_project(db_session, "WF SentPaid", "wf-sentpaid-01")
        owner = await _create_user_with_role(db_session, "owner-wfsentpaid01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent", sent_at=known_sent_at)

        await _login_existing_user(client, "owner-wfsentpaid01@test.com")

        before_paid = datetime.now(UTC)

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "paid"},
            follow_redirects=False,
        )

        # Assert
        assert resp.status_code == 303

        fresh = await _get_settlement_fresh(db_session, settlement.id)
        assert fresh.status == "paid"
        assert fresh.paid_at is not None
        assert fresh.paid_at >= before_paid
        # sent_at zachowane
        assert fresh.sent_at is not None
        assert fresh.sent_at == known_sent_at

    async def test_paid_to_sent_clears_paid_at_preserves_sent_at(self, client, db_session):
        """paid -> sent czyści paid_at, zachowuje sent_at."""
        # Arrange
        known_sent_at = datetime(2026, 2, 1, 8, 0, 0, tzinfo=UTC)
        known_paid_at = datetime(2026, 2, 10, 12, 0, 0, tzinfo=UTC)
        project = await _create_project(db_session, "WF PaidSent", "wf-paidsent-01")
        owner = await _create_user_with_role(db_session, "owner-wfpaidsent01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "paid", sent_at=known_sent_at, paid_at=known_paid_at)

        await _login_existing_user(client, "owner-wfpaidsent01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "sent"},
            follow_redirects=False,
        )

        # Assert
        assert resp.status_code == 303

        fresh = await _get_settlement_fresh(db_session, settlement.id)
        assert fresh.status == "sent"
        assert fresh.paid_at is None
        # sent_at zachowane
        assert fresh.sent_at == known_sent_at

    async def test_sent_to_draft_clears_both_timestamps(self, client, db_session):
        """sent -> draft czysci zarówno sent_at jak i paid_at."""
        # Arrange
        known_sent_at = datetime(2026, 3, 5, 9, 0, 0, tzinfo=UTC)
        project = await _create_project(db_session, "WF SentDraft", "wf-sentdraft-01")
        owner = await _create_user_with_role(db_session, "owner-wfsentdraft01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent", sent_at=known_sent_at)

        await _login_existing_user(client, "owner-wfsentdraft01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "draft"},
            follow_redirects=False,
        )

        # Assert
        assert resp.status_code == 303

        fresh = await _get_settlement_fresh(db_session, settlement.id)
        assert fresh.status == "draft"
        assert fresh.sent_at is None
        assert fresh.paid_at is None

    async def test_invalid_transition_draft_to_paid_redirects_with_flash(self, client, db_session):
        """draft -> paid bezposrednio jest niedozwolone -- redirect + flash error, status nie zmieniony."""
        # Arrange
        project = await _create_project(db_session, "WF InvalidDraftPaid", "wf-invdraftpaid-01")
        owner = await _create_user_with_role(db_session, "owner-wfinvdp01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        await _login_existing_user(client, "owner-wfinvdp01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "paid"},
            follow_redirects=False,
        )

        # Assert: redirect (nie 5xx)
        assert resp.status_code == 303

        # Status NIE zmieniony
        fresh = await _get_settlement_fresh(db_session, settlement.id)
        assert fresh.status == "draft"

    async def test_invalid_transition_same_status_fails(self, client, db_session):
        """draft -> draft (ten sam status) jest niedozwolone -- redirect, status bez zmian."""
        # Arrange
        project = await _create_project(db_session, "WF SameStatus", "wf-samestatus-01")
        owner = await _create_user_with_role(db_session, "owner-wfss01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        await _login_existing_user(client, "owner-wfss01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "draft"},
            follow_redirects=False,
        )

        # Assert: redirect
        assert resp.status_code == 303

        # Status bez zmian
        fresh = await _get_settlement_fresh(db_session, settlement.id)
        assert fresh.status == "draft"

    async def test_unknown_status_redirects_with_flash(self, client, db_session):
        """new_status='xyz' -> redirect z flash error, status bez zmian."""
        # Arrange
        project = await _create_project(db_session, "WF UnknownStatus", "wf-unknown-01")
        owner = await _create_user_with_role(db_session, "owner-wfunknown01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        await _login_existing_user(client, "owner-wfunknown01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "xyz"},
            follow_redirects=False,
        )

        # Assert: redirect (nie 5xx)
        assert resp.status_code == 303

        # Status bez zmian
        fresh = await _get_settlement_fresh(db_session, settlement.id)
        assert fresh.status == "draft"

    async def test_no_write_permission_returns_403(self, client, db_session):
        """User bez rozliczenia:write (rola member) -> 403 przy POST /status."""
        # Arrange
        project = await _create_project(db_session, "WF NoWrite", "wf-nowrite-01")
        owner = await _create_user_with_role(db_session, "owner-wfnw01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")

        # member NIE ma rozliczenia:write
        await _create_user_with_role(db_session, "member-wfnw01@test.com", project, "member")
        await _login_existing_user(client, "member-wfnw01@test.com")

        # Act
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "sent"},
            follow_redirects=False,
        )

        # Assert: 403
        assert resp.status_code == 403

    async def test_frozen_tickets_after_draft_to_sent(self, client, db_session):
        """Po zmianie draft->sent powiazane tickety staja sie zamrozone."""
        # Arrange
        project = await _create_project(db_session, "WF FreezeOnSent", "wf-freezeon-01")
        owner = await _create_user_with_role(db_session, "owner-wffreezon01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "draft")
        ticket = await _create_ticket(db_session, project, number=20001)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-wffreezon01@test.com")

        # Weryfikacja: przed zmiana ticket NIE jest frozen
        result = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket.id))
        t_before = result.scalar_one()
        assert is_ticket_frozen(t_before) is False

        # Act: zmiana draft -> sent
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "sent"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Assert: ticket jest teraz frozen
        result2 = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket.id))
        t_after = result2.scalar_one()
        assert is_ticket_frozen(t_after) is True

    async def test_unfrozen_after_sent_to_draft(self, client, db_session):
        """Po sent->draft ticket znow jest edytowalny (nie frozen)."""
        # Arrange
        known_sent_at = datetime(2026, 4, 1, 10, 0, 0, tzinfo=UTC)
        project = await _create_project(db_session, "WF UnfreezeOnDraft", "wf-unfreeze-01")
        owner = await _create_user_with_role(db_session, "owner-wfunfreeze01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent", sent_at=known_sent_at)
        ticket = await _create_ticket(db_session, project, number=20002)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-wfunfreeze01@test.com")

        # Weryfikacja: ticket jest frozen przed zmiana
        result = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket.id))
        t_before = result.scalar_one()
        assert is_ticket_frozen(t_before) is True

        # Act: cofniecie sent -> draft
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "draft"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Assert: ticket NIE jest juz frozen
        result2 = await db_session.execute(select(Ticket).options(selectinload(Ticket.settlements)).where(Ticket.id == ticket.id))
        t_after = result2.scalar_one()
        assert is_ticket_frozen(t_after) is False

    async def test_cross_project_write_permission_required_in_all_projects(self, client, db_session):
        """
        Settlement cross-project (A + B). User ma rozliczenia:write w A ale nie w B.
        POST /status powinno byc odrzucone (HTTPException 403).

        Test weryfikuje logike serwisu bezposrednio (change_settlement_status).
        """
        # Arrange
        project_a = await _create_project(db_session, "WF CrossA", "wf-crossa-01")
        project_b = await _create_project(db_session, "WF CrossB", "wf-crossb-01")

        # Owner ma write w A
        owner = await _create_user_with_role(db_session, "owner-wfcross01@test.com", project_a, "owner")
        # Owner NIE ma write w B (tylko member = brak rozliczenia:write)
        await db_session.flush()
        member_b = ProjectMember(
            project_id=project_b.id,
            user_id=owner.id,
            role="member",
        )
        db_session.add(member_b)
        await db_session.flush()

        # Tworzy settlement manualnie w projekcie A
        result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
        next_number = int(result.scalar_one()) + 1
        settlement = Settlement(
            number=next_number,
            name="Cross-Project Settlement",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            status="draft",
            created_by_id=owner.id,
        )
        db_session.add(settlement)
        await db_session.flush()

        # Dodaj M2M dla OBU projektow
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project_a.id))
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project_b.id))
        await db_session.flush()

        # Pobierz settlement z eager-loaded projects dla change_settlement_status
        fresh = await _get_settlement_fresh(db_session, settlement.id)

        # Act: wywolaj serwis bezposrednio -- powinno rzucic HTTPException 403
        with pytest.raises(HTTPException) as exc_info:
            await change_settlement_status(db_session, fresh, owner.id, "sent")

        # Assert
        assert exc_info.value.status_code == 403


# ---------------------------------------------------------------------------
# Testy jednostkowe: change_settlement_status -- walidacja bezposrednia
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestChangeSettlementStatusUnit:
    """Testy logiki change_settlement_status bez HTTP klienta."""

    async def test_invalid_status_raises_value_error(self, db_session):
        """new_status='xyz' -> ValueError z informacja o dozwolonych statusach."""
        # Arrange
        project = await _create_project(db_session, "Unit InvalidStatus", "unit-invst-01")
        creator = await _create_user_with_role(db_session, "creator-unitinvst01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "draft")
        fresh = await _get_settlement_fresh(db_session, settlement.id)

        # Act & Assert
        with pytest.raises(ValueError, match="Nieprawidlowy status"):
            await change_settlement_status(db_session, fresh, creator.id, "xyz")

    async def test_invalid_transition_raises_value_error(self, db_session):
        """draft -> paid (niedozwolone przejscie) -> ValueError."""
        # Arrange
        project = await _create_project(db_session, "Unit InvTransition", "unit-invtr-01")
        creator = await _create_user_with_role(db_session, "creator-unitinvtr01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "draft")
        fresh = await _get_settlement_fresh(db_session, settlement.id)

        # Act & Assert
        with pytest.raises(ValueError, match="Nieprawidlowe przejscie statusu"):
            await change_settlement_status(db_session, fresh, creator.id, "paid")

    async def test_draft_to_sent_timestamp_logic(self, db_session):
        """draft -> sent: serwis ustawia sent_at, paid_at pozostaje None."""
        # Arrange
        project = await _create_project(db_session, "Unit DraftSent", "unit-ds-01")
        creator = await _create_user_with_role(db_session, "creator-unitds01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "draft")
        fresh = await _get_settlement_fresh(db_session, settlement.id)

        before = datetime.now(UTC)

        # Act
        updated = await change_settlement_status(db_session, fresh, creator.id, "sent")

        # Assert
        assert updated.status == "sent"
        assert updated.sent_at is not None
        assert updated.sent_at >= before
        assert updated.paid_at is None

    async def test_sent_to_draft_clears_timestamps(self, db_session):
        """sent -> draft: serwis czysci sent_at i paid_at."""
        # Arrange
        known_sent = datetime(2026, 1, 20, 12, 0, 0, tzinfo=UTC)
        project = await _create_project(db_session, "Unit SentDraft", "unit-sd-01")
        creator = await _create_user_with_role(db_session, "creator-unitsd01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "sent", sent_at=known_sent)
        fresh = await _get_settlement_fresh(db_session, settlement.id)

        # Act
        updated = await change_settlement_status(db_session, fresh, creator.id, "draft")

        # Assert
        assert updated.status == "draft"
        assert updated.sent_at is None
        assert updated.paid_at is None

    async def test_sent_to_paid_preserves_sent_at(self, db_session):
        """sent -> paid: paid_at ustawiony, sent_at zachowany."""
        # Arrange
        known_sent = datetime(2026, 1, 25, 8, 30, 0, tzinfo=UTC)
        project = await _create_project(db_session, "Unit SentPaid", "unit-sp-01")
        creator = await _create_user_with_role(db_session, "creator-unitsp01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "sent", sent_at=known_sent)
        fresh = await _get_settlement_fresh(db_session, settlement.id)

        before_paid = datetime.now(UTC)

        # Act
        updated = await change_settlement_status(db_session, fresh, creator.id, "paid")

        # Assert
        assert updated.status == "paid"
        assert updated.paid_at is not None
        assert updated.paid_at >= before_paid
        assert updated.sent_at == known_sent

    async def test_paid_to_sent_clears_paid_at(self, db_session):
        """paid -> sent: paid_at wyczyszczony, sent_at zachowany."""
        # Arrange
        known_sent = datetime(2026, 2, 5, 9, 0, 0, tzinfo=UTC)
        known_paid = datetime(2026, 2, 15, 14, 0, 0, tzinfo=UTC)
        project = await _create_project(db_session, "Unit PaidSent", "unit-ps-01")
        creator = await _create_user_with_role(db_session, "creator-unitps01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, creator, "paid", sent_at=known_sent, paid_at=known_paid)
        fresh = await _get_settlement_fresh(db_session, settlement.id)

        # Act
        updated = await change_settlement_status(db_session, fresh, creator.id, "sent")

        # Assert
        assert updated.status == "sent"
        assert updated.paid_at is None
        assert updated.sent_at == known_sent
