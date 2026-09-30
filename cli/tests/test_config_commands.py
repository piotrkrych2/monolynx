"""Testy komend monolynx_cli.commands.config: get/set/list.

Specyfikacja dla MON-125 -- pakiet monolynx_cli.commands jeszcze nie istnieje w
chwili pisania tych testow (TDD, red phase). Kontrakt: patrz interface.md ticketa
MON-125 (sekcja 2, bullety config get/set/list).
"""

from __future__ import annotations

import functools
import json

import httpx
import pytest
from monolynx_cli import client as client_mod
from monolynx_cli import config as config_mod
from monolynx_cli.main import app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# config get
# ---------------------------------------------------------------------------


def test_get_plain_field_prints_value(runner):
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})

    result = runner.invoke(app, ["config", "get", "profile.default.endpoint"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"key": "profile.default.endpoint", "value": "https://x.test"}


def test_get_general_active_profile(runner):
    config_mod.save_config({"general": {"active_profile": "praca"}})

    result = runner.invoke(app, ["config", "get", "general.active_profile"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"key": "general.active_profile", "value": "praca"}


def test_get_secret_field_is_masked(runner):
    token = "osk_" + "9" * 20
    config_mod.save_config({"profile": {"default": {"token": token}}})

    result = runner.invoke(app, ["config", "get", "profile.default.token"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"key": "profile.default.token", "value": config_mod.mask_token(token)}
    assert token not in result.output


def test_get_non_str_secret_value_masks_via_str_conversion(runner):
    config_mod.save_config({"profile": {"default": {"token": 1234567890123456}}})

    result = runner.invoke(app, ["config", "get", "profile.default.token"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "key": "profile.default.token",
        "value": config_mod.mask_token("1234567890123456"),
    }
    assert "1234567890123456" not in result.stdout


def test_get_missing_value_exits_1(runner):
    result = runner.invoke(app, ["config", "get", "profile.default.endpoint"])

    assert result.exit_code == 1
    assert result.stderr.strip()
    assert result.stdout.strip() == ""


def test_get_invalid_key_without_dot_exits_2(runner):
    result = runner.invoke(app, ["config", "get", "general"])

    assert result.exit_code == 2
    assert result.stderr.strip()


def test_get_invalid_general_field_exits_2(runner):
    result = runner.invoke(app, ["config", "get", "general.nieznane"])

    assert result.exit_code == 2


def test_get_invalid_profile_field_exits_2(runner):
    result = runner.invoke(app, ["config", "get", "profile.default.nieznane"])

    assert result.exit_code == 2


def test_get_profile_key_without_field_exits_2(runner):
    result = runner.invoke(app, ["config", "get", "profile.default"])

    assert result.exit_code == 2


def test_get_corrupted_config_exits_1(runner):
    from monolynx_cli.config import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("to nie jest [poprawny toml", encoding="utf-8")

    result = runner.invoke(app, ["config", "get", "general.active_profile"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(path) in result.stderr


# ---------------------------------------------------------------------------
# config set
# ---------------------------------------------------------------------------


def test_set_plain_field_then_get_roundtrips(runner):
    result = runner.invoke(app, ["config", "set", "profile.default.endpoint", "https://custom.test"])

    assert result.exit_code == 0, result.output
    assert config_mod.load_config()["profile"]["default"]["endpoint"] == "https://custom.test"

    result = runner.invoke(app, ["config", "get", "profile.default.endpoint"])
    assert json.loads(result.stdout) == {"key": "profile.default.endpoint", "value": "https://custom.test"}


def test_set_confirmation_goes_to_stderr_not_stdout(runner):
    # MON-228 D2: "Ustawiono ..." to komunikat stanu, stdout zostaje pusty dla potoków.
    result = runner.invoke(app, ["config", "set", "profile.default.endpoint", "https://custom.test"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert result.stderr.strip() == "Ustawiono profile.default.endpoint"


def test_set_quiet_suppresses_confirmation_but_still_writes(runner):
    result = runner.invoke(app, ["--quiet", "config", "set", "profile.default.endpoint", "https://custom.test"])

    assert result.exit_code == 0, result.output
    assert result.output == ""
    assert config_mod.load_config()["profile"]["default"]["endpoint"] == "https://custom.test"


def test_set_general_active_profile(runner):
    result = runner.invoke(app, ["config", "set", "general.active_profile", "praca"])

    assert result.exit_code == 0, result.output
    assert config_mod.load_config()["general"]["active_profile"] == "praca"


def test_set_secret_field_hides_value_in_stdout_and_masks_on_get(runner):
    token = "osk_" + "5" * 20

    result = runner.invoke(app, ["config", "set", "profile.default.token", token])

    assert result.exit_code == 0, result.output
    # result.output = stdout+stderr polaczone -- silniejszy test nieobecnosci sekretu
    # niz sprawdzanie kazdego strumienia z osobna.
    assert token not in result.output
    assert config_mod.load_config()["profile"]["default"]["token"] == token

    result = runner.invoke(app, ["config", "get", "profile.default.token"])
    assert json.loads(result.stdout) == {"key": "profile.default.token", "value": config_mod.mask_token(token)}


@pytest.mark.parametrize("value", ["token", "oauth"])
def test_set_auth_type_accepts_valid_values(runner, value):
    result = runner.invoke(app, ["config", "set", "profile.default.auth_type", value])

    assert result.exit_code == 0, result.output
    assert config_mod.load_config()["profile"]["default"]["auth_type"] == value


def test_set_auth_type_rejects_invalid_value(runner):
    result = runner.invoke(app, ["config", "set", "profile.default.auth_type", "cos-dziwnego"])

    assert result.exit_code == 2
    assert result.stderr.strip()
    assert config_mod.load_config() == {}


def test_set_invalid_key_exits_2_without_writing(runner):
    result = runner.invoke(app, ["config", "set", "general.nieznane", "x"])

    assert result.exit_code == 2
    assert result.stderr.strip()
    assert config_mod.load_config() == {}


def test_set_profile_field_not_in_profile_fields_exits_2(runner):
    result = runner.invoke(app, ["config", "set", "profile.default.nieznane", "x"])

    assert result.exit_code == 2
    assert config_mod.load_config() == {}


def test_set_does_not_overwrite_other_profiles(runner):
    config_mod.save_config({"profile": {"praca": {"endpoint": "https://praca.test"}}})

    result = runner.invoke(app, ["config", "set", "profile.default.project", "monolynx"])

    assert result.exit_code == 0, result.output
    data = config_mod.load_config()
    assert data["profile"]["praca"] == {"endpoint": "https://praca.test"}
    assert data["profile"]["default"]["project"] == "monolynx"


def test_set_does_not_overwrite_other_fields_of_same_profile(runner):
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test", "project": "monolynx"}}})

    result = runner.invoke(app, ["config", "set", "profile.default.project", "inny"])

    assert result.exit_code == 0, result.output
    data = config_mod.load_config()
    assert data["profile"]["default"]["endpoint"] == "https://x.test"
    assert data["profile"]["default"]["project"] == "inny"


def test_set_corrupted_config_exits_1_without_write(runner):
    from monolynx_cli.config import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("to nie jest [poprawny toml", encoding="utf-8")
    before = path.read_bytes()

    result = runner.invoke(app, ["config", "set", "profile.default.endpoint", "https://custom.test"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(path) in result.stderr
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# config list
# ---------------------------------------------------------------------------


def test_list_returns_json_rows_with_key_and_value(runner):
    config_mod.save_config(
        {
            "general": {"active_profile": "default"},
            "profile": {"default": {"endpoint": "https://monolynx.com"}},
        }
    )

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    active_profile_row = {"key": "general.active_profile", "value": "default"}
    endpoint_row = {"key": "profile.default.endpoint", "value": "https://monolynx.com"}
    assert active_profile_row in rows
    assert endpoint_row in rows
    # general.active_profile ma byc pierwszy, potem profile -- kolejnosc bez zmian.
    assert rows.index(active_profile_row) < rows.index(endpoint_row)


def test_list_masks_secret_fields(runner):
    token = "osk_" + "3" * 20
    config_mod.save_config({"profile": {"default": {"token": token}}})

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert {"key": "profile.default.token", "value": config_mod.mask_token(token)} in rows
    assert token not in result.output


def test_list_non_str_secret_value_does_not_crash_and_does_not_leak_raw_value(runner):
    config_mod.save_config({"profile": {"default": {"token": 123}}})

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0
    rows = json.loads(result.stdout)
    assert {"key": "profile.default.token", "value": config_mod.mask_token("123")} in rows
    assert "123" not in result.stdout


def test_list_corrupted_config_exits_1(runner):
    from monolynx_cli.config import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("to nie jest [poprawny toml", encoding="utf-8")

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(path) in result.stderr


def test_list_empty_config_exits_0_with_empty_json_array(runner):
    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0
    assert json.loads(result.stdout) == []


def test_list_empty_config_table_format_prints_nothing_and_brak_wynikow_on_stderr(runner):
    result = runner.invoke(app, ["-o", "table", "config", "list"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert "Brak wyników." in result.stderr


# ---------------------------------------------------------------------------
# MON-134: przelaczanie profilu end-to-end (config set -> kolejna komenda)
# ---------------------------------------------------------------------------


def test_set_active_profile_switches_profile_used_by_next_command(runner, monkeypatch, mock_transport):
    # Dwa profile z roznymi tokenami: dowodem przelaczenia jest Authorization nastepnej komendy,
    # nie samo echo `config get` (odczyt tej samej wartosci, ktora wlasnie zapisano).
    from monolynx_cli.commands import auth as auth_mod

    config_mod.save_config(
        {
            "general": {"active_profile": "praca"},
            "profile": {
                "praca": {"auth_type": "token", "token": "tok-praca-1234"},
                "dom": {"auth_type": "token", "token": "tok-dom-5678"},
            },
        }
    )
    transport, requests = mock_transport(lambda request: httpx.Response(200, json={"id": "1", "email": "sw@example.com"}))
    monkeypatch.setattr(
        auth_mod,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )

    before = runner.invoke(app, ["auth", "whoami"])
    switched = runner.invoke(app, ["config", "set", "general.active_profile", "dom"])
    after = runner.invoke(app, ["auth", "whoami"])

    assert before.exit_code == 0, before.output
    assert switched.exit_code == 0, switched.output
    assert after.exit_code == 0, after.output
    assert json.loads(before.stdout)["profile"] == "praca"
    assert json.loads(after.stdout)["profile"] == "dom"
    assert [request.headers["authorization"] for request in requests] == ["Bearer tok-praca-1234", "Bearer tok-dom-5678"]
