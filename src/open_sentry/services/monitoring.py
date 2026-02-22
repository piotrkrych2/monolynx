"""Serwis monitoringu -- sprawdzanie URL-i."""

from __future__ import annotations

import asyncio
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, Request, build_opener

logger = logging.getLogger("open_sentry.monitoring")

_executor = ThreadPoolExecutor(max_workers=4)

# Opener bez HTTPRedirectHandler -- zapobiega SSRF via redirect
_opener = build_opener(HTTPSHandler)


def _check_url_sync(url: str, timeout: int) -> dict[str, object]:
    """Synchroniczne sprawdzenie URL (uruchamiane w thread pool)."""
    start = time.monotonic()
    try:
        req = Request(url, method="GET")
        req.add_header("User-Agent", "OpenSentry-Monitor/1.0")
        with _opener.open(req, timeout=timeout) as resp:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            status_code = resp.status
            is_success = 200 <= status_code < 400
            return {
                "status_code": status_code,
                "response_time_ms": elapsed_ms,
                "is_success": is_success,
                "error_message": None,
            }
    except HTTPError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status_code": exc.code,
            "response_time_ms": elapsed_ms,
            "is_success": False,
            "error_message": str(exc.reason)[:1024],
        }
    except URLError as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status_code": None,
            "response_time_ms": elapsed_ms,
            "is_success": False,
            "error_message": str(exc.reason)[:1024],
        }
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        return {
            "status_code": None,
            "response_time_ms": elapsed_ms,
            "is_success": False,
            "error_message": str(exc)[:1024],
        }


async def check_url(url: str, timeout: int = 10) -> dict[str, object]:
    """Asynchroniczne sprawdzenie URL -- deleguje do thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, partial(_check_url_sync, url, timeout))
