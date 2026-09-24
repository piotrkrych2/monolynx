"""Serwis zaleznosci (blocker) miedzy ticketami -- walidacja i replace-all zapis,
plus backfill starych, prozaicznych deklaracji zaleznosci sprzed MON-198 (MON-207 P2)."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.models.project import Project
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_dependency import TicketDependency

# Klucz ticketu: kod projektu (litera + 1-9 alfanumerykow, jak CODE_PATTERN w
# dashboard/projects.py: ^[A-Z][A-Z0-9]{1,9}$) + '-' + numer, np. MON-5, PIM-123, T006A-1.
# \b na obu koncach: bez niego "T006A-1" lapalo tylko "A-1" (cyfry przed "A" przerywaly
# stara klase [A-Za-z], regex zaczynal dopasowanie od ostatniej litery przed "-").
_KEY_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9]{1,9}-\d+\b")
_DESCRIPTION_HEADING_RE = re.compile(r"^## (?:Zaleznosci|Zależności)[ \t]*$", re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^##[ \t]", re.MULTILINE)
_COMMENT_MARKER_RE = re.compile(r"^\*\*(?:Zaleznosci|Zależności):\*\*")


def _dedupe_preserve_order(keys: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for key in keys:
        if key not in seen:
            seen.add(key)
            result.append(key)
    return result


def extract_description_dependency_keys(description: str | None) -> list[str]:
    """Wyciaga klucze ticketow (np. MON-5) z sekcji '## Zaleznosci'/'## Zależności'
    w opisie ticketu, az do nastepnego naglowka '## ' albo konca tekstu. Klucze poza
    ta sekcja (wzmianki w prozie) sa ignorowane. Zwraca unikalne klucze w kolejnosci
    wystapienia, uppercase."""
    if not description:
        return []

    heading_match = _DESCRIPTION_HEADING_RE.search(description)
    if heading_match is None:
        return []

    section_start = heading_match.end()
    remainder = description[section_start:]
    next_heading_match = _NEXT_HEADING_RE.search(remainder)
    section_text = remainder[: next_heading_match.start()] if next_heading_match else remainder

    keys = [k.upper() for k in _KEY_RE.findall(section_text)]
    return _dedupe_preserve_order(keys)


def extract_comment_dependency_keys(content: str) -> list[str]:
    """Wyciaga klucze ticketow z komentarza, ale TYLKO gdy tresc (po strip) zaczyna sie
    od markera '**Zaleznosci:**'/'**Zależności:**' -- wzmianka w srodku tekstu nie liczy
    sie jako deklaracja zaleznosci."""
    stripped = content.strip()
    marker_match = _COMMENT_MARKER_RE.match(stripped)
    if marker_match is None:
        return []

    rest = stripped[marker_match.end() :]
    keys = [k.upper() for k in _KEY_RE.findall(rest)]
    return _dedupe_preserve_order(keys)


@dataclass
class BackfillProposal:
    ticket_key: str
    blocker_keys: list[str] = field(default_factory=list)
    unknown_keys: list[str] = field(default_factory=list)
    error: str | None = None
    applied: bool = False


@dataclass
class BackfillReport:
    proposals: list[BackfillProposal] = field(default_factory=list)


async def _project_code(db: AsyncSession, project_id: uuid.UUID) -> str:
    result = await db.execute(select(Project.code).where(Project.id == project_id))
    return result.scalar_one()


async def _find_cycle_path(
    db: AsyncSession,
    start_id: uuid.UUID,
    target_id: uuid.UUID,
) -> list[uuid.UUID] | None:
    """Rekurencyjne CTE po ticket_dependencies: czy od `start_id` da sie dojsc do
    `target_id` idac istniejacymi krawedziami ticket_id -> blocker_id (X zalezy od Y)?
    Zwraca pelna sciezke UUID (od start_id do target_id) albo None gdy brak polaczenia --
    dziala dla cyklu dowolnej dlugosci, nie tylko bezposredniego."""
    # CAST(:start_id AS uuid), nie ":start_id::uuid" -- SQLAlchemy text() parsuje bindy
    # regexem na ":nazwa", ktory nie rozumie postgresowego "::"; ":start_id::uuid" byl
    # czytany jako bind "start_i" + literal "d::uuid", co rzucalo "A value is required
    # for bind parameter 'start_i'" przy kazdym set_blockers z niepusta lista (MON-207 review r3).
    query = text(
        """
        WITH RECURSIVE chain(node, path) AS (
            SELECT blocker_id, ARRAY[CAST(:start_id AS uuid), blocker_id]
            FROM ticket_dependencies
            WHERE ticket_id = :start_id
            UNION ALL
            SELECT td.blocker_id, chain.path || td.blocker_id
            FROM ticket_dependencies td
            JOIN chain ON td.ticket_id = chain.node
            WHERE NOT td.blocker_id = ANY(chain.path)
        )
        SELECT path FROM chain WHERE node = :target_id LIMIT 1
        """
    )
    result = await db.execute(query, {"start_id": start_id, "target_id": target_id})
    row = result.first()
    return list(row[0]) if row else None


async def _cycle_error_message(db: AsyncSession, ticket: Ticket, unique_blocker_ids: set[uuid.UUID]) -> str | None:
    """Sprawdza, czy dodanie ktoregokolwiek z `unique_blocker_ids` jako blokera `ticket`
    utworzyloby cykl wg BIEZACEGO stanu bazy (przed jakimkolwiek zapisem) -- bez efektow
    ubocznych, zeby ta sama logika sluzyla i set_blockers (walidacja przed zapisem), i
    backfill_dependencies w trybie dry-run (MON-207 review r3: dry-run milczal o cyklu,
    ktory --apply i tak by odrzucil)."""
    for candidate_id in sorted(unique_blocker_ids, key=str):
        cycle_path = await _find_cycle_path(db, candidate_id, ticket.id)
        if cycle_path is not None:
            full_path = [ticket.id, *cycle_path]
            numbers_result = await db.execute(select(Ticket.id, Ticket.number).where(Ticket.id.in_(set(full_path))))
            number_by_id = {row.id: row.number for row in numbers_result}
            project_code = await _project_code(db, ticket.project_id)
            key_path = " -> ".join(f"{project_code}-{number_by_id[node_id]}" for node_id in full_path)
            return f"Cykl zaleznosci: {key_path}"
    return None


async def set_blockers(db: AsyncSession, ticket: Ticket, blocker_ids: list[uuid.UUID]) -> None:
    """Zastepuje WSZYSTKIE blokery ticketu podana lista (replace-all; [] czysci).

    Duplikaty w liscie sa ignorowane. Kazdy element jest walidowany PRZED jakimkolwiek
    zapisem/delete -- przy bledzie ValueError stan bazy pozostaje bez zmian (atomowo
    w ramach otwartej transakcji, bo walidacja nie wykonuje zadnego DELETE/INSERT przed
    swoim zakonczeniem). Nie commituje -- zgodnie z .claude/rules/db-commit.md
    commit robi wywolujacy (handler MCP / dashboard).
    """
    # pg_advisory_xact_lock serializuje sekcje krytyczna (walidacja cyklu + zapis) w ramach
    # projektu -- bez tego dwa rownolegle wywolania na tym samym grafie moga obie przejsc
    # walidacje przed zapisem drugiej i razem utworzyc cykl. hashtextextended(text, seed)
    # daje bigint z przestrzeni niezaleznej od innych stalych ID lockow w projekcie (np.
    # MONITOR_ADVISORY_LOCK_ID). Zwalniany automatycznie na koniec transakcji wywolujacego.
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtextextended(:project_id, 0))"),
        {"project_id": str(ticket.project_id)},
    )

    unique_blocker_ids = set(blocker_ids)

    if ticket.id in unique_blocker_ids:
        project_code = await _project_code(db, ticket.project_id)
        raise ValueError(f"Ticket {project_code}-{ticket.number} nie moze blokowac samego siebie")

    if unique_blocker_ids:
        result = await db.execute(
            select(Ticket.id).where(
                Ticket.id.in_(unique_blocker_ids),
                Ticket.project_id == ticket.project_id,
            )
        )
        valid_ids = set(result.scalars().all())
        missing_ids = unique_blocker_ids - valid_ids
        if missing_ids:
            # Bez UUID w komunikacie. Klucz w komunikacie tylko gdy ticket naprawde istnieje
            # (w innym projekcie) -- to celowa pomoc, nie wyciek istnienia. Gdy podany ID nie
            # istnieje nigdzie, komunikat jest ogolny i identyczny dla kazdego takiego ID.
            first_missing_id = min(missing_ids, key=str)
            foreign_result = await db.execute(
                select(Ticket.number, Project.code).join(Project, Project.id == Ticket.project_id).where(Ticket.id == first_missing_id)
            )
            foreign_row = foreign_result.first()
            if foreign_row is not None:
                raise ValueError(f"Bloker {foreign_row.code}-{foreign_row.number} nie nalezy do tego projektu")
            raise ValueError("Wskazany bloker nie nalezy do tego projektu lub nie istnieje")

        cycle_error = await _cycle_error_message(db, ticket, unique_blocker_ids)
        if cycle_error is not None:
            raise ValueError(cycle_error)

    await db.execute(delete(TicketDependency).where(TicketDependency.ticket_id == ticket.id))
    for blocker_id in unique_blocker_ids:
        db.add(TicketDependency(ticket_id=ticket.id, blocker_id=blocker_id))


async def backfill_dependencies(
    db: AsyncSession,
    *,
    apply: bool,
    project_id: uuid.UUID | None = None,
) -> BackfillReport:
    """Skanuje opisy i komentarze ticketow pod katem starych, prozaicznych deklaracji
    zaleznosci (sprzed MON-198, zapisanych jako tekst zamiast TicketDependency) i albo
    tylko raportuje co by dodal (apply=False), albo zapisuje przez set_blockers i commituje
    (apply=True).

    Ticket w statusie 'done' jest calkowicie pomijany na etapie zbierania kandydatow --
    nie trafia do report.proposals w ogole (nie jest to odrzucona propozycja). Ticket,
    dla ktorego dodanie wykrytych blokerow utworzyloby cykl, dostaje propozycje z `error`
    ustawionym i `applied=False` -- pomijany, ale reszta propozycji z tego samego przebiegu
    jest zapisywana (blad jednego ticketu nie blokuje innych, bo set_blockers waliduje przed
    zapisem -- nie ma wiec realnego bledu SQL w trakcie transakcji, ktory wymagalby osobnego
    savepointu).

    project_id: opcjonalnie ogranicza skan do jednego projektu.
    """
    stmt = (
        select(Ticket)
        .join(Project, Project.id == Ticket.project_id)
        .options(
            selectinload(Ticket.project),
            selectinload(Ticket.comments),
            selectinload(Ticket.blocked_by),
        )
        .where(Project.is_active.is_(True))
    )
    if project_id is not None:
        stmt = stmt.where(Ticket.project_id == project_id)
    tickets = list((await db.execute(stmt)).scalars().all())

    # Mapa klucz -> ticket, per projekt -- budowana ze WSZYSTKICH ticketow (w tym 'done'),
    # bo ticket done moze byc legalnym blokerem innego ticketu; wykluczamy 'done' dopiero
    # ponizej, na etapie iteracji ticketow-kandydatow do wlasnej propozycji.
    tickets_by_project: dict[uuid.UUID, dict[str, Ticket]] = {}
    for t in tickets:
        tickets_by_project.setdefault(t.project_id, {})[t.key.upper()] = t

    proposals: list[BackfillProposal] = []
    for ticket in tickets:
        if ticket.status == "done":
            continue  # ticket done w ogole nie trafia do proposals (rekoncyliacja MON-207)

        extracted_keys = extract_description_dependency_keys(ticket.description)
        for comment in ticket.comments:
            extracted_keys.extend(extract_comment_dependency_keys(comment.content))
        extracted_keys = _dedupe_preserve_order(extracted_keys)
        if not extracted_keys:
            continue

        project_keys = tickets_by_project.get(ticket.project_id, {})
        existing_blocker_ids = {b.id for b in ticket.blocked_by}

        new_blocker_ids: list[uuid.UUID] = []
        blocker_keys: list[str] = []
        unknown_keys: list[str] = []
        for key in extracted_keys:
            candidate = project_keys.get(key)
            if candidate is None:
                unknown_keys.append(key)
                continue
            if candidate.id == ticket.id or candidate.id in existing_blocker_ids or candidate.id in new_blocker_ids:
                continue
            new_blocker_ids.append(candidate.id)
            blocker_keys.append(key)

        if not new_blocker_ids and not unknown_keys:
            continue  # wszystkie wykryte klucze to juz istniejace blokery -- nic do zaproponowania

        error: str | None = None
        applied = False

        if new_blocker_ids:
            union_ids = existing_blocker_ids | set(new_blocker_ids)
            # Cykl sprawdzany takze w dry-run (apply=False), nie tylko przed zapisem w
            # set_blockers -- inaczej dry-run pokazywal propozycje jako OK, a --apply na
            # tych samych danych i tak by ja odrzucil (MON-207 review r3).
            error = await _cycle_error_message(db, ticket, union_ids)
            if error is None and apply:
                try:
                    await set_blockers(db, ticket, list(union_ids))
                except ValueError as e:
                    error = str(e)
                else:
                    applied = True

        proposals.append(
            BackfillProposal(
                ticket_key=ticket.key,
                blocker_keys=blocker_keys,
                unknown_keys=unknown_keys,
                error=error,
                applied=applied,
            )
        )

    if apply:
        await db.commit()

    return BackfillReport(proposals=proposals)
