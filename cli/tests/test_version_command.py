"""Testy komendy `monolynx version [--check]` (MON-232).

Kontrakt komendy i modułu `monolynx_cli.commands.version`:

- `monolynx -o json version` -> `{"current": <monolynx_cli.version.__version__>}`; bez żądań sieciowych.
- `monolynx -o json version --check` -> klucze dokładnie `current`, `latest`, `update_available`,
  `upgrade_command`; `latest` pochodzi z `https://pypi.org/pypi/monolynx-cli/json` (`info.version`),
  `update_available` to porównanie numeryczne krotek (`latest` > `current`). Błąd sieci, timeout,
  HTTP != 200, zły JSON albo brak `info.version` -> kod 0, `latest` = null, `update_available` = false.
- Funkcje modułowe (wołane przez nazwę modułową, więc monkeypatchowalne):
  `detect_installer(prefix: str) -> str` (po podanej ścieżce, bez `os.path.realpath`; kolejność: `/pipx/venvs/` -> `pipx`,
  `/uv/tools/` -> `uv`, `/Cellar/` lub `/homebrew/` -> `brew`, inaczej `unknown`),
  `upgrade_command(installer: str) -> str` (`brew upgrade monolynx`, `pipx upgrade monolynx-cli`,
  `uv tool upgrade monolynx-cli`, w pozostałych przypadkach `pip install -U monolynx-cli`),
  `fetch_latest(timeout: float = 2.0) -> str | None` (None przy każdym błędzie; zapytanie przez httpx,
  bez nagłówka Authorization).
- Ścieżką instalacji komendy jest `sys.prefix` (korzeń środowiska, w którym działa CLI), nie `sys.executable`:
  `bin/python` w venv jest symlinkiem do interpretera bazowego, a korzeń venv zostaje pod katalogiem menedżera.
- Komenda nie czyta konfiguracji ani tokenu i nie wysyła ich nigdzie.

Kształt odpowiedzi PyPI pochodzi z przechwycenia `tests/fixtures/pypi/monolynx-cli-json.json`
(`curl -s https://pypi.org/pypi/monolynx-cli/json`, 2026-10-01); testy w `cli/` nie czytają
plików spoza pakietu, bo job CI kopiuje tylko `cli/`.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from monolynx_cli.commands import version as version_cmd
from monolynx_cli.main import app
from monolynx_cli.version import __version__

pytestmark = pytest.mark.unit

PYPI_URL = "https://pypi.org/pypi/monolynx-cli/json"
CHECK_KEYS = {"current", "latest", "update_available", "upgrade_command"}
PROXY_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")

BREW_PREFIX = "/opt/homebrew/Cellar/monolynx/0.2.0/libexec"
PIPX_PREFIX = "/home/u/.local/share/pipx/venvs/monolynx-cli"
UV_PREFIX = "/home/u/.local/share/uv/tools/monolynx-cli"
PLAIN_PREFIX = "/usr"

# pipx i uv na Pythonie z Homebrew: korzeń venv leży pod katalogiem menedżera, ale ścieżka zawiera też `/homebrew/`.
INSTALLER_CASES = [
    (BREW_PREFIX, "brew", "brew upgrade monolynx"),
    ("/usr/local/Cellar/monolynx/0.2.0/libexec", "brew", "brew upgrade monolynx"),
    ("/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13", "brew", "brew upgrade monolynx"),
    (PIPX_PREFIX, "pipx", "pipx upgrade monolynx-cli"),
    ("/Users/u/.local/pipx/venvs/monolynx-cli", "pipx", "pipx upgrade monolynx-cli"),
    ("/opt/homebrew/pipx/venvs/monolynx-cli", "pipx", "pipx upgrade monolynx-cli"),
    (UV_PREFIX, "uv", "uv tool upgrade monolynx-cli"),
    ("/opt/homebrew/uv/tools/monolynx-cli", "uv", "uv tool upgrade monolynx-cli"),
    (PLAIN_PREFIX, "unknown", "pip install -U monolynx-cli"),
    ("/home/u/project/.venv", "unknown", "pip install -U monolynx-cli"),
]


def pypi_payload(version: str) -> dict:
    return {"info": {"name": "monolynx-cli", "version": version}, "releases": {version: []}}


def install_pypi(monkeypatch, handler: Callable[[httpx.Request], httpx.Response]) -> list[httpx.Request]:
    """Wymusza MockTransport na każdym httpx.Client (także tym w httpx.get) i zapisuje żądania."""
    requests: list[httpx.Request] = []

    def record(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    transport = httpx.MockTransport(record)
    original_init = httpx.Client.__init__

    def patched_init(self, *args, **kwargs):
        kwargs["transport"] = transport
        original_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", patched_init)
    for name in PROXY_VARS:
        monkeypatch.delenv(name, raising=False)
    return requests


def raise_on_request(error: Exception) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        raise error

    return handler


def serve_version(version: str) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(200, json=pypi_payload(version))


def set_current_version(monkeypatch, value: str) -> None:
    # Komenda może czytać wersję z własnego importu albo z modułu `monolynx_cli.version`.
    import monolynx_cli.version as version_module

    monkeypatch.setattr(version_module, "__version__", value)
    monkeypatch.setattr(version_cmd, "__version__", value, raising=False)


def run_json(runner, *args: str):
    result = runner.invoke(app, ["-o", "json", *args])
    assert result.exit_code == 0, result.output
    return json.loads(result.stdout), result


# ---------------------------------------------------------------------------
# Wersja pakietu i rejestracja
# ---------------------------------------------------------------------------


def test_package_version_is_0_2_0():
    assert __version__ == "0.2.0"


def test_version_is_registered_as_standalone_root_command():
    import typer.main

    click_app = typer.main.get_command(app)

    assert "version" in click_app.commands
    assert not hasattr(click_app.commands["version"], "commands")


# ---------------------------------------------------------------------------
# detect_installer / upgrade_command
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("prefix", "installer", "_command"), INSTALLER_CASES)
def test_detect_installer_classifies_prefix_path(prefix, installer, _command):
    assert version_cmd.detect_installer(prefix) == installer


def make_venv_on_homebrew_python(tmp_path: Path, manager_dir: str) -> tuple[Path, Path]:
    """Układ jak po `pipx install` / `uv tool install` na Pythonie z Homebrew.

    `<venv>/bin/python` to symlink do interpretera bazowego pod `/Cellar/`, poza venv; korzeń venv leży pod
    katalogiem menedżera. Zwraca (prefix, interpreter).
    """
    base_python = tmp_path / "opt" / "homebrew" / "Cellar" / "python@3.12" / "3.12.0" / "bin" / "python3"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("", encoding="utf-8")
    prefix = tmp_path / "home" / ".local" / "share" / manager_dir / "monolynx-cli"
    (prefix / "bin").mkdir(parents=True)
    interpreter = prefix / "bin" / "python"
    interpreter.symlink_to(base_python)
    return prefix, interpreter


@pytest.mark.parametrize(
    ("manager_dir", "installer"),
    [("pipx/venvs", "pipx"), ("uv/tools", "uv")],
)
def test_detect_installer_keeps_manager_for_venv_over_homebrew_python(tmp_path, manager_dir, installer):
    prefix, interpreter = make_venv_on_homebrew_python(tmp_path, manager_dir)

    assert version_cmd.detect_installer(str(prefix)) == installer
    # Punkt odniesienia: po rozwiązaniu symlinku interpreter wygląda jak brew, stąd klasyfikacja po prefiksie.
    assert version_cmd.detect_installer(str(interpreter.resolve())) == "brew"


def test_detect_installer_does_not_resolve_symlinks(tmp_path):
    real = tmp_path / "Cellar" / "monolynx" / "0.2.0" / "libexec"
    real.mkdir(parents=True)
    link = tmp_path / "plain" / "env"
    link.parent.mkdir(parents=True)
    link.symlink_to(real)

    assert version_cmd.detect_installer(str(link)) == "unknown"


@pytest.mark.parametrize(
    ("installer", "command"),
    [
        ("brew", "brew upgrade monolynx"),
        ("pipx", "pipx upgrade monolynx-cli"),
        ("uv", "uv tool upgrade monolynx-cli"),
        ("unknown", "pip install -U monolynx-cli"),
        ("none", "pip install -U monolynx-cli"),
    ],
)
def test_upgrade_command_per_installer(installer, command):
    assert version_cmd.upgrade_command(installer) == command


# ---------------------------------------------------------------------------
# fetch_latest
# ---------------------------------------------------------------------------


class TestFetchLatest:
    def test_returns_info_version_from_pypi_response(self, monkeypatch):
        requests = install_pypi(monkeypatch, serve_version("0.1.0"))

        assert version_cmd.fetch_latest() == "0.1.0"
        assert len(requests) == 1
        assert str(requests[0].url) == PYPI_URL
        assert requests[0].method == "GET"

    def test_returns_whatever_version_pypi_reports(self, monkeypatch):
        install_pypi(monkeypatch, serve_version("3.14.15"))

        assert version_cmd.fetch_latest() == "3.14.15"

    def test_default_timeout_is_two_seconds(self, monkeypatch):
        requests = install_pypi(monkeypatch, serve_version("0.1.0"))

        version_cmd.fetch_latest()

        timeout = requests[0].extensions["timeout"]
        assert {timeout["connect"], timeout["read"], timeout["write"], timeout["pool"]} == {2.0}

    def test_custom_timeout_is_applied(self, monkeypatch):
        requests = install_pypi(monkeypatch, serve_version("0.1.0"))

        version_cmd.fetch_latest(timeout=0.5)

        timeout = requests[0].extensions["timeout"]
        assert {timeout["connect"], timeout["read"], timeout["write"], timeout["pool"]} == {0.5}

    def test_sends_no_authorization_header(self, monkeypatch):
        monkeypatch.setenv("MONOLYNX_TOKEN", "tajny-token-123")
        requests = install_pypi(monkeypatch, serve_version("0.1.0"))

        version_cmd.fetch_latest()

        assert "authorization" not in requests[0].headers
        assert "tajny-token-123" not in str(requests[0].headers)
        assert "tajny-token-123" not in str(requests[0].url)

    @pytest.mark.parametrize(
        "failure",
        [
            httpx.ConnectTimeout("timeout"),
            httpx.ReadTimeout("timeout"),
            httpx.ConnectError("offline"),
            httpx.RemoteProtocolError("boom"),
        ],
        ids=["connect-timeout", "read-timeout", "connect-error", "protocol-error"],
    )
    def test_transport_errors_return_none(self, monkeypatch, failure):
        install_pypi(monkeypatch, raise_on_request(failure))

        assert version_cmd.fetch_latest() is None

    @pytest.mark.parametrize("status", [404, 500, 503])
    def test_http_error_status_returns_none(self, monkeypatch, status):
        install_pypi(monkeypatch, lambda request: httpx.Response(status, json=pypi_payload("9.9.9")))

        assert version_cmd.fetch_latest() is None

    @pytest.mark.parametrize(
        "response",
        [
            httpx.Response(200, text="to nie jest json"),
            httpx.Response(200, json={}),
            httpx.Response(200, json={"info": {}}),
            httpx.Response(200, json={"info": {"version": 7}}),
            httpx.Response(200, json={"info": {"version": None}}),
            httpx.Response(200, json=[1, 2]),
        ],
        ids=["garbage", "empty-object", "no-version", "numeric-version", "null-version", "list"],
    )
    def test_malformed_body_returns_none(self, monkeypatch, response):
        install_pypi(monkeypatch, lambda request: response)

        assert version_cmd.fetch_latest() is None


# ---------------------------------------------------------------------------
# monolynx version
# ---------------------------------------------------------------------------


class TestVersionWithoutCheck:
    def test_prints_only_current_version_as_json(self, runner):
        data, _ = run_json(runner, "version")

        assert data == {"current": __version__}

    def test_makes_no_network_request(self, runner, monkeypatch):
        requests = install_pypi(monkeypatch, serve_version("9.9.9"))

        def forbidden(*args, **kwargs):
            raise AssertionError("fetch_latest wywołane bez --check")

        monkeypatch.setattr(version_cmd, "fetch_latest", forbidden)

        data, _ = run_json(runner, "version")

        assert data == {"current": __version__}
        assert requests == []

    def test_works_without_config_or_token(self, runner, isolated_config):
        assert not isolated_config.exists()

        data, _ = run_json(runner, "version")

        assert data["current"] == __version__

    def test_yaml_output(self, runner):
        result = runner.invoke(app, ["-o", "yaml", "version"])

        assert result.exit_code == 0
        assert result.stdout.strip() == f"current: {__version__}"

    def test_quiet_flag_keeps_data_and_silences_stderr(self, runner):
        result = runner.invoke(app, ["-q", "-o", "json", "version"])

        assert result.exit_code == 0
        assert json.loads(result.stdout) == {"current": __version__}
        assert result.stderr == ""


class TestVersionCheck:
    def test_newer_pypi_version_reports_update_available(self, runner, monkeypatch):
        set_current_version(monkeypatch, "0.2.0")
        monkeypatch.setattr(sys, "prefix", PLAIN_PREFIX)
        install_pypi(monkeypatch, serve_version("0.3.0"))

        data, _ = run_json(runner, "version", "--check")

        assert set(data) == CHECK_KEYS
        assert data["current"] == "0.2.0"
        assert data["latest"] == "0.3.0"
        assert data["update_available"] is True
        assert data["upgrade_command"] == "pip install -U monolynx-cli"

    def test_same_version_reports_no_update(self, runner, monkeypatch):
        set_current_version(monkeypatch, "0.2.0")
        install_pypi(monkeypatch, serve_version("0.2.0"))

        data, _ = run_json(runner, "version", "--check")

        assert set(data) == CHECK_KEYS
        assert data["latest"] == "0.2.0"
        assert data["update_available"] is False

    def test_older_pypi_version_reports_no_update(self, runner, monkeypatch):
        set_current_version(monkeypatch, "0.2.0")
        install_pypi(monkeypatch, serve_version("0.1.0"))

        data, _ = run_json(runner, "version", "--check")

        assert data["latest"] == "0.1.0"
        assert data["update_available"] is False

    @pytest.mark.parametrize(
        ("current", "latest", "expected"),
        [("0.9.0", "0.10.0", True), ("0.10.0", "0.9.0", False), ("1.0.0", "0.99.99", False), ("0.2.0", "0.2.1", True)],
    )
    def test_update_available_compares_versions_numerically(self, runner, monkeypatch, current, latest, expected):
        set_current_version(monkeypatch, current)
        install_pypi(monkeypatch, serve_version(latest))

        data, _ = run_json(runner, "version", "--check")

        assert data["update_available"] is expected

    @pytest.mark.parametrize(("prefix", "_installer", "command"), INSTALLER_CASES)
    def test_upgrade_command_follows_install_prefix(self, runner, monkeypatch, prefix, _installer, command):
        set_current_version(monkeypatch, "0.2.0")
        monkeypatch.setattr(sys, "prefix", prefix)
        install_pypi(monkeypatch, serve_version("0.3.0"))

        data, _ = run_json(runner, "version", "--check")

        assert data["upgrade_command"] == command

    @pytest.mark.parametrize(
        ("manager_dir", "command"),
        [("pipx/venvs", "pipx upgrade monolynx-cli"), ("uv/tools", "uv tool upgrade monolynx-cli")],
    )
    def test_real_venv_layout_on_homebrew_python_uses_manager_command(self, runner, monkeypatch, tmp_path, manager_dir, command):
        prefix, interpreter = make_venv_on_homebrew_python(tmp_path, manager_dir)
        set_current_version(monkeypatch, "0.2.0")
        monkeypatch.setattr(sys, "prefix", str(prefix))
        monkeypatch.setattr(sys, "executable", str(interpreter))
        install_pypi(monkeypatch, serve_version("0.3.0"))

        data, _ = run_json(runner, "version", "--check")

        assert data["upgrade_command"] == command

    @pytest.mark.parametrize(
        "handler",
        [
            raise_on_request(httpx.ConnectTimeout("timeout")),
            raise_on_request(httpx.ConnectError("offline")),
            lambda request: httpx.Response(500, json=pypi_payload("9.9.9")),
            lambda request: httpx.Response(200, text="nie json"),
            lambda request: httpx.Response(200, json={"info": {}}),
        ],
        ids=["timeout", "connect-error", "http-500", "bad-json", "no-version"],
    )
    def test_pypi_failure_exits_zero_with_latest_null(self, runner, monkeypatch, handler):
        set_current_version(monkeypatch, "0.2.0")
        install_pypi(monkeypatch, handler)

        result = runner.invoke(app, ["-o", "json", "version", "--check"])

        assert result.exit_code == 0
        data = json.loads(result.stdout)
        assert data["current"] == "0.2.0"
        assert data["latest"] is None
        assert data["update_available"] is False

    def test_check_uses_fetch_latest_hook(self, runner, monkeypatch):
        set_current_version(monkeypatch, "0.2.0")
        monkeypatch.setattr(version_cmd, "fetch_latest", lambda timeout=2.0: "5.0.0")

        data, _ = run_json(runner, "version", "--check")

        assert data["latest"] == "5.0.0"
        assert data["update_available"] is True

    def test_check_never_sends_token_to_pypi(self, runner, monkeypatch):
        monkeypatch.setenv("MONOLYNX_TOKEN", "tajny-token-123")
        requests = install_pypi(monkeypatch, serve_version("0.3.0"))

        result = runner.invoke(app, ["-o", "json", "version", "--check"])

        assert result.exit_code == 0
        assert len(requests) == 1
        assert "authorization" not in requests[0].headers
        assert "tajny-token-123" not in result.output

    def test_quiet_flag_keeps_data_and_silences_stderr(self, runner, monkeypatch):
        install_pypi(monkeypatch, serve_version("0.3.0"))

        result = runner.invoke(app, ["-q", "-o", "json", "version", "--check"])

        assert result.exit_code == 0
        assert set(json.loads(result.stdout)) == CHECK_KEYS
        assert result.stderr == ""

    def test_module_does_not_import_backend_package(self):
        import ast

        source = Path(version_cmd.__file__).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                assert not any(alias.name == "monolynx" or alias.name.startswith("monolynx.") for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module != "monolynx"
                assert not node.module.startswith("monolynx.")
