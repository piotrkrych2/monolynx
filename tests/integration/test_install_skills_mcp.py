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

        content = result["skills"][0]["files"][0]["content"]
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

    async def test_returns_all_skill_files_with_skill_md_first(self):
        mock_auth_fn, project = _mock_auth()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["monolynx-work"],
            )

        files = result["skills"][0]["files"]
        paths = [f["relative_path"] for f in files]
        assert paths[0] == ".claude/skills/monolynx-work/SKILL.md"
        assert ".claude/skills/monolynx-work/pipeline.md" in paths
        assert ".claude/skills/monolynx-work/review-rubric.md" in paths

    @pytest.mark.parametrize(
        ("target", "expected_dir"),
        [("claude", ".claude/skills"), ("codex", ".agents/skills"), ("cursor", ".cursor/skills")],
    )
    async def test_target_changes_skills_directory(self, target: str, expected_dir: str):
        mock_auth_fn, project = _mock_auth()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["monolynx-help"],
                target=target,
            )

        assert result["skills_dir"] == expected_dir
        assert result["skills"][0]["files"][0]["relative_path"] == f"{expected_dir}/monolynx-help/SKILL.md"

    async def test_rejects_unknown_target(self):
        mock_auth_fn, project = _mock_auth()

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn), pytest.raises(ValueError, match="Nieznany target"):
            await install_monolynx_skills(_make_ctx(), project.slug, target="vscode")

    async def test_replaces_project_id_placeholder(self):
        mock_auth_fn, project = _mock_auth("foo-bar")

        with patch("monolynx.mcp_server._get_user_and_project", mock_auth_fn):
            result = await install_monolynx_skills(
                _make_ctx(),
                project.slug,
                skill_names=["monolynx-ticket-create"],
            )

        content = result["skills"][0]["files"][0]["content"]
        assert "<PROJECT-ID>" not in content
        assert "foo-bar" in content

    @pytest.mark.parametrize("target", ["claude", "codex", "cursor"])
    async def test_nested_resources_preserve_paths_order_and_script_content(self, tmp_path, target):
        skill = tmp_path / "monolynx-sample"
        (skill / "scripts").mkdir(parents=True)
        (skill / "references").mkdir()
        (skill / "SKILL.md").write_text("---\nname: monolynx-sample\ndescription: Sample\n---\n<PROJECT-SLUG>", encoding="utf-8")
        (skill / "references/context.md").write_text("Project <PROJECT-SLUG>", encoding="utf-8")
        (skill / "scripts/context.md").write_text("Other <PROJECT-ID>", encoding="utf-8")
        script = 'print("<PROJECT-SLUG>")\n'
        shell = 'echo "<PROJECT-ID>"\n'
        (skill / "scripts/run.py").write_text(script, encoding="utf-8")
        (skill / "scripts/run.sh").write_text(shell, encoding="utf-8")
        auth, project = _mock_auth("acme")
        with patch("monolynx.mcp_server._get_user_and_project", auth), patch("monolynx.mcp_server._STATIC_SKILLS_DIR", tmp_path):
            result = await install_monolynx_skills(_make_ctx(), project.slug, skill_names=["monolynx-sample"], target=target)
        prefix = f"{result['skills_dir']}/monolynx-sample/"
        files = {item["relative_path"].removeprefix(prefix): item["content"] for item in result["skills"][0]["files"]}
        assert list(files) == ["SKILL.md", "references/context.md", "scripts/context.md", "scripts/run.py", "scripts/run.sh"]
        assert files["references/context.md"] == "Project acme"
        assert files["scripts/context.md"] == "Other acme"
        assert files["scripts/run.py"] == script
        assert files["scripts/run.sh"] == shell

    async def test_rejects_symlink_resource_outside_package(self, tmp_path):
        packages = tmp_path / "packages"
        skill = packages / "monolynx-sample"
        (skill / "references").mkdir(parents=True)
        (skill / "SKILL.md").write_text("---\nname: monolynx-sample\n---\nSample", encoding="utf-8")
        outside = tmp_path / "outside.md"
        outside.write_text("private", encoding="utf-8")
        (skill / "references/outside.md").symlink_to(outside)
        auth, project = _mock_auth()
        with (
            patch("monolynx.mcp_server._get_user_and_project", auth),
            patch("monolynx.mcp_server._STATIC_SKILLS_DIR", packages),
            pytest.raises(ValueError),
        ):
            await install_monolynx_skills(_make_ctx(), project.slug, skill_names=["monolynx-sample"], target="codex")
