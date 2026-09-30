"""Testy szkieletu CLI: entry pointy, --version, --help, grupy podkomend, izolacja backendu.

Specyfikacja dla MON-129 -- pakiet monolynx_cli jeszcze nie istnieje w chwili pisania
tych testow (TDD, red phase). Kontrakt: patrz raport ticketa MON-129.

Sekcja "Flagi globalne (MON-127)" testuje root callback: --output/-o, --no-color,
NO_COLOR, --quiet/-q, --profile/--project (pierwszenstwo flaga lokalna > flaga globalna >
env > config). Kontrakt: interface.md ticketa MON-127.

Sekcja "Klient HTTP (MON-126)" na koncu pliku testuje --debug/--timeout w root callbacku
i globalny handler MonolynxGroup: kody wyjscia 0/1/2/3/4 przez prawdziwy `app`
(CliRunner.invoke omija Typer.__call__, wiec handler musi siedziec w klasie grupy).
"""

from __future__ import annotations

import ast
import contextlib
import csv
import functools
import io
import json
import re
import subprocess
import sys
from importlib import metadata
from pathlib import Path

import httpx
import pytest
import typer
import yaml
from monolynx_cli import client as client_mod
from monolynx_cli import config as config_mod
from monolynx_cli import output as output_mod
from monolynx_cli.config import mask_token
from monolynx_cli.main import app
from monolynx_cli.version import __version__

pytestmark = pytest.mark.unit

_SGR_RE = re.compile(r"\x1b\[([0-9;]*)m")
_SGR_COLOR_PARAMS = frozenset(range(30, 39)) | frozenset(range(40, 49)) | frozenset(range(90, 98)) | frozenset(range(100, 108))


def _has_color_sgr(text: str) -> bool:
    """Czy `text` zawiera kod SGR koloru (pierwszy plan 30-38/90-97, tlo 40-48/100-107,
    w tym `38;5;n`/`38;2;r;g;b` -- wystarczy, ze 38/48 jest jednym z parametrow).

    Rich nie usuwa STYLU (bold=1, italic=3, underline=4...) przy no_color=True, tylko
    KOLOR -- goly substring "\\x1b[3" myli kursywe (SGR 3, np. repr.bool_true =
    "italic bright_green") z zakresem kodow koloru 30-39. Rozbicie parametrow po ";" i
    porownanie z konkretnymi kodami koloru odrozna te dwa przypadki. Duplikat helpera z
    test_output.py -- conftest.py nie ruszamy, kazdy plik testowy ma wlasna kopie.
    """
    for match in _SGR_RE.finditer(text):
        params = match.group(1)
        if not params:
            continue
        if any(int(p) in _SGR_COLOR_PARAMS for p in params.split(";") if p):
            return True
    return False


EXPECTED_GROUPS = frozenset(
    {
        "auth",
        "config",
        "project",
        "member",
        "ticket",
        "sprint",
        "wiki",
        "monitoring",
        "issues",
        "time",
        "plan",
        "rozliczenia",
        "graph",
        "pipeline",
        "heartbeat",
        "role",
    }
)

# Grupy plus samodzielne komendy root (MON-141: `completion` nie jest grupa podkomend).
EXPECTED_ROOT_COMMANDS = EXPECTED_GROUPS | {"completion"}

# Nazwy jawnie zakazane na poziomie root -- MON-129 nie wprowadza tych podkomend.
FORBIDDEN_TOP_LEVEL_COMMANDS = frozenset({"board", "burndown", "label", "ac", "comment"})


# ---------------------------------------------------------------------------
# --version
# ---------------------------------------------------------------------------


def test_version_option_prints_version_and_exits_zero(runner):
    result = runner.invoke(app, ["--version"])

    assert result.exit_code == 0
    assert __version__ in result.output


def test_version_module_matches_installed_package_version():
    installed_version = metadata.version("monolynx-cli")

    assert __version__ == installed_version


# ---------------------------------------------------------------------------
# --help: grupy podkomend
# ---------------------------------------------------------------------------


def test_help_output_lists_every_expected_group(runner):
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for name in EXPECTED_GROUPS:
        assert name in result.output, f"grupa '{name}' nie pojawia sie w --help"


def test_registered_top_level_commands_match_expected_groups_exactly():
    click_app = typer.main.get_command(app)

    assert set(click_app.commands) == EXPECTED_ROOT_COMMANDS


def test_forbidden_top_level_commands_are_not_registered():
    click_app = typer.main.get_command(app)

    registered = set(click_app.commands)
    assert registered.isdisjoint(FORBIDDEN_TOP_LEVEL_COMMANDS)


def test_every_registered_group_has_nonempty_help():
    click_app = typer.main.get_command(app)

    for name in EXPECTED_GROUPS:
        command = click_app.commands[name]
        help_text = command.help or command.get_short_help_str()
        assert help_text, f"grupa '{name}' ma pusty opis"


def test_help_includes_completion_flags(runner):
    # Szerokosc terminala wymuszona, zeby rich/click nie zlamal dlugich flag na
    # osobne linie i test nie byl kruchy na szerokosc terminala odpalajacego pytest.
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "200", "TERMINAL_WIDTH": "200"})

    assert result.exit_code == 0
    assert "--install-completion" in result.output
    assert "--show-completion" in result.output


def test_invoked_without_args_shows_usage(runner):
    # Szerokosc terminala wymuszona jak w tescie completion flags -- rich nie ma
    # zlamac nazwy grup na osobne linie.
    result = runner.invoke(app, [], env={"COLUMNS": "200", "TERMINAL_WIDTH": "200"})

    combined = result.output
    with contextlib.suppress(ValueError):
        # stdout/stderr nie byly rozdzielone (starszy Click) -- tekst juz jest w result.output
        combined += result.stderr

    # Bez no_args_is_help Click konczy blednym "Missing command" (Usage: ... COMMAND
    # [ARGS]...), ktory tez zawiera slowo "Usage" -- to nie odroznia pomocy od bledu.
    # Prawdziwa pomoc wymienia nazwy grup, blad "Missing command" -- nie.
    assert "Missing command" not in combined
    for name in EXPECTED_GROUPS:
        assert name in combined, f"grupa '{name}' nie pojawia sie w pomocy bez argumentow"


# ---------------------------------------------------------------------------
# python -m monolynx_cli oraz entry pointy
# ---------------------------------------------------------------------------


def test_python_dash_m_monolynx_cli_help_matches_expected_groups():
    result = subprocess.run(
        [sys.executable, "-m", "monolynx_cli", "--help"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0
    for name in EXPECTED_GROUPS:
        assert name in result.stdout, f"grupa '{name}' brak w wyjsciu python -m monolynx_cli --help"


def test_console_script_entry_points_both_target_main_app():
    entry_points = metadata.entry_points(group="console_scripts")
    by_name = {ep.name: ep.value for ep in entry_points if ep.name in {"monolynx", "mnx"}}

    assert by_name.get("monolynx") == "monolynx_cli.main:app"
    assert by_name.get("mnx") == "monolynx_cli.main:app"


# ---------------------------------------------------------------------------
# Brak importu backendu
# ---------------------------------------------------------------------------


def _is_backend_module(name: str) -> bool:
    return name == "monolynx" or name.startswith("monolynx.")


def test_package_never_imports_backend_monolynx_package():
    import monolynx_cli

    package_dir = Path(monolynx_cli.__file__).parent
    violations: list[tuple[Path, str]] = []

    for path in package_dir.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                violations.extend((path, alias.name) for alias in node.names if _is_backend_module(alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module and _is_backend_module(node.module):
                violations.append((path, node.module))

    assert not violations, f"import backendu monolynx znaleziony w: {violations}"


# ---------------------------------------------------------------------------
# Infrastruktura testowa: isolated_config
# ---------------------------------------------------------------------------


def test_isolated_config_redirects_platformdirs_to_tmp_path(tmp_path):
    import platformdirs

    config_dir = Path(platformdirs.user_config_dir("monolynx"))
    config_path = Path(platformdirs.user_config_path("monolynx"))

    assert config_dir.is_relative_to(tmp_path)
    assert config_path.is_relative_to(tmp_path)


def test_clear_monolynx_env_removes_existing_vars(monkeypatch):
    import conftest

    monkeypatch.setenv("MONOLYNX_TOKEN", "secret")
    monkeypatch.setenv("MONOLYNX_PROJECT_SLUG", "monolynx")
    monkeypatch.setenv("UNRELATED_VAR", "keep-me")

    conftest._clear_monolynx_env(monkeypatch)

    import os

    assert "MONOLYNX_TOKEN" not in os.environ
    assert "MONOLYNX_PROJECT_SLUG" not in os.environ
    assert os.environ["UNRELATED_VAR"] == "keep-me"


# ---------------------------------------------------------------------------
# Infrastruktura testowa: mock_transport
# ---------------------------------------------------------------------------


def test_mock_transport_records_method_path_and_body(mock_transport):
    transport, requests = mock_transport(lambda request: httpx.Response(200, json={"ok": True}))

    with httpx.Client(transport=transport, base_url="https://x.test") as client:
        response = client.post("/api/v2/y", json={"a": 1})

    assert response.status_code == 200
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert requests[0].url.path == "/api/v2/y"
    assert json.loads(requests[0].content) == {"a": 1}


def test_mock_transport_returns_handler_response_untouched(mock_transport):
    transport, _requests = mock_transport(lambda request: httpx.Response(201, json={"created": True}))

    with httpx.Client(transport=transport, base_url="https://x.test") as client:
        response = client.get("/api/v2/z")

    assert response.status_code == 201
    assert response.json() == {"created": True}


# ---------------------------------------------------------------------------
# Flagi globalne (MON-127): --output/-o, --no-color, NO_COLOR, --quiet/-q
# ---------------------------------------------------------------------------


def _patch_whoami_client(monkeypatch, mock_transport, email: str = "probe@example.com"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "11111111-1111-4111-8111-111111111111", "email": email})

    return _patch_auth_client(monkeypatch, mock_transport, handler)


def _patch_auth_client(monkeypatch, mock_transport, handler):
    from monolynx_cli.commands import auth as auth_mod

    transport, requests = mock_transport(handler)
    monkeypatch.setattr(
        auth_mod,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )
    return requests


@pytest.mark.parametrize(
    ("flag", "parse"),
    [
        ("json", json.loads),
        ("yaml", yaml.safe_load),
    ],
)
def test_output_flag_selects_structured_format_for_config_list(runner, flag, parse):
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})

    result = runner.invoke(app, ["-o", flag, "config", "list"])

    assert result.exit_code == 0, result.output
    rows = parse(result.stdout)
    assert {"key": "profile.default.endpoint", "value": "https://x.test"} in rows


def test_output_csv_flag_selects_csv_format_for_config_list(runner):
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})

    result = runner.invoke(app, ["-o", "csv", "config", "list"])

    assert result.exit_code == 0, result.output
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows[0] == ["key", "value"]
    assert ["profile.default.endpoint", "https://x.test"] in rows[1:]


def test_no_output_flag_defaults_to_json_outside_tty(runner):
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})

    result = runner.invoke(app, ["config", "list"])

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {"key": "profile.default.endpoint", "value": "https://x.test"} in rows


def test_output_table_flag_forces_table_outside_tty(runner):
    # Wymusza -o table poza TTY (domyslnie bylby json) -- jesli flaga jawna byla by
    # ignorowana, wyjscie dalej bylo by poprawnym JSON-em i json.loads by sie udal.
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})

    result = runner.invoke(app, ["-o", "table", "config", "list"])

    assert result.exit_code == 0, result.output
    with pytest.raises(json.JSONDecodeError):
        json.loads(result.stdout)
    assert "profile.default.endpoint" in result.stdout


def test_quiet_flag_does_not_change_stdout_content(runner):
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})

    without_quiet = runner.invoke(app, ["config", "list"])
    with_quiet = runner.invoke(app, ["--quiet", "config", "list"])

    assert without_quiet.exit_code == 0
    assert with_quiet.exit_code == 0
    assert with_quiet.stdout == without_quiet.stdout


def test_quiet_flag_does_not_suppress_command_error_messages(runner):
    without_quiet = runner.invoke(app, ["config", "get", "zly.klucz"])
    with_quiet = runner.invoke(app, ["--quiet", "config", "get", "zly.klucz"])

    assert without_quiet.exit_code == with_quiet.exit_code
    assert with_quiet.stderr.strip() != ""
    assert with_quiet.stderr == without_quiet.stderr


def test_quiet_flag_suppresses_echo_stderr_brak_wynikow_message(runner):
    without_quiet = runner.invoke(app, ["-o", "table", "config", "list"])
    with_quiet = runner.invoke(app, ["--quiet", "-o", "table", "config", "list"])

    assert without_quiet.exit_code == 0
    assert with_quiet.exit_code == 0
    assert "Brak wyników." in without_quiet.stderr
    assert with_quiet.stderr == ""


def test_no_color_flag_sets_global_options_no_color_true_and_produces_no_ansi(runner, monkeypatch):
    # Bez FORCE_COLOR stdout CliRunnera nie jest TTY i rich sam nie emituje koloru
    # niezaleznie od --no-color -- test by przeszedl nawet gdyby flaga nigdzie nie
    # docierala do Console(...). FORCE_COLOR wymusza tryb terminala, zeby efekt
    # --no-color byl faktycznie obserwowalny. Bold naglowka i kursywa wartosci (np.
    # repr.bool_true = "italic bright_green") to STYL, nie KOLOR -- no_color go nie
    # zdejmuje, wiec asercja uzywa _has_color_sgr zamiast golego substringu "\x1b[3"/
    # "\x1b[9" (3 to kursywa, nie zakres kolorow 30-39).
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})
    calls: list[bool] = []
    original_render = output_mod.render

    def spy(data, fmt, *, no_color=False, quiet=False):
        calls.append(no_color)
        return original_render(data, fmt, no_color=no_color, quiet=quiet)

    monkeypatch.setattr(output_mod, "render", spy)

    with_color = runner.invoke(app, ["-o", "table", "config", "list"], env={"FORCE_COLOR": "1"})
    without_color = runner.invoke(app, ["--no-color", "-o", "table", "config", "list"], env={"FORCE_COLOR": "1"})

    assert with_color.exit_code == 0, with_color.output
    assert without_color.exit_code == 0, without_color.output
    assert calls == [False, True]
    assert _has_color_sgr(with_color.stdout)
    assert not _has_color_sgr(without_color.stdout)


def test_no_color_env_var_sets_global_options_no_color_true(runner, monkeypatch):
    # Ta sama pulapka co wyzej: bez FORCE_COLOR rich nigdy nie koloruje, wiec asercja
    # na "brak ANSI" nic by nie chronila. Wymuszamy terminal i porownujemy z przebiegiem
    # bez NO_COLOR na tych samych danych; _has_color_sgr odroznia kolor od stylu
    # (bold/italic), ktorego no_color nie usuwa.
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})
    calls: list[bool] = []
    original_render = output_mod.render

    def spy(data, fmt, *, no_color=False, quiet=False):
        calls.append(no_color)
        return original_render(data, fmt, no_color=no_color, quiet=quiet)

    monkeypatch.setattr(output_mod, "render", spy)

    with_color = runner.invoke(app, ["-o", "table", "config", "list"], env={"FORCE_COLOR": "1"})
    without_color = runner.invoke(app, ["-o", "table", "config", "list"], env={"FORCE_COLOR": "1", "NO_COLOR": "1"})

    assert with_color.exit_code == 0, with_color.output
    assert without_color.exit_code == 0, without_color.output
    assert calls == [False, True]
    assert _has_color_sgr(with_color.stdout)
    assert not _has_color_sgr(without_color.stdout)


def test_without_no_color_flag_or_env_global_options_no_color_is_false(runner, monkeypatch):
    config_mod.save_config({"profile": {"default": {"endpoint": "https://x.test"}}})
    calls: list[bool] = []
    original_render = output_mod.render

    def spy(data, fmt, *, no_color=False, quiet=False):
        calls.append(no_color)
        return original_render(data, fmt, no_color=no_color, quiet=quiet)

    monkeypatch.setattr(output_mod, "render", spy)

    result = runner.invoke(app, ["-o", "table", "config", "list"])

    assert result.exit_code == 0, result.output
    assert calls == [False]


def test_global_profile_and_project_flags_win_over_env_vars(runner, monkeypatch, mock_transport):
    from monolynx_cli.commands import auth as auth_mod

    monkeypatch.setenv("MONOLYNX_PROFILE", "fromenv")
    monkeypatch.setenv("MONOLYNX_PROJECT", "envproj")
    config_mod.save_config({"profile": {"flagprof": {"auth_type": "token", "token": "tok-flag"}}})
    _patch_whoami_client(monkeypatch, mock_transport, email="flag@example.com")

    calls: list[tuple[object, object]] = []
    original_resolve_settings = auth_mod.resolve_settings

    def spy(cli_flags=None):
        settings = original_resolve_settings(cli_flags)
        calls.append((cli_flags, settings))
        return settings

    monkeypatch.setattr(auth_mod, "resolve_settings", spy)

    result = runner.invoke(app, ["--profile", "flagprof", "--project", "flagproj", "auth", "whoami"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    cli_flags, settings = calls[0]
    assert cli_flags.get("profile") == "flagprof"
    assert cli_flags.get("project") == "flagproj"
    assert settings.profile == "flagprof"
    assert settings.project == "flagproj"


def test_local_profile_flag_wins_over_global_profile_flag(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"local": {"auth_type": "token", "token": "tok-local"}}})
    _patch_whoami_client(monkeypatch, mock_transport, email="local@example.com")

    result = runner.invoke(app, ["--profile", "global", "auth", "whoami", "--profile", "local"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["profile"] == "local"


def test_login_global_profile_and_project_flags_win_over_env_vars(runner, monkeypatch, mock_transport):
    # Mutanty do zabicia: `profile or opts.profile` -> `profile` oraz usuniecie
    # `"project": opts.project` w auth login (cli/src/monolynx_cli/commands/auth.py:64).
    from monolynx_cli.commands import auth as auth_mod

    monkeypatch.setenv("MONOLYNX_PROFILE", "fromenv")
    monkeypatch.setenv("MONOLYNX_PROJECT", "envproj")
    _patch_whoami_client(monkeypatch, mock_transport, email="loginflag@example.com")

    calls: list[tuple[object, object]] = []
    original_resolve_settings = auth_mod.resolve_settings

    def spy(cli_flags=None):
        settings = original_resolve_settings(cli_flags)
        calls.append((cli_flags, settings))
        return settings

    monkeypatch.setattr(auth_mod, "resolve_settings", spy)

    result = runner.invoke(
        app,
        ["--profile", "flagprof", "--project", "flagproj", "auth", "login", "--token", "tok-login"],
    )

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    cli_flags, settings = calls[0]
    assert cli_flags.get("profile") == "flagprof"
    assert cli_flags.get("project") == "flagproj"
    assert settings.profile == "flagprof"
    assert settings.project == "flagproj"


def test_login_local_profile_flag_wins_over_global_profile_flag(runner, monkeypatch, mock_transport):
    _patch_whoami_client(monkeypatch, mock_transport, email="loginlocal@example.com")

    result = runner.invoke(
        app,
        ["--profile", "global", "auth", "login", "--profile", "local", "--token", "tok-login"],
    )

    assert result.exit_code == 0, result.output
    assert "profil local" in result.stderr


def test_logout_global_profile_and_project_flags_win_over_env_vars(runner, monkeypatch):
    # Mutanty do zabicia: `profile or opts.profile` -> `profile` oraz usuniecie
    # `"project": opts.project` w auth logout (cli/src/monolynx_cli/commands/auth.py:113).
    from monolynx_cli.commands import auth as auth_mod

    monkeypatch.setenv("MONOLYNX_PROFILE", "fromenv")
    monkeypatch.setenv("MONOLYNX_PROJECT", "envproj")
    config_mod.save_config({"profile": {"flagprof": {"auth_type": "token", "token": "tok-flag"}}})

    calls: list[tuple[object, object]] = []
    original_resolve_settings = auth_mod.resolve_settings

    def spy(cli_flags=None):
        settings = original_resolve_settings(cli_flags)
        calls.append((cli_flags, settings))
        return settings

    monkeypatch.setattr(auth_mod, "resolve_settings", spy)

    result = runner.invoke(app, ["--profile", "flagprof", "--project", "flagproj", "auth", "logout"])

    assert result.exit_code == 0, result.output
    assert len(calls) == 1
    cli_flags, settings = calls[0]
    assert cli_flags.get("profile") == "flagprof"
    assert cli_flags.get("project") == "flagproj"
    assert settings.profile == "flagprof"
    assert settings.project == "flagproj"


def test_logout_local_profile_flag_wins_over_global_profile_flag(runner, monkeypatch):
    config_mod.save_config({"profile": {"local": {"auth_type": "token", "token": "tok-local"}}})

    result = runner.invoke(app, ["--profile", "global", "auth", "logout", "--profile", "local"])

    assert result.exit_code == 0, result.output
    assert "profilu local" in result.stderr


# ---------------------------------------------------------------------------
# Klient HTTP (MON-126): GlobalOptions, --debug, --timeout, kody wyjscia 0-4
# ---------------------------------------------------------------------------

_OSK_TOKEN = "osk_" + "a" * 40
# secrets.token_urlsafe(48) daje 64 znaki bez prefiksu osk_.
_OAUTH_TOKEN = "Zk3-" + "q" * 60
_WIDE = {"COLUMNS": "2000"}
_PROBLEM_CT = {"content-type": "application/problem+json"}


def _problem_handler(status: int, *, detail: str | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            json={"title": f"Tytul {status}", "detail": detail or f"Szczegoly {status}"},
            headers=_PROBLEM_CT,
        )

    return handler


def _me_ok(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"id": "11111111-1111-4111-8111-111111111111", "email": "ok@example.com"})


def test_global_options_defaults_for_debug_and_timeout():
    from monolynx_cli.options import GlobalOptions

    options = GlobalOptions()

    assert options.debug is False
    assert options.timeout == 30.0


def test_get_options_returns_debug_and_timeout_stored_in_context():
    import typer.core
    from monolynx_cli.options import GlobalOptions, get_options

    # typer 0.27 nie ciagnie pakietu click, wiec kontekst budujemy z typer.Context.
    ctx = typer.Context(typer.core.TyperCommand("probe"))
    ctx.obj = GlobalOptions(debug=True, timeout=5.5)

    options = get_options(ctx)

    assert options.debug is True
    assert options.timeout == 5.5


def test_help_lists_debug_and_timeout_flags(runner):
    result = runner.invoke(app, ["--help"], env={"COLUMNS": "200", "TERMINAL_WIDTH": "200"})

    assert result.exit_code == 0
    assert "--debug" in result.output
    assert "--timeout" in result.output


# --- kody wyjscia: 0 ---------------------------------------------------------


def test_exit_code_0_on_successful_whoami(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["email"] == "ok@example.com"
    assert result.stderr == ""


# --- kody wyjscia: 1 ---------------------------------------------------------


def test_exit_code_1_on_500_after_retries_with_message_on_stderr_only(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    requests = _patch_auth_client(monkeypatch, mock_transport, _problem_handler(500))

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "Szczegoly 500" in result.stderr
    assert "500" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 4


def test_exit_code_1_on_404_problem_json_shows_title_and_detail(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    requests = _patch_auth_client(monkeypatch, mock_transport, _problem_handler(404))

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 1
    assert "Tytul 404" in result.stderr
    assert "Szczegoly 404" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 1


# --- kody wyjscia: 2 ---------------------------------------------------------


@pytest.mark.parametrize("value", ["0", "-1", "-0.5", "abc", "nan"])
def test_exit_code_2_on_invalid_timeout_and_no_request_is_sent(runner, monkeypatch, mock_transport, value):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    requests = _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    result = runner.invoke(app, ["--timeout", value, "auth", "whoami"])

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.strip()
    assert requests == []


def test_exit_code_2_on_unknown_global_option(runner):
    result = runner.invoke(app, ["--nie-ma-takiej-opcji", "auth", "whoami"])

    assert result.exit_code == 2
    assert result.stdout == ""


def test_exit_code_2_on_unknown_command_option(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    requests = _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    result = runner.invoke(app, ["auth", "whoami", "--nie-ma-takiej-opcji"])

    assert result.exit_code == 2
    assert requests == []


# --- kody wyjscia: 3 ---------------------------------------------------------


@pytest.mark.parametrize("status", [401, 403])
def test_exit_code_3_on_401_and_403(runner, monkeypatch, mock_transport, status):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    requests = _patch_auth_client(monkeypatch, mock_transport, _problem_handler(status))

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert f"Szczegoly {status}" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 1


def test_exit_code_3_without_token_and_no_request_is_sent(runner, monkeypatch, mock_transport):
    requests = _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 3
    assert "monolynx auth login" in result.stderr
    assert result.stdout == ""
    assert requests == []


# --- kody wyjscia: 4 ---------------------------------------------------------


@pytest.mark.parametrize(
    "error_factory",
    [
        lambda request: httpx.ConnectError("polaczenie odrzucone", request=request),
        lambda request: httpx.ReadTimeout("timeout", request=request),
    ],
    ids=["connect", "read-timeout"],
)
def test_exit_code_4_on_network_error_without_retry(runner, monkeypatch, mock_transport, error_factory):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)

    def handler(request: httpx.Request) -> httpx.Response:
        raise error_factory(request)

    requests = _patch_auth_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 4
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.strip()
    assert result.stdout == ""
    assert len(requests) == 1


# --- globalny handler: obejmuje wszystkie grupy, nie tylko auth ---------------


@pytest.fixture
def probe_command(monkeypatch):
    """Rejestruje komende `pipeline probe` rzucajaca podany wyjatek (poza grupa auth)."""
    from monolynx_cli import main as main_mod

    monkeypatch.setattr(main_mod.pipeline_app, "registered_commands", list(main_mod.pipeline_app.registered_commands))

    def register(exc: Exception) -> None:
        @main_mod.pipeline_app.command("probe")
        def probe() -> None:
            raise exc

    return register


@pytest.mark.parametrize(
    ("exc", "expected_code", "fragment"),
    [
        (client_mod.MonolynxAPIError(409, "Konflikt", "ticket juz istnieje"), 1, "ticket juz istnieje"),
        (client_mod.MonolynxAuthError(401, "Unauthorized", "token wygasl"), 3, "token wygasl"),
        (client_mod.MonolynxAuthError(None, "Brak tokenu", "zaloguj sie"), 3, "zaloguj sie"),
        (client_mod.MonolynxNetworkError("brak polaczenia z serwerem"), 4, "brak polaczenia z serwerem"),
    ],
    ids=["api", "auth-401", "auth-no-token", "network"],
)
def test_global_handler_maps_client_errors_from_any_group_to_exit_codes(runner, probe_command, exc, expected_code, fragment):
    probe_command(exc)

    result = runner.invoke(app, ["pipeline", "probe"], env=_WIDE)

    assert result.exit_code == expected_code
    assert isinstance(result.exception, SystemExit)
    assert fragment in result.stderr
    assert result.stdout == ""


@pytest.mark.usefixtures("probe_command")
def test_global_handler_ignores_unrelated_exit_codes(runner):
    def raise_exit() -> None:
        raise typer.Exit(7)

    from monolynx_cli import main as main_mod

    main_mod.pipeline_app.command("exit-seven")(raise_exit)

    result = runner.invoke(app, ["pipeline", "exit-seven"])

    assert result.exit_code == 7


def test_error_message_is_not_suppressed_by_quiet_flag(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    _patch_auth_client(monkeypatch, mock_transport, _problem_handler(404))

    without_quiet = runner.invoke(app, ["auth", "whoami"])
    with_quiet = runner.invoke(app, ["--quiet", "auth", "whoami"])

    assert with_quiet.exit_code == without_quiet.exit_code == 1
    assert "Szczegoly 404" in with_quiet.stderr
    assert with_quiet.stderr == without_quiet.stderr


def test_error_message_prints_rich_markup_literally(runner, monkeypatch, mock_transport):
    # Gdyby handler renderowal komunikat z markup=True, "[/b]" bez otwarcia rzucilby MarkupError,
    # a "[bold]x[/bold]" zniknelo by z tekstu.
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    detail = "[bold]x[/bold] a[/b]c"
    _patch_auth_client(monkeypatch, mock_transport, _problem_handler(400, detail=detail))

    result = runner.invoke(app, ["auth", "whoami"], env=_WIDE)

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert detail in result.stderr


# --- --timeout end-to-end ------------------------------------------------------


def _timeout_recorder(seen: list[dict]):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.extensions["timeout"]))
        return httpx.Response(200, json={"id": "11111111-1111-4111-8111-111111111111", "email": "t@example.com"})

    return handler


def test_timeout_flag_changes_httpx_timeout_of_client(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    seen: list[dict] = []
    _patch_auth_client(monkeypatch, mock_transport, _timeout_recorder(seen))

    result = runner.invoke(app, ["--timeout", "7.5", "auth", "whoami"])

    assert result.exit_code == 0, result.output
    assert seen == [{"connect": 7.5, "read": 7.5, "write": 7.5, "pool": 7.5}]


def test_default_timeout_is_30_seconds_end_to_end(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    seen: list[dict] = []
    _patch_auth_client(monkeypatch, mock_transport, _timeout_recorder(seen))

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 0, result.output
    assert seen == [{"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}]


def test_timeout_flag_reaches_login_client(runner, monkeypatch, mock_transport):
    seen: list[dict] = []
    _patch_auth_client(monkeypatch, mock_transport, _timeout_recorder(seen))

    result = runner.invoke(app, ["--timeout", "4.25", "auth", "login", "--token", "tok-login-1234", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert seen == [{"connect": 4.25, "read": 4.25, "write": 4.25, "pool": 4.25}]


# --- --debug end-to-end --------------------------------------------------------


@pytest.mark.parametrize("token", [_OSK_TOKEN, _OAUTH_TOKEN], ids=["osk", "oauth-bez-prefiksu"])
def test_debug_logs_request_and_response_on_stderr_and_never_leaks_full_token(runner, monkeypatch, mock_transport, token):
    monkeypatch.setenv("MONOLYNX_TOKEN", token)
    _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    result = runner.invoke(app, ["--debug", "auth", "whoami"], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert "> GET https://monolynx.com/api/v2/me" in result.stderr
    assert f"Authorization: Bearer {mask_token(token)}" in result.stderr
    assert "< 200" in result.stderr
    assert token not in result.stdout
    assert token not in result.stderr.replace("\n", "")
    # whoami wypisuje na stdout zamaskowany token -- to jest oczekiwane, pelny nie.
    assert mask_token(token) in result.stdout


@pytest.mark.parametrize("token", [_OSK_TOKEN, _OAUTH_TOKEN], ids=["osk", "oauth-bez-prefiksu"])
def test_debug_with_401_error_still_never_leaks_full_token(runner, monkeypatch, mock_transport, token):
    monkeypatch.setenv("MONOLYNX_TOKEN", token)
    _patch_auth_client(monkeypatch, mock_transport, _problem_handler(401))

    result = runner.invoke(app, ["--debug", "auth", "whoami"], env=_WIDE)

    assert result.exit_code == 3
    assert "< 401" in result.stderr
    assert f"Authorization: Bearer {mask_token(token)}" in result.stderr
    assert "Szczegoly 401" in result.stderr
    assert token not in result.stdout
    assert token not in result.stderr.replace("\n", "")


@pytest.mark.parametrize("token", [_OSK_TOKEN, _OAUTH_TOKEN], ids=["osk", "oauth-bez-prefiksu"])
def test_debug_login_never_leaks_full_token(runner, monkeypatch, mock_transport, token):
    _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    result = runner.invoke(app, ["--debug", "auth", "login", "--token", token, "--profile", "p"], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert f"Authorization: Bearer {mask_token(token)}" in result.stderr
    assert token not in result.stdout
    assert token not in result.stderr.replace("\n", "")


def test_no_debug_flag_writes_no_request_trace_to_stderr(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    result = runner.invoke(app, ["auth", "whoami"], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert result.stderr == ""


def test_debug_trace_does_not_change_stdout_payload(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_TOKEN", _OSK_TOKEN)
    _patch_auth_client(monkeypatch, mock_transport, _me_ok)

    plain = runner.invoke(app, ["auth", "whoami"], env=_WIDE)
    debug = runner.invoke(app, ["--debug", "auth", "whoami"], env=_WIDE)

    assert debug.stdout == plain.stdout
    assert debug.stderr != ""
