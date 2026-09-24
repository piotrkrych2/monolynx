"""Testy backfillu zaleznosci (blocker) z istniejacych opisow/komentarzy ticketow (MON-207, P2).

Migracja z MON-198 wprowadzila `blocked_by_ids` jako jedyny oficjalny sposob
zapisu zaleznosci, ale starsze tickety maja te informacje wpisane recznie jako
prozaiczny tekst w opisie (sekcja `## Zaleznosci`) albo w komentarzu
(`**Zaleznosci:** KLUCZ, KLUCZ`). Ten plik pokrywa ekstrakcje kluczy z obu
zrodel oraz `services.ticket_dependencies.backfill_dependencies` (dry-run vs
apply, unia z istniejacymi blokerami, nieznane klucze, cykl blokujacy zapis
tylko dla tego ticketu). Tickety `done` w ogole NIE trafiaja do
`report.proposals` (calkowite pominiecie na etapie zbierania kandydatow, nie
odrzucona propozycja) -- decyzja TM z rekoncyliacji tego ticketu.
"""

import secrets
import uuid
from unittest import mock

import pytest
from sqlalchemy import select

from monolynx import cli
from monolynx.models import Project, Ticket, TicketComment, TicketDependency, User
from monolynx.services.auth import hash_password
from monolynx.services.ticket_dependencies import (
    backfill_dependencies,
    extract_comment_dependency_keys,
    extract_description_dependency_keys,
    set_blockers,
)

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers (wzorowane na tests/unit/test_ticket_dependencies.py)
# ---------------------------------------------------------------------------


async def _make_project(db, suffix: str) -> Project:
    unique = uuid.uuid4().hex
    project = Project(
        name=f"TDB Project {suffix}",
        code=f"T{unique[:4]}".upper(),
        slug=f"tdb-{suffix}-{unique[:6]}",
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


async def _make_user(db, suffix: str) -> User:
    user = User(
        email=f"tdb-{suffix}-{uuid.uuid4().hex[:8]}@test.com",
        password_hash=hash_password("testpass"),
    )
    db.add(user)
    await db.flush()
    return user


async def _make_comment(db, ticket: Ticket, user: User, content: str) -> TicketComment:
    comment = TicketComment(ticket_id=ticket.id, user_id=user.id, content=content)
    db.add(comment)
    await db.flush()
    return comment


async def _dependency_pairs(db, ticket_id):
    result = await db.execute(select(TicketDependency.ticket_id, TicketDependency.blocker_id).where(TicketDependency.ticket_id == ticket_id))
    return set(result.all())


# ---------------------------------------------------------------------------
# extract_description_dependency_keys
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractDescriptionDependencyKeys:
    def test_extracts_keys_from_ascii_section_heading(self):
        description = "## Zaleznosci\nMON-5\nMON-3\n"
        assert extract_description_dependency_keys(description) == ["MON-5", "MON-3"]

    def test_extracts_keys_from_diacritics_section_heading(self):
        description = "## Zależności\nMON-7\n"
        assert extract_description_dependency_keys(description) == ["MON-7"]

    def test_stops_at_next_heading(self):
        description = "## Zaleznosci\nMON-1\n## Inne\nMON-9 nie powinien trafic do wyniku\n"
        assert extract_description_dependency_keys(description) == ["MON-1"]

    def test_reads_until_eof_when_no_next_heading(self):
        description = "## Zaleznosci\nBlokowany przez MON-11 oraz MON-12.\n"
        assert extract_description_dependency_keys(description) == ["MON-11", "MON-12"]

    def test_dedupes_preserving_first_occurrence_order(self):
        description = "## Zaleznosci\nMON-5\nMON-3\nMON-5\n"
        assert extract_description_dependency_keys(description) == ["MON-5", "MON-3"]

    def test_uppercases_lowercase_keys(self):
        description = "## Zaleznosci\nmon-5\n"
        assert extract_description_dependency_keys(description) == ["MON-5"]

    def test_no_section_returns_empty_list(self):
        description = "Zwykly opis ticketu, wspomina MON-5 bez sekcji naglowka."
        assert extract_description_dependency_keys(description) == []

    def test_keys_before_section_heading_are_ignored(self):
        description = "Blokowane przez MON-77 (wzmianka poza sekcja).\n\n## Zaleznosci\nMON-5\n"
        assert extract_description_dependency_keys(description) == ["MON-5"]

    def test_none_description_returns_empty_list(self):
        assert extract_description_dependency_keys(None) == []

    def test_extracts_key_with_digits_in_project_code(self):
        """Regresja MON-207 (CI 2878254502): kod projektu z cyfra (np. T006A) lapal
        tylko koncowke "A-1", bo stara klasa [A-Za-z] przerywala dopasowanie na
        pierwszej cyfrze. `_KEY_RE` musi zwrocic caly klucz "T006A-1"."""
        description = "## Zaleznosci\nT006A-1\n"
        assert extract_description_dependency_keys(description) == ["T006A-1"]

    def test_leading_word_boundary_rejects_digit_glued_before_key(self):
        """Regresja review krytyka (runda 4): "XMON-5" NIE zabija zadnego mutanta,
        bo dopasowanie leftmost-first zaczyna sie od "X" niezaleznie od `\\b`. "1MON-5"
        rozroznia: z wiodacym `\\b` -> [] (cyfra i litera to oba znaki \\w, brak granicy
        miedzy "1" a "M"), bez wiodacego `\\b` -> ["MON-5"] (weryfikacja: python3 -c
        z re.findall na obu wariantach wzorca, patrz raport)."""
        description = "## Zaleznosci\n1MON-5\n"
        assert extract_description_dependency_keys(description) == []

    def test_trailing_word_boundary_rejects_letter_glued_after_key(self):
        """Analogicznie dla `\\b` na koncu wzorca: "MON-5a" z pelnym regexem daje [],
        bez koncowego `\\b` dalby ["MON-5"] (litera "a" po cyfrze to oba \\w, brak
        granicy)."""
        description = "## Zaleznosci\nMON-5a\n"
        assert extract_description_dependency_keys(description) == []


# ---------------------------------------------------------------------------
# extract_comment_dependency_keys
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractCommentDependencyKeys:
    def test_extracts_keys_with_ascii_marker(self):
        assert extract_comment_dependency_keys("**Zaleznosci:** MON-1, MON-2") == ["MON-1", "MON-2"]

    def test_extracts_keys_with_diacritics_marker(self):
        assert extract_comment_dependency_keys("**Zależności:** MON-3") == ["MON-3"]

    def test_strips_leading_whitespace_before_checking_marker(self):
        assert extract_comment_dependency_keys("   **Zaleznosci:** MON-4") == ["MON-4"]

    def test_marker_not_at_start_is_ignored(self):
        """Marker musi otwierac komentarz -- wzmianka w srodku tekstu nie liczy sie jako deklaracja."""
        content = "Widze ze **Zaleznosci:** MON-5 sa tu problematyczne."
        assert extract_comment_dependency_keys(content) == []

    def test_marker_without_keys_returns_empty_list(self):
        assert extract_comment_dependency_keys("**Zaleznosci:** brak zmian") == []

    def test_comment_without_marker_returns_empty_list(self):
        assert extract_comment_dependency_keys("Zwykly komentarz bez deklaracji.") == []

    def test_extracts_key_with_digits_in_project_code(self):
        """Regresja MON-207 (CI 2878254502), analogicznie do wersji opisowej."""
        assert extract_comment_dependency_keys("**Zaleznosci:** T006A-1") == ["T006A-1"]

    def test_leading_word_boundary_rejects_digit_glued_before_key(self):
        """Patrz docstring analogicznego testu w TestExtractDescriptionDependencyKeys."""
        assert extract_comment_dependency_keys("**Zaleznosci:** 1MON-5") == []

    def test_trailing_word_boundary_rejects_letter_glued_after_key(self):
        assert extract_comment_dependency_keys("**Zaleznosci:** MON-5a") == []


# ---------------------------------------------------------------------------
# backfill_dependencies -- dry-run
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBackfillDependenciesDryRun:
    async def test_dry_run_does_not_write_any_dependency(self, db_session):
        project = await _make_project(db_session, "dryrun")
        blocker = await _make_ticket(db_session, project, 1, title="Blocker")
        dependent = await _make_ticket(
            db_session,
            project,
            2,
            title="Dependent",
            description=f"## Zaleznosci\n{blocker.key}\n",
        )

        report = await backfill_dependencies(db_session, apply=False)

        proposal = next(p for p in report.proposals if p.ticket_key == dependent.key)
        assert proposal.blocker_keys == [blocker.key]
        assert proposal.applied is False
        assert await _dependency_pairs(db_session, dependent.id) == set()

    async def test_dry_run_detects_cycle_and_reports_error(self, db_session):
        """Info z review krytyka (MON-207 runda 3): backend dodaje wykrywanie cykli
        rowniez w dry-run (propozycja z error takze przy apply=False) - dry-run ma
        pokazac uzytkownikowi PRAWDZIWY skutek apply, wiec cykl musi byc widoczny
        zanim ktokolwiek uruchomi --apply."""
        project = await _make_project(db_session, "dryruncycle")
        ticket_a = await _make_ticket(db_session, project, 1, title="A")
        ticket_b = await _make_ticket(
            db_session,
            project,
            2,
            title="B",
            description=f"## Zaleznosci\n{ticket_a.key}\n",
        )
        # A jest juz blokowany przez B -> propozycja backfillu "B blokowany przez A"
        # utworzylaby bezposredni cykl A<->B.
        await set_blockers(db_session, ticket_a, [ticket_b.id])
        await db_session.flush()

        report = await backfill_dependencies(db_session, apply=False)

        proposal = next(p for p in report.proposals if p.ticket_key == ticket_b.key)
        assert proposal.applied is False
        assert proposal.error is not None
        assert await _dependency_pairs(db_session, ticket_b.id) == set()


# ---------------------------------------------------------------------------
# backfill_dependencies -- apply
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestBackfillDependenciesApply:
    async def test_apply_writes_dependency_from_description(self, db_session):
        project = await _make_project(db_session, "applydesc")
        blocker = await _make_ticket(db_session, project, 1, title="Blocker")
        dependent = await _make_ticket(
            db_session,
            project,
            2,
            title="Dependent",
            description=f"## Zaleznosci\n{blocker.key}\n",
        )

        report = await backfill_dependencies(db_session, apply=True)

        proposal = next(p for p in report.proposals if p.ticket_key == dependent.key)
        assert proposal.applied is True
        assert await _dependency_pairs(db_session, dependent.id) == {(dependent.id, blocker.id)}

    async def test_apply_writes_dependency_from_comment(self, db_session):
        project = await _make_project(db_session, "applycomment")
        blocker = await _make_ticket(db_session, project, 1, title="Blocker")
        dependent = await _make_ticket(db_session, project, 2, title="Dependent")
        user = await _make_user(db_session, "commentauthor")
        await _make_comment(db_session, dependent, user, f"**Zaleznosci:** {blocker.key}")

        report = await backfill_dependencies(db_session, apply=True)

        proposal = next(p for p in report.proposals if p.ticket_key == dependent.key)
        assert proposal.applied is True
        assert await _dependency_pairs(db_session, dependent.id) == {(dependent.id, blocker.id)}

    async def test_apply_unions_with_existing_manual_blocker(self, db_session):
        project = await _make_project(db_session, "union")
        existing_blocker = await _make_ticket(db_session, project, 1, title="Existing blocker")
        new_blocker = await _make_ticket(db_session, project, 2, title="New blocker")
        dependent = await _make_ticket(
            db_session,
            project,
            3,
            title="Dependent",
            description=f"## Zaleznosci\n{new_blocker.key}\n",
        )
        await set_blockers(db_session, dependent, [existing_blocker.id])
        await db_session.flush()

        await backfill_dependencies(db_session, apply=True)

        pairs = await _dependency_pairs(db_session, dependent.id)
        assert pairs == {
            (dependent.id, existing_blocker.id),
            (dependent.id, new_blocker.id),
        }

    async def test_unknown_key_recorded_and_not_written(self, db_session):
        project = await _make_project(db_session, "unknown")
        dependent = await _make_ticket(
            db_session,
            project,
            1,
            title="Dependent",
            description="## Zaleznosci\nZZZ-999\n",
        )

        report = await backfill_dependencies(db_session, apply=True)

        proposal = next(p for p in report.proposals if p.ticket_key == dependent.key)
        assert proposal.unknown_keys == ["ZZZ-999"]
        assert await _dependency_pairs(db_session, dependent.id) == set()

    async def test_done_ticket_produces_no_proposal_and_no_write(self, db_session):
        """Decyzja TM (rekoncyliacja MON-207): tickety `done` w ogole NIE trafiaja do
        report.proposals -- to nie jest odrzucona propozycja (applied=False + error),
        tylko calkowite pominiecie na etapie zbierania kandydatow do backfillu."""
        project = await _make_project(db_session, "doneskip")
        blocker = await _make_ticket(db_session, project, 1, title="Blocker")
        done_ticket = await _make_ticket(
            db_session,
            project,
            2,
            title="Done ticket",
            status="done",
            description=f"## Zaleznosci\n{blocker.key}\n",
        )

        report = await backfill_dependencies(db_session, apply=True)

        proposal_keys = {p.ticket_key for p in report.proposals}
        assert done_ticket.key not in proposal_keys
        assert await _dependency_pairs(db_session, done_ticket.id) == set()

    async def test_cycle_error_skips_only_that_ticket_others_still_saved(self, db_session):
        project = await _make_project(db_session, "cycleskip")
        ticket_a = await _make_ticket(db_session, project, 1, title="A")
        ticket_b = await _make_ticket(
            db_session,
            project,
            2,
            title="B",
            description=f"## Zaleznosci\n{ticket_a.key}\n",
        )
        ticket_c = await _make_ticket(db_session, project, 3, title="C")
        ticket_d = await _make_ticket(
            db_session,
            project,
            4,
            title="D",
            description=f"## Zaleznosci\n{ticket_c.key}\n",
        )
        # A jest juz blokowany przez B -> propozycja backfillu "B blokowany przez A"
        # utworzylaby bezposredni cykl A<->B.
        await set_blockers(db_session, ticket_a, [ticket_b.id])
        await db_session.flush()

        report = await backfill_dependencies(db_session, apply=True)

        proposal_b = next(p for p in report.proposals if p.ticket_key == ticket_b.key)
        assert proposal_b.applied is False
        assert proposal_b.error is not None
        assert await _dependency_pairs(db_session, ticket_b.id) == set()

        proposal_d = next(p for p in report.proposals if p.ticket_key == ticket_d.key)
        assert proposal_d.applied is True
        assert await _dependency_pairs(db_session, ticket_d.id) == {(ticket_d.id, ticket_c.id)}

    async def test_project_id_filters_to_single_project(self, db_session):
        project_a = await _make_project(db_session, "filtera")
        project_b = await _make_project(db_session, "filterb")
        blocker_a = await _make_ticket(db_session, project_a, 1, title="Blocker A")
        dependent_a = await _make_ticket(
            db_session,
            project_a,
            2,
            title="Dependent A",
            description=f"## Zaleznosci\n{blocker_a.key}\n",
        )
        blocker_b = await _make_ticket(db_session, project_b, 1, title="Blocker B")
        dependent_b = await _make_ticket(
            db_session,
            project_b,
            2,
            title="Dependent B",
            description=f"## Zaleznosci\n{blocker_b.key}\n",
        )

        report = await backfill_dependencies(db_session, apply=False, project_id=project_a.id)

        proposal_keys = {p.ticket_key for p in report.proposals}
        assert dependent_a.key in proposal_keys
        assert dependent_b.key not in proposal_keys

    async def test_apply_calls_commit(self, db_session):
        """Regresja MON-71/db-commit.md: backfill musi commitowac, nie tylko flush -
        flush wewnatrz tej samej sesji wyglada jak sukces nawet bez commit.

        Uwaga (review krytyka MON-207 runda 3): weryfikacja "widocznosci z
        niezaleznego polaczenia" (jak w test_activity_service.py) tutaj NIE
        odroznia commita od jego braku -- `db_session` (tests/conftest.py:55-58)
        wiaze AsyncSession z polaczeniem, na ktorym juz trwa zewnetrzny
        `connection.begin()`; SQLAlchemy domyslnie dolacza sesje do tej
        transakcji przez SAVEPOINT (`join_transaction_mode="conditional_savepoint"`).
        Bez aktywnego SAVEPOINT ten tryb dziala jak `rollback_only` -- commit nigdy
        nie jest propagowany na zewnetrzna transakcje, wiec niezalezne polaczenie
        nie zobaczy wiersza NAWET po prawdziwym `session.commit()`. Jedyny sposob
        odroznic "commit wywolany" od "flush bez commit" w tym modelu transakcji
        to szpieg na metodzie commit."""
        project = await _make_project(db_session, "commitcheck")
        blocker = await _make_ticket(db_session, project, 1, title="Blocker")
        await _make_ticket(
            db_session,
            project,
            2,
            title="Dependent",
            description=f"## Zaleznosci\n{blocker.key}\n",
        )

        with mock.patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            await backfill_dependencies(db_session, apply=True)

        assert commit_spy.called

    async def test_dry_run_does_not_call_commit(self, db_session):
        project = await _make_project(db_session, "commitcheckdry")
        blocker = await _make_ticket(db_session, project, 1, title="Blocker")
        await _make_ticket(
            db_session,
            project,
            2,
            title="Dependent",
            description=f"## Zaleznosci\n{blocker.key}\n",
        )

        with mock.patch.object(db_session, "commit", wraps=db_session.commit) as commit_spy:
            await backfill_dependencies(db_session, apply=False)

        assert not commit_spy.called


# ---------------------------------------------------------------------------
# CLI registration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBackfillDependenciesCliRegistration:
    def test_backfill_dependencies_registered_in_cli_commands(self):
        assert "backfill-dependencies" in cli.COMMANDS
