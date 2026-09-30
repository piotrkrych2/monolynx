"""Wspólne helpery komend: ustawienia, klient projektu, potwierdzenia, paginacja, teksty z pliku/stdin.

Każda grupa komend otwiera klienta przez `new_client(ctx, settings)` (albo `open_project(ctx)`,
które dodaje sprawdzenie tokenu i projektu i zwraca (MonolynxClient, slug)). Fabryka tworzy
klienta przez nazwę modułową `MonolynxClient` w TYM module, którą testy podmieniają przez
monkeypatch (`monolynx_cli.commands._common.MonolynxClient`), więc grupy nie importują klasy
klienta do tworzenia instancji. Fabryka podpina odświeżanie tokenu OAuth: proaktywne przed
pierwszym żądaniem (gdy access token wygasa w ciągu doby) i reaktywne po 401.

Odpowiedź serwera i plik konfiguracyjny to granice systemu: `fetch_all` sprawdza kształt
koperty każdej strony przed użyciem, a `load_settings` zamienia uszkodzoną konfigurację na
komunikat i kod 1. Każda wartość użytkownika wstawiana do ścieżki URL przechodzi przez
`path_segment`. Błędy lokalne (brak projektu, sprzeczne flagi, brak pliku) kończą się
typer.Exit z komunikatem na stderr, bez żadnego żądania HTTP. Brak tokenu to
MonolynxAuthError (kod 3), tak jak w `auth whoami`. Błędy odpowiedzi serwera propagują do
globalnego handlera w monolynx_cli.main.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote

import typer

from monolynx_cli import oauth
from monolynx_cli.client import MonolynxAPIError, MonolynxAuthError, MonolynxClient
from monolynx_cli.config import ConfigError, Settings, resolve_settings
from monolynx_cli.options import get_options

PER_PAGE = 100
STDIN_MARKER = "-"


def _stdin_is_tty() -> bool:
    # Osobna funkcja, żeby testy mogły ją podmienić: CliRunner nie ma prawdziwego TTY.
    return sys.stdin.isatty()


def usage_error(message: str) -> typer.Exit:
    """Wypisz komunikat na stderr i zwróć typer.Exit(2) do rzucenia (błąd użycia)."""
    typer.echo(message, err=True)
    return typer.Exit(2)


def load_settings(ctx: typer.Context, project: str | None = None) -> Settings:
    opts = get_options(ctx)
    try:
        return resolve_settings({"profile": opts.profile, "project": project or opts.project})
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc


def require_project(settings: Settings) -> str:
    if not settings.project:
        typer.echo("Brak projektu: podaj --project, ustaw MONOLYNX_PROJECT albo project w profilu.", err=True)
        raise typer.Exit(2)
    return settings.project


def new_client(ctx: typer.Context, settings: Settings) -> MonolynxClient:
    """Klient API z odświeżaniem tokenu OAuth; dla tokenu statycznego i MONOLYNX_TOKEN bez odświeżania."""
    opts = get_options(ctx)
    bearer = oauth.refresh_if_expiring(settings, timeout=opts.timeout) or settings.bearer
    refresh = oauth.make_refresh_hook(settings, timeout=opts.timeout)
    return MonolynxClient(settings.endpoint, bearer, timeout=opts.timeout, debug=opts.debug, refresh=refresh)


def open_project(ctx: typer.Context) -> tuple[MonolynxClient, str]:
    """Zwróć klienta API i slug projektu z flag globalnych, zmiennych środowiskowych i profilu."""
    settings = load_settings(ctx)
    if settings.bearer is None:
        raise MonolynxAuthError(None, "Brak tokenu", "Brak zapisanego tokenu. Zaloguj się: monolynx auth login")
    slug = require_project(settings)
    path_segment(slug)  # odrzuca `..` zanim new_client wykona choćby odświeżenie tokenu
    return new_client(ctx, settings), slug


def project_path(slug: str, suffix: str = "") -> str:
    """Ścieżka endpointu API v2 w obrębie projektu, np. project_path("mon", "/tickets")."""
    return f"/api/v2/projects/{path_segment(slug)}{suffix}"


def confirm_destructive(message: str, *, yes: bool) -> None:
    if yes:
        return
    if not _stdin_is_tty():
        typer.echo("Operacja wymaga potwierdzenia: dodaj --yes.", err=True)
        raise typer.Exit(2)
    if not typer.confirm(message, default=False):
        typer.echo("Anulowano.", err=True)
        raise typer.Exit(1)


def path_segment(value: str) -> str:
    """Zakoduj wartość użytkownika jako jeden segment ścieżki URL.

    `.` i `..` (oraz pusty tekst) kodowanie zostawia bez zmian, a klient HTTP normalizuje je
    w ścieżce, więc wyszłyby poza zamierzony zasób; to błąd użycia, bez żądania HTTP.
    """
    if value in ("", ".", ".."):
        raise usage_error(f"Niedozwolony identyfikator w ścieżce: {value!r}.")
    return quote(value, safe="")


def read_text_option(value: str | None, file_path: Path | None, *, option: str, file_option: str | None = None) -> str | None:
    """Rozwiąż tekst z flagi (`-` = stdin) albo z pliku; None gdy żadne nie podane.

    Podanie obu źródeł naraz, brak pliku i pusty stdin to błąd użycia (exit 2).
    """
    if value is not None and file_path is not None:
        raise usage_error(f"Podaj {option} albo {file_option}, nie oba naraz.")
    if file_path is not None:
        try:
            return file_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            detail = exc.strerror if isinstance(exc, OSError) else "plik nie jest poprawnym UTF-8"
            raise usage_error(f"Nie można odczytać pliku {file_path}: {detail or exc}") from exc
    if value == STDIN_MARKER:
        text = sys.stdin.read()
        if not text.strip():
            raise usage_error(f"Pusty tekst na standardowym wejściu dla {option} -.")
        return text
    return value


def without_none(data: dict[str, Any]) -> dict[str, Any]:
    """Odrzuć klucze z wartością None; puste stringi i puste listy zostają (semantyka czyszczenia MCP)."""
    return {key: value for key, value in data.items() if value is not None}


def _invalid_page(detail: str) -> MonolynxAPIError:
    return MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", detail)


def fetch_all(client: MonolynxClient, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Pobierz elementy ze wszystkich stron; `params` (np. filtr statusu) idą na każdą stronę."""
    items: list[dict[str, Any]] = []
    page = 1
    total_pages = 1
    while page <= total_pages:
        payload = client.get(path, params={**(params or {}), "page": page, "per_page": PER_PAGE})
        if not isinstance(payload, dict):
            raise _invalid_page("Odpowiedź nie jest obiektem JSON.")
        page_items = payload.get("items")
        if not isinstance(page_items, list) or not all(isinstance(item, dict) for item in page_items):
            raise _invalid_page("Pole items nie jest listą obiektów.")
        pages = payload.get("total_pages")
        if isinstance(pages, bool) or not isinstance(pages, int) or pages < 1:
            raise _invalid_page("Pole total_pages nie jest liczbą całkowitą >= 1.")
        items.extend(page_items)
        total_pages = pages
        page += 1
    return items
