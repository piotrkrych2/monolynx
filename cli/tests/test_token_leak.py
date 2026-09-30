"""Przekrojowy test bezpieczenstwa (MON-134): pelny token nigdy nie trafia na stdout ani stderr.

Test_main.py sprawdza to dla `auth whoami` i `auth login`; tutaj ta sama gwarancja dla komendy
z KAZDEJ grupy, ktora otwiera klienta HTTP inna sciezka (auth, project, ticket/sprint przez
`_common.open_project`, wiki przez wlasny `_open_client`) oraz dla lokalnych `config list/get`.
Wszystko z `--debug`, bo to tryb, w ktorym CLI loguje naglowki zadania, i dla obu ksztaltow
tokenu: legacy `osk_*` oraz OAuth bez prefiksu (`token_urlsafe(48)`) -- maskowanie nie moze
zakladac prefiksu.

Test nie jest trywialnie zielony: kazdy wariant sieciowy dowodzi najpierw, ze zadanie faktycznie
wyszlo z pelnym tokenem w Authorization (lista requestow z `mock_transport`) i ze na stderr
pojawila sie forma zamaskowana, dopiero potem sprawdza nieobecnosc pelnego tokenu. Znaki nowej
linii sa usuwane przed asercja, zeby zawiniety token nie dawal falszywej zieleni.
"""

from __future__ import annotations

import functools
import json

import httpx
import pytest
from monolynx_cli import client as client_mod
from monolynx_cli import config as config_mod
from monolynx_cli.config import mask_token
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

_OSK_TOKEN = "osk_" + "a" * 40
# secrets.token_urlsafe(48) daje 64 znaki bez prefiksu osk_.
_OAUTH_TOKEN = "Zk3-" + "q" * 60
_TOKENS = pytest.mark.parametrize("token", [_OSK_TOKEN, _OAUTH_TOKEN], ids=["osk", "oauth-bez-prefiksu"])
_WIDE = {"COLUMNS": "2000"}
_PROBLEM_CT = {"content-type": "application/problem+json"}

# Po jednej komendzie odczytu z kazdej grupy, ktora bije w API.
_NETWORK_COMMANDS = [
    pytest.param(["auth", "whoami"], id="auth-whoami"),
    pytest.param(["project", "list"], id="project-list"),
    pytest.param(["ticket", "list"], id="ticket-list"),
    pytest.param(["sprint", "list"], id="sprint-list"),
    pytest.param(["wiki", "list"], id="wiki-list"),
]


def _patch_clients(monkeypatch, mock_transport, handler):
    """Podmienia `MonolynxClient` w KAZDYM module komend, ktory tworzy klienta.

    Wszystkie grupy poza auth tworza klienta przez fabryke `_common.new_client`, wiec podmiana
    samego modulu grupy nic by nie zmienila; auth ma wlasne wiazanie nazwy.
    """
    from monolynx_cli.commands import _common, auth

    transport, requests = mock_transport(handler)
    factory = functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None)
    for module in (_common, auth):
        monkeypatch.setattr(module, "MonolynxClient", factory)
    return requests


def _server(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        if status != 200:
            return httpx.Response(status, json={"title": "Odmowa", "detail": "Token odrzucony"}, headers=_PROBLEM_CT)
        if request.url.path == "/api/v2/me":
            return httpx.Response(200, json={"id": "11111111-1111-4111-8111-111111111111", "email": "leak@example.com"})
        return httpx.Response(200, json={"items": [], "page": 1, "per_page": 100, "total": 0, "total_pages": 1})

    return handler


@_TOKENS
@pytest.mark.parametrize(
    ("status", "expected_exit"),
    [(200, 0), (401, 3)],
    ids=["odpowiedz-200", "odpowiedz-401"],
)
@pytest.mark.parametrize("command", _NETWORK_COMMANDS)
def test_debug_never_leaks_full_token_for_command_from_every_group(runner, monkeypatch, mock_transport, command, token, status, expected_exit):
    monkeypatch.setenv("MONOLYNX_TOKEN", token)
    requests = _patch_clients(monkeypatch, mock_transport, _server(status))

    result = runner.invoke(app, ["--debug", "--project", "leak-proj", *command], env=_WIDE)

    assert result.exit_code == expected_exit, result.output
    # Dowod, ze token faktycznie poszedl w zadaniu -- bez tego brak tokenu w wyjsciu moglby
    # znaczyc tylko, ze komenda nie wyslala niczego.
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == f"Bearer {token}"
    assert f"Authorization: Bearer {mask_token(token)}" in result.stderr
    assert token not in result.stdout.replace("\n", "")
    assert token not in result.stderr.replace("\n", "")
    assert token not in result.output.replace("\n", "")


# config list/get sa lokalne (zero HTTP), wiec wariant odpowiedzi 200/401 nie istnieje i nie jest
# udawany; zamiast niego dowodem "nie trywialnie" jest zamaskowana forma na stdout.


@_TOKENS
def test_debug_config_list_masks_token_from_config_file(runner, token):
    config_mod.save_config({"profile": {"default": {"auth_type": "token", "token": token}}})

    result = runner.invoke(app, ["--debug", "config", "list"], env=_WIDE)

    assert result.exit_code == 0, result.output
    rows = json.loads(result.stdout)
    assert {"key": "profile.default.token", "value": mask_token(token)} in rows
    assert token not in result.stdout.replace("\n", "")
    assert token not in result.stderr.replace("\n", "")


@_TOKENS
def test_debug_config_get_masks_token_from_config_file(runner, token):
    config_mod.save_config({"profile": {"default": {"auth_type": "token", "token": token}}})

    result = runner.invoke(app, ["--debug", "config", "get", "profile.default.token"], env=_WIDE)

    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout) == {"key": "profile.default.token", "value": mask_token(token)}
    assert token not in result.stdout.replace("\n", "")
    assert token not in result.stderr.replace("\n", "")
