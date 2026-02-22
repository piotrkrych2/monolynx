"""Django middleware przechwytujace nieobsluzone wyjatki."""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("open_sentry")


class OpenSentryMiddleware:
    """Django middleware przechwytujace nieobsluzone wyjatki.

    Dodaj jako PIERWSZY element MIDDLEWARE w settings.py.
    """

    def __init__(self, get_response: Callable[..., Any]) -> None:
        self.get_response = get_response
        try:
            from django.conf import settings

            import open_sentry_sdk

            open_sentry_sdk.init(settings)
        except Exception as e:
            logger.error("OpenSentryMiddleware: nie udalo sie zainicjalizowac SDK: %s", e)

    def __call__(self, request: Any) -> Any:
        return self.get_response(request)

    def process_exception(self, request: Any, exception: BaseException) -> None:
        """Wywoływane przez Django gdy view rzuci nieobsluzone exception."""
        try:
            import open_sentry_sdk

            event_id = open_sentry_sdk.capture_exception(exception, request=request)
            request.open_sentry_event_id = event_id
        except Exception as e:
            logger.warning("OpenSentryMiddleware: blad podczas raportowania: %s", e)

        return None
