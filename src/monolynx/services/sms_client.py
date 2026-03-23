"""Serwis SMS -- wysylanie powiadomien przez lepszesmsy.pl."""

from __future__ import annotations

import json
import logging
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

from monolynx.config import settings

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)

LEPSZESMSY_API_URL = "https://lepszesmsy.pl/api/messages/"


def _send_sms_sync(phone: str, message: str) -> None:
    """Wysyla SMS synchronicznie (uruchamiany w tle przez executor)."""
    if not settings.LEPSZESMSY_LICENSE_KEY:
        logger.warning("LEPSZESMSY_LICENSE_KEY nie skonfigurowany -- SMS do %s nie zostal wyslany", phone)
        return

    payload = json.dumps(
        {
            "external_id": str(uuid.uuid4()),
            "license__key": settings.LEPSZESMSY_LICENSE_KEY,
            "message": message,
            "phone": phone,
            "priority": 10,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        LEPSZESMSY_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            logger.info("SMS do %s wyslany, status HTTP: %d", phone, status)
    except Exception:
        logger.exception("Blad wysylania SMS do %s", phone)


def send_sms(phone: str, message: str) -> None:
    """Wysyla SMS w tle -- nigdy nie crashuje aplikacji."""
    try:
        _executor.submit(_send_sms_sync, phone, message)
    except Exception:
        logger.exception("Blad zlecania wysylki SMS do %s", phone)
