"""Komendy monolynx_cli.commands.auth: login/logout/whoami.

Login domyślnie robi OAuth 2.1 w przeglądarce (monolynx_cli.oauth), z --token zapisuje
token API bez przeglądarki. Po zalogowaniu i we whoami komenda bije w GET /api/v2/me
backendu Monolynx (autoryzacja Bearer) przez monolynx_cli.client.MonolynxClient -- nazwę
modułową `MonolynxClient` podmieniają testy przez monkeypatch; dla profilu oauth klient
dostaje hook odświeżania tokenu po 401 (oauth.make_refresh_hook). Błędy HTTP i sieci to wyjątki klienta (MonolynxError), które
propaguje do globalnego handlera w monolynx_cli.main (kody wyjścia 1/3/4), więc komendy
nie mają własnych typer.Exit dla odpowiedzi serwera.
Token nigdy nie trafia na stdout/stderr w pełnej postaci, tylko przez config.mask_token.
Plik konfiguracyjny może być ręcznie edytowany przez człowieka, a odpowiedź serwera
może nie być tym, czego oczekujemy (zły --endpoint, HTML zamiast JSON) -- obie granice
są sprawdzane przed użyciem, nigdy nie kończą się nieobsłużonym wyjątkiem.

Lokalna flaga `--profile` każdej komendy wygrywa z globalną `--profile` z roota
(monolynx_cli.main), globalna wygrywa z MONOLYNX_PROFILE/MONOLYNX_PROJECT -- kolejność
pierwszeństwa poza tym bez zmian, patrz config.resolve_settings. whoami wypisuje wynik
przez output.emit() (konwencja modułu monolynx_cli.output), nie przez typer.echo.
Komunikaty stanu (login, logout, brak profilu) idą na stderr przez output.echo_stderr i
są wyciszane przez --quiet; stdout zostaje czysty dla potoków.
"""

from __future__ import annotations

from collections.abc import Callable

import typer

from monolynx_cli import oauth
from monolynx_cli.client import MonolynxAPIError, MonolynxAuthError, MonolynxClient
from monolynx_cli.config import (
    DEFAULT_PROFILE,
    LOGOUT_CLEARED_FIELDS,
    ConfigError,
    get_profile,
    load_config,
    mask_token,
    resolve_settings,
    save_config,
)
from monolynx_cli.options import GlobalOptions, get_options
from monolynx_cli.output import echo_stderr, emit

app = typer.Typer(no_args_is_help=True)


def _fetch_email(endpoint: str, token: str, opts: GlobalOptions, refresh: Callable[[], str | None] | None = None) -> str:
    with MonolynxClient(endpoint, token, timeout=opts.timeout, debug=opts.debug, refresh=refresh) as client:
        payload = client.get("/api/v2/me")
    email = payload.get("email") if isinstance(payload, dict) else None
    if not isinstance(email, str):
        raise MonolynxAPIError(200, "Nieprawidłowa odpowiedź serwera", "Odpowiedź nie zawiera pola email.")
    return email


def _save_token_profile(profile: str, endpoint: str, token: str) -> None:
    data = load_config()
    profiles = data.setdefault("profile", {})
    target = dict(profiles.get(profile, {}))
    target["endpoint"] = endpoint
    target["auth_type"] = "token"
    target["token"] = token
    profiles[profile] = target
    data.setdefault("general", {}).setdefault("active_profile", DEFAULT_PROFILE)
    save_config(data)


@app.command("login")
def login(
    ctx: typer.Context,
    endpoint: str | None = typer.Option(None, "--endpoint", help="Adres API Monolynx."),
    token: str | None = typer.Option(None, "--token", help="Token API zamiast logowania w przeglądarce (bez OAuth)."),
    profile: str | None = typer.Option(None, "--profile", help="Nazwa profilu docelowego."),
    no_browser: bool = typer.Option(False, "--no-browser", help="Nie otwieraj przeglądarki, wypisz adres autoryzacji na stderr."),
) -> None:
    """Zaloguj się przez OAuth w przeglądarce (albo tokenem API z --token) i zapisz sesję w profilu konfiguracji."""
    opts = get_options(ctx)
    try:
        settings = resolve_settings({"endpoint": endpoint, "profile": profile or opts.profile, "project": opts.project})
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    tokens: oauth.TokenSet | None = None
    if token is None:
        tokens = oauth.run_login(settings.endpoint, no_browser=no_browser, timeout=opts.timeout, quiet=opts.quiet, no_color=opts.no_color)
        email = _fetch_email(settings.endpoint, tokens.access_token, opts)
    else:
        email = _fetch_email(settings.endpoint, token, opts)

    try:
        if tokens is not None:
            oauth.save_tokens(settings.profile, settings.endpoint, tokens)
        else:
            _save_token_profile(settings.profile, settings.endpoint, str(token))
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    echo_stderr(f"Zalogowano jako {email} (profil {settings.profile})", quiet=opts.quiet, no_color=opts.no_color)


@app.command("logout")
def logout(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", help="Nazwa profilu do wylogowania."),
) -> None:
    """Wyczyść zapisane sekrety (token/access_token/refresh_token/expires_at) profilu."""
    opts = get_options(ctx)
    try:
        settings = resolve_settings({"profile": profile or opts.profile, "project": opts.project})
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    try:
        data = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    profiles = data.get("profile", {})
    if settings.profile not in profiles:
        echo_stderr(f"Profil {settings.profile} nie istnieje.", quiet=opts.quiet, no_color=opts.no_color)
        return

    target = dict(profiles[settings.profile])
    for field in LOGOUT_CLEARED_FIELDS:
        target.pop(field, None)
    profiles[settings.profile] = target
    save_config(data)

    echo_stderr(f"Wylogowano z profilu {settings.profile}", quiet=opts.quiet, no_color=opts.no_color)


@app.command("whoami")
def whoami(
    ctx: typer.Context,
    profile: str | None = typer.Option(None, "--profile", help="Nazwa profilu do sprawdzenia."),
) -> None:
    """Wyświetl dane zalogowanego użytkownika."""
    opts = get_options(ctx)
    try:
        settings = resolve_settings({"profile": profile or opts.profile, "project": opts.project})
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if settings.bearer is None:
        raise MonolynxAuthError(None, "Brak tokenu", "Brak zapisanego tokenu. Zaloguj się: monolynx auth login")

    hook = oauth.make_refresh_hook(settings, timeout=opts.timeout)
    email = _fetch_email(settings.endpoint, str(settings.bearer), opts, refresh=hook)

    result: dict[str, str | None] = {"email": email, "profile": settings.profile, "auth_type": settings.auth_type}
    if settings.auth_type != "oauth":
        result["token"] = mask_token(str(settings.bearer))
        emit(ctx, result)
        return

    # Po odświeżeniu w profilu leży nowy access token i nowy expires_at, a settings trzyma stary.
    try:
        stored = get_profile(settings.profile)
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc
    result["token"] = mask_token(str(stored.get("access_token") or settings.bearer))
    result["expires_at"] = stored.get("expires_at")
    emit(ctx, result)
