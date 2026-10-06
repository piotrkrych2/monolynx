"""Testy RBAC modułów Scrum i Wiki w kanałach MCP i API v2 (MON-241).

Kontrakt: samo członkostwo w projekcie nie wystarcza. Operacje na ticketach, sprintach, komentarzach,
kryteriach akceptacji, etykietach, czasie pracy, załącznikach i stronach wiki sprawdzają uprawnienia roli
(`scrum:read|write|delete`, `wiki:read|write|delete`; metadane wpisów bloga MON-261: `list_blog_posts` i `get_blog_post` -> `wiki:read`,
`set_blog_post_meta` -> `wiki:write`; lint wpisu MON-262: `lint_blog_post` -> `wiki:read`) tak samo jak dashboard:

- odmowa to `ValueError("Brak uprawnienia <moduł>:<akcja>")` w MCP i 403 `application/problem+json` z tym samym
  `detail` w API v2,
- check idzie przed lookupem zasobu, parsowaniem id i walidacją wejścia (rola bez uprawnienia nie odróżnia 404 od 403
  ani błędu wejścia od odmowy); dowodzą tego `ORDER_CASES` (po jednym wierszu na wejście, które bez checku skończyłoby się
  innym błędem, w MCP i, gdy wejście dociera do handlera, w API v2), `TestPermissionBeforeLookup` oraz
  `TestPermissionBeforeInputValidationInMcp`; osobno narzędzia LLM Wiki z włączoną flagą,
- endpoint v2, który po zapisie czyta zasób (POST/PATCH ticketu, start/complete sprintu), sprawdza `scrum:read`
  przed zapisem, więc rola z `scrum:write` bez `scrum:read` nie zostawia po sobie zapisu,
- `is_public=true` wymaga tego samego uprawnienia co formularz dashboardu, czyli `wiki:write`.

Aktorzy (jeden projekt, osobny użytkownik z tokenem na rolę):
- `owner`, `admin`, `member`: role legacy (`DEFAULT_ROLE_PERMISSIONS`), member ma read+write bez delete,
- `readonly`: rola niestandardowa `{"scrum": ["read"], "wiki": ["read"]}`,
- `no_scrum_read` (`{"wiki": ["read"]}`), `no_wiki_read` (`{"scrum": ["read"]}`),
- `write_no_read` (`{"scrum": ["write"]}`).

Tabela `CASES` opisuje każde narzędzie MCP (i, jeśli istnieje, endpoint v2 z `TOOL_TO_ENDPOINT`) z wymaganym
uprawnieniem. Testy odmowy porównują pełny stan bazy (`snapshot`) przed i po wywołaniu, więc zapis przed checkiem
albo po nim też wychodzi. MinIO i embeddingi są zamockowane (MinIO jako słownik w pamięci); sesja testowa jest
współdzielona przez oba kanały, więc testy nie dowodzą commita (`db-commit.md`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx import mcp_server
from monolynx.models.blog_post import BlogPost
from monolynx.models.label import Label
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.ticket_attachment import TicketAttachment
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.time_tracking_entry import TimeTrackingEntry
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services.ticket_numbering import get_next_ticket_number
from tests.integration.api_v2.conftest import ApiV2Actor, add_member, make_actor, make_project
from tests.integration.tool_endpoint_map import TOOL_TO_ENDPOINT

pytestmark = pytest.mark.integration

_PROBLEM_JSON = "application/problem+json"

_CUSTOM_ROLES: dict[str, dict[str, list[str]]] = {
    "readonly": {"scrum": ["read"], "wiki": ["read"]},
    "no_scrum_read": {"wiki": ["read"]},
    "no_wiki_read": {"scrum": ["read"]},
    "write_no_read": {"scrum": ["write"]},
}

# Rola, która nie ma danego uprawnienia, a ma pozostałe potrzebne do dotarcia do checku.
_DENIED_ROLES: dict[str, tuple[str, ...]] = {
    "scrum:read": ("no_scrum_read",),
    "wiki:read": ("no_wiki_read",),
    "scrum:write": ("readonly",),
    "wiki:write": ("readonly",),
    "scrum:delete": ("member", "readonly"),
    "wiki:delete": ("member", "readonly"),
}

# Role, które mają uprawnienie (rola z drugiego modułu dowodzi, że check nie miesza modułów).
_ALLOWED_ROLES: dict[str, tuple[str, ...]] = {
    "scrum:read": ("owner", "admin", "member", "readonly", "no_wiki_read"),
    "wiki:read": ("owner", "admin", "member", "readonly", "no_scrum_read"),
    "scrum:write": ("owner", "admin", "member"),
    "wiki:write": ("owner", "admin", "member"),
    "scrum:delete": ("owner", "admin"),
    "wiki:delete": ("owner", "admin"),
}

_B64_HELLO = "aGVsbG8="
_MISSING_KEY = "ZZZZ-999"
_MISSING_UUID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Środowisko
# ---------------------------------------------------------------------------


@dataclass
class Seed:
    """Zasoby projektu założone ORM-em przed każdym testem."""

    ticket_id: uuid.UUID
    key: str
    criterion_id: uuid.UUID
    sprint_id: uuid.UUID
    page_id: uuid.UUID
    label_id: uuid.UUID


@dataclass(frozen=True)
class V2Req:
    json: Any = None
    params: Mapping[str, Any] | None = None
    path: Mapping[str, Any] = field(default_factory=dict)


McpCall = Callable[[Seed], tuple[tuple[Any, ...], dict[str, Any]]]


@dataclass(frozen=True)
class Case:
    """Narzędzie MCP z wymaganym uprawnieniem; `v2` jest None dla narzędzi bez endpointu API v2.

    `positive=False` wyłącza narzędzie z testu "rola z uprawnieniem przechodzi": jego udana ścieżka wymaga
    flagi LLM Wiki, aktywnego sprintu, realnych plików w MinIO albo zasobów, których seed nie zakłada.
    """

    tool: str
    permission: str
    mcp: McpCall
    v2: Callable[[Seed], V2Req] | None = None
    positive: bool = True


def _a(*args: Any, **kwargs: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
    return args, kwargs


def _ticket_path(s: Seed) -> dict[str, Any]:
    return {"ticket_id": s.key}


CASES: list[Case] = [
    # --- scrum:read ---
    Case("list_tickets", "scrum:read", lambda s: _a(), lambda s: V2Req()),
    Case("search_tickets", "scrum:read", lambda s: _a(query="Ticket"), lambda s: V2Req(params={"query": "Ticket"})),
    Case("get_ticket", "scrum:read", lambda s: _a(s.key), lambda s: V2Req(path=_ticket_path(s))),
    Case("get_board", "scrum:read", lambda s: _a(), lambda s: V2Req()),
    Case("list_comments", "scrum:read", lambda s: _a(s.key), lambda s: V2Req(path=_ticket_path(s))),
    Case("list_acceptance_criteria", "scrum:read", lambda s: _a(s.key), lambda s: V2Req(path=_ticket_path(s))),
    Case("list_sprints", "scrum:read", lambda s: _a(), lambda s: V2Req()),
    Case("get_sprint", "scrum:read", lambda s: _a(str(s.sprint_id)), lambda s: V2Req(path={"sprint_id": s.sprint_id})),
    Case("get_burndown", "scrum:read", lambda s: _a(), lambda s: V2Req(), positive=False),
    Case("list_labels", "scrum:read", lambda s: _a(), lambda s: V2Req()),
    Case("get_attachment", "scrum:read", lambda s: _a(s.key, str(uuid.uuid4())), positive=False),
    # --- scrum:write ---
    Case("create_ticket", "scrum:write", lambda s: _a("Nowy ticket"), lambda s: V2Req(json={"title": "Nowy ticket"})),
    Case(
        "update_ticket",
        "scrum:write",
        lambda s: _a(s.key, title="Zmieniony tytuł"),
        lambda s: V2Req(json={"title": "Zmieniony tytuł"}, path=_ticket_path(s)),
    ),
    Case(
        "bulk_update_tickets",
        "scrum:write",
        lambda s: _a([s.key], status="done"),
        lambda s: V2Req(json={"ticket_ids": [s.key], "status": "done"}),
    ),
    Case(
        "add_comment",
        "scrum:write",
        lambda s: _a(s.key, "Nowy komentarz"),
        lambda s: V2Req(json={"content": "Nowy komentarz"}, path=_ticket_path(s)),
    ),
    Case(
        "add_acceptance_criterion",
        "scrum:write",
        lambda s: _a(s.key, "Nowe kryterium"),
        lambda s: V2Req(json={"description": "Nowe kryterium"}, path=_ticket_path(s)),
    ),
    Case(
        "update_acceptance_criterion",
        "scrum:write",
        lambda s: _a(s.key, str(s.criterion_id), is_completed=True),
        lambda s: V2Req(json={"is_completed": True}, path={"ticket_id": s.key, "criterion_id": s.criterion_id}),
    ),
    Case(
        "create_sprint",
        "scrum:write",
        lambda s: _a("Sprint nowy", "2026-12-01"),
        lambda s: V2Req(json={"name": "Sprint nowy", "start_date": "2026-12-01"}),
    ),
    Case(
        "update_sprint",
        "scrum:write",
        lambda s: _a(str(s.sprint_id), name="Zmieniona nazwa"),
        lambda s: V2Req(json={"name": "Zmieniona nazwa"}, path={"sprint_id": s.sprint_id}),
    ),
    Case("start_sprint", "scrum:write", lambda s: _a(str(s.sprint_id)), lambda s: V2Req(path={"sprint_id": s.sprint_id})),
    Case(
        "complete_sprint",
        "scrum:write",
        lambda s: _a(str(s.sprint_id)),
        lambda s: V2Req(path={"sprint_id": s.sprint_id}),
        positive=False,
    ),
    Case("create_label", "scrum:write", lambda s: _a("nowa-etykieta"), lambda s: V2Req(json={"name": "nowa-etykieta"})),
    Case("log_time", "scrum:write", lambda s: _a(s.key, 30, "2026-10-01")),
    Case("add_attachment", "scrum:write", lambda s: _a(s.key, _B64_HELLO, "plik.txt"), positive=False),
    Case("create_ticket_from_issue", "scrum:write", lambda s: _a(str(uuid.uuid4())), positive=False),
    # --- scrum:delete ---
    Case("delete_ticket", "scrum:delete", lambda s: _a(s.key), lambda s: V2Req(path=_ticket_path(s))),
    Case(
        "delete_acceptance_criterion",
        "scrum:delete",
        lambda s: _a(s.key, str(s.criterion_id)),
        lambda s: V2Req(path={"ticket_id": s.key, "criterion_id": s.criterion_id}),
    ),
    # --- wiki:read ---
    Case("list_wiki_pages", "wiki:read", lambda s: _a(), lambda s: V2Req()),
    Case("get_wiki_page", "wiki:read", lambda s: _a(str(s.page_id)), lambda s: V2Req(path={"page_id": s.page_id})),
    Case("search_wiki", "wiki:read", lambda s: _a("seed"), lambda s: V2Req(params={"query": "seed"}), positive=False),
    Case("get_wiki_config", "wiki:read", lambda s: _a(), lambda s: V2Req()),
    Case("lint_wiki", "wiki:read", lambda s: _a(), positive=False),
    Case("get_wiki_backlinks", "wiki:read", lambda s: _a(str(s.page_id)), positive=False),
    Case("list_wiki_files", "wiki:read", lambda s: _a()),
    Case("list_wiki_page_attachments", "wiki:read", lambda s: _a(str(s.page_id))),
    Case("get_wiki_attachment", "wiki:read", lambda s: _a(str(s.page_id), "brak.txt"), positive=False),
    Case("get_wiki_file", "wiki:read", lambda s: _a(str(uuid.uuid4())), positive=False),
    Case("list_blog_posts", "wiki:read", lambda s: _a(), lambda s: V2Req()),
    Case("get_blog_post", "wiki:read", lambda s: _a(str(s.page_id)), lambda s: V2Req(path={"page_id": s.page_id})),
    Case("lint_blog_post", "wiki:read", lambda s: _a(str(s.page_id)), lambda s: V2Req(path={"page_id": s.page_id})),
    # --- wiki:write ---
    Case(
        "create_wiki_page",
        "wiki:write",
        lambda s: _a("Nowa strona", "treść"),
        lambda s: V2Req(json={"title": "Nowa strona", "content": "treść"}),
    ),
    Case(
        "update_wiki_page",
        "wiki:write",
        lambda s: _a(str(s.page_id), title="Zmieniona strona"),
        lambda s: V2Req(json={"title": "Zmieniona strona"}, path={"page_id": s.page_id}),
    ),
    Case("regenerate_wiki_index", "wiki:write", lambda s: _a(), positive=False),
    Case("append_wiki_log", "wiki:write", lambda s: _a("wpis do dziennika"), positive=False),
    Case("add_wiki_file", "wiki:write", lambda s: _a(_B64_HELLO, "plik.txt"), positive=False),
    Case("add_wiki_page_attachment", "wiki:write", lambda s: _a(str(s.page_id), _B64_HELLO, "plik.txt"), positive=False),
    Case("update_wiki_file", "wiki:write", lambda s: _a(str(uuid.uuid4()), description="opis"), positive=False),
    Case(
        "set_blog_post_meta",
        "wiki:write",
        lambda s: _a(str(s.page_id), description="Nowy opis wpisu"),
        lambda s: V2Req(json={"description": "Nowy opis wpisu"}, path={"page_id": s.page_id}),
    ),
    # --- wiki:delete ---
    Case("delete_wiki_page", "wiki:delete", lambda s: _a(str(s.page_id)), lambda s: V2Req(path={"page_id": s.page_id})),
]

CASE_BY_TOOL = {case.tool: case for case in CASES}


@dataclass
class RbacEnv:
    db: AsyncSession
    client: httpx.AsyncClient
    project: Project
    seeder: ApiV2Actor
    seed: Seed
    minio: dict[str, str]
    actors: dict[str, ApiV2Actor] = field(default_factory=dict)

    async def actor(self, role: str) -> ApiV2Actor:
        if role in self.actors:
            return self.actors[role]
        actor = await make_actor(self.db, f"rbac-{role}")
        if role in _CUSTOM_ROLES:
            role_obj = Role(name=f"rbac-{role}", project_id=self.project.id, permissions=_CUSTOM_ROLES[role], is_system=False)
            self.db.add(role_obj)
            await self.db.flush()
            self.db.add(ProjectMember(project_id=self.project.id, user_id=actor.user.id, role="member", role_id=role_obj.id))
            await self.db.flush()
        else:
            await add_member(self.db, self.project, actor.user, role=role)
        self.actors[role] = actor
        return actor

    async def mcp(self, role: str, tool: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        actor = await self.actor(role)
        return await getattr(mcp_server, tool)(_mcp_ctx(actor), self.project.slug, *args, **kwargs)

    async def mcp_error(self, role: str, tool: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        with pytest.raises(ValueError) as info:
            await self.mcp(role, tool, args, kwargs)
        return str(info.value)

    async def v2(self, role: str, tool: str, req: V2Req) -> httpx.Response:
        actor = await self.actor(role)
        spec = TOOL_TO_ENDPOINT[tool]
        path = spec.path.format(project_slug=self.project.slug, **{key: str(value) for key, value in req.path.items()})
        return await self.client.request(spec.method, path, headers=actor.headers, json=req.json, params=req.params)


def _mcp_ctx(actor: ApiV2Actor) -> MagicMock:
    token = actor.headers["Authorization"].removeprefix("Bearer ")
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


@pytest.fixture
async def env(db_session, client, monkeypatch) -> RbacEnv:
    minio: dict[str, str] = {}

    def upload_markdown(project_slug: str, page_id: uuid.UUID, content: str) -> str:
        path = f"{project_slug}/{page_id}.md"
        minio[path] = content
        return path

    def delete_object(path: str) -> None:
        minio.pop(path, None)

    monkeypatch.setattr("monolynx.services.wiki.upload_markdown", upload_markdown)
    monkeypatch.setattr("monolynx.services.wiki.get_markdown", lambda path: minio[path])
    monkeypatch.setattr("monolynx.services.wiki.delete_object", delete_object)
    monkeypatch.setattr("monolynx.services.embeddings.update_page_embeddings", AsyncMock())

    @asynccontextmanager
    async def session_factory():
        yield db_session

    monkeypatch.setattr(mcp_server, "async_session_factory", session_factory)

    project = await make_project(db_session, "rbac")
    seeder = await make_actor(db_session, "rbac-seeder")
    await add_member(db_session, project, seeder.user, role="owner")

    ticket = Ticket(project_id=project.id, number=await get_next_ticket_number(project.id, db_session), title="Ticket startowy")
    db_session.add(ticket)
    await db_session.flush()
    criterion = TicketAcceptanceCriterion(ticket_id=ticket.id, description="Kryterium startowe", position=0, created_by_user_id=seeder.user.id)
    comment = TicketComment(ticket_id=ticket.id, user_id=seeder.user.id, content="Komentarz startowy")
    sprint = Sprint(project_id=project.id, name="Sprint startowy", status="planning", start_date=date(2026, 10, 1), end_date=date(2026, 10, 14))
    label = Label(project_id=project.id, name="etykieta-startowa", color="#123456")
    page_id = uuid.uuid4()
    page_path = f"{project.slug}/{page_id}.md"
    minio[page_path] = "# Treść startowa"
    page = WikiPage(
        id=page_id,
        project_id=project.id,
        title="Strona startowa",
        slug="strona-startowa",
        position=0,
        minio_path=page_path,
        created_by_id=seeder.user.id,
        last_edited_by_id=seeder.user.id,
    )
    db_session.add_all([criterion, comment, sprint, label, page])
    await db_session.flush()
    db_session.add(BlogPost(page_id=page.id, slug="strona-startowa", description="Opis startowy", tags=["startowy"]))
    await db_session.flush()

    seed = Seed(
        ticket_id=ticket.id,
        key=f"{project.code}-{ticket.number}",
        criterion_id=criterion.id,
        sprint_id=sprint.id,
        page_id=page.id,
        label_id=label.id,
    )
    return RbacEnv(db=db_session, client=client, project=project, seeder=seeder, seed=seed, minio=minio)


# ---------------------------------------------------------------------------
# Stan bazy
# ---------------------------------------------------------------------------


def _rows(result: Any) -> list[tuple[str, ...]]:
    return sorted(tuple(str(value) for value in row) for row in result.all())


async def snapshot(env: RbacEnv) -> dict[str, Any]:
    """Pełny stan zasobów projektu odczytany zapytaniami o kolumny (nie przez identity map)."""
    db = env.db
    pid = env.project.id

    async def count(model: Any, *where: Any) -> int:
        return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    return {
        "tickets": _rows(await db.execute(select(Ticket.title, Ticket.status, Ticket.priority, Ticket.sprint_id).where(Ticket.project_id == pid))),
        "criteria": _rows(
            await db.execute(
                select(TicketAcceptanceCriterion.description, TicketAcceptanceCriterion.is_completed)
                .join(Ticket, TicketAcceptanceCriterion.ticket_id == Ticket.id)
                .where(Ticket.project_id == pid)
            )
        ),
        "comments": _rows(
            await db.execute(select(TicketComment.content).join(Ticket, TicketComment.ticket_id == Ticket.id).where(Ticket.project_id == pid))
        ),
        "sprints": _rows(await db.execute(select(Sprint.name, Sprint.status, Sprint.goal).where(Sprint.project_id == pid))),
        "labels": _rows(await db.execute(select(Label.name, Label.color).where(Label.project_id == pid))),
        "pages": _rows(await db.execute(select(WikiPage.title, WikiPage.slug, WikiPage.is_public).where(WikiPage.project_id == pid))),
        "blog_posts": _rows(
            await db.execute(
                select(BlogPost.slug, BlogPost.description, BlogPost.tags, BlogPost.lang, BlogPost.author_name)
                .join(WikiPage, WikiPage.id == BlogPost.page_id)
                .where(WikiPage.project_id == pid)
            )
        ),
        "minio": dict(env.minio),
        "time_entries": await count(TimeTrackingEntry, TimeTrackingEntry.project_id == pid),
        "ticket_attachments": await count(TicketAttachment, TicketAttachment.ticket_id.in_(select(Ticket.id).where(Ticket.project_id == pid))),
        "wiki_files": await count(WikiFile, WikiFile.project_id == pid),
        "wiki_attachments": await count(WikiAttachment, WikiAttachment.wiki_page_id.in_(select(WikiPage.id).where(WikiPage.project_id == pid))),
        "wiki_llm_enabled": (await db.execute(select(Project.wiki_llm_enabled).where(Project.id == pid))).scalar_one(),
    }


def _assert_problem(response: httpx.Response, status: int, detail: str) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith(_PROBLEM_JSON)
    assert response.json()["detail"] == detail


def _expected_ok_status(tool: str) -> int:
    method = TOOL_TO_ENDPOINT[tool].method
    if method == "DELETE":
        return 204
    if method == "POST" and tool not in {"start_sprint", "complete_sprint"}:
        return 201
    return 200


_DENIAL_PARAMS = [pytest.param(case, role, id=f"{case.tool}-{role}") for case in CASES for role in _DENIED_ROLES[case.permission]]
_DENIAL_V2_PARAMS = [pytest.param(case, role, id=f"{case.tool}-{role}") for case, role in (p.values for p in _DENIAL_PARAMS) if case.v2]
_ALLOWED_PARAMS = [pytest.param(case, role, id=f"{case.tool}-{role}") for case in CASES if case.positive for role in _ALLOWED_ROLES[case.permission]]
_ALLOWED_V2_PARAMS = [pytest.param(case, role, id=f"{case.tool}-{role}") for case, role in (p.values for p in _ALLOWED_PARAMS) if case.v2]


def test_cases_cover_every_scrum_and_wiki_tool_with_a_permission():
    """Tabela obejmuje narzędzia, które mają sprawdzać uprawnienia scrum:*/wiki:*, i każdy endpoint v2 z mapy."""
    mapped_scrum_wiki = {
        name
        for name, spec in TOOL_TO_ENDPOINT.items()
        if any(part in spec.path for part in ("/tickets", "/sprints", "/board", "/burndown", "/labels", "/wiki/")) and name not in {"set_wiki_config"}
    }

    assert mapped_scrum_wiki <= set(CASE_BY_TOOL)
    assert all(case.tool in TOOL_TO_ENDPOINT for case in CASES if case.v2)
    assert all(case.permission in _DENIED_ROLES and case.permission in _ALLOWED_ROLES for case in CASES)


# ---------------------------------------------------------------------------
# Odmowa: rola bez uprawnienia
# ---------------------------------------------------------------------------


class TestDeniedInMcp:
    @pytest.mark.parametrize(("case", "role"), _DENIAL_PARAMS)
    async def test_role_without_permission_gets_value_error_and_no_state_change(self, case, role, env):
        before = await snapshot(env)
        args, kwargs = case.mcp(env.seed)

        message = await env.mcp_error(role, case.tool, args, kwargs)

        assert message == f"Brak uprawnienia {case.permission}"
        assert await snapshot(env) == before


class TestDeniedInApiV2:
    @pytest.mark.parametrize(("case", "role"), _DENIAL_V2_PARAMS)
    async def test_role_without_permission_gets_403_problem_and_no_state_change(self, case, role, env):
        assert case.v2 is not None
        before = await snapshot(env)

        response = await env.v2(role, case.tool, case.v2(env.seed))

        _assert_problem(response, 403, f"Brak uprawnienia {case.permission}")
        assert await snapshot(env) == before


# ---------------------------------------------------------------------------
# Przejście: rola z uprawnieniem nie jest blokowana
# ---------------------------------------------------------------------------


class TestAllowedInMcp:
    @pytest.mark.parametrize(("case", "role"), _ALLOWED_PARAMS)
    async def test_role_with_permission_is_not_blocked(self, case, role, env):
        args, kwargs = case.mcp(env.seed)

        result = await env.mcp(role, case.tool, args, kwargs)

        assert result is not None


class TestAllowedInApiV2:
    @pytest.mark.parametrize(("case", "role"), _ALLOWED_V2_PARAMS)
    async def test_role_with_permission_gets_success_status(self, case, role, env):
        assert case.v2 is not None

        response = await env.v2(role, case.tool, case.v2(env.seed))

        assert response.status_code == _expected_ok_status(case.tool)


# ---------------------------------------------------------------------------
# Member: zapis i odczyt tak, usuwanie nie (efekt w bazie)
# ---------------------------------------------------------------------------


class TestMemberRole:
    async def test_member_creates_ticket_comment_and_page_through_mcp(self, env):
        await env.mcp("member", "create_ticket", ("Ticket membera",), {})
        await env.mcp("member", "add_comment", (env.seed.key, "Komentarz membera"), {})
        await env.mcp("member", "update_wiki_page", (str(env.seed.page_id),), {"content": "# Zmiana membera"})

        state = await snapshot(env)
        assert "Ticket membera" in [row[0] for row in state["tickets"]]
        assert ("Komentarz membera",) in state["comments"]
        assert state["minio"][f"{env.project.slug}/{env.seed.page_id}.md"] == "# Zmiana membera"

    async def test_member_creates_ticket_comment_and_page_through_v2(self, env):
        created = await env.v2("member", "create_ticket", V2Req(json={"title": "Ticket membera"}))
        commented = await env.v2("member", "add_comment", V2Req(json={"content": "Komentarz membera"}, path=_ticket_path(env.seed)))
        updated = await env.v2("member", "update_wiki_page", V2Req(json={"content": "# Zmiana membera"}, path={"page_id": env.seed.page_id}))

        assert (created.status_code, commented.status_code, updated.status_code) == (201, 201, 200)
        state = await snapshot(env)
        assert "Ticket membera" in [row[0] for row in state["tickets"]]
        assert ("Komentarz membera",) in state["comments"]
        assert state["minio"][f"{env.project.slug}/{env.seed.page_id}.md"] == "# Zmiana membera"

    @pytest.mark.parametrize("tool", ["delete_ticket", "delete_acceptance_criterion", "delete_wiki_page"])
    async def test_member_cannot_delete_in_either_channel(self, tool, env):
        case = CASE_BY_TOOL[tool]
        assert case.v2 is not None
        before = await snapshot(env)
        args, kwargs = case.mcp(env.seed)

        message = await env.mcp_error("member", tool, args, kwargs)
        response = await env.v2("member", tool, case.v2(env.seed))

        assert message == f"Brak uprawnienia {case.permission}"
        _assert_problem(response, 403, f"Brak uprawnienia {case.permission}")
        assert await snapshot(env) == before


# ---------------------------------------------------------------------------
# Owner i admin: usuwanie działa w obu kanałach (efekt w bazie)
# ---------------------------------------------------------------------------


class TestOwnerAndAdminDelete:
    @pytest.mark.parametrize("role", ["owner", "admin"])
    @pytest.mark.parametrize("channel", ["mcp", "v2"])
    async def test_ticket_is_deleted(self, role, channel, env):
        case = CASE_BY_TOOL["delete_ticket"]
        assert case.v2 is not None

        if channel == "mcp":
            await env.mcp(role, "delete_ticket", *case.mcp(env.seed))
        else:
            assert (await env.v2(role, "delete_ticket", case.v2(env.seed))).status_code == 204

        assert (await snapshot(env))["tickets"] == []

    @pytest.mark.parametrize("role", ["owner", "admin"])
    @pytest.mark.parametrize("channel", ["mcp", "v2"])
    async def test_criterion_is_deleted(self, role, channel, env):
        case = CASE_BY_TOOL["delete_acceptance_criterion"]
        assert case.v2 is not None

        if channel == "mcp":
            await env.mcp(role, "delete_acceptance_criterion", *case.mcp(env.seed))
        else:
            assert (await env.v2(role, "delete_acceptance_criterion", case.v2(env.seed))).status_code == 204

        assert (await snapshot(env))["criteria"] == []

    @pytest.mark.parametrize("role", ["owner", "admin"])
    @pytest.mark.parametrize("channel", ["mcp", "v2"])
    async def test_wiki_page_is_deleted(self, role, channel, env):
        case = CASE_BY_TOOL["delete_wiki_page"]
        assert case.v2 is not None

        if channel == "mcp":
            await env.mcp(role, "delete_wiki_page", *case.mcp(env.seed))
        else:
            assert (await env.v2(role, "delete_wiki_page", case.v2(env.seed))).status_code == 204

        assert (await snapshot(env))["pages"] == []

    @pytest.mark.parametrize("role", ["owner", "admin"])
    async def test_sprint_lifecycle_through_both_channels(self, role, env):
        second = Sprint(project_id=env.project.id, name="Sprint drugi", status="planning", start_date=date(2026, 11, 1), end_date=date(2026, 11, 14))
        env.db.add(second)
        await env.db.flush()

        await env.mcp(role, "start_sprint", (str(env.seed.sprint_id),), {})
        await env.mcp(role, "complete_sprint", (str(env.seed.sprint_id),), {})
        started = await env.v2(role, "start_sprint", V2Req(path={"sprint_id": second.id}))
        completed = await env.v2(role, "complete_sprint", V2Req(path={"sprint_id": second.id}))

        assert (started.status_code, completed.status_code) == (200, 200)
        assert (await snapshot(env))["sprints"] == [("Sprint drugi", "completed", "None"), ("Sprint startowy", "completed", "None")]


# ---------------------------------------------------------------------------
# Odczyt zachowuje dane projektu przy roli tylko-do-odczytu
# ---------------------------------------------------------------------------


class TestReadOnlyRoleReads:
    async def test_readonly_reads_seeded_data_in_both_channels(self, env):
        ticket_text = await env.mcp("readonly", "get_ticket", (env.seed.key,), {})
        page = await env.mcp("readonly", "get_wiki_page", (str(env.seed.page_id),), {})
        ticket_response = await env.v2("readonly", "get_ticket", V2Req(path=_ticket_path(env.seed)))
        page_response = await env.v2("readonly", "get_wiki_page", V2Req(path={"page_id": env.seed.page_id}))

        assert "Ticket startowy" in ticket_text
        assert page["content"] == "# Treść startowa"
        assert ticket_response.status_code == 200
        assert ticket_response.json()["title"] == "Ticket startowy"
        assert page_response.status_code == 200
        assert page_response.json()["content"] == "# Treść startowa"


# ---------------------------------------------------------------------------
# Check przed lookupem zasobu
# ---------------------------------------------------------------------------


class TestPermissionBeforeLookup:
    @pytest.mark.parametrize("missing", ["ZZZZ-999", str(uuid.uuid4())], ids=["key", "uuid"])
    async def test_role_without_scrum_read_gets_403_not_404_for_missing_ticket(self, missing, env):
        message = await env.mcp_error("no_scrum_read", "get_ticket", (missing,), {})
        response = await env.v2("no_scrum_read", "get_ticket", V2Req(path={"ticket_id": missing}))

        assert message == "Brak uprawnienia scrum:read"
        _assert_problem(response, 403, "Brak uprawnienia scrum:read")

    async def test_member_gets_403_not_404_when_deleting_missing_ticket(self, env):
        missing = str(uuid.uuid4())

        message = await env.mcp_error("member", "delete_ticket", (missing,), {})
        response = await env.v2("member", "delete_ticket", V2Req(path={"ticket_id": missing}))

        assert message == "Brak uprawnienia scrum:delete"
        _assert_problem(response, 403, "Brak uprawnienia scrum:delete")

    async def test_member_gets_403_not_404_when_deleting_missing_wiki_page(self, env):
        missing = uuid.uuid4()

        message = await env.mcp_error("member", "delete_wiki_page", (str(missing),), {})
        response = await env.v2("member", "delete_wiki_page", V2Req(path={"page_id": missing}))

        assert message == "Brak uprawnienia wiki:delete"
        _assert_problem(response, 403, "Brak uprawnienia wiki:delete")

    async def test_role_without_wiki_read_gets_403_not_404_for_missing_page(self, env):
        missing = uuid.uuid4()

        message = await env.mcp_error("no_wiki_read", "get_wiki_page", (str(missing),), {})
        response = await env.v2("no_wiki_read", "get_wiki_page", V2Req(path={"page_id": missing}))

        assert message == "Brak uprawnienia wiki:read"
        _assert_problem(response, 403, "Brak uprawnienia wiki:read")

    async def test_role_without_scrum_write_gets_permission_error_for_malformed_sprint_id_in_mcp(self, env):
        message = await env.mcp_error("readonly", "update_sprint", ("nie-uuid",), {"name": "x"})

        assert message == "Brak uprawnienia scrum:write"

    @pytest.mark.parametrize(
        ("tool", "role", "permission", "args", "kwargs", "v2_req"),
        [
            pytest.param(
                "add_comment",
                "readonly",
                "scrum:write",
                (_MISSING_KEY, "x"),
                {},
                V2Req(json={"content": "x"}, path={"ticket_id": _MISSING_KEY}),
                id="add_comment",
            ),
            pytest.param(
                "list_comments",
                "no_scrum_read",
                "scrum:read",
                (_MISSING_KEY,),
                {},
                V2Req(path={"ticket_id": _MISSING_KEY}),
                id="list_comments",
            ),
            pytest.param(
                "update_ticket",
                "readonly",
                "scrum:write",
                (_MISSING_KEY,),
                {"title": "x"},
                V2Req(json={"title": "x"}, path={"ticket_id": _MISSING_KEY}),
                id="update_ticket",
            ),
            pytest.param(
                "list_acceptance_criteria",
                "no_scrum_read",
                "scrum:read",
                (_MISSING_KEY,),
                {},
                V2Req(path={"ticket_id": _MISSING_KEY}),
                id="list_acceptance_criteria",
            ),
            pytest.param(
                "add_acceptance_criterion",
                "readonly",
                "scrum:write",
                (_MISSING_KEY, "x"),
                {},
                V2Req(json={"description": "x"}, path={"ticket_id": _MISSING_KEY}),
                id="add_acceptance_criterion",
            ),
            pytest.param(
                "update_acceptance_criterion",
                "readonly",
                "scrum:write",
                (_MISSING_KEY, _MISSING_UUID),
                {"is_completed": True},
                V2Req(json={"is_completed": True}, path={"ticket_id": _MISSING_KEY, "criterion_id": _MISSING_UUID}),
                id="update_acceptance_criterion",
            ),
            pytest.param(
                "delete_acceptance_criterion",
                "member",
                "scrum:delete",
                (_MISSING_KEY, _MISSING_UUID),
                {},
                V2Req(path={"ticket_id": _MISSING_KEY, "criterion_id": _MISSING_UUID}),
                id="delete_acceptance_criterion",
            ),
            pytest.param(
                "update_wiki_page",
                "readonly",
                "wiki:write",
                (_MISSING_UUID,),
                {"title": "x"},
                V2Req(json={"title": "x"}, path={"page_id": _MISSING_UUID}),
                id="update_wiki_page",
            ),
            pytest.param(
                "list_wiki_page_attachments",
                "no_wiki_read",
                "wiki:read",
                (_MISSING_UUID,),
                {},
                None,
                id="list_wiki_page_attachments",
            ),
        ],
    )
    async def test_missing_resource_does_not_change_permission_denial(self, tool, role, permission, args, kwargs, v2_req, env):
        message = await env.mcp_error(role, tool, args, kwargs)

        assert message == f"Brak uprawnienia {permission}"
        if v2_req is not None:
            response = await env.v2(role, tool, v2_req)
            _assert_problem(response, 403, f"Brak uprawnienia {permission}")


class TestPermissionBeforeInputValidationInMcp:
    """MCP `add_attachment` (pusta nazwa, zły base64, pusta treść) i `log_time` (zerowy czas, zła data): odmowa `scrum:write`."""

    @pytest.mark.parametrize(
        ("tool", "args"),
        [
            pytest.param("add_attachment", ("{key}", _B64_HELLO, ""), id="add_attachment-empty-filename"),
            pytest.param("add_attachment", ("{key}", "%%%nie-base64%%%", "plik.txt"), id="add_attachment-bad-base64"),
            pytest.param("add_attachment", ("{key}", "", "plik.txt"), id="add_attachment-empty-content"),
            pytest.param("log_time", ("{key}", 0, "2026-10-01"), id="log_time-zero-duration"),
            pytest.param("log_time", ("{key}", 30, "nie-data"), id="log_time-bad-date"),
        ],
    )
    async def test_readonly_gets_permission_error_for_invalid_input(self, tool, args, env):
        before = await snapshot(env)
        resolved = tuple(env.seed.key if arg == "{key}" else arg for arg in args)

        message = await env.mcp_error("readonly", tool, resolved, {})

        assert message == "Brak uprawnienia scrum:write"
        assert await snapshot(env) == before


# ---------------------------------------------------------------------------
# Check przed parsowaniem id, walidacją wejścia i lookupem: jeden wiersz na wejście, które bez checku dałoby inny błąd
# ---------------------------------------------------------------------------

# Rola bez DOKŁADNIE sprawdzanego uprawnienia: zatrzymuje ją tylko sprawdzająca warstwa, nie wcześniejszy check innego modułu.
_ORDER_ROLE = {
    "scrum:read": "no_scrum_read",
    "scrum:write": "readonly",
    "scrum:delete": "member",
    "wiki:read": "no_wiki_read",
    "wiki:write": "readonly",
    "wiki:delete": "member",
}
_MALFORMED = "nie-uuid"


@dataclass(frozen=True)
class OrderCase:
    """Wywołanie z wejściem, które za checkiem kończy się innym błędem; `v2` jest None, gdy wejście nie dociera do handlera v2."""

    id: str
    tool: str
    permission: str
    mcp: McpCall
    v2: Callable[[Seed], V2Req] | None = None

    @property
    def role(self) -> str:
        return _ORDER_ROLE[self.permission]


def _criterion_path(s: Seed, criterion_id: str) -> dict[str, Any]:
    return {"ticket_id": s.key, "criterion_id": criterion_id}


ORDER_CASES: list[OrderCase] = [
    # --- scrum:write: walidacja tytułu, dat, priorytetów, id blokerów i sprintu ---
    OrderCase("create_ticket-blank-title", "create_ticket", "scrum:write", lambda s: _a(""), lambda s: V2Req(json={"title": ""})),
    OrderCase(
        "create_ticket-bad-due-date",
        "create_ticket",
        "scrum:write",
        lambda s: _a("Tytuł", due_date="nie-data"),
        lambda s: V2Req(json={"title": "Tytuł", "due_date": "nie-data"}),
    ),
    OrderCase(
        "create_ticket-malformed-blocker-id",
        "create_ticket",
        "scrum:write",
        lambda s: _a("Tytuł", blocked_by_ids=[_MALFORMED]),
        lambda s: V2Req(json={"title": "Tytuł", "blocked_by_ids": [_MALFORMED]}),
    ),
    OrderCase(
        "update_ticket-malformed-blocker-id",
        "update_ticket",
        "scrum:write",
        lambda s: _a(s.key, blocked_by_ids=[_MALFORMED]),
        lambda s: V2Req(json={"blocked_by_ids": [_MALFORMED]}, path=_ticket_path(s)),
    ),
    OrderCase(
        "bulk_update_tickets-invalid-status",
        "bulk_update_tickets",
        "scrum:write",
        lambda s: _a([s.key], status="nieistniejacy"),
        lambda s: V2Req(json={"ticket_ids": [s.key], "status": "nieistniejacy"}),
    ),
    OrderCase(
        "bulk_update_tickets-invalid-priority",
        "bulk_update_tickets",
        "scrum:write",
        lambda s: _a([s.key], priority="nieistniejacy"),
        lambda s: V2Req(json={"ticket_ids": [s.key], "priority": "nieistniejacy"}),
    ),
    OrderCase(
        "bulk_update_tickets-bad-due-date",
        "bulk_update_tickets",
        "scrum:write",
        lambda s: _a([s.key], due_date="nie-data"),
        lambda s: V2Req(json={"ticket_ids": [s.key], "due_date": "nie-data"}),
    ),
    OrderCase(
        "bulk_update_tickets-malformed-sprint-id",
        "bulk_update_tickets",
        "scrum:write",
        lambda s: _a([s.key], sprint_id=_MALFORMED),
        lambda s: V2Req(json={"ticket_ids": [s.key], "sprint_id": _MALFORMED}),
    ),
    # Limit 100 ids egzekwuje w v2 schemat (422 przed handlerem), więc wiersz jest tylko MCP.
    OrderCase("bulk_update_tickets-over-limit", "bulk_update_tickets", "scrum:write", lambda s: _a([s.key] * 101, status="done")),
    OrderCase(
        "add_comment-blank-content",
        "add_comment",
        "scrum:write",
        lambda s: _a(s.key, "   "),
        lambda s: V2Req(json={"content": "   "}, path=_ticket_path(s)),
    ),
    OrderCase(
        "add_acceptance_criterion-blank-description",
        "add_acceptance_criterion",
        "scrum:write",
        lambda s: _a(s.key, "   "),
        lambda s: V2Req(json={"description": "   "}, path=_ticket_path(s)),
    ),
    OrderCase(
        "update_acceptance_criterion-malformed-criterion-id",
        "update_acceptance_criterion",
        "scrum:write",
        lambda s: _a(s.key, _MALFORMED, is_completed=True),
        lambda s: V2Req(json={"is_completed": True}, path=_criterion_path(s, _MALFORMED)),
    ),
    OrderCase(
        "update_acceptance_criterion-missing-criterion",
        "update_acceptance_criterion",
        "scrum:write",
        lambda s: _a(s.key, _MISSING_UUID, is_completed=True),
        lambda s: V2Req(json={"is_completed": True}, path=_criterion_path(s, _MISSING_UUID)),
    ),
    OrderCase(
        "update_acceptance_criterion-blank-description",
        "update_acceptance_criterion",
        "scrum:write",
        lambda s: _a(s.key, str(s.criterion_id), description="   "),
        lambda s: V2Req(json={"description": "   "}, path=_criterion_path(s, str(s.criterion_id))),
    ),
    OrderCase(
        "create_sprint-blank-name",
        "create_sprint",
        "scrum:write",
        lambda s: _a("   ", "2026-12-01"),
        lambda s: V2Req(json={"name": "   ", "start_date": "2026-12-01"}),
    ),
    OrderCase(
        "create_sprint-bad-start-date",
        "create_sprint",
        "scrum:write",
        lambda s: _a("Sprint nowy", "nie-data"),
        lambda s: V2Req(json={"name": "Sprint nowy", "start_date": "nie-data"}),
    ),
    OrderCase(
        "update_sprint-malformed-id",
        "update_sprint",
        "scrum:write",
        lambda s: _a(_MALFORMED, name="Zmiana"),
        lambda s: V2Req(json={"name": "Zmiana"}, path={"sprint_id": _MALFORMED}),
    ),
    OrderCase(
        "update_sprint-missing-sprint",
        "update_sprint",
        "scrum:write",
        lambda s: _a(_MISSING_UUID, name="Zmiana"),
        lambda s: V2Req(json={"name": "Zmiana"}, path={"sprint_id": _MISSING_UUID}),
    ),
    OrderCase(
        "update_sprint-blank-name",
        "update_sprint",
        "scrum:write",
        lambda s: _a(str(s.sprint_id), name="   "),
        lambda s: V2Req(json={"name": "   "}, path={"sprint_id": s.sprint_id}),
    ),
    OrderCase(
        "update_sprint-end-before-start",
        "update_sprint",
        "scrum:write",
        lambda s: _a(str(s.sprint_id), end_date="2026-09-01"),
        lambda s: V2Req(json={"end_date": "2026-09-01"}, path={"sprint_id": s.sprint_id}),
    ),
    OrderCase(
        "start_sprint-malformed-id",
        "start_sprint",
        "scrum:write",
        lambda s: _a(_MALFORMED),
        lambda s: V2Req(path={"sprint_id": _MALFORMED}),
    ),
    OrderCase(
        "start_sprint-missing-sprint",
        "start_sprint",
        "scrum:write",
        lambda s: _a(_MISSING_UUID),
        lambda s: V2Req(path={"sprint_id": _MISSING_UUID}),
    ),
    OrderCase(
        "complete_sprint-malformed-id",
        "complete_sprint",
        "scrum:write",
        lambda s: _a(_MALFORMED),
        lambda s: V2Req(path={"sprint_id": _MALFORMED}),
    ),
    OrderCase(
        "complete_sprint-missing-sprint",
        "complete_sprint",
        "scrum:write",
        lambda s: _a(_MISSING_UUID),
        lambda s: V2Req(path={"sprint_id": _MISSING_UUID}),
    ),
    OrderCase("create_label-blank-name", "create_label", "scrum:write", lambda s: _a("   "), lambda s: V2Req(json={"name": "   "})),
    OrderCase(
        "create_label-bad-color",
        "create_label",
        "scrum:write",
        lambda s: _a("nowa-etykieta", color="czerwony"),
        lambda s: V2Req(json={"name": "nowa-etykieta", "color": "czerwony"}),
    ),
    OrderCase(
        "create_label-duplicate-name",
        "create_label",
        "scrum:write",
        lambda s: _a("etykieta-startowa"),
        lambda s: V2Req(json={"name": "etykieta-startowa"}),
    ),
    OrderCase("add_attachment-missing-ticket", "add_attachment", "scrum:write", lambda s: _a(_MISSING_KEY, _B64_HELLO, "plik.txt")),
    OrderCase("log_time-missing-ticket", "log_time", "scrum:write", lambda s: _a(_MISSING_KEY, 30, "2026-10-01")),
    OrderCase("create_ticket_from_issue-malformed-id", "create_ticket_from_issue", "scrum:write", lambda s: _a(_MALFORMED)),
    # --- scrum:read: parsowanie filtrów i id, lookup zasobu ---
    OrderCase(
        "list_tickets-malformed-sprint-id",
        "list_tickets",
        "scrum:read",
        lambda s: _a(sprint_id=_MALFORMED),
        lambda s: V2Req(params={"sprint_id": _MALFORMED}),
    ),
    OrderCase(
        "list_tickets-bad-due-date-before",
        "list_tickets",
        "scrum:read",
        lambda s: _a(due_date_before="nie-data"),
        lambda s: V2Req(params={"due_date_before": "nie-data"}),
    ),
    OrderCase(
        "list_tickets-malformed-label-id",
        "list_tickets",
        "scrum:read",
        lambda s: _a(label_id=_MALFORMED),
        lambda s: V2Req(params={"label_id": _MALFORMED}),
    ),
    OrderCase(
        "search_tickets-malformed-sprint-id",
        "search_tickets",
        "scrum:read",
        lambda s: _a(sprint_id=_MALFORMED),
        lambda s: V2Req(params={"sprint_id": _MALFORMED}),
    ),
    OrderCase(
        "search_tickets-bad-due-before",
        "search_tickets",
        "scrum:read",
        lambda s: _a(due_before="nie-data"),
        lambda s: V2Req(params={"due_before": "nie-data"}),
    ),
    OrderCase("get_ticket-malformed-key", "get_ticket", "scrum:read", lambda s: _a("nie-klucz"), lambda s: V2Req(path={"ticket_id": "nie-klucz"})),
    OrderCase(
        "get_sprint-malformed-id",
        "get_sprint",
        "scrum:read",
        lambda s: _a(_MALFORMED),
        lambda s: V2Req(path={"sprint_id": _MALFORMED}),
    ),
    OrderCase(
        "get_sprint-missing-sprint",
        "get_sprint",
        "scrum:read",
        lambda s: _a(_MISSING_UUID),
        lambda s: V2Req(path={"sprint_id": _MISSING_UUID}),
    ),
    OrderCase(
        "get_burndown-malformed-sprint-id",
        "get_burndown",
        "scrum:read",
        lambda s: _a(sprint_id=_MALFORMED),
        lambda s: V2Req(params={"sprint_id": _MALFORMED}),
    ),
    OrderCase(
        "get_burndown-missing-sprint",
        "get_burndown",
        "scrum:read",
        lambda s: _a(sprint_id=_MISSING_UUID),
        lambda s: V2Req(params={"sprint_id": _MISSING_UUID}),
    ),
    OrderCase("get_attachment-malformed-attachment-id", "get_attachment", "scrum:read", lambda s: _a(s.key, _MALFORMED)),
    OrderCase("get_attachment-missing-ticket", "get_attachment", "scrum:read", lambda s: _a(_MISSING_KEY, _MISSING_UUID)),
    # --- scrum:delete ---
    OrderCase(
        "delete_ticket-malformed-key",
        "delete_ticket",
        "scrum:delete",
        lambda s: _a("nie-klucz"),
        lambda s: V2Req(path={"ticket_id": "nie-klucz"}),
    ),
    OrderCase(
        "delete_acceptance_criterion-malformed-criterion-id",
        "delete_acceptance_criterion",
        "scrum:delete",
        lambda s: _a(s.key, _MALFORMED),
        lambda s: V2Req(path=_criterion_path(s, _MALFORMED)),
    ),
    OrderCase(
        "delete_acceptance_criterion-missing-criterion",
        "delete_acceptance_criterion",
        "scrum:delete",
        lambda s: _a(s.key, _MISSING_UUID),
        lambda s: V2Req(path=_criterion_path(s, _MISSING_UUID)),
    ),
    # --- wiki:write: tytuł, id strony i rodzica, nazwa pliku, base64, mime_type ---
    # v2 przyjmuje page_id i parent_id jako uuid.UUID, więc malformed id kończy się tam 422 przed handlerem (wiersze tylko MCP).
    OrderCase(
        "create_wiki_page-blank-title",
        "create_wiki_page",
        "wiki:write",
        lambda s: _a("   ", "treść"),
        lambda s: V2Req(json={"title": "   ", "content": "treść"}),
    ),
    OrderCase("create_wiki_page-malformed-parent-id", "create_wiki_page", "wiki:write", lambda s: _a("Tytuł", "treść", parent_id=_MALFORMED)),
    OrderCase(
        "create_wiki_page-missing-parent",
        "create_wiki_page",
        "wiki:write",
        lambda s: _a("Tytuł", "treść", parent_id=_MISSING_UUID),
        lambda s: V2Req(json={"title": "Tytuł", "content": "treść", "parent_id": _MISSING_UUID}),
    ),
    OrderCase("update_wiki_page-malformed-id", "update_wiki_page", "wiki:write", lambda s: _a(_MALFORMED, title="Zmiana")),
    OrderCase(
        "add_wiki_page_attachment-blank-filename",
        "add_wiki_page_attachment",
        "wiki:write",
        lambda s: _a(str(s.page_id), _B64_HELLO, ""),
    ),
    OrderCase(
        "add_wiki_page_attachment-bad-base64",
        "add_wiki_page_attachment",
        "wiki:write",
        lambda s: _a(str(s.page_id), "%%%nie-base64%%%", "plik.txt"),
    ),
    OrderCase("add_wiki_page_attachment-empty-content", "add_wiki_page_attachment", "wiki:write", lambda s: _a(str(s.page_id), "", "plik.txt")),
    OrderCase(
        "add_wiki_page_attachment-bad-mime-type",
        "add_wiki_page_attachment",
        "wiki:write",
        lambda s: _a(str(s.page_id), _B64_HELLO, "plik.txt", mime_type="zly mime"),
    ),
    OrderCase(
        "add_wiki_page_attachment-malformed-page-id", "add_wiki_page_attachment", "wiki:write", lambda s: _a(_MALFORMED, _B64_HELLO, "plik.txt")
    ),
    OrderCase("add_wiki_page_attachment-missing-page", "add_wiki_page_attachment", "wiki:write", lambda s: _a(_MISSING_UUID, _B64_HELLO, "plik.txt")),
    OrderCase("add_wiki_file-blank-filename", "add_wiki_file", "wiki:write", lambda s: _a(_B64_HELLO, "")),
    OrderCase("add_wiki_file-bad-base64", "add_wiki_file", "wiki:write", lambda s: _a("%%%nie-base64%%%", "plik.txt")),
    OrderCase("add_wiki_file-empty-content", "add_wiki_file", "wiki:write", lambda s: _a("", "plik.txt")),
    OrderCase("add_wiki_file-bad-mime-type", "add_wiki_file", "wiki:write", lambda s: _a(_B64_HELLO, "plik.txt", mime_type="zly mime")),
    OrderCase("update_wiki_file-malformed-id", "update_wiki_file", "wiki:write", lambda s: _a(_MALFORMED, description="opis")),
    OrderCase("set_blog_post_meta-malformed-id", "set_blog_post_meta", "wiki:write", lambda s: _a(_MALFORMED, description="Zmiana")),
    OrderCase(
        "set_blog_post_meta-malformed-translation-of-id",
        "set_blog_post_meta",
        "wiki:write",
        lambda s: _a(str(s.page_id), description="Zmiana", translation_of_id=_MALFORMED),
    ),
    OrderCase(
        "set_blog_post_meta-malformed-date",
        "set_blog_post_meta",
        "wiki:write",
        lambda s: _a(str(s.page_id), description="Zmiana", last_verified_at="31/12/2026"),
    ),
    OrderCase(
        "set_blog_post_meta-invalid-lang",
        "set_blog_post_meta",
        "wiki:write",
        lambda s: _a(str(s.page_id), lang="de"),
        lambda s: V2Req(json={"lang": "de"}, path={"page_id": s.page_id}),
    ),
    OrderCase(
        "set_blog_post_meta-missing-page",
        "set_blog_post_meta",
        "wiki:write",
        lambda s: _a(_MISSING_UUID, description="Zmiana"),
        lambda s: V2Req(json={"description": "Zmiana"}, path={"page_id": _MISSING_UUID}),
    ),
    # --- wiki:read ---
    OrderCase("get_wiki_page-malformed-id", "get_wiki_page", "wiki:read", lambda s: _a(_MALFORMED)),
    OrderCase("list_wiki_page_attachments-malformed-page-id", "list_wiki_page_attachments", "wiki:read", lambda s: _a(_MALFORMED)),
    OrderCase("get_wiki_attachment-malformed-page-id", "get_wiki_attachment", "wiki:read", lambda s: _a(_MALFORMED, "plik.txt")),
    OrderCase("get_wiki_file-malformed-id", "get_wiki_file", "wiki:read", lambda s: _a(_MALFORMED)),
    OrderCase("get_blog_post-malformed-id", "get_blog_post", "wiki:read", lambda s: _a(_MALFORMED)),
    OrderCase("lint_blog_post-malformed-id", "lint_blog_post", "wiki:read", lambda s: _a(_MALFORMED)),
    OrderCase(
        "lint_blog_post-missing-page", "lint_blog_post", "wiki:read", lambda s: _a(_MISSING_UUID), lambda s: V2Req(path={"page_id": _MISSING_UUID})
    ),
    OrderCase(
        "get_blog_post-missing-page", "get_blog_post", "wiki:read", lambda s: _a(_MISSING_UUID), lambda s: V2Req(path={"page_id": _MISSING_UUID})
    ),
    OrderCase(
        "list_blog_posts-invalid-status",
        "list_blog_posts",
        "wiki:read",
        lambda s: _a(status="archived"),
        lambda s: V2Req(params={"status": "archived"}),
    ),
    # --- wiki:delete ---
    OrderCase("delete_wiki_page-malformed-id", "delete_wiki_page", "wiki:delete", lambda s: _a(_MALFORMED)),
]

_ORDER_PARAMS = [pytest.param(case, id=case.id) for case in ORDER_CASES]
_ORDER_V2_PARAMS = [pytest.param(case, id=case.id) for case in ORDER_CASES if case.v2]


class TestPermissionBeforeParsingValidationAndLookup:
    """Rola bez sprawdzanego uprawnienia dostaje pełny tekst odmowy zamiast błędu parsowania, walidacji albo lookupu (stan bazy bez zmian)."""

    @pytest.mark.parametrize("case", _ORDER_PARAMS)
    async def test_mcp_returns_permission_denial(self, case, env):
        before = await snapshot(env)
        args, kwargs = case.mcp(env.seed)

        message = await env.mcp_error(case.role, case.tool, args, kwargs)

        assert message == f"Brak uprawnienia {case.permission}"
        assert await snapshot(env) == before

    @pytest.mark.parametrize("case", _ORDER_V2_PARAMS)
    async def test_v2_returns_403_with_permission_denial(self, case, env):
        assert case.v2 is not None
        before = await snapshot(env)

        response = await env.v2(case.role, case.tool, case.v2(env.seed))

        _assert_problem(response, 403, f"Brak uprawnienia {case.permission}")
        assert await snapshot(env) == before


class TestPermissionBeforeLlmWikiGateAndInput:
    """Flaga `wiki_llm_enabled` włączona: odmowa wyprzedza walidację wejścia i lookup (przy wyłączonej fladze dowodzi tego tabela `CASES`)."""

    @pytest.mark.parametrize(
        ("tool", "role", "permission", "args", "kwargs"),
        [
            pytest.param("lint_wiki", "no_wiki_read", "wiki:read", (), {}, id="lint_wiki"),
            pytest.param("get_wiki_backlinks", "no_wiki_read", "wiki:read", (_MALFORMED,), {}, id="get_wiki_backlinks-malformed-page-id"),
            pytest.param("get_wiki_backlinks", "no_wiki_read", "wiki:read", (_MISSING_UUID,), {}, id="get_wiki_backlinks-missing-page"),
            pytest.param("regenerate_wiki_index", "readonly", "wiki:write", (), {}, id="regenerate_wiki_index"),
            pytest.param("append_wiki_log", "readonly", "wiki:write", ("   ",), {}, id="append_wiki_log-blank-entry"),
            pytest.param("append_wiki_log", "readonly", "wiki:write", ("wpis do dziennika",), {}, id="append_wiki_log"),
        ],
    )
    async def test_mcp_returns_permission_denial_with_flag_enabled(self, tool, role, permission, args, kwargs, env):
        env.project.wiki_llm_enabled = True
        await env.db.flush()
        before = await snapshot(env)

        message = await env.mcp_error(role, tool, args, kwargs)

        assert message == f"Brak uprawnienia {permission}"
        assert await snapshot(env) == before
        assert before["wiki_llm_enabled"] is True


# ---------------------------------------------------------------------------
# v2: scrum:write bez scrum:read nie zostawia zapisu (odczyt po zapisie sprawdzony przed zapisem)
# ---------------------------------------------------------------------------


class TestWriteWithoutReadInApiV2:
    @pytest.mark.parametrize("tool", ["create_ticket", "update_ticket", "start_sprint", "complete_sprint"])
    async def test_write_endpoint_that_reads_back_denies_before_writing(self, tool, env):
        case = CASE_BY_TOOL[tool]
        assert case.v2 is not None
        before = await snapshot(env)

        response = await env.v2("write_no_read", tool, case.v2(env.seed))

        _assert_problem(response, 403, "Brak uprawnienia scrum:read")
        assert await snapshot(env) == before

    @pytest.mark.parametrize("tool", ["create_ticket", "update_ticket", "start_sprint", "complete_sprint"])
    async def test_role_without_read_and_write_gets_write_denial_first_in_both_channels(self, tool, env):
        case = CASE_BY_TOOL[tool]
        assert case.v2 is not None
        before = await snapshot(env)
        args, kwargs = case.mcp(env.seed)

        message = await env.mcp_error("no_scrum_read", tool, args, kwargs)
        response = await env.v2("no_scrum_read", tool, case.v2(env.seed))

        assert message == "Brak uprawnienia scrum:write"
        _assert_problem(response, 403, "Brak uprawnienia scrum:write")
        assert await snapshot(env) == before

    async def test_mcp_writes_without_read_where_v2_denies(self, env):
        """Różnica kontraktu z mapy: MCP nie czyta zasobu po zapisie, więc `scrum:write` wystarcza."""
        created = await env.mcp("write_no_read", "create_ticket", ("Ticket bez odczytu",), {})
        updated = await env.mcp("write_no_read", "update_ticket", (env.seed.key,), {"title": "Zmiana bez odczytu"})
        started = await env.mcp("write_no_read", "start_sprint", (str(env.seed.sprint_id),), {})
        completed = await env.mcp("write_no_read", "complete_sprint", (str(env.seed.sprint_id),), {})

        assert created["title"] == "Ticket bez odczytu"
        assert updated["title"] == "Zmiana bez odczytu"
        assert (started["message"], completed["message"]) == ("Sprint rozpoczety", "Sprint zakonczony")
        state = await snapshot(env)
        assert [row[0] for row in state["tickets"]] == ["Ticket bez odczytu", "Zmiana bez odczytu"]
        assert state["sprints"] == [("Sprint startowy", "completed", "None")]

    async def test_role_with_write_only_can_still_write_endpoints_that_do_not_read_back(self, env):
        response = await env.v2("write_no_read", "add_comment", V2Req(json={"content": "Komentarz bez odczytu"}, path=_ticket_path(env.seed)))

        assert response.status_code == 201
        assert ("Komentarz bez odczytu",) in (await snapshot(env))["comments"]


# ---------------------------------------------------------------------------
# is_public wymaga wiki:write (jak formularz dashboardu)
# ---------------------------------------------------------------------------


class TestIsPublicRequiresWikiWrite:
    async def test_readonly_cannot_create_public_page_in_mcp(self, env):
        before = await snapshot(env)

        message = await env.mcp_error("readonly", "create_wiki_page", ("Publiczna", "treść"), {"is_public": True})

        assert message == "Brak uprawnienia wiki:write"
        assert await snapshot(env) == before

    async def test_readonly_cannot_create_public_page_in_v2(self, env):
        before = await snapshot(env)

        response = await env.v2("readonly", "create_wiki_page", V2Req(json={"title": "Publiczna", "content": "treść", "is_public": True}))

        _assert_problem(response, 403, "Brak uprawnienia wiki:write")
        assert await snapshot(env) == before

    async def test_readonly_cannot_publish_existing_page_in_mcp(self, env):
        message = await env.mcp_error("readonly", "update_wiki_page", (str(env.seed.page_id),), {"is_public": True})

        assert message == "Brak uprawnienia wiki:write"
        assert await self._is_public(env) is False

    async def test_readonly_cannot_publish_existing_page_in_v2(self, env):
        response = await env.v2("readonly", "update_wiki_page", V2Req(json={"is_public": True}, path={"page_id": env.seed.page_id}))

        _assert_problem(response, 403, "Brak uprawnienia wiki:write")
        assert await self._is_public(env) is False

    @pytest.mark.parametrize("role", ["member", "admin"])
    async def test_role_with_wiki_write_publishes_page_in_mcp(self, role, env):
        created = await env.mcp(role, "create_wiki_page", ("Publiczna", "treść"), {"is_public": True})
        await env.mcp(role, "update_wiki_page", (str(env.seed.page_id),), {"is_public": True})

        assert created["is_public"] is True
        assert await self._is_public(env) is True
        assert ("Publiczna", "publiczna", "True") in (await snapshot(env))["pages"]

    @pytest.mark.parametrize("role", ["member", "admin"])
    async def test_role_with_wiki_write_publishes_page_in_v2(self, role, env):
        created = await env.v2(role, "create_wiki_page", V2Req(json={"title": "Publiczna", "content": "treść", "is_public": True}))
        updated = await env.v2(role, "update_wiki_page", V2Req(json={"is_public": True}, path={"page_id": env.seed.page_id}))

        assert (created.status_code, updated.status_code) == (201, 200)
        assert created.json()["is_public"] is True
        assert await self._is_public(env) is True
        assert ("Publiczna", "publiczna", "True") in (await snapshot(env))["pages"]

    @staticmethod
    async def _is_public(env: RbacEnv) -> bool:
        return (await env.db.execute(select(WikiPage.is_public).where(WikiPage.id == env.seed.page_id))).scalar_one()


class TestDashboardWikiFormRequiresWikiWrite:
    """Ta sama reguła w dashboardzie: formularz wiki z `is_public` wymaga `wiki:write`."""

    @staticmethod
    async def _login(env: RbacEnv, role: str) -> None:
        actor = await env.actor(role)
        response = await env.client.post("/auth/login", data={"email": actor.user.email, "password": "testpass123"}, follow_redirects=False)
        assert response.status_code == 303

    async def test_readonly_gets_403_and_no_page_is_created(self, env):
        before = await snapshot(env)
        await self._login(env, "readonly")

        response = await env.client.post(
            f"/dashboard/{env.project.slug}/wiki/pages/create",
            data={"title": "Publiczna z formularza", "content": "treść", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        assert response.status_code == 403
        assert response.json() == {"detail": "Brak uprawnienia wiki:write"}
        assert await snapshot(env) == before

    async def test_member_creates_public_page(self, env):
        await self._login(env, "member")

        response = await env.client.post(
            f"/dashboard/{env.project.slug}/wiki/pages/create",
            data={"title": "Publiczna z formularza", "content": "treść", "position": "0", "is_public": "on"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert ("Publiczna z formularza", "publiczna-z-formularza", "True") in (await snapshot(env))["pages"]
