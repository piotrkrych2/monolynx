"""Testy komend monolynx_cli.commands.auth: login/logout/whoami.

Specyfikacja dla MON-125, rozszerzona w MON-126: login/whoami wolaja GET /api/v2/me przez
`monolynx_cli.client.MonolynxClient`, a bledy klienta (401/403 -> 3, siec -> 4, inne API -> 1)
propaguja do globalnego handlera w monolynx_cli.main. Punkt wstrzykniecia HTTP to nazwa
modulowa `monolynx_cli.commands.auth.MonolynxClient`, podmieniana w testach na
`functools.partial(MonolynxClient, transport=..., sleep=...)` -- transport z fixture
`mock_transport` (conftest.py), sleep no-op, wiec retry nie spi naprawde.
"""

from __future__ import annotations

import ast
import csv
import functools
import io
import json
import os
import stat
from datetime import datetime, timedelta
from pathlib import Path

import httpx
import pytest
import typer
import yaml
from monolynx_cli import client as client_mod
from monolynx_cli import config as config_mod
from monolynx_cli import oauth
from monolynx_cli.config import mask_token
from monolynx_cli.main import app
from monolynx_cli.version import __version__

pytestmark = pytest.mark.unit

_PROBLEM_CT = {"content-type": "application/problem+json"}


def _me_handler(email: str, *, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(
                status,
                json={"title": f"Blad {status}", "detail": f"Szczegoly bledu {status}"},
                headers=_PROBLEM_CT,
            )
        return httpx.Response(status, json={"id": "11111111-1111-4111-8111-111111111111", "email": email})

    return handler


def _patch_client(monkeypatch, mock_transport, handler):
    from monolynx_cli.commands import auth as auth_mod

    transport, requests = mock_transport(handler)
    monkeypatch.setattr(
        auth_mod,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None),
    )
    return requests


# ---------------------------------------------------------------------------
# auth login: sukces
# ---------------------------------------------------------------------------


def test_login_success_writes_endpoint_auth_type_token_to_target_profile(runner, monkeypatch, mock_transport):
    config_mod.save_config(
        {
            "general": {"active_profile": "preexisting"},
            "profile": {"other": {"endpoint": "https://other.test", "project": "other-proj"}},
        }
    )
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("login@example.com"))

    result = runner.invoke(
        app,
        ["auth", "login", "--token", "tok-value-1234", "--profile", "target", "--endpoint", "https://custom-ep.test"],
    )

    assert result.exit_code == 0, result.output
    data = config_mod.load_config()
    assert data["general"]["active_profile"] == "preexisting"
    assert data["profile"]["target"] == {
        "endpoint": "https://custom-ep.test",
        "auth_type": "token",
        "token": "tok-value-1234",
    }
    assert data["profile"]["other"] == {"endpoint": "https://other.test", "project": "other-proj"}
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v2/me"
    assert requests[0].headers["authorization"] == "Bearer tok-value-1234"


def test_login_success_preserves_other_existing_fields_of_target_profile(runner, monkeypatch, mock_transport):
    # Zabija mutanta "target = {}" zamiast kopii istniejacego profilu: profil "p" juz
    # istnieje z polami spoza tych trzech, ktore login nadpisuje (endpoint/auth_type/token).
    config_mod.save_config(
        {
            "profile": {
                "p": {
                    "endpoint": "https://stary-ep.test",
                    "project": "istniejacy-projekt",
                    "client_id": "mlx_istniejacy_klient",
                    "expires_at": "2099-01-01T00:00:00Z",
                    "access_token": "stary-access-token",
                }
            }
        }
    )
    _patch_client(monkeypatch, mock_transport, _me_handler("zachowany@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "nowy-token-999", "--profile", "p"])

    assert result.exit_code == 0, result.output
    profile = config_mod.load_config()["profile"]["p"]
    assert profile["project"] == "istniejacy-projekt"
    assert profile["client_id"] == "mlx_istniejacy_klient"
    assert profile["expires_at"] == "2099-01-01T00:00:00Z"
    assert profile["access_token"] == "stary-access-token"
    assert profile["auth_type"] == "token"
    assert profile["token"] == "nowy-token-999"


def test_login_success_prints_email_and_profile(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _me_handler("czesc@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok-abc", "--profile", "praca"])

    assert result.exit_code == 0
    # MON-228 D2: potwierdzenie to komunikat stanu, idzie na stderr; stdout zostaje czysty dla potoków.
    assert result.stdout == ""
    assert result.stderr.strip() == "Zalogowano jako czesc@example.com (profil praca)"


def test_login_quiet_suppresses_success_message_on_both_streams(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _me_handler("czesc@example.com"))

    result = runner.invoke(app, ["--quiet", "auth", "login", "--token", "tok-abc", "--profile", "praca"])

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "Zalogowano" not in result.output
    assert config_mod.load_config()["profile"]["praca"]["token"] == "tok-abc"


def test_login_sets_active_profile_to_default_when_missing(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _me_handler("a@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok-abc", "--profile", "custom"])

    assert result.exit_code == 0
    assert config_mod.load_config()["general"]["active_profile"] == "default"


# ---------------------------------------------------------------------------
# auth login: OAuth 2.1 z loopback redirect (MON-143)
# Brak --token to celowo logowanie przez przegladarke (OAuth), bez promptu o token;
# --token zostaje sciezka bez przegladarki i bez DCR. Serwer autoryzacji i symulacja
# przegladarki pochodza z test_oauth.py (FakeAuthServer, make_browser, EchoSpy).
# ---------------------------------------------------------------------------


def _helpers():
    import test_oauth

    return test_oauth


@pytest.fixture
def oauth_env(monkeypatch, mock_transport):
    from monolynx_cli.commands import auth as auth_mod

    server = _helpers().install_server(monkeypatch, mock_transport)
    monkeypatch.setattr(
        auth_mod,
        "MonolynxClient",
        functools.partial(client_mod.MonolynxClient, transport=server.transport, sleep=lambda seconds: None),
    )
    yield server
    server.join()


def _oauth_login_args(*extra: str) -> list[str]:
    return [*extra, "auth", "login", "--profile", "p", "--endpoint", _helpers().BASE]


def _login_secrets(server) -> list[str]:
    forms = [form for form in server.token_forms if form["grant_type"] == "authorization_code"]
    return [_helpers().CODE, *(form["code_verifier"] for form in forms), *server.issued_access, *server.issued_refresh]


def _assert_no_secrets(text: str, secrets: list[str]) -> None:
    assert secrets
    for secret in secrets:
        assert secret not in text


def test_login_without_token_runs_oauth_and_does_not_prompt(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    monkeypatch.setattr(oauth.webbrowser, "open", helpers.make_browser(oauth_env))

    result = runner.invoke(app, _oauth_login_args(), input="")

    assert result.exit_code == 0, result.output
    assert result.stdout == ""
    assert "Zalogowano jako oauth-user@example.com (profil p)" in result.stderr
    assert oauth_env.paths() == [helpers.DISCOVERY_PATH, "/register", "/token", "/api/v2/me"]


def test_login_oauth_saves_tokens_to_profile_and_drops_legacy_token(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    config_mod.save_config(
        {
            "general": {"active_profile": "preexisting"},
            "profile": {
                "p": {"endpoint": "https://stary.test", "project": "keep-proj", "auth_type": "token", "token": "legacy-token-1234"},
                "other": {"endpoint": "https://other.test", "project": "other-proj"},
            },
        }
    )
    monkeypatch.setattr(oauth.webbrowser, "open", helpers.make_browser(oauth_env))

    result = runner.invoke(app, _oauth_login_args())

    assert result.exit_code == 0, result.output
    data = config_mod.load_config()
    client_id = next(iter(oauth_env.clients))
    profile = data["profile"]["p"]
    assert profile["endpoint"] == helpers.BASE
    assert profile["auth_type"] == "oauth"
    assert profile["access_token"] == oauth_env.issued_access[0]
    assert profile["refresh_token"] == oauth_env.issued_refresh[0]
    assert profile["client_id"] == client_id
    assert profile["project"] == "keep-proj"
    assert "token" not in profile
    assert datetime.fromisoformat(profile["expires_at"]).utcoffset() == timedelta(0)
    assert data["profile"]["other"] == {"endpoint": "https://other.test", "project": "other-proj"}
    assert data["general"]["active_profile"] == "preexisting"
    me_request = next(request for request in oauth_env.requests if request.url.path == "/api/v2/me")
    assert me_request.headers["authorization"] == f"Bearer {oauth_env.issued_access[0]}"


def test_login_oauth_twice_registers_new_client_each_time_and_both_succeed(runner, monkeypatch, oauth_env):
    helpers = _helpers()

    monkeypatch.setattr(oauth.webbrowser, "open", helpers.make_browser(oauth_env, code="code-first-" + "1" * 20))
    first = runner.invoke(app, _oauth_login_args())
    first_client_id = config_mod.load_config()["profile"]["p"]["client_id"]
    monkeypatch.setattr(oauth.webbrowser, "open", helpers.make_browser(oauth_env, code="code-second-" + "2" * 20))
    second = runner.invoke(app, _oauth_login_args())

    assert first.exit_code == 0, first.output
    assert second.exit_code == 0, second.output
    assert len(oauth_env.register_bodies) == 2
    profile = config_mod.load_config()["profile"]["p"]
    assert profile["client_id"] != first_client_id
    assert profile["client_id"] == list(oauth_env.clients)[1]
    assert profile["access_token"] == oauth_env.issued_access[1]


def test_login_with_token_flag_skips_browser_and_dcr(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    oauth_env.valid_access.add("tok-z-flagi-1234")
    opened: list[str] = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: opened.append("x") or True)

    result = runner.invoke(app, ["auth", "login", "--token", "tok-z-flagi-1234", "--profile", "p", "--endpoint", helpers.BASE])

    assert result.exit_code == 0, result.output
    assert opened == []
    assert oauth_env.paths() == ["/api/v2/me"]
    assert oauth_env.timeouts == []
    profile = config_mod.load_config()["profile"]["p"]
    assert profile["auth_type"] == "token"
    assert profile["token"] == "tok-z-flagi-1234"


def test_login_no_browser_prints_url_to_stderr_only_and_completes(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    opened: list[str] = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: opened.append("x") or True)
    monkeypatch.setattr(typer, "echo", helpers.EchoSpy(oauth_env))

    result = runner.invoke(app, [*_oauth_login_args(), "--no-browser"])

    assert result.exit_code == 0, result.output
    assert opened == []
    (url,) = oauth_env.opened_urls
    assert url.startswith(f"{helpers.BASE}/authorize?")
    assert "code_challenge_method=S256" in url
    assert url in result.stderr
    assert url not in result.stdout
    assert result.stdout == ""
    assert "Zalogowano jako oauth-user@example.com (profil p)" in result.stderr


@pytest.mark.parametrize("failure", ["returns_false", "raises"])
def test_login_prints_url_to_stderr_when_browser_cannot_open(runner, monkeypatch, oauth_env, failure):
    helpers = _helpers()

    def failing_open(*args, **kwargs):
        if failure == "raises":
            raise RuntimeError("brak przegladarki")
        return False

    monkeypatch.setattr(oauth.webbrowser, "open", failing_open)
    monkeypatch.setattr(typer, "echo", helpers.EchoSpy(oauth_env))

    result = runner.invoke(app, _oauth_login_args())

    assert result.exit_code == 0, result.output
    (url,) = oauth_env.opened_urls
    assert url in result.stderr
    assert url not in result.stdout


def test_login_help_lists_no_browser_flag(runner):
    result = runner.invoke(app, ["auth", "login", "--help"])

    assert result.exit_code == 0
    assert "--no-browser" in result.stdout


@pytest.mark.parametrize("mode", ["denied", "wrong_state", "missing_state"])
def test_login_oauth_rejected_callback_exits_3_and_leaves_config_untouched(runner, monkeypatch, oauth_env, mode):
    helpers = _helpers()
    config_mod.save_config({"general": {"active_profile": "p"}, "profile": {"p": {"endpoint": helpers.BASE, "project": "keep-proj"}}})
    before = config_mod.config_path().read_bytes()
    monkeypatch.setattr(oauth.webbrowser, "open", helpers.make_browser(oauth_env, mode=mode))

    result = runner.invoke(app, _oauth_login_args())

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.strip()
    assert result.stdout == ""
    assert config_mod.config_path().read_bytes() == before
    assert oauth_env.token_forms == []
    assert "/api/v2/me" not in oauth_env.paths()


def test_login_oauth_timeout_exits_3_and_leaves_config_untouched(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    config_mod.save_config({"general": {"active_profile": "p"}, "profile": {"p": {"endpoint": helpers.BASE, "project": "keep-proj"}}})
    before = config_mod.config_path().read_bytes()
    monkeypatch.setattr(oauth, "LOGIN_TIMEOUT", 0.5)
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: True)

    result = runner.invoke(app, _oauth_login_args())

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.strip()
    assert config_mod.config_path().read_bytes() == before


def test_login_oauth_network_error_exits_4_and_leaves_config_untouched(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    config_mod.save_config({"general": {"active_profile": "p"}, "profile": {"p": {"endpoint": helpers.BASE, "project": "keep-proj"}}})
    before = config_mod.config_path().read_bytes()
    oauth_env.discovery_error = httpx.ConnectError("polaczenie odrzucone")
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: pytest.fail("bez discovery nie ma przegladarki"))

    result = runner.invoke(app, _oauth_login_args())

    assert result.exit_code == 4
    assert isinstance(result.exception, SystemExit)
    assert config_mod.config_path().read_bytes() == before


def test_login_oauth_me_rejection_exits_3_and_does_not_write_config(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    oauth_env.me_status = 401
    monkeypatch.setattr(oauth.webbrowser, "open", helpers.make_browser(oauth_env))

    result = runner.invoke(app, _oauth_login_args())

    assert result.exit_code == 3
    assert "Szczegoly bledu 401" in result.stderr
    assert config_mod.config_path().exists() is False


@pytest.mark.parametrize("debug", [False, True])
def test_login_oauth_never_prints_tokens_code_or_verifier(runner, monkeypatch, oauth_env, debug):
    helpers = _helpers()
    monkeypatch.setattr(oauth.webbrowser, "open", helpers.make_browser(oauth_env))

    result = runner.invoke(app, _oauth_login_args(*(["--debug"] if debug else [])))

    assert result.exit_code == 0, result.output
    _assert_no_secrets(result.output, _login_secrets(oauth_env))


def test_login_oauth_no_browser_output_never_contains_tokens_code_or_verifier(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    monkeypatch.setattr(typer, "echo", helpers.EchoSpy(oauth_env))

    result = runner.invoke(app, [*_oauth_login_args("--debug"), "--no-browser"])

    assert result.exit_code == 0, result.output
    _assert_no_secrets(result.output, _login_secrets(oauth_env))


# ---------------------------------------------------------------------------
# auth login: profil docelowy (kolejnosc pierwszenstwa)
# ---------------------------------------------------------------------------


def test_login_profile_env_used_when_no_flag(runner, monkeypatch, mock_transport):
    monkeypatch.setenv("MONOLYNX_PROFILE", "envprof")
    _patch_client(monkeypatch, mock_transport, _me_handler("c@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok"])

    assert result.exit_code == 0
    assert "envprof" in config_mod.load_config()["profile"]


def test_login_profile_active_config_used_when_no_flag_and_no_env(runner, monkeypatch, mock_transport):
    config_mod.save_config({"general": {"active_profile": "cfgprof"}})
    _patch_client(monkeypatch, mock_transport, _me_handler("d@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok"])

    assert result.exit_code == 0
    data = config_mod.load_config()
    assert "cfgprof" in data["profile"]
    assert data["general"]["active_profile"] == "cfgprof"


# ---------------------------------------------------------------------------
# auth login: endpoint (kolejnosc pierwszenstwa)
# ---------------------------------------------------------------------------


def test_login_endpoint_flag_wins_over_env_and_profile(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"endpoint": "https://profile-ep.test"}}})
    monkeypatch.setenv("MONOLYNX_ENDPOINT", "https://env-ep.test")
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("e@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p", "--endpoint", "https://flag-ep.test/"])

    assert result.exit_code == 0, result.output
    assert config_mod.load_config()["profile"]["p"]["endpoint"] == "https://flag-ep.test"
    assert str(requests[0].url).startswith("https://flag-ep.test")


def test_login_endpoint_env_wins_over_profile(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"endpoint": "https://profile-ep.test"}}})
    monkeypatch.setenv("MONOLYNX_ENDPOINT", "https://env-ep.test/")
    _patch_client(monkeypatch, mock_transport, _me_handler("f@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert config_mod.load_config()["profile"]["p"]["endpoint"] == "https://env-ep.test"


def test_login_endpoint_profile_wins_over_default(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"endpoint": "https://profile-ep.test///"}}})
    _patch_client(monkeypatch, mock_transport, _me_handler("g@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert config_mod.load_config()["profile"]["p"]["endpoint"] == "https://profile-ep.test"


def test_login_endpoint_default_when_nothing_configured(runner, monkeypatch, mock_transport):
    from monolynx_cli.config import DEFAULT_ENDPOINT

    _patch_client(monkeypatch, mock_transport, _me_handler("h@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "swiezy"])

    assert result.exit_code == 0, result.output
    assert config_mod.load_config()["profile"]["swiezy"]["endpoint"] == DEFAULT_ENDPOINT


# ---------------------------------------------------------------------------
# auth login: bledy
# ---------------------------------------------------------------------------


def test_login_401_exits_3_and_does_not_write_config(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=401))

    result = runner.invoke(app, ["auth", "login", "--token", "zly-token", "--profile", "p"])

    assert result.exit_code == 3
    assert "Szczegoly bledu 401" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 1
    assert config_mod.config_path().exists() is False


def test_login_401_without_problem_json_exits_3_and_does_not_write_config(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(401, json={"detail": "blad"}))

    result = runner.invoke(app, ["auth", "login", "--token", "zly-token", "--profile", "p"])

    assert result.exit_code == 3
    assert result.stderr.strip()
    assert len(requests) == 1
    assert config_mod.config_path().exists() is False


def test_login_403_exits_3_and_does_not_write_config(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=403))

    result = runner.invoke(app, ["auth", "login", "--token", "zly-token", "--profile", "p"])

    assert result.exit_code == 3
    assert "Szczegoly bledu 403" in result.stderr
    assert config_mod.config_path().exists() is False


def test_login_500_exits_1_after_retries_and_does_not_write_config(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=500))

    result = runner.invoke(app, ["auth", "login", "--token", "zly-token", "--profile", "p"])

    assert result.exit_code == 1
    assert "Szczegoly bledu 500" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 4
    assert config_mod.config_path().exists() is False


def test_login_404_problem_json_exits_1_and_does_not_write_config(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=404))

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 1
    assert "Szczegoly bledu 404" in result.stderr
    assert len(requests) == 1
    assert config_mod.config_path().exists() is False


def test_login_html_502_exits_1_without_parse_crash_and_without_write(runner, monkeypatch, mock_transport):
    _patch_client(
        monkeypatch,
        mock_transport,
        lambda request: httpx.Response(502, content=b"<html>Bad Gateway</html>", headers={"content-type": "text/html"}),
    )

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "502" in result.stderr
    assert config_mod.config_path().exists() is False


def test_login_network_error_exits_4_without_retry_and_does_not_write_config(runner, monkeypatch, mock_transport):
    def _raise(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("polaczenie odrzucone", request=request)

    requests = _patch_client(monkeypatch, mock_transport, _raise)

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 4
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.strip()
    assert result.stdout == ""
    assert len(requests) == 1
    assert config_mod.config_path().exists() is False


def test_login_200_non_json_body_exits_1_without_write(runner, monkeypatch, mock_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"to nie jest json", headers={"content-type": "text/plain"})

    _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 1
    assert result.stderr.strip()
    assert config_mod.config_path().exists() is False


def test_login_200_json_list_body_exits_1_without_write(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, lambda request: httpx.Response(200, json=[1, 2]))

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.strip()
    assert config_mod.config_path().exists() is False


def test_login_uses_monolynx_client_with_token_from_flag(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("client@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok-z-flagi-1234", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert requests[0].headers["authorization"] == "Bearer tok-z-flagi-1234"
    assert requests[0].headers["user-agent"] == f"monolynx-cli/{__version__}"
    assert requests[0].headers["accept"] == "application/json"


def test_login_200_json_without_email_field_exits_1_without_write(runner, monkeypatch, mock_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "11111111-1111-4111-8111-111111111111"})

    _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 1
    assert result.stderr.strip()
    assert config_mod.config_path().exists() is False


def test_login_200_email_field_wrong_type_exits_1_without_write(runner, monkeypatch, mock_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"email": 12345})

    _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 1
    assert result.stderr.strip()
    assert config_mod.config_path().exists() is False


def test_login_corrupted_config_exits_1_without_write(runner, monkeypatch, mock_transport):
    from monolynx_cli.config import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("to nie jest [poprawny toml", encoding="utf-8")
    before = path.read_bytes()
    _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok", "--profile", "p"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(path) in result.stderr
    assert path.read_bytes() == before


def test_login_never_prints_full_token(runner, monkeypatch, mock_transport):
    _patch_client(monkeypatch, mock_transport, _me_handler("i@example.com"))
    long_token = "osk_super-tajny-token-1234567890"

    result = runner.invoke(app, ["auth", "login", "--token", long_token, "--profile", "p"])

    # result.output = stdout+stderr polaczone -- sprawdza oba strumienie naraz.
    assert long_token not in result.output


def test_login_401_leaves_preexisting_config_untouched(runner, monkeypatch, mock_transport):
    config_mod.save_config({"general": {"active_profile": "p"}, "profile": {"p": {"endpoint": "https://x.test"}}})
    before = config_mod.config_path().read_bytes()
    _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=401))

    result = runner.invoke(app, ["auth", "login", "--token", "zly", "--profile", "p"])

    assert result.exit_code == 3
    assert config_mod.config_path().read_bytes() == before


# ---------------------------------------------------------------------------
# auth logout
# ---------------------------------------------------------------------------


def test_logout_clears_secret_fields_but_keeps_the_rest(runner):
    config_mod.save_config(
        {
            "profile": {
                "p": {
                    "endpoint": "https://x.test",
                    "project": "monolynx",
                    "client_id": "mlx_client",
                    "auth_type": "oauth",
                    "token": "tok",
                    "access_token": "acc",
                    "refresh_token": "ref",
                    "expires_at": "2099-01-01T00:00:00Z",
                }
            }
        }
    )

    result = runner.invoke(app, ["auth", "logout", "--profile", "p"])

    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr.strip() == "Wylogowano z profilu p"
    profile = config_mod.load_config()["profile"]["p"]
    assert profile == {
        "endpoint": "https://x.test",
        "project": "monolynx",
        "client_id": "mlx_client",
        "auth_type": "oauth",
    }


def test_logout_missing_profile_exits_0_with_message(runner):
    result = runner.invoke(app, ["auth", "logout", "--profile", "nieznany"])

    assert result.exit_code == 0
    # MON-228 D2: komunikat stanu idzie na stderr, stdout zostaje pusty.
    assert result.stdout == ""
    assert result.stderr.strip() == "Profil nieznany nie istnieje."


def test_logout_quiet_suppresses_success_and_missing_profile_messages(runner):
    config_mod.save_config({"profile": {"p": {"endpoint": "https://x.test", "auth_type": "token", "token": "tok-1234567890"}}})

    logged_out = runner.invoke(app, ["--quiet", "auth", "logout", "--profile", "p"])
    missing = runner.invoke(app, ["--quiet", "auth", "logout", "--profile", "nieznany"])

    assert logged_out.exit_code == 0, logged_out.output
    assert logged_out.output == ""
    assert "token" not in config_mod.load_config()["profile"]["p"]
    assert missing.exit_code == 0, missing.output
    assert missing.output == ""


def test_logout_corrupted_config_exits_1_without_write(runner):
    from monolynx_cli.config import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("to nie jest [poprawny toml", encoding="utf-8")
    before = path.read_bytes()

    result = runner.invoke(app, ["auth", "logout", "--profile", "p"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(path) in result.stderr
    assert path.read_bytes() == before


# ---------------------------------------------------------------------------
# auth whoami
# ---------------------------------------------------------------------------


def test_whoami_success_prints_expected_lines(runner, monkeypatch, mock_transport):
    # Nazwa historyczna (przed MON-127 wypisywal linie tekstu) -- teraz domyslny
    # format poza TTY to JSON, wiec sprawdzamy strukture, nie linie.
    token = "osk_" + "z" * 20
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": token}}})
    _patch_client(monkeypatch, mock_transport, _me_handler("who@example.com"))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {
        "email": "who@example.com",
        "profile": "p",
        "auth_type": "token",
        "token": mask_token(token),
    }
    assert token not in result.output


def test_whoami_oauth_profile_shows_oauth_auth_type_expires_at_and_masks_access_token(runner, monkeypatch, mock_transport):
    # MON-143: dla auth_type oauth whoami dokłada expires_at z profilu.
    access_token = "oauth-" + "a" * 20
    config_mod.save_config({"profile": {"p": {"auth_type": "oauth", "access_token": access_token, "expires_at": "2031-05-06T07:08:09+00:00"}}})
    _patch_client(monkeypatch, mock_transport, _me_handler("oauth-user@example.com"))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload == {
        "email": "oauth-user@example.com",
        "profile": "p",
        "auth_type": "oauth",
        "token": mask_token(access_token),
        "expires_at": "2031-05-06T07:08:09+00:00",
    }
    assert access_token not in result.output


@pytest.mark.parametrize("flag", ["yaml", "csv", "table"])
def test_whoami_masks_token_in_every_output_format(runner, monkeypatch, mock_transport, flag):
    token = "osk_" + flag[0] * 20
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": token}}})
    _patch_client(monkeypatch, mock_transport, _me_handler(f"{flag}@example.com"))

    result = runner.invoke(app, ["-o", flag, "auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert mask_token(token) in result.stdout
    assert token not in result.output


def test_whoami_yaml_format_has_expected_fields(runner, monkeypatch, mock_transport):
    token = "osk_" + "y" * 20
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": token}}})
    _patch_client(monkeypatch, mock_transport, _me_handler("yaml@example.com"))

    result = runner.invoke(app, ["-o", "yaml", "auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    payload = yaml.safe_load(result.stdout)
    assert payload == {
        "email": "yaml@example.com",
        "profile": "p",
        "auth_type": "token",
        "token": mask_token(token),
    }


def test_whoami_csv_format_has_header_and_one_row(runner, monkeypatch, mock_transport):
    token = "osk_" + "c" * 20
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": token}}})
    _patch_client(monkeypatch, mock_transport, _me_handler("csv@example.com"))

    result = runner.invoke(app, ["-o", "csv", "auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    rows = list(csv.reader(io.StringIO(result.stdout)))
    assert rows == [
        ["email", "profile", "auth_type", "token"],
        ["csv@example.com", "p", "token", mask_token(token)],
    ]


def test_whoami_200_non_json_body_exits_1(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"to nie jest json", headers={"content-type": "text/plain"})

    _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 1
    assert result.stderr.strip()


def test_whoami_200_json_without_email_field_exits_1(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "x"})

    _patch_client(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 1
    assert result.stderr.strip()


def test_whoami_corrupted_config_exits_1(runner):
    from monolynx_cli.config import config_path

    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("to nie jest [poprawny toml", encoding="utf-8")

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert str(path) in result.stderr


def test_whoami_401_exits_3(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=401))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 3
    assert "Szczegoly bledu 401" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 1


def test_whoami_403_exits_3(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})
    _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=403))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 3
    assert "Szczegoly bledu 403" in result.stderr
    assert result.stdout == ""


def test_whoami_500_exits_1_after_retries(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=500))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 1
    assert "Szczegoly bledu 500" in result.stderr
    assert result.stdout == ""
    assert len(requests) == 4


def test_whoami_404_problem_json_exits_1(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny", status=404))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 1
    assert "Szczegoly bledu 404" in result.stderr
    assert len(requests) == 1


def test_whoami_html_502_exits_1_without_parse_crash(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})
    _patch_client(
        monkeypatch,
        mock_transport,
        lambda request: httpx.Response(502, content=b"<html>Bad Gateway</html>", headers={"content-type": "text/html"}),
    )

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 1
    assert isinstance(result.exception, SystemExit)
    assert "502" in result.stderr
    assert result.stdout == ""


def test_whoami_network_error_exits_4_without_retry(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})

    def _raise(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("polaczenie odrzucone", request=request)

    requests = _patch_client(monkeypatch, mock_transport, _raise)

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 4
    assert isinstance(result.exception, SystemExit)
    assert result.stderr.strip()
    assert result.stdout == ""
    assert len(requests) == 1


def test_whoami_read_timeout_exits_4(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": "tok"}}})

    def _raise(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout", request=request)

    _patch_client(monkeypatch, mock_transport, _raise)

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 4


def test_whoami_without_token_exits_3_and_makes_no_request(runner, monkeypatch, mock_transport):
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("nieistotny"))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "pusty"])

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "monolynx auth login" in result.stderr
    assert result.stdout == ""
    assert requests == []


def test_whoami_sends_profile_token_as_bearer_with_client_headers(runner, monkeypatch, mock_transport):
    oauth_like_token = "Zk3-" + "q" * 60
    config_mod.save_config({"profile": {"p": {"auth_type": "token", "token": oauth_like_token}}})
    requests = _patch_client(monkeypatch, mock_transport, _me_handler("hdr@example.com"))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert len(requests) == 1
    assert requests[0].url.path == "/api/v2/me"
    assert requests[0].headers["authorization"] == f"Bearer {oauth_like_token}"
    assert requests[0].headers["user-agent"] == f"monolynx-cli/{__version__}"
    assert oauth_like_token not in result.output


def test_whoami_profile_flag_does_not_change_active_profile(runner, monkeypatch, mock_transport):
    config_mod.save_config(
        {
            "general": {"active_profile": "keep-me"},
            "profile": {"other": {"auth_type": "token", "token": "tok-other"}},
        }
    )
    before = config_mod.config_path().read_bytes()
    _patch_client(monkeypatch, mock_transport, _me_handler("j@example.com"))

    result = runner.invoke(app, ["auth", "whoami", "--profile", "other"])

    assert result.exit_code == 0, result.output
    assert config_mod.config_path().read_bytes() == before


def test_whoami_monolynx_profile_env_selects_profile(runner, monkeypatch, mock_transport):
    config_mod.save_config({"profile": {"fromenv": {"auth_type": "token", "token": "tok-env"}}})
    monkeypatch.setenv("MONOLYNX_PROFILE", "fromenv")
    _patch_client(monkeypatch, mock_transport, _me_handler("k@example.com"))

    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["profile"] == "fromenv"


# ---------------------------------------------------------------------------
# auth whoami: profil oauth i odswiezanie tokenu (MON-143)
# ---------------------------------------------------------------------------


def _seed_expired_oauth_profile(server) -> None:
    helpers = _helpers()
    server.valid_refresh.add(helpers.OLD_REFRESH)
    config_mod.save_config(
        {
            "general": {"active_profile": "p"},
            "profile": {
                "p": {
                    "endpoint": helpers.BASE,
                    "project": "keep-proj",
                    "auth_type": "oauth",
                    "access_token": helpers.OLD_ACCESS,
                    "refresh_token": helpers.OLD_REFRESH,
                    "expires_at": helpers.EXPIRED_AT,
                    "client_id": "mlx_seeded_client",
                }
            },
        }
    )


def test_whoami_oauth_valid_token_does_not_refresh_and_shows_expires_at(runner, oauth_env):
    helpers = _helpers()
    _seed_expired_oauth_profile(oauth_env)
    oauth_env.valid_access.add(helpers.OLD_ACCESS)

    result = runner.invoke(app, ["-o", "json", "auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert oauth_env.paths() == ["/api/v2/me"]
    payload = json.loads(result.stdout)
    assert payload["expires_at"] == helpers.EXPIRED_AT
    assert payload["auth_type"] == "oauth"
    assert payload["token"] == mask_token(helpers.OLD_ACCESS)


@pytest.mark.parametrize("debug", [False, True])
def test_whoami_oauth_401_refreshes_token_retries_and_persists_rotated_tokens(runner, oauth_env, debug):
    helpers = _helpers()
    _seed_expired_oauth_profile(oauth_env)

    result = runner.invoke(app, [*(["--debug"] if debug else []), "-o", "json", "auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert [(request.method, request.url.path) for request in oauth_env.requests] == [
        ("GET", "/api/v2/me"),
        ("GET", helpers.DISCOVERY_PATH),
        ("POST", "/token"),
        ("GET", "/api/v2/me"),
    ]
    new_access = oauth_env.issued_access[0]
    assert oauth_env.requests[0].headers["authorization"] == f"Bearer {helpers.OLD_ACCESS}"
    assert oauth_env.requests[3].headers["authorization"] == f"Bearer {new_access}"
    assert oauth_env.token_forms == [{"grant_type": "refresh_token", "refresh_token": helpers.OLD_REFRESH, "client_id": "mlx_seeded_client"}]
    profile = config_mod.load_config()["profile"]["p"]
    assert profile["access_token"] == new_access
    assert profile["refresh_token"] == oauth_env.issued_refresh[0]
    assert profile["refresh_token"] != helpers.OLD_REFRESH
    assert profile["expires_at"] != helpers.EXPIRED_AT
    assert profile["client_id"] == "mlx_seeded_client"
    assert profile["project"] == "keep-proj"
    payload = json.loads(result.stdout)
    assert payload["email"] == "oauth-user@example.com"
    assert payload["token"] == mask_token(new_access)
    assert payload["expires_at"] == profile["expires_at"]
    secrets = [helpers.OLD_ACCESS, helpers.OLD_REFRESH, new_access, oauth_env.issued_refresh[0]]
    _assert_no_secrets(result.output, secrets)


def test_whoami_oauth_invalid_grant_exits_3_with_relogin_hint_and_leaves_config_untouched(runner, oauth_env):
    helpers = _helpers()
    _seed_expired_oauth_profile(oauth_env)
    oauth_env.valid_refresh.clear()
    before = config_mod.config_path().read_bytes()

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "monolynx auth login" in result.stderr
    assert result.stdout == ""
    assert config_mod.config_path().read_bytes() == before
    assert oauth_env.paths() == ["/api/v2/me", helpers.DISCOVERY_PATH, "/token"]
    _assert_no_secrets(result.output, [helpers.OLD_ACCESS, helpers.OLD_REFRESH])


def test_whoami_env_token_over_oauth_profile_has_no_refresh_and_no_expires_at(runner, monkeypatch, oauth_env):
    helpers = _helpers()
    _seed_expired_oauth_profile(oauth_env)
    monkeypatch.setenv("MONOLYNX_TOKEN", "env-token-" + "e" * 20)
    oauth_env.valid_access.add("env-token-" + "e" * 20)

    result = runner.invoke(app, ["-o", "json", "auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["auth_type"] == "token"
    assert "expires_at" not in payload
    assert oauth_env.paths() == ["/api/v2/me"]
    assert config_mod.load_config()["profile"]["p"]["access_token"] == helpers.OLD_ACCESS


def test_whoami_env_token_401_exits_3_without_calling_token_endpoint(runner, monkeypatch, oauth_env):
    _seed_expired_oauth_profile(oauth_env)
    monkeypatch.setenv("MONOLYNX_TOKEN", "env-token-" + "e" * 20)
    before = config_mod.config_path().read_bytes()

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 3
    assert oauth_env.paths() == ["/api/v2/me"]
    assert config_mod.config_path().read_bytes() == before


def test_whoami_token_profile_401_exits_3_without_calling_token_endpoint(runner, oauth_env):
    config_mod.save_config({"profile": {"p": {"endpoint": _helpers().BASE, "auth_type": "token", "token": "tok-" + "t" * 20}}})

    result = runner.invoke(app, ["auth", "whoami", "--profile", "p"])

    assert result.exit_code == 3
    assert oauth_env.paths() == ["/api/v2/me"]


def test_whoami_token_profile_output_has_no_expires_at_key(runner, oauth_env):
    config_mod.save_config(
        {"profile": {"p": {"endpoint": _helpers().BASE, "auth_type": "token", "token": "tok-" + "t" * 20, "expires_at": "2031-05-06T07:08:09+00:00"}}}
    )
    oauth_env.valid_access.add("tok-" + "t" * 20)

    result = runner.invoke(app, ["-o", "json", "auth", "whoami", "--profile", "p"])

    assert result.exit_code == 0, result.output
    assert "expires_at" not in json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Guardy AST (MON-126): auth.py ma isc przez MonolynxClient -- bez wlasnego httpx i bez
# recznego typer.Exit(3), bo kody wyjscia mapuje globalny handler. config.py dalej nie
# zna klienta HTTP (komendy config sa czysto lokalne).
# ---------------------------------------------------------------------------


def _import_violations(tree: ast.Module, forbidden: set[str]) -> list[str]:
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            violations.extend(alias.name for alias in node.names if alias.name in forbidden)
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module in forbidden:
                violations.append(node.module)
            violations.extend(f"{node.module}.{alias.name}" for alias in node.names if f"{node.module}.{alias.name}" in forbidden)
    return violations


def _exit_3_calls(tree: ast.Module) -> list[int]:
    lines: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_exit = (isinstance(func, ast.Attribute) and func.attr == "Exit") or (isinstance(func, ast.Name) and func.id == "Exit")
        if not is_exit:
            continue
        args = [*node.args, *(kw.value for kw in node.keywords if kw.arg == "code")]
        if any(isinstance(arg, ast.Constant) and arg.value == 3 for arg in args):
            lines.append(node.lineno)
    return lines


def _parse_command_module(filename: str) -> ast.Module:
    import monolynx_cli

    path = Path(monolynx_cli.__file__).parent / "commands" / filename
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def test_auth_module_does_not_import_httpx():
    tree = _parse_command_module("auth.py")

    assert _import_violations(tree, {"httpx"}) == []


def test_auth_module_imports_monolynx_client():
    tree = _parse_command_module("auth.py")

    imported = [
        alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module == "monolynx_cli.client" for alias in node.names
    ]
    assert "MonolynxClient" in imported


def test_auth_module_has_no_manual_exit_3():
    tree = _parse_command_module("auth.py")

    assert _exit_3_calls(tree) == []


def test_exit_3_guard_detects_positional_keyword_and_bare_exit_forms():
    # Guard sam musi lapac wszystkie trzy formy zapisu, inaczej test powyzej nic nie chroni.
    tree = ast.parse("import typer\nfrom typer import Exit\ntyper.Exit(3)\nExit(3)\ntyper.Exit(code=3)\ntyper.Exit(1)\n")

    assert _exit_3_calls(tree) == [3, 4, 5]


def test_config_module_does_not_import_client():
    tree = _parse_command_module("config.py")

    assert _import_violations(tree, {"monolynx_cli.client", "monolynx_cli.client.MonolynxClient"}) == []


# ---------------------------------------------------------------------------
# MON-134: prawa pliku po `auth login` przez komende, brak tokenu w komendach domenowych
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="tryby POSIX (0o600/0o700) nie maja odpowiednika na Windows")
def test_login_command_creates_config_file_0600_in_directory_0700(runner, monkeypatch, mock_transport):
    # test_config.py sprawdza tryby na samym save_config; tu dowod, ze sciezka przez komende
    # (login -> save_config) tez zostawia token w pliku nieczytelnym dla innych uzytkownikow.
    _patch_client(monkeypatch, mock_transport, _me_handler("perm@example.com"))

    result = runner.invoke(app, ["auth", "login", "--token", "tok-perm-1234", "--profile", "p"])

    assert result.exit_code == 0, result.output
    path = config_mod.config_path()
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(path.parent).st_mode) == 0o700


@pytest.mark.parametrize(
    "command",
    [
        pytest.param(["project", "list"], id="project-list"),
        pytest.param(["member", "list"], id="member-list"),
        pytest.param(["ticket", "list"], id="ticket-list"),
        pytest.param(["sprint", "list"], id="sprint-list"),
        pytest.param(["wiki", "list"], id="wiki-list"),
    ],
)
def test_domain_command_without_token_exits_3_and_sends_no_request(runner, monkeypatch, mock_transport, command):
    from monolynx_cli.commands import _common
    from monolynx_cli.commands import auth as auth_mod

    transport, requests = mock_transport(_me_handler("nieistotny"))
    factory = functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None)
    for module in (_common, auth_mod):
        monkeypatch.setattr(module, "MonolynxClient", factory)

    result = runner.invoke(app, ["--project", "bez-tokenu", *command])

    assert result.exit_code == 3
    assert isinstance(result.exception, SystemExit)
    assert "monolynx auth login" in result.stderr
    assert result.stdout == ""
    assert "Traceback" not in result.output
    assert requests == []
