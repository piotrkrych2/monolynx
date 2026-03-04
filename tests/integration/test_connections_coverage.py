"""Testy integracyjne -- pokrycie brakujacych sciezek w dashboard/connections.py."""

import secrets
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from tests.conftest import login_session


async def _create_project(db_session, slug):
    code = "C" + secrets.token_hex(4).upper()
    project = Project(
        name=f"Conn {slug}",
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _login_and_add_member(client, db_session, project, email):
    await login_session(client, db_session, email=email)
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    member = ProjectMember(project_id=project.id, user_id=user.id, role="member")
    db_session.add(member)
    await db_session.flush()
    return user


def _mock_graph_service_enabled(mock_gs):
    mock_gs.is_enabled.return_value = True
    mock_gs._driver = MagicMock()
    mock_gs.get_stats = AsyncMock(return_value={"total_nodes": 5, "total_edges": 3, "nodes_by_type": {"File": 2}, "edges_by_type": {"CALLS": 3}})
    mock_gs.list_nodes = AsyncMock(
        return_value=[
            {
                "id": uuid.uuid4().hex,
                "project_id": str(uuid.uuid4()),
                "name": "main.py",
                "type": "File",
                "file_path": "/src/main.py",
                "line_number": None,
                "metadata": {},
            }
        ]
    )
    mock_gs.create_node = AsyncMock(
        return_value={
            "id": uuid.uuid4().hex,
            "project_id": str(uuid.uuid4()),
            "name": "TestNode",
            "type": "File",
            "file_path": None,
            "line_number": None,
            "metadata": {},
        }
    )
    mock_gs.create_edge = AsyncMock(return_value={"source_id": "src-1", "target_id": "tgt-1", "type": "CALLS", "metadata": {}})
    mock_gs.delete_node = AsyncMock(return_value=True)
    mock_gs.get_graph = AsyncMock(return_value={"nodes": [], "edges": []})


@pytest.mark.integration
class TestProjectNotFoundRedirects:
    async def _login(self, client, db_session):
        await login_session(client, db_session, email=f"pnf-{uuid.uuid4().hex[:8]}@test.com")

    async def test_connections_index_project_not_found(self, client, db_session):
        await self._login(client, db_session)
        resp = await client.get("/dashboard/nonexistent-slug-xyz/connections/", follow_redirects=False)
        assert resp.status_code == 302

    async def test_node_list_project_not_found(self, client, db_session):
        await self._login(client, db_session)
        resp = await client.get("/dashboard/nonexistent-slug-xyz/connections/nodes", follow_redirects=False)
        assert resp.status_code == 302

    async def test_node_create_form_project_not_found(self, client, db_session):
        await self._login(client, db_session)
        resp = await client.get("/dashboard/nonexistent-slug-xyz/connections/nodes/create", follow_redirects=False)
        assert resp.status_code == 302

    async def test_node_create_post_project_not_found(self, client, db_session):
        await self._login(client, db_session)
        resp = await client.post(
            "/dashboard/nonexistent-slug-xyz/connections/nodes/create",
            data={"name": "Test", "type": "File"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    async def test_edge_create_form_project_not_found(self, client, db_session):
        await self._login(client, db_session)
        resp = await client.get("/dashboard/nonexistent-slug-xyz/connections/edges/create", follow_redirects=False)
        assert resp.status_code == 302

    async def test_edge_create_post_project_not_found(self, client, db_session):
        await self._login(client, db_session)
        resp = await client.post(
            "/dashboard/nonexistent-slug-xyz/connections/edges/create",
            data={"source_id": "a", "target_id": "b", "type": "CALLS"},
            follow_redirects=False,
        )
        assert resp.status_code == 302

    async def test_node_delete_project_not_found(self, client, db_session):
        await self._login(client, db_session)
        resp = await client.post("/dashboard/nonexistent-slug-xyz/connections/nodes/abc123/delete", follow_redirects=False)
        assert resp.status_code == 302


@pytest.mark.integration
class TestNodeListSearch:
    async def test_node_list_with_search_param(self, client, db_session):
        slug = f"nls-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"nls-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.get(f"/dashboard/{project.slug}/connections/nodes?search=main")

        assert resp.status_code == 200
        mock_gs.list_nodes.assert_called_once()


@pytest.mark.integration
class TestNodeCreateWithLineNumber:
    async def test_node_create_with_line_number(self, client, db_session):
        slug = f"ncln-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"ncln-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/create",
                data={"name": "MyFunc", "type": "Function", "file_path": "/src/utils.py", "line_number": "42"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        node_data = mock_gs.create_node.call_args[0][1]
        assert node_data["line_number"] == 42
        assert node_data["file_path"] == "/src/utils.py"

    async def test_node_create_invalid_line_number(self, client, db_session):
        slug = f"nciln-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"nciln-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/create",
                data={"name": "MyFunc", "type": "Function", "line_number": "abc"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        node_data = mock_gs.create_node.call_args[0][1]
        assert node_data["line_number"] is None


@pytest.mark.integration
class TestNodeCreateError:
    async def test_node_create_exception(self, client, db_session):
        slug = f"nce-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"nce-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            mock_gs.create_node = AsyncMock(side_effect=Exception("Neo4j connection failed"))
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/create",
                data={"name": "FailNode", "type": "File"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/connections/nodes" in resp.headers["location"]


@pytest.mark.integration
class TestEdgeCreateValidation:
    async def test_edge_create_empty_source_id(self, client, db_session):
        slug = f"ecms-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"ecms-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/edges/create",
                data={"source_id": "", "target_id": "tgt-1", "type": "CALLS"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/edges/create" in resp.headers["location"]

    async def test_edge_create_invalid_type(self, client, db_session):
        slug = f"ecit-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"ecit-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/edges/create",
                data={"source_id": "src-1", "target_id": "tgt-1", "type": "INVALID"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/edges/create" in resp.headers["location"]

    async def test_edge_create_returns_none(self, client, db_session):
        slug = f"ecrn-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"ecrn-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            mock_gs.create_edge = AsyncMock(return_value=None)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/edges/create",
                data={"source_id": "src-1", "target_id": "tgt-1", "type": "CALLS"},
                follow_redirects=False,
            )

        assert resp.status_code == 303

    async def test_edge_create_exception(self, client, db_session):
        slug = f"ecex-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"ecex-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            mock_gs.create_edge = AsyncMock(side_effect=Exception("Neo4j error"))
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/edges/create",
                data={"source_id": "src-1", "target_id": "tgt-1", "type": "CALLS"},
                follow_redirects=False,
            )

        assert resp.status_code == 303


@pytest.mark.integration
class TestNodeDeleteEdgeCases:
    async def test_node_delete_returns_false(self, client, db_session):
        slug = f"ndnf-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"ndnf-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            mock_gs.delete_node = AsyncMock(return_value=False)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/nonexistent-id/delete",
                follow_redirects=False,
            )

        assert resp.status_code == 303

    async def test_node_delete_exception(self, client, db_session):
        slug = f"ndex-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"ndex-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            mock_gs.delete_node = AsyncMock(side_effect=Exception("Neo4j error"))
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/abc123/delete",
                follow_redirects=False,
            )

        assert resp.status_code == 303


@pytest.mark.integration
class TestGraphApiEdgeCases:
    async def test_graph_api_with_type_filter(self, client, db_session):
        slug = f"gatf-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"gatf-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.get(f"/dashboard/{project.slug}/connections/api/graph?type=File")

        assert resp.status_code == 200
        assert "nodes" in resp.json()

    async def test_graph_api_project_not_found(self, client, db_session):
        await login_session(client, db_session, email=f"gapnf-{uuid.uuid4().hex[:8]}@test.com")
        resp = await client.get("/dashboard/nonexistent-slug-xyz/connections/api/graph")
        assert resp.status_code == 404

    async def test_graph_api_graph_disabled(self, client, db_session):
        slug = f"gad-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"gad-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            mock_gs.is_enabled.return_value = False
            mock_gs._driver = None
            resp = await client.get(f"/dashboard/{project.slug}/connections/api/graph")

        assert resp.status_code == 200
        assert resp.json() == {"nodes": [], "edges": []}

    async def test_graph_api_get_graph_exception(self, client, db_session):
        slug = f"gaex-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        await _login_and_add_member(client, db_session, project, f"gaex-{uuid.uuid4().hex[:8]}@test.com")

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            mock_gs.get_graph = AsyncMock(side_effect=Exception("Neo4j timeout"))
            resp = await client.get(f"/dashboard/{project.slug}/connections/api/graph")

        assert resp.status_code == 200
        assert resp.json() == {"nodes": [], "edges": []}
