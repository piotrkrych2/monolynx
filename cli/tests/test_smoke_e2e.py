"""Smoke e2e (MON-134): CLI kontra realny serwer Monolynx, tylko odczyt.

Uruchamiany wylacznie z `MONOLYNX_TEST_TOKEN` (opcjonalnie `MONOLYNX_TEST_ENDPOINT`, domyslnie
https://monolynx.com); bez tokenu jest pomijany, wiec zwykly przebieg `-m unit` i CI bez sekretu
go nie dotykaja. Zmienne czytamy do stalych PRZY IMPORCIE modulu: autouse `isolated_config` z
conftest.py czysci wszystkie `MONOLYNX_*` z os.environ przed kazdym testem, a w tescie przekazujemy
je dalej jako `MONOLYNX_TOKEN`/`MONOLYNX_ENDPOINT`. Zadnych zapisow po stronie serwera, a pelny
token nie moze sie pojawic w wyjsciu.
"""

from __future__ import annotations

import json
import os

import pytest
from monolynx_cli.config import mask_token
from monolynx_cli.main import app

_TEST_TOKEN = os.environ.get("MONOLYNX_TEST_TOKEN", "")
_TEST_ENDPOINT = os.environ.get("MONOLYNX_TEST_ENDPOINT", "https://monolynx.com")

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _TEST_TOKEN, reason="brak MONOLYNX_TEST_TOKEN - smoke e2e wymaga tokenu do prawdziwego serwera"),
]


@pytest.fixture
def live_server(monkeypatch):
    monkeypatch.setenv("MONOLYNX_TOKEN", _TEST_TOKEN)
    monkeypatch.setenv("MONOLYNX_ENDPOINT", _TEST_ENDPOINT)


@pytest.mark.usefixtures("live_server")
def test_whoami_returns_authenticated_user_from_real_server(runner):
    result = runner.invoke(app, ["auth", "whoami"])

    assert result.exit_code == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["email"]
    assert payload["token"] == mask_token(_TEST_TOKEN)
    assert _TEST_TOKEN not in result.output


@pytest.mark.usefixtures("live_server")
def test_project_list_returns_json_list_from_real_server(runner):
    result = runner.invoke(app, ["project", "list"])

    assert result.exit_code == 0, result.stderr
    projects = json.loads(result.stdout)
    assert isinstance(projects, list)
    assert all(isinstance(project, dict) and project["slug"] for project in projects)
    assert _TEST_TOKEN not in result.output
