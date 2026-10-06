"""Testy kontraktowe parytetu MCP <-> API v2 (MON-131).

Cel: refactor, który wyciągnął logikę do `services/`, pozwala dwóm frontendom (narzędzia
MCP w `mcp_server.py` i endpointy `/api/v2`) wołać tę samą warstwę, więc rozjazd (pole
obecne w jednym kanale i brakujące w drugim, inny kod błędu, pominięty commit) ma wychodzić
w testach, nie u użytkownika. Plik zawiera pięć grup testów:

1. kompletność mapy `tool_endpoint_map.py` względem narzędzi zarejestrowanych w MCP oraz
   względem tras v2 (`find_coverage_problems`, `find_missing_routes` są sprawdzane także na
   syntetycznych zbiorach, co dowodzi, że test pada przy nowym narzędziu bez wpisu),
2. parytet 49 zmapowanych narzędzi (`SCENARIOS`, jeden scenariusz na narzędzie),
3. jawne pinowanie znanych różnic kontraktu (`known_differences` w mapie),
4. odrzucenie pustego tytułu strony wiki przez oba kanały (rozjazd wykryty w MON-131,
   naprawiony guardem w `api/v2/wiki.py`),
5. uprawnienia i członkostwo: rola bez `settings:write`/`users:write`/`settings:delete`/
   `users:delete` oraz użytkownik spoza projektu dostają `ValueError` w MCP i 403 w v2. Uprawnienia
   `scrum:*` i `wiki:*` (member, rola tylko-do-odczytu, owner i admin) pokrywa osobno
   `test_rbac_scrum_wiki_channels.py`; scenariusze parytetu biegną jako owner, który ma wszystkie,
6. graf Połączeń (MON-248): 5 odczytów mapowanych na `/graph/*`; Neo4j nie działa w testach,
   więc funkcje `monolynx.services.graph` są podmieniane jednym zestawem mocków (`_graph_backend`)
   dla obu kanałów, a klasa `TestGraphChannelErrors` porównuje wyłączony graf i brak `connections:read`.

Ten sam token (`UserApiToken` z `generate_api_token()`) obsługuje oba kanały: jako `Bearer`
w nagłówku v2 i w zamockowanym `Context` MCP. `mcp_server.async_session_factory` jest
podmieniona na fabrykę zwracającą sesję testową, więc oba kanały widzą te same dane w jednej
transakcji z rollbackiem. Skutek: testy tego pliku NIE dowodzą commita (`db-commit.md`,
MON-71); trwałość na osobnych połączeniach sprawdza `test_api_v2_persistence.py`.

Zapisy porównuje się na dwóch osobnych obiektach (jeden zapis przez MCP, drugi przez v2) i
czyta po zapisie zapytaniem o kolumny, nie przez obiekty z identity map. Sześć narzędzi MCP
zwraca tekst (`list_tickets`, `get_ticket`, `get_board`, `list_acceptance_criteria`,
`get_sprint`, `list_wiki_pages`); ich wartości wyciągają parsery `_parse_*`. Trzy narzędzia grafu
zwracają Arrow DSL (`get_graph_node`, `query_graph`, `find_graph_path`); parytet z v2 polega na tym,
że `format_graph_dsl(<JSON z v2>)` jest identyczny z tekstem MCP.

MinIO, embeddingi i wysyłka maili są zamockowane (`side_effects`); MinIO jest słownikiem
w pamięci, więc treść zapisana przez jeden kanał jest czytelna przez drugi.
"""

from __future__ import annotations

import re
import secrets
import string
import uuid
from collections.abc import Awaitable, Callable, Iterable, Iterator, Mapping
from contextlib import ExitStack, asynccontextmanager, contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx import mcp_server
from monolynx.constants import LABEL_COLOR_PALETTE
from monolynx.models.blog_post import BlogPost
from monolynx.models.issue import Issue
from monolynx.models.label import Label, TicketLabel
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.ticket_attachment import TicketAttachment
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.ticket_dependency import TicketDependency
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services import graph as graph_service
from monolynx.services import ticket_dependencies
from monolynx.services.ticket_numbering import get_next_ticket_number
from tests.integration.api_v2.conftest import ApiV2Actor, add_member, make_actor, make_owner_project, make_project
from tests.integration.tool_endpoint_map import INTENTIONALLY_UNMAPPED, TOOL_TO_ENDPOINT, ToolEndpointSpec

pytestmark = pytest.mark.integration

_DASH = "\u2014"
_ARROW = "→"
_PROBLEM_JSON = "application/problem+json"

# Narzędzia zapisujące (`is_write`) ustalone w tickecie MON-131; test `test_is_write_flags_match_contract`
# pilnuje, że mapa i ta lista się nie rozjeżdżają, bo testy trwałości filtrują mapę po `is_write`.
_WRITE_TOOLS = frozenset(
    {
        "create_project",
        "update_project",
        "delete_project",
        "invite_member",
        "remove_member",
        "create_label",
        "create_ticket",
        "update_ticket",
        "delete_ticket",
        "bulk_update_tickets",
        "add_comment",
        "add_acceptance_criterion",
        "update_acceptance_criterion",
        "delete_acceptance_criterion",
        "create_sprint",
        "update_sprint",
        "start_sprint",
        "complete_sprint",
        "create_wiki_page",
        "update_wiki_page",
        "delete_wiki_page",
        "set_wiki_config",
        "set_blog_post_meta",
    }
)

_PLACEHOLDER_NAMES = frozenset({"project_slug", "ticket_id", "criterion_id", "sprint_id", "page_id", "member_id", "node_id"})

_GRAPH_READ_TOOLS = ("list_graph_nodes", "get_graph_node", "query_graph", "find_graph_path", "get_graph_stats")
_GRAPH_WRITE_TOOLS = (
    "create_graph_node",
    "delete_graph_node",
    "create_graph_edge",
    "delete_graph_edge",
    "bulk_create_graph_nodes",
    "bulk_create_graph_edges",
    "replace_graph",
)
_GRAPH_DISABLED = "Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)"


# ---------------------------------------------------------------------------
# Kompletność mapy
# ---------------------------------------------------------------------------


def find_coverage_problems(tool_names: Iterable[str], mapped: Mapping[str, object], unmapped: Mapping[str, str]) -> list[str]:
    """Lista problemów pokrycia: narzędzie bez wpisu, wpis w obu słownikach, wpis na nieistniejące
    narzędzie i pusty powód w `unmapped`. Pusta lista = mapa kompletna."""
    names = set(tool_names)
    problems: list[str] = []
    for name in sorted(names - mapped.keys() - unmapped.keys()):
        problems.append(f"narzędzie {name} nie ma wpisu ani w TOOL_TO_ENDPOINT, ani w INTENTIONALLY_UNMAPPED")
    for name in sorted(mapped.keys() & unmapped.keys()):
        problems.append(f"narzędzie {name} występuje w obu słownikach")
    for name in sorted((mapped.keys() | unmapped.keys()) - names):
        problems.append(f"wpis {name} wskazuje nieistniejące narzędzie MCP")
    for name, reason in sorted(unmapped.items()):
        if not reason.strip():
            problems.append(f"narzędzie {name} nie ma powodu w INTENTIONALLY_UNMAPPED")
    return problems


def find_missing_routes(specs: Mapping[str, ToolEndpointSpec], openapi_paths: Mapping[str, Mapping[str, object]]) -> list[str]:
    """Wpisy mapy, których (metoda, ścieżka) nie ma wśród tras zwróconych przez OpenAPI v2."""
    problems: list[str] = []
    for name, spec in sorted(specs.items()):
        methods = openapi_paths.get(spec.path)
        if methods is None:
            problems.append(f"{name}: ścieżka {spec.path} nie istnieje w API v2")
        elif spec.method.lower() not in methods:
            problems.append(f"{name}: ścieżka {spec.path} nie obsługuje metody {spec.method}")
    return problems


class TestMapCompleteness:
    async def test_every_mcp_tool_has_exactly_one_entry(self):
        tools = await mcp_server.mcp.list_tools()

        problems = find_coverage_problems([t.name for t in tools], TOOL_TO_ENDPOINT, INTENTIONALLY_UNMAPPED)

        assert problems == []
        assert len(TOOL_TO_ENDPOINT) == 49
        assert len(INTENTIONALLY_UNMAPPED) == 74
        assert all(reason.strip() for reason in INTENTIONALLY_UNMAPPED.values())

    def test_new_tool_without_entry_is_reported(self):
        problems = find_coverage_problems(["list_projects", "brand_new_tool"], {"list_projects": object()}, {})

        assert problems == ["narzędzie brand_new_tool nie ma wpisu ani w TOOL_TO_ENDPOINT, ani w INTENTIONALLY_UNMAPPED"]

    def test_tool_in_both_dictionaries_is_reported(self):
        problems = find_coverage_problems(["list_projects"], {"list_projects": object()}, {"list_projects": "powód"})

        assert problems == ["narzędzie list_projects występuje w obu słownikach"]

    def test_entry_for_removed_tool_is_reported(self):
        problems = find_coverage_problems(["list_projects"], {"list_projects": object(), "removed_tool": object()}, {})

        assert problems == ["wpis removed_tool wskazuje nieistniejące narzędzie MCP"]

    def test_blank_reason_is_reported(self):
        problems = find_coverage_problems(["install_monolynx_skills"], {}, {"install_monolynx_skills": "   "})

        assert problems == ["narzędzie install_monolynx_skills nie ma powodu w INTENTIONALLY_UNMAPPED"]

    async def test_every_mapped_endpoint_exists_in_v2_routes(self, client):
        response = await client.get("/api/v2/openapi.json")
        assert response.status_code == 200

        problems = find_missing_routes(TOOL_TO_ENDPOINT, response.json()["paths"])

        assert problems == []

    def test_entry_pointing_to_missing_route_is_reported(self):
        specs = {
            "gone_path": ToolEndpointSpec("GET", "/api/v2/projects/{project_slug}/nie-ma", is_write=False),
            "wrong_method": ToolEndpointSpec("DELETE", "/api/v2/projects", is_write=True),
            "fine": ToolEndpointSpec("GET", "/api/v2/projects", is_write=False),
        }
        paths = {"/api/v2/projects": {"get": {}, "post": {}}}

        problems = find_missing_routes(specs, paths)

        assert problems == [
            "gone_path: ścieżka /api/v2/projects/{project_slug}/nie-ma nie istnieje w API v2",
            "wrong_method: ścieżka /api/v2/projects nie obsługuje metody DELETE",
        ]

    def test_is_write_flags_match_contract(self):
        assert {name for name, spec in TOOL_TO_ENDPOINT.items() if spec.is_write} == _WRITE_TOOLS

    def test_graph_reads_are_mapped_and_graph_writes_stay_unmapped_with_sync_reason(self):
        assert set(_GRAPH_READ_TOOLS) <= TOOL_TO_ENDPOINT.keys()
        assert all(not TOOL_TO_ENDPOINT[name].is_write for name in _GRAPH_READ_TOOLS)
        assert set(_GRAPH_WRITE_TOOLS) <= INTENTIONALLY_UNMAPPED.keys()
        assert set(_GRAPH_WRITE_TOOLS).isdisjoint(TOOL_TO_ENDPOINT)
        assert all("sync_graph" in INTENTIONALLY_UNMAPPED[name] for name in _GRAPH_WRITE_TOOLS)

    def test_paths_use_contract_placeholders_only(self):
        for name, spec in TOOL_TO_ENDPOINT.items():
            placeholders = set(re.findall(r"\{(\w+)\}", spec.path))
            assert placeholders <= _PLACEHOLDER_NAMES, f"{name}: nieznany placeholder w {spec.path}"
            assert spec.method in {"GET", "POST", "PATCH", "DELETE"}, name


# ---------------------------------------------------------------------------
# Środowisko parytetu
# ---------------------------------------------------------------------------


@dataclass
class SideEffects:
    minio: dict[str, str]
    deleted_paths: list[str]
    mcp_mail: MagicMock
    v2_mail: MagicMock


@pytest.fixture
def side_effects(monkeypatch) -> SideEffects:
    """MinIO jako słownik w pamięci, embeddingi bez efektu, maile jako spy (osobno dla MCP i v2)."""
    minio: dict[str, str] = {}
    deleted_paths: list[str] = []

    def upload_markdown(project_slug: str, page_id: uuid.UUID, content: str) -> str:
        path = f"{project_slug}/{page_id}.md"
        minio[path] = content
        return path

    def get_markdown(path: str) -> str:
        return minio[path]

    def delete_object(path: str) -> None:
        deleted_paths.append(path)
        minio.pop(path, None)

    mcp_mail = MagicMock()
    v2_mail = MagicMock()
    monkeypatch.setattr("monolynx.services.wiki.upload_markdown", upload_markdown)
    monkeypatch.setattr("monolynx.services.wiki.get_markdown", get_markdown)
    monkeypatch.setattr("monolynx.services.wiki.delete_object", delete_object)
    monkeypatch.setattr("monolynx.services.embeddings.update_page_embeddings", AsyncMock())
    monkeypatch.setattr("monolynx.mcp_server.send_invitation_email", mcp_mail)
    monkeypatch.setattr("monolynx.api.v2.projects.send_invitation_email", v2_mail)
    return SideEffects(minio=minio, deleted_paths=deleted_paths, mcp_mail=mcp_mail, v2_mail=v2_mail)


def _mcp_ctx(token: str) -> MagicMock:
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _token_of(actor: ApiV2Actor) -> str:
    return actor.headers["Authorization"].removeprefix("Bearer ")


async def call_mcp(token: str, tool: str, *args: Any, **kwargs: Any) -> Any:
    return await getattr(mcp_server, tool)(_mcp_ctx(token), *args, **kwargs)


async def call_mcp_error(token: str, tool: str, *args: Any, **kwargs: Any) -> str:
    with pytest.raises(ValueError) as info:
        await call_mcp(token, tool, *args, **kwargs)
    return str(info.value)


@dataclass
class ParityEnv:
    """Jeden użytkownik (owner projektu `project`) i jeden token dla obu kanałów."""

    db: AsyncSession
    client: httpx.AsyncClient
    actor: ApiV2Actor
    project: Project
    fx: SideEffects

    @property
    def token(self) -> str:
        return _token_of(self.actor)

    @property
    def slug(self) -> str:
        return self.project.slug

    async def mcp(self, tool: str, *args: Any, **kwargs: Any) -> Any:
        return await call_mcp(self.token, tool, *args, **kwargs)

    async def mcp_error(self, tool: str, *args: Any, **kwargs: Any) -> str:
        return await call_mcp_error(self.token, tool, *args, **kwargs)

    async def v2(
        self,
        tool: str,
        *,
        slug: str | None = None,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        **path_params: Any,
    ) -> httpx.Response:
        return await call_v2(self.client, tool, self.actor.headers, slug or self.slug, json=json, params=params, **path_params)


async def call_v2(
    client: httpx.AsyncClient,
    tool: str,
    headers: Mapping[str, str],
    slug: str,
    *,
    json: Any = None,
    params: Mapping[str, Any] | None = None,
    **path_params: Any,
) -> httpx.Response:
    """Woła endpoint zmapowany dla `tool`: metoda i ścieżka pochodzą z `TOOL_TO_ENDPOINT`."""
    spec = TOOL_TO_ENDPOINT[tool]
    path = spec.path.format(project_slug=slug, **{key: str(value) for key, value in path_params.items()})
    return await client.request(spec.method, path, headers=dict(headers), json=json, params=params)


@pytest.fixture
async def env(db_session, client, monkeypatch, side_effects) -> ParityEnv:
    actor, project = await make_owner_project(db_session, "parity")

    @asynccontextmanager
    async def session_factory():
        yield db_session

    monkeypatch.setattr(mcp_server, "async_session_factory", session_factory)
    return ParityEnv(db=db_session, client=client, actor=actor, project=project, fx=side_effects)


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _ts(minutes: int) -> datetime:
    return datetime(2026, 9, 1, 12, 0, tzinfo=UTC) + timedelta(minutes=minutes)


def _assert_problem(response: httpx.Response, status: int, detail: str) -> None:
    assert response.status_code == status
    assert response.headers["content-type"].startswith(_PROBLEM_JSON)
    assert response.json()["detail"] == detail


# ---------------------------------------------------------------------------
# Seedowanie (ORM, bez wołania testowanych kanałów)
# ---------------------------------------------------------------------------


async def _new_user(env: ParityEnv, tag: str, *, first_name: str = "", last_name: str = "") -> User:
    user = User(email=f"parity-{tag}-{uuid.uuid4().hex[:8]}@example.com", password_hash=None, first_name=first_name, last_name=last_name)
    env.db.add(user)
    await env.db.flush()
    return user


async def _extra_project(env: ParityEnv, tag: str, role: str = "owner") -> Project:
    project = await make_project(env.db, tag)
    await add_member(env.db, project, env.actor.user, role=role)
    return project


async def _sprint(
    env: ParityEnv,
    name: str,
    *,
    project: Project | None = None,
    status: str = "planning",
    goal: str | None = None,
    start: date = date(2026, 10, 1),
    end: date | None = date(2026, 10, 14),
    created_at: datetime | None = None,
) -> Sprint:
    extra: dict[str, Any] = {"created_at": created_at} if created_at else {}
    sprint = Sprint(project_id=(project or env.project).id, name=name, status=status, goal=goal, start_date=start, end_date=end, **extra)
    env.db.add(sprint)
    await env.db.flush()
    return sprint


async def _ticket(env: ParityEnv, title: str, *, project: Project | None = None, **fields: Any) -> Ticket:
    target = project or env.project
    number = await get_next_ticket_number(target.id, env.db)
    ticket = Ticket(project_id=target.id, number=number, title=title, **fields)
    env.db.add(ticket)
    await env.db.flush()
    return ticket


def _key(project: Project, ticket: Ticket) -> str:
    return f"{project.code}-{ticket.number}"


async def _label(env: ParityEnv, name: str, color: str = "#123456", project: Project | None = None) -> Label:
    label = Label(project_id=(project or env.project).id, name=name, color=color)
    env.db.add(label)
    await env.db.flush()
    return label


async def _tag(env: ParityEnv, ticket: Ticket, *labels: Label) -> None:
    for label in labels:
        env.db.add(TicketLabel(ticket_id=ticket.id, label_id=label.id))
    await env.db.flush()


async def _wiki_page(
    env: ParityEnv,
    title: str,
    slug: str,
    content: str = "# Tresc",
    *,
    project: Project | None = None,
    parent: WikiPage | None = None,
    position: int = 0,
    is_ai_touched: bool = False,
    created_by: User | None = None,
) -> WikiPage:
    target = project or env.project
    page_id = uuid.uuid4()
    path = f"{target.slug}/{page_id}.md"
    env.fx.minio[path] = content
    author = created_by or env.actor.user
    page = WikiPage(
        id=page_id,
        project_id=target.id,
        parent_id=parent.id if parent else None,
        title=title,
        slug=slug,
        position=position,
        minio_path=path,
        is_ai_touched=is_ai_touched,
        created_by_id=author.id,
        last_edited_by_id=env.actor.user.id,
    )
    env.db.add(page)
    await env.db.flush()
    return page


# ---------------------------------------------------------------------------
# Odczyt stanu bazy po zapisie (zapytania o kolumny, nie identity map)
# ---------------------------------------------------------------------------


async def _ticket_state(env: ParityEnv, ticket_id: uuid.UUID | str) -> dict[str, Any]:
    ticket_uuid = uuid.UUID(str(ticket_id))
    db = env.db
    row = (
        await db.execute(
            select(
                Ticket.title,
                Ticket.description,
                Ticket.status,
                Ticket.priority,
                Ticket.story_points,
                Ticket.sprint_id,
                Ticket.assignee_id,
                Ticket.due_date,
                Ticket.created_via_ai,
                Ticket.spec_page_id,
            ).where(Ticket.id == ticket_uuid)
        )
    ).one()
    state = dict(row._mapping)
    state["labels"] = set((await db.execute(select(TicketLabel.label_id).where(TicketLabel.ticket_id == ticket_uuid))).scalars().all())
    state["blockers"] = set((await db.execute(select(TicketDependency.blocker_id).where(TicketDependency.ticket_id == ticket_uuid))).scalars().all())
    state["criteria"] = list(
        (
            await db.execute(
                select(TicketAcceptanceCriterion.description)
                .where(TicketAcceptanceCriterion.ticket_id == ticket_uuid)
                .order_by(TicketAcceptanceCriterion.position)
            )
        )
        .scalars()
        .all()
    )
    return state


async def _ticket_exists(env: ParityEnv, ticket_id: uuid.UUID | str) -> bool:
    count = (await env.db.execute(select(func.count(Ticket.id)).where(Ticket.id == uuid.UUID(str(ticket_id))))).scalar_one()
    return count == 1


def _without(state: Mapping[str, Any], *keys: str) -> dict[str, Any]:
    return {k: v for k, v in state.items() if k not in keys}


async def _project_state(env: ParityEnv, project_id: uuid.UUID) -> dict[str, Any]:
    row = (
        await env.db.execute(select(Project.name, Project.slug, Project.code, Project.description, Project.is_active).where(Project.id == project_id))
    ).one()
    return dict(row._mapping)


async def _sprint_state(env: ParityEnv, sprint_id: uuid.UUID | str) -> dict[str, Any]:
    row = (
        await env.db.execute(
            select(Sprint.name, Sprint.goal, Sprint.status, Sprint.start_date, Sprint.end_date).where(Sprint.id == uuid.UUID(str(sprint_id)))
        )
    ).one()
    return dict(row._mapping)


async def _wiki_state(env: ParityEnv, page_id: uuid.UUID | str) -> dict[str, Any]:
    row = (
        await env.db.execute(
            select(
                WikiPage.title,
                WikiPage.slug,
                WikiPage.parent_id,
                WikiPage.position,
                WikiPage.is_ai_touched,
                WikiPage.is_public,
                WikiPage.created_by_id,
                WikiPage.last_edited_by_id,
                WikiPage.minio_path,
            ).where(WikiPage.id == uuid.UUID(str(page_id)))
        )
    ).one()
    state = dict(row._mapping)
    state["content"] = env.fx.minio.get(state["minio_path"])
    return state


# ---------------------------------------------------------------------------
# Parsery narzędzi MCP zwracających tekst
# ---------------------------------------------------------------------------


def _parse_ticket_table(text: str) -> tuple[int, int, int, list[tuple[str, ...]]]:
    """`list_tickets`: (total, page, total_pages, wiersze jako krotki 10 komórek).

    Komórki: Key, Title, Status, Pri, Assignee, Sprint, SP, Due, Labels, Blocked by.
    """
    lines = text.split("\n")
    header = re.fullmatch(r"(\d+) tickets \(page (\d+)/(\d+)\)", lines[0])
    assert header, lines[0]
    rows = [] if lines[2].startswith("(brak") else [tuple(cell.strip() for cell in line.split(" | ")) for line in lines[3:]]
    return int(header[1]), int(header[2]), int(header[3]), rows


def _parse_board(text: str) -> dict[str, Any]:
    """`get_board`: sprint (nazwa, start, koniec, suma SP) oraz kolumny status -> wiersze (key, title, priority, assignee, sp, labels)."""
    lines = text.split("\n")
    header = re.fullmatch(rf"Sprint: (.+) \((\d{{4}}-\d{{2}}-\d{{2}}) {_ARROW} (\S+)\) \| (\d+)sp total", lines[0])
    assert header, lines[0]
    section_names = {"Todo": "todo", "In Progress": "in_progress", "In Review": "in_review", "Done": "done"}
    columns: dict[str, list[tuple[Any, ...]]] = {}
    current = ""
    for line in lines[1:]:
        if line.startswith("## "):
            current = section_names[line[3:]]
            columns[current] = []
        elif line and line != "(brak)":
            key, title, priority, assignee, tail = line.split(" | ")
            tail_match = re.fullmatch(r"(--|\d+sp)(?: \[(.*)\])?", tail)
            assert tail_match, tail
            story_points = None if tail_match[1] == "--" else int(tail_match[1].removesuffix("sp"))
            labels = tail_match[2].split(", ") if tail_match[2] else []
            columns[current].append((key, title, priority, assignee.removeprefix("@") if assignee != "--" else None, story_points, labels))
    return {"sprint": (header[1], header[2], header[3], int(header[4])), "columns": columns}


def _parse_ticket_detail(text: str) -> dict[str, Any]:
    """`get_ticket`: naglowek, metadane i sekcje (opis, zalaczniki, kryteria, blokery, komentarze)."""
    lines = text.split("\n")
    key, title = lines[0].split(" | ", 1)
    meta = re.fullmatch(r"Status: (\S+) \| Priority: (\S+) \| Sprint: (.*) \| Assignee: (.*)", lines[1])
    points = re.fullmatch(r"Story Points: (\S+) \| Due: (\S+) \| Labels: (.*)", lines[2])
    audit = re.fullmatch(r"Created: \S+ \| Updated: \S+ \| AI: (yes|no)", lines[3])
    assert meta and points and audit, lines[:4]
    detail: dict[str, Any] = {
        "key": key,
        "title": title,
        "status": meta[1],
        "priority": meta[2],
        "sprint": meta[3],
        "assignee": meta[4],
        "story_points": points[1],
        "due": points[2],
        "labels": sorted(points[3].split(", ")) if points[3] != _DASH else [],
        "created_via_ai": audit[1] == "yes",
        "id": lines[4].removeprefix("ID: "),
        "description": None,
        "attachments": [],
        "criteria": [],
        "blocked_by": [],
        "blocks": [],
        "comments": [],
    }
    section = ""
    for line in lines[5:]:
        if line.startswith("## "):
            section = line[3:].split(" (")[0]
        elif not line:
            continue
        elif section == "Description":
            detail["description"] = line
        elif section == "Attachments":
            attachment = re.fullmatch(r"- (.*) \((.*), (\d+) KB\)", line)
            assert attachment, line
            detail["attachments"].append((attachment[1], attachment[2], int(attachment[3])))
        elif section == "Acceptance Criteria":
            criterion = re.fullmatch(r"\[( |x)\] (.*?)( \(AI\))?", line)
            assert criterion, line
            detail["criteria"].append((criterion[1] == "x", criterion[2], criterion[3] is not None))
        elif section.startswith("Blokery"):
            relation = re.fullmatch(r"(Blokowany przez|Blokuje): (\S+) \| (\S+) \| (.*)", line)
            assert relation, line
            target = "blocked_by" if relation[1] == "Blokowany przez" else "blocks"
            detail[target].append((relation[2], relation[3], relation[4]))
        elif section == "Comments":
            comment = re.fullmatch(r"\[\d{4}-\d{2}-\d{2} (\S+)\] (.*)", line)
            assert comment, line
            detail["comments"].append((comment[1], comment[2]))
    return detail


def _parse_criteria(text: str) -> list[dict[str, Any]]:
    """`list_acceptance_criteria`: id, opis, ukonczenie i flaga AI; 'Brak kryteriów akceptacji.' daje pustą listę."""
    if text.startswith("Brak kryteri"):
        return []
    lines = text.split("\n")
    assert re.fullmatch(r"Kryteria akceptacji \S+ (\d+) pozycji", lines[0]) or re.search(r"\d+ pozycji", lines[0]), lines[0]
    parsed = []
    for line in lines[1:]:
        if not line:
            continue
        match = re.fullmatch(r"\[( |x)\] \[([0-9a-f-]{36})\] (.*?)( \(AI\))? \| autor: (\S+) \| \d{4}-\d{2}-\d{2}(?: \| ukonczone: .*)?", line)
        assert match, line
        parsed.append(
            {"is_completed": match[1] == "x", "id": match[2], "description": match[3], "created_via_ai": match[4] is not None, "author": match[5]}
        )
    return parsed


def _parse_sprint(text: str) -> dict[str, Any]:
    """`get_sprint`: naglowek (id, nazwa, status, daty, cel, sumy SP); tabela ticketow jest pomijana (znana roznica)."""
    lines = text.split("\n")
    header = re.fullmatch(rf"Sprint: (.*) \| Status: (\S+) \| (\d{{4}}-\d{{2}}-\d{{2}}) {_ARROW} (.*)", lines[1])
    assert lines[0].startswith("ID: ") and header, lines[:2]
    goal_line = next((line for line in lines if line.startswith("Goal: ")), None)
    totals = next(re.fullmatch(r"Total: (\d+)sp \| Done: (\d+)sp", line) for line in lines if line.startswith("Total: "))
    assert totals
    return {
        "id": lines[0].removeprefix("ID: "),
        "name": header[1],
        "status": header[2],
        "start_date": header[3],
        "end_date": None if header[4] == "(brak)" else header[4],
        "goal": goal_line.removeprefix("Goal: ") if goal_line else None,
        "total_sp": int(totals[1]),
        "done_sp": int(totals[2]),
    }


def _parse_wiki_tree(text: str) -> list[dict[str, Any]]:
    """`list_wiki_pages`: id, tytuł, głębokość (wcięcie / 2) i data updated_at (YYYY-MM-DD)."""
    lines = text.split("\n")
    if lines[0] == "0 pages":
        return []
    rows = []
    for line in lines[3:]:
        match = re.fullmatch(r"([0-9a-f-]{36}) \| ( *)(.*?) *\| (\d{4}-\d{2}-\d{2})", line)
        assert match, line
        rows.append({"id": match[1], "depth": len(match[2]) // 2, "title": match[3], "updated": match[4]})
    return rows


# ---------------------------------------------------------------------------
# Scenariusze: Projects (11)
# ---------------------------------------------------------------------------


async def scenario_list_projects(env: ParityEnv) -> None:
    # Wstawiane w kolejności innej niż alfabetyczna po nazwie (rule #2 test-kills-mutant).
    zzz = await _extra_project(env, "zzz-list", role="member")
    aaa = await _extra_project(env, "aaa-list", role="admin")
    expected = [(aaa.name, aaa.slug, "admin"), (env.project.name, env.project.slug, "owner"), (zzz.name, zzz.slug, "member")]

    mcp_items = await env.mcp("list_projects")
    response = await env.v2("list_projects")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(p["name"], p["slug"], p["role"]) for p in mcp_items] == expected
    assert [(i["name"], i["slug"], i["role"]) for i in items] == expected
    assert [_dt(p["created_at"]) for p in mcp_items] == [_dt(i["created_at"]) for i in items]
    assert [i["code"] for i in items] == [aaa.code, env.project.code, zzz.code]


async def scenario_get_project(env: ParityEnv) -> None:
    env.project.description = "Opis parytetu"
    teammate = await _new_user(env, "getproj")
    await add_member(env.db, env.project, teammate, role="member")
    sprint = await _sprint(env, "Sprint Projektu", status="active", goal="Cel projektu")
    await _ticket(env, "Todo A", status="todo", sprint_id=sprint.id)
    await _ticket(env, "Todo B", status="todo", sprint_id=sprint.id)
    await _ticket(env, "Gotowe", status="done", sprint_id=sprint.id)

    mcp_out = await env.mcp("get_project", env.slug)
    response = await env.v2("get_project")

    assert response.status_code == 200
    body = response.json()
    for key in ("id", "name", "slug", "description", "role", "members_count", "tickets_count", "active_sprint", "monitors_count", "heartbeats_count"):
        assert body[key] == mcp_out[key], key
    assert _dt(body["created_at"]) == _dt(mcp_out["created_at"])
    assert body["code"] == env.project.code
    assert body["description"] == "Opis parytetu"
    assert body["role"] == "owner"
    assert body["members_count"] == 2
    assert body["tickets_count"] == 3
    assert body["active_sprint"] == {"name": "Sprint Projektu", "goal": "Cel projektu", "tickets_by_status": {"todo": 2, "done": 1}}


async def scenario_create_project(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    codes = ["".join(secrets.choice(string.ascii_uppercase) for _ in range(6)) for _ in range(2)]

    mcp_out = await env.mcp("create_project", name=f"Projekt MCP {tag}", slug=f"projekt-mcp-{tag}", description="Opis MCP", code=codes[0])
    response = await env.v2(
        "create_project",
        json={"name": f"Projekt v2 {tag}", "slug": f"projekt-v2-{tag}", "description": "Opis v2", "code": codes[1]},
    )

    assert response.status_code == 201
    body = response.json()
    assert set(mcp_out) - {"message"} == set(body)
    assert (mcp_out["name"], mcp_out["slug"], mcp_out["description"], mcp_out["code"]) == (
        f"Projekt MCP {tag}",
        f"projekt-mcp-{tag}",
        "Opis MCP",
        codes[0],
    )
    assert (body["name"], body["slug"], body["description"], body["code"]) == (f"Projekt v2 {tag}", f"projekt-v2-{tag}", "Opis v2", codes[1])
    for created in (mcp_out, body):
        state = await _project_state(env, uuid.UUID(created["id"]))
        assert state["is_active"] is True
        assert state["slug"] == created["slug"]
        role = (
            await env.db.execute(
                select(ProjectMember.role).where(ProjectMember.project_id == uuid.UUID(created["id"]), ProjectMember.user_id == env.actor.user.id)
            )
        ).scalar_one()
        assert role == "owner"

    # Bez slug/description/code: oba kanały wyprowadzają slug z nazwy i kod ze sluga.
    default_mcp = await env.mcp("create_project", name=f"Domyslny MCP {tag}")
    default_v2 = (await env.v2("create_project", json={"name": f"Domyslny v2 {tag}"})).json()
    assert default_mcp["slug"] == f"domyslny-mcp-{tag}"
    assert default_v2["slug"] == f"domyslny-v2-{tag}"
    assert default_mcp["description"] is None
    assert default_v2["description"] is None
    assert default_mcp["code"].startswith("DM")
    assert default_v2["code"].startswith("DV")


async def scenario_update_project(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    project_b = await _extra_project(env, "upd-b")
    slug_a = env.slug

    mcp_out = await env.mcp("update_project", slug_a, name="Nowa nazwa MCP", description="Nowy opis MCP", new_slug=f"nowy-mcp-{tag}")
    response = await env.v2(
        "update_project",
        slug=project_b.slug,
        json={"name": "Nowa nazwa v2", "description": "Nowy opis v2", "slug": f"nowy-v2-{tag}"},
    )

    assert response.status_code == 200
    body = response.json()
    assert (mcp_out["name"], mcp_out["slug"], mcp_out["description"]) == ("Nowa nazwa MCP", f"nowy-mcp-{tag}", "Nowy opis MCP")
    assert (body["name"], body["slug"], body["description"]) == ("Nowa nazwa v2", f"nowy-v2-{tag}", "Nowy opis v2")
    assert mcp_out["code"] == env.project.code
    assert body["code"] == project_b.code
    assert _without(await _project_state(env, env.project.id), "name", "slug", "code", "description") == _without(
        await _project_state(env, project_b.id), "name", "slug", "code", "description"
    )

    # Brak parametru = bez zmian: sam `name` nie rusza opisu ani sluga.
    only_name = await env.mcp("update_project", f"nowy-mcp-{tag}", name="Tylko nazwa MCP")
    response = await env.v2("update_project", slug=f"nowy-v2-{tag}", json={"name": "Tylko nazwa v2"})
    assert response.status_code == 200
    assert (only_name["name"], only_name["slug"], only_name["description"]) == ("Tylko nazwa MCP", f"nowy-mcp-{tag}", "Nowy opis MCP")
    body = response.json()
    assert (body["name"], body["slug"], body["description"]) == ("Tylko nazwa v2", f"nowy-v2-{tag}", "Nowy opis v2")

    message = await env.mcp_error("update_project", f"nowy-mcp-{tag}", new_slug="Zly Slug")
    _assert_problem(await env.v2("update_project", slug=f"nowy-v2-{tag}", json={"slug": "Zly Slug"}), 422, message)


async def scenario_delete_project(env: ParityEnv) -> None:
    project_b = await _extra_project(env, "del-b")
    slug_a, slug_b = env.slug, project_b.slug

    mcp_out = await env.mcp("delete_project", slug_a, confirm=True)
    response = await env.v2("delete_project", slug=slug_b)

    assert response.status_code == 204
    assert response.content == b""
    assert env.project.name in mcp_out["message"]
    assert (await _project_state(env, env.project.id))["is_active"] is False
    assert (await _project_state(env, project_b.id))["is_active"] is False
    # Soft delete znika z list_projects w obu kanałach i z dostępu po slugu.
    assert slug_a not in [p["slug"] for p in await env.mcp("list_projects")]
    listed = (await env.v2("list_projects")).json()["items"]
    assert slug_b not in [i["slug"] for i in listed]
    message = await env.mcp_error("get_project", slug_a)
    _assert_problem(await env.v2("get_project", slug=slug_a), 404, message)


async def scenario_invite_member(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    email_a, email_b = f"parity-inv-mcp-{tag}@example.com", f"parity-inv-v2-{tag}@example.com"

    mcp_out = await env.mcp("invite_member", env.slug, email_a)
    response = await env.v2("invite_member", json={"email": email_b})

    assert response.status_code == 201
    body = response.json()
    assert (mcp_out["user_email"], mcp_out["role"]) == (email_a, "member")
    assert (body["email"], body["role"], body["is_new_user"]) == (email_b, "member", True)
    assert "invitation_id" not in mcp_out
    assert "invitation_token" not in body
    tokens: dict[str, uuid.UUID] = {}
    for email, expires_at in ((email_a, mcp_out["expires_at"]), (email_b, body["expires_at"])):
        row = (await env.db.execute(select(User.invitation_token, User.invitation_expires_at).where(User.email == email))).one()
        assert row.invitation_token is not None
        assert str(row.invitation_token) not in str(mcp_out)
        assert str(row.invitation_token) not in response.text
        tokens[email] = row.invitation_token
        assert row.invitation_expires_at == _dt(expires_at)
        assert timedelta(days=6) < _dt(expires_at) - datetime.now(UTC) < timedelta(days=8)
        member_role = (
            await env.db.execute(
                select(ProjectMember.role)
                .join(User, User.id == ProjectMember.user_id)
                .where(User.email == email, ProjectMember.project_id == env.project.id)
            )
        ).scalar_one()
        assert member_role == "member"
    env.fx.mcp_mail.assert_called_once_with(email_a, "", tokens[email_a])
    env.fx.v2_mail.assert_called_once_with(email_b, "", tokens[email_b])

    # Istniejące konto: dodane od razu z podaną rolą, bez zaproszenia i bez maila.
    existing_a = await _new_user(env, "inv-a")
    existing_b = await _new_user(env, "inv-b")
    mcp_existing = await env.mcp("invite_member", env.slug, existing_a.email, role="admin")
    response = await env.v2("invite_member", json={"email": existing_b.email, "role": "admin"})
    assert response.status_code == 201
    body = response.json()
    assert (mcp_existing["user_email"], mcp_existing["role"]) == (existing_a.email, "admin")
    assert "invitation_id" not in mcp_existing
    assert "expires_at" not in mcp_existing
    assert (body["email"], body["role"], body["is_new_user"]) == (existing_b.email, "admin", False)
    assert "invitation_token" not in body
    assert body["expires_at"] is None
    for user in (existing_a, existing_b):
        role = (
            await env.db.execute(select(ProjectMember.role).where(ProjectMember.project_id == env.project.id, ProjectMember.user_id == user.id))
        ).scalar_one()
        assert role == "admin"
    assert env.fx.mcp_mail.call_count == 1
    assert env.fx.v2_mail.call_count == 1

    message = await env.mcp_error("invite_member", env.slug, existing_a.email)
    _assert_problem(await env.v2("invite_member", json={"email": existing_a.email}), 422, message)


async def scenario_list_members(env: ParityEnv) -> None:
    # Kolejność wstawiania (owner, member Zofia, admin, member Anna) różni się od oczekiwanej (owner, admin, Anna, Zofia).
    zofia = await _new_user(env, "members-z", first_name="Zofia", last_name="Zet")
    await add_member(env.db, env.project, zofia, role="member")
    adam = await _new_user(env, "members-a", first_name="Adam", last_name="Ab")
    await add_member(env.db, env.project, adam, role="admin")
    anna = await _new_user(env, "members-n", first_name="Anna", last_name="Aaa")
    await add_member(env.db, env.project, anna, role="member")
    expected = [
        (env.actor.user.email, env.actor.user.email, "owner"),
        (adam.email, "Adam Ab", "admin"),
        (anna.email, "Anna Aaa", "member"),
        (zofia.email, "Zofia Zet", "member"),
    ]

    mcp_items = await env.mcp("list_members", env.slug)
    response = await env.v2("list_members")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(m["email"], m["name"], m["role"]) for m in mcp_items] == expected
    assert [(m["email"], m["name"], m["role"]) for m in items] == expected
    assert [m["user_id"] for m in mcp_items] == [m["user_id"] for m in items]
    assert [_dt(m["joined_at"]) for m in mcp_items] == [_dt(m["joined_at"]) for m in items]
    assert items[1]["user_id"] == str(adam.id)


async def scenario_remove_member(env: ParityEnv) -> None:
    target_a = await _new_user(env, "rm-a")
    target_b = await _new_user(env, "rm-b")
    bystander = await _new_user(env, "rm-c")
    for user in (target_a, target_b, bystander):
        await add_member(env.db, env.project, user, role="member")

    mcp_out = await env.mcp("remove_member", env.slug, target_a.email)
    response = await env.v2("remove_member", member_id=target_b.id)

    assert response.status_code == 204
    assert response.content == b""
    assert target_a.email in mcp_out["message"]
    remaining = set((await env.db.execute(select(ProjectMember.user_id).where(ProjectMember.project_id == env.project.id))).scalars().all())
    assert remaining == {env.actor.user.id, bystander.id}

    message = await env.mcp_error("remove_member", env.slug, env.actor.user.email)
    _assert_problem(await env.v2("remove_member", member_id=env.actor.user.id), 422, message)


async def scenario_get_project_summary(env: ParityEnv) -> None:
    env.db.add(Issue(project_id=env.project.id, fingerprint=uuid.uuid4().hex, title="Nierozwiazany blad", status="unresolved"))
    env.db.add(Issue(project_id=env.project.id, fingerprint=uuid.uuid4().hex, title="Rozwiazany blad", status="resolved"))
    await _ticket(env, "Backlog A", status="backlog")
    await _ticket(env, "Backlog B", status="backlog")
    sprint = await _sprint(env, "Sprint Podsumowania", status="active", goal="Cel podsumowania")
    await _ticket(env, "W sprincie", status="todo", sprint_id=sprint.id)
    await env.db.flush()

    mcp_out = await env.mcp("get_project_summary", env.slug)
    response = await env.v2("get_project_summary")

    assert response.status_code == 200
    body = response.json()
    for key in ("issues_unresolved", "monitors_failing", "uptime_24h", "active_sprint", "backlog_count"):
        assert body[key] == mcp_out[key], key
    assert body["issues_unresolved"] == 1
    assert body["backlog_count"] == 2
    assert body["monitors_failing"] == 0
    assert body["uptime_24h"] is None
    assert body["active_sprint"] == {"name": "Sprint Podsumowania", "goal": "Cel podsumowania", "tickets_by_status": {"todo": 1}, "total_tickets": 1}
    # Znana różnica: klucz `project` istnieje tylko w MCP.
    assert mcp_out["project"] == {"name": env.project.name, "slug": env.slug}
    assert "project" not in body


async def scenario_list_labels(env: ParityEnv) -> None:
    # "Zebra" wstawiona przed "Alfa": oczekiwana kolejność jest po nazwie.
    zebra = await _label(env, "Zebra", "#111111")
    alfa = await _label(env, "Alfa", "#222222")
    for title in ("Z alfa 1", "Z alfa 2"):
        await _tag(env, await _ticket(env, title), alfa)
    expected = [(str(alfa.id), "Alfa", "#222222", 2), (str(zebra.id), "Zebra", "#111111", 0)]

    mcp_items = await env.mcp("list_labels", env.slug)
    response = await env.v2("list_labels")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(lb["id"], lb["name"], lb["color"], lb["tickets_count"]) for lb in mcp_items] == expected
    assert [(lb["id"], lb["name"], lb["color"], lb["tickets_count"]) for lb in items] == expected


async def scenario_create_label(env: ParityEnv) -> None:
    mcp_out = await env.mcp("create_label", env.slug, "Etykieta MCP", color="#abcdef")
    response = await env.v2("create_label", json={"name": "Etykieta v2", "color": "#fedcba"})

    assert response.status_code == 201
    body = response.json()
    assert set(mcp_out) - {"message"} == set(body)
    assert (mcp_out["name"], mcp_out["color"]) == ("Etykieta MCP", "#abcdef")
    assert (body["name"], body["color"]) == ("Etykieta v2", "#fedcba")
    for created in (mcp_out, body):
        row = (await env.db.execute(select(Label.project_id, Label.name, Label.color).where(Label.id == uuid.UUID(created["id"])))).one()
        assert (row.project_id, row.name, row.color) == (env.project.id, created["name"], created["color"])

    # Bez koloru oba kanały losują go z tej samej palety.
    default_mcp = await env.mcp("create_label", env.slug, "Domyslna MCP")
    default_v2 = (await env.v2("create_label", json={"name": "Domyslna v2"})).json()
    assert default_mcp["color"] in LABEL_COLOR_PALETTE
    assert default_v2["color"] in LABEL_COLOR_PALETTE

    message = await env.mcp_error("create_label", env.slug, "Etykieta MCP")
    _assert_problem(await env.v2("create_label", json={"name": "Etykieta MCP"}), 422, message)


# ---------------------------------------------------------------------------
# Scenariusze: Tickets (14)
# ---------------------------------------------------------------------------


@dataclass
class TicketSet:
    sprint: Sprint
    label: Label
    other: User
    first: Ticket
    second: Ticket
    third: Ticket


async def _seed_ticket_set(env: ParityEnv) -> TicketSet:
    """Trzy tickety wstawiane w kolejności (first, second, third); sortowanie serwisu po `order` daje second, third, first."""
    sprint = await _sprint(env, "Sprint Alfa", status="planning")
    label = await _label(env, "backend")
    other = await _new_user(env, "ticket-set")
    first = await _ticket(
        env,
        "Pierwszy ticket parytetu",
        status="todo",
        priority="high",
        story_points=3,
        sprint_id=sprint.id,
        assignee_id=other.id,
        due_date=date(2026, 10, 5),
        order=2,
    )
    await _tag(env, first, label)
    second = await _ticket(env, "Drugi ticket", status="backlog", priority="low", order=0)
    third = await _ticket(
        env,
        "Trzeci ticket z bardzo dlugim tytulem przekraczajacym limit",
        status="in_review",
        priority="medium",
        story_points=8,
        sprint_id=sprint.id,
        order=1,
    )
    return TicketSet(sprint=sprint, label=label, other=other, first=first, second=second, third=third)


async def _assert_list_tickets_parity(env: ParityEnv, seed: TicketSet, expected: list[Ticket], **filters: Any) -> None:
    text = await env.mcp("list_tickets", env.slug, **filters)
    response = await env.v2("list_tickets", params=filters)
    assert response.status_code == 200
    body = response.json()
    total, page, total_pages, rows = _parse_ticket_table(text)
    assert (total, page, total_pages) == (body["total"], body["page"], body["total_pages"]) == (len(expected), 1, 1)
    assert body["per_page"] == 20
    sprint_names = {str(seed.sprint.id): seed.sprint.name}
    label_names = {str(seed.label.id): seed.label.name}
    v2_rows = [
        (
            item["key"],
            item["title"][:40].strip(),
            item["status"],
            item["priority"],
            item["assignee_email"] or "--",
            sprint_names.get(item["sprint_id"], "--"),
            str(item["story_points"]) if item["story_points"] is not None else "--",
            item["due_date"] or "--",
            ", ".join(label_names[label_id] for label_id in item["label_ids"]),
            ", ".join(b["key"] for b in sorted(item["blocked_by"], key=lambda b: int(b["key"].rsplit("-", 1)[1]))) or "--",
        )
        for item in body["items"]
    ]
    assert rows == v2_rows
    assert [row[0] for row in rows] == [_key(env.project, t) for t in expected]


async def scenario_list_tickets(env: ParityEnv) -> None:
    seed = await _seed_ticket_set(env)
    await ticket_dependencies.set_blockers(env.db, seed.first, [seed.third.id, seed.second.id])
    await env.db.flush()

    await _assert_list_tickets_parity(env, seed, [seed.second, seed.third, seed.first])
    await _assert_list_tickets_parity(env, seed, [seed.first], status="todo")
    await _assert_list_tickets_parity(env, seed, [seed.first], label_id=str(seed.label.id))
    await _assert_list_tickets_parity(env, seed, [seed.second], search="Drugi")
    await _assert_list_tickets_parity(env, seed, [seed.third, seed.first], sprint_id=str(seed.sprint.id))
    await _assert_list_tickets_parity(env, seed, [seed.first], due_date_before="2026-10-10")

    _, _, _, rows = _parse_ticket_table(await env.mcp("list_tickets", env.slug))
    assert rows[1][1] == "Trzeci ticket z bardzo dlugim tytulem pr"
    assert rows[2][8] == "backend"
    assert rows[2][4] == seed.other.email
    assert rows[2][9] == f"{_key(env.project, seed.second)}, {_key(env.project, seed.third)}"
    assert rows[0][9] == "--"


async def _assert_search_parity(env: ParityEnv, expected: list[Ticket], **filters: Any) -> None:
    mcp_out = await env.mcp("search_tickets", env.slug, **filters)
    response = await env.v2("search_tickets", params=filters)
    assert response.status_code == 200
    body = response.json()
    assert mcp_out["results"] == body["items"]
    assert (mcp_out["total"], mcp_out["page"], mcp_out["total_pages"]) == (body["total"], body["page"], body["total_pages"]) == (len(expected), 1, 1)
    assert [row["key"] for row in body["items"]] == [_key(env.project, t) for t in expected]


async def scenario_search_tickets(env: ParityEnv) -> None:
    seed = await _seed_ticket_set(env)

    await _assert_search_parity(env, [seed.second, seed.third, seed.first])
    await _assert_search_parity(env, [seed.second], query="Drugi")
    await _assert_search_parity(env, [seed.first], assignee_email=seed.other.email)
    await _assert_search_parity(env, [seed.third], status="in_review")
    await _assert_search_parity(env, [seed.third, seed.first], sprint_id=str(seed.sprint.id))

    response = await env.v2("search_tickets")
    first_row = next(row for row in response.json()["items"] if row["id"] == str(seed.first.id))
    assert first_row == {
        "id": str(seed.first.id),
        "key": _key(env.project, seed.first),
        "title": "Pierwszy ticket parytetu",
        "status": "todo",
        "priority": "high",
        "story_points": 3,
        "assignee": seed.other.email,
        "due_date": "2026-10-05",
        "settlements": [],
    }


def _v2_ticket_view(body: Mapping[str, Any], sprint_names: Mapping[str, str], label_names: Mapping[str, str]) -> dict[str, Any]:
    """Odpowiedź v2 sprowadzona do kształtu zwracanego przez `_parse_ticket_detail`."""
    return {
        "key": body["key"],
        "title": body["title"],
        "status": body["status"],
        "priority": body["priority"],
        "sprint": sprint_names.get(body["sprint_id"], _DASH),
        "assignee": body["assignee_email"] or _DASH,
        "story_points": str(body["story_points"]) if body["story_points"] is not None else _DASH,
        "due": body["due_date"] or _DASH,
        "labels": sorted(label_names[label_id] for label_id in body["label_ids"]),
        "created_via_ai": body["created_via_ai"],
        "id": body["id"],
        "description": body["description"],
        "attachments": [(a["filename"], a["mime_type"], round(a["size"] / 1024)) for a in body["attachments"]],
        "criteria": [(c["is_completed"], c["description"], c["created_via_ai"]) for c in body["acceptance_criteria"]],
        "blocked_by": [(t["key"], t["status"], t["title"]) for t in body["blocked_by"]],
        "blocks": [(t["key"], t["status"], t["title"]) for t in body["blocks"]],
        "comments": [("ai" if c["created_via_ai"] else c["author"], c["content"]) for c in body["comments"]],
    }


async def scenario_get_ticket(env: ParityEnv) -> None:
    seed = await _seed_ticket_set(env)
    urgent = await _label(env, "pilne", "#ff0000")
    rich = await _ticket(
        env,
        "Bogaty ticket",
        description="Opis parytetu",
        status="in_progress",
        priority="critical",
        story_points=5,
        sprint_id=seed.sprint.id,
        assignee_id=seed.other.id,
        due_date=date(2026, 10, 20),
        created_via_ai=True,
        order=9,
    )
    await _tag(env, rich, seed.label, urgent)
    blocker = await _ticket(env, "Bloker bogatego", status="todo", order=10)
    blocked = await _ticket(env, "Blokowany przez bogatego", status="in_review", order=11)
    await ticket_dependencies.set_blockers(env.db, rich, [blocker.id])
    await ticket_dependencies.set_blockers(env.db, blocked, [rich.id])
    # Kryteria i komentarze wstawiane w kolejności innej niż position / created_at.
    for position, description, done in ((2, "Trzecie kryterium", False), (0, "Pierwsze kryterium", True), (1, "Drugie kryterium", False)):
        env.db.add(
            TicketAcceptanceCriterion(
                ticket_id=rich.id,
                description=description,
                position=position,
                is_completed=done,
                created_by_user_id=env.actor.user.id,
                created_via_ai=position == 1,
            )
        )
    for minutes, author, content in (
        (30, seed.other, "Komentarz trzeci"),
        (10, env.actor.user, "Komentarz pierwszy"),
        (20, seed.other, "Komentarz drugi"),
    ):
        env.db.add(TicketComment(ticket_id=rich.id, user_id=author.id, content=content, created_via_ai=False, created_at=_ts(minutes)))
    env.db.add(TicketAttachment(ticket_id=rich.id, filename="log.txt", storage_path="tickets/log.txt", mime_type="text/plain", size=2048))
    bare = await _ticket(env, "Ticket bez metadanych", order=12)
    await env.db.flush()

    sprint_names = {str(seed.sprint.id): seed.sprint.name}
    label_names = {str(seed.label.id): seed.label.name, str(urgent.id): urgent.name}
    for ticket, mcp_id, v2_id in ((rich, _key(env.project, rich), str(rich.id)), (bare, str(bare.id), _key(env.project, bare))):
        text = await env.mcp("get_ticket", env.slug, mcp_id)
        response = await env.v2("get_ticket", ticket_id=v2_id)
        assert response.status_code == 200
        mcp_view = _parse_ticket_detail(text)
        assert mcp_view == _v2_ticket_view(response.json(), sprint_names, label_names), _key(env.project, ticket)

    rich_view = _parse_ticket_detail(await env.mcp("get_ticket", env.slug, str(rich.id)))
    assert (rich_view["status"], rich_view["priority"], rich_view["story_points"], rich_view["due"]) == ("in_progress", "critical", "5", "2026-10-20")
    assert rich_view["labels"] == ["backend", "pilne"]
    assert rich_view["criteria"] == [(True, "Pierwsze kryterium", False), (False, "Drugie kryterium", True), (False, "Trzecie kryterium", False)]
    assert rich_view["comments"] == [
        (env.actor.user.email, "Komentarz pierwszy"),
        (seed.other.email, "Komentarz drugi"),
        (seed.other.email, "Komentarz trzeci"),
    ]
    assert rich_view["blocked_by"] == [(_key(env.project, blocker), "todo", "Bloker bogatego")]
    assert rich_view["blocks"] == [(_key(env.project, blocked), "in_review", "Blokowany przez bogatego")]
    assert rich_view["attachments"] == [("log.txt", "text/plain", 2)]
    bare_view = _parse_ticket_detail(await env.mcp("get_ticket", env.slug, str(bare.id)))
    assert (bare_view["sprint"], bare_view["assignee"], bare_view["story_points"], bare_view["due"], bare_view["labels"]) == (
        _DASH,
        _DASH,
        _DASH,
        _DASH,
        [],
    )

    missing = f"{env.project.code}-999"
    message = await env.mcp_error("get_ticket", env.slug, missing)
    _assert_problem(await env.v2("get_ticket", ticket_id=missing), 404, message)


async def scenario_create_ticket(env: ParityEnv) -> None:
    sprint = await _sprint(env, "Sprint tworzenia")
    other = await _new_user(env, "create-ticket")
    await add_member(env.db, env.project, other, role="member")
    label = await _label(env, "tworzenie")
    blocker = await _ticket(env, "Bloker tworzenia")
    spec = await _wiki_page(env, "Specyfikacja", "specyfikacja-parytet")
    payload = {
        "description": "Opis nowego ticketa",
        "priority": "high",
        "story_points": 5,
        "sprint_id": str(sprint.id),
        "assignee_email": other.email,
        "due_date": "2026-11-15",
        "label_ids": [str(label.id)],
        "acceptance_criteria": ["Kryterium jeden", "Kryterium dwa"],
        "spec_page_id": str(spec.id),
        "blocked_by_ids": [str(blocker.id)],
    }

    mcp_out = await env.mcp("create_ticket", env.slug, "Ticket z MCP", **payload)
    response = await env.v2("create_ticket", json={"title": "Ticket z v2", **payload})

    assert response.status_code == 201
    body = response.json()
    assert mcp_out["title"] == "Ticket z MCP"
    assert body["title"] == "Ticket z v2"
    assert (mcp_out["status"], body["status"]) == ("todo", "todo")
    assert (mcp_out["due_date"], body["due_date"]) == ("2026-11-15", "2026-11-15")
    assert (mcp_out["created_via_ai"], body["created_via_ai"]) == (True, True)
    assert mcp_out["acceptance_criteria_count"] == len(body["acceptance_criteria"]) == 2
    assert mcp_out["spec_page_id"] == str(spec.id)
    assert mcp_out["key"] != body["key"]
    assert body["blocked_by"][0]["key"] == _key(env.project, blocker)
    assert body["label_ids"] == [str(label.id)]
    state_a = await _ticket_state(env, mcp_out["id"])
    state_b = await _ticket_state(env, body["id"])
    assert _without(state_a, "title") == _without(state_b, "title")
    assert (state_a["priority"], state_a["story_points"], state_a["sprint_id"], state_a["assignee_id"]) == ("high", 5, sprint.id, other.id)
    assert (state_a["labels"], state_a["blockers"], state_a["spec_page_id"]) == ({label.id}, {blocker.id}, spec.id)
    assert state_a["criteria"] == ["Kryterium jeden", "Kryterium dwa"]

    # Wartości domyślne (bez parametrów opcjonalnych) są w obu kanałach takie same.
    minimal_mcp = await env.mcp("create_ticket", env.slug, "Minimalny MCP")
    minimal_v2 = (await env.v2("create_ticket", json={"title": "Minimalny v2"})).json()
    for created in (minimal_mcp, minimal_v2):
        state = await _ticket_state(env, created["id"])
        assert (state["status"], state["priority"], state["created_via_ai"], state["sprint_id"]) == ("backlog", "medium", True, None)

    message = await env.mcp_error("create_ticket", env.slug, "   ")
    _assert_problem(await env.v2("create_ticket", json={"title": "   "}), 422, message)


async def scenario_update_ticket(env: ParityEnv) -> None:
    sprint = await _sprint(env, "Sprint aktualizacji")
    label = await _label(env, "aktualizacja")
    blocker = await _ticket(env, "Bloker aktualizacji")
    ticket_a = await _ticket(env, "Do zmiany MCP", description="stary opis", priority="low", story_points=1)
    ticket_b = await _ticket(env, "Do zmiany v2", description="stary opis", priority="low", story_points=1)
    changes = {
        "description": "nowy opis",
        "status": "in_progress",
        "priority": "high",
        "story_points": 8,
        "sprint_id": str(sprint.id),
        "assignee_email": "me",
        "due_date": "2026-12-01",
        "label_ids": [str(label.id)],
        "blocked_by_ids": [str(blocker.id)],
    }

    mcp_out = await env.mcp("update_ticket", env.slug, str(ticket_a.id), title="Zmieniony MCP", **changes)
    response = await env.v2("update_ticket", ticket_id=ticket_b.id, json={"title": "Zmieniony v2", **changes})

    assert response.status_code == 200
    body = response.json()
    assert (mcp_out["title"], mcp_out["status"], mcp_out["due_date"]) == ("Zmieniony MCP", "in_progress", "2026-12-01")
    assert (body["title"], body["status"], body["due_date"]) == ("Zmieniony v2", "in_progress", "2026-12-01")
    state_a, state_b = await _ticket_state(env, ticket_a.id), await _ticket_state(env, ticket_b.id)
    assert _without(state_a, "title") == _without(state_b, "title")
    assert (state_a["assignee_id"], state_a["labels"], state_a["blockers"], state_a["story_points"]) == (
        env.actor.user.id,
        {label.id},
        {blocker.id},
        8,
    )

    # Brak parametru = bez zmian: sam tytuł nie rusza reszty pól.
    await env.mcp("update_ticket", env.slug, str(ticket_a.id), title="Tylko tytul MCP")
    assert (await env.v2("update_ticket", ticket_id=ticket_b.id, json={"title": "Tylko tytul v2"})).status_code == 200
    only_title_a, only_title_b = await _ticket_state(env, ticket_a.id), await _ticket_state(env, ticket_b.id)
    assert _without(only_title_a, "title") == _without(only_title_b, "title") == _without(state_a, "title")

    # Pusty string czyści assignee, termin, etykiety i blokery.
    await env.mcp("update_ticket", env.slug, str(ticket_a.id), assignee_email="", due_date="", label_ids=[], blocked_by_ids=[])
    cleared = await env.v2("update_ticket", ticket_id=ticket_b.id, json={"assignee_email": "", "due_date": "", "label_ids": [], "blocked_by_ids": []})
    assert cleared.status_code == 200
    cleared_a, cleared_b = await _ticket_state(env, ticket_a.id), await _ticket_state(env, ticket_b.id)
    assert _without(cleared_a, "title") == _without(cleared_b, "title")
    assert (cleared_a["assignee_id"], cleared_a["due_date"], cleared_a["labels"], cleared_a["blockers"]) == (None, None, set(), set())
    assert cleared_a["priority"] == "high"

    message = await env.mcp_error("update_ticket", env.slug, str(ticket_a.id), status="nie-ma-takiego")
    _assert_problem(await env.v2("update_ticket", ticket_id=ticket_b.id, json={"status": "nie-ma-takiego"}), 422, message)


async def scenario_delete_ticket(env: ParityEnv) -> None:
    ticket_a = await _ticket(env, "Do usuniecia MCP")
    ticket_b = await _ticket(env, "Do usuniecia v2")
    survivor = await _ticket(env, "Zostaje")

    mcp_out = await env.mcp("delete_ticket", env.slug, _key(env.project, ticket_a))
    response = await env.v2("delete_ticket", ticket_id=ticket_b.id)

    assert response.status_code == 204
    assert response.content == b""
    assert "Do usuniecia MCP" in mcp_out["message"]
    assert not await _ticket_exists(env, ticket_a.id)
    assert not await _ticket_exists(env, ticket_b.id)
    assert await _ticket_exists(env, survivor.id)

    missing = f"{env.project.code}-999"
    message = await env.mcp_error("delete_ticket", env.slug, missing)
    _assert_problem(await env.v2("delete_ticket", ticket_id=missing), 404, message)


async def scenario_bulk_update_tickets(env: ParityEnv) -> None:
    sprint = await _sprint(env, "Sprint masowy")
    other = await _new_user(env, "bulk")
    await add_member(env.db, env.project, other, role="member")
    mcp_tickets = [await _ticket(env, f"Masowy MCP {i}", priority="low") for i in range(2)]
    v2_tickets = [await _ticket(env, f"Masowy v2 {i}", priority="low") for i in range(2)]
    unknown = f"{env.project.code}-999"
    changes = {"status": "done", "priority": "critical", "assignee_email": other.email, "sprint_id": str(sprint.id), "due_date": "2026-12-24"}

    mcp_out = await env.mcp("bulk_update_tickets", env.slug, [*(str(t.id) for t in mcp_tickets), unknown], **changes)
    response = await env.v2("bulk_update_tickets", json={"ticket_ids": [*(str(t.id) for t in v2_tickets), unknown], **changes})

    assert response.status_code == 200
    body = response.json()
    assert mcp_out["updated"] == body["updated"] == 2
    assert mcp_out["failed"] == body["failed"] == [{"id": unknown, "reason": "Nieprawidlowy identyfikator ticketa"}]
    states = [await _ticket_state(env, t.id) for t in (*mcp_tickets, *v2_tickets)]
    for state in states:
        assert (state["status"], state["priority"], state["assignee_id"], state["sprint_id"], state["due_date"]) == (
            "done",
            "critical",
            other.id,
            sprint.id,
            date(2026, 12, 24),
        )

    # Brak parametru = bez zmian: sam priorytet nie rusza statusu.
    await env.mcp("bulk_update_tickets", env.slug, [str(mcp_tickets[0].id)], priority="low")
    assert (await env.v2("bulk_update_tickets", json={"ticket_ids": [str(v2_tickets[0].id)], "priority": "low"})).status_code == 200
    for ticket in (mcp_tickets[0], v2_tickets[0]):
        state = await _ticket_state(env, ticket.id)
        assert (state["priority"], state["status"]) == ("low", "done")


async def scenario_get_board(env: ParityEnv) -> None:
    empty = await _extra_project(env, "board-empty")
    assert await env.mcp("get_board", empty.slug) == "(Brak aktywnego sprintu)"
    empty_response = await env.v2("get_board", slug=empty.slug)
    assert empty_response.status_code == 200
    assert empty_response.json() == {"sprint": None, "columns": {}}

    sprint = await _sprint(env, "Sprint Tablicy", status="active", goal="Cel tablicy")
    other = await _new_user(env, "board")
    label = await _label(env, "tablica")
    late = await _ticket(env, "Todo pozniej", status="todo", priority="low", story_points=2, sprint_id=sprint.id, order=5)
    early = await _ticket(env, "Todo wczesniej", status="todo", priority="high", sprint_id=sprint.id, order=1, assignee_id=other.id)
    progress = await _ticket(env, "W trakcie", status="in_progress", priority="medium", story_points=3, sprint_id=sprint.id, order=2)
    await _tag(env, progress, label)
    done = await _ticket(env, "Gotowe", status="done", priority="critical", story_points=5, sprint_id=sprint.id, order=3)
    expected = {
        "todo": [
            (_key(env.project, early), "Todo wczesniej", "high", other.email, None, []),
            (_key(env.project, late), "Todo pozniej", "low", None, 2, []),
        ],
        "in_progress": [(_key(env.project, progress), "W trakcie", "medium", None, 3, ["tablica"])],
        "in_review": [],
        "done": [(_key(env.project, done), "Gotowe", "critical", None, 5, [])],
    }

    parsed = _parse_board(await env.mcp("get_board", env.slug))
    response = await env.v2("get_board")

    assert response.status_code == 200
    body = response.json()
    assert parsed["sprint"] == (sprint.name, "2026-10-01", "2026-10-14", 10)
    assert (body["sprint"]["name"], body["sprint"]["start_date"], body["sprint"]["end_date"], body["sprint"]["status"]) == (
        sprint.name,
        "2026-10-01",
        "2026-10-14",
        "active",
    )
    v2_columns = {
        status: [(i["key"], i["title"], i["priority"], i["assignee"], i["story_points"], i["labels"]) for i in items]
        for status, items in body["columns"].items()
    }
    assert parsed["columns"] == v2_columns == expected


async def scenario_list_comments(env: ParityEnv) -> None:
    other = await _new_user(env, "comments")
    ticket = await _ticket(env, "Z komentarzami")
    # Wstawiane w kolejności innej niż created_at.
    for minutes, author, content, via_ai in ((30, other, "Trzeci", True), (10, env.actor.user, "Pierwszy", False), (20, other, "Drugi", False)):
        env.db.add(TicketComment(ticket_id=ticket.id, user_id=author.id, content=content, created_via_ai=via_ai, created_at=_ts(minutes)))
    await env.db.flush()
    expected = [("Pierwszy", env.actor.user.email, False), ("Drugi", other.email, False), ("Trzeci", other.email, True)]

    mcp_items = await env.mcp("list_comments", env.slug, _key(env.project, ticket))
    response = await env.v2("list_comments", ticket_id=ticket.id)

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(c["content"], c["author"], c["created_via_ai"]) for c in mcp_items] == expected
    assert [(c["content"], c["author"], c["created_via_ai"]) for c in items] == expected
    assert [c["id"] for c in mcp_items] == [c["id"] for c in items]
    assert [_dt(c["created_at"]) for c in mcp_items] == [_dt(c["created_at"]) for c in items] == [_ts(10), _ts(20), _ts(30)]


async def scenario_add_comment(env: ParityEnv) -> None:
    ticket_a = await _ticket(env, "Komentarz MCP")
    ticket_b = await _ticket(env, "Komentarz v2")

    mcp_out = await env.mcp("add_comment", env.slug, _key(env.project, ticket_a), "Komentarz z MCP")
    response = await env.v2("add_comment", ticket_id=ticket_b.id, json={"content": "Komentarz z v2"})

    assert response.status_code == 201
    body = response.json()
    assert mcp_out["created_via_ai"] is True
    assert (body["content"], body["author"], body["created_via_ai"]) == ("Komentarz z v2", env.actor.user.email, True)
    for comment_id, ticket, content in ((mcp_out["id"], ticket_a, "Komentarz z MCP"), (body["id"], ticket_b, "Komentarz z v2")):
        row = (
            await env.db.execute(
                select(TicketComment.ticket_id, TicketComment.user_id, TicketComment.content, TicketComment.created_via_ai).where(
                    TicketComment.id == uuid.UUID(comment_id)
                )
            )
        ).one()
        assert tuple(row) == (ticket.id, env.actor.user.id, content, True)

    message = await env.mcp_error("add_comment", env.slug, str(ticket_a.id), "   ")
    _assert_problem(await env.v2("add_comment", ticket_id=ticket_b.id, json={"content": "   "}), 422, message)


async def scenario_list_acceptance_criteria(env: ParityEnv) -> None:
    empty = await _ticket(env, "Bez kryteriow")
    assert _parse_criteria(await env.mcp("list_acceptance_criteria", env.slug, str(empty.id))) == []
    empty_response = await env.v2("list_acceptance_criteria", ticket_id=empty.id)
    assert empty_response.status_code == 200
    assert empty_response.json()["items"] == []

    other = await _new_user(env, "criteria")
    ticket = await _ticket(env, "Z kryteriami")
    # Wstawiane w kolejności innej niż position.
    for position, description, done, via_ai in ((2, "Trzecie", False, False), (0, "Pierwsze", True, True), (1, "Drugie", False, False)):
        env.db.add(
            TicketAcceptanceCriterion(
                ticket_id=ticket.id,
                description=description,
                position=position,
                is_completed=done,
                completed_by_user_id=other.id if done else None,
                completed_at=_ts(5) if done else None,
                created_by_user_id=other.id,
                created_via_ai=via_ai,
            )
        )
    await env.db.flush()

    mcp_rows = _parse_criteria(await env.mcp("list_acceptance_criteria", env.slug, _key(env.project, ticket)))
    response = await env.v2("list_acceptance_criteria", ticket_id=ticket.id)

    assert response.status_code == 200
    items = response.json()["items"]
    expected = [("Pierwsze", True, True), ("Drugie", False, False), ("Trzecie", False, False)]
    assert [(c["description"], c["is_completed"], c["created_via_ai"]) for c in mcp_rows] == expected
    assert [(c["description"], c["is_completed"], c["created_via_ai"]) for c in items] == expected
    assert [c["id"] for c in mcp_rows] == [c["id"] for c in items]
    assert [c["position"] for c in items] == [0, 1, 2]
    # Znana różnica: autor kryterium jest tylko w tekście MCP.
    assert {c["author"] for c in mcp_rows} == {other.email}


async def scenario_add_acceptance_criterion(env: ParityEnv) -> None:
    tickets = [await _ticket(env, "Kryterium MCP"), await _ticket(env, "Kryterium v2")]
    for ticket in tickets:
        env.db.add(TicketAcceptanceCriterion(ticket_id=ticket.id, description="Istniejace", position=0, created_by_user_id=env.actor.user.id))
    await env.db.flush()

    mcp_out = await env.mcp("add_acceptance_criterion", env.slug, str(tickets[0].id), "Nowe z MCP")
    response = await env.v2("add_acceptance_criterion", ticket_id=tickets[1].id, json={"description": "Nowe z v2"})

    assert response.status_code == 201
    body = response.json()
    assert (mcp_out["position"], body["position"]) == (1, 1)
    assert (mcp_out["created_via_ai"], body["created_via_ai"]) == (True, True)
    assert (body["description"], body["is_completed"]) == ("Nowe z v2", False)
    assert (await _ticket_state(env, tickets[0].id))["criteria"] == ["Istniejace", "Nowe z MCP"]
    assert (await _ticket_state(env, tickets[1].id))["criteria"] == ["Istniejace", "Nowe z v2"]

    message = await env.mcp_error("add_acceptance_criterion", env.slug, str(tickets[0].id), "   ")
    _assert_problem(await env.v2("add_acceptance_criterion", ticket_id=tickets[1].id, json={"description": "   "}), 422, message)


async def _criterion_state(env: ParityEnv, criterion_id: uuid.UUID | str) -> dict[str, Any]:
    row = (
        await env.db.execute(
            select(
                TicketAcceptanceCriterion.description,
                TicketAcceptanceCriterion.is_completed,
                TicketAcceptanceCriterion.completed_by_user_id,
                TicketAcceptanceCriterion.completed_via_ai,
            ).where(TicketAcceptanceCriterion.id == uuid.UUID(str(criterion_id)))
        )
    ).one()
    return dict(row._mapping)


async def scenario_update_acceptance_criterion(env: ParityEnv) -> None:
    tickets = [await _ticket(env, "Aktualizacja kryterium MCP"), await _ticket(env, "Aktualizacja kryterium v2")]
    criteria = []
    for ticket in tickets:
        criterion = TicketAcceptanceCriterion(ticket_id=ticket.id, description="Stary opis", position=0, created_by_user_id=env.actor.user.id)
        env.db.add(criterion)
        criteria.append(criterion)
    await env.db.flush()

    mcp_out = await env.mcp(
        "update_acceptance_criterion", env.slug, str(tickets[0].id), str(criteria[0].id), description="Nowy opis", is_completed=True
    )
    response = await env.v2(
        "update_acceptance_criterion", ticket_id=tickets[1].id, criterion_id=criteria[1].id, json={"description": "Nowy opis", "is_completed": True}
    )

    assert response.status_code == 200
    body = response.json()
    assert (mcp_out["description"], mcp_out["is_completed"]) == ("Nowy opis", True)
    assert (body["description"], body["is_completed"]) == ("Nowy opis", True)
    done_a, done_b = await _criterion_state(env, criteria[0].id), await _criterion_state(env, criteria[1].id)
    assert done_a == done_b == {"description": "Nowy opis", "is_completed": True, "completed_by_user_id": env.actor.user.id, "completed_via_ai": True}

    # Brak parametru = bez zmian: sam opis nie odznacza ukończenia.
    await env.mcp("update_acceptance_criterion", env.slug, str(tickets[0].id), str(criteria[0].id), description="Tylko opis")
    only_description = await env.v2(
        "update_acceptance_criterion", ticket_id=tickets[1].id, criterion_id=criteria[1].id, json={"description": "Tylko opis"}
    )
    assert only_description.json()["is_completed"] is True
    kept_a, kept_b = await _criterion_state(env, criteria[0].id), await _criterion_state(env, criteria[1].id)
    assert kept_a == kept_b == {**done_a, "description": "Tylko opis"}

    await env.mcp("update_acceptance_criterion", env.slug, str(tickets[0].id), str(criteria[0].id), is_completed=False)
    reopened = await env.v2("update_acceptance_criterion", ticket_id=tickets[1].id, criterion_id=criteria[1].id, json={"is_completed": False})
    assert reopened.json()["description"] == "Tylko opis"
    reopened_a, reopened_b = await _criterion_state(env, criteria[0].id), await _criterion_state(env, criteria[1].id)
    assert reopened_a == reopened_b == {"description": "Tylko opis", "is_completed": False, "completed_by_user_id": None, "completed_via_ai": False}

    unknown = uuid.uuid4()
    message = await env.mcp_error("update_acceptance_criterion", env.slug, str(tickets[0].id), str(unknown), description="x")
    _assert_problem(
        await env.v2("update_acceptance_criterion", ticket_id=tickets[1].id, criterion_id=unknown, json={"description": "x"}), 404, message
    )


async def scenario_delete_acceptance_criterion(env: ParityEnv) -> None:
    tickets = [await _ticket(env, "Usuniecie kryterium MCP"), await _ticket(env, "Usuniecie kryterium v2")]
    doomed, kept = [], []
    for ticket in tickets:
        for position, bucket in ((0, doomed), (1, kept)):
            criterion = TicketAcceptanceCriterion(
                ticket_id=ticket.id, description=f"Kryterium {position}", position=position, created_by_user_id=env.actor.user.id
            )
            env.db.add(criterion)
            bucket.append(criterion)
    await env.db.flush()

    mcp_out = await env.mcp("delete_acceptance_criterion", env.slug, str(tickets[0].id), str(doomed[0].id))
    response = await env.v2("delete_acceptance_criterion", ticket_id=tickets[1].id, criterion_id=doomed[1].id)

    assert response.status_code == 204
    assert response.content == b""
    assert mcp_out["id"] == str(doomed[0].id)
    for ticket in tickets:
        assert (await _ticket_state(env, ticket.id))["criteria"] == ["Kryterium 1"]

    message = await env.mcp_error("delete_acceptance_criterion", env.slug, str(tickets[0].id), str(doomed[0].id))
    _assert_problem(await env.v2("delete_acceptance_criterion", ticket_id=tickets[1].id, criterion_id=doomed[1].id), 404, message)


# ---------------------------------------------------------------------------
# Scenariusze: Sprints (7)
# ---------------------------------------------------------------------------


async def scenario_list_sprints(env: ParityEnv) -> None:
    # created_at wstawiane w kolejności (Y, X, Z); serwis sortuje malejąco po created_at: Z, Y, X.
    active = await _sprint(env, "Sprint Y", status="active", goal="Cel Y", created_at=_ts(30))
    planned = await _sprint(env, "Sprint X", status="planning", end=None, created_at=_ts(10))
    done = await _sprint(env, "Sprint Z", status="completed", created_at=_ts(40))
    await _ticket(env, "W Y 1", sprint_id=active.id, story_points=3)
    await _ticket(env, "W Y 2", sprint_id=active.id, story_points=5)
    await _ticket(env, "W Z bez punktow", sprint_id=done.id)
    expected = [
        (str(done.id), "Sprint Z", "completed", 1, 0),
        (str(active.id), "Sprint Y", "active", 2, 8),
        (str(planned.id), "Sprint X", "planning", 0, 0),
    ]

    def summarize(items: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        return [(s["id"], s["name"], s["status"], s["ticket_count"], s["story_points_total"]) for s in items]

    mcp_items = await env.mcp("list_sprints", env.slug)
    response = await env.v2("list_sprints")

    assert response.status_code == 200
    items = response.json()["items"]
    assert summarize(mcp_items) == summarize(items) == expected
    for mcp_item, item in zip(mcp_items, items, strict=True):
        assert (mcp_item["goal"], mcp_item["start_date"], mcp_item["end_date"]) == (item["goal"], item["start_date"], item["end_date"])
        assert _dt(mcp_item["created_at"]) == _dt(item["created_at"])
    assert (items[2]["goal"], items[2]["end_date"]) == (None, None)

    filtered_mcp = await env.mcp("list_sprints", env.slug, status="active")
    filtered_v2 = (await env.v2("list_sprints", params={"status": "active"})).json()["items"]
    assert summarize(filtered_mcp) == summarize(filtered_v2) == [expected[1]]


async def scenario_get_sprint(env: ParityEnv) -> None:
    full = await _sprint(env, "Sprint pelny", status="active", goal="Cel sprintu")
    minimal = await _sprint(env, "Sprint minimalny", end=None)
    await _ticket(env, "Do zrobienia", status="todo", story_points=3, sprint_id=full.id)
    await _ticket(env, "Zrobione", status="done", story_points=5, sprint_id=full.id)

    for sprint, goal, end in ((full, "Cel sprintu", "2026-10-14"), (minimal, None, None)):
        parsed = _parse_sprint(await env.mcp("get_sprint", env.slug, str(sprint.id)))
        response = await env.v2("get_sprint", sprint_id=sprint.id)
        assert response.status_code == 200
        body = response.json()
        for key in ("id", "name", "status", "start_date", "end_date", "goal"):
            assert parsed[key] == body[key], key
        assert (body["goal"], body["end_date"], body["start_date"]) == (goal, end, "2026-10-01")
    # Znana różnica: sumy punktów i tabela ticketów są tylko w tekście MCP.
    assert _parse_sprint(await env.mcp("get_sprint", env.slug, str(full.id)))["total_sp"] == 8
    assert _parse_sprint(await env.mcp("get_sprint", env.slug, str(full.id)))["done_sp"] == 5

    unknown = uuid.uuid4()
    message = await env.mcp_error("get_sprint", env.slug, str(unknown))
    _assert_problem(await env.v2("get_sprint", sprint_id=unknown), 404, message)


async def scenario_create_sprint(env: ParityEnv) -> None:
    mcp_out = await env.mcp("create_sprint", env.slug, "Sprint MCP", "2026-10-01", goal="Cel MCP", end_date="2026-10-15")
    response = await env.v2("create_sprint", json={"name": "Sprint v2", "start_date": "2026-10-01", "goal": "Cel v2", "end_date": "2026-10-15"})

    assert response.status_code == 201
    body = response.json()
    assert (mcp_out["name"], mcp_out["status"]) == ("Sprint MCP", "planning")
    assert (body["name"], body["status"], body["goal"], body["start_date"], body["end_date"]) == (
        "Sprint v2",
        "planning",
        "Cel v2",
        "2026-10-01",
        "2026-10-15",
    )
    state_a, state_b = await _sprint_state(env, mcp_out["id"]), await _sprint_state(env, body["id"])
    assert _without(state_a, "name", "goal") == _without(state_b, "name", "goal")
    assert (state_a["goal"], state_a["end_date"], state_a["status"]) == ("Cel MCP", date(2026, 10, 15), "planning")

    # Bez celu i końca: oba kanały zostawiają null.
    minimal_mcp = await env.mcp("create_sprint", env.slug, "Minimalny MCP", "2026-11-01")
    minimal_v2 = (await env.v2("create_sprint", json={"name": "Minimalny v2", "start_date": "2026-11-01"})).json()
    for created in (minimal_mcp, minimal_v2):
        state = await _sprint_state(env, created["id"])
        assert (state["goal"], state["end_date"], state["status"]) == (None, None, "planning")

    message = await env.mcp_error("create_sprint", env.slug, "   ", "2026-10-01")
    _assert_problem(await env.v2("create_sprint", json={"name": "   ", "start_date": "2026-10-01"}), 422, message)


async def scenario_update_sprint(env: ParityEnv) -> None:
    sprint_a = await _sprint(env, "Stary MCP", goal="Stary cel")
    sprint_b = await _sprint(env, "Stary v2", goal="Stary cel")
    changes = {"goal": "Nowy cel", "start_date": "2026-10-02", "end_date": "2026-10-20"}

    mcp_out = await env.mcp("update_sprint", env.slug, str(sprint_a.id), name="Nowy MCP", **changes)
    response = await env.v2("update_sprint", sprint_id=sprint_b.id, json={"name": "Nowy v2", **changes})

    assert response.status_code == 200
    body = response.json()
    assert (mcp_out["name"], mcp_out["goal"], mcp_out["start_date"], mcp_out["end_date"], mcp_out["status"]) == (
        "Nowy MCP",
        "Nowy cel",
        "2026-10-02",
        "2026-10-20",
        "planning",
    )
    assert (body["name"], body["goal"], body["start_date"], body["end_date"], body["status"]) == (
        "Nowy v2",
        "Nowy cel",
        "2026-10-02",
        "2026-10-20",
        "planning",
    )
    assert _dt(mcp_out["created_at"]) == _dt((await env.v2("get_sprint", sprint_id=sprint_a.id)).json()["created_at"])

    # Brak parametru = bez zmian: sama nazwa nie rusza celu ani dat.
    await env.mcp("update_sprint", env.slug, str(sprint_a.id), name="Tylko nazwa MCP")
    assert (await env.v2("update_sprint", sprint_id=sprint_b.id, json={"name": "Tylko nazwa v2"})).status_code == 200
    for sprint in (sprint_a, sprint_b):
        state = await _sprint_state(env, sprint.id)
        assert (state["goal"], state["start_date"], state["end_date"]) == ("Nowy cel", date(2026, 10, 2), date(2026, 10, 20))

    message = await env.mcp_error("update_sprint", env.slug, str(sprint_a.id), end_date="2026-10-01")
    _assert_problem(await env.v2("update_sprint", sprint_id=sprint_b.id, json={"end_date": "2026-10-01"}), 422, message)


async def scenario_start_sprint(env: ParityEnv) -> None:
    project_b = await _extra_project(env, "start-b")
    sprint_a = await _sprint(env, "Sprint do startu")
    sprint_b = await _sprint(env, "Sprint do startu", project=project_b)

    mcp_out = await env.mcp("start_sprint", env.slug, str(sprint_a.id))
    response = await env.v2("start_sprint", slug=project_b.slug, sprint_id=sprint_b.id)

    assert response.status_code == 200
    assert mcp_out["sprint_id"] == str(sprint_a.id)
    assert response.json()["status"] == "active"
    assert (await _sprint_state(env, sprint_a.id))["status"] == "active"
    assert (await _sprint_state(env, sprint_b.id))["status"] == "active"

    # Drugi aktywny sprint w tym samym projekcie jest odrzucany tym samym komunikatem.
    second_a = await _sprint(env, "Drugi sprint")
    second_b = await _sprint(env, "Drugi sprint", project=project_b)
    message = await env.mcp_error("start_sprint", env.slug, str(second_a.id))
    _assert_problem(await env.v2("start_sprint", slug=project_b.slug, sprint_id=second_b.id), 422, message)
    assert (await _sprint_state(env, second_a.id))["status"] == "planning"


async def scenario_complete_sprint(env: ParityEnv) -> None:
    project_b = await _extra_project(env, "complete-b")
    pairs = []
    for project in (env.project, project_b):
        sprint = await _sprint(env, "Sprint do zakonczenia", project=project, status="active")
        unfinished = await _ticket(env, "Niedokonczony", project=project, status="in_progress", sprint_id=sprint.id)
        finished = await _ticket(env, "Dokonczony", project=project, status="done", sprint_id=sprint.id)
        pairs.append((sprint, unfinished, finished))

    mcp_out = await env.mcp("complete_sprint", env.slug, str(pairs[0][0].id))
    response = await env.v2("complete_sprint", slug=project_b.slug, sprint_id=pairs[1][0].id)

    assert response.status_code == 200
    assert mcp_out["sprint_id"] == str(pairs[0][0].id)
    assert response.json()["status"] == "completed"
    for sprint, unfinished, finished in pairs:
        assert (await _sprint_state(env, sprint.id))["status"] == "completed"
        unfinished_state, finished_state = await _ticket_state(env, unfinished.id), await _ticket_state(env, finished.id)
        assert (unfinished_state["status"], unfinished_state["sprint_id"]) == ("backlog", None)
        assert (finished_state["status"], finished_state["sprint_id"]) == ("done", sprint.id)

    planning_a = await _sprint(env, "Tylko planowany")
    planning_b = await _sprint(env, "Tylko planowany", project=project_b)
    message = await env.mcp_error("complete_sprint", env.slug, str(planning_a.id))
    _assert_problem(await env.v2("complete_sprint", slug=project_b.slug, sprint_id=planning_b.id), 422, message)


async def scenario_get_burndown(env: ParityEnv) -> None:
    today = date.today()
    sprint = await _sprint(env, "Sprint Burndown", status="active", start=today - timedelta(days=3), end=today + timedelta(days=11))
    await _ticket(env, "Spalone", status="done", story_points=3, sprint_id=sprint.id)
    await _ticket(env, "Pozostale", status="todo", story_points=5, sprint_id=sprint.id)

    mcp_default = await env.mcp("get_burndown", env.slug)
    default_response = await env.v2("get_burndown")
    mcp_explicit = await env.mcp("get_burndown", env.slug, sprint_id=str(sprint.id))
    explicit_response = await env.v2("get_burndown", params={"sprint_id": str(sprint.id)})

    for mcp_data, response in ((mcp_default, default_response), (mcp_explicit, explicit_response)):
        assert response.status_code == 200
        body = response.json()
        for key in ("sprint", "ideal_line", "actual_line", "current_velocity", "forecast_completion", "on_track"):
            assert body[key] == mcp_data[key], key
    body = default_response.json()
    assert body["sprint"] == {
        "name": "Sprint Burndown",
        "start_date": (today - timedelta(days=3)).isoformat(),
        "end_date": (today + timedelta(days=11)).isoformat(),
        "total_story_points": 8,
    }
    assert len(body["ideal_line"]) == 15
    assert (body["ideal_line"][0]["remaining_points"], body["ideal_line"][-1]["remaining_points"]) == (8, 0)
    assert len(body["actual_line"]) == 4
    assert body["actual_line"][0]["remaining_points"] == 8

    no_sprint = await _extra_project(env, "burndown-none")
    message = await env.mcp_error("get_burndown", no_sprint.slug)
    _assert_problem(await env.v2("get_burndown", slug=no_sprint.slug), 404, message)
    unknown = uuid.uuid4()
    message = await env.mcp_error("get_burndown", env.slug, sprint_id=str(unknown))
    _assert_problem(await env.v2("get_burndown", params={"sprint_id": str(unknown)}), 404, message)


# ---------------------------------------------------------------------------
# Scenariusze: Wiki (8)
# ---------------------------------------------------------------------------


async def scenario_list_wiki_pages(env: ParityEnv) -> None:
    empty = await _extra_project(env, "wiki-empty")
    assert _parse_wiki_tree(await env.mcp("list_wiki_pages", empty.slug)) == []
    empty_response = await env.v2("list_wiki_pages", slug=empty.slug)
    assert empty_response.status_code == 200
    assert empty_response.json()["items"] == []

    # Wstawiane w kolejności (Alfa pos. 1, Bravo pos. 0, Dziecko, Wnuk); oczekiwana pre-order: Bravo, Dziecko, Wnuk, Alfa.
    alfa = await _wiki_page(env, "Alfa", "alfa", position=1)
    bravo = await _wiki_page(env, "Bravo", "bravo", position=0)
    child = await _wiki_page(env, "Dziecko", "dziecko", parent=bravo)
    grandchild = await _wiki_page(env, "Wnuk", "wnuk", parent=child)
    expected = [(str(bravo.id), "Bravo", 0), (str(child.id), "Dziecko", 1), (str(grandchild.id), "Wnuk", 2), (str(alfa.id), "Alfa", 0)]

    rows = _parse_wiki_tree(await env.mcp("list_wiki_pages", env.slug))
    response = await env.v2("list_wiki_pages")

    assert response.status_code == 200
    items = response.json()["items"]
    assert [(r["id"], r["title"], r["depth"]) for r in rows] == expected
    assert [(i["id"], i["title"], i["depth"]) for i in items] == expected
    assert [r["updated"] for r in rows] == [_dt(i["updated_at"]).date().isoformat() for i in items]
    assert [i["parent_id"] for i in items] == [None, str(bravo.id), str(child.id), None]


async def scenario_get_wiki_page(env: ParityEnv) -> None:
    author = await _new_user(env, "wiki-author")
    parent = await _wiki_page(env, "Rodzic", "rodzic")
    page = await _wiki_page(
        env, "Strona parytetu", "strona-parytetu", "# Naglowek\n\nTresc strony", parent=parent, position=3, is_ai_touched=True, created_by=author
    )

    mcp_out = await env.mcp("get_wiki_page", env.slug, str(page.id))
    response = await env.v2("get_wiki_page", page_id=page.id)

    assert response.status_code == 200
    body = response.json()
    for key in ("id", "title", "slug", "parent_id", "position", "content", "is_ai_touched", "created_by", "last_edited_by"):
        assert body[key] == mcp_out[key], key
    assert _dt(body["created_at"]) == _dt(mcp_out["created_at"])
    assert _dt(body["updated_at"]) == _dt(mcp_out["updated_at"])
    assert (body["title"], body["slug"], body["parent_id"], body["position"]) == ("Strona parytetu", "strona-parytetu", str(parent.id), 3)
    assert (body["content"], body["is_ai_touched"]) == ("# Naglowek\n\nTresc strony", True)
    assert (body["created_by"], body["last_edited_by"]) == (author.email, env.actor.user.email)

    unknown = uuid.uuid4()
    message = await env.mcp_error("get_wiki_page", env.slug, str(unknown))
    _assert_problem(await env.v2("get_wiki_page", page_id=unknown), 404, message)


async def scenario_create_wiki_page(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    parent = await _wiki_page(env, "Rodzic tworzenia", f"rodzic-tworzenia-{tag}")
    payload = {"content": "# Nowa tresc", "parent_id": str(parent.id), "position": 4, "is_public": True}

    mcp_out = await env.mcp("create_wiki_page", env.slug, f"Parytet mcp {tag}", **payload)
    response = await env.v2("create_wiki_page", json={"title": f"Parytet v2 {tag}", **payload})

    assert response.status_code == 201
    body = response.json()
    assert (mcp_out["title"], mcp_out["slug"], mcp_out["is_public"]) == (f"Parytet mcp {tag}", f"parytet-mcp-{tag}", True)
    assert (body["title"], body["slug"], body["is_public"], body["parent_id"], body["position"]) == (
        f"Parytet v2 {tag}",
        f"parytet-v2-{tag}",
        True,
        str(parent.id),
        4,
    )
    # Znana różnica: MCP oznacza stronę jako dotkniętą przez AI, v2 nie.
    assert (mcp_out["is_ai_touched"], body["is_ai_touched"]) == (True, False)
    state_a, state_b = await _wiki_state(env, mcp_out["id"]), await _wiki_state(env, body["id"])
    assert (state_a["is_ai_touched"], state_b["is_ai_touched"]) == (True, False)
    assert _without(state_a, "title", "slug", "minio_path", "is_ai_touched") == _without(state_b, "title", "slug", "minio_path", "is_ai_touched")
    assert (state_a["content"], state_a["created_by_id"], state_a["last_edited_by_id"]) == ("# Nowa tresc", env.actor.user.id, env.actor.user.id)
    assert (state_a["parent_id"], state_a["position"], state_a["is_public"]) == (parent.id, 4, True)

    # Wartości domyślne: strona prywatna na pozycji 0 w obu kanałach.
    default_mcp = await env.mcp("create_wiki_page", env.slug, f"Domyslna mcp {tag}", "tresc")
    default_v2 = (await env.v2("create_wiki_page", json={"title": f"Domyslna v2 {tag}", "content": "tresc"})).json()
    for created in (default_mcp, default_v2):
        state = await _wiki_state(env, created["id"])
        assert (state["is_public"], state["position"], state["parent_id"]) == (False, 0, None)


async def scenario_update_wiki_page(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    page_a = await _wiki_page(env, "Stara mcp", f"stara-mcp-{tag}", "stara tresc")
    page_b = await _wiki_page(env, "Stara v2", f"stara-v2-{tag}", "stara tresc")
    changes = {"content": "nowa tresc", "position": 7, "is_public": True}

    mcp_out = await env.mcp("update_wiki_page", env.slug, str(page_a.id), title=f"Nowa mcp {tag}", **changes)
    response = await env.v2("update_wiki_page", page_id=page_b.id, json={"title": f"Nowa v2 {tag}", **changes})

    assert response.status_code == 200
    body = response.json()
    assert (mcp_out["title"], mcp_out["is_public"]) == (f"Nowa mcp {tag}", True)
    assert (body["title"], body["slug"], body["position"], body["is_public"]) == (f"Nowa v2 {tag}", f"nowa-v2-{tag}", 7, True)
    state_a, state_b = await _wiki_state(env, page_a.id), await _wiki_state(env, page_b.id)
    assert (state_a["slug"], state_a["content"], state_a["position"]) == (f"nowa-mcp-{tag}", "nowa tresc", 7)
    assert _without(state_a, "title", "slug", "minio_path", "is_ai_touched") == _without(state_b, "title", "slug", "minio_path", "is_ai_touched")
    # Znana różnica: MCP ustawia is_ai_touched, v2 zostawia flagę bez zmian.
    assert (mcp_out["is_ai_touched"], state_a["is_ai_touched"], state_b["is_ai_touched"]) == (True, True, False)

    # Brak parametru = bez zmian: sama treść nie rusza tytułu, pozycji ani publikacji.
    await env.mcp("update_wiki_page", env.slug, str(page_a.id), content="tylko tresc mcp")
    assert (await env.v2("update_wiki_page", page_id=page_b.id, json={"content": "tylko tresc v2"})).status_code == 200
    kept_a, kept_b = await _wiki_state(env, page_a.id), await _wiki_state(env, page_b.id)
    assert (kept_a["content"], kept_b["content"]) == ("tylko tresc mcp", "tylko tresc v2")
    for kept in (kept_a, kept_b):
        assert (kept["position"], kept["is_public"]) == (7, True)
    assert (kept_a["title"], kept_b["title"]) == (f"Nowa mcp {tag}", f"Nowa v2 {tag}")

    unknown = uuid.uuid4()
    message = await env.mcp_error("update_wiki_page", env.slug, str(unknown), title="x")
    _assert_problem(await env.v2("update_wiki_page", page_id=unknown, json={"title": "x"}), 404, message)


async def scenario_delete_wiki_page(env: ParityEnv) -> None:
    trees = []
    for suffix in ("mcp", "v2"):
        root = await _wiki_page(env, f"Korzen {suffix}", f"korzen-{suffix}-{uuid.uuid4().hex[:6]}")
        child = await _wiki_page(env, f"Dziecko {suffix}", f"dziecko-{suffix}-{uuid.uuid4().hex[:6]}", parent=root)
        trees.append((root, child))
    survivor = await _wiki_page(env, "Zostaje", f"zostaje-{uuid.uuid4().hex[:6]}")
    paths = [[(await _wiki_state(env, page.id))["minio_path"] for page in tree] for tree in trees]

    mcp_out = await env.mcp("delete_wiki_page", env.slug, str(trees[0][0].id))
    response = await env.v2("delete_wiki_page", page_id=trees[1][0].id)

    assert response.status_code == 204
    assert response.content == b""
    assert "Korzen mcp" in mcp_out["message"]
    remaining = set((await env.db.execute(select(WikiPage.id).where(WikiPage.project_id == env.project.id))).scalars().all())
    assert remaining == {survivor.id}
    assert set(env.fx.deleted_paths) == {*paths[0], *paths[1]}

    unknown = uuid.uuid4()
    message = await env.mcp_error("delete_wiki_page", env.slug, str(unknown))
    _assert_problem(await env.v2("delete_wiki_page", page_id=unknown), 404, message)


async def scenario_search_wiki(env: ParityEnv) -> None:
    hit = {"id": str(uuid.uuid4()), "title": "Trafiona strona", "slug": "trafiona-strona", "snippet": "fragment tresci", "similarity": 0.87}
    search = AsyncMock(return_value=[hit])

    with patch("monolynx.services.embeddings.search_wiki_pages", search):
        mcp_items = await env.mcp("search_wiki", env.slug, "zapytanie parytetu", limit=3)
        response = await env.v2("search_wiki", params={"query": "zapytanie parytetu", "limit": 3})
        default_mcp = await env.mcp("search_wiki", env.slug, "domyslny limit")
        default_response = await env.v2("search_wiki", params={"query": "domyslny limit"})

    assert response.status_code == 200
    assert mcp_items == response.json() == [hit]
    assert default_response.json() == default_mcp == [hit]
    calls = search.await_args_list
    assert [(c.args[0], c.args[1], c.kwargs["limit"]) for c in calls] == [
        (env.project.id, "zapytanie parytetu", 3),
        (env.project.id, "zapytanie parytetu", 3),
        (env.project.id, "domyslny limit", 10),
        (env.project.id, "domyslny limit", 10),
    ]

    # Brak parametru query: v2 odrzuca zapytanie zamiast szukać pustej frazy.
    assert (await env.v2("search_wiki")).status_code == 422


async def scenario_get_wiki_config(env: ParityEnv) -> None:
    pristine = await _extra_project(env, "wikicfg-off")
    assert await env.mcp("get_wiki_config", pristine.slug) == {
        "wiki_llm_enabled": False,
        "index_page_id": None,
        "log_page_id": None,
        "schema_page_id": None,
    }
    pristine_response = await env.v2("get_wiki_config", slug=pristine.slug)
    assert pristine_response.status_code == 200
    assert pristine_response.json() == {"wiki_llm_enabled": False, "index_page_id": None, "log_page_id": None, "schema_page_id": None}

    env.project.wiki_llm_enabled = True
    index = await _wiki_page(env, "Katalog", "wiki-index")
    log = await _wiki_page(env, "Dziennik", "wiki-log")
    schema = await _wiki_page(env, "Regulamin", "wiki-schema")

    mcp_out = await env.mcp("get_wiki_config", env.slug)
    response = await env.v2("get_wiki_config")

    assert response.status_code == 200
    expected = {"wiki_llm_enabled": True, "index_page_id": str(index.id), "log_page_id": str(log.id), "schema_page_id": str(schema.id)}
    assert mcp_out == expected
    assert response.json() == expected


async def scenario_set_wiki_config(env: ParityEnv) -> None:
    project_b = await _extra_project(env, "wikiset-b")

    async def enabled(project: Project) -> bool:
        return (await env.db.execute(select(Project.wiki_llm_enabled).where(Project.id == project.id))).scalar_one()

    mcp_on = await env.mcp("set_wiki_config", env.slug, True)
    response_on = await env.v2("set_wiki_config", slug=project_b.slug, json={"enabled": True})

    assert response_on.status_code == 200
    assert mcp_on["wiki_llm_enabled"] is True
    assert response_on.json() == {"wiki_llm_enabled": True}
    assert await enabled(env.project) is True
    assert await enabled(project_b) is True

    mcp_off = await env.mcp("set_wiki_config", env.slug, False)
    response_off = await env.v2("set_wiki_config", slug=project_b.slug, json={"enabled": False})

    assert response_off.status_code == 200
    assert mcp_off["wiki_llm_enabled"] is False
    assert response_off.json() == {"wiki_llm_enabled": False}
    assert await enabled(env.project) is False
    assert await enabled(project_b) is False


# ---------------------------------------------------------------------------
# Blog (MON-261, MON-262): lista wpisów, szczegóły, częściowy zapis metadanych i lint
# ---------------------------------------------------------------------------

_BLOG_VIEW_KEYS = (
    "page_id",
    "title",
    "slug",
    "previous_slugs",
    "description",
    "published_at",
    "author_name",
    "tags",
    "lang",
    "translation_of_id",
    "cover_attachment_id",
    "last_verified_at",
    "is_public",
    "public_url",
    "markdown_url",
)


async def _blog_post(
    env: ParityEnv, title: str, slug: str, *, is_public: bool = False, with_post: bool = True, content: str = "# Tresc wpisu", **fields: Any
) -> WikiPage:
    page = await _wiki_page(env, title, slug, content)
    if is_public:
        page.is_public = True
    if with_post:
        env.db.add(BlogPost(page_id=page.id, slug=slug, **fields))
    await env.db.flush()
    return page


async def _blog_state(env: ParityEnv, page_id: uuid.UUID | str) -> dict[str, Any]:
    row = (
        await env.db.execute(
            select(
                BlogPost.slug,
                BlogPost.previous_slugs,
                BlogPost.description,
                BlogPost.published_at,
                BlogPost.author_name,
                BlogPost.tags,
                BlogPost.lang,
                BlogPost.translation_of_id,
                BlogPost.cover_attachment_id,
                BlogPost.last_verified_at,
            ).where(BlogPost.page_id == uuid.UUID(str(page_id)))
        )
    ).one()
    return dict(row._mapping)


def _assert_blog_view_equal(mcp_item: dict[str, Any], v2_item: dict[str, Any], *, skip: tuple[str, ...] = ()) -> None:
    """Pola widoku wpisu są identyczne w obu kanałach; data publikacji porównywana jako moment (inny zapis offsetu)."""
    assert set(mcp_item) >= set(_BLOG_VIEW_KEYS)
    assert set(v2_item) >= set(_BLOG_VIEW_KEYS)
    for key in _BLOG_VIEW_KEYS:
        if key in skip:
            continue
        if key == "published_at" and mcp_item[key] is not None:
            assert _dt(mcp_item[key]) == _dt(v2_item[key]), key
        else:
            assert mcp_item[key] == v2_item[key], key


async def scenario_list_blog_posts(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    published = await _blog_post(
        env, "Wpis publiczny", f"pub-{tag}", is_public=True, published_at=_ts(10), tags=["a"], description="Opis", last_verified_at=date(2026, 8, 1)
    )
    draft = await _blog_post(env, "Szkic wpisu", f"draft-{tag}", is_public=False)
    await _blog_post(env, "Strona bez wpisu", f"norow-{tag}", with_post=False)

    mcp_out = await env.mcp("list_blog_posts", env.slug)
    response = await env.v2("list_blog_posts")

    assert response.status_code == 200
    body = response.json()
    assert (body["total"], body["page"]) == (2, 1)
    assert [i["page_id"] for i in mcp_out] == [i["page_id"] for i in body["items"]] == [str(published.id), str(draft.id)]
    for mcp_item, v2_item in zip(mcp_out, body["items"], strict=True):
        _assert_blog_view_equal(mcp_item, v2_item)
    assert mcp_out[0]["public_url"] is not None
    assert mcp_out[1]["public_url"] is None

    mcp_draft = await env.mcp("list_blog_posts", env.slug, status="draft")
    v2_draft = await env.v2("list_blog_posts", params={"status": "draft"})
    assert [i["page_id"] for i in mcp_draft] == [i["page_id"] for i in v2_draft.json()["items"]] == [str(draft.id)]

    message = await env.mcp_error("list_blog_posts", env.slug, status="archived")
    _assert_problem(await env.v2("list_blog_posts", params={"status": "archived"}), 422, message)


async def scenario_get_blog_post(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    page = await _blog_post(
        env,
        "Wpis ze szczegółami",
        f"detail-{tag}",
        is_public=True,
        content="# Treść szczegółów",
        published_at=_ts(5),
        tags=["x", "y"],
        lang="en",
        last_verified_at=date(2026, 8, 2),
    )

    mcp_out = await env.mcp("get_blog_post", env.slug, str(page.id))
    response = await env.v2("get_blog_post", page_id=page.id)

    assert response.status_code == 200
    body = response.json()
    _assert_blog_view_equal(mcp_out, body)
    assert (mcp_out["content"], body["content"]) == ("# Treść szczegółów", "# Treść szczegółów")
    assert (body["lang"], body["tags"], body["is_public"]) == ("en", ["x", "y"], True)

    unknown = uuid.uuid4()
    message = await env.mcp_error("get_blog_post", env.slug, str(unknown))
    _assert_problem(await env.v2("get_blog_post", page_id=unknown), 404, message)

    no_row = await _blog_post(env, "Strona bez wpisu", f"norow-{tag}", with_post=False)
    no_row_message = await env.mcp_error("get_blog_post", env.slug, str(no_row.id))
    assert no_row_message == "Wpis bloga nie istnieje"
    _assert_problem(await env.v2("get_blog_post", page_id=no_row.id), 404, no_row_message)


async def scenario_lint_blog_post(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    page = await _blog_post(
        env,
        "Wpis do lintu",
        f"lint-{tag}",
        content="# Tytuł\n\nWstęp bez streszczenia.\n",
        description="Za krótki opis",
    )

    mcp_out = await env.mcp("lint_blog_post", env.slug, str(page.id))
    response = await env.v2("lint_blog_post", page_id=page.id)

    assert response.status_code == 200
    body = response.json()
    assert mcp_out["issues"] == body["issues"]
    assert mcp_out["score"] == body["score"]
    assert (mcp_out["error_count"], mcp_out["warning_count"], mcp_out["info_count"]) == (
        body["error_count"],
        body["warning_count"],
        body["info_count"],
    )
    assert mcp_out["page_id"] == body["page_id"] == str(page.id)
    levels = {issue["level"] for issue in body["issues"]}
    assert {"error", "warning"} <= levels
    assert {"description-length", "tldr-missing", "last-verified-missing"} <= {issue["rule"] for issue in body["issues"]}
    assert body["score"] < 100
    # Różnica kanałów z `known_differences`: tabela jest tylko w MCP.
    assert "table" in mcp_out
    assert "table" not in body

    unknown = uuid.uuid4()
    message = await env.mcp_error("lint_blog_post", env.slug, str(unknown))
    _assert_problem(await env.v2("lint_blog_post", page_id=unknown), 404, message)

    no_row = await _blog_post(env, "Strona bez wpisu", f"lint-norow-{tag}", with_post=False)
    no_row_message = await env.mcp_error("lint_blog_post", env.slug, str(no_row.id))
    assert no_row_message == "Wpis bloga nie istnieje"
    _assert_problem(await env.v2("lint_blog_post", page_id=no_row.id), 404, no_row_message)


async def scenario_set_blog_post_meta(env: ParityEnv) -> None:
    tag = uuid.uuid4().hex[:8]
    original = await _blog_post(env, "Oryginał", f"orig-{tag}")
    page_a = await _blog_post(env, "Wpis MCP", f"a-{tag}", description="Stary", tags=["x"], last_verified_at=date(2026, 7, 1))
    page_b = await _blog_post(env, "Wpis v2", f"b-{tag}", description="Stary", tags=["x"], last_verified_at=date(2026, 7, 1))
    original_id, a_id, b_id = original.id, page_a.id, page_b.id
    payload = {
        "description": "Nowy opis",
        "author_name": "Ada",
        "tags": ["t1", "t2"],
        "lang": "en",
        "translation_of_id": str(original_id),
        "last_verified_at": "2026-08-09",
    }

    mcp_out = await env.mcp("set_blog_post_meta", env.slug, str(a_id), slug=f"mcp-{tag}", **payload)
    response = await env.v2("set_blog_post_meta", page_id=b_id, json={"slug": f"v2-{tag}", **payload})

    assert response.status_code == 200
    body = response.json()
    _assert_blog_view_equal(mcp_out, body, skip=("page_id", "title", "slug", "previous_slugs", "public_url", "markdown_url"))
    assert (mcp_out["slug"], body["slug"]) == (f"mcp-{tag}", f"v2-{tag}")
    assert (mcp_out["previous_slugs"], body["previous_slugs"]) == ([f"a-{tag}"], [f"b-{tag}"])
    state_a, state_b = await _blog_state(env, a_id), await _blog_state(env, b_id)
    assert _without(state_a, "slug", "previous_slugs") == _without(state_b, "slug", "previous_slugs")
    assert (state_a["description"], state_a["tags"], state_a["lang"], state_a["translation_of_id"]) == ("Nowy opis", ["t1", "t2"], "en", original_id)
    assert state_a["last_verified_at"] == date(2026, 8, 9)

    # Czyszczenie: MCP pustym tekstem, v2 wartością null; tags=[] czyści w obu kanałach.
    mcp_clear = await env.mcp("set_blog_post_meta", env.slug, str(a_id), translation_of_id="", last_verified_at="", tags=[])
    v2_clear = await env.v2("set_blog_post_meta", page_id=b_id, json={"translation_of_id": None, "last_verified_at": None, "tags": []})

    assert v2_clear.status_code == 200
    for key in ("translation_of_id", "last_verified_at"):
        assert (mcp_clear[key], v2_clear.json()[key]) == (None, None)
    assert (mcp_clear["tags"], v2_clear.json()["tags"]) == ([], [])
    cleared_a, cleared_b = await _blog_state(env, a_id), await _blog_state(env, b_id)
    assert _without(cleared_a, "slug", "previous_slugs") == _without(cleared_b, "slug", "previous_slugs")
    assert (cleared_a["translation_of_id"], cleared_a["last_verified_at"], cleared_a["tags"]) == (None, None, [])
    assert cleared_a["description"] == "Nowy opis"

    # Błędy: ta sama treść komunikatu, w v2 jako problem+json z właściwym kodem.
    bad_lang = await env.mcp_error("set_blog_post_meta", env.slug, str(a_id), lang="de")
    _assert_problem(await env.v2("set_blog_post_meta", page_id=b_id, json={"lang": "de"}), 422, bad_lang)
    unknown = uuid.uuid4()
    not_found = await env.mcp_error("set_blog_post_meta", env.slug, str(unknown), description="x")
    _assert_problem(await env.v2("set_blog_post_meta", page_id=unknown, json={"description": "x"}), 404, not_found)


# ---------------------------------------------------------------------------
# Graf Połączeń (MON-248): Neo4j nie działa w testach, oba kanały wołą te same funkcje
# `monolynx.services.graph`, więc jeden zestaw mocków obsługuje MCP i v2.
# ---------------------------------------------------------------------------

_GRAPH_SERVICE_FUNCTIONS = ("list_nodes", "get_node", "get_neighbors", "get_graph", "search_graph", "find_path", "get_stats")


@contextmanager
def _graph_backend(*, enabled: bool = True, **returns: Any) -> Iterator[SimpleNamespace]:
    """Podmienia funkcje serwisu grafu na mocki z podpisem oryginału (autospec): asercje na wywołaniu
    nie rozróżniają argumentu pozycyjnego od nazwanego, a `reset()` pozwala sprawdzić każdy kanał osobno.
    Zwracane wartości leżą w `returns` (zmiana w trakcie scenariusza nie wymaga dotykania atrapy)."""
    state: dict[str, Any] = dict(returns)
    with ExitStack() as stack:
        mocks = {
            name: stack.enter_context(patch.object(graph_service, name, autospec=True, side_effect=lambda *a, _n=name, **k: state.get(_n)))
            for name in _GRAPH_SERVICE_FUNCTIONS
        }
        is_enabled = stack.enter_context(patch.object(graph_service, "is_enabled", autospec=True, return_value=enabled))

        def reset() -> None:
            for mock in (*mocks.values(), is_enabled):
                mock.reset_mock()

        yield SimpleNamespace(is_enabled=is_enabled, reset=reset, returns=state, **mocks)


def _graph_node(env: ParityEnv, node_id: str, name: str, node_type: str = "File", **fields: Any) -> dict[str, Any]:
    return {
        "id": node_id,
        "project_id": str(env.project.id),
        "name": name,
        "type": node_type,
        "file_path": fields.get("file_path"),
        "line_number": fields.get("line_number"),
        "metadata": fields.get("metadata", {}),
    }


def _graph_edge(source_id: str, target_id: str, edge_type: str = "CALLS", metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"source_id": source_id, "target_id": target_id, "type": edge_type, "metadata": metadata or {}}


def _dsl_of(body: Mapping[str, Any]) -> str:
    return graph_service.format_graph_dsl({key: body[key] for key in ("nodes", "edges", "depth_map") if key in body})


async def scenario_list_graph_nodes(env: ParityEnv) -> None:
    nodes = [
        _graph_node(env, "n1", "alfa.py", "File", file_path="app/alfa.py", line_number=1),
        _graph_node(env, "n2", "Alfa", "Class", file_path="app/alfa.py", line_number=7, metadata={"docstring": "opis"}),
    ]
    other = await _extra_project(env, "graph-list")

    with _graph_backend(list_nodes=nodes) as g:
        mcp_out = await env.mcp("list_graph_nodes", env.slug, type="File", search="alf", limit=5)
        g.list_nodes.assert_awaited_once_with(env.project.id, type_filter="File", search="alf", limit=5)
        g.reset()
        response = await env.v2("list_graph_nodes", params={"type": "File", "search": "alf", "limit": 5})
        g.list_nodes.assert_awaited_once_with(env.project.id, type_filter="File", search="alf", limit=5)
        g.reset()

        default_mcp = await env.mcp("list_graph_nodes", env.slug)
        g.list_nodes.assert_awaited_once_with(env.project.id, type_filter=None, search=None, limit=100)
        g.reset()
        default_response = await env.v2("list_graph_nodes")
        g.list_nodes.assert_awaited_once_with(env.project.id, type_filter=None, search=None, limit=100)
        g.reset()

        other_response = await env.v2("list_graph_nodes", slug=other.slug)
        g.list_nodes.assert_awaited_once_with(other.id, type_filter=None, search=None, limit=100)

    assert response.status_code == 200
    assert mcp_out == response.json() == nodes
    assert default_response.status_code == 200
    assert default_mcp == default_response.json() == nodes
    assert other_response.status_code == 200


async def scenario_get_graph_node(env: ParityEnv) -> None:
    root = _graph_node(env, "n1", "Alfa", "Class", file_path="app/alfa.py", line_number=7)
    child = _graph_node(env, "n2", "Alfa.run", "Method", file_path="app/alfa.py", line_number=12)
    edge = _graph_edge("n1", "n2", "CONTAINS", {"confidence": "EXTRACTED", "source_relation": "contains"})
    neighbors = {"nodes": [root, child], "edges": [edge], "depth_map": {"n1": 0, "n2": 1}}

    with _graph_backend(get_node=root, get_neighbors=neighbors) as g:
        text = await env.mcp("get_graph_node", env.slug, "n1", depth=2, relation_types=["CONTAINS", "CALLS"], node_types=["Class", "Method"])
        g.get_node.assert_awaited_once_with(env.project.id, "n1")
        g.get_neighbors.assert_awaited_once_with(env.project.id, "n1", depth=2, relation_types=["CONTAINS", "CALLS"], node_types=["Class", "Method"])
        g.reset()
        response = await env.v2(
            "get_graph_node",
            node_id="n1",
            params={"depth": 2, "relation_types": ["CONTAINS", "CALLS"], "node_types": ["Class", "Method"]},
        )
        g.get_node.assert_awaited_once_with(env.project.id, "n1")
        g.get_neighbors.assert_awaited_once_with(env.project.id, "n1", depth=2, relation_types=["CONTAINS", "CALLS"], node_types=["Class", "Method"])
        g.reset()

        default_text = await env.mcp("get_graph_node", env.slug, "n1")
        g.get_neighbors.assert_awaited_once_with(env.project.id, "n1", depth=1, relation_types=None, node_types=None)
        g.reset()
        default_response = await env.v2("get_graph_node", node_id="n1")
        g.get_neighbors.assert_awaited_once_with(env.project.id, "n1", depth=1, relation_types=None, node_types=None)
        g.reset()

        g.returns["get_node"] = None
        message = await env.mcp_error("get_graph_node", env.slug, "brak")
        g.get_neighbors.assert_not_awaited()
        missing = await env.v2("get_graph_node", node_id="brak")
        g.get_neighbors.assert_not_awaited()

    assert response.status_code == 200
    body = response.json()
    assert body["node"] == root
    assert body["depth_map"] == neighbors["depth_map"]
    assert [n["id"] for n in body["nodes"]] == ["n1", "n2"]
    assert (body["edges"][0]["source_id"], body["edges"][0]["target_id"], body["edges"][0]["type"]) == ("n1", "n2", "CONTAINS")
    assert (body["edges"][0]["confidence"], body["edges"][0]["source_relation"]) == ("EXTRACTED", "contains")
    assert text == _dsl_of(body)
    assert default_response.status_code == 200
    assert default_text == _dsl_of(default_response.json()) == text
    assert message == "Node nie istnieje"
    _assert_problem(missing, 404, message)


async def scenario_query_graph(env: ParityEnv) -> None:
    alfa = _graph_node(env, "n1", "Alfa", "Class", file_path="app/alfa.py", line_number=7)
    beta = _graph_node(env, "n2", "Beta", "Function")
    found = {"nodes": [alfa, beta], "edges": [_graph_edge("n1", "n2", "CALLS", {"confidence": "INFERRED"})]}
    whole = {"nodes": [beta, alfa], "edges": []}

    with _graph_backend(search_graph=found, get_graph=whole) as g:
        mcp_found = await env.mcp("query_graph", env.slug, node_type="Class", search="alf", limit=7)
        g.search_graph.assert_awaited_once_with(env.project.id, search="alf", type_filter="Class", limit=7)
        g.get_graph.assert_not_awaited()
        g.reset()
        found_response = await env.v2("query_graph", params={"node_type": "Class", "search": "alf", "limit": 7})
        g.search_graph.assert_awaited_once_with(env.project.id, search="alf", type_filter="Class", limit=7)
        g.get_graph.assert_not_awaited()
        g.reset()

        mcp_whole = await env.mcp("query_graph", env.slug, node_type="Function", limit=9)
        g.get_graph.assert_awaited_once_with(env.project.id, type_filter="Function", limit=9)
        g.search_graph.assert_not_awaited()
        g.reset()
        whole_response = await env.v2("query_graph", params={"node_type": "Function", "limit": 9})
        g.get_graph.assert_awaited_once_with(env.project.id, type_filter="Function", limit=9)
        g.search_graph.assert_not_awaited()
        g.reset()

        default_mcp = await env.mcp("query_graph", env.slug)
        g.get_graph.assert_awaited_once_with(env.project.id, type_filter=None, limit=200)
        g.reset()
        default_response = await env.v2("query_graph")
        g.get_graph.assert_awaited_once_with(env.project.id, type_filter=None, limit=200)
        g.reset()

        message = await env.mcp_error("query_graph", env.slug, search="   ")
        blank = await env.v2("query_graph", params={"search": "   "})
        g.search_graph.assert_not_awaited()
        g.get_graph.assert_not_awaited()

    assert found_response.status_code == 200
    assert mcp_found == _dsl_of(found_response.json())
    assert [n["id"] for n in found_response.json()["nodes"]] == ["n1", "n2"]
    assert whole_response.status_code == 200
    assert mcp_whole == _dsl_of(whole_response.json())
    assert [n["id"] for n in whole_response.json()["nodes"]] == ["n2", "n1"]
    assert default_response.status_code == 200
    assert default_mcp == _dsl_of(default_response.json()) == mcp_whole
    assert message == "search nie moze byc pusty"
    _assert_problem(blank, 422, message)


async def scenario_find_graph_path(env: ParityEnv) -> None:
    start = _graph_node(env, "a", "Start", "Function")
    end = _graph_node(env, "b", "Koniec", "Function")
    path = {"nodes": [start, end], "edges": [_graph_edge("a", "b", "CALLS")]}

    with _graph_backend(find_path=path) as g:
        mcp_text = await env.mcp("find_graph_path", env.slug, "a", "b")
        g.find_path.assert_awaited_once_with(env.project.id, "a", "b")
        g.reset()
        response = await env.v2("find_graph_path", params={"source_id": "a", "target_id": "b"})
        g.find_path.assert_awaited_once_with(env.project.id, "a", "b")
        g.reset()

        g.returns["find_path"] = {"nodes": [], "edges": []}
        empty_mcp = await env.mcp("find_graph_path", env.slug, "a", "z")
        empty_response = await env.v2("find_graph_path", params={"source_id": "a", "target_id": "z"})
        g.reset()

        missing_target = await env.v2("find_graph_path", params={"source_id": "a"})
        g.find_path.assert_not_awaited()

    assert response.status_code == 200
    assert response.json()["edges"][0]["type"] == "CALLS"
    assert [n["id"] for n in response.json()["nodes"]] == ["a", "b"]
    assert mcp_text == _dsl_of(response.json())
    assert empty_response.status_code == 200
    assert empty_response.json() == {"nodes": [], "edges": []}
    assert empty_mcp == _dsl_of(empty_response.json())
    assert missing_target.status_code == 422


async def scenario_get_graph_stats(env: ParityEnv) -> None:
    stats = {
        "total_nodes": 3,
        "total_edges": 2,
        "nodes_by_type": {"File": 1, "Class": 1, "Function": 1, "Method": 0, "Const": 0, "Module": 0},
        "edges_by_type": {"CONTAINS": 1, "CALLS": 1},
    }
    other = await _extra_project(env, "graph-stats")

    with _graph_backend(get_stats=stats) as g:
        mcp_out = await env.mcp("get_graph_stats", env.slug)
        g.get_stats.assert_awaited_once_with(env.project.id)
        g.reset()
        response = await env.v2("get_graph_stats")
        g.get_stats.assert_awaited_once_with(env.project.id)
        g.reset()
        await env.v2("get_graph_stats", slug=other.slug)
        g.get_stats.assert_awaited_once_with(other.id)

    assert response.status_code == 200
    assert mcp_out == response.json() == stats


Scenario = Callable[[ParityEnv], Awaitable[None]]

SCENARIOS: dict[str, Scenario] = {
    "list_projects": scenario_list_projects,
    "get_project": scenario_get_project,
    "create_project": scenario_create_project,
    "update_project": scenario_update_project,
    "delete_project": scenario_delete_project,
    "invite_member": scenario_invite_member,
    "list_members": scenario_list_members,
    "remove_member": scenario_remove_member,
    "get_project_summary": scenario_get_project_summary,
    "list_labels": scenario_list_labels,
    "create_label": scenario_create_label,
    "list_tickets": scenario_list_tickets,
    "search_tickets": scenario_search_tickets,
    "get_ticket": scenario_get_ticket,
    "create_ticket": scenario_create_ticket,
    "update_ticket": scenario_update_ticket,
    "delete_ticket": scenario_delete_ticket,
    "bulk_update_tickets": scenario_bulk_update_tickets,
    "get_board": scenario_get_board,
    "list_comments": scenario_list_comments,
    "add_comment": scenario_add_comment,
    "list_acceptance_criteria": scenario_list_acceptance_criteria,
    "add_acceptance_criterion": scenario_add_acceptance_criterion,
    "update_acceptance_criterion": scenario_update_acceptance_criterion,
    "delete_acceptance_criterion": scenario_delete_acceptance_criterion,
    "list_sprints": scenario_list_sprints,
    "get_sprint": scenario_get_sprint,
    "create_sprint": scenario_create_sprint,
    "update_sprint": scenario_update_sprint,
    "start_sprint": scenario_start_sprint,
    "complete_sprint": scenario_complete_sprint,
    "get_burndown": scenario_get_burndown,
    "list_wiki_pages": scenario_list_wiki_pages,
    "get_wiki_page": scenario_get_wiki_page,
    "create_wiki_page": scenario_create_wiki_page,
    "update_wiki_page": scenario_update_wiki_page,
    "delete_wiki_page": scenario_delete_wiki_page,
    "search_wiki": scenario_search_wiki,
    "get_wiki_config": scenario_get_wiki_config,
    "set_wiki_config": scenario_set_wiki_config,
    "list_blog_posts": scenario_list_blog_posts,
    "get_blog_post": scenario_get_blog_post,
    "lint_blog_post": scenario_lint_blog_post,
    "set_blog_post_meta": scenario_set_blog_post_meta,
    "list_graph_nodes": scenario_list_graph_nodes,
    "get_graph_node": scenario_get_graph_node,
    "query_graph": scenario_query_graph,
    "find_graph_path": scenario_find_graph_path,
    "get_graph_stats": scenario_get_graph_stats,
}


class TestToolEndpointParity:
    def test_every_mapped_tool_has_a_scenario(self):
        assert set(SCENARIOS) == set(TOOL_TO_ENDPOINT)

    @pytest.mark.parametrize("tool_name", sorted(TOOL_TO_ENDPOINT))
    async def test_mcp_tool_and_v2_endpoint_agree(self, tool_name, env):
        await SCENARIOS[tool_name](env)


# ---------------------------------------------------------------------------
# Znane różnice kontraktu (pinowane, żeby nieaktualny opis w mapie wychodził w testach)
# ---------------------------------------------------------------------------


class TestKnownDifferences:
    def test_tools_with_skipped_fields_document_them_in_the_map(self):
        skipped = [
            "list_projects",
            "get_project",
            "create_project",
            "delete_project",
            "invite_member",
            "remove_member",
            "get_project_summary",
            "get_ticket",
            "update_ticket",
            "start_sprint",
            "complete_sprint",
            "list_acceptance_criteria",
            "get_sprint",
            "create_wiki_page",
            "update_wiki_page",
            "list_blog_posts",
            "set_blog_post_meta",
            "list_graph_nodes",
            "get_graph_node",
            "query_graph",
            "find_graph_path",
        ]
        for name in skipped:
            assert TOOL_TO_ENDPOINT[name].known_differences.strip(), name

    async def test_delete_project_requires_confirm_only_in_mcp(self, env):
        project_a = await _extra_project(env, "confirm-a")
        project_b = await _extra_project(env, "confirm-b")

        message = await env.mcp_error("delete_project", project_a.slug)
        response = await env.v2("delete_project", slug=project_b.slug)

        assert message == "Aby usunac projekt, podaj confirm=true"
        assert (await _project_state(env, project_a.id))["is_active"] is True
        assert response.status_code == 204
        assert (await _project_state(env, project_b.id))["is_active"] is False

    async def test_get_ticket_spec_page_id_matches_mcp_and_settlements_are_missing_in_v2(self, env):
        spec = await _wiki_page(env, "Specyfikacja ticketa", "specyfikacja-ticketa")
        ticket = await _ticket(env, "Ticket ze specyfikacja", spec_page_id=spec.id)
        unset = await _ticket(env, "Ticket bez specyfikacji")

        text = await env.mcp("get_ticket", env.slug, str(ticket.id))
        response = await env.v2("get_ticket", ticket_id=ticket.id)
        unset_response = await env.v2("get_ticket", ticket_id=unset.id)

        assert f"Spec: Specyfikacja ticketa | Spec ID: {spec.id}" in text
        assert response.status_code == 200
        assert response.json()["spec_page_id"] == str(spec.id)
        assert not {"spec_page", "settlements"} & set(response.json())
        assert unset_response.json()["spec_page_id"] is None

    async def test_get_ticket_comment_by_ai_is_shown_as_ai_only_in_mcp(self, env):
        ticket = await _ticket(env, "Komentarz od AI")
        env.db.add(TicketComment(ticket_id=ticket.id, user_id=env.actor.user.id, content="Uwaga agenta", created_via_ai=True))
        await env.db.flush()

        view = _parse_ticket_detail(await env.mcp("get_ticket", env.slug, str(ticket.id)))
        body = (await env.v2("get_ticket", ticket_id=ticket.id)).json()

        assert view["comments"] == [("ai", "Uwaga agenta")]
        assert [(c["author"], c["content"]) for c in body["comments"]] == [(env.actor.user.email, "Uwaga agenta")]


# ---------------------------------------------------------------------------
# Graf: wyłączony Neo4j i brak connections:read w obu kanałach (MON-248)
# ---------------------------------------------------------------------------

# narzędzie -> (argumenty pozycyjne MCP, query v2, parametry ścieżki v2)
_GRAPH_CALLS: dict[str, tuple[tuple[Any, ...], dict[str, Any] | None, dict[str, Any]]] = {
    "list_graph_nodes": ((), None, {}),
    "get_graph_node": (("n1",), None, {"node_id": "n1"}),
    "query_graph": ((), None, {}),
    "find_graph_path": (("a", "b"), {"source_id": "a", "target_id": "b"}, {}),
    "get_graph_stats": ((), None, {}),
}


class TestGraphChannelErrors:
    def test_every_graph_read_tool_has_a_call_definition(self):
        assert set(_GRAPH_CALLS) == set(_GRAPH_READ_TOOLS)

    @pytest.mark.parametrize("tool", _GRAPH_READ_TOOLS)
    async def test_disabled_graph_is_value_error_in_mcp_and_503_problem_in_v2(self, tool, env):
        mcp_args, params, path_params = _GRAPH_CALLS[tool]

        with _graph_backend(enabled=False) as g:
            message = await env.mcp_error(tool, env.slug, *mcp_args)
            response = await env.v2(tool, params=params, **path_params)

        assert message == _GRAPH_DISABLED
        _assert_problem(response, 503, message)
        for name in _GRAPH_SERVICE_FUNCTIONS:
            getattr(g, name).assert_not_awaited()

    @pytest.mark.parametrize("tool", _GRAPH_READ_TOOLS)
    async def test_role_without_connections_read_is_denied_in_both_channels_before_graph_is_touched(self, tool, env):
        mcp_args, params, path_params = _GRAPH_CALLS[tool]
        limited = await make_actor(env.db, f"graph-noperm-{tool}")
        role = Role(name=f"graph-noperm-{tool}", project_id=env.project.id, permissions={"scrum": ["read"], "wiki": ["read"]}, is_system=False)
        env.db.add(role)
        await env.db.flush()
        env.db.add(ProjectMember(project_id=env.project.id, user_id=limited.user.id, role="member", role_id=role.id))
        await env.db.flush()

        with _graph_backend(enabled=False) as g:
            message = await call_mcp_error(_token_of(limited), tool, env.slug, *mcp_args)
            response = await call_v2(env.client, tool, limited.headers, env.slug, params=params, **path_params)

        assert message == "Brak uprawnienia connections:read"
        _assert_problem(response, 403, message)
        g.is_enabled.assert_not_called()
        for name in _GRAPH_SERVICE_FUNCTIONS:
            getattr(g, name).assert_not_awaited()


# ---------------------------------------------------------------------------
# Błędy: pusty tytuł strony wiki
# ---------------------------------------------------------------------------


class TestWikiBlankTitle:
    async def test_create_wiki_page_with_blank_title_is_rejected_by_both_channels(self, env):
        message = await env.mcp_error("create_wiki_page", env.slug, "   ", "tresc")
        response = await env.v2("create_wiki_page", json={"title": "   ", "content": "tresc"})

        assert message == "Tytul jest wymagany"
        _assert_problem(response, 422, "Tytul jest wymagany")
        created = (await env.db.execute(select(func.count(WikiPage.id)).where(WikiPage.project_id == env.project.id))).scalar_one()
        assert created == 0


# ---------------------------------------------------------------------------
# Uprawnienia i członkostwo
# ---------------------------------------------------------------------------


@dataclass
class PermissionCase:
    tool: str
    role: str
    permission: str
    service_message: str


_PERMISSION_CASES = [
    PermissionCase("update_project", "member", "settings:write", "Brak uprawnienia do edycji projektu (settings:write)"),
    PermissionCase("invite_member", "member", "users:write", "Brak uprawnienia do zapraszania czlonkow do projektu (users:write)"),
    PermissionCase("set_wiki_config", "member", "settings:write", "Brak uprawnienia do zmiany konfiguracji projektu (settings:write)"),
    PermissionCase("delete_project", "admin", "settings:delete", "Brak uprawnienia do usuniecia projektu (settings:delete)"),
    PermissionCase("remove_member", "admin", "users:delete", "Brak uprawnienia do usuniecia czlonkow z projektu (users:delete)"),
]


class TestPermissionDenied:
    @pytest.mark.parametrize("case", _PERMISSION_CASES, ids=lambda c: c.tool)
    async def test_role_without_permission_is_denied_in_both_channels(self, case, env):
        limited = await make_actor(env.db, f"limited-{case.tool}")
        await add_member(env.db, env.project, limited.user, role=case.role)
        victim = await _new_user(env, f"victim-{case.tool}")
        await add_member(env.db, env.project, victim, role="member")
        name_before = env.project.name
        mcp_calls: dict[str, tuple[tuple[Any, ...], dict[str, Any]]] = {
            "update_project": ((env.slug,), {"name": "Zmiana bez prawa"}),
            "invite_member": ((env.slug, "ktos-nowy@example.com"), {}),
            "set_wiki_config": ((env.slug, True), {}),
            "delete_project": ((env.slug,), {"confirm": True}),
            "remove_member": ((env.slug, victim.email), {}),
        }
        v2_json: dict[str, Any] = {
            "update_project": {"name": "Zmiana bez prawa"},
            "invite_member": {"email": "ktos-nowy@example.com"},
            "set_wiki_config": {"enabled": True},
        }
        args, kwargs = mcp_calls[case.tool]

        message = await call_mcp_error(_token_of(limited), case.tool, *args, **kwargs)
        response = await call_v2(env.client, case.tool, limited.headers, env.slug, json=v2_json.get(case.tool), member_id=victim.id)

        assert message == case.service_message
        _assert_problem(response, 403, f"Brak uprawnienia {case.permission}")
        state = await _project_state(env, env.project.id)
        assert (state["name"], state["is_active"]) == (name_before, True)
        assert (await env.db.execute(select(func.count(ProjectMember.id)).where(ProjectMember.user_id == victim.id))).scalar_one() == 1
        assert (await env.db.execute(select(User.id).where(User.email == "ktos-nowy@example.com"))).first() is None
        assert (await env.db.execute(select(Project.wiki_llm_enabled).where(Project.id == env.project.id))).scalar_one() is False


_MEMBERSHIP_CASES: list[tuple[str, tuple[Any, ...], dict[str, Any], dict[str, Any] | None]] = [
    ("create_ticket", ("Ticket spoza projektu",), {}, {"title": "Ticket spoza projektu"}),
    ("create_wiki_page", ("Strona spoza projektu", "tresc"), {}, {"title": "Strona spoza projektu", "content": "tresc"}),
    ("list_tickets", (), {}, None),
    ("get_board", (), {}, None),
    ("list_members", (), {}, None),
    ("list_wiki_pages", (), {}, None),
]


class TestNonMemberDenied:
    @pytest.mark.parametrize(("tool", "args", "kwargs", "v2_json"), _MEMBERSHIP_CASES, ids=[case[0] for case in _MEMBERSHIP_CASES])
    async def test_user_outside_project_gets_value_error_and_403(self, tool, args, kwargs, v2_json, env):
        outsider = await make_actor(env.db, f"outsider-{tool}")

        message = await call_mcp_error(_token_of(outsider), tool, env.slug, *args, **kwargs)
        response = await call_v2(env.client, tool, outsider.headers, env.slug, json=v2_json)

        assert message == f"Uzytkownik nie jest czlonkiem projektu '{env.slug}'"
        _assert_problem(response, 403, message)
        assert (await env.db.execute(select(func.count(Ticket.id)).where(Ticket.project_id == env.project.id))).scalar_one() == 0
        assert (await env.db.execute(select(func.count(WikiPage.id)).where(WikiPage.project_id == env.project.id))).scalar_one() == 0

    async def test_unknown_project_gets_value_error_and_404(self, env):
        slug = f"nie-ma-{uuid.uuid4().hex[:8]}"

        message = await env.mcp_error("list_tickets", slug)
        response = await env.v2("list_tickets", slug=slug)

        assert message == f"Projekt '{slug}' nie istnieje"
        _assert_problem(response, 404, message)

    async def test_soft_deleted_project_is_not_found_in_both_channels(self, env):
        env.project.is_active = False
        await env.db.flush()

        message = await env.mcp_error("get_board", env.slug)
        response = await env.v2("get_board")

        assert message == f"Projekt '{env.slug}' nie istnieje"
        _assert_problem(response, 404, message)
