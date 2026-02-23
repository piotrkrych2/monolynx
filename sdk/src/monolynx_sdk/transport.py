"""AsyncTransport -- wysylanie payloadow w watku tla (stdlib only)."""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from monolynx_sdk.config import Config

logger = logging.getLogger("monolynx")


class AsyncTransport:
    """Wysyla payloady w watku tla. Uzywa stdlib urllib -- zero zaleznosci."""

    def __init__(self, config: Config) -> None:
        self.config = config
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="monolynx")

    def send(self, payload: dict[str, object]) -> None:
        self._executor.submit(self._do_send, payload)

    def _do_send(self, payload: dict[str, object]) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        req = urllib.request.Request(
            url=self.config.server_url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "X-Monolynx-Key": self.config.api_key,
                "User-Agent": "monolynx-python/0.1.0",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.config.timeout) as resp:
                if resp.status >= 400:
                    logger.warning("Monolynx: serwer odpowiedzial kodem %d", resp.status)
        except urllib.error.HTTPError as e:
            logger.warning("Monolynx: blad HTTP %d: %s", e.code, e.reason)
        except urllib.error.URLError as e:
            logger.warning("Monolynx: blad polaczenia: %s", e.reason)
        except Exception as e:
            logger.warning("Monolynx: nieoczekiwany blad transportu: %s", e)

    def flush(self, timeout: float = 10.0) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="monolynx")

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)
