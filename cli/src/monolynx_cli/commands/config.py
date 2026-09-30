"""Komendy monolynx_cli.commands.config: get/set/list nad plikiem config.toml.

Klucze kropkowe: "general.active_profile" albo "profile.<nazwa>.<pole>" gdzie <pole>
należy do config.PROFILE_FIELDS. Sekrety (config.SECRET_FIELDS) są zawsze maskowane
przy wypisywaniu, nigdy nie trafiają na stdout w pełnej postaci. Uszkodzony plik (zły
TOML albo zła struktura sekcji) zgłasza się przez config.ConfigError -> stderr i kod
wyjścia 1, bez zapisu.

get i list wypisują wynik przez output.emit() (konwencja modułu monolynx_cli.output),
nie przez typer.echo -- format wybiera globalna flaga --output/-o (domyślnie table w
terminalu, json poza nim). set nie zwraca danych do wyświetlenia, tylko potwierdzenie
zapisu, które idzie na stderr przez output.echo_stderr (wycisza je --quiet); stdout zostaje pusty.
"""

from __future__ import annotations

from typing import Any

import typer

from monolynx_cli.config import PROFILE_FIELDS, SECRET_FIELDS, ConfigError, load_config, mask_token, save_config
from monolynx_cli.options import get_options
from monolynx_cli.output import echo_stderr, emit

app = typer.Typer(no_args_is_help=True)

_VALID_AUTH_TYPES = ("token", "oauth")


def _split_key(key: str) -> tuple[str, ...]:
    return tuple(key.split("."))


@app.command("get")
def get(ctx: typer.Context, key: str) -> None:
    """Wypisz wartość pod kluczem kropkowym (sekrety zamaskowane)."""
    parts = _split_key(key)

    try:
        data = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if parts[0] == "general":
        if len(parts) != 2 or parts[1] != "active_profile":
            typer.echo(f"Nieprawidłowy klucz: {key}", err=True)
            raise typer.Exit(2)
        value = data.get("general", {}).get("active_profile")
    elif parts[0] == "profile":
        if len(parts) != 3 or parts[2] not in PROFILE_FIELDS:
            typer.echo(f"Nieprawidłowy klucz: {key}", err=True)
            raise typer.Exit(2)
        _, profile_name, field = parts
        value = data.get("profile", {}).get(profile_name, {}).get(field)
        if value is not None and field in SECRET_FIELDS:
            value = mask_token(str(value))
    else:
        typer.echo(f"Nieprawidłowy klucz: {key}", err=True)
        raise typer.Exit(2)

    if value is None:
        typer.echo(f"Brak wartości dla klucza: {key}", err=True)
        raise typer.Exit(1)

    emit(ctx, {"key": key, "value": value})


@app.command("set")
def set_value(ctx: typer.Context, key: str, value: str) -> None:
    """Ustaw wartość pod kluczem kropkowym."""
    parts = _split_key(key)

    try:
        data = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    if parts[0] == "general":
        if len(parts) != 2 or parts[1] != "active_profile":
            typer.echo(f"Nieprawidłowy klucz: {key}", err=True)
            raise typer.Exit(2)
        data.setdefault("general", {})["active_profile"] = value
    elif parts[0] == "profile":
        if len(parts) != 3 or parts[2] not in PROFILE_FIELDS:
            typer.echo(f"Nieprawidłowy klucz: {key}", err=True)
            raise typer.Exit(2)
        if parts[2] == "auth_type" and value not in _VALID_AUTH_TYPES:
            typer.echo(f"Nieprawidłowa wartość auth_type: {value} (dozwolone: token, oauth)", err=True)
            raise typer.Exit(2)
        _, profile_name, field = parts
        data.setdefault("profile", {}).setdefault(profile_name, {})[field] = value
    else:
        typer.echo(f"Nieprawidłowy klucz: {key}", err=True)
        raise typer.Exit(2)

    save_config(data)
    opts = get_options(ctx)
    echo_stderr(f"Ustawiono {key}", quiet=opts.quiet, no_color=opts.no_color)


@app.command("list")
def list_all(ctx: typer.Context) -> None:
    """Wypisz całą konfigurację jako listę wierszy {klucz, wartość} (sekrety zamaskowane)."""
    try:
        data = load_config()
    except ConfigError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(1) from exc

    rows: list[dict[str, Any]] = []

    active_profile = data.get("general", {}).get("active_profile")
    if active_profile is not None:
        rows.append({"key": "general.active_profile", "value": active_profile})

    for profile_name in sorted(data.get("profile", {})):
        profile = data["profile"][profile_name]
        for field in PROFILE_FIELDS:
            if field not in profile:
                continue
            value = profile[field]
            if field in SECRET_FIELDS:
                value = mask_token(str(value))
            rows.append({"key": f"profile.{profile_name}.{field}", "value": value})

    emit(ctx, rows)
