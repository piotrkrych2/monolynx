"""Serwis logiki projektow, czlonkow, etykiet i zagregowanych statystyk projektu
-- sesje przekazuje wywolujacy (MCP tool / REST API v2)."""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.constants import DEFAULT_ROLE_PERMISSIONS, INVITATION_DAYS, LABEL_COLOR_PALETTE
from monolynx.dashboard.helpers import CODE_PATTERN, SLUG_PATTERN
from monolynx.models.heartbeat import Heartbeat
from monolynx.models.issue import Issue
from monolynx.models.label import Label, TicketLabel
from monolynx.models.monitor import Monitor
from monolynx.models.monitor_check import MonitorCheck
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.services.permissions import check_permission, ensure_can_manage_role, ensure_permission

# --- Projekty ---


@dataclass
class ProjectListItem:
    project: Project
    role: str


async def list_projects(db: AsyncSession, user: User) -> list[ProjectListItem]:
    """Lista projektow, do ktorych uzytkownik jest przypisany (tylko aktywne)."""
    result = await db.execute(
        select(Project, ProjectMember.role)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == user.id,
            Project.is_active.is_(True),
        )
        .order_by(Project.name)
    )
    return [ProjectListItem(project=project, role=role) for project, role in result.all()]


@dataclass
class ProjectDetailResult:
    project: Project
    role: str | None
    members_count: int
    tickets_count: int
    active_sprint: dict[str, Any] | None
    monitors_count: int
    heartbeats_count: int


async def get_project(db: AsyncSession, user: User, project: Project) -> ProjectDetailResult:
    """Pelne szczegoly projektu: rola wywolujacego, statystyki, aktywny sprint."""
    role_result = await db.execute(
        select(ProjectMember.role).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == user.id,
        )
    )
    role = role_result.scalar_one()

    members_count = (
        await db.execute(
            select(func.count(ProjectMember.id)).where(
                ProjectMember.project_id == project.id,
            )
        )
    ).scalar() or 0

    tickets_count = (
        await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.project_id == project.id,
            )
        )
    ).scalar() or 0

    sprint_result = await db.execute(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == "active",
        )
    )
    sprint = sprint_result.scalar_one_or_none()

    active_sprint = None
    if sprint:
        ticket_stats = await db.execute(select(Ticket.status, func.count(Ticket.id)).where(Ticket.sprint_id == sprint.id).group_by(Ticket.status))
        status_counts: dict[str, int] = {row[0]: row[1] for row in ticket_stats.all()}
        active_sprint = {
            "name": sprint.name,
            "goal": sprint.goal,
            "tickets_by_status": status_counts,
        }

    monitors_count = (
        await db.execute(
            select(func.count(Monitor.id)).where(
                Monitor.project_id == project.id,
            )
        )
    ).scalar() or 0

    heartbeats_count = (
        await db.execute(
            select(func.count(Heartbeat.id)).where(
                Heartbeat.project_id == project.id,
            )
        )
    ).scalar() or 0

    return ProjectDetailResult(
        project=project,
        role=role,
        members_count=members_count,
        tickets_count=tickets_count,
        active_sprint=active_sprint,
        monitors_count=monitors_count,
        heartbeats_count=heartbeats_count,
    )


def _slugify(name: str) -> str:
    """Zamien nazwe projektu na slug (male litery, myslniki zamiast spacji/znakow)."""
    slug = name.lower()
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s-]+", "-", slug)
    slug = slug.strip("-")
    return slug or "projekt"


def _code_from_slug(slug: str) -> str:
    """Wygeneruj kod projektu ze sluga (np. 'anna-miastkowska' -> 'AM', 'monolynx' -> 'MONOL')."""
    parts = slug.split("-")
    code = slug[:5].upper() if len(parts) == 1 else "".join(p[0] for p in parts if p)[:10].upper()
    if not code:
        code = "PROJ"
    # Upewnij sie ze kod zaczyna sie od litery
    if not code[0].isalpha():
        code = "P" + code[:9]
    # Upewnij sie ze kod ma minimum 2 znaki
    if len(code) < 2:
        code = code * 2
    return code


async def suggest_available_slug(db: AsyncSession, slug: str) -> tuple[bool, str | None]:
    """Sprawdza dostepnosc sluga; zajety -> pierwszy wolny wariant `slug-2..99` (albo None).

    Wejscie jest znormalizowane i poprawne formatowo. Bez filtra `is_active`: projekt po
    soft delete nadal zajmuje slug (unikalnosc kolumny dotyczy wszystkich wierszy).
    """
    candidates = [slug] + [f"{slug}-{i}" for i in range(2, 100)]
    candidates = [c for c in candidates if len(c) <= 255]
    taken = set((await db.execute(select(Project.slug).where(Project.slug.in_(candidates)))).scalars().all())
    if slug not in taken:
        return True, slug
    return False, next((c for c in candidates if c not in taken), None)


async def suggest_available_code(db: AsyncSession, code: str) -> tuple[bool, str | None]:
    """Sprawdza dostepnosc kodu; zajety -> pierwszy wolny wariant z sufiksem 2..99 (albo None).

    Sufiks wchodzi w limit 10 znakow: kod jest skracany z prawej, zeby sufiks sie miescil.
    Bez filtra `is_active` (kod unikalny takze dla projektow po soft delete).
    """
    candidates = [code]
    for i in range(2, 100):
        variant = code[: 10 - len(str(i))] + str(i)
        if CODE_PATTERN.match(variant) and variant not in candidates:
            candidates.append(variant)
    taken = set((await db.execute(select(Project.code).where(Project.code.in_(candidates)))).scalars().all())
    if code not in taken:
        return True, code
    return False, next((c for c in candidates if c not in taken), None)


async def create_project(
    db: AsyncSession,
    user: User,
    name: str,
    slug: str | None = None,
    description: str | None = None,
    code: str | None = None,
) -> Project:
    """Tworzy nowy projekt w Monolynx; wywolujacy zostaje dodany jako owner."""
    name = name.strip()
    if not name:
        raise ValueError("Nazwa projektu nie moze byc pusta.")
    if len(name) > 255:
        raise ValueError("Nazwa projektu nie moze przekraczac 255 znakow.")
    if description is not None and len(description) > 1000:
        raise ValueError("Opis projektu nie moze przekraczac 1000 znakow.")

    slug = slug.strip().lower() if slug else _slugify(name)

    if not SLUG_PATTERN.match(slug):
        raise ValueError(f"Nieprawidlowy slug '{slug}'. Slug moze zawierac tylko male litery, cyfry i myslniki (np. 'anna-miastkowska').")

    code = code.strip().upper() if code else _code_from_slug(slug)

    if not CODE_PATTERN.match(code):
        raise ValueError(f"Nieprawidlowy kod '{code}'. Kod musi miec 2-10 znakow: wielkie litery i cyfry, zaczynac sie od litery (np. PIM, PROJ2).")

    slug_free, _ = await suggest_available_slug(db, slug)
    if not slug_free:
        raise ValueError(f"Projekt ze slugiem '{slug}' juz istnieje. Podaj inny slug.")

    code_free, code_suggestion = await suggest_available_code(db, code)
    if not code_free:
        if code_suggestion is None:
            raise ValueError(f"Kod '{code}' jest juz zajety i nie udalo sie znalezc wolnego wariantu. Podaj inny kod.")
        code = code_suggestion

    project = Project(
        name=name,
        slug=slug,
        code=code,
        description=description,
        api_key=secrets.token_urlsafe(32),
    )
    db.add(project)
    try:
        await db.flush()
    except IntegrityError as e:
        await db.rollback()
        raise ValueError("Projekt z takim slugiem lub kodem juz istnieje.") from e

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role="owner",
    )
    db.add(member)
    await db.commit()
    await db.refresh(project)

    return project


async def update_project(
    db: AsyncSession,
    user: User,
    project: Project,
    name: str | None = None,
    description: str | None = None,
    new_slug: str | None = None,
) -> Project:
    """Aktualizuj projekt. Podaj tylko pola do zmiany. Wymaga settings:write."""
    if not await check_permission(db, user.id, project.id, "settings", "write"):
        raise ValueError("Brak uprawnienia do edycji projektu (settings:write)")

    if new_slug is not None:
        if len(new_slug) > 255:
            raise ValueError("Slug moze miec maksymalnie 255 znakow")
        if not SLUG_PATTERN.match(new_slug):
            raise ValueError("Slug moze zawierac tylko male litery, cyfry i myslniki")
        existing = await db.execute(select(Project.id).where(Project.slug == new_slug, Project.id != project.id))
        if existing.scalar_one_or_none() is not None:
            raise ValueError(f"Slug '{new_slug}' jest juz zajety")

    # Pobierz projekt do edycji w tej sesji
    proj_result = await db.execute(select(Project).where(Project.id == project.id))
    proj = proj_result.scalar_one()

    if name is not None:
        stripped_name = name.strip()
        if not stripped_name:
            raise ValueError("Nazwa nie moze byc pusta")
        if len(stripped_name) > 255:
            raise ValueError("Nazwa moze miec maksymalnie 255 znakow")
        proj.name = stripped_name
    if description is not None:
        stripped_desc = description.strip()
        if len(stripped_desc) > 1000:
            raise ValueError("Opis moze miec maksymalnie 1000 znakow")
        proj.description = stripped_desc if stripped_desc else None
    if new_slug is not None:
        proj.slug = new_slug

    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ValueError("Projekt z takim slugiem juz istnieje") from None
    await db.refresh(proj)

    return proj


async def delete_project(db: AsyncSession, user: User, project: Project) -> None:
    """Usun projekt (soft delete). Wymaga settings:delete. `confirm` jest walidowany przez wrapper."""
    if not await check_permission(db, user.id, project.id, "settings", "delete"):
        raise ValueError("Brak uprawnienia do usuniecia projektu (settings:delete)")

    proj_result = await db.execute(select(Project).where(Project.id == project.id))
    proj = proj_result.scalar_one()

    proj.is_active = False
    await db.commit()


# --- Czlonkowie ---


@dataclass
class InviteMemberResult:
    email: str
    role: str
    is_new_user: bool
    invitation_token: uuid.UUID | None = None
    expires_at: datetime | None = None


async def invite_member(db: AsyncSession, user: User, project: Project, email: str, role: str = "member") -> InviteMemberResult:
    """Zaprosz nowa osobe do projektu lub dodaj istniejacego uzytkownika jako czlonka.

    Jesli uzytkownik z danym emailem juz istnieje i jest aktywny — dodawany jest
    bezposrednio jako czlonek (is_new_user=False, bez wysylki maila).
    Jesli uzytkownik nie istnieje — tworzone jest nowe konto (bez hasla) z tokenem
    zaproszenia waznym 7 dni (is_new_user=True); wyslanie maila nalezy do wrappera.
    Wymaga users:write; uprawnienia nadawanej roli musza byc podzbiorem uprawnien
    zapraszajacego (ensure_can_manage_role).
    """
    if role not in ("member", "admin"):
        raise ValueError("Rola musi byc 'member' lub 'admin' (owner nie moze byc przyznany przez zaproszenie)")

    email = email.strip().lower()
    if not email:
        raise ValueError("Email nie moze byc pusty")
    if "@" not in email or len(email) > 255:
        raise ValueError("Nieprawidlowy format adresu email")

    if not await check_permission(db, user.id, project.id, "users", "write"):
        raise ValueError("Brak uprawnienia do zapraszania czlonkow do projektu (users:write)")
    await ensure_can_manage_role(db, user.id, project.id, granted=DEFAULT_ROLE_PERMISSIONS[role])

    # Sprawdz czy user juz istnieje w systemie (takze nieaktywny)
    any_user_result = await db.execute(select(User).where(User.email == email))
    any_user = any_user_result.scalar_one_or_none()
    if any_user is not None and not any_user.is_active:
        raise ValueError(f"Uzytkownik {email} jest dezaktywowany")

    existing_user = any_user if (any_user is not None and any_user.is_active) else None

    if existing_user is not None:
        existing_member_result = await db.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == existing_user.id,
            )
        )
        if existing_member_result.scalar_one_or_none() is not None:
            raise ValueError(f"Uzytkownik '{email}' jest juz czlonkiem projektu '{project.slug}'")

        new_member = ProjectMember(
            project_id=project.id,
            user_id=existing_user.id,
            role=role,
        )
        db.add(new_member)
        try:
            await db.commit()
        except IntegrityError:
            await db.rollback()
            raise ValueError(f"Uzytkownik '{email}' jest juz czlonkiem projektu '{project.slug}'") from None

        return InviteMemberResult(email=email, role=role, is_new_user=False)

    # Nowy uzytkownik — utworz konto z tokenem zaproszenia
    invitation_token = uuid.uuid4()
    expires_at = datetime.now(UTC) + timedelta(days=INVITATION_DAYS)

    new_user = User(
        email=email,
        first_name="",
        last_name="",
        password_hash=None,
        invitation_token=invitation_token,
        invitation_expires_at=expires_at,
    )
    db.add(new_user)

    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise ValueError(f"Uzytkownik z emailem '{email}' juz istnieje w systemie") from None

    new_member = ProjectMember(
        project_id=project.id,
        user_id=new_user.id,
        role=role,
    )
    db.add(new_member)
    await db.commit()

    return InviteMemberResult(
        email=email,
        role=role,
        is_new_user=True,
        invitation_token=invitation_token,
        expires_at=expires_at,
    )


@dataclass
class MemberResult:
    user_id: uuid.UUID
    name: str
    email: str
    role: str
    joined_at: datetime


async def list_members(db: AsyncSession, user: User, project: Project) -> list[MemberResult]:
    """Lista czlonkow projektu z rolami; owner na gorze, potem admin, potem member — kazda grupa alfabetycznie po name."""
    result = await db.execute(
        select(ProjectMember, User)
        .join(User, User.id == ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id)
        .order_by(
            case(
                (ProjectMember.role == "owner", 1),
                (ProjectMember.role == "admin", 2),
                else_=3,
            ),
            (User.first_name + " " + User.last_name),
        )
    )
    rows = result.all()

    return [
        MemberResult(
            user_id=member.user_id,
            name=f"{u.first_name} {u.last_name}".strip() or u.email,
            email=u.email,
            role=member.role,
            joined_at=member.created_at,
        )
        for member, u in rows
    ]


async def remove_member(db: AsyncSession, user: User, project: Project, email: str) -> None:
    """Usun czlonka z projektu. Wymaga users:delete. Nie mozna usunac ownera.

    Tickety przypisane do usuwanej osoby pozostaja bez zmian (assignee nie jest czyszczony).
    """
    email = email.strip().lower()
    if not email:
        raise ValueError("Email nie moze byc pusty")
    if "@" not in email or len(email) > 255:
        raise ValueError("Nieprawidlowy format adresu email")

    if not await check_permission(db, user.id, project.id, "users", "delete"):
        raise ValueError("Brak uprawnienia do usuniecia czlonkow z projektu (users:delete)")

    # User.email jest case-sensitive, a konta z dashboardu moga miec wielkie litery - porownanie
    # po lower() znajduje takie konto; dwa konta roznace sie tylko wielkoscia liter to niejednoznacznosc.
    matches = (await db.execute(select(User).where(func.lower(User.email) == email).limit(2))).scalars().all()
    if not matches:
        raise ValueError(f"Uzytkownik z emailem '{email}' nie istnieje w systemie")
    if len(matches) > 1:
        raise ValueError(f"Email '{email}' pasuje do wielu kont roznych tylko wielkoscia liter - nie mozna jednoznacznie wskazac czlonka")

    await _delete_member(db, project, matches[0], label=email)


async def remove_member_by_user_id(db: AsyncSession, user: User, project: Project, user_id: uuid.UUID) -> None:
    """Usun czlonka z projektu po user_id. Wymaga users:delete. Nie mozna usunac ownera.

    Wspolny rdzen z `remove_member`; nie zalezy od wielkosci liter w e-mailu.
    """
    if not await check_permission(db, user.id, project.id, "users", "delete"):
        raise ValueError("Brak uprawnienia do usuniecia czlonkow z projektu (users:delete)")

    target_user = (await db.execute(select(User).where(User.id == user_id))).scalar_one_or_none()
    if target_user is None:
        raise ValueError(f"Uzytkownik '{user_id}' nie istnieje w systemie")

    await _delete_member(db, project, target_user, label=str(user_id))


async def _delete_member(db: AsyncSession, project: Project, target_user: User, label: str) -> None:
    member_result = await db.execute(
        select(ProjectMember).where(
            ProjectMember.project_id == project.id,
            ProjectMember.user_id == target_user.id,
        )
    )
    member = member_result.scalar_one_or_none()
    if member is None:
        raise ValueError(f"Uzytkownik '{label}' nie jest czlonkiem projektu '{project.slug}'")

    if member.role == "owner":
        raise ValueError("Nie mozna usunac ownera projektu. Zmien najpierw role ownera na innego czlonka.")

    await db.delete(member)
    await db.commit()


# --- Podsumowanie projektu ---


@dataclass
class ProjectSummaryResult:
    issues_unresolved: int
    monitors_failing: int
    uptime_24h: float | None
    active_sprint: dict[str, Any] | None
    backlog_count: int


async def get_project_summary(db: AsyncSession, user: User, project: Project) -> ProjectSummaryResult:
    """Zagregowane statystyki projektu: otwarte bledy, monitory, aktywny sprint, backlog."""
    twenty_four_hours_ago = datetime.now(UTC) - timedelta(hours=24)

    issues_count = (
        await db.execute(
            select(func.count(Issue.id)).where(
                Issue.project_id == project.id,
                Issue.status == "unresolved",
            )
        )
    ).scalar() or 0

    latest_check_sq = (
        select(
            MonitorCheck.monitor_id,
            func.max(MonitorCheck.checked_at).label("max_checked_at"),
        )
        .group_by(MonitorCheck.monitor_id)
        .subquery()
    )
    failing_result = await db.execute(
        select(func.count(Monitor.id))
        .join(latest_check_sq, Monitor.id == latest_check_sq.c.monitor_id)
        .join(
            MonitorCheck,
            (MonitorCheck.monitor_id == latest_check_sq.c.monitor_id) & (MonitorCheck.checked_at == latest_check_sq.c.max_checked_at),
        )
        .where(
            Monitor.project_id == project.id,
            Monitor.is_active.is_(True),
            MonitorCheck.is_success.is_(False),
        )
    )
    monitors_failing = failing_result.scalar() or 0

    uptime_result = await db.execute(
        select(
            func.count(MonitorCheck.id),
            func.count(case((MonitorCheck.is_success.is_(True), 1))),
        )
        .join(Monitor, MonitorCheck.monitor_id == Monitor.id)
        .where(
            Monitor.project_id == project.id,
            Monitor.is_active.is_(True),
            MonitorCheck.checked_at >= twenty_four_hours_ago,
        )
    )
    uptime_row = uptime_result.one()
    uptime_24h: float | None = None
    if uptime_row[0] > 0:
        uptime_24h = round((uptime_row[1] / uptime_row[0]) * 100, 1)

    sprint_result = await db.execute(
        select(Sprint).where(
            Sprint.project_id == project.id,
            Sprint.status == "active",
        )
    )
    sprint = sprint_result.scalar_one_or_none()

    sprint_summary = None
    if sprint:
        ticket_stats = await db.execute(select(Ticket.status, func.count(Ticket.id)).where(Ticket.sprint_id == sprint.id).group_by(Ticket.status))
        status_counts: dict[str, int] = {row[0]: row[1] for row in ticket_stats.all()}
        sprint_summary = {
            "name": sprint.name,
            "goal": sprint.goal,
            "tickets_by_status": status_counts,
            "total_tickets": sum(status_counts.values()),
        }

    backlog_count = (
        await db.execute(
            select(func.count(Ticket.id)).where(
                Ticket.project_id == project.id,
                Ticket.status == "backlog",
            )
        )
    ).scalar() or 0

    return ProjectSummaryResult(
        issues_unresolved=issues_count,
        monitors_failing=monitors_failing,
        uptime_24h=uptime_24h,
        active_sprint=sprint_summary,
        backlog_count=backlog_count,
    )


# --- Etykiety ---


@dataclass
class LabelSummary:
    id: uuid.UUID
    name: str
    color: str
    tickets_count: int


async def list_labels(db: AsyncSession, user: User, project: Project) -> list[LabelSummary]:
    """Lista etykiet projektu z liczba powiazanych ticketow."""
    await ensure_permission(db, user.id, project.id, "scrum", "read")
    result = await db.execute(
        select(
            Label.id,
            Label.name,
            Label.color,
            func.count(TicketLabel.ticket_id).label("tickets_count"),
        )
        .outerjoin(TicketLabel, TicketLabel.label_id == Label.id)
        .where(Label.project_id == project.id)
        .group_by(Label.id, Label.name, Label.color)
        .order_by(Label.name)
    )
    return [LabelSummary(id=row.id, name=row.name, color=row.color, tickets_count=row.tickets_count) for row in result.all()]


async def create_label(db: AsyncSession, user: User, project: Project, name: str, color: str | None = None) -> Label:
    """Tworzy nowa etykiete (label) w projekcie. Bez podanego koloru — losowy z palety."""
    await ensure_permission(db, user.id, project.id, "scrum", "write")
    name = name.strip()
    if not name:
        raise ValueError("Nazwa etykiety nie moze byc pusta.")
    if len(name) > 100:
        raise ValueError("Nazwa etykiety nie moze przekraczac 100 znakow.")

    if color is not None:
        color = color.strip()
        if not color.startswith("#") or len(color) != 7:
            raise ValueError("Kolor musi byc w formacie hex np. '#e74c3c'.")
    else:
        color = secrets.choice(LABEL_COLOR_PALETTE)

    existing = await db.execute(
        select(Label.id).where(
            Label.project_id == project.id,
            Label.name == name,
        )
    )
    if existing.scalar_one_or_none() is not None:
        raise ValueError(f"Etykieta o nazwie '{name}' juz istnieje w tym projekcie.")

    label = Label(
        project_id=project.id,
        name=name,
        color=color,
    )
    db.add(label)
    await db.commit()
    await db.refresh(label)

    return label
