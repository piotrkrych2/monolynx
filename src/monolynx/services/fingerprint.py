"""Algorytm fingerprintowania -- serce systemu grupowania bledow."""

from __future__ import annotations

import hashlib

SKIP_PATTERNS = (
    "site-packages/",
    "/lib/python",
    "<frozen",
    "django/core/handlers",
    "django/middleware",
)


def compute_fingerprint(exception_data: dict[str, object]) -> str:
    """Oblicza fingerprint SHA256 ze stacktrace.

    Strategia:
    1. Bierze typ wyjatku (np. "ValueError")
    2. Bierze nazwy plikow + funkcji z ramek stacktrace (bez numerow linii!)
    3. Laczy je i hashuje SHA256

    Bez numerow linii -- bo dodanie jednej linii kodu zmienialyby fingerprint.
    """
    parts: list[str] = [str(exception_data.get("type", "UnknownError"))]

    stacktrace = exception_data.get("stacktrace", {})
    frames: list[dict[str, object]] = []
    if isinstance(stacktrace, dict):
        raw = stacktrace.get("frames", [])
        frames = raw if isinstance(raw, list) else []

    for frame in frames:
        if not isinstance(frame, dict):
            continue
        filename = str(frame.get("filename", ""))
        if _is_app_frame(filename):
            function = str(frame.get("function", "?"))
            parts.append(f"{filename}:{function}")

    raw = "|".join(parts)
    return hashlib.sha256(raw.encode()).hexdigest()


def _is_app_frame(filename: str) -> bool:
    """Filtruje ramki z site-packages i stdlib."""
    return not any(p in filename for p in SKIP_PATTERNS)
