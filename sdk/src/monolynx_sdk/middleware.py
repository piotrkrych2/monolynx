"""Django middleware przechwytujace nieobsluzone wyjatki."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("monolynx")


class MonolynxMiddleware:
    """Django middleware przechwytujace nieobsluzone wyjatki.

    Dodaj jako PIERWSZY element MIDDLEWARE w settings.py.
    """

    def __init__(self, get_response: Callable[..., Any]) -> None:
        self.get_response = get_response
        try:
            from django.conf import settings

            import monolynx_sdk

            monolynx_sdk.init(settings)
        except Exception as e:
            logger.error("MonolynxMiddleware: nie udalo sie zainicjalizowac SDK: %s", e)

    def __call__(self, request: Any) -> Any:
        return self.get_response(request)

    def process_exception(self, request: Any, exception: BaseException) -> None:
        """Wywoływane przez Django gdy view rzuci nieobsluzone exception."""
        try:
            import monolynx_sdk

            event_id = monolynx_sdk.capture_exception(exception, request=request)
            request.monolynx_event_id = event_id
        except Exception as e:
            logger.warning("MonolynxMiddleware: blad podczas raportowania: %s", e)

        return None
