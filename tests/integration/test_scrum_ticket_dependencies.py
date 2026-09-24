"""Testy integracyjne -- zaleznosci (blokery) ticketow w module Scrum (MON-199).

TDD: testy pisane PRZED implementacja UI zaleznosci -- maja teraz FAILOWAC.
Model TicketDependency, relacje Ticket.blocked_by/blocking i serwis
services.ticket_dependencies.set_blockers pochodza z MON-198 (juz w bazowym
commicie). Ten plik pokrywa kontrakt interfejsu ustalony przez Team Managera:
pole formularza blocked_by_ids (create/edit), sekcje "Zaleznosci" na szczegole,
badge "zablokowany" na szczegole/backlogu/tablicy, gate zamrozenia.

MON-207 (P9, decyzja TM -- wariant 2 "ujednolicic"): KAZDY nieprawidlowy
blocker_id (self-reference, bloker z innego projektu, nieistniejacy,
cykl bezposredni/posredni) na CREATE i EDIT zwraca 422 -- surowe blocker_ids
ida prosto do set_blockers, bez wstepnego filtra "jak etykiety" (cichy drop
blednego ID byl uznany przez code review MR !98 za problem, nie feature).
Komunikat bez surowego UUID: klucz ticketu tam, gdzie ticket istnieje (self-ref,
obcy projekt, cykl), ogolny komunikat gdy ID nie istnieje w ogole. Przy 422 caly
submit jest odrzucany -- inne pola formularza (np. tytul) NIE zostaja zapisane
(TestBlockerValidation), analogicznie do cyklu bezposredniego (TestBlockerDirectCycle).
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from monolynx.constants import STATUS_LABELS
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_dependency import TicketDependency
from monolynx.models.user import User
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers (wzorowane na tests/integration/test_settlements_scrum_integration.py)
# ---------------------------------------------------------------------------


def _make_project(name: str, slug: str | None = None) -> Project:
    if slug is None:
        slug = f"tdep-{secrets.token_hex(4)}"
    return Project(
        name=name,
        slug=slug,
        code=secrets.token_hex(3).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


async def _create_user_with_role(
    db_session,
    email: str,
    project: Project,
    role: str = "owner",
    is_superuser: bool = False,
) -> User:
    user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=is_superuser)
    db_session.add(user)
    await db_session.flush()
    db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
    await db_session.flush()
    return user


async def _login_existing_user(client, email: str) -> None:
    resp = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


async def _create_ticket(
    db_session,
    project: Project,
    title: str,
    number: int,
    status: str = "backlog",
) -> Ticket:
    ticket = Ticket(project_id=project.id, number=number, title=title, status=status, priority="medium")
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _create_settlement(db_session, project: Project, creator: User, status: str = "draft") -> Settlement:
    result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    next_number = int(result.scalar_one()) + 1
    settlement = Settlement(
        number=next_number,
        name=f"Rozliczenie {next_number}",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        status=status,
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()
    db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project.id))
    await db_session.flush()
    return settlement


async def _link_ticket_to_settlement(db_session, settlement: Settlement, ticket: Ticket) -> None:
    result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
    s = result.scalar_one()
    s.tickets.append(ticket)
    await db_session.flush()


async def _dependency_pairs(db_session, ticket_id) -> set:
    """Zwraca zbior (ticket_id, blocker_id) dla wszystkich wierszy TicketDependency danego ticketu."""
    result = await db_session.execute(select(TicketDependency.ticket_id, TicketDependency.blocker_id).where(TicketDependency.ticket_id == ticket_id))
    return {(row.ticket_id, row.blocker_id) for row in result.all()}


def _blocker_error_text(html: str) -> str:
    """Wyciaga tekst elementu `data-testid="blocker-error"` -- ten sam wzorzec
    split-po-markerze co `test_edit_form_excludes_self_and_marks_current_blockers_selected`
    ponizej (projekt nie uzywa parsera HTML w testach). Formularz 422 renderuje
    zaznaczony selektor kandydatow (UUID-y w `value` opcji) i `action` z id ticketu --
    to sa wartosci formularza, nie wyciek w komunikacie, wiec komunikat bledu
    sprawdzamy w IZOLACJI od reszty odpowiedzi (MON-207, rekoncyliacja runda 2)."""
    after_marker = html.split('data-testid="blocker-error"', 1)[1]
    inner = after_marker.split(">", 1)[1]
    return inner.split("<", 1)[0].strip()


def _option_selected(html: str, select_name: str, value) -> bool:
    """Sprawdza czy opcja o danym `value` w selekcie `select_name` ma atrybut selected."""
    select_html = html.split(f'name="{select_name}"', 1)[1]
    options_html = select_html.split("</select>", 1)[0]
    option_html = options_html.split(f'value="{value}"', 1)[1].split("</option>", 1)[0]
    return "selected" in option_html


# ---------------------------------------------------------------------------
# 1. create -- blocked_by_ids zapisuje relacje
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketCreateWithBlockers:
    async def test_create_ticket_with_blocked_by_ids_saves_dependency(self, client, db_session):
        project = _make_project("TDEP Create", "tdep-create-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepcreate01@test.com", project, "owner")
        blocker = await _create_ticket(db_session, project, "Bloker", 1)

        await _login_existing_user(client, "owner-tdepcreate01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Zablokowany", "blocked_by_ids": [str(blocker.id)]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Ticket).where(Ticket.project_id == project.id, Ticket.title == "Zablokowany"))
        ticket = result.scalar_one()

        assert await _dependency_pairs(db_session, ticket.id) == {(ticket.id, blocker.id)}


# ---------------------------------------------------------------------------
# 2. edit -- dodaje i usuwa blokery (replace-all)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketEditBlockers:
    async def test_edit_adds_blocker(self, client, db_session):
        project = _make_project("TDEP Edit Add", "tdep-edit-add-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepeditadd01@test.com", project, "owner")
        ticket = await _create_ticket(db_session, project, "Edytowany", 1)
        blocker = await _create_ticket(db_session, project, "Nowy bloker", 2)

        await _login_existing_user(client, "owner-tdepeditadd01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Edytowany", "blocked_by_ids": [str(blocker.id)]},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        assert await _dependency_pairs(db_session, ticket.id) == {(ticket.id, blocker.id)}

    async def test_edit_omitting_field_removes_all_blockers(self, client, db_session):
        project = _make_project("TDEP Edit Remove", "tdep-edit-rm-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepeditrm01@test.com", project, "owner")
        blocker = await _create_ticket(db_session, project, "Bloker", 1)
        ticket = await _create_ticket(db_session, project, "Zablokowany", 2)
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepeditrm01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Zablokowany"},  # brak pola blocked_by_ids
            follow_redirects=False,
        )
        assert resp.status_code == 303

        assert await _dependency_pairs(db_session, ticket.id) == set()


# ---------------------------------------------------------------------------
# 3. walidacja -- KAZDY nieprawidlowy blocker_id (self-ref, obcy projekt,
#    nieistniejacy) na CREATE i EDIT -> 422 (decyzja TM do P9, wariant 2:
#    "surowe blocker_ids prosto do set_blockers, bez wstepnego filtra";
#    cichy drop bledny ID byl uznany za problem, nie za feature "jak etykiety").
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlockerValidation:
    async def test_edit_self_reference_returns_422_and_does_not_save_title(self, client, db_session):
        project = _make_project("TDEP Self", "tdep-self-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepself01@test.com", project, "owner")
        ticket = await _create_ticket(db_session, project, "Self", 1)
        ticket_key = f"{project.code}-{ticket.number}"

        await _login_existing_user(client, "owner-tdepself01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Self edytowany", "priority": "high", "blocked_by_ids": [str(ticket.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 422
        assert "Self edytowany" in resp.text
        error_text = _blocker_error_text(resp.text)
        assert ticket_key in error_text
        assert str(ticket.id) not in error_text

        assert await _dependency_pairs(db_session, ticket.id) == set()

        # 422 = caly submit odrzucony (nie tylko relacja) -- tytul i priorytet zostaja oryginalne
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        refreshed = result.scalar_one()
        assert refreshed.title == "Self"
        assert refreshed.priority == "medium"

    async def test_create_blocker_from_other_project_returns_422_and_ticket_not_created(self, client, db_session):
        project = _make_project("TDEP Cross Create", "tdep-cross-create-01")
        other_project = _make_project("TDEP Cross Other", "tdep-cross-other-01")
        db_session.add_all([project, other_project])
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepcrosscr01@test.com", project, "owner")
        foreign_ticket = await _create_ticket(db_session, other_project, "Obcy", 1)
        foreign_key = f"{other_project.code}-{foreign_ticket.number}"

        await _login_existing_user(client, "owner-tdepcrosscr01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Nowy", "blocked_by_ids": [str(foreign_ticket.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 422
        assert "Nowy" in resp.text
        error_text = _blocker_error_text(resp.text)
        assert foreign_key in error_text
        assert str(foreign_ticket.id) not in error_text

        result = await db_session.execute(select(Ticket).where(Ticket.project_id == project.id, Ticket.title == "Nowy"))
        assert result.scalar_one_or_none() is None

    async def test_edit_blocker_from_other_project_returns_422_and_does_not_save_title(self, client, db_session):
        project = _make_project("TDEP Cross Edit", "tdep-cross-edit-01")
        other_project = _make_project("TDEP Cross Edit Other", "tdep-cross-edit-other-01")
        db_session.add_all([project, other_project])
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepcrossed01@test.com", project, "owner")
        ticket = await _create_ticket(db_session, project, "Oryginalny", 1)
        foreign_ticket = await _create_ticket(db_session, other_project, "Obcy", 1)
        foreign_key = f"{other_project.code}-{foreign_ticket.number}"

        await _login_existing_user(client, "owner-tdepcrossed01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Edytowany", "priority": "high", "blocked_by_ids": [str(foreign_ticket.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 422
        assert "Edytowany" in resp.text
        error_text = _blocker_error_text(resp.text)
        assert foreign_key in error_text
        assert str(foreign_ticket.id) not in error_text

        assert await _dependency_pairs(db_session, ticket.id) == set()

        # 422 = caly submit odrzucony -- inne pola (tytul, priorytet) NIE zostaly zapisane
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        refreshed = result.scalar_one()
        assert refreshed.title == "Oryginalny"
        assert refreshed.priority == "medium"

    async def test_create_with_nonexistent_blocker_returns_422_with_generic_message_and_no_uuid(self, client, db_session):
        """Bloker, ktory nie istnieje w ogole (nie ma go w zadnym projekcie), nie ma klucza
        do pokazania -- oczekujemy ogolnego komunikatu bez surowego UUID w tresci odpowiedzi
        (decyzja TM do P9: komunikat bez UUID wszedzie, klucz tylko gdy istnieje)."""
        project = _make_project("TDEP Nonexistent Create", "tdep-nonexist-create-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepnonexist01@test.com", project, "owner")

        nonexistent_blocker_id = uuid.uuid4()
        await _login_existing_user(client, "owner-tdepnonexist01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/create",
            data={"title": "Nowy z widmo-blokerem", "priority": "high", "blocked_by_ids": [str(nonexistent_blocker_id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 422
        assert "Nowy z widmo-blokerem" in resp.text
        error_text = _blocker_error_text(resp.text)
        assert "bloker" in error_text.lower()
        assert str(nonexistent_blocker_id) not in error_text

        result = await db_session.execute(select(Ticket).where(Ticket.project_id == project.id, Ticket.title == "Nowy z widmo-blokerem"))
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# 4. cykl bezposredni -- odrzucony jako 422 (bez zapisu reszty submitu),
#    tytul z formularza widoczny w odpowiedzi, komunikat uzywa klucza ticketu
#    (MON-207: dawniej 303 + flash + redirect na edit; krytyk wskazal, ze
#    redirect gubi wpisane pola formularza i UUID w komunikacie jest bezuzyteczny
#    dla czlowieka czytajacego blad)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBlockerDirectCycle:
    async def test_direct_cycle_rejected_returns_422_with_submitted_title_and_ticket_key(self, client, db_session):
        project = _make_project("TDEP Cycle", "tdep-cycle-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepcycle01@test.com", project, "owner")
        ticket_a = await _create_ticket(db_session, project, "A", 1)
        ticket_b = await _create_ticket(db_session, project, "B", 2)
        # A jest juz blokowany przez B
        db_session.add(TicketDependency(ticket_id=ticket_a.id, blocker_id=ticket_b.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepcycle01@test.com")
        # Edycja B: proba ustawienia blocked_by_ids=[A] -> cykl bezposredni A<->B
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket_b.id}/edit",
            data={
                "title": "B zmieniony tytul",
                "priority": "high",
                "blocked_by_ids": [str(ticket_a.id)],
            },
            follow_redirects=False,
        )

        assert resp.status_code == 422
        # Formularz nie gubi wpisanych danych -- zmieniony (niezapisany) tytul
        # jest widoczny wprost w odpowiedzi, nie tylko w bazie po redirectcie
        assert "B zmieniony tytul" in resp.text
        # Komunikat bledu uzywa klucza ticketu (PROJECT-N), nie surowego UUID
        key_a = f"{project.code}-{ticket_a.number}"
        error_text = _blocker_error_text(resp.text)
        assert str(ticket_a.id) not in error_text
        assert key_a in error_text
        # A jest prawdziwym kandydatem w tym samym projekcie (nie obcy/nieistniejacy) --
        # formularz musi pokazac uzytkownikowi dokladnie to, co wyslal, wiec opcja A
        # w selektorze blocked_by_ids zostaje zaznaczona mimo odrzucenia submitu
        assert _option_selected(resp.text, "blocked_by_ids", ticket_a.id)

        # Zadna zmiana z tego submitu (tytul, priorytet) nie zostala zapisana
        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket_b.id))
        refreshed_b = result.scalar_one()
        assert refreshed_b.title == "B"
        assert refreshed_b.priority == "medium"

        # Zadna nowa relacja u B nie powstala
        assert await _dependency_pairs(db_session, ticket_b.id) == set()
        # Oryginalna relacja A<-B pozostaje nienaruszona
        assert await _dependency_pairs(db_session, ticket_a.id) == {(ticket_a.id, ticket_b.id)}


# ---------------------------------------------------------------------------
# 5. zamrozony ticket -- edycja nie zmienia blokerow (istniejacy gate)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFrozenTicketBlockers:
    async def test_frozen_ticket_post_edit_does_not_change_blockers(self, client, db_session):
        project = _make_project("TDEP Frozen", "tdep-frozen-01")
        db_session.add(project)
        await db_session.flush()
        owner = await _create_user_with_role(db_session, "owner-tdepfrozen01@test.com", project, "owner")
        settlement = await _create_settlement(db_session, project, owner, "sent")
        ticket = await _create_ticket(db_session, project, "Zamrozony", 1)
        blocker = await _create_ticket(db_session, project, "Bloker", 2)
        await _link_ticket_to_settlement(db_session, settlement, ticket)

        await _login_existing_user(client, "owner-tdepfrozen01@test.com")
        resp = await client.post(
            f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit",
            data={"title": "Proba zmiany", "blocked_by_ids": [str(blocker.id)]},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert f"/scrum/tickets/{ticket.id}" in resp.headers["location"]

        assert await _dependency_pairs(db_session, ticket.id) == set()

        result = await db_session.execute(select(Ticket).where(Ticket.id == ticket.id))
        refreshed = result.scalar_one()
        assert refreshed.title == "Zamrozony"


# ---------------------------------------------------------------------------
# 6. szczegol ticketu -- sekcja "Zaleznosci", link + status blokera, badge
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketDetailDependencies:
    async def test_detail_shows_dependencies_section_with_link_and_status(self, client, db_session):
        project = _make_project("TDEP Detail", "tdep-detail-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepdetail01@test.com", project, "owner")
        blocker = await _create_ticket(db_session, project, "Bloker w toku", 1, status="in_progress")
        ticket = await _create_ticket(db_session, project, "Zablokowany", 2)
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepdetail01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")

        assert resp.status_code == 200
        assert 'data-testid="dependencies-section"' in resp.text
        assert f'href="/dashboard/{project.slug}/scrum/tickets/{blocker.id}"' in resp.text
        assert "Bloker w toku" in resp.text
        assert STATUS_LABELS["in_progress"] in resp.text

    async def test_detail_badge_present_when_blocker_not_done(self, client, db_session):
        project = _make_project("TDEP Badge On", "tdep-badge-on-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepbadgeon01@test.com", project, "owner")
        blocker = await _create_ticket(db_session, project, "Bloker", 1, status="todo")
        ticket = await _create_ticket(db_session, project, "Zablokowany", 2)
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepbadgeon01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")

        assert resp.status_code == 200
        assert 'data-testid="blocked-badge"' in resp.text

    async def test_detail_badge_absent_when_all_blockers_done(self, client, db_session):
        project = _make_project("TDEP Badge Off Done", "tdep-badge-off-done-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepbadgeoffd01@test.com", project, "owner")
        blocker = await _create_ticket(db_session, project, "Bloker", 1, status="done")
        ticket = await _create_ticket(db_session, project, "Odblokowany", 2)
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepbadgeoffd01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")

        assert resp.status_code == 200
        assert 'data-testid="blocked-badge"' not in resp.text

    async def test_detail_badge_absent_when_no_blockers(self, client, db_session):
        project = _make_project("TDEP Badge Off None", "tdep-badge-off-none-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepbadgeoffn01@test.com", project, "owner")
        ticket = await _create_ticket(db_session, project, "Bez blokerow", 1)

        await _login_existing_user(client, "owner-tdepbadgeoffn01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}")

        assert resp.status_code == 200
        assert 'data-testid="blocked-badge"' not in resp.text


# ---------------------------------------------------------------------------
# 7. backlog i tablica -- badge zablokowanego ticketu
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBacklogBoardBadge:
    async def test_backlog_shows_badge_for_blocked_ticket(self, client, db_session):
        project = _make_project("TDEP Backlog Badge", "tdep-backlog-badge-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepbacklogb01@test.com", project, "owner")
        blocker = await _create_ticket(db_session, project, "Bloker", 1, status="todo")
        ticket = await _create_ticket(db_session, project, "Zablokowany na backlogu", 2)
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepbacklogb01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/backlog?status=all")

        assert resp.status_code == 200
        assert 'data-testid="blocked-badge"' in resp.text

    async def test_board_shows_badge_for_blocked_ticket(self, client, db_session):
        project = _make_project("TDEP Board Badge", "tdep-board-badge-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepboardb01@test.com", project, "owner")
        sprint = Sprint(project_id=project.id, name="Sprint aktywny", status="active", start_date=date(2026, 1, 1))
        db_session.add(sprint)
        await db_session.flush()
        blocker = await _create_ticket(db_session, project, "Bloker", 1, status="todo")
        ticket = await _create_ticket(db_session, project, "Zablokowany na tablicy", 2, status="todo")
        ticket.sprint_id = sprint.id
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepboardb01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/board")

        assert resp.status_code == 200
        assert 'data-testid="blocked-badge"' in resp.text


# ---------------------------------------------------------------------------
# 8. formularz create/edit -- <select name="blocked_by_ids" multiple>
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketFormBlockersSelect:
    async def test_create_form_has_blocked_by_select_with_project_tickets(self, client, db_session):
        project = _make_project("TDEP Form Create", "tdep-form-create-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepformcr01@test.com", project, "owner")
        candidate = await _create_ticket(db_session, project, "Kandydat na blokera", 1)

        await _login_existing_user(client, "owner-tdepformcr01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/create")

        assert resp.status_code == 200
        assert 'name="blocked_by_ids"' in resp.text
        select_open_tag = resp.text.split('name="blocked_by_ids"', 1)[1].split(">", 1)[0]
        assert "multiple" in select_open_tag
        assert f'value="{candidate.id}"' in resp.text
        assert "Kandydat na blokera" in resp.text

    async def test_edit_form_excludes_self_and_marks_current_blockers_selected(self, client, db_session):
        project = _make_project("TDEP Form Edit", "tdep-form-edit-01")
        db_session.add(project)
        await db_session.flush()
        await _create_user_with_role(db_session, "owner-tdepformed01@test.com", project, "owner")
        blocker = await _create_ticket(db_session, project, "Aktualny bloker", 1)
        ticket = await _create_ticket(db_session, project, "Edytowany", 2)
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await _login_existing_user(client, "owner-tdepformed01@test.com")
        resp = await client.get(f"/dashboard/{project.slug}/scrum/tickets/{ticket.id}/edit")

        assert resp.status_code == 200

        # select blocked_by_ids nie zawiera opcji samego edytowanego ticketu
        select_html = resp.text.split('name="blocked_by_ids"', 1)[1]
        options_html = select_html.split("</select>", 1)[0]
        assert f'value="{ticket.id}"' not in options_html

        # aktualny bloker jest oznaczony jako selected
        option_html = options_html.split(f'value="{blocker.id}"', 1)[1].split("</option>", 1)[0]
        assert "selected" in option_html
