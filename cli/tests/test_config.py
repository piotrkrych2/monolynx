"""Testy modulu monolynx_cli.config: load/save/get_profile/resolve_settings/mask_token.

Specyfikacja dla MON-125 -- modul monolynx_cli.config jeszcze nie istnieje w chwili
pisania tych testow (TDD, red phase). Kontrakt: patrz interface.md ticketa MON-125
(sekcja 1).
"""

from __future__ import annotations

import dataclasses
import os
import stat

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# config_path
# ---------------------------------------------------------------------------


def test_config_path_is_config_toml_under_patched_platformdirs(tmp_path):
    from monolynx_cli.config import config_path

    path = config_path()

    assert path.is_relative_to(tmp_path)
    assert path.name == "config.toml"


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------


def test_load_config_returns_empty_dict_when_file_missing():
    from monolynx_cli.config import load_config

    assert load_config() == {}


def test_load_config_round_trips_saved_data():
    from monolynx_cli.config import load_config, save_config

    data = {
        "general": {"active_profile": "praca"},
        "profile": {
            "praca": {"endpoint": "https://praca.test", "project": "monolynx"},
            "default": {"endpoint": "https://monolynx.com"},
        },
    }

    save_config(data)

    assert load_config() == data


# ---------------------------------------------------------------------------
# load_config: ConfigError na uszkodzonym pliku
# ---------------------------------------------------------------------------


def _write_raw_config(text: str):
    from monolynx_cli.config import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def test_config_error_is_an_exception():
    from monolynx_cli.config import ConfigError

    assert issubclass(ConfigError, Exception)


def test_load_config_raises_config_error_on_invalid_toml():
    from monolynx_cli.config import ConfigError, load_config

    path = _write_raw_config("to nie jest [poprawny toml")

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert str(path) in str(exc_info.value)


def test_load_config_raises_config_error_when_general_section_is_not_a_table():
    from monolynx_cli.config import ConfigError, load_config

    path = _write_raw_config('general = "nie-tablica"\n')

    with pytest.raises(ConfigError) as exc_info:
        load_config()

    assert str(path) in str(exc_info.value)


def test_load_config_raises_config_error_when_profile_section_is_not_a_table():
    from monolynx_cli.config import ConfigError, load_config

    _write_raw_config('profile = "nie-tablica"\n')

    with pytest.raises(ConfigError):
        load_config()


def test_load_config_raises_config_error_when_single_profile_entry_is_not_a_table():
    from monolynx_cli.config import ConfigError, load_config

    _write_raw_config('[profile]\ndefault = "nie-tablica"\n')

    with pytest.raises(ConfigError):
        load_config()


# ---------------------------------------------------------------------------
# save_config: zapis atomowy i uprawnienia
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="uprawnienia POSIX nie maja sensu na Windows")
def test_save_config_sets_file_permissions_0600():
    from monolynx_cli.config import config_path, save_config

    save_config({"general": {"active_profile": "default"}})

    mode = stat.S_IMODE(config_path().stat().st_mode)
    assert mode == 0o600


@pytest.mark.skipif(os.name == "nt", reason="uprawnienia POSIX nie maja sensu na Windows")
def test_save_config_sets_directory_permissions_0700():
    from monolynx_cli.config import config_path, save_config

    save_config({"general": {"active_profile": "default"}})

    mode = stat.S_IMODE(config_path().parent.stat().st_mode)
    assert mode == 0o700


def test_save_config_on_windows_skips_chmod_without_error(monkeypatch):
    # os.name globalnie zmienia klase pathlib (WindowsPath vs PosixPath) i wywala sie
    # na hoscie POSIX -- patchujemy tylko wlasny hook config.py, nie system operacyjny.
    import monolynx_cli.config as config_mod

    monkeypatch.setattr(config_mod, "_is_windows", lambda: True)

    def _boom(*args, **kwargs):
        raise AssertionError("os.chmod nie powinien byc wolany na Windows")

    monkeypatch.setattr(os, "chmod", _boom)

    config_mod.save_config({"general": {"active_profile": "default"}})

    assert config_mod.load_config() == {"general": {"active_profile": "default"}}


def test_save_config_leaves_no_leftover_temp_files():
    from monolynx_cli.config import config_path, save_config

    save_config({"general": {"active_profile": "default"}})

    entries = {p.name for p in config_path().parent.iterdir()}
    assert entries == {"config.toml"}


def test_save_config_leaves_original_file_untouched_when_os_replace_fails(monkeypatch):
    # Zabija mutanta "zapis wprost do path przez os.open, bez pliku tymczasowego i
    # os.replace": bez prawdziwej atomowosci ten test albo nie zlapie OSError (bo
    # os.replace nigdy nie jest wolany), albo stary plik jest juz nadpisany zanim
    # padnie wyjatek.
    from monolynx_cli.config import config_path, save_config

    save_config({"general": {"active_profile": "oryginalny"}})
    before = config_path().read_bytes()

    def _boom(*args, **kwargs):
        raise OSError("symulowany blad zapisu")

    monkeypatch.setattr(os, "replace", _boom)

    with pytest.raises(OSError):
        save_config({"general": {"active_profile": "nowy"}})

    assert config_path().read_bytes() == before


def test_save_config_writes_all_profiles_from_given_data():
    from monolynx_cli.config import load_config, save_config

    save_config(
        {
            "profile": {
                "default": {"endpoint": "https://a.test"},
                "praca": {"endpoint": "https://b.test"},
            }
        }
    )

    reloaded = load_config()
    assert reloaded["profile"]["default"]["endpoint"] == "https://a.test"
    assert reloaded["profile"]["praca"]["endpoint"] == "https://b.test"


# ---------------------------------------------------------------------------
# get_profile
# ---------------------------------------------------------------------------


def test_get_profile_returns_section_from_explicit_data():
    from monolynx_cli.config import get_profile

    data = {"profile": {"default": {"endpoint": "https://x.test"}}}

    assert get_profile("default", data) == {"endpoint": "https://x.test"}


def test_get_profile_missing_profile_returns_empty_dict():
    from monolynx_cli.config import get_profile

    data = {"profile": {"default": {"endpoint": "https://x.test"}}}

    assert get_profile("nieznany", data) == {}


def test_get_profile_returns_copy_not_reference():
    from monolynx_cli.config import get_profile

    data = {"profile": {"default": {"endpoint": "https://x.test"}}}

    result = get_profile("default", data)
    result["endpoint"] = "zmutowane"

    assert data["profile"]["default"]["endpoint"] == "https://x.test"


def test_get_profile_with_none_data_reads_from_disk():
    from monolynx_cli.config import get_profile, save_config

    save_config({"profile": {"default": {"project": "monolynx"}}})

    assert get_profile("default") == {"project": "monolynx"}


def test_get_profile_no_file_and_no_data_returns_empty_dict():
    from monolynx_cli.config import get_profile

    assert get_profile("default") == {}


# ---------------------------------------------------------------------------
# resolve_settings: kolejnosc pierwszenstwa profilu
# ---------------------------------------------------------------------------


def test_resolve_settings_defaults_when_nothing_configured():
    from monolynx_cli.config import DEFAULT_ENDPOINT, DEFAULT_PROFILE, Settings, resolve_settings

    settings = resolve_settings()

    assert settings == Settings(
        endpoint=DEFAULT_ENDPOINT,
        project=None,
        profile=DEFAULT_PROFILE,
        auth_type=None,
        bearer=None,
    )


def test_resolve_settings_profile_flag_wins_over_env_and_config(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"general": {"active_profile": "from-config"}})
    monkeypatch.setenv("MONOLYNX_PROFILE", "from-env")

    settings = resolve_settings({"profile": "from-flag"})

    assert settings.profile == "from-flag"


def test_resolve_settings_profile_env_wins_over_config(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"general": {"active_profile": "from-config"}})
    monkeypatch.setenv("MONOLYNX_PROFILE", "from-env")

    settings = resolve_settings({"profile": None})

    assert settings.profile == "from-env"


def test_resolve_settings_profile_config_wins_over_default():
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"general": {"active_profile": "from-config"}})

    settings = resolve_settings()

    assert settings.profile == "from-config"


# ---------------------------------------------------------------------------
# resolve_settings: kolejnosc pierwszenstwa endpointu
# ---------------------------------------------------------------------------


def test_resolve_settings_endpoint_flag_wins_over_env_and_profile(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"endpoint": "https://profile-ep.test"}}})
    monkeypatch.setenv("MONOLYNX_ENDPOINT", "https://env-ep.test")

    settings = resolve_settings({"profile": "p", "endpoint": "https://flag-ep.test/"})

    assert settings.endpoint == "https://flag-ep.test"


def test_resolve_settings_endpoint_env_wins_over_profile(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"endpoint": "https://profile-ep.test"}}})
    monkeypatch.setenv("MONOLYNX_ENDPOINT", "https://env-ep.test/")

    settings = resolve_settings({"profile": "p"})

    assert settings.endpoint == "https://env-ep.test"


def test_resolve_settings_endpoint_profile_wins_over_default():
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"endpoint": "https://profile-ep.test///"}}})

    settings = resolve_settings({"profile": "p"})

    assert settings.endpoint == "https://profile-ep.test"


def test_resolve_settings_endpoint_default_when_nothing():
    from monolynx_cli.config import DEFAULT_ENDPOINT, resolve_settings

    settings = resolve_settings({"profile": "brak-profilu"})

    assert settings.endpoint == DEFAULT_ENDPOINT


# ---------------------------------------------------------------------------
# resolve_settings: kolejnosc pierwszenstwa project
# ---------------------------------------------------------------------------


def test_resolve_settings_project_flag_wins_over_env_and_profile(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"project": "profile-project"}}})
    monkeypatch.setenv("MONOLYNX_PROJECT", "env-project")

    settings = resolve_settings({"profile": "p", "project": "flag-project"})

    assert settings.project == "flag-project"


def test_resolve_settings_project_env_wins_over_profile(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"project": "profile-project"}}})
    monkeypatch.setenv("MONOLYNX_PROJECT", "env-project")

    settings = resolve_settings({"profile": "p"})

    assert settings.project == "env-project"


def test_resolve_settings_project_profile_wins_over_default():
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"project": "profile-project"}}})

    settings = resolve_settings({"profile": "p"})

    assert settings.project == "profile-project"


def test_resolve_settings_project_none_when_nothing():
    from monolynx_cli.config import resolve_settings

    settings = resolve_settings({"profile": "brak-profilu"})

    assert settings.project is None


# ---------------------------------------------------------------------------
# resolve_settings: token/bearer/auth_type
# ---------------------------------------------------------------------------


def test_resolve_settings_token_flag_wins_over_env_and_profile(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"auth_type": "oauth", "access_token": "profile-access-token"}}})
    monkeypatch.setenv("MONOLYNX_TOKEN", "env-token")

    settings = resolve_settings({"profile": "p", "token": "flag-token"})

    assert settings.auth_type == "token"
    assert settings.bearer == "flag-token"


def test_resolve_settings_token_env_wins_over_profile(monkeypatch):
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"auth_type": "oauth", "access_token": "profile-access-token"}}})
    monkeypatch.setenv("MONOLYNX_TOKEN", "env-token")

    settings = resolve_settings({"profile": "p"})

    assert settings.auth_type == "token"
    assert settings.bearer == "env-token"


def test_resolve_settings_token_profile_oauth_uses_access_token():
    from monolynx_cli.config import resolve_settings, save_config

    save_config(
        {
            "profile": {
                "p": {
                    "auth_type": "oauth",
                    "access_token": "oauth-access-xyz",
                    "token": "legacy-token-nieuzywany",
                }
            }
        }
    )

    settings = resolve_settings({"profile": "p"})

    assert settings.auth_type == "oauth"
    assert settings.bearer == "oauth-access-xyz"


def test_resolve_settings_token_profile_token_type_uses_token_field():
    from monolynx_cli.config import resolve_settings, save_config

    save_config(
        {
            "profile": {
                "p": {
                    "auth_type": "token",
                    "token": "plain-token-abc",
                    "access_token": "nieuzywany-oauth-token",
                }
            }
        }
    )

    settings = resolve_settings({"profile": "p"})

    assert settings.auth_type == "token"
    assert settings.bearer == "plain-token-abc"


def test_resolve_settings_token_profile_without_auth_type_defaults_to_token():
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"token": "legacy-token-bez-typu"}}})

    settings = resolve_settings({"profile": "p"})

    assert settings.auth_type == "token"
    assert settings.bearer == "legacy-token-bez-typu"


def test_resolve_settings_token_none_when_profile_has_nothing_relevant():
    from monolynx_cli.config import resolve_settings, save_config

    save_config({"profile": {"p": {"endpoint": "https://x.test"}}})

    settings = resolve_settings({"profile": "p"})

    assert settings.auth_type is None
    assert settings.bearer is None


def test_settings_is_frozen_dataclass():
    from monolynx_cli.config import resolve_settings

    settings = resolve_settings()

    with pytest.raises(dataclasses.FrozenInstanceError):
        settings.endpoint = "https://zmieniony.test"


# ---------------------------------------------------------------------------
# mask_token
# ---------------------------------------------------------------------------


def test_mask_token_osk_prefix_shows_osk_stars_and_last_four():
    from monolynx_cli.config import mask_token

    raw = "osk_" + "1" * 12

    assert mask_token(raw) == "osk_****1111"


def test_mask_token_without_prefix_shows_stars_and_last_four():
    from monolynx_cli.config import mask_token

    assert mask_token("plaintoken12345") == "****2345"


def test_mask_token_exactly_8_chars_is_not_plain_stars():
    from monolynx_cli.config import mask_token

    result = mask_token("abcdefgh")

    assert result == "****efgh"
    assert result != "****"


def test_mask_token_7_chars_returns_plain_stars():
    from monolynx_cli.config import mask_token

    assert mask_token("abcdefg") == "****"


def test_mask_token_short_osk_prefixed_returns_plain_stars():
    from monolynx_cli.config import mask_token

    assert mask_token("osk_abc") == "****"
