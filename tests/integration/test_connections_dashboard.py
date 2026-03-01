"""Testy integracyjne -- dashboard polaczen (graf, node'y, edge'y, API)."""

import secrets
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from tests.conftest import login_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(db_session, slug):
    """Helper: tworzy projekt z unikalnym kodem."""
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
    """Helper: loguje uzytkownika i dodaje jako czlonka projektu."""
    await login_session(client, db_session, email=email)
    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()
    member = ProjectMember(project_id=project.id, user_id=user.id, role="member")
    db_session.add(member)
    await db_session.flush()
    return user


def _mock_graph_service_enabled(mock_gs):
    """Konfiguruje mock graph_service jako wlaczony z podstawowymi danymi."""
    mock_gs.is_enabled.return_value = True
    mock_gs._driver = MagicMock()
    mock_gs.get_stats = AsyncMock(
        return_value={
            "total_nodes": 5,
            "total_edges": 3,
            "nodes_by_type": {"File": 2, "Class": 3},
            "edges_by_type": {"CALLS": 3},
        }
    )
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
            },
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
    mock_gs.create_edge = AsyncMock(
        return_value={
            "source_id": "src-1",
            "target_id": "tgt-1",
            "type": "CALLS",
            "metadata": {},
        }
    )
    mock_gs.delete_node = AsyncMock(return_value=True)
    mock_gs.get_graph = AsyncMock(return_value={"nodes": [], "edges": []})


# ---------------------------------------------------------------------------
# 1. Connections index
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestConnectionsIndex:
    async def test_connections_index_requires_login(self, client, db_session):
        slug = f"ci-auth-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        resp = await client.get(
            f"/dashboard/{project.slug}/connections/",
            follow_redirects=False,
        )
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["location"]

    async def test_connections_index_renders(self, client, db_session):
        slug = f"ci-render-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"ci-render-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.get(f"/dashboard/{project.slug}/connections/")

        assert resp.status_code == 200

    async def test_connections_index_graph_disabled(self, client, db_session):
        slug = f"ci-disabled-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"ci-disabled-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            mock_gs.is_enabled.return_value = False
            mock_gs._driver = None
            resp = await client.get(f"/dashboard/{project.slug}/connections/")

        assert resp.status_code == 200
        assert "Neo4j" in resp.text


# ---------------------------------------------------------------------------
# 2. Node list
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNodeList:
    async def test_node_list_renders(self, client, db_session):
        slug = f"nl-render-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"nl-render-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.get(f"/dashboard/{project.slug}/connections/nodes")

        assert resp.status_code == 200

    async def test_node_list_with_filter(self, client, db_session):
        slug = f"nl-filter-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"nl-filter-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.get(f"/dashboard/{project.slug}/connections/nodes?type=File")

        assert resp.status_code == 200
        mock_gs.list_nodes.assert_called_once()
        call_kwargs = mock_gs.list_nodes.call_args
        assert call_kwargs[1].get("type_filter") == "File" or call_kwargs.kwargs.get("type_filter") == "File"


# ---------------------------------------------------------------------------
# 3. Node create
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNodeCreate:
    async def test_node_create_form(self, client, db_session):
        slug = f"nc-form-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"nc-form-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.get(f"/dashboard/{project.slug}/connections/nodes/create")

        assert resp.status_code == 200

    async def test_node_create_submit(self, client, db_session):
        slug = f"nc-submit-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"nc-submit-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/create",
                data={"name": "TestNode", "type": "File"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/connections/nodes" in resp.headers["location"]
        mock_gs.create_node.assert_called_once()

    async def test_node_create_empty_name(self, client, db_session):
        slug = f"nc-empty-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"nc-empty-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/create",
                data={"name": "", "type": "File"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/connections/nodes/create" in resp.headers["location"]

    async def test_node_create_invalid_type(self, client, db_session):
        slug = f"nc-invalid-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"nc-invalid-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/create",
                data={"name": "Test", "type": "InvalidType"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/connections/nodes/create" in resp.headers["location"]


# ---------------------------------------------------------------------------
# 4. Edge create
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestEdgeCreate:
    async def test_edge_create_form(self, client, db_session):
        slug = f"ec-form-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"ec-form-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.get(f"/dashboard/{project.slug}/connections/edges/create")

        assert resp.status_code == 200
        mock_gs.list_nodes.assert_called_once()

    async def test_edge_create_submit(self, client, db_session):
        slug = f"ec-submit-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"ec-submit-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/edges/create",
                data={"source_id": "src-1", "target_id": "tgt-1", "type": "CALLS"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/connections/" in resp.headers["location"]
        mock_gs.create_edge.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Node delete
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNodeDelete:
    async def test_node_delete(self, client, db_session):
        slug = f"nd-del-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"nd-del-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            resp = await client.post(
                f"/dashboard/{project.slug}/connections/nodes/abc123/delete",
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert "/connections/nodes" in resp.headers["location"]
        mock_gs.delete_node.assert_called_once()


# ---------------------------------------------------------------------------
# 6. Graph API
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGraphApi:
    async def test_graph_api(self, client, db_session):
        slug = f"ga-api-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        email = f"ga-api-{uuid.uuid4().hex[:8]}@test.com"
        await _login_and_add_member(client, db_session, project, email)

        with patch("monolynx.dashboard.connections.graph_service") as mock_gs:
            _mock_graph_service_enabled(mock_gs)
            mock_gs.get_graph = AsyncMock(
                return_value={
                    "nodes": [{"id": "n1", "name": "A", "type": "File"}],
                    "edges": [{"source_id": "n1", "target_id": "n2", "type": "CALLS"}],
                }
            )
            resp = await client.get(f"/dashboard/{project.slug}/connections/api/graph")

        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data

    async def test_graph_api_unauthorized(self, client, db_session):
        slug = f"ga-unauth-{uuid.uuid4().hex[:8]}"
        project = await _create_project(db_session, slug)
        resp = await client.get(
            f"/dashboard/{project.slug}/connections/api/graph",
            follow_redirects=False,
        )
        assert resp.status_code == 401
