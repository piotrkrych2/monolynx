"""OpenSentryClient -- glowna klasa orkiestrujaca SDK."""

from __future__ import annotations

import logging

from open_sentry_sdk.config import Config
from open_sentry_sdk.payload import PayloadBuilder
from open_sentry_sdk.transport import AsyncTransport

logger = logging.getLogger("open_sentry")


class OpenSentryClient:
    def __init__(self, config: Config) -> None:
        self.config = config
        self._payload_builder = PayloadBuilder(config)
        self._transport = AsyncTransport(config)

    def capture_exception(
        self,
        exception: BaseException,
        request: object | None = None,
    ) -> str:
        """Przechwytuje wyjatek i wysyla event. Zwraca event_id."""
        try:
            payload = self._payload_builder.build_from_exception(exception, request)
            self._transport.send(payload)
            return payload["event_id"]
        except Exception as e:
            logger.warning("Open Sentry: blad podczas capture_exception: %s", e)
            return ""

    def capture_message(
        self,
        message: str,
        level: str = "error",
        request: object | None = None,
    ) -> str:
        """Wysyla wiadomosc tekstowa jako event. Zwraca event_id."""
        try:
            payload = self._payload_builder.build_from_message(message, level, request)
            self._transport.send(payload)
            return payload["event_id"]
        except Exception as e:
            logger.warning("Open Sentry: blad podczas capture_message: %s", e)
            return ""

    def flush(self) -> None:
        try:
            self._transport.flush()
        except Exception as e:
            logger.warning("Open Sentry: blad podczas flush: %s", e)

    def close(self) -> None:
        try:
            self._transport.close()
        except Exception as e:
            logger.warning("Open Sentry: blad podczas close: %s", e)
