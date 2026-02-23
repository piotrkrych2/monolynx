"""Serwis email -- wysylanie zaproszen przez SMTP."""

from __future__ import annotations

import logging
import smtplib
import uuid
from concurrent.futures import ThreadPoolExecutor
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from monolynx.config import settings

logger = logging.getLogger(__name__)

_executor = ThreadPoolExecutor(max_workers=1)


def _send_email_sync(to: str, subject: str, body_html: str) -> None:
    """Wysyla email synchronicznie (uruchamiany w utle przez executor)."""
    if not settings.SMTP_HOST:
        logger.warning("SMTP nie skonfigurowany -- email do %s nie zostal wyslany", to)
        return

    msg = MIMEMultipart("alternative")
    msg["From"] = settings.SMTP_FROM_EMAIL
    msg["To"] = to
    msg["Subject"] = subject
    msg.attach(MIMEText(body_html, "html", "utf-8"))

    try:
        if settings.SMTP_USE_TLS:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)
            server.starttls()
        else:
            server = smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT)

        if settings.SMTP_USER:
            server.login(settings.SMTP_USER, settings.SMTP_PASSWORD)

        server.sendmail(settings.SMTP_FROM_EMAIL, to, msg.as_string())
        server.quit()
        logger.info("Email wyslany do %s", to)
    except Exception:
        logger.exception("Blad wysylania emaila do %s", to)


def send_email(to: str, subject: str, body_html: str) -> None:
    """Wysyla email w tle -- nigdy nie crashuje aplikacji."""
    try:
        _executor.submit(_send_email_sync, to, subject, body_html)
    except Exception:
        logger.exception("Blad zlecania wysylki emaila do %s", to)


def send_invitation_email(to: str, first_name: str, token: uuid.UUID) -> None:
    """Wysyla email z zaproszeniem do ustawienia hasla."""
    link = f"{settings.APP_URL.rstrip('/')}/auth/accept-invite/{token}"
    subject = "Zaproszenie do Monolynx"
    btn = "display:inline-block;padding:10px 24px;background:#4f46e5;color:#fff;text-decoration:none;border-radius:6px;"
    greeting = f" {first_name}" if first_name else ""
    body_html = (
        "<html><body style='font-family:sans-serif;color:#333;'>"
        f"<h2>Witaj{greeting}!</h2>"
        "<p>Zostales zaproszony do platformy "
        "<strong>Monolynx</strong>.</p>"
        "<p>Kliknij ponizszy link, aby ustawic haslo "
        "i aktywowac konto:</p>"
        f'<p><a href="{link}" style="{btn}">'
        "Ustaw haslo</a></p>"
        '<p style="color:#666;font-size:13px;">'
        "Link wygasa po 7 dniach.</p>"
        '<p style="color:#999;font-size:12px;">'
        "Jesli nie oczekiwales tego zaproszenia, "
        "zignoruj ta wiadomosc.</p>"
        "</body></html>"
    )
    send_email(to, subject, body_html)
