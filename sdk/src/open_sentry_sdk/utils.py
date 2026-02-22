"""Funkcje pomocnicze -- sanityzacja danych, ekstrakcja user info."""

from __future__ import annotations

SENSITIVE_KEYS = {
    "password",
    "passwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "session",
    "csrf",
    "csrftoken",
    "credit_card",
    "card_number",
    "cvv",
    "set-cookie",
    "x-api-key",
    "x-csrftoken",
}


def sanitize_headers(headers: dict[str, str]) -> dict[str, str]:
    """Filtruje wrazliwe naglowki."""
    return {k: "[Filtered]" if k.lower() in SENSITIVE_KEYS else v for k, v in headers.items()}


def sanitize_data(data: dict[str, object]) -> dict[str, object]:
    """Filtruje wrazliwe pola z danych POST."""
    return {k: "[Filtered]" if k.lower() in SENSITIVE_KEYS else v for k, v in data.items()}


def extract_user_info(request: object) -> dict[str, str | None] | None:
    """Bezpiecznie wyciaga user info z Django request."""
    try:
        user = getattr(request, "user", None)
        if user is None or not getattr(user, "is_authenticated", False):
            return None

        return {
            "id": str(getattr(user, "pk", "")),
            "username": getattr(user, "username", None),
            "email": getattr(user, "email", None),
            "ip_address": get_client_ip(request),
        }
    except Exception:
        return None


def get_client_ip(request: object) -> str:
    """Wyciaga IP klienta z Django request META."""
    try:
        meta = getattr(request, "META", {})
        forwarded = meta.get("HTTP_X_FORWARDED_FOR", "")
        if forwarded:
            return forwarded.split(",")[0].strip()
        return meta.get("REMOTE_ADDR", "")
    except Exception:
        return ""
