"""Serwis powiadomien -- email, SMS, Slack dla monitorow."""

from __future__ import annotations

import html
import ipaddress
import json
import logging
import socket
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

ALERT_DEBOUNCE_MINUTES = 5
_slack_executor = ThreadPoolExecutor(max_workers=1)


def _is_webhook_url_safe(url: str) -> bool:
    """Waliduj URL webhooka pod katem SSRF. Zwraca True jesli bezpieczny."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in ("http", "https"):
        return False

    hostname = parsed.hostname
    if not hostname:
        return False

    blocked_hosts = {"localhost", "127.0.0.1", "0.0.0.0", "[::1]", "::1"}
    if hostname.lower() in blocked_hosts:
        return False

    try:
        addr_infos = socket.getaddrinfo(hostname, None, socket.AF_UNSPEC, socket.SOCK_STREAM)
    except socket.gaierror:
        return False

    for _family, _, _, _, sockaddr in addr_infos:
        ip_str = sockaddr[0]
        try:
            ip = ipaddress.ip_address(ip_str)
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            return False

    return True


def _build_alert_message(monitor_name: str, monitor_url: str, status_code: int | None, error_message: str | None) -> str:
    """Buduj tresc alertu."""
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    status_part = str(status_code) if status_code is not None else "brak"
    error_part = error_message or "brak"
    return f"\u26a0\ufe0f Monitor '{monitor_name}' - AWARIA | URL: {monitor_url} | Status: {status_part} | {error_part} | {timestamp}"


def _send_slack_webhook_sync(webhook_url: str, message: str) -> None:
    """Wysyla powiadomienie Slack przez webhook (synchronicznie)."""
    if not _is_webhook_url_safe(webhook_url):
        logger.warning("Webhook URL %s jest niedozwolony (SSRF protection)", webhook_url)
        return

    payload = json.dumps({"text": message}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("Slack webhook wyslany do %s, status: %d", webhook_url, resp.status)
    except Exception:
        logger.exception("Blad wysylania Slack webhook do %s", webhook_url)


async def send_monitor_alert(monitor: Any, check: Any, db: AsyncSession) -> None:
    """Wyslij alert dla monitora jesli jest skonfigurowany i debounce minął."""
    from monolynx.services.email import send_email
    from monolynx.services.sms_client import send_sms

    notification_config: dict[str, Any] = getattr(monitor, "notification_config", None) or {}
    if not notification_config:
        return

    # Debouncing: nie wysylaj czesciej niz co ALERT_DEBOUNCE_MINUTES minut
    last_alert_sent_at: datetime | None = getattr(monitor, "last_alert_sent_at", None)
    if last_alert_sent_at is not None:
        now = datetime.now(UTC)
        if last_alert_sent_at.tzinfo is None:
            last_alert_sent_at = last_alert_sent_at.replace(tzinfo=UTC)
        if now - last_alert_sent_at < timedelta(minutes=ALERT_DEBOUNCE_MINUTES):
            logger.debug(
                "Alert dla monitora %s pominieto (debounce %d min)",
                getattr(monitor, "name", monitor),
                ALERT_DEBOUNCE_MINUTES,
            )
            return

    monitor_name: str = getattr(monitor, "name", "") or str(getattr(monitor, "url", ""))
    monitor_url: str = getattr(monitor, "url", "")
    status_code: int | None = getattr(check, "status_code", None)
    error_message: str | None = getattr(check, "error_message", None)

    alert_text = _build_alert_message(monitor_name, monitor_url, status_code, error_message)
    alert_subject = f"\u26a0\ufe0f Monitor '{monitor_name}' - AWARIA"

    sent_any = False

    # Email
    if notification_config.get("email_enabled"):
        for recipient in notification_config.get("email_recipients", []):
            recipient = recipient.strip()
            if recipient:
                body_html = (
                    "<html><body style='font-family:sans-serif;color:#333;'>"
                    f"<h2>{html.escape(alert_subject)}</h2>"
                    f"<p><strong>URL:</strong> {html.escape(monitor_url)}</p>"
                    f"<p><strong>Status HTTP:</strong> {html.escape(str(status_code) if status_code is not None else 'brak')}</p>"
                    f"<p><strong>Blad:</strong> {html.escape(error_message or 'brak')}</p>"
                    f"<p style='color:#999;font-size:12px;'>{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}</p>"
                    "</body></html>"
                )
                send_email(recipient, alert_subject, body_html)
                sent_any = True

    # SMS
    if notification_config.get("sms_enabled"):
        for phone in notification_config.get("sms_recipients", []):
            phone = phone.strip()
            if phone:
                send_sms(phone, alert_text)
                sent_any = True

    # Slack
    if notification_config.get("slack_enabled"):
        import asyncio

        loop = asyncio.get_running_loop()
        for webhook_url in notification_config.get("slack_channels", []):
            webhook_url = webhook_url.strip()
            if webhook_url:
                try:
                    await loop.run_in_executor(_slack_executor, _send_slack_webhook_sync, webhook_url, alert_text)
                    sent_any = True
                except Exception:
                    logger.exception("Blad wysylania Slack webhook dla monitora %s", monitor_name)

    if sent_any:
        monitor.last_alert_sent_at = datetime.now(UTC)
        await db.commit()
