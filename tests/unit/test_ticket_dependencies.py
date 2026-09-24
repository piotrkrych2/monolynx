"""Testy zaleznosci (blocker) miedzy ticketami: model, serwis, MCP (MON-198).

Pokrywa model TicketDependency (insert, CHECK self-ref, CASCADE, relacje
blocked_by/blocking), serwis services.ticket_dependencies.set_blockers oraz
narzedzia MCP create_ticket/update_ticket/get_ticket z blocked_by_ids.
"""

import re
import secrets
import uuid
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import IntegrityError, InvalidRequestError
from sqlalchemy.orm import selectinload

from monolynx.mcp_server import create_ticket, get_ticket, update_ticket
from monolynx.models import Project, ProjectMember, Ticket, TicketDependency, User
from monolynx.models.ticket_dependency import TicketDependency as TicketDependencyFromSubmodule
from monolynx.services import ticket_dependencies
from monolynx.services.auth import hash_password

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token w naglowku (wzorowane na test_mcp_server.py)."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


async def _make_project(db, suffix: str) -> Project:
    """Tworzy dodatkowy testowy projekt (poza fixture mcp_project) -- na potrzeby scenariuszy cross-project."""
    unique = uuid.uuid4().hex
    slug = f"td-{suffix}-{unique[:6]}"
    project = Project(
        name=f"TD Project {suffix}",
        # kod z losowego hexa, nie z suffixu -- dwa projekty w tym samym tescie
        # (np. "svc-proja"/"svc-projb") daja identyczny prefiks po slug.replace()
        code=f"T{unique[:4]}".upper(),
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


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


@pytest.fixture
async def mcp_user(db_session):
    """Testowy uzytkownik MCP."""
    user = User(
        email=f"mcp-td-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


@pytest.fixture
async def mcp_project(db_session):
    """Testowy projekt."""
    _slug = f"mcp-td-{uuid.uuid4().hex[:8]}"
    project = Project(
        name="MCP TD Project",
        slug=_slug,
        code=_slug.replace("-", "").upper()[:5],
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.fixture
async def mcp_member(db_session, mcp_user, mcp_project):
    """Czlonkostwo w projekcie."""
    member = ProjectMember(
        project_id=mcp_project.id,
        user_id=mcp_user.id,
        role="owner",
    )
    db_session.add(member)
    await db_session.flush()
    return member


@pytest.fixture
def mock_factory(db_session):
    """Zwraca async context manager zastepujacy async_session_factory.

    Podmienia commit() na flush(), zeby nie commitowac outer transakcji
    testowej (db_session). Dzieki temu db_session.rollback() na teardown
    nadal dziala.
    """
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
def mock_verify(mcp_user):
    """AsyncMock verify_mcp_token zwracajacy test usera."""
    return AsyncMock(return_value=mcp_user)


async def _dependency_rows_for(db, ticket_id):
    result = await db.execute(select(TicketDependency).where(TicketDependency.ticket_id == ticket_id))
    return result.scalars().all()


# ---------------------------------------------------------------------------
# Import -- oba sciezki wskazuja na ta sama klase
# ---------------------------------------------------------------------------


class TestTicketDependencyImport:
    def test_importable_from_models_package_and_submodule(self):
        assert TicketDependency is TicketDependencyFromSubmodule


# ---------------------------------------------------------------------------
# Model TicketDependency
# ---------------------------------------------------------------------------


class TestTicketDependencyModel:
    async def test_insert_dependency(self, db_session):
        project = await _make_project(db_session, "model-insert")
        ticket = await _make_ticket(db_session, project, 1)
        blocker = await _make_ticket(db_session, project, 2)

        dep = TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id)
        db_session.add(dep)
        await db_session.flush()

        assert dep.ticket_id == ticket.id
        assert dep.blocker_id == blocker.id

    async def test_check_constraint_rejects_self_reference(self, db_session):
        project = await _make_project(db_session, "model-selfref")
        ticket = await _make_ticket(db_session, project, 1)

        dep = TicketDependency(ticket_id=ticket.id, blocker_id=ticket.id)
        db_session.add(dep)

        with pytest.raises(IntegrityError):
            await db_session.flush()

    async def test_cascade_delete_blocker_removes_dependency_row(self, db_session):
        project = await _make_project(db_session, "model-cascade")
        ticket = await _make_ticket(db_session, project, 1)
        blocker = await _make_ticket(db_session, project, 2)

        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        await db_session.delete(blocker)
        await db_session.flush()

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert rows == []

    async def test_blocked_by_and_blocking_relationships_both_directions(self, db_session):
        project = await _make_project(db_session, "model-rel")
        ticket = await _make_ticket(db_session, project, 1)
        blocker = await _make_ticket(db_session, project, 2)

        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        # Zapamietujemy ID przed expire_all() -- po nim odczyt atrybutu na
        # wygaslym obiekcie to synchroniczny lazy load i rzuca MissingGreenlet.
        ticket_id = ticket.id
        blocker_id = blocker.id
        db_session.expire_all()

        reloaded_ticket = (
            await db_session.execute(select(Ticket).options(selectinload(Ticket.blocked_by)).where(Ticket.id == ticket_id))
        ).scalar_one()
        reloaded_blocker = (
            await db_session.execute(select(Ticket).options(selectinload(Ticket.blocking)).where(Ticket.id == blocker_id))
        ).scalar_one()

        assert [t.id for t in reloaded_ticket.blocked_by] == [blocker_id]
        assert [t.id for t in reloaded_blocker.blocking] == [ticket_id]

    async def test_blocked_by_and_blocking_raise_without_explicit_selectinload(self, db_session):
        """MON-207: relacja przechodzi na lazy="raise" -- dostep bez jawnego
        selectinload() musi eksplodowac natychmiast (InvalidRequestError), nie
        ladowac sie cicho samodzielnie (stare lazy="selectin" na relacji self-
        referential kosztowalo N+1/rekurencje przy kazdym plain select(Ticket))
        ani rzucic MissingGreenlet w polowie renderu. Z jawnym selectinload()
        dziala tak jak wczesniej.

        Identity map: kazde zapytanie tutaj poprzedza expunge_all(), zeby nie
        dostac obiektu, ktoremu relacje zaladowalo wczesniejsze zapytanie w tym
        samym db_session.
        """
        project = await _make_project(db_session, "model-rel-implicit")
        ticket = await _make_ticket(db_session, project, 1)
        blocker = await _make_ticket(db_session, project, 2)

        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        ticket_id = ticket.id
        blocker_id = blocker.id
        db_session.expunge_all()

        reloaded_ticket = (await db_session.execute(select(Ticket).where(Ticket.id == ticket_id))).scalar_one()
        reloaded_blocker = (await db_session.execute(select(Ticket).where(Ticket.id == blocker_id))).scalar_one()

        with pytest.raises(InvalidRequestError):
            _ = reloaded_ticket.blocked_by
        with pytest.raises(InvalidRequestError):
            _ = reloaded_blocker.blocking

        db_session.expunge_all()

        eager_ticket = (await db_session.execute(select(Ticket).options(selectinload(Ticket.blocked_by)).where(Ticket.id == ticket_id))).scalar_one()
        eager_blocker = (await db_session.execute(select(Ticket).options(selectinload(Ticket.blocking)).where(Ticket.id == blocker_id))).scalar_one()

        assert [t.id for t in eager_ticket.blocked_by] == [blocker_id]
        assert [t.id for t in eager_blocker.blocking] == [ticket_id]


class TestTicketDependencyBlockerIndex:
    def test_blocker_id_has_dedicated_index(self):
        """MON-207 (P10): Ticket.blocking filtruje po blocker_id (primaryjoin
        Ticket.id == TicketDependency.blocker_id) -- klucz glowny zlozony
        (ticket_id, blocker_id) indeksuje pare od ticket_id, nie przyspiesza
        wyszukiwania samym blocker_id, wiec kolumna potrzebuje wlasnego indeksu.
        """
        matching = [idx for idx in TicketDependency.__table__.indexes if any(col.name == "blocker_id" for col in idx.columns)]
        assert matching, "Brak indeksu obejmujacego kolumne blocker_id w TicketDependency."
        assert any(idx.name == "ix_ticket_dependencies_blocker_id" for idx in matching), (
            f"Zaden indeks na blocker_id nie nazywa sie 'ix_ticket_dependencies_blocker_id' (znalezione: {[idx.name for idx in matching]})."
        )


# ---------------------------------------------------------------------------
# Serwis ticket_dependencies.set_blockers
# ---------------------------------------------------------------------------


class TestSetBlockersService:
    async def test_happy_path_two_blockers(self, db_session):
        project = await _make_project(db_session, "svc-happy")
        ticket = await _make_ticket(db_session, project, 1)
        blocker1 = await _make_ticket(db_session, project, 2)
        blocker2 = await _make_ticket(db_session, project, 3)

        await ticket_dependencies.set_blockers(db_session, ticket, [blocker1.id, blocker2.id])
        await db_session.flush()

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert {r.blocker_id for r in rows} == {blocker1.id, blocker2.id}

    async def test_second_call_replaces_first(self, db_session):
        project = await _make_project(db_session, "svc-replace")
        ticket = await _make_ticket(db_session, project, 1)
        blocker1 = await _make_ticket(db_session, project, 2)
        blocker2 = await _make_ticket(db_session, project, 3)

        await ticket_dependencies.set_blockers(db_session, ticket, [blocker1.id])
        await db_session.flush()

        await ticket_dependencies.set_blockers(db_session, ticket, [blocker2.id])
        await db_session.flush()

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert {r.blocker_id for r in rows} == {blocker2.id}

    async def test_empty_list_clears_blockers(self, db_session):
        project = await _make_project(db_session, "svc-clear")
        ticket = await _make_ticket(db_session, project, 1)
        blocker = await _make_ticket(db_session, project, 2)

        await ticket_dependencies.set_blockers(db_session, ticket, [blocker.id])
        await db_session.flush()

        await ticket_dependencies.set_blockers(db_session, ticket, [])
        await db_session.flush()

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert rows == []

    async def test_duplicates_in_list_produce_single_dependency(self, db_session):
        project = await _make_project(db_session, "svc-dup")
        ticket = await _make_ticket(db_session, project, 1)
        blocker = await _make_ticket(db_session, project, 2)

        await ticket_dependencies.set_blockers(db_session, ticket, [blocker.id, blocker.id])
        await db_session.flush()

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert len(rows) == 1

    async def test_self_reference_raises(self, db_session):
        project = await _make_project(db_session, "svc-self")
        ticket = await _make_ticket(db_session, project, 1)

        with pytest.raises(ValueError, match="nie moze blokowac samego siebie"):
            await ticket_dependencies.set_blockers(db_session, ticket, [ticket.id])

    async def test_blocker_from_other_project_raises(self, db_session):
        project_a = await _make_project(db_session, "svc-proja")
        project_b = await _make_project(db_session, "svc-projb")
        ticket = await _make_ticket(db_session, project_a, 1)
        other_project_ticket = await _make_ticket(db_session, project_b, 1)

        with pytest.raises(ValueError, match="nie nalezy do tego projektu"):
            await ticket_dependencies.set_blockers(db_session, ticket, [other_project_ticket.id])

    async def test_nonexistent_blocker_raises_same_message_as_wrong_project(self, db_session):
        """Nieistniejacy UUID daje ten sam komunikat co bloker z innego projektu -- bez wycieku istnienia."""
        project = await _make_project(db_session, "svc-missing")
        ticket = await _make_ticket(db_session, project, 1)

        with pytest.raises(ValueError, match="nie nalezy do tego projektu"):
            await ticket_dependencies.set_blockers(db_session, ticket, [uuid.uuid4()])

    async def test_direct_cycle_raises_with_key_path(self, db_session):
        """A juz blokuje B (B zalezy od A) -- proba ustawienia B jako blokera A
        tworzy cykl bezposredni A -> B -> A. Komunikat uzywa kluczy ticketow
        (PROJECT-N), nie surowych UUID -- czlowiek/agent czytajacy blad ma
        zidentyfikowac tickety bez dodatkowego zapytania."""
        project = await _make_project(db_session, "svc-cycle")
        ticket_a = await _make_ticket(db_session, project, 1)
        ticket_b = await _make_ticket(db_session, project, 2)

        db_session.add(TicketDependency(ticket_id=ticket_b.id, blocker_id=ticket_a.id))
        await db_session.flush()

        key_a = f"{project.code}-{ticket_a.number}"
        key_b = f"{project.code}-{ticket_b.number}"
        with pytest.raises(ValueError, match=re.escape(f"{key_a} -> {key_b} -> {key_a}")):
            await ticket_dependencies.set_blockers(db_session, ticket_a, [ticket_b.id])

    async def test_transitive_cycle_of_three_raises_with_full_key_path(self, db_session):
        """A zalezy od B, B zalezy od C (lancuch C -> B -> A). Proba ustawienia
        A jako blokera C zamyka cykl tranzytywny: C zalezy od A (proponowane),
        A zalezy od B (istniejace), B zalezy od C (istniejace) -> z powrotem do C.
        Komunikat wymienia PELNA sciezke kluczy zaczynajac od ticketu edytowanego
        (C), nie tylko dwa skrajne elementy -- inaczej agent nie wie, ktora
        posrednia relacja zamyka petle."""
        project = await _make_project(db_session, "svc-cycle3")
        ticket_a = await _make_ticket(db_session, project, 1)
        ticket_b = await _make_ticket(db_session, project, 2)
        ticket_c = await _make_ticket(db_session, project, 3)

        db_session.add(TicketDependency(ticket_id=ticket_a.id, blocker_id=ticket_b.id))  # A zalezy od B
        db_session.add(TicketDependency(ticket_id=ticket_b.id, blocker_id=ticket_c.id))  # B zalezy od C
        await db_session.flush()

        key_a = f"{project.code}-{ticket_a.number}"
        key_b = f"{project.code}-{ticket_b.number}"
        key_c = f"{project.code}-{ticket_c.number}"
        expected_path = f"{key_c} -> {key_a} -> {key_b} -> {key_c}"
        with pytest.raises(ValueError, match=re.escape(expected_path)):
            await ticket_dependencies.set_blockers(db_session, ticket_c, [ticket_a.id])

    async def test_acyclic_diamond_graph_does_not_false_positive(self, db_session):
        """DAG bez petli (A zalezy od B i C, B zalezy od C -- C jest wspolnym
        "przodkiem" osiagalnym dwiema sciezkami) nie moze byc falszywie zgloszony
        jako cykl -- detekcja cykli tranzytywnych nie moze mylic "wielokrotnie
        osiagalny" z "cykliczny"."""
        project = await _make_project(db_session, "svc-dag")
        ticket_a = await _make_ticket(db_session, project, 1)
        ticket_b = await _make_ticket(db_session, project, 2)
        ticket_c = await _make_ticket(db_session, project, 3)

        await ticket_dependencies.set_blockers(db_session, ticket_b, [ticket_c.id])
        await db_session.flush()

        await ticket_dependencies.set_blockers(db_session, ticket_a, [ticket_b.id, ticket_c.id])
        await db_session.flush()

        rows = await _dependency_rows_for(db_session, ticket_a.id)
        assert {r.blocker_id for r in rows} == {ticket_b.id, ticket_c.id}

    async def test_uses_advisory_xact_lock(self, db_session, engine):
        """Detekcja cykli lezy w wyscigu: dwa rownolegle set_blockers na tym samym
        grafie moga obie przejsc walidacje przed zapisem drugiej. pg_advisory_xact_lock
        serializuje sekcje krytyczna w ramach transakcji -- test przechwytuje SQL
        przez event listener na silniku, zamiast zgadywac po efekcie ubocznym."""
        project = await _make_project(db_session, "svc-advisory")
        ticket = await _make_ticket(db_session, project, 1)
        blocker = await _make_ticket(db_session, project, 2)

        statements: list[tuple[str, object]] = []

        def _capture(conn, cursor, statement, parameters, context, executemany):
            statements.append((statement, parameters))

        event.listen(engine.sync_engine, "before_cursor_execute", _capture)
        try:
            await ticket_dependencies.set_blockers(db_session, ticket, [blocker.id])
        finally:
            event.remove(engine.sync_engine, "before_cursor_execute", _capture)

        lock_calls = [(stmt, params) for stmt, params in statements if "pg_advisory_xact_lock" in stmt]
        assert lock_calls, "Zadne zapytanie set_blockers nie zawiera pg_advisory_xact_lock -- brak serializacji sekcji krytycznej."

        # Review krytyka: lock musi serializowac po CALYM grafie zaleznosci projektu,
        # nie po pojedynczym tickecie -- inaczej dwa rownolegle set_blockers na
        # roznych tickeach tego samego projektu wciaz moga oba przejsc walidacje
        # przed zapisem drugiego. Mutant "lock na ticket.id zamiast project.id"
        # ma zabic ta asercje.
        _, lock_params = lock_calls[0]
        assert str(project.id) in str(lock_params), (
            "Parametr pg_advisory_xact_lock nie pochodzi z project_id -- mutant 'lock na ticket.id' przetrwalby."
        )
        assert str(ticket.id) not in str(lock_params)

    async def test_validation_failure_is_atomic_nothing_added_or_changed(self, db_session):
        """Blad walidacji na jednym elemencie listy nie zmienia istniejacego stanu blokerow."""
        project = await _make_project(db_session, "svc-atomic")
        ticket = await _make_ticket(db_session, project, 1)
        existing_blocker = await _make_ticket(db_session, project, 2)
        new_valid_blocker = await _make_ticket(db_session, project, 3)

        await ticket_dependencies.set_blockers(db_session, ticket, [existing_blocker.id])
        await db_session.flush()

        with pytest.raises(ValueError):
            await ticket_dependencies.set_blockers(db_session, ticket, [new_valid_blocker.id, uuid.uuid4()])

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert {r.blocker_id for r in rows} == {existing_blocker.id}


# ---------------------------------------------------------------------------
# MCP create_ticket
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateTicketBlockedByIds:
    async def test_create_with_blocked_by_ids_is_committed(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        blocker = await _make_ticket(db_session, mcp_project, 1, title="Blocker")

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_ticket(ctx, mcp_project.slug, "Blocked ticket", blocked_by_ids=[str(blocker.id)])

        rows = await _dependency_rows_for(db_session, uuid.UUID(result["id"]))
        assert len(rows) == 1
        assert rows[0].blocker_id == blocker.id

    async def test_create_invalid_blocker_uuid_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy UUID blokera"),
        ):
            await create_ticket(ctx, mcp_project.slug, "Bad blocker", blocked_by_ids=["not-a-uuid"])

    async def test_create_blocker_from_other_project_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        other_project = await _make_project(db_session, "create-other")
        other_ticket = await _make_ticket(db_session, other_project, 1)

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="nie nalezy do tego projektu"),
        ):
            await create_ticket(ctx, mcp_project.slug, "Cross-project blocker", blocked_by_ids=[str(other_ticket.id)])


# ---------------------------------------------------------------------------
# MCP update_ticket
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUpdateTicketBlockedByIds:
    async def test_update_sets_blockers(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = await _make_ticket(db_session, mcp_project, 1, title="Main")
        blocker = await _make_ticket(db_session, mcp_project, 2, title="Blocker")

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), blocked_by_ids=[str(blocker.id)])

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert len(rows) == 1
        assert rows[0].blocker_id == blocker.id

    async def test_update_empty_list_clears_blockers(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = await _make_ticket(db_session, mcp_project, 1, title="Main")
        blocker = await _make_ticket(db_session, mcp_project, 2, title="Blocker")
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), blocked_by_ids=[])

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert rows == []

    async def test_update_without_blocked_by_ids_keeps_existing(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = await _make_ticket(db_session, mcp_project, 1, title="Main")
        blocker = await _make_ticket(db_session, mcp_project, 2, title="Blocker")
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), title="Renamed")

        rows = await _dependency_rows_for(db_session, ticket.id)
        assert len(rows) == 1
        assert rows[0].blocker_id == blocker.id

    async def test_update_invalid_blocker_uuid_raises(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = await _make_ticket(db_session, mcp_project, 1, title="Main")

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy UUID blokera"),
        ):
            await update_ticket(ctx, mcp_project.slug, str(ticket.id), blocked_by_ids=["not-a-uuid"])


# ---------------------------------------------------------------------------
# MCP get_ticket -- sekcja "## Blokery (relacja)" (MON-207: dawniej "## Zaleznosci",
# nazwa zderzala sie z reczna sekcja "## Zaleznosci" w opisie ticketu pisana przez
# ticket-create - dwie rozne rzeczy o tej samej nazwie).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetTicketDependenciesSection:
    async def test_shows_section_with_blocker_key_and_status(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        blocker = await _make_ticket(db_session, mcp_project, 1, title="Blocker", status="in_progress")
        ticket = await _make_ticket(db_session, mcp_project, 2, title="Blocked")
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_ticket(ctx, mcp_project.slug, str(ticket.id))

        assert any(line.startswith("## Blokery (relacja)") for line in result.splitlines())
        assert "## Zaleznosci" not in result
        assert f"{mcp_project.code}-{blocker.number}" in result
        assert "in_progress" in result

    async def test_no_dependencies_section_absent(self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify):
        ticket = await _make_ticket(db_session, mcp_project, 1, title="Lonely")

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_ticket(ctx, mcp_project.slug, str(ticket.id))

        assert "## Blokery (relacja)" not in result

    async def test_description_own_zaleznosci_section_is_not_the_relation_header(
        self, db_session, mcp_user, mcp_project, mcp_member, mock_factory, mock_verify
    ):
        """Opis ticketu (proza autora, np. z szablonu ticket-create) moze miec
        wlasna sekcje '## Zaleznosci' -- to NIE jest relacja strukturalna i nie
        moze byc mylona z naglowkiem generowanym przez serwer, gdy sa faktyczne
        blokery (teraz '## Blokery (relacja)')."""
        blocker = await _make_ticket(db_session, mcp_project, 1, title="Blocker")
        ticket = await _make_ticket(
            db_session,
            mcp_project,
            2,
            title="Blocked",
            description="Opis zadania.\n\n## Zaleznosci\n- proza autora, nie relacja",
        )
        db_session.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker.id))
        await db_session.flush()

        ctx = _make_ctx()
        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_ticket(ctx, mcp_project.slug, str(ticket.id))

        assert any(line.startswith("## Blokery (relacja)") for line in result.splitlines())
