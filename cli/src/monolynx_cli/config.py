"""Konfiguracja lokalna CLI: plik TOML, profile, precedencja ustawień, maskowanie tokenów.

Plik konfiguracyjny leży pod platformdirs.user_config_dir("monolynx") / "config.toml".
Struktura: [general] active_profile, [profile.<nazwa>] endpoint/project/auth_type/
token/access_token/refresh_token/expires_at/client_id. Pola access_token/refresh_token/
expires_at/client_id to na razie tylko schemat (odczyt i zapis) -- sam przepływ logowania
OAuth to zakres innego ticketu (MON-143). Uszkodzony plik (zły TOML) albo sekcja
general/profile/profile.<nazwa> o nieprawidłowej strukturze zgłasza się przez ConfigError --
komendy auth i config łapią ten wyjątek i kończą się komunikatem na stderr, bez zapisu.
"""

from __future__ import annotations

import dataclasses
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import platformdirs
import tomli_w

if sys.version_info >= (3, 11):  # noqa: UP036 -- cli wspiera Python 3.10, gdzie stdlib nie ma tomllib
    import tomllib
else:
    import tomli as tomllib

DEFAULT_ENDPOINT = "https://monolynx.com"
DEFAULT_PROFILE = "default"

SECRET_FIELDS = ("token", "access_token", "refresh_token")
LOGOUT_CLEARED_FIELDS = ("token", "access_token", "refresh_token", "expires_at")
PROFILE_FIELDS = (
    "endpoint",
    "project",
    "auth_type",
    "token",
    "access_token",
    "refresh_token",
    "expires_at",
    "client_id",
)


class ConfigError(Exception):
    """Plik konfiguracyjny jest uszkodzony albo ma nieprawidłową strukturę."""


def config_path() -> Path:
    return Path(platformdirs.user_config_dir("monolynx")) / "config.toml"


def load_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}

    with path.open("rb") as fh:
        try:
            data = tomllib.load(fh)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"Plik konfiguracyjny {path} jest uszkodzony: {exc}") from exc

    general = data.get("general")
    if general is not None and not isinstance(general, dict):
        raise ConfigError(f"Sekcja general w pliku {path} ma nieprawidłową strukturę (oczekiwano tabeli).")

    profiles = data.get("profile")
    if profiles is not None:
        if not isinstance(profiles, dict):
            raise ConfigError(f"Sekcja profile w pliku {path} ma nieprawidłową strukturę (oczekiwano tabeli).")
        for name, profile in profiles.items():
            if not isinstance(profile, dict):
                raise ConfigError(f"Sekcja profile.{name} w pliku {path} ma nieprawidłową strukturę (oczekiwano tabeli).")

    return data


def _is_windows() -> bool:
    return os.name == "nt"


def save_config(data: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not _is_windows():
        os.chmod(path.parent, 0o700)

    tmp_path = path.parent / f".{path.name}.tmp"
    if _is_windows():
        with tmp_path.open("wb") as fh:
            tomli_w.dump(data, fh)
    else:
        # os.open z trybem 0o600 tworzy plik od razu z wąskimi prawami -- bez tego
        # okna między utworzeniem (prawa z umask, zwykle 0644) a chmod token byłby
        # przez chwilę czytelny dla innych użytkowników. chmod zostaje poniżej jako
        # gwarancja, gdyby tmp_path przetrwał z poprzedniego (przerwanego) zapisu --
        # O_CREAT nie zmienia trybu pliku, który już istnieje.
        fd = os.open(tmp_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as fh:
            tomli_w.dump(data, fh)
        os.chmod(tmp_path, 0o600)
    os.replace(tmp_path, path)


def get_profile(name: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
    if data is None:
        data = load_config()
    return dict(data.get("profile", {}).get(name, {}))


@dataclasses.dataclass(frozen=True)
class Settings:
    endpoint: str
    project: str | None
    profile: str
    auth_type: str | None
    bearer: str | None


def resolve_settings(cli_flags: Mapping[str, str | None] | None = None) -> Settings:
    flags = cli_flags or {}
    data = load_config()

    profile_name = flags.get("profile") or os.environ.get("MONOLYNX_PROFILE") or data.get("general", {}).get("active_profile") or DEFAULT_PROFILE
    profile = get_profile(profile_name, data)

    endpoint = flags.get("endpoint") or os.environ.get("MONOLYNX_ENDPOINT") or profile.get("endpoint") or DEFAULT_ENDPOINT
    endpoint = endpoint.rstrip("/")

    project = flags.get("project") or os.environ.get("MONOLYNX_PROJECT") or profile.get("project")

    auth_type: str | None
    bearer: str | None
    token_flag = flags.get("token")
    env_token = os.environ.get("MONOLYNX_TOKEN")
    if token_flag:
        auth_type, bearer = "token", token_flag
    elif env_token:
        auth_type, bearer = "token", env_token
    elif profile.get("auth_type") == "oauth":
        auth_type, bearer = "oauth", profile.get("access_token")
    elif profile.get("auth_type") == "token" or profile.get("token"):
        auth_type, bearer = "token", profile.get("token")
    else:
        auth_type, bearer = None, None

    return Settings(endpoint=endpoint, project=project, profile=profile_name, auth_type=auth_type, bearer=bearer)


def mask_token(raw: str) -> str:
    if len(raw) < 8:
        return "****"
    if raw.startswith("osk_"):
        return "osk_****" + raw[-4:]
    return "****" + raw[-4:]
