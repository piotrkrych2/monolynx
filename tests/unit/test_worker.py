"""Testy jednostkowe -- worker monitoringu."""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.unit
class TestWorkerImport:
    """Testy importu modulu worker."""

    def test_module_imports_successfully(self):
        """Modul worker importuje sie bez bledow."""
        import monolynx.worker

        assert hasattr(monolynx.worker, "main")

    def test_module_has_main_function(self):
        """Modul worker ma funkcje main()."""
        from monolynx.worker import main

        assert callable(main)
        assert asyncio.iscoroutinefunction(main)


@pytest.mark.unit
class TestWorkerMain:
    """Testy funkcji main() -- setup signal handlers, uruchomienie petli."""

    @patch("monolynx.worker.monitor_checker_loop")
    @patch("monolynx.worker.settings")
    async def test_main_starts_checker_task_and_handles_signal(self, mock_settings, mock_checker_loop):
        """main() tworzy task monitoringu i moze byc anulowana."""
        mock_settings.LOG_LEVEL = "info"
        mock_settings.ENVIRONMENT = "test"

        # Make the checker loop sleep forever until cancelled
        async def fake_checker_loop(session_factory, acquire_lock=True):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                return

        mock_checker_loop.side_effect = fake_checker_loop

        mock_engine = AsyncMock()
        mock_factory = MagicMock()

        with (
            patch("monolynx.database.async_session_factory", mock_factory),
            patch("monolynx.database.engine", mock_engine),
            patch("monolynx.worker.logging"),
        ):
            from monolynx.worker import main

            task = asyncio.create_task(main())

            # Give it time to set up signal handlers and start the checker task
            await asyncio.sleep(0.05)

            # Cancel the task (simulates shutdown)
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task

        # monitor_checker_loop was called
        mock_checker_loop.assert_called_once()

    @patch("monolynx.worker.monitor_checker_loop")
    @patch("monolynx.worker.settings")
    async def test_main_passes_acquire_lock_true(self, mock_settings, mock_checker_loop):
        """main() przekazuje acquire_lock=True do monitor_checker_loop."""
        mock_settings.LOG_LEVEL = "info"
        mock_settings.ENVIRONMENT = "test"

        async def fake_checker_loop(session_factory, acquire_lock=True):
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                return

        mock_checker_loop.side_effect = fake_checker_loop

        mock_engine = AsyncMock()
        mock_factory = MagicMock()

        with (
            patch("monolynx.database.async_session_factory", mock_factory),
            patch("monolynx.database.engine", mock_engine),
            patch("monolynx.worker.logging"),
        ):
            from monolynx.worker import main

            task = asyncio.create_task(main())
            await asyncio.sleep(0.05)
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task

        # Verify acquire_lock=True was passed
        call_kwargs = mock_checker_loop.call_args
        assert call_kwargs[1].get("acquire_lock") is True


@pytest.mark.unit
class TestWorkerModuleBlock:
    """Testy bloku __name__ == '__main__'."""

    def test_module_has_main_guard(self):
        """Modul worker zawiera blok if __name__ == '__main__'."""
        import inspect

        import monolynx.worker

        source = inspect.getsource(monolynx.worker)
        assert 'if __name__ == "__main__"' in source or "if __name__ == '__main__'" in source
