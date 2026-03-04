"""Testy pokrywajace luki w pokryciu kodu -- Ticket.key, fingerprint edge cases, get_db, lifespan."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.services.fingerprint import compute_fingerprint


@pytest.mark.unit
class TestTicketKeyProperty:
    """Testy dla Ticket.key -- property zwracajace klucz JIRA-style."""

    def _make_ticket(self, number, project=None):
        """Tworzy mock Ticket z dzialajacym property key."""
        from monolynx.models.ticket import Ticket

        mock_ticket = MagicMock(spec=Ticket)
        mock_ticket.number = number
        mock_ticket.project = project
        # Use the real property
        mock_ticket.key = Ticket.key.fget(mock_ticket)
        return mock_ticket

    def test_key_with_project_code(self):
        project = MagicMock()
        project.code = "PIM"
        ticket = self._make_ticket(42, project)
        assert ticket.key == "PIM-42"

    def test_key_with_project_code_different_number(self):
        project = MagicMock()
        project.code = "LEP"
        ticket = self._make_ticket(1, project)
        assert ticket.key == "LEP-1"

    def test_key_without_project(self):
        ticket = self._make_ticket(99, None)
        assert ticket.key == "?-99"

    def test_key_with_project_no_code(self):
        project = MagicMock()
        project.code = ""
        ticket = self._make_ticket(7, project)
        assert ticket.key == "?-7"

    def test_key_with_project_code_none(self):
        project = MagicMock()
        project.code = None
        ticket = self._make_ticket(3, project)
        assert ticket.key == "?-3"


@pytest.mark.unit
class TestFingerprintEdgeCases:
    """Testy pokrywajace brakujace galezi w compute_fingerprint."""

    def test_stacktrace_is_string(self):
        exc = {"type": "ValueError", "stacktrace": "not a dict"}
        only_type = compute_fingerprint({"type": "ValueError"})
        assert compute_fingerprint(exc) == only_type

    def test_stacktrace_is_list(self):
        exc = {"type": "TypeError", "stacktrace": ["frame1", "frame2"]}
        only_type = compute_fingerprint({"type": "TypeError"})
        assert compute_fingerprint(exc) == only_type

    def test_stacktrace_is_number(self):
        exc = {"type": "RuntimeError", "stacktrace": 42}
        only_type = compute_fingerprint({"type": "RuntimeError"})
        assert compute_fingerprint(exc) == only_type

    def test_stacktrace_is_none(self):
        exc = {"type": "KeyError", "stacktrace": None}
        only_type = compute_fingerprint({"type": "KeyError"})
        assert compute_fingerprint(exc) == only_type

    def test_frame_is_not_dict_string(self):
        exc = {
            "type": "ValueError",
            "stacktrace": {
                "frames": [
                    "not a dict frame",
                    {"filename": "app/views.py", "function": "handler"},
                ]
            },
        }
        exc_clean = {
            "type": "ValueError",
            "stacktrace": {"frames": [{"filename": "app/views.py", "function": "handler"}]},
        }
        assert compute_fingerprint(exc) == compute_fingerprint(exc_clean)

    def test_frame_is_not_dict_number(self):
        exc = {
            "type": "ValueError",
            "stacktrace": {"frames": [123, {"filename": "app/utils.py", "function": "parse"}]},
        }
        exc_clean = {
            "type": "ValueError",
            "stacktrace": {"frames": [{"filename": "app/utils.py", "function": "parse"}]},
        }
        assert compute_fingerprint(exc) == compute_fingerprint(exc_clean)

    def test_frame_is_none(self):
        exc = {
            "type": "ValueError",
            "stacktrace": {"frames": [None, {"filename": "app/models.py", "function": "save"}]},
        }
        exc_clean = {
            "type": "ValueError",
            "stacktrace": {"frames": [{"filename": "app/models.py", "function": "save"}]},
        }
        assert compute_fingerprint(exc) == compute_fingerprint(exc_clean)

    def test_frames_field_is_not_list(self):
        exc = {"type": "ValueError", "stacktrace": {"frames": "not a list"}}
        only_type = compute_fingerprint({"type": "ValueError"})
        assert compute_fingerprint(exc) == only_type

    def test_frames_field_is_dict(self):
        exc = {"type": "ValueError", "stacktrace": {"frames": {"0": {"filename": "app.py"}}}}
        only_type = compute_fingerprint({"type": "ValueError"})
        assert compute_fingerprint(exc) == only_type


@pytest.mark.unit
class TestGetDb:
    """Testy dla get_db -- async generator z async_session_factory."""

    async def test_get_db_yields_session(self):
        from monolynx.database import get_db

        mock_session = AsyncMock()
        mock_factory = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session
        mock_cm.__aexit__.return_value = None
        mock_factory.return_value = mock_cm

        with patch("monolynx.database.async_session_factory", mock_factory):
            gen = get_db()
            session = await gen.__anext__()
            assert session is mock_session
            with pytest.raises(StopAsyncIteration):
                await gen.__anext__()

    async def test_get_db_can_be_used_in_async_for(self):
        from monolynx.database import get_db

        mock_session = AsyncMock()
        mock_factory = MagicMock()
        mock_cm = AsyncMock()
        mock_cm.__aenter__.return_value = mock_session
        mock_cm.__aexit__.return_value = None
        mock_factory.return_value = mock_cm

        sessions = []
        with patch("monolynx.database.async_session_factory", mock_factory):
            async for s in get_db():
                sessions.append(s)
        assert len(sessions) == 1
        assert sessions[0] is mock_session


@pytest.mark.unit
class TestLifespan:
    """Testy dla lifespan -- inicjalizacja i cleanup aplikacji."""

    async def test_lifespan_monitor_loop_disabled(self):
        mock_app = MagicMock()
        mock_session_manager_cm = AsyncMock()
        mock_session_manager_cm.__aenter__.return_value = None
        mock_session_manager_cm.__aexit__.return_value = None
        mock_session_manager = MagicMock()
        mock_session_manager.run.return_value = mock_session_manager_cm

        with (
            patch("monolynx.main.settings") as mock_settings,
            patch("monolynx.main.mcp_server") as mock_mcp,
            patch("monolynx.main.logger") as mock_logger,
            patch("monolynx.services.minio_client.ensure_bucket"),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock),
        ):
            mock_settings.LOG_LEVEL = "info"
            mock_settings.ENVIRONMENT = "test"
            mock_settings.ENABLE_MONITOR_LOOP = False
            mock_mcp.session_manager = mock_session_manager

            from monolynx.main import lifespan

            async with lifespan(mock_app):
                pass

            mock_logger.info.assert_any_call("Monitor checker loop disabled (ENABLE_MONITOR_LOOP=false)")

    async def test_lifespan_monitor_loop_enabled(self):
        mock_app = MagicMock()
        mock_session_manager_cm = AsyncMock()
        mock_session_manager_cm.__aenter__.return_value = None
        mock_session_manager_cm.__aexit__.return_value = None
        mock_session_manager = MagicMock()
        mock_session_manager.run.return_value = mock_session_manager_cm

        async def fake_checker_loop(factory):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                raise

        with (
            patch("monolynx.main.settings") as mock_settings,
            patch("monolynx.main.mcp_server") as mock_mcp,
            patch("monolynx.main.logger"),
            patch("monolynx.services.minio_client.ensure_bucket"),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock),
            patch("monolynx.services.monitor_loop.monitor_checker_loop", side_effect=fake_checker_loop) as mock_loop,
        ):
            mock_settings.LOG_LEVEL = "info"
            mock_settings.ENVIRONMENT = "test"
            mock_settings.ENABLE_MONITOR_LOOP = True
            mock_mcp.session_manager = mock_session_manager

            from monolynx.main import lifespan

            async with lifespan(mock_app):
                mock_loop.assert_called_once()

    async def test_lifespan_minio_failure_does_not_crash(self):
        mock_app = MagicMock()
        mock_session_manager_cm = AsyncMock()
        mock_session_manager_cm.__aenter__.return_value = None
        mock_session_manager_cm.__aexit__.return_value = None
        mock_session_manager = MagicMock()
        mock_session_manager.run.return_value = mock_session_manager_cm

        with (
            patch("monolynx.main.settings") as mock_settings,
            patch("monolynx.main.mcp_server") as mock_mcp,
            patch("monolynx.main.logger") as mock_logger,
            patch("monolynx.services.minio_client.ensure_bucket", side_effect=ConnectionError("MinIO down")),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock),
        ):
            mock_settings.LOG_LEVEL = "info"
            mock_settings.ENVIRONMENT = "test"
            mock_settings.ENABLE_MONITOR_LOOP = False
            mock_mcp.session_manager = mock_session_manager

            from monolynx.main import lifespan

            async with lifespan(mock_app):
                pass

            mock_logger.exception.assert_any_call("Nie udalo sie zainicjalizowac MinIO bucket")

    async def test_lifespan_neo4j_init_failure_does_not_crash(self):
        mock_app = MagicMock()
        mock_session_manager_cm = AsyncMock()
        mock_session_manager_cm.__aenter__.return_value = None
        mock_session_manager_cm.__aexit__.return_value = None
        mock_session_manager = MagicMock()
        mock_session_manager.run.return_value = mock_session_manager_cm

        with (
            patch("monolynx.main.settings") as mock_settings,
            patch("monolynx.main.mcp_server") as mock_mcp,
            patch("monolynx.main.logger") as mock_logger,
            patch("monolynx.services.minio_client.ensure_bucket"),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock, side_effect=ConnectionError("Neo4j down")),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock),
        ):
            mock_settings.LOG_LEVEL = "info"
            mock_settings.ENVIRONMENT = "test"
            mock_settings.ENABLE_MONITOR_LOOP = False
            mock_mcp.session_manager = mock_session_manager

            from monolynx.main import lifespan

            async with lifespan(mock_app):
                pass

            mock_logger.exception.assert_any_call("Nie udalo sie zainicjalizowac Neo4j")

    async def test_lifespan_neo4j_close_failure_does_not_crash(self):
        mock_app = MagicMock()
        mock_session_manager_cm = AsyncMock()
        mock_session_manager_cm.__aenter__.return_value = None
        mock_session_manager_cm.__aexit__.return_value = None
        mock_session_manager = MagicMock()
        mock_session_manager.run.return_value = mock_session_manager_cm

        with (
            patch("monolynx.main.settings") as mock_settings,
            patch("monolynx.main.mcp_server") as mock_mcp,
            patch("monolynx.main.logger") as mock_logger,
            patch("monolynx.services.minio_client.ensure_bucket"),
            patch("monolynx.services.graph.init_driver", new_callable=AsyncMock),
            patch("monolynx.services.graph.init_schema", new_callable=AsyncMock),
            patch("monolynx.services.graph.close_driver", new_callable=AsyncMock, side_effect=RuntimeError("Close failed")),
        ):
            mock_settings.LOG_LEVEL = "info"
            mock_settings.ENVIRONMENT = "test"
            mock_settings.ENABLE_MONITOR_LOOP = False
            mock_mcp.session_manager = mock_session_manager

            from monolynx.main import lifespan

            async with lifespan(mock_app):
                pass

            mock_logger.exception.assert_any_call("Blad zamykania Neo4j")
