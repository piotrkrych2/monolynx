"""Testy integracyjne -- MCP tool install_monolynx_skills."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.mcp_server import _list_available_skills, install_monolynx_skills


def _make_ctx(token: str = "test-token") -> MagicMock:
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _mock_auth(slug: str = "my-project"):
    mock_user = MagicMock()
    mock_user.id = uuid.uuid4()
    mock_project = MagicMock()
    mock_project.id = uuid.uuid4()
    mock_project.slug = slug
    return AsyncMock(return_value=(mock_user, mock_project)), mock_project


@pytest.mark.integration
class TestInstallMonolynxSkills:
    async def test_default_returns_catalog_only(self):
        mock_auth_fn, project = _mock_auth("my-project")

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(_make_ctx(), project.slug)

        available = _list_available_skills()
        assert result["project_slug"] == "my-project"
        assert set(result["available"]) == set(available)
        assert "skills" not in result
        assert len(result["catalog"]) == len(available)
        for entry in result["catalog"]:
            assert entry["name"] in available
            assert isinstance(entry["description"], str)
        assert "hint" in result

    async def test_filters_by_skill_names(self):
        mock_auth_fn, project = _mock_auth()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["monolynx-help"],
            )

        assert [s["name"] for s in result["skills"]] == ["monolynx-help"]

    async def test_rejects_unknown_skill_name(self):
        mock_auth_fn, project = _mock_auth()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn), pytest.raises(ValueError, match="Nieznane skille"):
            await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["nope"],
            )

    async def test_replaces_project_slug_placeholder(self):
        mock_auth_fn, project = _mock_auth("acme-inc")

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["monolynx-work"],
            )

        content = result["skills"][0]["content"]
        assert "<PROJECT-SLUG>" not in content
        assert "<PROJECT-ID>" not in content
        assert "acme-inc" in content

    async def test_dedupes_skill_names(self):
        mock_auth_fn, project = _mock_auth()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["monolynx-help", "monolynx-help"],
            )

        assert len(result["skills"]) == 1

    async def test_replaces_project_id_placeholder(self):
        mock_auth_fn, project = _mock_auth("foo-bar")

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["monolynx-ticket-create"],
            )

        content = result["skills"][0]["content"]
        assert "<PROJECT-ID>" not in content
        assert "foo-bar" in content
