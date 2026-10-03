"""Testy integracyjne -- services/tickets.py (MON-137: wyekstrahowana logika MCP tickety/board/sprinty).

Testy wolaja funkcje serwisu bezposrednio z fixture `db_session`, bez przechodzenia
przez wrappery MCP (te maja wlasne testy w tests/unit/test_mcp_server.py). Skupiamy
sie na kontrakcie serwisu: resolve_ticket_uuid, blocked_by_ids (create/update),
izolacja miedzy projektami, frozen tickety, bulk update, board, kryteria akceptacji,
sprinty i wywolanie db.commit() (spy, nie tylko flush).
"""

from __future__ import annotations

import secrets
import string
import uuid
from datetime import date
from unittest.mock import patch

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_attachment import TicketAttachment
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.ticket_dependency import TicketDependency
from monolynx.models.user import User
from monolynx.services import tickets as svc
from monolynx.services.auth import hash_password

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _make_user(db, tag: str) -> User:
    user = User(email=f"tk_svc_{tag}_{uuid.uuid4().hex[:8]}@x.com", password_hash=hash_password("x"))
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str) -> Project:
    # Kod TYLKO z liter -- _TICKET_KEY_RE (^([A-Za-z]{1,10})-(\d+)$) wymaga prefiksu
    # bez cyfr; secrets.token_hex() dawalby hex (0-9a-f), co psuloby testy kluczy PROJ-N.
    letters_suffix = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
    project = Project(
        name=f"Tickets Svc {tag}",
        slug=f"tk-svc-{tag}-{uuid.uuid4().hex[:6]}",
        code=f"T{letters_suffix}",
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


async def _make_ticket(db, project: Project, number: int, **kwargs) -> Ticket:
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=kwargs.pop("title", f"Ticket #{number}"),
        status=kwargs.pop("status", "todo"),
        priority=kwargs.pop("priority", "medium"),
        **kwargs,
    )
    db.add(ticket)
    await db.flush()
    return ticket


async def _freeze_ticket(db, project: Project, ticket: Ticket, creator: User, status: str = "sent") -> Settlement:
    """Podpina ticket do rozliczenia sent/paid -- ticket staje sie zamrozony (is_ticket_frozen)."""
    next_number = (await db.execute(select(Settlement.number).order_by(Settlement.number.desc()).limit(1))).scalar_one_or_none() or 0
    settlement = Settlement(
        number=next_number + 1,
        name=f"Rozliczenie {next_number + 1}",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        status=status,
        created_by_id=creator.id,
        is_active=True,
    )
    db.add(settlement)
    await db.flush()

    from sqlalchemy.orm import selectinload

    result = await db.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
    s = result.scalar_one()
    s.tickets.append(ticket)
    await db.flush()
    return settlement


async def _setup(db, tag: str, role: str = "owner"):
    """Standardowy zestaw: user (owner), project."""
    user = await _make_user(db, tag)
    project = await _make_project(db, tag)
    await _make_member(db, project, user, role=role)
    return user, project


# ---------------------------------------------------------------------------
# resolve_ticket_uuid
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestResolveTicketUuid:
    async def test_resolve_by_uuid_returns_same_uuid(self, db_session):
        _user, project = await _setup(db_session, "res-uuid")
        ticket = await _make_ticket(db_session, project, number=1)

        resolved = await svc.resolve_ticket_uuid(str(ticket.id), project.id, db_session)
        assert resolved == ticket.id

    async def test_resolve_by_key_returns_correct_uuid(self, db_session):
        _user, project = await _setup(db_session, "res-key")
        ticket = await _make_ticket(db_session, project, number=42)

        resolved = await svc.resolve_ticket_uuid(f"{project.code}-42", project.id, db_session)
        assert resolved == ticket.id

    async def test_resolve_key_wrong_project_raises(self, db_session):
        """Numer ticketu istnieje, ale w INNYM projekcie -- filtr project_id musi to wylapac."""
        _user_a, project_a = await _setup(db_session, "res-wrong-a")
        _user_b, project_b = await _setup(db_session, "res-wrong-b")
        ticket = await _make_ticket(db_session, project_a, number=7)

        with pytest.raises(ValueError, match="nie istnieje w projekcie"):
            await svc.resolve_ticket_uuid(f"{project_a.code}-{ticket.number}", project_b.id, db_session)

    async def test_resolve_nonexistent_key_raises(self, db_session):
        _user, project = await _setup(db_session, "res-missing")

        with pytest.raises(ValueError, match="nie istnieje w projekcie"):
            await svc.resolve_ticket_uuid(f"{project.code}-9999", project.id, db_session)

    async def test_resolve_invalid_format_raises(self, db_session):
        _user, project = await _setup(db_session, "res-badfmt")

        with pytest.raises(ValueError, match="Nieprawidlowy identyfikator ticketa: 'nie-taki-klucz'"):
            await svc.resolve_ticket_uuid("nie-taki-klucz", project.id, db_session)

    async def test_resolve_key_with_digits_in_prefix_raises_invalid_format(self, db_session):
        """_TICKET_KEY_RE wymaga prefiksu ZLOZONEGO WYLACZNIE Z LITER -- cyfra w prefiksie
        (np. kod projektu z cyframi) nie jest kluczem, tylko nieprawidlowym formatem."""
        _user, project = await _setup(db_session, "res-digitprefix")

        with pytest.raises(ValueError, match="Nieprawidlowy identyfikator ticketa: 'AB12-5'"):
            await svc.resolve_ticket_uuid("AB12-5", project.id, db_session)


# ---------------------------------------------------------------------------
# create_ticket / update_ticket -- blocked_by_ids
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateTicketBlockers:
    async def test_create_ticket_with_blocked_by_ids_creates_dependency_row(self, db_session):
        user, project = await _setup(db_session, "cr-block-ok")
        blocker = await svc.create_ticket(db_session, user, project, title="Bloker")

        main = await svc.create_ticket(
            db_session,
            user,
            project,
            title="Glowny",
            blocked_by_ids=[str(blocker.ticket.id)],
        )

        rows = (await db_session.execute(select(TicketDependency).where(TicketDependency.ticket_id == main.ticket.id))).scalars().all()
        assert len(rows) == 1
        assert rows[0].blocker_id == blocker.ticket.id

    async def test_create_ticket_invalid_blocker_uuid_raises_and_ticket_not_created(self, db_session):
        user, project = await _setup(db_session, "cr-block-bad")

        count_before = (await db_session.execute(select(Ticket.id).where(Ticket.project_id == project.id))).scalars().all()

        with pytest.raises(ValueError, match="Nieprawidlowy UUID blokera: 'nie-uuid'"):
            await svc.create_ticket(db_session, user, project, title="Nie powinien powstac", blocked_by_ids=["nie-uuid"])

        count_after = (await db_session.execute(select(Ticket.id).where(Ticket.project_id == project.id))).scalars().all()
        assert len(count_after) == len(count_before), "walidacja blokerow musi biec PRZED zapisem ticketu"


@pytest.mark.integration
class TestUpdateTicketBlockers:
    async def test_update_ticket_replaces_blockers(self, db_session):
        user, project = await _setup(db_session, "up-block-repl")
        blocker_a = await _make_ticket(db_session, project, number=1, title="A")
        blocker_b = await _make_ticket(db_session, project, number=2, title="B")
        blocker_c = await _make_ticket(db_session, project, number=3, title="C")
        main = await svc.create_ticket(db_session, user, project, title="Main", blocked_by_ids=[str(blocker_a.id)])

        await svc.update_ticket(
            db_session,
            user,
            project,
            str(main.ticket.id),
            blocked_by_ids=[str(blocker_b.id), str(blocker_c.id)],
        )

        rows = (await db_session.execute(select(TicketDependency.blocker_id).where(TicketDependency.ticket_id == main.ticket.id))).scalars().all()
        assert set(rows) == {blocker_b.id, blocker_c.id}

    async def test_update_ticket_invalid_blocker_uuid_raises_before_any_change(self, db_session):
        user, project = await _setup(db_session, "up-block-bad")
        ticket = await _make_ticket(db_session, project, number=1, title="Oryginal")

        with pytest.raises(ValueError, match="Nieprawidlowy UUID blokera: 'zle'"):
            await svc.update_ticket(db_session, user, project, str(ticket.id), title="Nie powinno sie zapisac", blocked_by_ids=["zle"])

        await db_session.refresh(ticket)
        assert ticket.title == "Oryginal"

    async def test_update_ticket_frozen_blocks_other_fields(self, db_session):
        user, project = await _setup(db_session, "up-frozen-block")
        ticket = await _make_ticket(db_session, project, number=1, title="Zamrozony", priority="low")
        await _freeze_ticket(db_session, project, ticket, user, status="sent")

        with pytest.raises(ValueError, match="Ticket zamrozony"):
            await svc.update_ticket(db_session, user, project, str(ticket.id), priority="high")

        await db_session.refresh(ticket)
        assert ticket.priority == "low"

    async def test_update_ticket_frozen_allows_status_only(self, db_session):
        user, project = await _setup(db_session, "up-frozen-status")
        ticket = await _make_ticket(db_session, project, number=1, title="Zamrozony", status="todo")
        await _freeze_ticket(db_session, project, ticket, user, status="paid")

        result = await svc.update_ticket(db_session, user, project, str(ticket.id), status="done")
        assert result.status == "done"


# ---------------------------------------------------------------------------
# get_ticket -- relacje preloaded
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetTicket:
    async def test_get_ticket_preloads_relations_without_lazy_error(self, db_session):
        user, project = await _setup(db_session, "get-relations", role="owner")
        blocker = await svc.create_ticket(db_session, user, project, title="Bloker")
        created = await svc.create_ticket(
            db_session,
            user,
            project,
            title="Glowny",
            acceptance_criteria=["Kryterium 1"],
            blocked_by_ids=[str(blocker.ticket.id)],
        )
        # Komentarz i zalacznik tworzone bezposrednio przez ORM (nie svc.add_comment) --
        # test ma sprawdzac WYLACZNIE kontrakt get_ticket (selectinloady), nie add_comment.
        db_session.add(TicketComment(ticket_id=created.ticket.id, user_id=user.id, content="Pierwszy komentarz"))
        db_session.add(TicketAttachment(ticket_id=created.ticket.id, filename="plik.pdf", storage_path="tickets/plik.pdf", size=1234))
        await db_session.flush()

        result = await svc.get_ticket(db_session, user, project, str(created.ticket.id))

        # Dostep synchroniczny bez await -- jesli serwis pominie ktorys selectinload,
        # SQLAlchemy rzuci MissingGreenlet (lazy="select", domyslne dla comments/attachments/
        # TicketComment.author) albo InvalidRequestError (lazy="raise" dla blocked_by/blocking).
        assert [c.description for c in result.ticket.acceptance_criteria] == ["Kryterium 1"]
        assert [c.content for c in result.ticket.comments] == ["Pierwszy komentarz"]
        assert result.ticket.comments[0].author.email == user.email
        assert [a.filename for a in result.ticket.attachments] == ["plik.pdf"]
        assert [b.id for b in result.ticket.blocked_by] == [blocker.ticket.id]
        assert result.ticket.blocking == []
        assert result.ticket.settlements == []
        assert result.can_read_settlements is True

    async def test_get_ticket_nonexistent_raises(self, db_session):
        _user, project = await _setup(db_session, "get-missing")
        with pytest.raises(ValueError, match=r"^Ticket nie istnieje$"):
            await svc.get_ticket(db_session, _user, project, str(uuid.uuid4()))

    async def test_get_ticket_from_other_project_raises(self, db_session):
        """Ticket istnieje (poprawny UUID), ale nalezy do innego projektu -- filtr project_id w SELECT musi to wylapac."""
        user_a, project_a = await _setup(db_session, "get-cross-a")
        project_b = await _make_project(db_session, "get-cross-b")
        await _make_member(db_session, project_b, user_a)
        ticket = await _make_ticket(db_session, project_a, number=1)

        with pytest.raises(ValueError, match=r"^Ticket nie istnieje$"):
            await svc.get_ticket(db_session, user_a, project_b, str(ticket.id))

    async def test_get_ticket_without_membership_raises_permission_error(self, db_session):
        """User spoza projektu dostaje odmowe uprawnienia, a nie "Ticket nie istnieje" (check przed lookupem)."""
        user_a, project_a = await _setup(db_session, "get-nomember-a")
        _user_b, project_b = await _setup(db_session, "get-nomember-b")
        ticket = await _make_ticket(db_session, project_a, number=1)

        with pytest.raises(ValueError, match=r"^Brak uprawnienia scrum:read$"):
            await svc.get_ticket(db_session, user_a, project_b, str(ticket.id))


# ---------------------------------------------------------------------------
# bulk_update_tickets
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBulkUpdateTickets:
    async def test_bulk_update_partial_fail_reasons(self, db_session):
        user, project = await _setup(db_session, "bulk-partial")
        valid1 = await _make_ticket(db_session, project, number=1, status="todo")
        valid2 = await _make_ticket(db_session, project, number=2, status="todo")
        bad_format = "not-a-valid-key"
        missing_uuid = str(uuid.uuid4())

        result = await svc.bulk_update_tickets(
            db_session,
            user,
            project,
            ticket_ids=[str(valid1.id), str(valid2.id), bad_format, missing_uuid],
            status="done",
        )

        assert result.updated == 2
        failed_by_id = {f["id"]: f["reason"] for f in result.failed}
        assert failed_by_id[bad_format] == "Nieprawidlowy identyfikator ticketa"
        assert failed_by_id[missing_uuid] == "Ticket nie istnieje"

        await db_session.refresh(valid1)
        await db_session.refresh(valid2)
        assert valid1.status == "done"
        assert valid2.status == "done"

    async def test_bulk_update_frozen_ticket_rejects_non_status_field(self, db_session):
        user, project = await _setup(db_session, "bulk-frozen")
        frozen = await _make_ticket(db_session, project, number=1, priority="low")
        await _freeze_ticket(db_session, project, frozen, user, status="sent")
        normal = await _make_ticket(db_session, project, number=2, priority="low")

        result = await svc.bulk_update_tickets(
            db_session,
            user,
            project,
            ticket_ids=[str(frozen.id), str(normal.id)],
            priority="high",
        )

        assert result.updated == 1
        failed_by_id = {f["id"]: f["reason"] for f in result.failed}
        assert failed_by_id[str(frozen.id)] == "Ticket zamrozony — dozwolona tylko zmiana statusu"

        await db_session.refresh(frozen)
        await db_session.refresh(normal)
        assert frozen.priority == "low"
        assert normal.priority == "high"

    async def test_bulk_update_frozen_ticket_allows_status(self, db_session):
        user, project = await _setup(db_session, "bulk-frozen-status")
        frozen = await _make_ticket(db_session, project, number=1, status="todo")
        await _freeze_ticket(db_session, project, frozen, user, status="paid")

        result = await svc.bulk_update_tickets(db_session, user, project, ticket_ids=[str(frozen.id)], status="done")

        assert result.updated == 1
        assert result.failed == []
        await db_session.refresh(frozen)
        assert frozen.status == "done"

    async def test_bulk_update_too_many_raises(self, db_session):
        user, project = await _setup(db_session, "bulk-toomany")
        ids = [str(uuid.uuid4()) for _ in range(101)]

        with pytest.raises(ValueError, match=r"Zbyt duzo ticketow: 101"):
            await svc.bulk_update_tickets(db_session, user, project, ticket_ids=ids)


# ---------------------------------------------------------------------------
# get_board
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetBoard:
    async def test_get_board_no_active_sprint_returns_empty(self, db_session):
        user, project = await _setup(db_session, "board-noactive")
        planning = Sprint(project_id=project.id, name="Planowany", start_date=date(2026, 5, 1), status="planning")
        db_session.add(planning)
        await db_session.flush()

        result = await svc.get_board(db_session, user, project)

        assert result.sprint is None
        assert result.columns == {}

    async def test_get_board_orders_tickets_by_order_field(self, db_session):
        """Seed w kolejnosci C, A, B (rozna od oczekiwanej A, B, C) -- lapie brak ORDER BY Ticket.order."""
        user, project = await _setup(db_session, "board-order")
        sprint = Sprint(project_id=project.id, name="Aktywny", start_date=date(2026, 5, 1), status="active")
        db_session.add(sprint)
        await db_session.flush()

        await _make_ticket(db_session, project, number=3, title="C", status="todo", sprint_id=sprint.id, order=2)
        await _make_ticket(db_session, project, number=1, title="A", status="todo", sprint_id=sprint.id, order=0)
        await _make_ticket(db_session, project, number=2, title="B", status="todo", sprint_id=sprint.id, order=1)

        result = await svc.get_board(db_session, user, project)

        assert result.sprint.id == sprint.id
        assert [row["title"] for row in result.columns["todo"]] == ["A", "B", "C"]
        assert result.columns["done"] == []


# ---------------------------------------------------------------------------
# Kryteria akceptacji
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestAcceptanceCriteria:
    async def test_add_acceptance_criterion_increments_position(self, db_session):
        user, project = await _setup(db_session, "crit-add")
        ticket = await _make_ticket(db_session, project, number=1)

        c1 = await svc.add_acceptance_criterion(db_session, user, project, str(ticket.id), "Pierwsze")
        c2 = await svc.add_acceptance_criterion(db_session, user, project, str(ticket.id), "Drugie")

        assert c1.position == 0
        assert c2.position == 1
        assert c1.created_by_user_id == user.id
        assert c1.created_via_ai is True

    async def test_update_acceptance_criterion_marks_completed(self, db_session):
        user, project = await _setup(db_session, "crit-complete")
        ticket = await _make_ticket(db_session, project, number=1)
        crit = await svc.add_acceptance_criterion(db_session, user, project, str(ticket.id), "Do zrobienia")

        result = await svc.update_acceptance_criterion(db_session, user, project, str(ticket.id), str(crit.id), is_completed=True)

        assert result.is_completed is True
        assert result.completed_by_user_id == user.id
        assert result.completed_via_ai is True
        assert result.completed_at is not None

    async def test_update_acceptance_criterion_unmark_clears_completion_fields(self, db_session):
        user, project = await _setup(db_session, "crit-unmark")
        ticket = await _make_ticket(db_session, project, number=1)
        crit = await svc.add_acceptance_criterion(db_session, user, project, str(ticket.id), "Do zrobienia")
        await svc.update_acceptance_criterion(db_session, user, project, str(ticket.id), str(crit.id), is_completed=True)

        result = await svc.update_acceptance_criterion(db_session, user, project, str(ticket.id), str(crit.id), is_completed=False)

        assert result.is_completed is False
        assert result.completed_by_user_id is None
        assert result.completed_at is None
        assert result.completed_via_ai is False

    async def test_delete_acceptance_criterion_removes_it(self, db_session):
        user, project = await _setup(db_session, "crit-delete")
        ticket = await _make_ticket(db_session, project, number=1)
        crit = await svc.add_acceptance_criterion(db_session, user, project, str(ticket.id), "Usun mnie")

        await svc.delete_acceptance_criterion(db_session, user, project, str(ticket.id), str(crit.id))

        remaining = await svc.list_acceptance_criteria(db_session, user, project, str(ticket.id))
        assert remaining == []


# ---------------------------------------------------------------------------
# Sprinty (create/update -- poza start/complete, ktore zostaja w services/sprint.py)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSprintCrud:
    async def test_create_sprint_defaults_status_planning(self, db_session):
        user, project = await _setup(db_session, "sprint-create")

        sprint = await svc.create_sprint(db_session, user, project, name="Sprint 1", start_date="2026-05-01")

        assert sprint.status == "planning"
        assert sprint.start_date == date(2026, 5, 1)

    async def test_update_sprint_rejects_date_change_when_completed(self, db_session):
        user, project = await _setup(db_session, "sprint-completed")
        sprint = Sprint(project_id=project.id, name="Zakonczony", start_date=date(2026, 1, 1), status="completed")
        db_session.add(sprint)
        await db_session.flush()

        with pytest.raises(ValueError, match="Nie mozna zmienic dat zakonczonego sprintu"):
            await svc.update_sprint(db_session, user, project, str(sprint.id), start_date="2026-02-01")

    async def test_update_sprint_end_before_start_raises(self, db_session):
        user, project = await _setup(db_session, "sprint-baddate")
        sprint = Sprint(project_id=project.id, name="Sprint", start_date=date(2026, 5, 10), status="planning")
        db_session.add(sprint)
        await db_session.flush()

        with pytest.raises(ValueError, match="Data zakonczenia musi byc pozniejsza"):
            await svc.update_sprint(db_session, user, project, str(sprint.id), end_date="2026-05-01")

    async def test_update_sprint_success_changes_fields(self, db_session):
        user, project = await _setup(db_session, "sprint-ok")
        sprint = Sprint(project_id=project.id, name="Stara nazwa", start_date=date(2026, 5, 1), status="planning")
        db_session.add(sprint)
        await db_session.flush()

        result = await svc.update_sprint(
            db_session,
            user,
            project,
            str(sprint.id),
            name="Nowa nazwa",
            goal="Nowy cel",
            end_date="2026-05-20",
        )

        assert result.name == "Nowa nazwa"
        assert result.goal == "Nowy cel"
        assert result.end_date == date(2026, 5, 20)

    async def test_update_sprint_nonexistent_raises(self, db_session):
        user, project = await _setup(db_session, "sprint-missing")
        with pytest.raises(ValueError, match=r"^Sprint nie istnieje$"):
            await svc.update_sprint(db_session, user, project, str(uuid.uuid4()), name="X")


# ---------------------------------------------------------------------------
# Wywolanie db.commit() -- .claude/rules/db-commit.md + test-kills-mutant.md #7
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCommitBehavior:
    async def test_create_ticket_calls_commit(self, db_session):
        """create_ticket() musi wywolac db.commit() -- test pada na mutancie 'usun await db.commit()'.

        `db_session` (tests/conftest.py) wiaze sesje z polaczeniem w OTWARTEJ zewnetrznej
        transakcji, ktora fixture rollbackuje na koncu testu -- session.commit() wewnatrz
        testu nie commituje naprawde do bazy widocznej z innego polaczenia, wiec sprawdzanie
        "widocznosci" wiersza z osobnego polaczenia nic by tu nie dowiodlo. Zamiast tego
        szpiegujemy samo wywolanie commit() (wraps=... -- realny commit nadal sie wykonuje).
        """
        user, project = await _setup(db_session, "commit-check")

        with patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            result = await svc.create_ticket(db_session, user, project, title="Commit sprawdzian")

        assert result.ticket.id is not None
        commit_spy.assert_awaited()
