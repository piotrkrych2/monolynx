"""Testy dla src/monolynx/main.py: lifespan(), _register_routers(), i endpointow landing/markdown."""

import contextlib
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI

from monolynx.main import app as main_app
from monolynx.main import lifespan


def _flatten_routes(routes):
    """FastAPI wraps app.include_router() targets as _IncludedRouter, which has no
    .path of its own - actual routes live on .original_router.routes and may nest again."""
    flat = []
    for route in routes:
        original_router = getattr(route, "original_router", None)
        if original_router is not None:
            flat.extend(_flatten_routes(original_router.routes))
        else:
            flat.append(route)
    return flat


@contextlib.asynccontextmanager
async def _fake_session_manager_cm():
    yield


def _patch_session_manager_run():
    """mcp_server.session_manager.run() musi zwrocic swiezy async context manager przy kazdym wywolaniu."""
    return patch(
        "monolynx.main.mcp_server.session_manager.run",
        side_effect=lambda: _fake_session_manager_cm(),
    )


@pytest.mark.unit
class TestLifespan:
    async def test_happy_path_initializes_minio_neo4j_and_monitor_loop(self):
        fake_app = FastAPI()

        with (
            patch("monolynx.services.minio_client.ensure_bucket") as mock_ensure_bucket,
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock) as mock_init_driver,
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock) as mock_init_schema,
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock) as mock_close_driver,
            patch("monolynx.services.monitor_loop.monitor_checker_loop", new_callable=AsyncMock) as mock_loop,
            patch("monolynx.main.settings.ENABLE_MONITOR_LOOP", True),
            _patch_session_manager_run(),
        ):
            async with lifespan(fake_app):
                pass

        mock_ensure_bucket.assert_called_once()
        mock_init_driver.assert_awaited_once()
        mock_init_schema.assert_awaited_once()
        mock_close_driver.assert_awaited_once()
        mock_loop.assert_called_once()

    async def test_minio_exception_is_swallowed(self):
        fake_app = FastAPI()

        with (
            patch("monolynx.services.minio_client.ensure_bucket", side_effect=RuntimeError("minio down")),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock),
            patch("monolynx.main.settings.ENABLE_MONITOR_LOOP", False),
            _patch_session_manager_run(),
        ):
            async with lifespan(fake_app):
                pass

    async def test_neo4j_init_exception_is_swallowed(self):
        fake_app = FastAPI()

        with (
            patch("monolynx.services.minio_client.ensure_bucket"),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock, side_effect=RuntimeError("neo4j down")),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock),
            patch("monolynx.main.settings.ENABLE_MONITOR_LOOP", False),
            _patch_session_manager_run(),
        ):
            async with lifespan(fake_app):
                pass

    async def test_neo4j_close_exception_is_swallowed(self):
        fake_app = FastAPI()

        with (
            patch("monolynx.services.minio_client.ensure_bucket"),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock, side_effect=RuntimeError("close failed")),
            patch("monolynx.main.settings.ENABLE_MONITOR_LOOP", False),
            _patch_session_manager_run(),
        ):
            async with lifespan(fake_app):
                pass

    async def test_monitor_loop_disabled_does_not_start_task(self, caplog):
        fake_app = FastAPI()

        with (
            patch("monolynx.services.minio_client.ensure_bucket"),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock),
            patch("monolynx.services.monitor_loop.monitor_checker_loop", new_callable=AsyncMock) as mock_loop,
            patch("monolynx.main.settings.ENABLE_MONITOR_LOOP", False),
            _patch_session_manager_run(),
            caplog.at_level("INFO", logger="monolynx"),
        ):
            async with lifespan(fake_app):
                pass

        mock_loop.assert_not_called()
        assert any("Monitor checker loop disabled" in message for message in caplog.messages)


@pytest.mark.unit
class TestRegisterRouters:
    def test_expected_route_prefixes_are_registered(self):
        paths = [getattr(route, "path", "") for route in _flatten_routes(main_app.routes)]

        assert any(p.startswith("/auth") for p in paths)
        assert any(p.startswith("/api/v1/events") for p in paths)
        assert any(p.startswith("/api/v1/issues") for p in paths)
        assert any(p.startswith("/hb") for p in paths)
        assert any(p.startswith("/dashboard") for p in paths)
        assert any(p.startswith("/oauth") or "/oauth" in p for p in paths)

    def test_health_endpoint_registered(self):
        paths = [getattr(route, "path", "") for route in _flatten_routes(main_app.routes)]

        assert "/api/v1/health" in paths

    def test_mcp_mounted_at_root_and_slash_mcp(self):
        # "/mcp" mount always present; root mount registered last with path ""
        assert any(getattr(route, "path", None) == "/mcp" for route in main_app.routes)


@pytest.mark.integration
class TestHealthCheck:
    async def test_health_returns_ok(self, client):
        response = await client.get("/api/v1/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}


@pytest.mark.integration
class TestMarkdownStaticEndpoints:
    async def test_llms_txt_returns_content(self, client):
        response = await client.get("/llms.txt")

        assert response.status_code == 200
        assert "text/plain" in response.headers["content-type"]

    async def test_llms_txt_404_when_missing(self, client):
        with patch.object(Path, "is_file", return_value=False):
            response = await client.get("/llms.txt")

        assert response.status_code == 404

    async def test_how_to_use_monolynx_returns_markdown(self, client):
        response = await client.get("/how-to-use-monolynx.md")

        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]

    async def test_how_to_use_monolynx_404_when_missing(self, client):
        with patch.object(Path, "is_file", return_value=False):
            response = await client.get("/how-to-use-monolynx.md")

        assert response.status_code == 404

    async def test_agent_explain_returns_markdown(self, client):
        response = await client.get("/agent-explain.md")

        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]

    async def test_agent_explain_404_when_missing(self, client):
        with patch.object(Path, "is_file", return_value=False):
            response = await client.get("/agent-explain.md")

        assert response.status_code == 404

    async def test_agent_bootstrap_returns_markdown(self, client):
        response = await client.get("/agent-bootstrap.md")

        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]

    async def test_agent_bootstrap_404_when_missing(self, client):
        with patch.object(Path, "is_file", return_value=False):
            response = await client.get("/agent-bootstrap.md")

        assert response.status_code == 404


@pytest.mark.integration
class TestIndexMarkdown:
    async def test_index_md_defaults_to_en(self, client):
        response = await client.get("/index.md")

        assert response.status_code == 200
        assert "text/markdown" in response.headers["content-type"]
        assert "# Monolynx" in response.text

    async def test_index_md_pl(self, client):
        response = await client.get("/index.md", params={"lang": "pl"})

        assert response.status_code == 200
        assert "# Monolynx" in response.text

    async def test_index_md_invalid_lang_falls_back_to_en(self, client):
        response = await client.get("/index.md", params={"lang": "de"})

        assert response.status_code == 200
        assert "# Monolynx" in response.text


@pytest.mark.integration
class TestLandingAndContactPages:
    async def test_landing_page_redirects_when_skip_landing_page_enabled(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", True)

        response = await client.get("/", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    async def test_landing_page_renders_when_skip_landing_page_disabled(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/", params={"lang": "pl"})

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    async def test_landing_page_invalid_lang_falls_back_to_en(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/", params={"lang": "xx"})

        assert response.status_code == 200

    async def test_contact_page_redirects_when_skip_landing_page_enabled(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", True)

        response = await client.get("/contact", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "/auth/login"

    async def test_contact_page_renders_when_skip_landing_page_disabled(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/contact", params={"lang": "pl"})

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]

    async def test_contact_page_invalid_lang_falls_back_to_en(self, client, monkeypatch):
        monkeypatch.setattr("monolynx.main.settings.SKIP_LANDING_PAGE", False)

        response = await client.get("/contact", params={"lang": "xx"})

        assert response.status_code == 200
