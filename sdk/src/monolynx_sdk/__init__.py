"""Monolynx SDK -- minimalistyczny error tracker dla Django.

Uzycie:
    MIDDLEWARE = ["monolynx_sdk.middleware.MonolynxMiddleware", ...]
    MONOLYNX_DSN = "https://<key>@sentry.example.com/api/v1/events"
"""

from __future__ import annotations

import logging

from monolynx_sdk.client import MonolynxClient
from monolynx_sdk.config import Config

logger = logging.getLogger("monolynx")

_client: MonolynxClient | None = None


def init(settings: object | None = None) -> MonolynxClient:
    """Inicjalizuje SDK z Django settings. Zwraca klienta."""
    global _client
    try:
        if _client is not None:
            return _client
        config = Config.from_django_settings(settings)
        _client = MonolynxClient(config)
        logger.info("Monolynx SDK zainicjalizowany (env=%s)", config.environment)
        return _client
    except Exception as e:
        logger.error("Monolynx SDK: blad inicjalizacji: %s", e)
        raise


def get_client() -> MonolynxClient | None:
    """Zwraca biezacego klienta SDK lub None."""
    return _client


def capture_exception(
    exception: BaseException,
    request: object | None = None,
) -> str:
    """Przechwytuje wyjatek i wysyla event. Zwraca event_id."""
    try:
        if _client is None:
            logger.warning("Monolynx SDK: nie zainicjalizowany (brak init())")
            return ""
        return _client.capture_exception(exception, request)
    except Exception as e:
        logger.warning("Monolynx SDK: blad capture_exception: %s", e)
        return ""


def capture_message(
    message: str,
    level: str = "error",
    request: object | None = None,
) -> str:
    """Wysyla wiadomosc tekstowa jako event. Zwraca event_id."""
    try:
        if _client is None:
            logger.warning("Monolynx SDK: nie zainicjalizowany (brak init())")
            return ""
        return _client.capture_message(message, level, request)
    except Exception as e:
        logger.warning("Monolynx SDK: blad capture_message: %s", e)
        return ""
