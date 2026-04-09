"""Serwis rozliczen -- logika biznesowa modulu Rozliczenia."""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from datetime import UTC, date, datetime
from functools import partial
from typing import TYPE_CHECKING, Final

from fastapi import HTTPException
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.constants import (
    MAX_ATTACHMENTS_PER_SETTLEMENT,
    SETTLEMENT_ALLOWED_EXT,
    SETTLEMENT_ATTACHMENT_MAX_SIZE,
    SETTLEMENT_ATTACHMENT_STATES,
    SETTLEMENT_CATEGORIES,
    SETTLEMENT_STATES,
)
from monolynx.models.project import Project
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_attachment import SettlementAttachment
from monolynx.models.settlement_project import SettlementProject
from monolynx.services import minio_client
from monolynx.services.permissions import check_permission

if TYPE_CHECKING:
    from monolynx.models.ticket import Ticket

logger = logging.getLogger("monolynx.settlements")

ALLOWED_SETTLEMENT_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "draft": frozenset({"sent"}),
    "sent": frozenset({"paid", "draft"}),
    "paid": frozenset({"sent"}),
}


def is_ticket_frozen(ticket: Ticket) -> bool:
    """Czy ticket jest zamrozony -- podpiety do rozliczenia w sent/paid."""
    return any(s.status in ("sent", "paid") and s.is_active for s in (ticket.settlements or []))


async def validate_settlement_ticket_link(
    db: AsyncSession,
    settlement: Settlement,
    ticket: Ticket,
) -> None:
    """Rzuca ValueError jesli link nie jest dozwolony.

    Zasady:
    1. settlement musi byc draft
    2. ticket.project_id musi nalezec do settlement.projects
    """
    if settlement.status != "draft":
        raise ValueError("Mozna powiazac ticket tylko z rozliczeniem w statusie draft")

    settlement_project_ids = {p.id for p in (settlement.projects or [])}
    if ticket.project_id not in settlement_project_ids:
        raise ValueError("Ticket nie nalezy do zadnego projektu powiazanego z tym rozliczeniem")


async def change_settlement_status(
    db: AsyncSession,
    settlement: Settlement,
    user_id: uuid.UUID,
    new_status: str,
) -> Settlement:
    """Zmien status rozliczenia z walidacja przejscia, uprawnien i timestampow.

    Wymaga: settlement z eager-loadowanym `projects` (dla walidacji uprawnien).

    Dozwolone przejscia:
    - draft -> sent  (ustawia sent_at)
    - sent  -> paid  (ustawia paid_at, zachowuje sent_at)
    - sent  -> draft (czyści sent_at i paid_at)
    - paid  -> sent  (czyści paid_at, zachowuje sent_at)
    """
    # Walidacja nowego statusu
    if new_status not in SETTLEMENT_STATES:
        raise ValueError(f"Nieprawidlowy status: {new_status}. Dozwolone: {sorted(SETTLEMENT_STATES)}")

    current = settlement.status

    # Walidacja przejscia
    allowed = ALLOWED_SETTLEMENT_TRANSITIONS.get(current, frozenset())
    if new_status not in allowed:
        raise ValueError(f"Nieprawidlowe przejscie statusu: {current} \u2192 {new_status}. Dozwolone: {sorted(allowed) if allowed else '(brak)'}")

    # Walidacja uprawnien we wszystkich projektach powiazanych z rozliczeniem
    if not settlement.projects:
        raise HTTPException(status_code=403, detail="Rozliczenie nie ma aktywnych projektow")
    for project in settlement.projects:
        if not project.is_active:
            continue
        has_write = await check_permission(db, user_id, project.id, "rozliczenia", "write")
        if not has_write:
            raise HTTPException(
                status_code=403,
                detail=f"Brak uprawnienia rozliczenia:write w projekcie {project.name}",
            )

    # Logika timestampow
    now = datetime.now(UTC)
    if current == "draft" and new_status == "sent":
        settlement.sent_at = now
    elif current == "sent" and new_status == "paid":
        settlement.paid_at = now
        # sent_at zachowane
    elif current == "sent" and new_status == "draft":
        settlement.sent_at = None
        settlement.paid_at = None
    elif current == "paid" and new_status == "sent":
        settlement.paid_at = None
        # sent_at zachowane

    settlement.status = new_status
    await db.commit()
    await db.refresh(settlement)
    return settlement


async def get_next_settlement_number(db: AsyncSession) -> int:
    """Zwraca kolejny globalny numer rozliczenia (MAX(number) + 1).

    UWAGA: Race condition obslugiwany przez UNIQUE constraint na settlements.number.
    Wywolujacy powinien obsluzyc IntegrityError i retry.
    """
    result = await db.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    return int(result.scalar_one()) + 1


async def create_settlement(
    db: AsyncSession,
    user_id: uuid.UUID,
    name: str,
    period_from: date,
    period_to: date,
    project_ids: list[uuid.UUID],
    notes: str | None = None,
) -> Settlement:
    """Utworz nowe rozliczenie.

    Walidacja:
    - name niepuste i <= 200 znakow (rzuca ValueError)
    - period_from <= period_to (ValueError)
    - project_ids niepuste (ValueError)
    - kazdy project_id istnieje i is_active=True (ValueError)
    - user MA rozliczenia:write w KAZDYM z project_ids (HTTPException 403)

    Numeracja: retry-on-IntegrityError max 3x (get_next_settlement_number + insert).
    Tworzy Settlement + wpisy w settlement_projects (M2M).
    """
    # Walidacja name
    name = name.strip()
    if not name:
        raise ValueError("Nazwa rozliczenia nie moze byc pusta")
    if len(name) > 200:
        raise ValueError("Nazwa rozliczenia nie moze przekraczac 200 znakow")

    # Walidacja dat
    if period_from > period_to:
        raise ValueError("Data poczatku okresu nie moze byc pozniejsza niz data konca")

    # Walidacja project_ids
    if not project_ids:
        raise ValueError("Rozliczenie musi byc przypisane do co najmniej jednego projektu")

    # Deduplikacja
    project_ids = list(dict.fromkeys(project_ids))

    # Walidacja projektow - istnienie i is_active
    projects_result = await db.execute(select(Project).where(Project.id.in_(project_ids), Project.is_active.is_(True)))
    found_projects = {p.id for p in projects_result.scalars().all()}
    missing = set(project_ids) - found_projects
    if missing:
        raise ValueError(f"Projekty nie istnieja lub sa nieaktywne: {missing}")

    # Walidacja uprawnien - user musi miec write we wszystkich projektach
    for pid in project_ids:
        has_write = await check_permission(db, user_id, pid, "rozliczenia", "write")
        if not has_write:
            raise HTTPException(
                status_code=403,
                detail=f"Brak uprawnienia rozliczenia:write w projekcie {pid}",
            )

    # Retry loop dla race condition na unikalnym numerze
    settlement: Settlement | None = None
    for attempt in range(3):
        try:
            number = await get_next_settlement_number(db)
            settlement = Settlement(
                number=number,
                name=name,
                period_from=period_from,
                period_to=period_to,
                notes=notes,
                created_by_id=user_id,
            )
            db.add(settlement)
            await db.flush()  # uzyskaj id przed dodaniem M2M

            # Dodaj wpisy M2M
            for pid in project_ids:
                db.add(SettlementProject(settlement_id=settlement.id, project_id=pid))

            await db.commit()
            await db.refresh(settlement)
            break
        except IntegrityError:
            await db.rollback()
            if attempt == 2:
                raise
            continue

    assert settlement is not None
    return settlement


async def update_settlement(
    db: AsyncSession,
    settlement: Settlement,
    user_id: uuid.UUID,
    name: str,
    period_from: date,
    period_to: date,
    project_ids: list[uuid.UUID],
    notes: str | None = None,
) -> Settlement:
    """Edytuj rozliczenie (tylko draft).

    Walidacja:
    - settlement.status == "draft" (inaczej ValueError)
    - name/period/project_ids jak w create_settlement
    - user MA rozliczenia:write w KAZDYM z project_ids (nowych + usuwanych)

    Aktualizuje pola + zastepuje wpisy w settlement_projects (delete old, insert new).
    """
    # Walidacja statusu
    if settlement.status != "draft":
        raise ValueError("Rozliczenie mozna edytowac tylko w statusie draft")

    # Walidacja name
    name = name.strip()
    if not name:
        raise ValueError("Nazwa rozliczenia nie moze byc pusta")
    if len(name) > 200:
        raise ValueError("Nazwa rozliczenia nie moze przekraczac 200 znakow")

    # Walidacja dat
    if period_from > period_to:
        raise ValueError("Data poczatku okresu nie moze byc pozniejsza niz data konca")

    # Walidacja project_ids
    if not project_ids:
        raise ValueError("Rozliczenie musi byc przypisane do co najmniej jednego projektu")

    # Deduplikacja
    project_ids = list(dict.fromkeys(project_ids))

    # Walidacja projektow - istnienie i is_active
    projects_result = await db.execute(select(Project).where(Project.id.in_(project_ids), Project.is_active.is_(True)))
    found_projects = {p.id for p in projects_result.scalars().all()}
    missing = set(project_ids) - found_projects
    if missing:
        raise ValueError(f"Projekty nie istnieja lub sa nieaktywne: {missing}")

    # Zbierz stare project_ids z M2M
    old_project_ids = {p.id for p in settlement.projects}
    new_project_ids = set(project_ids)

    # Walidacja uprawnien dla wszystkich (old union new)
    all_project_ids = old_project_ids | new_project_ids
    for pid in all_project_ids:
        has_write = await check_permission(db, user_id, pid, "rozliczenia", "write")
        if not has_write:
            raise HTTPException(
                status_code=403,
                detail=f"Brak uprawnienia rozliczenia:write w projekcie {pid}",
            )

    # Aktualizuj pola
    settlement.name = name
    settlement.period_from = period_from
    settlement.period_to = period_to
    settlement.notes = notes

    # Zastap wpisy M2M
    await db.execute(delete(SettlementProject).where(SettlementProject.settlement_id == settlement.id))
    for pid in project_ids:
        db.add(SettlementProject(settlement_id=settlement.id, project_id=pid))

    await db.commit()
    await db.refresh(settlement)
    return settlement


async def delete_settlement(
    db: AsyncSession,
    settlement: Settlement,
    user_id: uuid.UUID,
) -> None:
    """Soft delete rozliczenia (tylko draft).

    Walidacja:
    - settlement.status == "draft" (ValueError)
    - user MA rozliczenia:delete w KAZDYM z powiazanych projektow (HTTPException 403)

    Soft delete: settlement.is_active = False; commit.
    """
    # Walidacja statusu
    if settlement.status != "draft":
        raise ValueError("Rozliczenie mozna usunac tylko w statusie draft")

    # Walidacja uprawnien we wszystkich powiazanych projektach
    if not settlement.projects:
        raise HTTPException(status_code=403, detail="Rozliczenie nie ma aktywnych projektow")
    for project in settlement.projects:
        has_delete = await check_permission(db, user_id, project.id, "rozliczenia", "delete")
        if not has_delete:
            raise HTTPException(
                status_code=403,
                detail=f"Brak uprawnienia rozliczenia:delete w projekcie {project.id}",
            )

    # Soft delete
    settlement.is_active = False
    await db.commit()


async def upload_settlement_attachment(
    db: AsyncSession,
    settlement: Settlement,
    user_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    mime_type: str | None,
    category: str,
    state: str,
) -> SettlementAttachment:
    """Upload zalacznika do rozliczenia.

    Walidacja:
    - settlement.status == "draft"
    - user MA rozliczenia:write w kazdym projekcie settlement
    - category in SETTLEMENT_CATEGORIES
    - state in SETTLEMENT_STATES
    - len(file_bytes) <= SETTLEMENT_ATTACHMENT_MAX_SIZE
    - filename niepuste po sanityzacji, rozszerzenie w SETTLEMENT_ALLOWED_EXT

    MinIO path: settlements/{settlement.id}/{YYYY}/{MM}/{DD}/{uuid}.{ext}
    """
    # Walidacja statusu
    if settlement.status != "draft":
        raise ValueError(f"Nie mozna dodawac zalacznikow do rozliczenia w statusie {settlement.status}")

    # Walidacja uprawnien we wszystkich powiazanych projektach
    if not settlement.projects:
        raise HTTPException(status_code=403, detail="Rozliczenie nie ma aktywnych projektow")
    for project in settlement.projects:
        has_write = await check_permission(db, user_id, project.id, "rozliczenia", "write")
        if not has_write:
            raise HTTPException(
                status_code=403,
                detail=f"Brak uprawnienia rozliczenia:write w projekcie {project.id}",
            )

    # Walidacja kategorii i stanu
    if category not in SETTLEMENT_CATEGORIES:
        raise ValueError(f"Nieprawidlowa kategoria: {category}. Dozwolone: {', '.join(sorted(SETTLEMENT_CATEGORIES))}")
    if state not in SETTLEMENT_ATTACHMENT_STATES:
        raise ValueError(f"Nieprawidlowy stan: {state}. Dozwolone: {', '.join(sorted(SETTLEMENT_ATTACHMENT_STATES))}")

    # Walidacja rozmiaru
    if len(file_bytes) > SETTLEMENT_ATTACHMENT_MAX_SIZE:
        raise ValueError("Plik za duzy (max 200MB)")

    # Walidacja limitu zalacznikow
    if len(settlement.attachments) >= MAX_ATTACHMENTS_PER_SETTLEMENT:
        raise ValueError(f"Osiagnieto limit {MAX_ATTACHMENTS_PER_SETTLEMENT} zalacznikow dla rozliczenia")

    # Sanityzacja nazwy pliku
    safe_name = os.path.basename(filename)
    safe_name = re.sub(r"[^\w\-.]", "_", safe_name).strip() or "file"

    # Walidacja rozszerzenia
    _, ext = os.path.splitext(safe_name.lower())
    if not ext or ext not in SETTLEMENT_ALLOWED_EXT:
        raise ValueError(f"Niedozwolone rozszerzenie pliku: '{ext or '(brak)'}'. Dozwolone typy: PDF, Word, Excel, obrazy, archiwa i inne.")

    # Generuj storage_path z unikalnym UUID (nie uzywamy oryginalnej nazwy w sciezce)
    now = datetime.now(UTC)
    date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
    unique_hex = uuid.uuid4().hex
    storage_path = f"settlements/{settlement.id}/{date_prefix}/{unique_hex}{ext}"

    # Upload do MinIO (blokujaca operacja — uruchamiamy w executor)
    content_type = mime_type or "application/octet-stream"
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(
        None,
        partial(minio_client.upload_object, storage_path, file_bytes, content_type),
    )

    # Zapis w bazie danych
    attachment = SettlementAttachment(
        settlement_id=settlement.id,
        category=category,
        state=state,
        filename=safe_name,
        storage_path=storage_path,
        mime_type=mime_type,
        size=len(file_bytes),
        uploaded_by_id=user_id,
    )
    db.add(attachment)
    await db.flush()
    await db.refresh(attachment)
    return attachment


async def delete_settlement_attachment(
    db: AsyncSession,
    attachment: SettlementAttachment,
    user_id: uuid.UUID,
) -> None:
    """Usun zalacznik (MinIO + DB).

    Walidacja:
    - attachment.settlement.status == "draft"
    - user ma rozliczenia:delete w kazdym projekcie settlement
    """
    # Walidacja statusu
    if attachment.settlement.status != "draft":
        raise ValueError(f"Nie mozna usuwac zalacznikow z rozliczenia w statusie {attachment.settlement.status}")

    # Walidacja uprawnien we wszystkich powiazanych projektach
    if not attachment.settlement.projects:
        raise HTTPException(status_code=403, detail="Rozliczenie nie ma aktywnych projektow")
    for project in attachment.settlement.projects:
        has_delete = await check_permission(db, user_id, project.id, "rozliczenia", "delete")
        if not has_delete:
            raise HTTPException(
                status_code=403,
                detail=f"Brak uprawnienia rozliczenia:delete w projekcie {project.id}",
            )

    # Usuniecie z MinIO (jesli nie uda sie -- logujemy, ale kontynuujemy)
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(
            None,
            partial(minio_client.delete_object, attachment.storage_path),
        )
    except Exception:
        logger.warning("Nie udalo sie usunac pliku z MinIO: %s", attachment.storage_path, exc_info=True)

    # Usuniecie z bazy danych
    await db.delete(attachment)
    await db.flush()


def get_settlement_attachment_bytes(
    attachment: SettlementAttachment,
) -> tuple[bytes, str]:
    """Pobierz zawartosc zalacznika z MinIO.

    Zwraca (bytes, content_type).
    """
    file_bytes, content_type = minio_client.get_attachment(attachment.storage_path)
    # Jesli MinIO zwrocil generyczny typ, uzywamy zapisanego mime_type
    if content_type == "application/octet-stream" and attachment.mime_type:
        content_type = attachment.mime_type
    return file_bytes, content_type
