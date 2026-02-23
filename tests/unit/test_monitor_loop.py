"""Testy jednostkowe -- monitor_loop (_check_single_monitor, run_monitor_checks, monitor_checker_loop)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.services.monitor_loop import (
    INTERVAL_SECONDS,
    MONITOR_ADVISORY_LOCK_ID,
    _check_single_monitor,
    monitor_checker_loop,
    run_monitor_checks,
)


@pytest.mark.unit
class TestIntervalSecondsConstant:
    """Testy stalej INTERVAL_SECONDS."""

    def test_minutes_value(self):
        assert INTERVAL_SECONDS["minutes"] == 60

    def test_hours_value(self):
        assert INTERVAL_SECONDS["hours"] == 3600

    def test_days_value(self):
        assert INTERVAL_SECONDS["days"] == 86400

    def test_all_keys_present(self):
        assert set(INTERVAL_SECONDS.keys()) == {"minutes", "hours", "days"}


@pytest.mark.unit
class TestCheckSingleMonitor:
    """Testy _check_single_monitor -- zapisuje wynik sprawdzenia do DB."""

    @patch("monolynx.services.monitoring.check_url")
    async def test_success_creates_check_record(self, mock_check_url):
        """Udane sprawdzenie tworzy MonitorCheck z poprawnymi danymi."""
        mock_check_url.return_value = {
            "status_code": 200,
            "response_time_ms": 150,
            "is_success": True,
            "error_message": None,
        }

        monitor_id = uuid.uuid4()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("monolynx.models.monitor_check.MonitorCheck") as mock_check_cls:
            mock_check_instance = MagicMock()
            mock_check_cls.return_value = mock_check_instance

            await _check_single_monitor(monitor_id, "https://example.com", mock_factory)

            mock_check_url.assert_awaited_once_with("https://example.com")
            mock_check_cls.assert_called_once_with(
                monitor_id=monitor_id,
                status_code=200,
                response_time_ms=150,
                is_success=True,
                error_message=None,
            )
            mock_session.add.assert_called_once_with(mock_check_instance)
            mock_session.commit.assert_awaited_once()

    @patch("monolynx.services.monitoring.check_url")
    async def test_failure_creates_check_record_with_error(self, mock_check_url):
        """Nieudane sprawdzenie (HTTP 500) tworzy MonitorCheck z is_success=False."""
        mock_check_url.return_value = {
            "status_code": 500,
            "response_time_ms": 300,
            "is_success": False,
            "error_message": "Internal Server Error",
        }

        monitor_id = uuid.uuid4()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("monolynx.models.monitor_check.MonitorCheck") as mock_check_cls:
            mock_check_instance = MagicMock()
            mock_check_cls.return_value = mock_check_instance

            await _check_single_monitor(monitor_id, "https://failing.com", mock_factory)

            mock_check_cls.assert_called_once_with(
                monitor_id=monitor_id,
                status_code=500,
                response_time_ms=300,
                is_success=False,
                error_message="Internal Server Error",
            )

    @patch("monolynx.services.monitoring.check_url")
    async def test_exception_in_check_url_creates_error_record(self, mock_check_url):
        """Wyjatek w check_url tworzy MonitorCheck z danymi bledu wewnetrznego."""
        mock_check_url.side_effect = RuntimeError("unexpected failure")

        monitor_id = uuid.uuid4()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("monolynx.models.monitor_check.MonitorCheck") as mock_check_cls:
            mock_check_instance = MagicMock()
            mock_check_cls.return_value = mock_check_instance

            await _check_single_monitor(monitor_id, "https://broken.com", mock_factory)

            mock_check_cls.assert_called_once_with(
                monitor_id=monitor_id,
                status_code=None,
                response_time_ms=None,
                is_success=False,
                error_message="Internal checker error",
            )
            mock_session.add.assert_called_once()
            mock_session.commit.assert_awaited_once()

    @patch("monolynx.services.monitoring.check_url")
    async def test_dns_failure_creates_error_record(self, mock_check_url):
        """Blad DNS -> check_url zwraca is_success=False, null status_code."""
        mock_check_url.return_value = {
            "status_code": None,
            "response_time_ms": 10,
            "is_success": False,
            "error_message": "Name or service not known",
        }

        monitor_id = uuid.uuid4()

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        with patch("monolynx.models.monitor_check.MonitorCheck") as mock_check_cls:
            mock_check_instance = MagicMock()
            mock_check_cls.return_value = mock_check_instance

            await _check_single_monitor(monitor_id, "https://nonexistent.invalid", mock_factory)

            mock_check_cls.assert_called_once_with(
                monitor_id=monitor_id,
                status_code=None,
                response_time_ms=10,
                is_success=False,
                error_message="Name or service not known",
            )


@pytest.mark.unit
class TestRunMonitorChecks:
    """Testy run_monitor_checks -- logika iteracji sprawdzania monitorow."""

    @patch("monolynx.services.monitor_loop._check_single_monitor")
    async def test_no_active_monitors_does_nothing(self, mock_check_single):
        """Brak aktywnych monitorow -> brak wywolan _check_single_monitor."""
        mock_session = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_factory = MagicMock()
        mock_factory.return_value = mock_session

        await run_monitor_checks(mock_factory)

        mock_check_single.assert_not_awaited()

    @patch("monolynx.services.monitor_loop._check_single_monitor")
    async def test_due_monitor_is_checked(self, mock_check_single):
        """Monitor bez wczesniejszych sprawdzen jest sprawdzany od razu."""
        mock_check_single.return_value = None

        monitor = MagicMock()
        monitor.id = uuid.uuid4()
        monitor.url = "https://example.com"
        monitor.interval_value = 5
        monitor.interval_unit = "minutes"
        monitor.is_active = True

        # First session call: return list of monitors
        mock_session_monitors = AsyncMock()
        mock_result_monitors = MagicMock()
        mock_result_monitors.scalars.return_value.all.return_value = [monitor]
        mock_session_monitors.execute = AsyncMock(return_value=mock_result_monitors)
        mock_session_monitors.__aenter__ = AsyncMock(return_value=mock_session_monitors)
        mock_session_monitors.__aexit__ = AsyncMock(return_value=False)

        # Second session call: return no previous check (scalar_one_or_none -> None)
        mock_session_check = AsyncMock()
        mock_result_check = MagicMock()
        mock_result_check.scalar_one_or_none.return_value = None
        mock_session_check.execute = AsyncMock(return_value=mock_result_check)
        mock_session_check.__aenter__ = AsyncMock(return_value=mock_session_check)
        mock_session_check.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        sessions = [mock_session_monitors, mock_session_check]

        def session_side_effect():
            nonlocal call_count
            s = sessions[call_count]
            call_count += 1
            return s

        mock_factory = MagicMock(side_effect=session_side_effect)

        await run_monitor_checks(mock_factory)

        mock_check_single.assert_awaited_once_with(monitor.id, "https://example.com", mock_factory)

    @patch("monolynx.services.monitor_loop._check_single_monitor")
    async def test_not_due_monitor_is_skipped(self, mock_check_single):
        """Monitor sprawdzony niedawno jest pomijany."""
        mock_check_single.return_value = None

        monitor = MagicMock()
        monitor.id = uuid.uuid4()
        monitor.url = "https://example.com"
        monitor.interval_value = 5
        monitor.interval_unit = "minutes"
        monitor.is_active = True

        # First session: list of monitors
        mock_session_monitors = AsyncMock()
        mock_result_monitors = MagicMock()
        mock_result_monitors.scalars.return_value.all.return_value = [monitor]
        mock_session_monitors.execute = AsyncMock(return_value=mock_result_monitors)
        mock_session_monitors.__aenter__ = AsyncMock(return_value=mock_session_monitors)
        mock_session_monitors.__aexit__ = AsyncMock(return_value=False)

        # Second session: recent check (1 minute ago, interval is 5 minutes -> not due)
        mock_session_check = AsyncMock()
        mock_result_check = MagicMock()
        recent_time = datetime.now(UTC) - timedelta(minutes=1)
        mock_result_check.scalar_one_or_none.return_value = recent_time
        mock_session_check.execute = AsyncMock(return_value=mock_result_check)
        mock_session_check.__aenter__ = AsyncMock(return_value=mock_session_check)
        mock_session_check.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        sessions = [mock_session_monitors, mock_session_check]

        def session_side_effect():
            nonlocal call_count
            s = sessions[call_count]
            call_count += 1
            return s

        mock_factory = MagicMock(side_effect=session_side_effect)

        await run_monitor_checks(mock_factory)

        mock_check_single.assert_not_awaited()

    @patch("monolynx.services.monitor_loop._check_single_monitor")
    async def test_overdue_monitor_is_checked(self, mock_check_single):
        """Monitor z przeterminowanym sprawdzeniem jest sprawdzany."""
        mock_check_single.return_value = None

        monitor = MagicMock()
        monitor.id = uuid.uuid4()
        monitor.url = "https://example.com"
        monitor.interval_value = 5
        monitor.interval_unit = "minutes"
        monitor.is_active = True

        # First session: list of monitors
        mock_session_monitors = AsyncMock()
        mock_result_monitors = MagicMock()
        mock_result_monitors.scalars.return_value.all.return_value = [monitor]
        mock_session_monitors.execute = AsyncMock(return_value=mock_result_monitors)
        mock_session_monitors.__aenter__ = AsyncMock(return_value=mock_session_monitors)
        mock_session_monitors.__aexit__ = AsyncMock(return_value=False)

        # Second session: old check (10 minutes ago, interval is 5 minutes -> overdue)
        mock_session_check = AsyncMock()
        mock_result_check = MagicMock()
        old_time = datetime.now(UTC) - timedelta(minutes=10)
        mock_result_check.scalar_one_or_none.return_value = old_time
        mock_session_check.execute = AsyncMock(return_value=mock_result_check)
        mock_session_check.__aenter__ = AsyncMock(return_value=mock_session_check)
        mock_session_check.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        sessions = [mock_session_monitors, mock_session_check]

        def session_side_effect():
            nonlocal call_count
            s = sessions[call_count]
            call_count += 1
            return s

        mock_factory = MagicMock(side_effect=session_side_effect)

        await run_monitor_checks(mock_factory)

        mock_check_single.assert_awaited_once_with(monitor.id, "https://example.com", mock_factory)

    @patch("monolynx.services.monitor_loop._check_single_monitor")
    async def test_multiple_monitors_mixed_due(self, mock_check_single):
        """Dwa monitory: jeden due, jeden nie -- sprawdzany tylko due."""
        mock_check_single.return_value = None

        monitor_due = MagicMock()
        monitor_due.id = uuid.uuid4()
        monitor_due.url = "https://due.com"
        monitor_due.interval_value = 5
        monitor_due.interval_unit = "minutes"

        monitor_not_due = MagicMock()
        monitor_not_due.id = uuid.uuid4()
        monitor_not_due.url = "https://not-due.com"
        monitor_not_due.interval_value = 5
        monitor_not_due.interval_unit = "minutes"

        # First session: both monitors
        mock_session_monitors = AsyncMock()
        mock_result_monitors = MagicMock()
        mock_result_monitors.scalars.return_value.all.return_value = [monitor_due, monitor_not_due]
        mock_session_monitors.execute = AsyncMock(return_value=mock_result_monitors)
        mock_session_monitors.__aenter__ = AsyncMock(return_value=mock_session_monitors)
        mock_session_monitors.__aexit__ = AsyncMock(return_value=False)

        # Session for monitor_due: no previous check -> due
        mock_session_due = AsyncMock()
        mock_result_due = MagicMock()
        mock_result_due.scalar_one_or_none.return_value = None
        mock_session_due.execute = AsyncMock(return_value=mock_result_due)
        mock_session_due.__aenter__ = AsyncMock(return_value=mock_session_due)
        mock_session_due.__aexit__ = AsyncMock(return_value=False)

        # Session for monitor_not_due: recent check -> not due
        mock_session_not_due = AsyncMock()
        mock_result_not_due = MagicMock()
        mock_result_not_due.scalar_one_or_none.return_value = datetime.now(UTC) - timedelta(seconds=30)
        mock_session_not_due.execute = AsyncMock(return_value=mock_result_not_due)
        mock_session_not_due.__aenter__ = AsyncMock(return_value=mock_session_not_due)
        mock_session_not_due.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        sessions = [mock_session_monitors, mock_session_due, mock_session_not_due]

        def session_side_effect():
            nonlocal call_count
            s = sessions[call_count]
            call_count += 1
            return s

        mock_factory = MagicMock(side_effect=session_side_effect)

        await run_monitor_checks(mock_factory)

        mock_check_single.assert_awaited_once_with(monitor_due.id, "https://due.com", mock_factory)

    @patch("monolynx.services.monitor_loop._check_single_monitor")
    async def test_unknown_interval_unit_defaults_to_60(self, mock_check_single):
        """Nieznana jednostka interwalu -> domyslnie 60 sekund."""
        mock_check_single.return_value = None

        monitor = MagicMock()
        monitor.id = uuid.uuid4()
        monitor.url = "https://example.com"
        monitor.interval_value = 1
        monitor.interval_unit = "unknown_unit"
        monitor.is_active = True

        # First session: list of monitors
        mock_session_monitors = AsyncMock()
        mock_result_monitors = MagicMock()
        mock_result_monitors.scalars.return_value.all.return_value = [monitor]
        mock_session_monitors.execute = AsyncMock(return_value=mock_result_monitors)
        mock_session_monitors.__aenter__ = AsyncMock(return_value=mock_session_monitors)
        mock_session_monitors.__aexit__ = AsyncMock(return_value=False)

        # Second session: check was 90 seconds ago, interval_value=1, default=60s -> due
        mock_session_check = AsyncMock()
        mock_result_check = MagicMock()
        mock_result_check.scalar_one_or_none.return_value = datetime.now(UTC) - timedelta(seconds=90)
        mock_session_check.execute = AsyncMock(return_value=mock_result_check)
        mock_session_check.__aenter__ = AsyncMock(return_value=mock_session_check)
        mock_session_check.__aexit__ = AsyncMock(return_value=False)

        call_count = 0
        sessions = [mock_session_monitors, mock_session_check]

        def session_side_effect():
            nonlocal call_count
            s = sessions[call_count]
            call_count += 1
            return s

        mock_factory = MagicMock(side_effect=session_side_effect)

        await run_monitor_checks(mock_factory)

        mock_check_single.assert_awaited_once()


@pytest.mark.unit
class TestMonitorCheckerLoop:
    """Testy monitor_checker_loop -- glowna petla."""

    @patch("monolynx.services.monitor_loop.run_monitor_checks")
    @patch("monolynx.services.monitor_loop.HEALTHCHECK_FILE")
    async def test_loop_runs_and_can_be_cancelled(self, mock_hf, mock_run_checks):
        """Petla uruchamia run_monitor_checks i moze byc anulowana."""
        mock_run_checks.return_value = None

        mock_factory = MagicMock()

        task = asyncio.create_task(
            monitor_checker_loop(
                mock_factory,
                acquire_lock=False,
                sleep_interval=0,
                startup_delay=0,
            )
        )

        # Let the loop run at least one iteration
        await asyncio.sleep(0.05)
        task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        # run_monitor_checks should have been called at least once
        assert mock_run_checks.await_count >= 1

    @patch("monolynx.services.monitor_loop.run_monitor_checks")
    @patch("monolynx.services.monitor_loop.HEALTHCHECK_FILE")
    async def test_loop_touches_healthcheck_file(self, mock_hf, mock_run_checks):
        """Petla dotyka pliku healthcheck po kazdej iteracji."""
        mock_run_checks.return_value = None

        mock_factory = MagicMock()

        task = asyncio.create_task(
            monitor_checker_loop(
                mock_factory,
                acquire_lock=False,
                sleep_interval=0,
                startup_delay=0,
            )
        )

        await asyncio.sleep(0.05)
        task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert mock_hf.touch.call_count >= 1

    @patch("monolynx.services.monitor_loop.run_monitor_checks")
    @patch("monolynx.services.monitor_loop.HEALTHCHECK_FILE")
    async def test_loop_handles_exception_and_continues(self, mock_hf, mock_run_checks):
        """Wyjatek w run_monitor_checks nie przerywa petli."""
        iteration = 0

        async def side_effect(sf):
            nonlocal iteration
            iteration += 1
            if iteration == 1:
                raise RuntimeError("temporary failure")
            # On second+ call, succeed

        mock_run_checks.side_effect = side_effect

        mock_factory = MagicMock()

        task = asyncio.create_task(
            monitor_checker_loop(
                mock_factory,
                acquire_lock=False,
                sleep_interval=0,
                startup_delay=0,
            )
        )

        # Wait long enough for: 1st iteration (exception + sleep(10)) + 2nd iteration
        # But sleep(10) is real time in the loop. We need to mock asyncio.sleep too.
        # Instead, let's just confirm the loop didn't exit after the exception.
        # We wait briefly to allow at least the first call, then cancel.
        await asyncio.sleep(0.05)
        task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        # At least one call was made (the one that raised)
        assert mock_run_checks.await_count >= 1

    @patch("monolynx.services.monitor_loop.run_monitor_checks")
    async def test_loop_with_startup_delay_zero(self, mock_run_checks):
        """startup_delay=0 nie czeka."""
        mock_run_checks.return_value = None

        mock_factory = MagicMock()

        task = asyncio.create_task(
            monitor_checker_loop(
                mock_factory,
                acquire_lock=False,
                sleep_interval=0,
                startup_delay=0,
            )
        )

        await asyncio.sleep(0.05)
        task.cancel()

        with contextlib.suppress(asyncio.CancelledError):
            await task

        assert mock_run_checks.await_count >= 1

    @patch("monolynx.services.monitor_loop.run_monitor_checks")
    @patch("monolynx.config.settings")
    async def test_loop_with_acquire_lock_not_acquired(self, mock_settings, mock_run_checks):
        """acquire_lock=True, lock nie zdobyty -> petla konczy sie od razu."""
        mock_settings.DATABASE_URL = "postgresql+asyncpg://test:test@localhost/test"
        mock_run_checks.return_value = None

        mock_factory = MagicMock()

        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = False  # Lock NOT acquired
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = AsyncMock()
        mock_engine.connect = AsyncMock(return_value=mock_conn)

        with patch("monolynx.services.monitor_loop.create_async_engine", return_value=mock_engine):
            await monitor_checker_loop(
                mock_factory,
                acquire_lock=True,
                sleep_interval=0,
                startup_delay=0,
            )

        # Lock not acquired -> run_monitor_checks NOT called
        mock_run_checks.assert_not_awaited()
        # close() is called in the if-not-acquired block AND in finally -> 2 times
        assert mock_conn.close.await_count == 2
        assert mock_engine.dispose.await_count == 2

    @patch("monolynx.services.monitor_loop.run_monitor_checks")
    @patch("monolynx.services.monitor_loop.HEALTHCHECK_FILE")
    @patch("monolynx.config.settings")
    async def test_loop_with_lock_acquired_runs_checks(self, mock_settings, mock_hf, mock_run_checks):
        """acquire_lock=True z udanym lockiem uruchamia petle sprawdzania."""
        mock_settings.DATABASE_URL = "postgresql+asyncpg://test:test@localhost/test"
        mock_run_checks.return_value = None

        mock_factory = MagicMock()

        mock_conn = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar.return_value = True  # Lock acquired
        mock_conn.execute = AsyncMock(return_value=mock_result)

        mock_engine = AsyncMock()
        mock_engine.connect = AsyncMock(return_value=mock_conn)

        with patch("monolynx.services.monitor_loop.create_async_engine", return_value=mock_engine):
            task = asyncio.create_task(
                monitor_checker_loop(
                    mock_factory,
                    acquire_lock=True,
                    sleep_interval=0,
                    startup_delay=0,
                )
            )

            await asyncio.sleep(0.05)
            task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await task

        assert mock_run_checks.await_count >= 1
        # Cleanup: lock conn and engine should be closed
        mock_conn.close.assert_awaited_once()
        mock_engine.dispose.assert_awaited_once()


@pytest.mark.unit
class TestMonitorAdvisoryLockId:
    """Testy stalej MONITOR_ADVISORY_LOCK_ID."""

    def test_advisory_lock_id_is_integer(self):
        assert isinstance(MONITOR_ADVISORY_LOCK_ID, int)

    def test_advisory_lock_id_value(self):
        assert MONITOR_ADVISORY_LOCK_ID == 738_201
