"""Testy plugin/scripts/monolynx_transport.py - preflight transportu skilli (MON-232).

Kontrakt skryptu (stdlib, Python 3.10+):

- `main(argv: list[str] | None = None) -> int`; argumenty: opcjonalne `--session-id ID`.
  Kod 0 zawsze, 64 przy bledzie uzycia (nieznany argument, `--session-id` bez wartosci).
- Stala modulowa `PYPI_URL = "https://pypi.org/pypi/monolynx-cli/json"` i funkcja
  `fetch_pypi_latest(timeout: float = 2.0) -> str | None` (None przy kazdym bledzie sieci,
  timeoucie, HTTP != 200, zlym JSON-ie lub braku `info.version`). Pobiera przez
  `urllib.request.urlopen(<url albo Request>, timeout=<float>)`; wolana z `main` przez
  nazwe modulowa (monkeypatchowalna).
- Cache najnowszej wersji: `~/.cache/monolynx/cli-latest.json` = `{"latest": "<wersja>"}`;
  swiezosc po mtime pliku (< 24 h = swiezy, brak sieci). Po udanym pobraniu z PyPI skrypt
  zapisuje tam `{"latest": ...}` (zapis best-effort).
- Kolejnosc ustalania `latest`: `monolynx -o json version --check` (kod 0 + JSON z `latest`),
  inaczej cache, inaczej PyPI. Rozwiazywane takze wtedy, gdy CLI nie ma w PATH.
- Wywolania CLI maja DOKLADNIE argv: `--version`; `--timeout 5 -o json auth whoami`;
  `-o json version --check` (flagi globalne przed grupa).
- Marker "podpowiedz pokazana": `~/.cache/monolynx/transport-hint/<id>`, gdzie `<id>` to
  `re.sub(r"[^A-Za-z0-9_-]", "_", session_id)`; zrodlo id: `--session-id` > env
  `CLAUDE_CODE_SESSION_ID` > staly klucz `no-session` z TTL 6 h po mtime (markery z realnym
  id nie wygasaja). Marker powstaje, gdy podpowiedz zostala wypisana.
- Ostatnia linia stdout (zamrozona):
  `TRANSPORT: mode=cli|mcp cli=<wersja|none> auth=ok|missing latest=<wersja|unknown>
  update=yes|no install=brew|pipx|uv|unknown|none`; przed nia zero albo jedna linia
  `TRANSPORT-HINT: ...` (priorytet: brak CLI, brak logowania, nowsza wersja).

Atrapa `monolynx` to wykonywalny skrypt Pythona sterowany plikiem JSON (`FAKE_SPEC`), ktory
loguje argv kazdego wywolania. Odpowiedzi atrapy pochodza z fixture'ow przechwyconych z
realnego `monolynx 0.1.0` (`tests/fixtures/monolynx/`); wyjatek: JSON `version --check`
(komenda jest dodana w CLI 0.2.0, ktore nie jest wydane, wiec nie ma przechwycenia).
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugin" / "scripts" / "monolynx_transport.py"
FIXTURES = ROOT / "tests" / "fixtures" / "monolynx"
PYPI_FIXTURE = ROOT / "tests" / "fixtures" / "pypi" / "monolynx-cli-json.json"

TRANSPORT_LINE = re.compile(
    r"^TRANSPORT: mode=(cli|mcp) cli=(\d+\.\d+\.\d+|none) auth=(ok|missing)"
    r" latest=(\d+\.\d+\.\d+|unknown) update=(yes|no) install=(brew|pipx|uv|unknown|none)$"
)

INSTALL_HINT_NO_CLI = (
    "TRANSPORT-HINT: CLI monolynx nie jest zainstalowane - pracuję przez MCP."
    " Instalacja: pipx install monolynx-cli (albo: brew install monolynx/tap/monolynx)"
)
INSTALL_HINT_NO_AUTH = "TRANSPORT-HINT: CLI monolynx nie jest zalogowane - pracuję przez MCP. Zaloguj: monolynx auth login"

# Sciezka binarki (po realpath) -> installer i komenda upgrade (kontrakt z MON-232, pkt 1).
INSTALL_LAYOUTS = {
    "brew": ("Cellar/monolynx/0.1.0/libexec/bin", "brew upgrade monolynx"),
    "pipx": ("pipx/venvs/monolynx-cli/bin", "pipx upgrade monolynx-cli"),
    "uv": ("uv/tools/monolynx-cli/bin", "uv tool upgrade monolynx-cli"),
    "unknown": ("other/bin", "pip install -U monolynx-cli"),
    # pipx/uv na Pythonie z Homebrew: w sciezce jest tez `/homebrew/`, a menedzer ma pierwszenstwo.
    "pipx-on-homebrew": ("opt/homebrew/pipx/venvs/monolynx-cli/bin", "pipx upgrade monolynx-cli"),
    "uv-on-homebrew": ("opt/homebrew/uv/tools/monolynx-cli/bin", "uv tool upgrade monolynx-cli"),
}

FAKE_TEMPLATE = """#!{python}
import json, os, sys

spec = json.load(open(os.environ["FAKE_SPEC"]))
argv = sys.argv[1:]
stdin = sys.stdin.read() if spec.get("read_stdin") else ""
with open(spec["log"], "a") as fh:
    fh.write(json.dumps({{"argv": argv, "stdin": stdin}}) + "\\n")
for route in spec["routes"]:
    if all(token in argv for token in route["match"]):
        sys.stdout.write(route.get("stdout", ""))
        sys.stderr.write(route.get("stderr", ""))
        sys.exit(route.get("code", 0))
sys.stderr.write("fake monolynx: no route for " + " ".join(argv) + "\\n")
sys.exit(97)
"""


def fixture(name: str) -> tuple[str, int]:
    """Cialo fixture'a (bez linii meta `# ...`) i kod wyjscia z linii `# exit: N`."""
    lines = (FIXTURES / f"{name}.0.1.0.txt").read_text(encoding="utf-8").splitlines()
    code = int(next(line for line in lines if line.startswith("# exit: ")).split(": ")[1])
    body = [line for line in lines if not line.startswith("# ")]
    return "\n".join(body) + "\n", code


def whoami_ok_route() -> dict:
    body, code = fixture("auth-whoami")
    assert code == 0
    return {"match": ["whoami"], "stdout": body, "code": 0}


def whoami_fail_route(code: int) -> dict:
    stderr, _ = fixture("auth-whoami-unauthenticated")
    return {"match": ["whoami"], "stderr": stderr, "code": code}


def version_flag_route() -> dict:
    body, code = fixture("version-flag")
    assert code == 0
    return {"match": ["--version"], "stdout": body, "code": 0}


def version_check_route(current: str, latest: str) -> dict:
    payload = {"current": current, "latest": latest, "update_available": latest != current, "upgrade_command": "x"}
    return {"match": ["version", "--check"], "stdout": json.dumps(payload) + "\n", "code": 0}


def version_check_missing_route() -> dict:
    # Realny przebieg CLI 0.1.0 (fixture version-check): komenda `version` nie istnieje, kod 2.
    stderr, code = fixture("version-check")
    assert code == 2
    return {"match": ["version"], "stderr": stderr, "code": 2}


class Env:
    """Izolowane srodowisko: HOME w tmp_path, atrapa `monolynx` na PATH, log wywolan."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "fake.log"
        self.spec_path = tmp_path / "spec.json"
        self.extra: dict[str, str] = {}

    def install_cli(self, routes: list[dict], layout: str = "brew", read_stdin: bool = False) -> None:
        subdir, _ = INSTALL_LAYOUTS[layout]
        real_dir = self.tmp / "prefix" / subdir
        real_dir.mkdir(parents=True)
        real = real_dir / "monolynx"
        real.write_text(FAKE_TEMPLATE.format(python=sys.executable), encoding="utf-8")
        real.chmod(0o755)
        (self.bin / "monolynx").symlink_to(real)
        self.spec_path.write_text(json.dumps({"log": str(self.log), "routes": routes, "read_stdin": read_stdin}), encoding="utf-8")

    def seed_cache(self, latest: str, age_seconds: float = 0) -> Path:
        path = self.home / ".cache" / "monolynx" / "cli-latest.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"latest": latest}), encoding="utf-8")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def marker(self, name: str) -> Path:
        return self.home / ".cache" / "monolynx" / "transport-hint" / name

    def env(self, **extra: str) -> dict[str, str]:
        base = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "FAKE_SPEC": str(self.spec_path)}
        base.update(self.extra)
        base.update(extra)
        return base

    def calls(self) -> list[list[str]]:
        if not self.log.exists():
            return []
        return [json.loads(line)["argv"] for line in self.log.read_text(encoding="utf-8").splitlines()]

    def run(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input="",
            capture_output=True,
            text=True,
            env=self.env(**env_extra),
            timeout=60,
        )


@pytest.fixture
def sandbox(tmp_path: Path) -> Env:
    return Env(tmp_path)


def parse(result: subprocess.CompletedProcess[str]) -> tuple[list[str], dict[str, str]]:
    """(linie przed linia TRANSPORT, pola linii TRANSPORT) z walidacja formatu zamrozonego."""
    lines = result.stdout.splitlines()
    assert lines, f"pusty stdout, stderr={result.stderr!r}"
    last = lines[-1]
    assert TRANSPORT_LINE.match(last), last
    fields = dict(token.split("=", 1) for token in last.removeprefix("TRANSPORT: ").split())
    return lines[:-1], fields


def hints(before: list[str]) -> list[str]:
    return [line for line in before if line.startswith("TRANSPORT-HINT:")]


def load_module():
    spec = importlib.util.spec_from_file_location("monolynx_transport_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeResponse:
    def __init__(self, payload: bytes):
        self._payload = payload

    def read(self, *_args) -> bytes:
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


# ---------------------------------------------------------------------------
# Stany: cli + auth + wersja
# ---------------------------------------------------------------------------


class TestStates:
    def test_cli_authenticated_and_current_gives_mode_cli_without_hint(self, sandbox):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.1.0")])

        result = sandbox.run("--session-id", "s-ok")

        assert result.returncode == 0
        before, fields = parse(result)
        assert before == []
        assert fields == {"mode": "cli", "cli": "0.1.0", "auth": "ok", "latest": "0.1.0", "update": "no", "install": "brew"}

    def test_missing_cli_gives_mode_mcp_install_hint_and_exact_last_line(self, sandbox):
        sandbox.seed_cache("0.2.0")

        result = sandbox.run("--session-id", "s-nocli")

        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            INSTALL_HINT_NO_CLI,
            "TRANSPORT: mode=mcp cli=none auth=missing latest=0.2.0 update=no install=none",
        ]

    @pytest.mark.parametrize("code", [1, 2, 3, 4])
    def test_whoami_failure_gives_mode_mcp_auth_missing_and_login_hint(self, sandbox, code):
        sandbox.install_cli([version_flag_route(), whoami_fail_route(code), version_check_route("0.1.0", "0.1.0")])

        result = sandbox.run("--session-id", f"s-auth-{code}")

        assert result.returncode == 0
        before, fields = parse(result)
        assert hints(before) == [INSTALL_HINT_NO_AUTH]
        assert fields["mode"] == "mcp"
        assert fields["auth"] == "missing"
        assert fields["cli"] == "0.1.0"
        assert fields["install"] == "brew"

    @pytest.mark.parametrize("layout", ["brew", "pipx", "uv", "unknown"])
    def test_outdated_cli_keeps_mode_cli_and_hints_installer_specific_upgrade(self, sandbox, layout):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.2.0")], layout=layout)

        result = sandbox.run("--session-id", f"s-old-{layout}")

        before, fields = parse(result)
        expected_cmd = INSTALL_LAYOUTS[layout][1]
        assert hints(before) == [f"TRANSPORT-HINT: Dostępna nowsza wersja CLI monolynx (0.1.0 -> 0.2.0). Aktualizacja: {expected_cmd}"]
        assert fields["mode"] == "cli"
        assert fields["auth"] == "ok"
        assert fields["update"] == "yes"
        assert fields["latest"] == "0.2.0"
        assert fields["install"] == layout

    @pytest.mark.parametrize(("layout", "installer"), [("pipx-on-homebrew", "pipx"), ("uv-on-homebrew", "uv")])
    def test_manager_venv_under_homebrew_path_is_not_taken_for_brew(self, sandbox, layout, installer):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.2.0")], layout=layout)

        result = sandbox.run("--session-id", f"s-{layout}")

        before, fields = parse(result)
        assert fields["install"] == installer
        assert hints(before) == [f"TRANSPORT-HINT: Dostępna nowsza wersja CLI monolynx (0.1.0 -> 0.2.0). Aktualizacja: {INSTALL_LAYOUTS[layout][1]}"]

    def test_not_logged_in_and_outdated_prefers_login_hint(self, sandbox):
        sandbox.install_cli([version_flag_route(), whoami_fail_route(3), version_check_route("0.1.0", "0.2.0")])

        result = sandbox.run("--session-id", "s-prio")

        before, fields = parse(result)
        assert hints(before) == [INSTALL_HINT_NO_AUTH]
        assert fields["update"] == "yes"

    @pytest.mark.parametrize(
        ("current", "latest", "expected"),
        [
            ("0.9.0", "0.10.0", "yes"),
            ("0.10.0", "0.9.0", "no"),
            ("0.2.0", "0.2.0", "no"),
            ("1.0.0", "0.9.9", "no"),
        ],
    )
    def test_update_flag_compares_versions_numerically(self, sandbox, current, latest, expected):
        sandbox.install_cli(
            [
                {"match": ["--version"], "stdout": f"{current}\n"},
                whoami_ok_route(),
                version_check_route(current, latest),
            ]
        )

        result = sandbox.run("--session-id", f"s-cmp-{current}-{latest}")

        _, fields = parse(result)
        assert fields["update"] == expected

    def test_unparsable_latest_means_update_no(self, sandbox):
        sandbox.install_cli(
            [
                version_flag_route(),
                whoami_ok_route(),
                {"match": ["version", "--check"], "stdout": json.dumps({"latest": "not-a-version"}) + "\n"},
            ]
        )

        result = sandbox.run("--session-id", "s-bad-latest")

        before, fields = parse(result)
        assert fields["update"] == "no"
        assert hints(before) == []

    def test_output_never_contains_whoami_email_or_token(self, sandbox):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.1.0")])

        result = sandbox.run("--session-id", "s-secrets")

        for stream in (result.stdout, result.stderr):
            assert "user@example.com" not in stream
            assert "POXh" not in stream
            assert "****" not in stream


# ---------------------------------------------------------------------------
# Wywolania CLI: dokladne argv
# ---------------------------------------------------------------------------


class TestCliInvocations:
    def test_calls_cli_with_exact_global_flags_before_group(self, sandbox):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.1.0")])

        sandbox.run("--session-id", "s-argv")

        calls = sandbox.calls()
        assert ["--version"] in calls
        assert ["--timeout", "5", "-o", "json", "auth", "whoami"] in calls
        assert ["-o", "json", "version", "--check"] in calls
        assert len(calls) == 3

    def test_transport_mcp_env_skips_whoami_version_check_and_network(self, sandbox):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.2.0")])

        result = sandbox.run("--session-id", "s-forced", MONOLYNX_TRANSPORT="mcp")

        assert sandbox.calls() == [["--version"]]
        assert result.stdout.splitlines() == ["TRANSPORT: mode=mcp cli=0.1.0 auth=missing latest=unknown update=no install=brew"]

    def test_transport_mcp_env_without_cli_reports_none_without_hint(self, sandbox):
        result = sandbox.run("--session-id", "s-forced-nocli", MONOLYNX_TRANSPORT="mcp")

        assert result.stdout.splitlines() == ["TRANSPORT: mode=mcp cli=none auth=missing latest=unknown update=no install=none"]
        assert not sandbox.marker("s-forced-nocli").exists()


# ---------------------------------------------------------------------------
# Marker podpowiedzi
# ---------------------------------------------------------------------------


class TestHintMarker:
    def _no_cli(self, sandbox: Env) -> None:
        sandbox.seed_cache("0.1.0")

    def test_second_call_with_same_session_id_suppresses_hint_but_keeps_transport_line(self, sandbox):
        self._no_cli(sandbox)

        first = sandbox.run("--session-id", "sess-a")
        second = sandbox.run("--session-id", "sess-a")

        assert hints(parse(first)[0]) == [INSTALL_HINT_NO_CLI]
        before, fields = parse(second)
        assert before == []
        assert fields["mode"] == "mcp"
        assert sandbox.marker("sess-a").exists()

    def test_different_session_id_shows_hint_again(self, sandbox):
        self._no_cli(sandbox)
        sandbox.run("--session-id", "sess-a")

        other = sandbox.run("--session-id", "sess-b")

        assert hints(parse(other)[0]) == [INSTALL_HINT_NO_CLI]

    def test_env_session_id_is_used_when_flag_missing(self, sandbox):
        self._no_cli(sandbox)

        first = sandbox.run(CLAUDE_CODE_SESSION_ID="env-sess")
        second = sandbox.run(CLAUDE_CODE_SESSION_ID="env-sess")
        third = sandbox.run(CLAUDE_CODE_SESSION_ID="env-sess-2")

        assert len(hints(parse(first)[0])) == 1
        assert hints(parse(second)[0]) == []
        assert len(hints(parse(third)[0])) == 1
        assert sandbox.marker("env-sess").exists()

    def test_flag_takes_precedence_over_env_session_id(self, sandbox):
        self._no_cli(sandbox)

        sandbox.run("--session-id", "from-flag", CLAUDE_CODE_SESSION_ID="from-env")

        assert sandbox.marker("from-flag").exists()
        assert not sandbox.marker("from-env").exists()

    def test_session_id_is_sanitized_to_safe_marker_name(self, sandbox):
        self._no_cli(sandbox)

        result = sandbox.run("--session-id", "../../evil id")

        assert result.returncode == 0
        assert sandbox.marker(re.sub(r"[^A-Za-z0-9_-]", "_", "../../evil id")).exists()
        assert not (sandbox.home / "evil id").exists()
        assert not (sandbox.home.parent / "evil id").exists()

    def test_named_session_marker_does_not_expire(self, sandbox):
        self._no_cli(sandbox)
        sandbox.run("--session-id", "old-sess")
        stamp = time.time() - 30 * 86400
        os.utime(sandbox.marker("old-sess"), (stamp, stamp))

        again = sandbox.run("--session-id", "old-sess")

        assert hints(parse(again)[0]) == []

    def test_no_session_id_uses_shared_key_and_second_call_is_silent(self, sandbox):
        self._no_cli(sandbox)

        first = sandbox.run()
        second = sandbox.run()

        assert len(hints(parse(first)[0])) == 1
        assert hints(parse(second)[0]) == []
        assert sandbox.marker("no-session").exists()

    def test_no_session_marker_older_than_six_hours_shows_hint_again(self, sandbox):
        self._no_cli(sandbox)
        sandbox.run()
        stamp = time.time() - 7 * 3600
        os.utime(sandbox.marker("no-session"), (stamp, stamp))

        again = sandbox.run()

        assert len(hints(parse(again)[0])) == 1

    def test_no_session_marker_younger_than_six_hours_still_suppresses(self, sandbox):
        self._no_cli(sandbox)
        sandbox.run()
        stamp = time.time() - 3600
        os.utime(sandbox.marker("no-session"), (stamp, stamp))

        again = sandbox.run()

        assert hints(parse(again)[0]) == []

    def test_sprint_run_env_never_prints_hint(self, sandbox):
        self._no_cli(sandbox)

        result = sandbox.run("--session-id", "child", MONOLYNX_SPRINT_RUN="1")

        before, fields = parse(result)
        assert before == []
        assert fields["mode"] == "mcp"
        assert fields["cli"] == "none"

    def test_sprint_run_env_silences_login_and_update_hints_too(self, sandbox):
        sandbox.install_cli([version_flag_route(), whoami_fail_route(3), version_check_route("0.1.0", "0.2.0")])

        result = sandbox.run("--session-id", "child-2", MONOLYNX_SPRINT_RUN="1")

        before, fields = parse(result)
        assert before == []
        assert fields["auth"] == "missing"
        assert fields["update"] == "yes"

    def test_unwritable_marker_dir_does_not_change_result(self, sandbox):
        self._no_cli(sandbox)
        # `~/.cache/monolynx/transport-hint` jako zwykly plik: zapis markera musi sie nie udac po cichu.
        blocker = sandbox.home / ".cache" / "monolynx" / "transport-hint"
        blocker.write_text("not a directory", encoding="utf-8")

        result = sandbox.run("--session-id", "sess-io")

        assert result.returncode == 0
        assert "Traceback" not in result.stderr
        assert hints(parse(result)[0]) == [INSTALL_HINT_NO_CLI]


# ---------------------------------------------------------------------------
# Usage
# ---------------------------------------------------------------------------


class TestUsage:
    @pytest.mark.parametrize("args", [["--bogus"], ["--session-id"], ["positional"]])
    def test_usage_error_exits_64(self, sandbox, args):
        result = sandbox.run(*args)

        assert result.returncode == 64
        assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# PyPI: fallback, cache, parsowanie (importlib + monkeypatch, bez sieci)
# ---------------------------------------------------------------------------


class TestPypiFallback:
    def _cli_without_version_command(self, sandbox: Env, monkeypatch) -> None:
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_missing_route()])
        monkeypatch.setenv("PATH", f"{sandbox.bin}:/usr/bin:/bin")
        monkeypatch.setenv("HOME", str(sandbox.home))
        monkeypatch.setenv("FAKE_SPEC", str(sandbox.spec_path))
        monkeypatch.delenv("MONOLYNX_TRANSPORT", raising=False)
        monkeypatch.delenv("MONOLYNX_SPRINT_RUN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)

    def test_cli_without_version_command_falls_back_to_pypi_latest(self, sandbox, monkeypatch, capsys):
        self._cli_without_version_command(sandbox, monkeypatch)
        module = load_module()
        monkeypatch.setattr(module, "fetch_pypi_latest", lambda *a, **k: "0.2.0")

        code = module.main(["--session-id", "pypi-1"])

        out = capsys.readouterr().out.splitlines()
        assert code == 0
        assert out[-1] == "TRANSPORT: mode=cli cli=0.1.0 auth=ok latest=0.2.0 update=yes install=brew"
        assert out[:-1] == ["TRANSPORT-HINT: Dostępna nowsza wersja CLI monolynx (0.1.0 -> 0.2.0). Aktualizacja: brew upgrade monolynx"]

    def test_pypi_failure_gives_latest_unknown_update_no_and_exit_zero(self, sandbox, monkeypatch, capsys):
        self._cli_without_version_command(sandbox, monkeypatch)
        module = load_module()
        monkeypatch.setattr(module, "fetch_pypi_latest", lambda *a, **k: None)

        code = module.main(["--session-id", "pypi-2"])

        out = capsys.readouterr().out.splitlines()
        assert code == 0
        assert out == ["TRANSPORT: mode=cli cli=0.1.0 auth=ok latest=unknown update=no install=brew"]

    def test_fresh_cache_is_used_without_fetching(self, sandbox, monkeypatch, capsys):
        self._cli_without_version_command(sandbox, monkeypatch)
        sandbox.seed_cache("0.5.0", age_seconds=3600)
        module = load_module()

        def forbidden(*_a, **_k):
            raise AssertionError("fetch_pypi_latest wolane mimo swiezego cache")

        monkeypatch.setattr(module, "fetch_pypi_latest", forbidden)

        module.main(["--session-id", "pypi-3"])

        assert "latest=0.5.0 update=yes" in capsys.readouterr().out

    def test_stale_cache_is_ignored_and_refreshed_from_pypi(self, sandbox, monkeypatch, capsys):
        self._cli_without_version_command(sandbox, monkeypatch)
        cache = sandbox.seed_cache("0.5.0", age_seconds=25 * 3600)
        module = load_module()
        monkeypatch.setattr(module, "fetch_pypi_latest", lambda *a, **k: "0.9.0")

        module.main(["--session-id", "pypi-4"])

        assert "latest=0.9.0 update=yes" in capsys.readouterr().out
        assert json.loads(cache.read_text(encoding="utf-8"))["latest"] == "0.9.0"

    def test_cache_younger_than_24h_boundary_side_is_fresh(self, sandbox, monkeypatch, capsys):
        self._cli_without_version_command(sandbox, monkeypatch)
        sandbox.seed_cache("0.5.0", age_seconds=23 * 3600)
        module = load_module()
        monkeypatch.setattr(module, "fetch_pypi_latest", lambda *a, **k: "0.9.0")

        module.main(["--session-id", "pypi-5"])

        assert "latest=0.5.0" in capsys.readouterr().out

    def test_missing_cli_still_resolves_latest_from_pypi(self, sandbox, monkeypatch, capsys):
        monkeypatch.setenv("PATH", f"{sandbox.bin}:/usr/bin:/bin")
        monkeypatch.setenv("HOME", str(sandbox.home))
        monkeypatch.delenv("MONOLYNX_TRANSPORT", raising=False)
        monkeypatch.delenv("MONOLYNX_SPRINT_RUN", raising=False)
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
        module = load_module()
        monkeypatch.setattr(module, "fetch_pypi_latest", lambda *a, **k: "0.7.0")

        module.main(["--session-id", "pypi-6"])

        out = capsys.readouterr().out.splitlines()
        assert out[-1] == "TRANSPORT: mode=mcp cli=none auth=missing latest=0.7.0 update=no install=none"


class TestFetchPypiLatest:
    def test_pypi_url_constant_points_at_monolynx_cli_json(self):
        assert load_module().PYPI_URL == "https://pypi.org/pypi/monolynx-cli/json"

    def test_parses_info_version_from_real_pypi_fixture(self, monkeypatch):
        module = load_module()
        payload = PYPI_FIXTURE.read_bytes()
        seen: dict = {}

        def fake_urlopen(target, *args, **kwargs):
            seen["target"] = getattr(target, "full_url", target)
            seen["timeout"] = kwargs.get("timeout", args[1] if len(args) > 1 else None)
            return FakeResponse(payload)

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        assert module.fetch_pypi_latest() == json.loads(payload)["info"]["version"] == "0.1.0"
        assert seen["target"] == "https://pypi.org/pypi/monolynx-cli/json"
        assert seen["timeout"] == 2.0

    def test_custom_timeout_is_passed_to_urlopen(self, monkeypatch):
        module = load_module()
        seen: dict = {}

        def fake_urlopen(_target, *args, **kwargs):
            seen["timeout"] = kwargs.get("timeout", args[1] if len(args) > 1 else None)
            return FakeResponse(PYPI_FIXTURE.read_bytes())

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

        module.fetch_pypi_latest(timeout=0.5)

        assert seen["timeout"] == 0.5

    @pytest.mark.parametrize(
        "failure",
        [OSError("boom"), TimeoutError("slow"), ValueError("bad")],
        ids=["oserror", "timeout", "valueerror"],
    )
    def test_network_errors_return_none(self, monkeypatch, failure):
        module = load_module()

        def raising(*_a, **_k):
            raise failure

        monkeypatch.setattr("urllib.request.urlopen", raising)

        assert module.fetch_pypi_latest() is None

    def test_url_error_returns_none(self, monkeypatch):
        import urllib.error

        module = load_module()

        def raising(*_a, **_k):
            raise urllib.error.URLError("offline")

        monkeypatch.setattr("urllib.request.urlopen", raising)

        assert module.fetch_pypi_latest() is None

    @pytest.mark.parametrize(
        "payload",
        [b"not json", b"{}", b'{"info": {}}', b'{"info": {"version": 5}}', b"[]"],
        ids=["garbage", "empty-object", "no-version", "non-string-version", "list"],
    )
    def test_malformed_payload_returns_none(self, monkeypatch, payload):
        module = load_module()
        monkeypatch.setattr("urllib.request.urlopen", lambda *_a, **_k: FakeResponse(payload))

        assert module.fetch_pypi_latest() is None

    @pytest.mark.parametrize(
        "failure",
        [http.client.IncompleteRead(b""), http.client.BadStatusLine("x")],
        ids=["incomplete-read", "bad-status-line"],
    )
    def test_http_client_exceptions_return_none(self, monkeypatch, failure):
        module = load_module()

        def raising(*_a, **_k):
            raise failure

        monkeypatch.setattr("urllib.request.urlopen", raising)

        assert module.fetch_pypi_latest() is None


def inproc_module(sandbox: Env, monkeypatch, routes: list[dict] | None = None):
    """Moduł wczytany w procesie testu, z HOME w tmp i (opcjonalnie) atrapą `monolynx` na PATH."""
    if routes is not None:
        sandbox.install_cli(routes)
    monkeypatch.setenv("PATH", f"{sandbox.bin}:/usr/bin:/bin")
    monkeypatch.setenv("HOME", str(sandbox.home))
    monkeypatch.setenv("FAKE_SPEC", str(sandbox.spec_path))
    for name in ("MONOLYNX_TRANSPORT", "MONOLYNX_SPRINT_RUN", "CLAUDE_CODE_SESSION_ID"):
        monkeypatch.delenv(name, raising=False)
    return load_module()


def stub_pypi(monkeypatch, module, value: str | None) -> list[int]:
    """Podmienia fetch_pypi_latest i zwraca licznik wywołań (lista długości = liczba wywołań)."""
    calls: list[int] = []

    def fake(*_a, **_k):
        calls.append(1)
        return value

    monkeypatch.setattr(module, "fetch_pypi_latest", fake)
    return calls


def time_out_cli_call(monkeypatch, module, *tokens: str) -> None:
    """subprocess.run rzuca TimeoutExpired dla wywołań CLI, których argv zawiera wszystkie `tokens`; reszta idzie do atrapy."""
    original = module.subprocess.run

    def run(args, *a, **k):
        if all(token in args[1:] for token in tokens):
            raise subprocess.TimeoutExpired(args, k.get("timeout", 0))
        return original(args, *a, **k)

    monkeypatch.setattr(module.subprocess, "run", run)


class TestCliProcessFailures:
    def test_version_flag_timeout_means_no_cli(self, sandbox, monkeypatch, capsys):
        routes = [version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.1.0")]
        module = inproc_module(sandbox, monkeypatch, routes)
        stub_pypi(monkeypatch, module, None)
        time_out_cli_call(monkeypatch, module, "--version")

        code = module.main(["--session-id", "fail-version-timeout"])

        assert code == 0
        assert capsys.readouterr().out.splitlines() == [
            INSTALL_HINT_NO_CLI,
            "TRANSPORT: mode=mcp cli=none auth=missing latest=unknown update=no install=none",
        ]

    def test_whoami_timeout_means_auth_missing(self, sandbox, monkeypatch, capsys):
        routes = [version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.1.0")]
        module = inproc_module(sandbox, monkeypatch, routes)
        time_out_cli_call(monkeypatch, module, "whoami")

        code = module.main(["--session-id", "fail-whoami-timeout"])

        assert code == 0
        assert capsys.readouterr().out.splitlines() == [
            INSTALL_HINT_NO_AUTH,
            "TRANSPORT: mode=mcp cli=0.1.0 auth=missing latest=0.1.0 update=no install=brew",
        ]

    def test_version_check_timeout_falls_back_to_fresh_cache(self, sandbox, monkeypatch, capsys):
        routes = [version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.9.0")]
        module = inproc_module(sandbox, monkeypatch, routes)
        sandbox.seed_cache("0.5.0")
        stub_pypi(monkeypatch, module, None)
        time_out_cli_call(monkeypatch, module, "version", "--check")

        module.main(["--session-id", "fail-check-timeout"])

        assert capsys.readouterr().out.splitlines()[-1] == "TRANSPORT: mode=cli cli=0.1.0 auth=ok latest=0.5.0 update=yes install=brew"

    @pytest.mark.parametrize("error", [PermissionError("brak praw"), FileNotFoundError("zniknelo")], ids=["permission", "not-found"])
    def test_cli_that_cannot_start_means_no_cli(self, sandbox, monkeypatch, capsys, error):
        module = inproc_module(sandbox, monkeypatch, [version_flag_route(), whoami_ok_route()])
        stub_pypi(monkeypatch, module, None)

        def broken_run(*_a, **_k):
            raise error

        monkeypatch.setattr(module.subprocess, "run", broken_run)

        code = module.main(["--session-id", "fail-start"])

        assert code == 0
        assert capsys.readouterr().out.splitlines() == [
            INSTALL_HINT_NO_CLI,
            "TRANSPORT: mode=mcp cli=none auth=missing latest=unknown update=no install=none",
        ]


class TestLatestSources:
    @pytest.mark.parametrize(
        "content",
        ["not json", "[]", '{"latest": 5}', "{}", '{"latest": null}'],
        ids=["garbage", "list", "non-string", "empty-object", "null"],
    )
    def test_corrupt_cache_is_treated_as_missing_and_pypi_is_asked(self, sandbox, monkeypatch, capsys, content):
        module = inproc_module(sandbox, monkeypatch)
        cache = sandbox.seed_cache("0.1.0")
        cache.write_text(content, encoding="utf-8")
        calls = stub_pypi(monkeypatch, module, "0.9.0")

        module.main(["--session-id", "cache-corrupt"])

        assert "latest=0.9.0" in capsys.readouterr().out
        assert calls == [1]

    def test_directory_in_place_of_cache_file_is_treated_as_missing(self, sandbox, monkeypatch, capsys):
        module = inproc_module(sandbox, monkeypatch)
        (sandbox.home / ".cache" / "monolynx" / "cli-latest.json").mkdir(parents=True)
        calls = stub_pypi(monkeypatch, module, "0.9.0")

        code = module.main(["--session-id", "cache-dir"])

        assert code == 0
        assert "latest=0.9.0" in capsys.readouterr().out
        assert calls == [1]

    def test_unwritable_cache_dir_still_reports_latest_from_pypi(self, sandbox, monkeypatch, capsys):
        module = inproc_module(sandbox, monkeypatch)
        # `~/.cache/monolynx` jako zwykly plik: ani odczyt, ani zapis cache nie moze sie udac.
        blocker = sandbox.home / ".cache" / "monolynx"
        blocker.parent.mkdir(parents=True)
        blocker.write_text("not a directory", encoding="utf-8")
        stub_pypi(monkeypatch, module, "0.9.0")

        code = module.main(["--session-id", "cache-unwritable"])

        assert code == 0
        assert "latest=0.9.0" in capsys.readouterr().out
        assert blocker.read_text(encoding="utf-8") == "not a directory"

    def test_successful_pypi_fetch_writes_cache(self, sandbox, monkeypatch, capsys):
        module = inproc_module(sandbox, monkeypatch)
        stub_pypi(monkeypatch, module, "0.9.0")

        module.main(["--session-id", "cache-write"])

        cache = sandbox.home / ".cache" / "monolynx" / "cli-latest.json"
        assert json.loads(cache.read_text(encoding="utf-8")) == {"latest": "0.9.0"}
        assert "latest=0.9.0" in capsys.readouterr().out

    @pytest.mark.parametrize(
        "stdout",
        ["to nie json\n", "[1, 2]\n", '"tekst"\n', '{"latest": null}\n', "{}\n", '{"latest": 7}\n'],
        ids=["garbage", "list", "string", "null-latest", "empty-object", "numeric-latest"],
    )
    def test_version_check_with_unusable_json_falls_back_to_cache(self, sandbox, monkeypatch, capsys, stdout):
        routes = [version_flag_route(), whoami_ok_route(), {"match": ["version", "--check"], "stdout": stdout, "code": 0}]
        module = inproc_module(sandbox, monkeypatch, routes)
        sandbox.seed_cache("0.5.0")
        calls = stub_pypi(monkeypatch, module, "0.9.0")

        module.main(["--session-id", "check-unusable"])

        assert "latest=0.5.0" in capsys.readouterr().out
        assert calls == []

    def test_version_check_nonzero_exit_with_valid_json_is_ignored(self, sandbox, monkeypatch, capsys):
        routes = [version_flag_route(), whoami_ok_route(), {**version_check_route("0.1.0", "0.8.0"), "code": 1}]
        module = inproc_module(sandbox, monkeypatch, routes)
        sandbox.seed_cache("0.5.0")
        stub_pypi(monkeypatch, module, None)

        module.main(["--session-id", "check-exit-1"])

        assert "latest=0.5.0" in capsys.readouterr().out

    def test_pypi_version_that_is_not_numeric_is_not_cached_and_gives_unknown(self, sandbox, monkeypatch, capsys):
        module = inproc_module(sandbox, monkeypatch)
        stub_pypi(monkeypatch, module, "dev")

        module.main(["--session-id", "pypi-dev"])

        assert not (sandbox.home / ".cache" / "monolynx" / "cli-latest.json").exists()
        assert capsys.readouterr().out.splitlines()[-1] == "TRANSPORT: mode=mcp cli=none auth=missing latest=unknown update=no install=none"


class TestSessionKeyAndArguments:
    def _no_cli(self, sandbox: Env) -> None:
        sandbox.seed_cache("0.1.0")

    def test_blank_session_id_flag_falls_back_to_shared_key(self, sandbox):
        self._no_cli(sandbox)

        sandbox.run("--session-id", " ")

        assert sandbox.marker("no-session").exists()
        assert not sandbox.marker("_").exists()

    def test_blank_env_session_id_falls_back_to_shared_key(self, sandbox):
        self._no_cli(sandbox)

        sandbox.run(CLAUDE_CODE_SESSION_ID="  ")

        assert sandbox.marker("no-session").exists()

    def test_last_session_id_flag_wins(self, sandbox):
        self._no_cli(sandbox)

        sandbox.run("--session-id", "first", "--session-id", "second")

        assert sandbox.marker("second").exists()
        assert not sandbox.marker("first").exists()

    def test_session_id_equals_form_is_a_usage_error(self, sandbox):
        result = sandbox.run("--session-id=abc")

        assert result.returncode == 64
        assert "Traceback" not in result.stderr
        assert not sandbox.marker("abc").exists()

    @pytest.mark.parametrize("stdout_code", [("dev\n", 0), ("0.1.0\n", 1), ("", 0)], ids=["no-digits", "nonzero-exit", "empty"])
    def test_version_output_without_parsable_version_means_no_cli(self, sandbox, stdout_code):
        stdout, code = stdout_code
        self._no_cli(sandbox)
        sandbox.install_cli([{"match": ["--version"], "stdout": stdout, "code": code}, whoami_ok_route()])

        result = sandbox.run("--session-id", "ver-unparsable")

        before, fields = parse(result)
        assert hints(before) == [INSTALL_HINT_NO_CLI]
        assert fields["cli"] == "none"
        assert fields["install"] == "none"
        assert fields["mode"] == "mcp"
        assert sandbox.calls() == [["--version"]]

    @pytest.mark.parametrize(
        ("stdout", "expected"),
        [("0.2.0rc1\n", "0.2.0"), ("monolynx 0.1.0\n", "0.1.0"), ("monolynx, version 3.14.15 (build 7)\n", "3.14.15")],
    )
    def test_version_is_extracted_from_surrounding_text(self, sandbox, stdout, expected):
        self._no_cli(sandbox)
        sandbox.install_cli([{"match": ["--version"], "stdout": stdout}, whoami_ok_route(), version_check_route(expected, expected)])

        result = sandbox.run("--session-id", f"ver-{expected}")

        _, fields = parse(result)
        assert fields["cli"] == expected
        assert fields["mode"] == "cli"


class TestTransportEnvValue:
    @pytest.mark.parametrize("value", [" MCP ", "mcp", "Mcp"])
    def test_mcp_value_is_case_and_whitespace_insensitive(self, sandbox, value):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.1.0")])

        result = sandbox.run("--session-id", "env-mcp", MONOLYNX_TRANSPORT=value)

        _, fields = parse(result)
        assert fields["mode"] == "mcp"
        assert fields["auth"] == "missing"
        assert sandbox.calls() == [["--version"]]

    @pytest.mark.parametrize("value", ["cli", "", "mcpx"])
    def test_other_values_do_not_force_mcp(self, sandbox, value):
        sandbox.install_cli([version_flag_route(), whoami_ok_route(), version_check_route("0.1.0", "0.1.0")])

        result = sandbox.run("--session-id", "env-other", MONOLYNX_TRANSPORT=value)

        _, fields = parse(result)
        assert fields["mode"] == "cli"
        assert fields["auth"] == "ok"

    @pytest.mark.parametrize("value", ["true", "0", "", "11"])
    def test_sprint_run_values_other_than_one_keep_the_hint(self, sandbox, value):
        sandbox.seed_cache("0.1.0")

        result = sandbox.run("--session-id", f"sprint-{value or 'empty'}", MONOLYNX_SPRINT_RUN=value)

        before, _ = parse(result)
        assert hints(before) == [INSTALL_HINT_NO_CLI]
