"""Kody wyjscia i komunikaty bledow end-to-end (MON-134): komenda z kazdej grupy przez CliRunner.

Konwencja (MON-126): 0 sukces, 1 blad API (400/404/422/5xx po wyczerpaniu retry), 2 zly argument,
3 autoryzacja (401/403), 4 blad sieci. test_client.py sprawdza mapowanie na poziomie klienta, a
test_main.py przez `auth whoami`; tutaj ta sama tabela przechodzi przez root `app` dla komend z
czterech grup, ktore otwieraja klienta roznymi sciezkami (auth, project, ticket/sprint przez
`_common.open_project`, wiki przez wlasny `_open_client`).

Kazdy przypadek asertuje DOKLADNIE jeden kod wyjscia, pusty stdout (blad nie zanieczyszcza danych),
tresc bledu na stderr i brak "Traceback" (kontrolowane wyjscie, nie nieobsluzony wyjatek). Liczba
requestow dowodzi polityki retry: GET po 5xx ponawiany do 4 prob, 4xx i blad sieci bez ponowien.
Sleep klienta jest no-op, wiec retry nie spi.
"""

from __future__ import annotations

import functools

import httpx
import pytest
from monolynx_cli import client as client_mod
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

_PROBLEM_CT = {"content-type": "application/problem+json"}
_WIDE = {"COLUMNS": "2000"}

_COMMANDS = [
    pytest.param(["auth", "whoami"], id="auth-whoami"),
    pytest.param(["project", "list"], id="project-list"),
    pytest.param(["ticket", "list"], id="ticket-list"),
    pytest.param(["sprint", "list"], id="sprint-list"),
    pytest.param(["wiki", "list"], id="wiki-list"),
]


def _patch_clients(monkeypatch, mock_transport, handler):
    # Wiazania nazwy `MonolynxClient` sa per modul komend -- patrz test_token_leak.py.
    from monolynx_cli.commands import _common, auth

    transport, requests = mock_transport(handler)
    factory = functools.partial(client_mod.MonolynxClient, transport=transport, sleep=lambda seconds: None)
    for module in (_common, auth):
        monkeypatch.setattr(module, "MonolynxClient", factory)
    return requests


def _problem(status: int):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json={"title": f"Tytul {status}", "detail": f"Szczegoly {status}"}, headers=_PROBLEM_CT)

    return handler


def _html_502(request: httpx.Request) -> httpx.Response:
    return httpx.Response(502, content=b"<html>Bad Gateway</html>", headers={"content-type": "text/html"})


def _connect_error(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("polaczenie odrzucone", request=request)


# (handler, oczekiwany kod wyjscia, fragmenty komunikatu, liczba requestow)
_CASES = [
    pytest.param(_problem(400), 1, ["Tytul 400", "Szczegoly 400"], 1, id="400"),
    pytest.param(_problem(404), 1, ["Tytul 404", "Szczegoly 404"], 1, id="404"),
    pytest.param(_problem(422), 1, ["Tytul 422", "Szczegoly 422"], 1, id="422"),
    pytest.param(_problem(500), 1, ["Tytul 500", "Szczegoly 500"], 4, id="500-po-retry"),
    pytest.param(_html_502, 1, ["502", "Bad Gateway"], 4, id="502-html-po-retry"),
    pytest.param(_problem(401), 3, ["Tytul 401", "Szczegoly 401"], 1, id="401"),
    pytest.param(_problem(403), 3, ["Tytul 403", "Szczegoly 403"], 1, id="403"),
    pytest.param(_connect_error, 4, ["ConnectError", "polaczenie odrzucone"], 1, id="blad-sieci"),
]


@pytest.mark.parametrize(("handler", "expected_exit", "fragments", "expected_requests"), _CASES)
@pytest.mark.parametrize("command", _COMMANDS)
def test_command_maps_server_failure_to_exit_code_and_stderr_message(
    runner, monkeypatch, mock_transport, command, handler, expected_exit, fragments, expected_requests
):
    monkeypatch.setenv("MONOLYNX_TOKEN", "tok-value-1234")
    requests = _patch_clients(monkeypatch, mock_transport, handler)

    result = runner.invoke(app, ["--project", "err-proj", *command], env=_WIDE)

    assert result.exit_code == expected_exit
    # SystemExit = kontrolowane wyjscie przez handler MonolynxGroup; inny typ oznaczalby
    # nieobsluzony wyjatek zdegradowany przez runner do kodu 1.
    assert isinstance(result.exception, SystemExit)
    assert result.stdout == ""
    for fragment in fragments:
        assert fragment in result.stderr
    assert "Traceback" not in result.output
    assert len(requests) == expected_requests


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["project", "create"], id="project-create-bez-name"),
        pytest.param(["ticket", "get"], id="ticket-get-bez-argumentu"),
        pytest.param(["sprint", "get"], id="sprint-get-bez-argumentu"),
        pytest.param(["wiki", "get"], id="wiki-get-bez-argumentu"),
    ],
)
def test_missing_required_argument_exits_2_without_request(runner, monkeypatch, mock_transport, args):
    monkeypatch.setenv("MONOLYNX_TOKEN", "tok-value-1234")
    requests = _patch_clients(monkeypatch, mock_transport, _problem(500))

    result = runner.invoke(app, ["--project", "err-proj", *args], env=_WIDE)

    assert result.exit_code == 2
    assert result.stdout == ""
    assert result.stderr.strip()
    assert "Traceback" not in result.output
    assert requests == []
