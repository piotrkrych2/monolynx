"""Testy RBAC modułów 500ki, Monitoring, Heartbeat, Połączenia i Pipelines w kanale MCP oraz w dashboardzie (MON-242).

Kontrakt: samo członkostwo w projekcie nie wystarcza. Narzędzia MCP tych modułów, `get_activity_log` i trasy
dashboardu sprawdzają uprawnienia roli tak samo jak trasy dashboardu:

- odmowa to `ValueError("Brak uprawnienia <moduł>:<akcja>")` (wyjątek: `delete_monitor` zachowuje własny komunikat),
- check idzie jako pierwsza rzecz po `_get_user_and_project`: przed `graph_service.is_enabled()`, lookupem zasobu,
  parsowaniem id i walidacją wejścia; dowodzą tego `ORDER_CASES` oraz `graph_service` bez żadnego wywołania,
- odmowa nie zostawia zapisu w bazie (snapshot przed i po),
- `create_ticket_from_issue` wymaga `500ki:write` ORAZ `scrum:write`,
- trasy `dashboard/pipelines.py` wymagają `scrum:read` (superuser bez członkostwa przechodzi),
- trasy `dashboard/sentry.py` nie pokazują projektu po soft delete.

Aktorzy (jeden projekt, osobny użytkownik z tokenem na rolę):
- `owner`, `member`: role legacy (`DEFAULT_ROLE_PERMISSIONS`),
- `empty`: rola niestandardowa `{}`,
- `readonly`: rola niestandardowa z samym `read` we wszystkich modułach,
- `allbut:<moduł>:<akcja>`: rola ze wszystkimi uprawnieniami OPRÓCZ jednego - jedyna rola, która odmawia dokładnie
  sprawdzanego uprawnienia, więc podmiana akcji (read/write/delete) albo modułu w checku daje czerwony test,
- role nazwane z `_NAMED_ROLES` dla rozróżnień write/delete i dla `create_ticket_from_issue`.

Użytkownik, projekt i rola są realne w bazie testowej; mockowany jest tylko zewnętrzny `graph_service` (Neo4j),
`_is_url_safe` (DNS) i `pipelines_service.append_job_log` (MinIO). Sesja testowa jest współdzielona, więc testy nie
dowodzą commita (`db-commit.md`).
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx import mcp_server
from monolynx.constants import PERMISSION_ACTIONS, PERMISSION_MODULES
from monolynx.models.activity_log import ActivityLog
from monolynx.models.heartbeat import Heartbeat
from monolynx.models.issue import Issue
from monolynx.models.monitor import Monitor
from monolynx.models.pipeline import Pipeline, PipelineJob, PipelineStep
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.wiki_page import WikiPage
from tests.integration.api_v2.conftest import ApiV2Actor, add_member, make_actor, make_project

pytestmark = pytest.mark.integration

_MISSING = str(uuid.uuid4())
_MALFORMED = "nie-uuid"
_DELETE_MONITOR_DENIAL = "Brak uprawnienia do usuniecia monitora (monitoring:delete)"

_GRAPH_METHODS = (
    "create_node",
    "list_nodes",
    "get_node",
    "get_neighbors",
    "delete_node",
    "create_edge",
    "delete_edge",
    "search_graph",
    "get_graph",
    "find_path",
    "get_stats",
    "replace_graph",
)


def _all_but(perm: str) -> dict[str, list[str]]:
    module, action = perm.split(":")
    perms = {m: list(PERMISSION_ACTIONS) for m in PERMISSION_MODULES}
    perms[module] = [a for a in PERMISSION_ACTIONS if a != action]
    return perms


_NAMED_ROLES: dict[str, dict[str, list[str]]] = {
    "graph_write_no_delete": {"connections": ["read", "write"]},
    "scrum_wiki_write_no_wiki_delete": {"scrum": ["read", "write"], "wiki": ["read", "write"]},
    "wiki_delete_only": {"wiki": ["delete"]},
    "issue_write_no_scrum_write": {"500ki": ["read", "write"]},
    "scrum_write_no_issue_write": {"scrum": ["read", "write"]},
    "issue_and_scrum_write": {"500ki": ["read", "write"], "scrum": ["read", "write"]},
    "scrum_read_only": {"scrum": ["read"]},
}


def _permissions_for(key: str) -> dict[str, list[str]]:
    if key == "empty":
        return {}
    if key == "readonly":
        return {m: ["read"] for m in PERMISSION_MODULES}
    if key.startswith("allbut:"):
        return _all_but(key.removeprefix("allbut:"))
    return _NAMED_ROLES[key]


# ---------------------------------------------------------------------------
# Środowisko
# ---------------------------------------------------------------------------


@dataclass
class Seed:
    issue_id: uuid.UUID
    monitor_id: uuid.UUID
    heartbeat_id: uuid.UUID
    heartbeat_token: str
    sprint_id: uuid.UUID
    pipeline_id: uuid.UUID
    job_id: uuid.UUID


@dataclass
class Env:
    db: AsyncSession
    client: httpx.AsyncClient
    project: Project
    seed: Seed
    graph: MagicMock
    append_log: AsyncMock
    actors: dict[str, ApiV2Actor] = field(default_factory=dict)

    async def actor(self, key: str) -> ApiV2Actor:
        if key in self.actors:
            return self.actors[key]
        actor = await make_actor(self.db, f"modrbac-{key.replace(':', '-')}")
        if key in ("owner", "member"):
            await add_member(self.db, self.project, actor.user, role=key)
        else:
            role = Role(name=f"rbac-{key}"[:50], project_id=self.project.id, permissions=_permissions_for(key), is_system=False)
            self.db.add(role)
            await self.db.flush()
            self.db.add(ProjectMember(project_id=self.project.id, user_id=actor.user.id, role="member", role_id=role.id))
            await self.db.flush()
        self.actors[key] = actor
        return actor

    async def call(self, key: str, tool: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        actor = await self.actor(key)
        return await getattr(mcp_server, tool)(_ctx(actor), self.project.slug, *args, **kwargs)

    async def error(self, key: str, tool: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> str:
        with pytest.raises(ValueError) as info:
            await self.call(key, tool, args, kwargs)
        return str(info.value)

    def graph_untouched(self) -> bool:
        return self.graph.is_enabled.call_count == 0 and all(getattr(self.graph, name).call_count == 0 for name in _GRAPH_METHODS)

    def graph_used(self) -> bool:
        return any(getattr(self.graph, name).call_count > 0 for name in _GRAPH_METHODS)


def _ctx(actor: ApiV2Actor) -> MagicMock:
    token = actor.headers["Authorization"].removeprefix("Bearer ")
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _fake_graph() -> MagicMock:
    graph = MagicMock()
    graph.is_enabled.return_value = True
    node = {"id": "n1", "type": "File", "name": "a.py", "file_path": None, "line_number": None, "metadata": {}}
    edge = {"source_id": "a", "target_id": "b", "type": "CALLS", "metadata": {}}
    subgraph = {"nodes": [node], "edges": []}
    graph.create_node = AsyncMock(return_value=node)
    graph.list_nodes = AsyncMock(return_value=[node])
    graph.get_node = AsyncMock(return_value=node)
    graph.get_neighbors = AsyncMock(return_value=subgraph)
    graph.delete_node = AsyncMock(return_value=True)
    graph.create_edge = AsyncMock(return_value=edge)
    graph.delete_edge = AsyncMock(return_value=True)
    graph.search_graph = AsyncMock(return_value=subgraph)
    graph.get_graph = AsyncMock(return_value=subgraph)
    graph.find_path = AsyncMock(return_value=subgraph)
    graph.get_stats = AsyncMock(return_value={"nodes": 1, "edges": 0})
    graph.replace_graph = AsyncMock(return_value={"inserted_nodes": 1, "inserted_edges": 0})
    return graph


@pytest.fixture
async def env(db_session, client, monkeypatch) -> Env:
    @asynccontextmanager
    async def session_factory():
        yield db_session

    monkeypatch.setattr(mcp_server, "async_session_factory", session_factory)
    monkeypatch.setattr(mcp_server, "_is_url_safe", lambda url: None)
    graph = _fake_graph()
    monkeypatch.setattr(mcp_server, "graph_service", graph)
    append_log = AsyncMock(return_value=SimpleNamespace(id=uuid.uuid4()))
    monkeypatch.setattr("monolynx.services.pipelines.append_job_log", append_log)

    project = await make_project(db_session, "modrbac")
    issue = Issue(project_id=project.id, title="Błąd startowy", fingerprint=uuid.uuid4().hex, status="unresolved")
    monitor = Monitor(project_id=project.id, url="https://example.com", name="Monitor startowy", interval_value=5, interval_unit="minutes")
    heartbeat = Heartbeat(project_id=project.id, name="hb-startowy", period=3600, grace=60)
    sprint = Sprint(project_id=project.id, name="Sprint startowy", status="planning", start_date=date(2026, 10, 1), end_date=date(2026, 10, 14))
    db_session.add_all([issue, monitor, heartbeat, sprint])
    await db_session.flush()

    pipeline = Pipeline(project_id=project.id, pipeline_type="sprint_close", sprint_id=sprint.id, status="created", meta={})
    db_session.add(pipeline)
    await db_session.flush()
    step = PipelineStep(pipeline_id=pipeline.id, name="wiki-update", position=0, status="pending")
    db_session.add(PipelineStep(pipeline_id=pipeline.id, name="wrap-up", position=1, status="pending"))
    db_session.add(step)
    await db_session.flush()
    job = PipelineJob(step_id=step.id, name="Job startowy", agent_type="researcher", status="created")
    db_session.add(job)
    await db_session.flush()

    seed = Seed(
        issue_id=issue.id,
        monitor_id=monitor.id,
        heartbeat_id=heartbeat.id,
        heartbeat_token=heartbeat.token,
        sprint_id=sprint.id,
        pipeline_id=pipeline.id,
        job_id=job.id,
    )
    return Env(db=db_session, client=client, project=project, seed=seed, graph=graph, append_log=append_log)


# ---------------------------------------------------------------------------
# Stan bazy
# ---------------------------------------------------------------------------


def _rows(result: Any) -> list[tuple[str, ...]]:
    return sorted(tuple(str(value) for value in row) for row in result.all())


async def snapshot(env: Env) -> dict[str, Any]:
    """Pełny stan zasobów projektu odczytany zapytaniami o kolumny (nie przez identity map)."""
    db = env.db
    pid = env.project.id

    async def count(model: Any, *where: Any) -> int:
        return (await db.execute(select(func.count()).select_from(model).where(*where))).scalar_one()

    return {
        "issues": _rows(await db.execute(select(Issue.title, Issue.status, Issue.source).where(Issue.project_id == pid))),
        "monitors": _rows(
            await db.execute(
                select(Monitor.name, Monitor.url, Monitor.interval_value, Monitor.interval_unit, Monitor.is_active).where(Monitor.project_id == pid)
            )
        ),
        "heartbeats": _rows(await db.execute(select(Heartbeat.name, Heartbeat.period, Heartbeat.grace).where(Heartbeat.project_id == pid))),
        "tickets": _rows(await db.execute(select(Ticket.title, Ticket.status).where(Ticket.project_id == pid))),
        "pipelines": _rows(await db.execute(select(Pipeline.pipeline_type, Pipeline.status).where(Pipeline.project_id == pid))),
        "steps": _rows(
            await db.execute(
                select(PipelineStep.name, PipelineStep.status)
                .join(Pipeline, PipelineStep.pipeline_id == Pipeline.id)
                .where(Pipeline.project_id == pid)
            )
        ),
        "jobs": _rows(
            await db.execute(
                select(PipelineJob.name, PipelineJob.status)
                .join(PipelineStep, PipelineJob.step_id == PipelineStep.id)
                .join(Pipeline, PipelineStep.pipeline_id == Pipeline.id)
                .where(Pipeline.project_id == pid)
            )
        ),
        "pages": await count(WikiPage, WikiPage.project_id == pid),
        "activity": await count(ActivityLog, ActivityLog.project_id == pid),
    }


# ---------------------------------------------------------------------------
# Tabela przypadków: każde narzędzie z wymaganym uprawnieniem
# ---------------------------------------------------------------------------

McpCall = Callable[[Seed], tuple[tuple[Any, ...], dict[str, Any]]]


def _a(*args: Any, **kwargs: Any) -> tuple[tuple[Any, ...], dict[str, Any]]:
    return args, kwargs


@dataclass(frozen=True)
class Case:
    """Narzędzie MCP z wymaganymi uprawnieniami.

    `effect`: "db" - owner zmienia stan bazy, "graph" - owner woła `graph_service`, "read" - owner dostaje wynik,
    "mocked" - skutek jest w zamockowanym serwisie. `denial` nadpisuje komunikat odmowy (tylko `delete_monitor`).
    """

    tool: str
    perms: tuple[str, ...]
    call: McpCall
    effect: str
    denial: str | None = None

    def message(self, perm: str) -> str:
        return self.denial or f"Brak uprawnienia {perm}"


CASES: list[Case] = [
    # --- 500ki ---
    Case("list_issues", ("500ki:read",), lambda s: _a(), "read"),
    Case("get_issue", ("500ki:read",), lambda s: _a(str(s.issue_id)), "read"),
    Case("update_issue_status", ("500ki:write",), lambda s: _a(str(s.issue_id), "resolved"), "db"),
    Case("create_issue", ("500ki:write",), lambda s: _a("Nowy błąd"), "db"),
    Case("create_ticket_from_issue", ("500ki:write", "scrum:write"), lambda s: _a(str(s.issue_id)), "db"),
    # --- monitoring ---
    Case("list_monitors", ("monitoring:read",), lambda s: _a(), "read"),
    Case("get_monitor", ("monitoring:read",), lambda s: _a(str(s.monitor_id)), "read"),
    Case("create_monitor", ("monitoring:write",), lambda s: _a("Nowy monitor", "https://example.org"), "db"),
    Case("update_monitor", ("monitoring:write",), lambda s: _a(str(s.monitor_id), name="Zmieniony monitor"), "db"),
    Case("delete_monitor", ("monitoring:delete",), lambda s: _a(str(s.monitor_id)), "db", denial=_DELETE_MONITOR_DENIAL),
    # --- heartbeat ---
    Case("list_heartbeats", ("heartbeat:read",), lambda s: _a(), "read"),
    Case("get_heartbeat", ("heartbeat:read",), lambda s: _a(str(s.heartbeat_id)), "read"),
    Case("create_heartbeat", ("heartbeat:write",), lambda s: _a("hb-nowy", 60), "db"),
    Case("update_heartbeat", ("heartbeat:write",), lambda s: _a(str(s.heartbeat_id), name="hb-zmieniony"), "db"),
    Case("delete_heartbeat", ("heartbeat:delete",), lambda s: _a(str(s.heartbeat_id)), "db"),
    # --- połączenia (graf) ---
    Case("list_graph_nodes", ("connections:read",), lambda s: _a(), "graph"),
    Case("get_graph_node", ("connections:read",), lambda s: _a("n1"), "graph"),
    Case("query_graph", ("connections:read",), lambda s: _a(), "graph"),
    Case("find_graph_path", ("connections:read",), lambda s: _a("a", "b"), "graph"),
    Case("get_graph_stats", ("connections:read",), lambda s: _a(), "graph"),
    Case("create_graph_node", ("connections:write",), lambda s: _a("File", "a.py"), "graph"),
    Case("create_graph_edge", ("connections:write",), lambda s: _a("a", "b", "CALLS"), "graph"),
    Case("bulk_create_graph_nodes", ("connections:write",), lambda s: _a([{"type": "File", "name": "a.py"}]), "graph"),
    Case("bulk_create_graph_edges", ("connections:write",), lambda s: _a([{"source_id": "a", "target_id": "b", "type": "CALLS"}]), "graph"),
    Case("delete_graph_node", ("connections:delete",), lambda s: _a("n1"), "graph"),
    Case("delete_graph_edge", ("connections:delete",), lambda s: _a("a", "b", "CALLS"), "graph"),
    Case("replace_graph", ("connections:delete",), lambda s: _a([{"id": "1", "type": "File", "name": "a.py"}], []), "graph"),
    # --- pipelines ---
    Case("list_pipelines", ("scrum:read",), lambda s: _a(), "read"),
    Case("get_pipeline", ("scrum:read",), lambda s: _a(str(s.pipeline_id)), "read"),
    Case("get_pipeline_job_log", ("scrum:read",), lambda s: _a(str(s.job_id)), "read"),
    Case("create_pipeline", ("scrum:write",), lambda s: _a("sprint_close", sprint_id=str(s.sprint_id)), "db"),
    Case("create_pipeline_job", ("scrum:write",), lambda s: _a(str(s.pipeline_id), "wrap-up", "Nowy job", "researcher"), "db"),
    Case("update_pipeline_job", ("scrum:write",), lambda s: _a(str(s.job_id), status="running"), "db"),
    Case("append_job_log", ("scrum:write",), lambda s: _a(str(s.job_id), "wpis do logu"), "mocked"),
    Case("finish_pipeline", ("scrum:write",), lambda s: _a(str(s.pipeline_id), status="success"), "db"),
    Case("clean_pipeline_logs", ("wiki:delete",), lambda s: _a(str(s.sprint_id)), "read"),
    # --- dziennik aktywności ---
    Case("get_activity_log", ("settings:read",), lambda s: _a(), "read"),
]

CASE_BY_TOOL = {case.tool: case for case in CASES}

_DENIAL_PARAMS = [pytest.param(case, perm, id=f"{case.tool}-{perm}") for case in CASES for perm in case.perms]
_CASE_PARAMS = [pytest.param(case, id=case.tool) for case in CASES]


def test_cases_cover_every_module_tool_with_a_permission():
    """Tabela obejmuje wszystkie narzędzia z zakresu MON-242 (37 sztuk), każde dokładnie raz."""
    tools = [case.tool for case in CASES]

    assert len(tools) == len(set(tools)) == 37
    assert all(hasattr(mcp_server, tool) for tool in tools)


async def _assert_denied(env: Env, case: Case, key: str, expected: str) -> None:
    before = await snapshot(env)
    args, kwargs = case.call(env.seed)

    message = await env.error(key, case.tool, args, kwargs)

    assert message == expected
    assert await snapshot(env) == before
    assert env.graph_untouched()
    assert env.append_log.await_count == 0


async def _assert_allowed(env: Env, case: Case, key: str) -> Any:
    before = await snapshot(env)
    args, kwargs = case.call(env.seed)

    result = await env.call(key, case.tool, args, kwargs)

    assert result is not None
    if case.effect == "db":
        assert await snapshot(env) != before
    elif case.effect == "graph":
        assert env.graph_used()
    elif case.effect == "mocked":
        assert env.append_log.await_count == 1
    return result


# ---------------------------------------------------------------------------
# Odmowa: rola ze wszystkim OPRÓCZ wymaganego uprawnienia
# ---------------------------------------------------------------------------


class TestRoleMissingExactlyTheRequiredPermission:
    @pytest.mark.parametrize(("case", "perm"), _DENIAL_PARAMS)
    async def test_gets_value_error_and_no_state_change(self, case, perm, env):
        await _assert_denied(env, case, f"allbut:{perm}", case.message(perm))


# ---------------------------------------------------------------------------
# Owner: wszystko działa
# ---------------------------------------------------------------------------


class TestOwnerRole:
    @pytest.mark.parametrize("case", _CASE_PARAMS)
    async def test_owner_is_not_blocked(self, case, env):
        await _assert_allowed(env, case, "owner")

    async def test_owner_reads_back_seeded_data(self, env):
        issues = await env.call("owner", "list_issues", (), {})
        monitor = await env.call("owner", "get_monitor", (str(env.seed.monitor_id),), {})
        heartbeats = await env.call("owner", "list_heartbeats", (), {})

        assert "Błąd startowy" in issues
        assert "Monitor startowy" in monitor
        assert [hb["name"] for hb in heartbeats] == ["hb-startowy"]

    async def test_owner_creates_ticket_from_issue_and_deletes_monitor(self, env):
        await env.call("owner", "create_ticket_from_issue", (str(env.seed.issue_id),), {})
        await env.call("owner", "delete_monitor", (str(env.seed.monitor_id),), {})

        state = await snapshot(env)
        assert [row[0] for row in state["tickets"]] == ["[500ki] Błąd startowy"]
        assert state["monitors"] == []


# ---------------------------------------------------------------------------
# Member: odczyty i pipelines write przechodzą, reszta zapisów nie
# ---------------------------------------------------------------------------

# Narzędzie -> uprawnienie, którego rola `member` nie ma (DEFAULT_ROLE_PERMISSIONS["member"]).
_MEMBER_DENIED = {
    "update_issue_status": "500ki:write",
    "create_issue": "500ki:write",
    "create_ticket_from_issue": "500ki:write",
    "create_monitor": "monitoring:write",
    "update_monitor": "monitoring:write",
    "create_heartbeat": "heartbeat:write",
    "update_heartbeat": "heartbeat:write",
    "delete_heartbeat": "heartbeat:delete",
    "create_graph_node": "connections:write",
    "create_graph_edge": "connections:write",
    "bulk_create_graph_nodes": "connections:write",
    "bulk_create_graph_edges": "connections:write",
    "delete_graph_node": "connections:delete",
    "delete_graph_edge": "connections:delete",
    "replace_graph": "connections:delete",
    "clean_pipeline_logs": "wiki:delete",
}

_MEMBER_ALLOWED = [
    "list_issues",
    "get_issue",
    "list_monitors",
    "get_monitor",
    "list_heartbeats",
    "get_heartbeat",
    "list_graph_nodes",
    "get_graph_node",
    "query_graph",
    "find_graph_path",
    "get_graph_stats",
    "list_pipelines",
    "get_pipeline",
    "get_pipeline_job_log",
    "create_pipeline",
    "create_pipeline_job",
    "update_pipeline_job",
    "append_job_log",
    "finish_pipeline",
    "get_activity_log",
]


class TestMemberRole:
    @pytest.mark.parametrize(("tool", "perm"), list(_MEMBER_DENIED.items()))
    async def test_member_is_denied(self, tool, perm, env):
        await _assert_denied(env, CASE_BY_TOOL[tool], "member", f"Brak uprawnienia {perm}")

    async def test_member_delete_monitor_keeps_its_own_message(self, env):
        await _assert_denied(env, CASE_BY_TOOL["delete_monitor"], "member", _DELETE_MONITOR_DENIAL)

    @pytest.mark.parametrize("tool", _MEMBER_ALLOWED)
    async def test_member_is_not_blocked(self, tool, env):
        await _assert_allowed(env, CASE_BY_TOOL[tool], "member")

    def test_member_lists_cover_every_case(self):
        assert set(_MEMBER_DENIED) | set(_MEMBER_ALLOWED) | {"delete_monitor"} == set(CASE_BY_TOOL)
        assert not set(_MEMBER_DENIED) & set(_MEMBER_ALLOWED)


# ---------------------------------------------------------------------------
# Rola pusta: odmowa wszędzie, także na odczytach
# ---------------------------------------------------------------------------


class TestEmptyRole:
    @pytest.mark.parametrize("case", _CASE_PARAMS)
    async def test_empty_role_is_denied_everywhere(self, case, env):
        before = await snapshot(env)
        args, kwargs = case.call(env.seed)

        message = await env.error("empty", case.tool, args, kwargs)

        assert message in {case.message(perm) for perm in case.perms}
        assert await snapshot(env) == before
        assert env.graph_untouched()
        assert env.append_log.await_count == 0

    @pytest.mark.parametrize("tool", ["list_heartbeats", "get_heartbeat"])
    async def test_empty_role_does_not_receive_heartbeat_secrets(self, tool, env):
        args, kwargs = CASE_BY_TOOL[tool].call(env.seed)

        message = await env.error("empty", tool, args, kwargs)

        assert message == "Brak uprawnienia heartbeat:read"
        assert env.seed.heartbeat_token not in message

    async def test_owner_gets_heartbeat_token_and_ping_url_that_empty_role_is_denied(self, env):
        detail = await env.call("owner", "get_heartbeat", (str(env.seed.heartbeat_id),), {})
        listing = await env.call("owner", "list_heartbeats", (), {})

        assert detail["token"] == env.seed.heartbeat_token
        assert detail["ping_url"].endswith(f"/hb/{env.seed.heartbeat_token}")
        assert listing[0]["ping_url"].endswith(f"/hb/{env.seed.heartbeat_token}")


# ---------------------------------------------------------------------------
# Rola read-only we wszystkich modułach: odczyty tak, zapisy i usunięcia nie
# ---------------------------------------------------------------------------


class TestReadOnlyRole:
    @pytest.mark.parametrize("case", [pytest.param(c, id=c.tool) for c in CASES if all(p.endswith(":read") for p in c.perms)])
    async def test_readonly_reads_are_not_blocked(self, case, env):
        await _assert_allowed(env, case, "readonly")

    @pytest.mark.parametrize("case", [pytest.param(c, id=c.tool) for c in CASES if not all(p.endswith(":read") for p in c.perms)])
    async def test_readonly_writes_and_deletes_are_denied(self, case, env):
        before = await snapshot(env)
        args, kwargs = case.call(env.seed)

        message = await env.error("readonly", case.tool, args, kwargs)

        assert message in {case.message(perm) for perm in case.perms if not perm.endswith(":read")}
        assert await snapshot(env) == before
        assert env.graph_untouched()


# ---------------------------------------------------------------------------
# write nie oznacza delete; clean_pipeline_logs wymaga wiki:delete, nie scrum
# ---------------------------------------------------------------------------


class TestWriteDoesNotImplyDelete:
    @pytest.mark.parametrize("tool", ["delete_graph_node", "delete_graph_edge", "replace_graph"])
    async def test_connections_write_without_delete_is_denied(self, tool, env):
        await _assert_denied(env, CASE_BY_TOOL[tool], "graph_write_no_delete", "Brak uprawnienia connections:delete")

    @pytest.mark.parametrize("tool", ["create_graph_node", "create_graph_edge", "bulk_create_graph_nodes", "bulk_create_graph_edges"])
    async def test_connections_write_without_delete_can_still_write(self, tool, env):
        await _assert_allowed(env, CASE_BY_TOOL[tool], "graph_write_no_delete")

    async def test_scrum_and_wiki_write_without_wiki_delete_cannot_clean_pipeline_logs(self, env):
        await _assert_denied(env, CASE_BY_TOOL["clean_pipeline_logs"], "scrum_wiki_write_no_wiki_delete", "Brak uprawnienia wiki:delete")

    async def test_wiki_delete_alone_is_enough_to_clean_pipeline_logs(self, env):
        result = await env.call("wiki_delete_only", "clean_pipeline_logs", (str(env.seed.sprint_id),), {})

        assert result == {"deleted": 0}

    async def test_heartbeat_write_without_delete_cannot_delete_heartbeat(self, env):
        await env.call("allbut:heartbeat:delete", "update_heartbeat", (str(env.seed.heartbeat_id),), {"name": "hb-ok"})

        message = await env.error("allbut:heartbeat:delete", "delete_heartbeat", (str(env.seed.heartbeat_id),), {})

        assert message == "Brak uprawnienia heartbeat:delete"
        assert [row[0] for row in (await snapshot(env))["heartbeats"]] == ["hb-ok"]


# ---------------------------------------------------------------------------
# create_ticket_from_issue: 500ki:write ORAZ scrum:write
# ---------------------------------------------------------------------------


class TestCreateTicketFromIssueNeedsBothPermissions:
    async def test_issue_write_without_scrum_write_is_denied(self, env):
        await _assert_denied(env, CASE_BY_TOOL["create_ticket_from_issue"], "issue_write_no_scrum_write", "Brak uprawnienia scrum:write")

    async def test_scrum_write_without_issue_write_is_denied(self, env):
        await _assert_denied(env, CASE_BY_TOOL["create_ticket_from_issue"], "scrum_write_no_issue_write", "Brak uprawnienia 500ki:write")

    async def test_both_permissions_create_the_ticket(self, env):
        await env.call("issue_and_scrum_write", "create_ticket_from_issue", (str(env.seed.issue_id),), {})

        assert [row[0] for row in (await snapshot(env))["tickets"]] == ["[500ki] Błąd startowy"]


# ---------------------------------------------------------------------------
# Check przed parsowaniem id, walidacją wejścia, lookupem i bramką grafu
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class OrderCase:
    """Wywołanie z wejściem, które za checkiem kończy się innym błędem; rola ma wszystko oprócz `perm`."""

    id: str
    tool: str
    perm: str
    call: McpCall
    message: str | None = None


def _order(tool: str, perm: str, name: str, call: McpCall, message: str | None = None) -> OrderCase:
    return OrderCase(f"{tool}-{name}", tool, perm, call, message)


ORDER_CASES: list[OrderCase] = [
    # --- 500ki ---
    _order("get_issue", "500ki:read", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("get_issue", "500ki:read", "missing-issue", lambda s: _a(_MISSING)),
    _order("update_issue_status", "500ki:write", "invalid-status", lambda s: _a(str(s.issue_id), "zly")),
    _order("update_issue_status", "500ki:write", "malformed-id", lambda s: _a(_MALFORMED, "resolved")),
    _order("update_issue_status", "500ki:write", "missing-issue", lambda s: _a(_MISSING, "resolved")),
    _order("create_issue", "500ki:write", "blank-title", lambda s: _a("   ")),
    _order("create_issue", "500ki:write", "bad-severity", lambda s: _a("Tytuł", severity="extreme")),
    _order("create_issue", "500ki:write", "bad-environment", lambda s: _a("Tytuł", environment="local")),
    _order("create_ticket_from_issue", "500ki:write", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("create_ticket_from_issue", "scrum:write", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("create_ticket_from_issue", "500ki:write", "missing-issue", lambda s: _a(_MISSING)),
    _order("create_ticket_from_issue", "scrum:write", "missing-issue", lambda s: _a(_MISSING)),
    # --- monitoring ---
    _order("get_monitor", "monitoring:read", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("get_monitor", "monitoring:read", "missing-monitor", lambda s: _a(_MISSING)),
    _order("create_monitor", "monitoring:write", "blank-name", lambda s: _a("   ", "https://example.org")),
    _order("create_monitor", "monitoring:write", "bad-url-scheme", lambda s: _a("Monitor", "ftp://example.org")),
    _order("create_monitor", "monitoring:write", "bad-interval-unit", lambda s: _a("Monitor", "https://example.org", interval_unit="lata")),
    _order("create_monitor", "monitoring:write", "interval-out-of-range", lambda s: _a("Monitor", "https://example.org", interval_value=0)),
    _order("update_monitor", "monitoring:write", "malformed-id", lambda s: _a(_MALFORMED, name="Zmiana")),
    _order("update_monitor", "monitoring:write", "missing-monitor", lambda s: _a(_MISSING, name="Zmiana")),
    _order("update_monitor", "monitoring:write", "blank-name", lambda s: _a(str(s.monitor_id), name="   ")),
    _order("update_monitor", "monitoring:write", "bad-url-scheme", lambda s: _a(str(s.monitor_id), url="ftp://example.org")),
    _order("update_monitor", "monitoring:write", "bad-interval-unit", lambda s: _a(str(s.monitor_id), interval_unit="lata")),
    _order("update_monitor", "monitoring:write", "interval-out-of-range", lambda s: _a(str(s.monitor_id), interval_value=61)),
    _order("delete_monitor", "monitoring:delete", "malformed-id", lambda s: _a(_MALFORMED), _DELETE_MONITOR_DENIAL),
    _order("delete_monitor", "monitoring:delete", "missing-monitor", lambda s: _a(_MISSING), _DELETE_MONITOR_DENIAL),
    # --- heartbeat ---
    _order("get_heartbeat", "heartbeat:read", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("get_heartbeat", "heartbeat:read", "missing-heartbeat", lambda s: _a(_MISSING)),
    _order("create_heartbeat", "heartbeat:write", "blank-name", lambda s: _a("   ", 60)),
    _order("create_heartbeat", "heartbeat:write", "zero-period", lambda s: _a("hb-nowy", 0)),
    _order("create_heartbeat", "heartbeat:write", "negative-grace", lambda s: _a("hb-nowy", 60, grace=-1)),
    _order("update_heartbeat", "heartbeat:write", "malformed-id", lambda s: _a(_MALFORMED, name="hb")),
    _order("update_heartbeat", "heartbeat:write", "missing-heartbeat", lambda s: _a(_MISSING, name="hb")),
    _order("update_heartbeat", "heartbeat:write", "blank-name", lambda s: _a(str(s.heartbeat_id), name="   ")),
    _order("update_heartbeat", "heartbeat:write", "zero-period", lambda s: _a(str(s.heartbeat_id), period=0)),
    _order("delete_heartbeat", "heartbeat:delete", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("delete_heartbeat", "heartbeat:delete", "missing-heartbeat", lambda s: _a(_MISSING)),
    # --- połączenia ---
    _order("create_graph_node", "connections:write", "bad-node-type", lambda s: _a("ZlyTyp", "a.py")),
    _order("create_graph_edge", "connections:write", "bad-edge-type", lambda s: _a("a", "b", "ZLY")),
    _order("query_graph", "connections:read", "blank-search", lambda s: _a(search="   ")),
    # --- pipelines ---
    _order("create_pipeline", "scrum:write", "bad-type", lambda s: _a("zly_typ")),
    _order("create_pipeline", "scrum:write", "ticket-and-sprint", lambda s: _a("ticket_work", ticket_id="X-1", sprint_id=str(s.sprint_id))),
    _order("create_pipeline", "scrum:write", "malformed-sprint-id", lambda s: _a("sprint_close", sprint_id=_MALFORMED)),
    _order("create_pipeline", "scrum:write", "missing-sprint", lambda s: _a("sprint_close", sprint_id=_MISSING)),
    _order("create_pipeline_job", "scrum:write", "malformed-pipeline-id", lambda s: _a(_MALFORMED, "wrap-up", "Job", "researcher")),
    _order("create_pipeline_job", "scrum:write", "missing-pipeline", lambda s: _a(_MISSING, "wrap-up", "Job", "researcher")),
    _order("create_pipeline_job", "scrum:write", "unknown-step", lambda s: _a(str(s.pipeline_id), "nieznany-step", "Job", "researcher")),
    _order("update_pipeline_job", "scrum:write", "bad-status", lambda s: _a(str(s.job_id), status="zly")),
    _order("update_pipeline_job", "scrum:write", "malformed-job-id", lambda s: _a(_MALFORMED, status="running")),
    _order("update_pipeline_job", "scrum:write", "missing-job", lambda s: _a(_MISSING, status="running")),
    _order("append_job_log", "scrum:write", "malformed-job-id", lambda s: _a(_MALFORMED, "wpis")),
    _order("append_job_log", "scrum:write", "missing-job", lambda s: _a(_MISSING, "wpis")),
    _order("finish_pipeline", "scrum:write", "bad-status", lambda s: _a(str(s.pipeline_id), status="zly")),
    _order("finish_pipeline", "scrum:write", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("finish_pipeline", "scrum:write", "missing-pipeline", lambda s: _a(_MISSING)),
    _order("get_pipeline", "scrum:read", "malformed-id", lambda s: _a(_MALFORMED)),
    _order("get_pipeline", "scrum:read", "missing-pipeline", lambda s: _a(_MISSING)),
    _order("get_pipeline_job_log", "scrum:read", "malformed-job-id", lambda s: _a(_MALFORMED)),
    _order("get_pipeline_job_log", "scrum:read", "missing-job", lambda s: _a(_MISSING)),
    _order("clean_pipeline_logs", "wiki:delete", "malformed-sprint-id", lambda s: _a(_MALFORMED)),
    _order("clean_pipeline_logs", "wiki:delete", "missing-sprint", lambda s: _a(_MISSING)),
    # --- dziennik aktywności ---
    _order("get_activity_log", "settings:read", "bad-entity-type", lambda s: _a(entity_type="zly")),
    _order("get_activity_log", "settings:read", "unknown-actor", lambda s: _a(actor_email="brak-takiego@example.com")),
]


class TestPermissionBeforeParsingValidationAndLookup:
    """Rola bez sprawdzanego uprawnienia dostaje komunikat odmowy zamiast błędu parsowania, walidacji albo lookupu."""

    @pytest.mark.parametrize("case", [pytest.param(c, id=c.id) for c in ORDER_CASES])
    async def test_denial_wins_over_other_errors(self, case, env):
        before = await snapshot(env)
        args, kwargs = case.call(env.seed)

        message = await env.error(f"allbut:{case.perm}", case.tool, args, kwargs)

        assert message == (case.message or f"Brak uprawnienia {case.perm}")
        assert await snapshot(env) == before
        assert env.graph_untouched()
        assert env.append_log.await_count == 0

    @pytest.mark.parametrize(
        ("tool", "perm", "call"),
        [
            pytest.param("list_graph_nodes", "connections:read", lambda s: _a(), id="list_graph_nodes"),
            pytest.param("create_graph_node", "connections:write", lambda s: _a("File", "a.py"), id="create_graph_node"),
            pytest.param("delete_graph_node", "connections:delete", lambda s: _a("n1"), id="delete_graph_node"),
            pytest.param("replace_graph", "connections:delete", lambda s: _a([], []), id="replace_graph"),
        ],
    )
    async def test_denial_wins_over_disabled_graph_gate(self, tool, perm, call, env):
        env.graph.is_enabled.return_value = False
        args, kwargs = call(env.seed)

        message = await env.error(f"allbut:{perm}", tool, args, kwargs)

        assert message == f"Brak uprawnienia {perm}"
        assert env.graph_untouched()

    async def test_role_with_permission_reaches_disabled_graph_gate(self, env):
        env.graph.is_enabled.return_value = False

        message = await env.error("owner", "list_graph_nodes", (), {})

        assert message == "Baza grafowa nie jest wlaczona (ENABLE_GRAPH_DB=false)"


# ---------------------------------------------------------------------------
# Dashboard: pipelines wymagają scrum:read
# ---------------------------------------------------------------------------


async def _login(env: Env, actor: ApiV2Actor) -> None:
    response = await env.client.post("/auth/login", data={"email": actor.user.email, "password": "testpass123"}, follow_redirects=False)
    assert response.status_code == 303


def _pipeline_urls(env: Env) -> list[tuple[str, str]]:
    base = f"/dashboard/{env.project.slug}/pipelines"
    pid = env.seed.pipeline_id
    return [
        ("list", f"{base}/"),
        ("api-list", f"{base}/api/list"),
        ("partial-list", f"{base}/partial/list"),
        ("api-pipeline", f"{base}/api/{pid}"),
        ("detail", f"{base}/{pid}"),
        ("partial-tree", f"{base}/{pid}/partial/tree"),
        ("job-detail", f"{base}/{pid}/jobs/{env.seed.job_id}"),
    ]


_PIPELINE_ROUTE_IDS = ["list", "api-list", "partial-list", "api-pipeline", "detail", "partial-tree", "job-detail"]


class TestDashboardPipelinesRequireScrumRead:
    @pytest.mark.parametrize("route", _PIPELINE_ROUTE_IDS)
    @pytest.mark.parametrize("role", ["empty", "allbut:scrum:read"])
    async def test_member_without_scrum_read_gets_403(self, role, route, env):
        await _login(env, await env.actor(role))
        url = dict(_pipeline_urls(env))[route]

        response = await env.client.get(url)

        assert response.status_code == 403
        assert "Job startowy" not in response.text

    @pytest.mark.parametrize("route", _PIPELINE_ROUTE_IDS)
    @pytest.mark.parametrize("role", ["owner", "member", "scrum_read_only"])
    async def test_member_with_scrum_read_gets_200(self, role, route, env):
        await _login(env, await env.actor(role))
        url = dict(_pipeline_urls(env))[route]

        response = await env.client.get(url)

        assert response.status_code == 200

    @pytest.mark.parametrize("route", _PIPELINE_ROUTE_IDS)
    async def test_superuser_without_membership_gets_200(self, route, env):
        superuser = await make_actor(env.db, "modrbac-superuser", is_superuser=True)
        await _login(env, superuser)
        url = dict(_pipeline_urls(env))[route]

        response = await env.client.get(url)

        assert response.status_code == 200

    async def test_regular_user_without_membership_gets_403(self, env):
        outsider = await make_actor(env.db, "modrbac-outsider")
        await _login(env, outsider)

        response = await env.client.get(f"/dashboard/{env.project.slug}/pipelines/api/list")

        assert response.status_code == 403


# ---------------------------------------------------------------------------
# Dashboard: 500ki nie pokazuje projektu po soft delete
# ---------------------------------------------------------------------------


def _sentry_urls(env: Env) -> list[tuple[str, str]]:
    base = f"/dashboard/{env.project.slug}/500ki"
    return [
        ("issue-list", f"{base}/issues"),
        ("issue-detail", f"{base}/issues/{env.seed.issue_id}"),
        ("setup-guide", f"{base}/setup-guide"),
    ]


_SENTRY_ROUTE_IDS = ["issue-list", "issue-detail", "setup-guide"]


class TestDashboardSentryHidesSoftDeletedProject:
    @pytest.mark.parametrize("route", _SENTRY_ROUTE_IDS)
    async def test_active_project_is_shown(self, route, env):
        await _login(env, await env.actor("owner"))

        response = await env.client.get(dict(_sentry_urls(env))[route])

        assert response.status_code == 200

    @pytest.mark.parametrize("route", _SENTRY_ROUTE_IDS)
    async def test_owner_gets_404_project_not_found_after_soft_delete(self, route, env):
        await _login(env, await env.actor("owner"))
        env.project.is_active = False
        await env.db.flush()

        response = await env.client.get(dict(_sentry_urls(env))[route])

        assert response.status_code == 404
        assert response.text == "Project not found"

    @pytest.mark.parametrize("route", _SENTRY_ROUTE_IDS)
    async def test_superuser_gets_404_project_not_found_after_soft_delete(self, route, env):
        superuser = await make_actor(env.db, "modrbac-sentry-superuser", is_superuser=True)
        await _login(env, superuser)
        env.project.is_active = False
        await env.db.flush()

        response = await env.client.get(dict(_sentry_urls(env))[route])

        assert response.status_code == 404
        assert response.text == "Project not found"
