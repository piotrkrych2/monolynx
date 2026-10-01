"""Komenda `monolynx version [--check]`: wersja zainstalowanego CLI i opcjonalnie najnowsza z PyPI.

Bez `--check` wypisuje tylko `{"current": ...}` i nie robi żadnego żądania sieciowego.
Z `--check` pyta publiczne JSON API PyPI (`info.version`) i dokłada `latest`,
`update_available` oraz `upgrade_command` dobraną do sposobu instalacji, rozpoznanego po `sys.prefix` (brew, pipx, uv,
w pozostałych przypadkach pip). Brak sieci, timeout, status inny niż 200 albo odpowiedź o
złym kształcie to nie błąd komendy: kod 0, `latest` = null, `update_available` = false -
skille pluginu wołają ją w tle i nie mogą się wywrócić przez niedostępny PyPI.
Komenda nie czyta konfiguracji ani tokenu i nie wysyła ich nigdzie.
"""

from __future__ import annotations

import re
import sys

import httpx
import typer

from monolynx_cli import version as package_version
from monolynx_cli.output import emit

PYPI_URL = "https://pypi.org/pypi/monolynx-cli/json"
DEFAULT_TIMEOUT = 2.0
_NUMERIC_VERSION = re.compile(r"\d+(\.\d+)*")

_UPGRADE_COMMANDS = {
    "brew": "brew upgrade monolynx",
    "pipx": "pipx upgrade monolynx-cli",
    "uv": "uv tool upgrade monolynx-cli",
}
_FALLBACK_UPGRADE_COMMAND = "pip install -U monolynx-cli"


def detect_installer(prefix: str) -> str:
    """Rozpoznaj sposób instalacji po korzeniu środowiska CLI (`sys.prefix`): brew, pipx, uv albo unknown."""
    # Bez realpath, celowo: `bin/python` w venv to symlink do interpretera bazowego, więc po
    # rozwiązaniu ścieżka traci `/pipx/venvs/` i `/uv/tools/` (pipx na Pythonie z Homebrew
    # wyglądałby jak brew). Korzeń venv zostaje pod katalogiem menedżera.
    # pipx i uv przed brew: są bardziej szczegółowe niż samo `/homebrew/` w ścieżce.
    if "/pipx/venvs/" in prefix:
        return "pipx"
    if "/uv/tools/" in prefix:
        return "uv"
    if "/Cellar/" in prefix or "/homebrew/" in prefix:
        return "brew"
    return "unknown"


def upgrade_command(installer: str) -> str:
    return _UPGRADE_COMMANDS.get(installer, _FALLBACK_UPGRADE_COMMAND)


def fetch_latest(timeout: float = DEFAULT_TIMEOUT) -> str | None:
    """Najnowsza wersja z PyPI albo None przy każdym błędzie (sieć, status, kształt odpowiedzi)."""
    try:
        response = httpx.get(PYPI_URL, timeout=timeout)
        if response.status_code != 200:
            return None
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return None
    info = payload.get("info") if isinstance(payload, dict) else None
    latest = info.get("version") if isinstance(info, dict) else None
    return latest if isinstance(latest, str) and latest else None


def _version_key(value: str) -> tuple[int, ...] | None:
    if not _NUMERIC_VERSION.fullmatch(value):
        return None
    return tuple(int(part) for part in value.split("."))


def _is_newer(latest: str, current: str) -> bool:
    latest_key = _version_key(latest)
    current_key = _version_key(current)
    return latest_key is not None and current_key is not None and latest_key > current_key


def version(
    ctx: typer.Context,
    check: bool = typer.Option(False, "--check", help="Sprawdź w PyPI najnowszą wersję i podaj komendę aktualizacji."),
) -> None:
    """Pokaż wersję zainstalowanego CLI.

    Z --check sprawdza najnowszą wersję w PyPI i podaje komendę aktualizacji dla
    Twojego sposobu instalacji (brew, pipx, uv, pip). Brak sieci nie jest błędem:
    pole latest ma wtedy wartość null.

    Nie wymaga logowania ani konfiguracji.
    """
    # Czytane z modułu w chwili wywołania, nie kopiowane do stałej przy imporcie.
    current = package_version.__version__
    if not check:
        emit(ctx, {"current": current})
        return
    latest = fetch_latest()
    emit(
        ctx,
        {
            "current": current,
            "latest": latest,
            "update_available": latest is not None and _is_newer(latest, current),
            "upgrade_command": upgrade_command(detect_installer(sys.prefix)),
        },
    )
