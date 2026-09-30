"""Trwałość zapisów API v2 na osobnych sesjach (MON-131, część 5).

Ani `get_db()`, ani `async_session_factory()` nie robią auto-commitu, a fixture
`db_session` z `tests/conftest.py` opakowuje cały test w jedną zewnętrzną
transakcję z rollbackiem. Zapis, w którym serwis zrobił tylko `flush()`, wygląda
tam na udany (jest ID, kolejny odczyt widzi wiersz), a w produkcji ginie przy
zamknięciu sesji (regresja MON-71, potem MON-203). Dlatego te testy nie używają
`db_session` ani `client`: każdy request dostaje własną sesję z
`async_sessionmaker(engine)`, a wynik jest czytany w NOWEJ sesji. Przy usuniętym
`db.commit()` wiersz nie istnieje (albo ma starą wartość) i test pada.

Każdy wpis `is_write=True` w `TOOL_TO_ENDPOINT` musi mieć scenariusz w
`PERSISTENCE_SCENARIOS`, a scenariusz wysyła request na metodę i ścieżkę wzięte
z mapy, więc sprawdza realny wpis mapy, nie własną kopię ścieżki.

Testy commitują do współdzielonej bazy `open_sentry_test`, więc każdy scenariusz
sprząta wszystkie swoje wiersze w `finally` (w tym rekordy tworzone pośrednio
przez endpoint: projekt z `create_project`, użytkownik z `invite_member`).
"""

from __future__ import annotations

import secrets
import string
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from monolynx.database import get_db
from monolynx.main import app
from monolynx.models.activity_log import ActivityLog
from monolynx.models.label import Label
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.models.wiki_page import WikiPage
from monolynx.services.mcp_auth import generate_api_token
from tests.integration.tool_endpoint_map import TOOL_TO_ENDPOINT

pytestmark = pytest.mark.integration


@dataclass
class _Tracker:
    """Rekordy do usunięcia w `finally`; rejestrowane PRZED requestem, żeby padnięty test też sprzątał."""

    project_ids: set[uuid.UUID] = field(default_factory=set)
    project_slugs: set[str] = field(default_factory=set)
    user_ids: set[uuid.UUID] = field(default_factory=set)
    user_emails: set[str] = field(default_factory=set)


@dataclass
class _Env:
    factory: async_sessionmaker[AsyncSession]
    client: AsyncClient
    tracker: _Tracker


@dataclass
class _Actor:
    user_id: uuid.UUID
    email: str
    headers: dict[str, str]


@dataclass
class _ProjectRef:
    id: uuid.UUID
    slug: str


Scenario = Callable[[_Env], Awaitable[None]]

PERSISTENCE_SCENARIOS: dict[str, Scenario] = {}


def _scenario(tool: str) -> Callable[[Scenario], Scenario]:
    def register(fn: Scenario) -> Scenario:
        PERSISTENCE_SCENARIOS[tool] = fn
        return fn

    return register


def _uid() -> str:
    return uuid.uuid4().hex[:8]


def _letters(length: int) -> str:
    # Klucz ticketu (`resolve_ticket_uuid`) akceptuje w kodzie projektu tylko litery.
    return "".join(secrets.choice(string.ascii_uppercase) for _ in range(length))


@pytest.fixture(autouse=True)
def _external_side_effects():
    """Odcina MinIO, embeddingi i SMTP.

    `sync_backlinks` też jest wyłączony, bo sam robi `db.commit()` na końcu
    `create_wiki_page`/`update_wiki_page` i utrwaliłby zapis mimo brakującego
    commitu w samym serwisie.
    """
    with (
        patch("monolynx.services.wiki.upload_markdown", side_effect=lambda slug, page_id, content: f"{slug}/{page_id}.md"),
        patch("monolynx.services.wiki.delete_object"),
        patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock),
        patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock),
        patch("monolynx.api.v2.projects.send_invitation_email"),
    ):
        yield


async def _cleanup(factory: async_sessionmaker[AsyncSession], tracker: _Tracker) -> None:
    """Usuń wiersze testu w kolejności zgodnej z FK (część relacji nie ma ON DELETE CASCADE)."""
    async with factory() as db:
        project_ids = set(tracker.project_ids)
        if tracker.project_slugs:
            rows = await db.execute(select(Project.id).where(Project.slug.in_(tracker.project_slugs)))
            project_ids.update(rows.scalars().all())
        user_ids = set(tracker.user_ids)
        if tracker.user_emails:
            rows = await db.execute(select(User.id).where(User.email.in_(tracker.user_emails)))
            user_ids.update(rows.scalars().all())

        if project_ids:
            # Kaskada DB usuwa komentarze, kryteria, zależności i ticket_labels.
            await db.execute(delete(Ticket).where(Ticket.project_id.in_(project_ids)))
            await db.execute(delete(Label).where(Label.project_id.in_(project_ids)))
            await db.execute(delete(Sprint).where(Sprint.project_id.in_(project_ids)))
            # Kaskada DB usuwa podstrony, embeddingi i backlinki.
            await db.execute(delete(WikiPage).where(WikiPage.project_id.in_(project_ids)))
            await db.execute(delete(ActivityLog).where(ActivityLog.project_id.in_(project_ids)))
            await db.execute(delete(ProjectMember).where(ProjectMember.project_id.in_(project_ids)))
            await db.execute(delete(Role).where(Role.project_id.in_(project_ids)))
            await db.execute(delete(Project).where(Project.id.in_(project_ids)))
        if user_ids:
            await db.execute(delete(ActivityLog).where(ActivityLog.actor_id.in_(user_ids)))
            await db.execute(delete(ProjectMember).where(ProjectMember.user_id.in_(user_ids)))
            await db.execute(delete(UserApiToken).where(UserApiToken.user_id.in_(user_ids)))
            await db.execute(delete(User).where(User.id.in_(user_ids)))
        await db.commit()


@asynccontextmanager
async def _persistence_env(engine) -> AsyncIterator[_Env]:
    """Własny klient z `get_db` na świeżych sesjach.

    Nie `client` z conftest: ten dzieli sesję z transakcją testu, a jego teardown
    robi `dependency_overrides.clear()`.
    """
    factory = async_sessionmaker(engine, expire_on_commit=False)
    tracker = _Tracker()

    async def override_get_db() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_get_db
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
            yield _Env(factory=factory, client=client, tracker=tracker)
    finally:
        app.dependency_overrides.pop(get_db, None)
        await _cleanup(factory, tracker)


async def _seed_actor(env: _Env, tag: str) -> _Actor:
    """Użytkownik z realnym tokenem API, zacommitowany w osobnej sesji."""
    raw_token, token_hash = generate_api_token()
    user_id = uuid.uuid4()
    email = f"persist-{tag}-{_uid()}@example.com"
    env.tracker.user_ids.add(user_id)
    async with env.factory() as db:
        db.add(User(id=user_id, email=email, is_superuser=False))
        await db.flush()
        db.add(UserApiToken(user_id=user_id, token_hash=token_hash, token_prefix=raw_token[:8], name=f"persist-{tag}"))
        await db.commit()
    return _Actor(user_id=user_id, email=email, headers={"Authorization": f"Bearer {raw_token}"})


async def _seed_project(env: _Env, owner: _Actor, tag: str, *, role: str = "owner") -> _ProjectRef:
    project_id = uuid.uuid4()
    slug = f"persist-{tag}-{_uid()}"
    env.tracker.project_ids.add(project_id)
    async with env.factory() as db:
        db.add(
            Project(
                id=project_id,
                name=f"Persist {tag}",
                slug=slug,
                code=_letters(7),
                api_key=secrets.token_urlsafe(32),
                is_active=True,
            )
        )
        await db.flush()
        db.add(ProjectMember(project_id=project_id, user_id=owner.user_id, role=role))
        await db.commit()
    return _ProjectRef(id=project_id, slug=slug)


async def _seed_plain_member(env: _Env, project: _ProjectRef, tag: str, *, role: str = "member") -> tuple[uuid.UUID, str]:
    """Członek bez tokenu (cel `remove_member`)."""
    user_id = uuid.uuid4()
    email = f"persist-{tag}-{_uid()}@example.com"
    env.tracker.user_ids.add(user_id)
    async with env.factory() as db:
        db.add(User(id=user_id, email=email))
        await db.flush()
        db.add(ProjectMember(project_id=project.id, user_id=user_id, role=role))
        await db.commit()
    return user_id, email


async def _seed_ticket(env: _Env, project: _ProjectRef, *, number: int = 1, **fields: Any) -> uuid.UUID:
    ticket_id = uuid.uuid4()
    values: dict[str, Any] = {"title": f"Ticket {number}", "status": "backlog", "priority": "low"}
    values.update(fields)
    async with env.factory() as db:
        db.add(Ticket(id=ticket_id, project_id=project.id, number=number, **values))
        await db.commit()
    return ticket_id


async def _seed_sprint(env: _Env, project: _ProjectRef, *, status: str = "planning") -> uuid.UUID:
    sprint_id = uuid.uuid4()
    async with env.factory() as db:
        db.add(Sprint(id=sprint_id, project_id=project.id, name=f"Sprint {_uid()}", goal="stary cel", start_date=date(2026, 10, 1), status=status))
        await db.commit()
    return sprint_id


async def _seed_wiki_page(env: _Env, project: _ProjectRef, actor: _Actor, *, title: str, parent_id: uuid.UUID | None = None) -> uuid.UUID:
    page_id = uuid.uuid4()
    async with env.factory() as db:
        db.add(
            WikiPage(
                id=page_id,
                project_id=project.id,
                parent_id=parent_id,
                title=title,
                slug=f"seed-{_uid()}",
                minio_path=f"{project.slug}/{page_id}.md",
                created_by_id=actor.user_id,
                last_edited_by_id=actor.user_id,
            )
        )
        await db.commit()
    return page_id


async def _call(env: _Env, tool: str, actor: _Actor, *, body: dict[str, Any] | None = None, **path_params: str) -> httpx.Response:
    spec = TOOL_TO_ENDPOINT[tool]
    return await env.client.request(spec.method, spec.path.format(**path_params), json=body, headers=actor.headers)


async def _one(env: _Env, stmt) -> Any:
    """Odczyt w NOWEJ sesji: widzi tylko to, co zostało zacommitowane."""
    async with env.factory() as db:
        return (await db.execute(stmt)).scalar_one_or_none()


async def _all(env: _Env, stmt) -> list[Any]:
    async with env.factory() as db:
        return list((await db.execute(stmt)).scalars().all())


# --- Projects ---


@_scenario("create_project")
async def _create_project(env: _Env) -> None:
    actor = await _seed_actor(env, "cproj")
    slug = f"persist-new-{_uid()}"
    code = "P" + _letters(6)
    env.tracker.project_slugs.add(slug)

    response = await _call(env, "create_project", actor, body={"name": "Nowy projekt", "slug": slug, "code": code, "description": "opis projektu"})

    assert response.status_code == 201
    project = await _one(env, select(Project).where(Project.slug == slug))
    assert project is not None
    assert (project.name, project.code, project.description, project.is_active) == ("Nowy projekt", code, "opis projektu", True)
    member = await _one(env, select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == actor.user_id))
    assert member is not None
    assert member.role == "owner"


@_scenario("update_project")
async def _update_project(env: _Env) -> None:
    actor = await _seed_actor(env, "uproj")
    project = await _seed_project(env, actor, "uproj")
    new_slug = f"persist-renamed-{_uid()}"

    response = await _call(
        env,
        "update_project",
        actor,
        body={"name": "Zmieniona nazwa", "slug": new_slug, "description": "nowy opis"},
        project_slug=project.slug,
    )

    assert response.status_code == 200
    saved = await _one(env, select(Project).where(Project.id == project.id))
    assert (saved.name, saved.slug, saved.description) == ("Zmieniona nazwa", new_slug, "nowy opis")


@_scenario("delete_project")
async def _delete_project(env: _Env) -> None:
    actor = await _seed_actor(env, "dproj")
    project = await _seed_project(env, actor, "dproj")

    response = await _call(env, "delete_project", actor, project_slug=project.slug)

    assert response.status_code == 204
    saved = await _one(env, select(Project).where(Project.id == project.id))
    assert saved is not None
    assert saved.is_active is False


@_scenario("invite_member")
async def _invite_member(env: _Env) -> None:
    actor = await _seed_actor(env, "invite")
    project = await _seed_project(env, actor, "invite")
    invitee_email = f"persist-invitee-{_uid()}@example.com"
    env.tracker.user_emails.add(invitee_email)

    response = await _call(env, "invite_member", actor, body={"email": invitee_email, "role": "admin"}, project_slug=project.slug)

    assert response.status_code == 201
    invitee = await _one(env, select(User).where(User.email == invitee_email))
    assert invitee is not None
    assert invitee.password_hash is None
    assert str(invitee.invitation_token) == response.json()["invitation_token"]
    member = await _one(env, select(ProjectMember).where(ProjectMember.project_id == project.id, ProjectMember.user_id == invitee.id))
    assert member is not None
    assert member.role == "admin"


@_scenario("remove_member")
async def _remove_member(env: _Env) -> None:
    actor = await _seed_actor(env, "rmmember")
    project = await _seed_project(env, actor, "rmmember")
    removed_id, _email = await _seed_plain_member(env, project, "removed")

    response = await _call(env, "remove_member", actor, project_slug=project.slug, member_id=str(removed_id))

    assert response.status_code == 204
    remaining = await _all(env, select(ProjectMember.user_id).where(ProjectMember.project_id == project.id))
    assert remaining == [actor.user_id]


@_scenario("create_label")
async def _create_label(env: _Env) -> None:
    actor = await _seed_actor(env, "label")
    project = await _seed_project(env, actor, "label")

    response = await _call(env, "create_label", actor, body={"name": "pilne", "color": "#123456"}, project_slug=project.slug)

    assert response.status_code == 201
    label = await _one(env, select(Label).where(Label.id == uuid.UUID(response.json()["id"])))
    assert label is not None
    assert (label.project_id, label.name, label.color) == (project.id, "pilne", "#123456")


# --- Tickets ---


@_scenario("create_ticket")
async def _create_ticket(env: _Env) -> None:
    actor = await _seed_actor(env, "cticket")
    project = await _seed_project(env, actor, "cticket")

    response = await _call(
        env,
        "create_ticket",
        actor,
        body={"title": "Nowy ticket", "description": "opis ticketu", "priority": "high"},
        project_slug=project.slug,
    )

    assert response.status_code == 201
    ticket = await _one(env, select(Ticket).where(Ticket.id == uuid.UUID(response.json()["id"])))
    assert ticket is not None
    assert (ticket.project_id, ticket.number, ticket.title, ticket.description) == (project.id, 1, "Nowy ticket", "opis ticketu")
    assert (ticket.priority, ticket.status) == ("high", "backlog")


@_scenario("update_ticket")
async def _update_ticket(env: _Env) -> None:
    actor = await _seed_actor(env, "uticket")
    project = await _seed_project(env, actor, "uticket")
    ticket_id = await _seed_ticket(env, project, title="Stary tytul", status="backlog", priority="low")

    response = await _call(
        env,
        "update_ticket",
        actor,
        body={"title": "Nowy tytul", "status": "in_progress", "priority": "critical"},
        project_slug=project.slug,
        ticket_id=str(ticket_id),
    )

    assert response.status_code == 200
    ticket = await _one(env, select(Ticket).where(Ticket.id == ticket_id))
    assert (ticket.title, ticket.status, ticket.priority) == ("Nowy tytul", "in_progress", "critical")


@_scenario("delete_ticket")
async def _delete_ticket(env: _Env) -> None:
    actor = await _seed_actor(env, "dticket")
    project = await _seed_project(env, actor, "dticket")
    ticket_id = await _seed_ticket(env, project)

    response = await _call(env, "delete_ticket", actor, project_slug=project.slug, ticket_id=str(ticket_id))

    assert response.status_code == 204
    assert await _one(env, select(Ticket).where(Ticket.id == ticket_id)) is None


@_scenario("bulk_update_tickets")
async def _bulk_update_tickets(env: _Env) -> None:
    actor = await _seed_actor(env, "bulk")
    project = await _seed_project(env, actor, "bulk")
    first_id = await _seed_ticket(env, project, number=1, status="backlog", priority="low")
    second_id = await _seed_ticket(env, project, number=2, status="backlog", priority="low")

    response = await _call(
        env,
        "bulk_update_tickets",
        actor,
        body={"ticket_ids": [str(first_id), str(second_id)], "status": "done", "priority": "high"},
        project_slug=project.slug,
    )

    assert response.status_code == 200
    assert response.json() == {"updated": 2, "failed": []}
    saved = await _all(env, select(Ticket).where(Ticket.project_id == project.id))
    assert {(t.id, t.status, t.priority) for t in saved} == {(first_id, "done", "high"), (second_id, "done", "high")}


@_scenario("add_comment")
async def _add_comment(env: _Env) -> None:
    actor = await _seed_actor(env, "comment")
    project = await _seed_project(env, actor, "comment")
    ticket_id = await _seed_ticket(env, project)

    response = await _call(env, "add_comment", actor, body={"content": "Treść komentarza"}, project_slug=project.slug, ticket_id=str(ticket_id))

    assert response.status_code == 201
    comments = await _all(env, select(TicketComment).where(TicketComment.ticket_id == ticket_id))
    assert [(c.content, c.user_id) for c in comments] == [("Treść komentarza", actor.user_id)]


@_scenario("add_acceptance_criterion")
async def _add_acceptance_criterion(env: _Env) -> None:
    actor = await _seed_actor(env, "acadd")
    project = await _seed_project(env, actor, "acadd")
    ticket_id = await _seed_ticket(env, project)

    response = await _call(
        env,
        "add_acceptance_criterion",
        actor,
        body={"description": "Nowe kryterium"},
        project_slug=project.slug,
        ticket_id=str(ticket_id),
    )

    assert response.status_code == 201
    criteria = await _all(env, select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.ticket_id == ticket_id))
    assert [(c.description, c.is_completed, c.created_by_user_id) for c in criteria] == [("Nowe kryterium", False, actor.user_id)]


async def _seed_criterion(env: _Env, ticket_id: uuid.UUID, actor: _Actor) -> uuid.UUID:
    criterion_id = uuid.uuid4()
    async with env.factory() as db:
        db.add(
            TicketAcceptanceCriterion(
                id=criterion_id,
                ticket_id=ticket_id,
                description="Stare kryterium",
                position=0,
                created_by_user_id=actor.user_id,
            )
        )
        await db.commit()
    return criterion_id


@_scenario("update_acceptance_criterion")
async def _update_acceptance_criterion(env: _Env) -> None:
    actor = await _seed_actor(env, "acupd")
    project = await _seed_project(env, actor, "acupd")
    ticket_id = await _seed_ticket(env, project)
    criterion_id = await _seed_criterion(env, ticket_id, actor)

    response = await _call(
        env,
        "update_acceptance_criterion",
        actor,
        body={"description": "Zmienione kryterium", "is_completed": True},
        project_slug=project.slug,
        ticket_id=str(ticket_id),
        criterion_id=str(criterion_id),
    )

    assert response.status_code == 200
    criterion = await _one(env, select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion_id))
    assert (criterion.description, criterion.is_completed, criterion.completed_by_user_id) == ("Zmienione kryterium", True, actor.user_id)


@_scenario("delete_acceptance_criterion")
async def _delete_acceptance_criterion(env: _Env) -> None:
    actor = await _seed_actor(env, "acdel")
    project = await _seed_project(env, actor, "acdel")
    ticket_id = await _seed_ticket(env, project)
    criterion_id = await _seed_criterion(env, ticket_id, actor)

    response = await _call(
        env,
        "delete_acceptance_criterion",
        actor,
        project_slug=project.slug,
        ticket_id=str(ticket_id),
        criterion_id=str(criterion_id),
    )

    assert response.status_code == 204
    assert await _one(env, select(TicketAcceptanceCriterion).where(TicketAcceptanceCriterion.id == criterion_id)) is None


# --- Sprints ---


@_scenario("create_sprint")
async def _create_sprint(env: _Env) -> None:
    actor = await _seed_actor(env, "csprint")
    project = await _seed_project(env, actor, "csprint")

    response = await _call(
        env,
        "create_sprint",
        actor,
        body={"name": "Sprint testowy", "goal": "cel sprintu", "start_date": "2026-10-01", "end_date": "2026-10-14"},
        project_slug=project.slug,
    )

    assert response.status_code == 201
    sprint = await _one(env, select(Sprint).where(Sprint.id == uuid.UUID(response.json()["id"])))
    assert sprint is not None
    assert (sprint.project_id, sprint.name, sprint.goal, sprint.status) == (project.id, "Sprint testowy", "cel sprintu", "planning")
    assert (sprint.start_date, sprint.end_date) == (date(2026, 10, 1), date(2026, 10, 14))


@_scenario("update_sprint")
async def _update_sprint(env: _Env) -> None:
    actor = await _seed_actor(env, "usprint")
    project = await _seed_project(env, actor, "usprint")
    sprint_id = await _seed_sprint(env, project)

    response = await _call(
        env,
        "update_sprint",
        actor,
        body={"name": "Zmieniony sprint", "goal": "nowy cel"},
        project_slug=project.slug,
        sprint_id=str(sprint_id),
    )

    assert response.status_code == 200
    sprint = await _one(env, select(Sprint).where(Sprint.id == sprint_id))
    assert (sprint.name, sprint.goal) == ("Zmieniony sprint", "nowy cel")


@_scenario("start_sprint")
async def _start_sprint(env: _Env) -> None:
    actor = await _seed_actor(env, "startsp")
    project = await _seed_project(env, actor, "startsp")
    sprint_id = await _seed_sprint(env, project, status="planning")

    response = await _call(env, "start_sprint", actor, project_slug=project.slug, sprint_id=str(sprint_id))

    assert response.status_code == 200
    sprint = await _one(env, select(Sprint).where(Sprint.id == sprint_id))
    assert sprint.status == "active"


@_scenario("complete_sprint")
async def _complete_sprint(env: _Env) -> None:
    actor = await _seed_actor(env, "endsp")
    project = await _seed_project(env, actor, "endsp")
    sprint_id = await _seed_sprint(env, project, status="active")
    open_id = await _seed_ticket(env, project, number=1, status="todo", sprint_id=sprint_id)
    done_id = await _seed_ticket(env, project, number=2, status="done", sprint_id=sprint_id)

    response = await _call(env, "complete_sprint", actor, project_slug=project.slug, sprint_id=str(sprint_id))

    assert response.status_code == 200
    sprint = await _one(env, select(Sprint).where(Sprint.id == sprint_id))
    assert sprint.status == "completed"
    tickets = {t.id: t for t in await _all(env, select(Ticket).where(Ticket.project_id == project.id))}
    assert (tickets[open_id].status, tickets[open_id].sprint_id) == ("backlog", None)
    assert (tickets[done_id].status, tickets[done_id].sprint_id) == ("done", sprint_id)


# --- Wiki ---


@_scenario("create_wiki_page")
async def _create_wiki_page(env: _Env) -> None:
    actor = await _seed_actor(env, "cwiki")
    project = await _seed_project(env, actor, "cwiki")

    response = await _call(env, "create_wiki_page", actor, body={"title": "Nowa strona", "content": "# Treść"}, project_slug=project.slug)

    assert response.status_code == 201
    page_id = uuid.UUID(response.json()["id"])
    page = await _one(env, select(WikiPage).where(WikiPage.id == page_id))
    assert page is not None
    assert (page.project_id, page.title, page.is_public, page.created_by_id) == (project.id, "Nowa strona", False, actor.user_id)
    assert page.minio_path == f"{project.slug}/{page_id}.md"


@_scenario("update_wiki_page")
async def _update_wiki_page(env: _Env) -> None:
    actor = await _seed_actor(env, "uwiki")
    project = await _seed_project(env, actor, "uwiki")
    page_id = await _seed_wiki_page(env, project, actor, title="Stary tytul")

    response = await _call(
        env,
        "update_wiki_page",
        actor,
        body={"title": "Nowy tytul", "content": "# Nowa treść", "position": 3},
        project_slug=project.slug,
        page_id=str(page_id),
    )

    assert response.status_code == 200
    page = await _one(env, select(WikiPage).where(WikiPage.id == page_id))
    assert (page.title, page.position) == ("Nowy tytul", 3)


@_scenario("delete_wiki_page")
async def _delete_wiki_page(env: _Env) -> None:
    actor = await _seed_actor(env, "dwiki")
    project = await _seed_project(env, actor, "dwiki")
    parent_id = await _seed_wiki_page(env, project, actor, title="Rodzic")
    child_id = await _seed_wiki_page(env, project, actor, title="Dziecko", parent_id=parent_id)

    response = await _call(env, "delete_wiki_page", actor, project_slug=project.slug, page_id=str(parent_id))

    assert response.status_code == 204
    remaining = await _all(env, select(WikiPage.id).where(WikiPage.id.in_([parent_id, child_id])))
    assert remaining == []


@_scenario("set_wiki_config")
async def _set_wiki_config(env: _Env) -> None:
    actor = await _seed_actor(env, "wikicfg")
    project = await _seed_project(env, actor, "wikicfg")

    response = await _call(env, "set_wiki_config", actor, body={"enabled": True}, project_slug=project.slug)

    assert response.status_code == 200
    saved = await _one(env, select(Project).where(Project.id == project.id))
    assert saved.wiki_llm_enabled is True


_WRITE_TOOLS = sorted(name for name, spec in TOOL_TO_ENDPOINT.items() if spec.is_write)


def test_every_write_tool_has_a_persistence_scenario() -> None:
    write_tools = {name for name, spec in TOOL_TO_ENDPOINT.items() if spec.is_write}

    assert set(PERSISTENCE_SCENARIOS) == write_tools


@pytest.mark.parametrize("tool_name", _WRITE_TOOLS)
async def test_write_endpoint_persists_to_separate_session(engine, tool_name: str) -> None:
    async with _persistence_env(engine) as env:
        await PERSISTENCE_SCENARIOS[tool_name](env)
