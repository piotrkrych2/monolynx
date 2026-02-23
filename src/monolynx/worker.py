"""Standalone worker monitoringu -- uruchamiany jako: python -m monolynx.worker."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from monolynx.config import settings
from monolynx.services.monitor_loop import monitor_checker_loop

logger = logging.getLogger("monolynx.worker")


async def main() -> None:
    logging.basicConfig(level=settings.LOG_LEVEL.upper())
    logger.info("Monitor worker starting (env=%s)", settings.ENVIRONMENT)

    from monolynx.database import async_session_factory, engine

    loop = asyncio.get_running_loop()
    checker_task = asyncio.create_task(monitor_checker_loop(async_session_factory, acquire_lock=True))

    stop_event = asyncio.Event()

    def _shutdown(sig: signal.Signals) -> None:
        logger.info("Received %s -- shutting down", sig.name)
        checker_task.cancel()
        stop_event.set()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown, sig)

    await stop_event.wait()

    with contextlib.suppress(asyncio.CancelledError):
        await checker_task

    await engine.dispose()
    logger.info("Monitor worker stopped")


if __name__ == "__main__":
    asyncio.run(main())
