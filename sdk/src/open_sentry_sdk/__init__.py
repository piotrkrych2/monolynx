"""Open Sentry SDK -- minimalistyczny error tracker dla Django.

Uzycie:
    MIDDLEWARE = ["open_sentry_sdk.middleware.OpenSentryMiddleware", ...]
    OPEN_SENTRY_DSN = "https://<key>@sentry.example.com/api/v1/events"
"""

from __future__ import annotations

import logging

from open_sentry_sdk.client import OpenSentryClient
from open_sentry_sdk.config import Config

logger = logging.getLogger("open_sentry")

_client: OpenSentryClient | None = None


def init(settings: object | None = None) -> OpenSentryClient:
    """Inicjalizuje SDK z Django settings. Zwraca klienta."""
    global _client
    try:
        if _client is not None:
            return _client
        config = Config.from_django_settings(settings)
        _client = OpenSentryClient(config)
        logger.info("Open Sentry SDK zainicjalizowany (env=%s)", config.environment)
        return _client
    except Exception as e:
        logger.error("Open Sentry SDK: blad inicjalizacji: %s", e)
        raise


def get_client() -> OpenSentryClient | None:
    """Zwraca biezacego klienta SDK lub None."""
    return _client


def capture_exception(
    exception: BaseException,
    request: object | None = None,
) -> str:
    """Przechwytuje wyjatek i wysyla event. Zwraca event_id."""
    try:
        if _client is None:
            logger.warning("Open Sentry SDK: nie zainicjalizowany (brak init())")
            return ""
        return _client.capture_exception(exception, request)
    except Exception as e:
        logger.warning("Open Sentry SDK: blad capture_exception: %s", e)
        return ""


def capture_message(
    message: str,
    level: str = "error",
    request: object | None = None,
) -> str:
    """Wysyla wiadomosc tekstowa jako event. Zwraca event_id."""
    try:
        if _client is None:
            logger.warning("Open Sentry SDK: nie zainicjalizowany (brak init())")
            return ""
        return _client.capture_message(message, level, request)
    except Exception as e:
        logger.warning("Open Sentry SDK: blad capture_message: %s", e)
        return ""
