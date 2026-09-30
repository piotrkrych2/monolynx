"""Wspolna infrastruktura testowa pakietu monolynx-cli -- runner, izolacja configu, MockTransport.

Fixture'y tutaj sa kontraktem dla wszystkich przyszlych ticketow CLI (MON-125, MON-126,
MON-127, MON-128, MON-130, MON-133, MON-143): nazwy i sygnatury nie zmieniaja sie bez
koordynacji z zespolem.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator

import httpx
import platformdirs
import pytest
from typer.testing import CliRunner


@pytest.fixture
def runner() -> CliRunner:
    # Click >= 8.2 usunal parametr mix_stderr (stdout/stderr sa rozdzielone domyslnie);
    # na starszym Click trzeba go podac jawnie, zeby dostac rozdzielone strumienie.
    try:
        return CliRunner(mix_stderr=False)
    except TypeError:
        return CliRunner()


def _clear_monolynx_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [key for key in os.environ if key.startswith("MONOLYNX_")]:
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch) -> Iterator[object]:
    # Autouse: zaden test CLI nie ma prawa dotknac prawdziwego ~/.config uzytkownika
    # ani dziedziczyc jego zmiennych MONOLYNX_* z shella, w ktorym odpalono pytest.
    # Uwaga dla implementacji: patchujemy atrybuty modulu platformdirs, wiec kod pod
    # testem musi wolac platformdirs.user_config_dir(...)/user_config_path(...) w
    # momencie wywolania, a nie robic `from platformdirs import user_config_dir`
    # na poziomie modulu -- takie bind przy imporcie omija ten patch.
    config_dir = tmp_path / "config"
    monkeypatch.setattr(platformdirs, "user_config_dir", lambda *args, **kwargs: str(config_dir))
    monkeypatch.setattr(platformdirs, "user_config_path", lambda *args, **kwargs: config_dir)
    _clear_monolynx_env(monkeypatch)
    yield config_dir


@pytest.fixture
def mock_transport() -> Callable[[Callable[[httpx.Request], httpx.Response]], tuple[httpx.MockTransport, list[httpx.Request]]]:
    def make(
        handler: Callable[[httpx.Request], httpx.Response],
    ) -> tuple[httpx.MockTransport, list[httpx.Request]]:
        requests: list[httpx.Request] = []

        def _record(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return handler(request)

        return httpx.MockTransport(_record), requests

    return make
