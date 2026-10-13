r"""Testy plugin/scripts/critic_backend.py - wybor backendu krytyka (Codex albo Claude) i uruchomienie `codex exec` (MON-274).

Testy czarnoskrzynkowe (atrapa `codex` w PATH, subprocess) oraz w procesie (importlib + monkeypatch) tam, gdzie kontrakt wymaga
czasu albo zdarzenia niedostepnego z zewnatrz (timeout, SIGKILL, limity domyslne, bledy odczytu plikow, ktore pod rootem nie zachodza).

Kontrakt (stdlib, Python 3.10+, `main(argv=None) -> int`, nigdy traceback):

`preflight` (takze wywolanie bez argumentow), zawsze kod 0:
- sondy: `shutil.which("codex")`, `codex --version`, `codex login status`; zalogowany = kod 0 i
  case-sensitive `Logged in` w stdout+stderr sondy,
- ostatnia linia stdout (zamrozona):
  `CRITIC: backend=codex|claude codex=<wersja|none> auth=ok|missing model=<id|inherit>`,
- w trybie wykrywania `backend=codex` tylko gdy: binarka jest, wersja odczytana, zalogowany, model poprawny; w trybie wymuszonym
  (`MONOLYNX_CRITIC=codex`) gdy binarka jest i model poprawny, niezaleznie od sond; wylacznik `claude` zawsze daje Claude,
- `MONOLYNX_CRITIC` (po strip().lower()): pusta/`auto` -> detekcja (`backend=codex` tylko gdy sondy potwierdzily Codex);
  `codex` -> tryb WYMUSZONY (`backend=codex`, gdy binarka jest i model poprawny, niezaleznie od sond; niepotwierdzone sondy
  daja jedna linie `CRITIC-HINT: Tryb wymuszony (MONOLYNX_CRITIC=codex): <powody po "; "> - krytykiem pozostaje Codex.`);
  `claude` oraz kazda inna wartosc -> `backend=claude` (sondy nadal sie wykonuja, wiec `codex=` i `auth=` sa prawdziwe),
- `MONOLYNX_CRITIC_CODEX_MODEL`: pusta -> `model=inherit`, poprawna wg `^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$` -> `model=<id>`,
  niepoprawna -> `backend=claude`, `model=inherit`,
- przy `backend=claude` PRZED zamrozona linia idzie dokladnie jedna linia `CRITIC-HINT: <powod po polsku>`; przy `backend=codex`
  brak HINT, z wyjatkiem trybu wymuszonego z niepotwierdzonymi sondami (dokladnie jedna linia HINT),
- nieznana podkomenda albo flaga -> kod 64 i komunikat na stderr.

`run --prompt-file P --out O --cwd D [--timeout S]`:
- `--timeout` calkowite 30..3600 (domyslnie 900); poza zakresem, nie-liczba, brak wymaganej flagi, nieznana flaga -> kod 64,
  stderr `critic_backend: <powod>`, nic nie jest uruchamiane ani kasowane, linii `CRITIC-RUN:` brak,
- bledy wejscia (prompt nie istnieje / katalog / nieczytelny / nie UTF-8 / pusty; `--cwd` nie katalog; brak katalogu nadrzednego
  `--out`; `--out` katalog; `--out` == prompt) -> kod 2, `reason=input`, zero efektow ubocznych, Codex nie wolany,
- `MONOLYNX_CRITIC=claude` (albo nieznana wartosc) -> kod 2, `reason=disabled`; brak binarki -> `reason=missing`;
  niepoprawny model -> `reason=input`,
- stary plik `--out` jest usuwany przed startem,
- argv, w tej kolejnosci: `codex exec -C <cwd> --sandbox read-only -c model_reasoning_effort="high" [-m <model>] -o <out> -`,
  prompt na stdin, bez `--worktree` i `--json`,
- timeout: killpg SIGTERM, do 5 s czekania, SIGKILL; kod 2, `reason=timeout`, brak pliku out,
- niezerowy kod codex -> kod 2, `reason=exit`, brak pliku out,
- werdykt: out istnieje, UTF-8, zawiera `**Code Review**` ORAZ dopasowanie `\b(\d{1,3})/100\b` z wartoscia 0..100; inaczej kod 2,
  `reason=verdict`, tresc w `<out>.rejected`, brak `<out>`; skrypt NIGDY nie interpretuje APPROVED/NEEDS WORK,
- ostatnia linia stdout: `CRITIC-RUN: status=ok out=<sciezka>` (kod 0) albo `CRITIC-RUN: status=error reason=<...>` (kod 2).

Czego oczekuja testy w procesie od implementacji (stale modulowe czytane w chwili uzycia, nie wpisane literalem):
- `MIN_TIMEOUT` (30) - walidacja `--timeout` (dolna granica 30 s uniemozliwia test timeoutu czarnoskrzynkowo),
- `DEFAULT_TIMEOUT` (900) - wartosc domyslna `--timeout`, przekazywana do oczekiwania na proces,
- `KILL_GRACE_SECONDS` (5) - czas miedzy SIGTERM a SIGKILL dla grupy procesow,
- `_PROBE_TIMEOUT` (30) - limit kazdej sondy preflightu (i liczba sekund w HINT-ach sond bez odpowiedzi).

Atrapa `codex` to wykonywalny skrypt Pythona sterowany plikiem JSON (`FAKE_SPEC`), ktory loguje argv i stdin kazdego wywolania.
Odpowiedzi atrapy pochodza z fixture'ow `tests/fixtures/codex/*.0.160.0.*` (przechwycone z realnego codex-cli 0.160.0, patrz README
tego katalogu); wyjatki: wyjscie `--version` (przechwycone, opisane w README) i stan "niezalogowany" (nieprzechwycony - wymagalby
wylogowania konta; ksztalt z kontraktu).
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "plugin" / "scripts" / "critic_backend.py"
FIXTURES = ROOT / "tests" / "fixtures" / "codex"

# WHY: tresc `codex login status` i werdykt `-o` z realnego przebiegu 0.160.0 (README fixture'ow).
LOGIN_STATUS = (FIXTURES / "login-status.0.160.0.txt").read_text(encoding="utf-8")
VERDICT = (FIXTURES / "exec-critic.0.160.0.stdout.txt").read_text(encoding="utf-8")
EXEC_HELP = (FIXTURES / "exec-help.0.160.0.txt").read_text(encoding="utf-8")
# WHY: `codex --version` -> `codex-cli 0.160.0` (README fixture'ow, sekcja 0.160.0); id modelu `gpt-6-sol` z naglowka stderr
# realnego przebiegu `codex exec` (README).
VERSION_STDOUT = "codex-cli 0.160.0\n"
VERSION = "0.160.0"
MODEL_ID = "gpt-6-sol"

CRITIC_LINE = re.compile(
    r"^CRITIC: backend=(codex|claude) codex=(\d+\.\d+\.\d+|none) auth=(ok|missing)"
    r" model=([A-Za-z0-9][A-Za-z0-9._:-]{0,63}|inherit)$"
)
RUN_OK_LINE = re.compile(r"^CRITIC-RUN: status=ok out=(.+)$")
RUN_ERROR_LINE = re.compile(r"^CRITIC-RUN: status=error reason=(timeout|exit|verdict|input|disabled|missing)$")

FAKE_CODEX = r"""#!__PYTHON__
import base64, json, os, signal, subprocess, sys, time

spec = json.load(open(os.environ["FAKE_SPEC"]))
argv = sys.argv[1:]
is_exec = bool(argv) and argv[0] == "exec"
stdin = sys.stdin.read() if is_exec else ""
with open(spec["log"], "a") as fh:
    fh.write(json.dumps({"argv": argv, "stdin": stdin}) + "\n")
if argv == ["--version"]:
    time.sleep(spec.get("version_sleep", 0))
    sys.stdout.write(spec["version_stdout"])
    sys.exit(spec["version_code"])
if argv == ["login", "status"]:
    time.sleep(spec.get("login_sleep", 0))
    sys.stdout.write(spec["login_stdout"])
    sys.stderr.write(spec["login_stderr"])
    sys.exit(spec["login_code"])
if is_exec:
    if spec.get("pid_file"):
        open(spec["pid_file"], "w").write(str(os.getpid()))
    if spec.get("child_pid_file"):
        child = subprocess.Popen(["sleep", "300"])
        open(spec["child_pid_file"], "w").write(str(child.pid))
    if spec.get("ignore_term"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    sys.stdout.write("CODEX-NOISE-STDOUT\n")
    sys.stderr.write("\x1b[31mCODEX-NOISE-STDERR\x1b[0m\n")
    for number in range(1, spec.get("stderr_lines", 0) + 1):
        sys.stderr.write("tail-line-%d\n" % number)
    out = argv[argv.index("-o") + 1]
    if spec.get("verdict_b64") is not None:
        open(out, "wb").write(base64.b64decode(spec["verdict_b64"]))
        if spec.get("out_mode") is not None:
            os.chmod(out, spec["out_mode"])
    if spec.get("sleep"):
        time.sleep(spec["sleep"])
    sys.exit(spec["exec_code"])
sys.stderr.write("fake codex: no route for " + " ".join(argv) + "\n")
sys.exit(97)
"""


def b64(text: str | bytes) -> str:
    raw = text.encode("utf-8") if isinstance(text, str) else text
    return base64.b64encode(raw).decode("ascii")


class Sandbox:
    """Izolowane srodowisko: HOME w tmp_path, atrapa `codex` w PATH (albo bez niej), log wywolan."""

    def __init__(self, tmp_path: Path):
        self.tmp = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.log = tmp_path / "fake.log"
        self.spec_path = tmp_path / "spec.json"
        self.work = tmp_path / "work"
        self.work.mkdir()
        self.prompt = tmp_path / "prompt.txt"
        self.prompt.write_text("Zrecenzuj zmiane.\nDrugi wiersz promptu z polskimi znakami: zażółć gęślą jaźń.\n", encoding="utf-8")
        self.out = tmp_path / "verdict.md"

    def install(self, **overrides: object) -> None:
        spec: dict[str, object] = {
            "log": str(self.log),
            "version_stdout": VERSION_STDOUT,
            "version_code": 0,
            "login_stdout": LOGIN_STATUS,
            "login_stderr": "",
            "login_code": 0,
            "exec_code": 0,
            "verdict_b64": b64(VERDICT),
        }
        spec.update(overrides)
        codex = self.bin / "codex"
        codex.write_text(FAKE_CODEX.replace("__PYTHON__", sys.executable), encoding="utf-8")
        codex.chmod(0o755)
        self.spec_path.write_text(json.dumps(spec), encoding="utf-8")

    def env(self, **extra: str) -> dict[str, str]:
        base = {"PATH": f"{self.bin}:/usr/bin:/bin", "HOME": str(self.home), "FAKE_SPEC": str(self.spec_path)}
        base.update(extra)
        return base

    def calls(self) -> list[dict]:
        if not self.log.exists():
            return []
        return [json.loads(line) for line in self.log.read_text(encoding="utf-8").splitlines()]

    def exec_calls(self) -> list[dict]:
        return [call for call in self.calls() if call["argv"][:1] == ["exec"]]

    def run_args(self, *, timeout: str | None = None) -> list[str]:
        args = ["run", "--prompt-file", str(self.prompt), "--out", str(self.out), "--cwd", str(self.work)]
        if timeout is not None:
            args += ["--timeout", timeout]
        return args

    def invoke(self, *args: str, **env_extra: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *args],
            input="",
            capture_output=True,
            text=True,
            env=self.env(**env_extra),
            timeout=60,
        )


@pytest.fixture
def sandbox(tmp_path: Path) -> Sandbox:
    box = Sandbox(tmp_path)
    box.install()
    return box


def no_traceback(result: subprocess.CompletedProcess[str]) -> None:
    assert "Traceback" not in result.stderr
    assert "Traceback" not in result.stdout


def parse_preflight(result: subprocess.CompletedProcess[str]) -> tuple[list[str], dict[str, str]]:
    """(linie przed linia CRITIC, pola linii CRITIC) z walidacja formatu zamrozonego."""
    assert result.returncode == 0, result.stderr
    no_traceback(result)
    lines = result.stdout.splitlines()
    assert lines, f"pusty stdout, stderr={result.stderr!r}"
    last = lines[-1]
    assert CRITIC_LINE.match(last), last
    assert sum(1 for line in lines if line.startswith("CRITIC: ")) == 1
    fields = dict(token.split("=", 1) for token in last.removeprefix("CRITIC: ").split())
    return lines[:-1], fields


def hints(before: list[str]) -> list[str]:
    return [line for line in before if line.startswith("CRITIC-HINT:")]


def last_run_line(result: subprocess.CompletedProcess[str]) -> str:
    lines = result.stdout.splitlines()
    assert lines, f"pusty stdout, stderr={result.stderr!r}"
    return lines[-1]


def assert_run_error(result: subprocess.CompletedProcess[str], reason: str) -> None:
    assert result.returncode == 2, (result.stdout, result.stderr)
    no_traceback(result)
    assert last_run_line(result) == f"CRITIC-RUN: status=error reason={reason}"
    assert result.stderr.strip(), "kod 2 niesie komunikat na stderr"


def assert_usage_error(sandbox: Sandbox, result: subprocess.CompletedProcess[str]) -> None:
    assert result.returncode == 64, (result.stdout, result.stderr)
    no_traceback(result)
    assert result.stderr.startswith("critic_backend: ")
    assert "CRITIC-RUN:" not in result.stdout
    assert sandbox.exec_calls() == []


# ---------------------------------------------------------------------------
# preflight: stany
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args", [(), ("preflight",)])
def test_preflight_installed_and_logged_in_selects_codex(sandbox, args):
    result = sandbox.invoke(*args)

    before, fields = parse_preflight(result)

    assert fields == {"backend": "codex", "codex": VERSION, "auth": "ok", "model": "inherit"}
    assert hints(before) == []
    assert result.stdout.splitlines()[-1].startswith("CRITIC: ")


def test_preflight_without_binary_selects_claude_with_single_hint_before_frozen_line(tmp_path):
    box = Sandbox(tmp_path)  # brak atrapy `codex` w PATH

    result = box.invoke("preflight")

    before, fields = parse_preflight(result)
    assert fields["backend"] == "claude"
    assert fields["codex"] == "none"
    assert fields["model"] == "inherit"
    assert len(hints(before)) == 1
    assert result.stdout.splitlines()[-2].startswith("CRITIC-HINT: ")
    assert box.calls() == []


def test_preflight_version_command_failing_selects_claude(tmp_path):
    box = Sandbox(tmp_path)
    box.install(version_code=1)

    before, fields = parse_preflight(box.invoke("preflight"))

    assert fields["backend"] == "claude"
    assert fields["codex"] == "none"
    assert len(hints(before)) == 1


def test_preflight_unreadable_version_selects_claude_with_codex_none(tmp_path):
    box = Sandbox(tmp_path)
    box.install(version_stdout="to nie jest numer wersji\n")

    before, fields = parse_preflight(box.invoke("preflight"))

    assert fields["backend"] == "claude"
    assert fields["codex"] == "none"
    assert len(hints(before)) == 1


@pytest.mark.parametrize(
    ("login_stdout", "login_stderr", "login_code"),
    [
        pytest.param("Not logged in\n", "", 1, id="not-logged-in-nonzero"),
        pytest.param("Not logged in\n", "", 0, id="not-logged-in-code-zero"),
        pytest.param("Zalogowano przez ChatGPT\n", "", 0, id="code-zero-without-phrase"),
        pytest.param("logged in using ChatGPT\n", "", 0, id="phrase-is-case-sensitive"),
        pytest.param(LOGIN_STATUS, "", 1, id="phrase-present-but-nonzero-code"),
    ],
)
def test_preflight_not_logged_in_selects_claude_with_auth_missing(tmp_path, login_stdout, login_stderr, login_code):
    box = Sandbox(tmp_path)
    box.install(login_stdout=login_stdout, login_stderr=login_stderr, login_code=login_code)

    before, fields = parse_preflight(box.invoke("preflight"))

    assert fields == {"backend": "claude", "codex": VERSION, "auth": "missing", "model": "inherit"}
    assert len(hints(before)) == 1


def test_preflight_logged_in_phrase_on_stderr_counts(tmp_path):
    box = Sandbox(tmp_path)
    box.install(login_stdout="", login_stderr=LOGIN_STATUS)

    _before, fields = parse_preflight(box.invoke("preflight"))

    assert fields["backend"] == "codex"
    assert fields["auth"] == "ok"


# ---------------------------------------------------------------------------
# preflight: wylacznik MONOLYNX_CRITIC
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["claude", "CLAUDE", "  Claude  ", "claude\n"])
def test_preflight_disabled_switch_forces_claude_but_keeps_true_probe_fields(sandbox, value):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC=value))

    assert fields == {"backend": "claude", "codex": VERSION, "auth": "ok", "model": "inherit"}
    assert before == [HINT_SWITCH_CLAUDE]


@pytest.mark.parametrize(("value", "shown"), [("gemini", "gemini"), ("0", "0"), ("off", "off"), ("claud", "claud"), (" Gemini ", "Gemini")])
def test_preflight_unknown_switch_value_maps_to_safe_claude_with_value_in_hint(sandbox, value, shown):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC=value))

    assert fields["backend"] == "claude"
    assert before == [f"CRITIC-HINT: Nieznana wartość MONOLYNX_CRITIC={shown!r} (dozwolone: auto, codex, claude) - krytykiem będzie Claude."]


@pytest.mark.parametrize("value", ["", "auto", "  AUTO  ", "Auto"])
def test_preflight_detection_values_keep_codex_when_everything_is_confirmed(sandbox, value):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC=value))

    assert fields["backend"] == "codex"
    assert hints(before) == []


# ---------------------------------------------------------------------------
# preflight: tryb wymuszony MONOLYNX_CRITIC=codex (backend=codex, gdy binarka jest i model poprawny, niezaleznie od sond)
# ---------------------------------------------------------------------------

FORCED_VALUES = ["codex", " CODEX ", "Codex", "codex\n"]
DETECTION_VALUES = ["", "auto", " AUTO ", "Auto"]
FORCED_HINT_PREFIX = "CRITIC-HINT: Tryb wymuszony (MONOLYNX_CRITIC=codex): "
FORCED_HINT_SUFFIX = " - krytykiem pozostaje Codex."


UNCONFIRMED_VERSION = "nie udało się odczytać wersji codex (codex --version)"
UNCONFIRMED_LOGIN = "codex login status nie potwierdził logowania"
UNCONFIRMED_VERSION_SILENT = "sonda codex --version nie odpowiedziała w 1 s albo nie dała się uruchomić"
UNCONFIRMED_LOGIN_SILENT = "sonda codex login status nie odpowiedziała w 1 s albo nie dała się uruchomić"


def forced_hint(*unconfirmed: str) -> str:
    """Pelna linia HINT trybu wymuszonego: powody niepotwierdzonych sond w kolejnosci wersja, logowanie, polaczone `; `."""
    return f"{FORCED_HINT_PREFIX}{'; '.join(unconfirmed)}{FORCED_HINT_SUFFIX}"


# Atrapy, w ktorych sonda odpowiedziala, ale nie potwierdzila Codexa: tryb wykrywania wybiera Claude, wymuszony zostaje przy Codex.
# scenariusz -> (nadpisania atrapy, pola `codex=` i `auth=` w trybie wymuszonym, powody w linii HINT).
# Tryb wymuszony sonduje logowanie takze bez odczytanej wersji (domyslna atrapa jest zalogowana), stad `auth=ok` przy zlej wersji.
UNCONFIRMED_PROBES = {
    "not-logged-in": ({"login_stdout": "Not logged in\n", "login_code": 1}, (VERSION, "missing"), [UNCONFIRMED_LOGIN]),
    "login-code-zero-without-phrase": ({"login_stdout": "Not logged in\n", "login_code": 0}, (VERSION, "missing"), [UNCONFIRMED_LOGIN]),
    "unreadable-version": ({"version_stdout": "to nie jest numer wersji\n"}, ("none", "ok"), [UNCONFIRMED_VERSION]),
    "version-command-failing": ({"version_code": 1}, ("none", "ok"), [UNCONFIRMED_VERSION]),
    "both-probes-unconfirmed": (
        {"version_stdout": "to nie jest numer wersji\n", "login_stdout": "Not logged in\n", "login_code": 1},
        ("none", "missing"),
        [UNCONFIRMED_VERSION, UNCONFIRMED_LOGIN],
    ),
}


@pytest.mark.parametrize("value", FORCED_VALUES)
def test_preflight_forced_mode_with_everything_confirmed_has_no_hint(sandbox, value):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC=value))

    assert fields == {"backend": "codex", "codex": VERSION, "auth": "ok", "model": "inherit"}
    assert hints(before) == []


@pytest.mark.parametrize("scenario", sorted(UNCONFIRMED_PROBES))
@pytest.mark.parametrize("value", FORCED_VALUES)
def test_preflight_forced_mode_keeps_codex_for_every_unconfirmed_probe_with_one_exact_hint(tmp_path, scenario, value):
    overrides, (version, auth), reasons = UNCONFIRMED_PROBES[scenario]
    box = Sandbox(tmp_path)
    box.install(**overrides)

    before, fields = parse_preflight(box.invoke("preflight", MONOLYNX_CRITIC=value))

    assert fields == {"backend": "codex", "codex": version, "auth": auth, "model": "inherit"}
    assert before == [forced_hint(*reasons)]


@pytest.mark.parametrize("scenario", sorted(UNCONFIRMED_PROBES))
@pytest.mark.parametrize("value", DETECTION_VALUES)
def test_preflight_detection_mode_selects_claude_for_the_same_unconfirmed_probes(tmp_path, scenario, value):
    # Kontrast do trybu wymuszonego: te same atrapy, ale pusta zmienna i `auto` zostaja przy wykrywaniu.
    box = Sandbox(tmp_path)
    box.install(**UNCONFIRMED_PROBES[scenario][0])

    before, fields = parse_preflight(box.invoke("preflight", MONOLYNX_CRITIC=value))

    assert fields["backend"] == "claude"
    assert len(hints(before)) == 1
    assert not hints(before)[0].startswith(FORCED_HINT_PREFIX)


@pytest.mark.parametrize("value", FORCED_VALUES)
def test_preflight_forced_mode_without_binary_selects_claude_with_binary_hint(tmp_path, value):
    box = Sandbox(tmp_path)  # brak atrapy `codex` w PATH

    before, fields = parse_preflight(box.invoke("preflight", MONOLYNX_CRITIC=value))

    assert fields["backend"] == "claude"
    assert fields["codex"] == "none"
    assert before == [HINT_NO_BINARY]


@pytest.mark.parametrize("value", FORCED_VALUES)
def test_preflight_forced_mode_with_invalid_model_selects_claude(sandbox, value):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC=value, MONOLYNX_CRITIC_CODEX_MODEL="has space"))

    assert fields == {"backend": "claude", "codex": VERSION, "auth": "ok", "model": "inherit"}
    assert before == [HINT_BAD_MODEL]


@pytest.mark.parametrize("value", FORCED_VALUES)
def test_preflight_forced_mode_with_valid_model_echoes_it(sandbox, value):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC=value, MONOLYNX_CRITIC_CODEX_MODEL=MODEL_ID))

    assert fields == {"backend": "codex", "codex": VERSION, "auth": "ok", "model": MODEL_ID}
    assert hints(before) == []


def test_preflight_forced_mode_with_silent_version_probe_keeps_codex_and_still_probes_login(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install(version_sleep=30)

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, ["preflight"], switch="codex", _PROBE_TIMEOUT=1)

    assert code == 0, err
    assert out.splitlines() == [forced_hint(UNCONFIRMED_VERSION_SILENT), "CRITIC: backend=codex codex=none auth=ok model=inherit"]
    assert [call["argv"] for call in box.calls()] == [["--version"], ["login", "status"]]


def test_preflight_forced_mode_with_silent_login_probe_keeps_codex_and_version(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install(login_sleep=30)

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, ["preflight"], switch=" CODEX ", _PROBE_TIMEOUT=1)

    assert code == 0, err
    assert out.splitlines() == [forced_hint(UNCONFIRMED_LOGIN_SILENT), f"CRITIC: backend=codex codex={VERSION} auth=missing model=inherit"]
    assert [call["argv"] for call in box.calls()] == [["--version"], ["login", "status"]]


def test_preflight_forced_mode_with_both_probes_silent_prints_one_joined_hint(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install(version_sleep=30, login_sleep=30)

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, ["preflight"], switch="codex", _PROBE_TIMEOUT=1)

    assert code == 0, err
    assert out.splitlines() == [
        forced_hint(UNCONFIRMED_VERSION_SILENT, UNCONFIRMED_LOGIN_SILENT),
        "CRITIC: backend=codex codex=none auth=missing model=inherit",
    ]


@pytest.mark.parametrize("sleeping", [{"version_sleep": 30}, {"login_sleep": 30}])
@pytest.mark.parametrize("value", DETECTION_VALUES)
def test_preflight_detection_mode_selects_claude_for_the_same_silent_probes(tmp_path, monkeypatch, capsys, sleeping, value):
    box = Sandbox(tmp_path)
    box.install(**sleeping)

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, ["preflight"], switch=value, _PROBE_TIMEOUT=1)

    assert code == 0, err
    lines = out.splitlines()
    assert lines[-1].startswith("CRITIC: backend=claude ")
    assert len(hints(lines[:-1])) == 1
    assert not hints(lines[:-1])[0].startswith(FORCED_HINT_PREFIX)


# ---------------------------------------------------------------------------
# preflight: model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model", [MODEL_ID, "a", "a" * 64, "gpt-5.1-codex:max", "A0._:-x"])
def test_preflight_valid_model_is_echoed(sandbox, model):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC_CODEX_MODEL=model))

    assert fields["backend"] == "codex"
    assert fields["model"] == model
    assert hints(before) == []


def test_preflight_empty_model_means_inherit(sandbox):
    _before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC_CODEX_MODEL=""))

    assert fields["backend"] == "codex"
    assert fields["model"] == "inherit"


@pytest.mark.parametrize("model", ["a" * 65, "-leading-dash", ".leading-dot", "_underscore", "has space", "path/slash", "semi;colon", "zażółć"])
def test_preflight_invalid_model_selects_claude_with_inherit_and_hint(sandbox, model):
    before, fields = parse_preflight(sandbox.invoke("preflight", MONOLYNX_CRITIC_CODEX_MODEL=model))

    assert fields["backend"] == "claude"
    assert fields["model"] == "inherit"
    assert before == [HINT_BAD_MODEL]


# ---------------------------------------------------------------------------
# preflight: bledy uzycia
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("args", [("bogus",), ("preflight", "--bogus"), ("--bogus",)])
def test_preflight_unknown_subcommand_or_flag_is_usage_error(sandbox, args):
    result = sandbox.invoke(*args)

    assert result.returncode == 64
    no_traceback(result)
    assert result.stderr.strip()
    assert "CRITIC:" not in result.stdout


# ---------------------------------------------------------------------------
# run: sukces i dokladne argv
# ---------------------------------------------------------------------------


def expected_argv(box: Sandbox, *, model: str | None = None) -> list[str]:
    argv = ["exec", "-C", str(box.work), "--sandbox", "read-only", "-c", 'model_reasoning_effort="high"']
    if model is not None:
        argv += ["-m", model]
    return [*argv, "-o", str(box.out), "-"]


def test_run_success_writes_verdict_and_reports_frozen_line(sandbox):
    result = sandbox.invoke(*sandbox.run_args())

    assert result.returncode == 0, result.stderr
    no_traceback(result)
    match = RUN_OK_LINE.match(last_run_line(result))
    assert match is not None, result.stdout
    assert match.group(1) == str(sandbox.out)
    assert sandbox.out.read_text(encoding="utf-8") == VERDICT
    assert "CODEX-NOISE" not in result.stdout


def test_run_passes_exact_argv_and_prompt_on_stdin(sandbox):
    result = sandbox.invoke(*sandbox.run_args())

    assert result.returncode == 0, result.stderr
    calls = sandbox.exec_calls()
    assert len(calls) == 1
    assert calls[0]["argv"] == expected_argv(sandbox)
    assert calls[0]["stdin"] == sandbox.prompt.read_text(encoding="utf-8")


def test_run_adds_model_flag_before_output_flag_when_variable_is_set(sandbox):
    result = sandbox.invoke(*sandbox.run_args(), MONOLYNX_CRITIC_CODEX_MODEL=MODEL_ID)

    assert result.returncode == 0, result.stderr
    assert [call["argv"] for call in sandbox.exec_calls()] == [expected_argv(sandbox, model=MODEL_ID)]


@pytest.mark.parametrize("value", ["", "auto", "codex", "  CODEX "])
def test_run_with_detection_or_forced_values_of_switch_runs_codex(sandbox, value):
    result = sandbox.invoke(*sandbox.run_args(), MONOLYNX_CRITIC=value, MONOLYNX_CRITIC_CODEX_MODEL="")

    assert result.returncode == 0, result.stderr
    assert [call["argv"] for call in sandbox.exec_calls()] == [expected_argv(sandbox)]


def test_run_exec_flags_exist_in_captured_help():
    # WHY: sygnatura `codex exec` sprawdzona wobec przechwyconego `--help` 0.160.0 (.claude/rules/external-tool-output.md pkt 5).
    for flag in ("-c, --config <key=value>", "-m, --model <MODEL>", "-s, --sandbox", "-C, --cd <DIR>", "-o, --output-last-message <FILE>"):
        assert flag in EXEC_HELP, flag
    assert "read-only" in EXEC_HELP
    assert "--worktree" in EXEC_HELP  # istnieje, a skrypt go NIE przekazuje (review dotyczy niecommitowanego drzewa)


def test_run_never_passes_worktree_or_json(sandbox):
    sandbox.invoke(*sandbox.run_args(), MONOLYNX_CRITIC_CODEX_MODEL=MODEL_ID)

    argv = sandbox.exec_calls()[0]["argv"]
    assert "--worktree" not in argv
    assert "--json" not in argv


def test_run_does_not_interpret_verdict_status(sandbox):
    needs_work = VERDICT.replace("95/100", "50/100").replace("APPROVED", "NEEDS WORK")
    sandbox.install(verdict_b64=b64(needs_work))

    result = sandbox.invoke(*sandbox.run_args())

    assert result.returncode == 0, result.stderr
    assert RUN_OK_LINE.match(last_run_line(result))
    assert sandbox.out.read_text(encoding="utf-8") == needs_work


# ---------------------------------------------------------------------------
# run: werdykt
# ---------------------------------------------------------------------------


def verdict_with(score_text: str) -> str:
    return f"**Code Review**\n\nagent-1: 100 -5 (a.py:1 uwaga) = {score_text}\n  -> APPROVED\n"


@pytest.mark.parametrize("score", ["0/100", "100/100", "95/100"])
def test_run_accepts_scores_on_the_boundaries(sandbox, score):
    sandbox.install(verdict_b64=b64(verdict_with(score)))

    result = sandbox.invoke(*sandbox.run_args())

    assert result.returncode == 0, result.stderr
    assert RUN_OK_LINE.match(last_run_line(result))


def test_run_accepts_verdict_when_at_least_one_score_is_in_range(sandbox):
    content = "**Code Review**\n\nagent-1: razem 150/100\nagent-2: razem 90/100\n"
    sandbox.install(verdict_b64=b64(content))

    result = sandbox.invoke(*sandbox.run_args())

    assert result.returncode == 0, result.stderr
    assert sandbox.out.read_text(encoding="utf-8") == content


@pytest.mark.parametrize(
    "content",
    [
        pytest.param(b"", id="empty"),
        pytest.param(b"  \n\t\n", id="whitespace-only"),
        pytest.param(b"agent-1: 100 -5 (a.py:1) = 95/100\n  -> APPROVED\n", id="no-code-review-header"),
        pytest.param(b"Code Review\n\nagent-1: 95/100\n", id="header-without-asterisks"),
        pytest.param(b"**code review**\n\nagent-1: 95/100\n", id="header-wrong-case"),
        pytest.param(b"**Code Review**\n\nagent-1: bez punktacji\nStatus: APPROVED\n", id="no-score"),
        pytest.param(verdict_with("150/100").encode(), id="score-above-100"),
        pytest.param(verdict_with("101/100").encode(), id="score-101"),
        pytest.param(verdict_with("1000/100").encode(), id="four-digit-score"),
        pytest.param(verdict_with("95/1000").encode(), id="denominator-1000"),
        pytest.param(verdict_with("95/10").encode(), id="denominator-10"),
        pytest.param(b"**Code Review**\n\nagent-1: \xff\xfe 95/100\n", id="not-utf8"),
    ],
)
def test_run_rejects_invalid_verdict_and_keeps_rejected_copy(sandbox, content):
    sandbox.install(verdict_b64=b64(content))

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "verdict")
    assert not sandbox.out.exists()
    rejected = Path(f"{sandbox.out}.rejected")
    assert rejected.read_bytes() == content


def test_run_codex_writing_no_file_is_verdict_error(sandbox):
    sandbox.install(verdict_b64=None)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "verdict")
    assert not sandbox.out.exists()


def test_run_stale_out_from_before_the_run_is_not_taken_as_new_verdict(sandbox):
    sandbox.out.write_text(VERDICT, encoding="utf-8")
    sandbox.install(verdict_b64=None)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "verdict")
    assert not sandbox.out.exists()


# ---------------------------------------------------------------------------
# run: kod wyjscia codex
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [1, 2, 130])
def test_run_nonzero_codex_exit_is_exit_error_and_removes_out(sandbox, code):
    sandbox.install(exec_code=code)  # atrapa zdazyla zapisac poprawny werdykt przed awaria
    sandbox.out.write_text("stary werdykt", encoding="utf-8")

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "exit")
    assert not sandbox.out.exists()
    assert "CODEX-NOISE-STDOUT" not in result.stdout


# ---------------------------------------------------------------------------
# run: wylacznik, brak binarki, model
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["claude", "CLAUDE", " Claude ", "gemini"])
def test_run_disabled_switch_does_not_call_codex(sandbox, value):
    result = sandbox.invoke(*sandbox.run_args(), MONOLYNX_CRITIC=value)

    assert_run_error(result, "disabled")
    assert sandbox.exec_calls() == []
    assert not sandbox.out.exists()


def test_run_without_binary_is_missing_error(tmp_path):
    box = Sandbox(tmp_path)  # brak atrapy w PATH

    result = box.invoke(*box.run_args())

    assert_run_error(result, "missing")
    assert not box.out.exists()


@pytest.mark.parametrize("model", ["a" * 65, "-bad", "has space", "semi;colon"])
def test_run_invalid_model_is_input_error_and_codex_not_called(sandbox, model):
    result = sandbox.invoke(*sandbox.run_args(), MONOLYNX_CRITIC_CODEX_MODEL=model)

    assert_run_error(result, "input")
    assert sandbox.exec_calls() == []


def test_run_accepts_model_of_64_characters(sandbox):
    model = "a" * 64

    result = sandbox.invoke(*sandbox.run_args(), MONOLYNX_CRITIC_CODEX_MODEL=model)

    assert result.returncode == 0, result.stderr
    assert [call["argv"] for call in sandbox.exec_calls()] == [expected_argv(sandbox, model=model)]


# ---------------------------------------------------------------------------
# run: bledy wejscia (kod 2, reason=input, zero efektow ubocznych)
# ---------------------------------------------------------------------------


def break_prompt_missing(box: Sandbox) -> None:
    box.prompt.unlink()


def break_prompt_is_directory(box: Sandbox) -> None:
    box.prompt.unlink()
    box.prompt.mkdir()


def break_prompt_empty(box: Sandbox) -> None:
    box.prompt.write_text("", encoding="utf-8")


def break_prompt_whitespace_only(box: Sandbox) -> None:
    box.prompt.write_text("  \n\t \n", encoding="utf-8")


def break_prompt_not_utf8(box: Sandbox) -> None:
    box.prompt.write_bytes(b"Zrecenzuj \xff\xfe zmiane\n")


def break_cwd_missing(box: Sandbox) -> None:
    box.work.rmdir()


def break_cwd_is_file(box: Sandbox) -> None:
    box.work.rmdir()
    box.work.write_text("plik, nie katalog", encoding="utf-8")


def break_out_parent_missing(box: Sandbox) -> None:
    box.out = box.tmp / "no-such-dir" / "verdict.md"


def break_out_is_directory(box: Sandbox) -> None:
    box.out = box.tmp / "out-dir"
    box.out.mkdir()


def break_out_is_prompt_file(box: Sandbox) -> None:
    box.out = box.prompt


INPUT_BREAKERS = [
    break_prompt_missing,
    break_prompt_is_directory,
    break_prompt_empty,
    break_prompt_whitespace_only,
    break_prompt_not_utf8,
    break_cwd_missing,
    break_cwd_is_file,
    break_out_parent_missing,
    break_out_is_directory,
    break_out_is_prompt_file,
]


@pytest.mark.parametrize("breaker", INPUT_BREAKERS, ids=lambda fn: fn.__name__.removeprefix("break_"))
def test_run_input_error_exits_2_and_never_calls_codex(sandbox, breaker):
    breaker(sandbox)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "input")
    assert sandbox.exec_calls() == []


@pytest.mark.skipif(os.geteuid() == 0, reason="root czyta plik bez uprawnien")
def test_run_unreadable_prompt_is_input_error(sandbox):
    sandbox.prompt.chmod(0)
    try:
        result = sandbox.invoke(*sandbox.run_args())
    finally:
        sandbox.prompt.chmod(0o644)

    assert_run_error(result, "input")
    assert sandbox.exec_calls() == []


def test_run_input_error_leaves_existing_out_untouched(sandbox):
    sandbox.out.write_text("stary werdykt", encoding="utf-8")
    break_prompt_missing(sandbox)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "input")
    assert sandbox.out.read_text(encoding="utf-8") == "stary werdykt"


def test_run_out_equal_to_prompt_file_keeps_the_prompt(sandbox):
    original = sandbox.prompt.read_text(encoding="utf-8")
    break_out_is_prompt_file(sandbox)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "input")
    assert sandbox.prompt.read_text(encoding="utf-8") == original


# ---------------------------------------------------------------------------
# run: --timeout i bledy uzycia (kod 64, nic nie uruchomione ani skasowane)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["29", "3601", "0", "-5", "abc", "30.5", "", "1e3"])
def test_run_timeout_outside_range_or_not_integer_is_usage_error(sandbox, value):
    sandbox.out.write_text("stary werdykt", encoding="utf-8")

    result = sandbox.invoke(*sandbox.run_args(timeout=value))

    assert_usage_error(sandbox, result)
    assert sandbox.out.read_text(encoding="utf-8") == "stary werdykt"


@pytest.mark.parametrize("value", ["30", "3600", "900"])
def test_run_timeout_boundaries_are_accepted(sandbox, value):
    result = sandbox.invoke(*sandbox.run_args(timeout=value))

    assert result.returncode == 0, result.stderr
    assert RUN_OK_LINE.match(last_run_line(result))


@pytest.mark.parametrize("missing_flag", ["--prompt-file", "--out", "--cwd"])
def test_run_missing_required_flag_is_usage_error(sandbox, missing_flag):
    args = sandbox.run_args()
    index = args.index(missing_flag)
    del args[index : index + 2]
    sandbox.out.write_text("stary werdykt", encoding="utf-8")

    result = sandbox.invoke(*args)

    assert_usage_error(sandbox, result)
    assert sandbox.out.read_text(encoding="utf-8") == "stary werdykt"


@pytest.mark.parametrize("extra", [["--bogus"], ["--bogus", "x"]])
def test_run_unknown_flag_or_argument_is_usage_error(sandbox, extra):
    result = sandbox.invoke(*sandbox.run_args(), *extra)

    assert_usage_error(sandbox, result)


# ---------------------------------------------------------------------------
# run: timeout (w procesie - dolna granica --timeout to 30 s, patrz docstring modulu)
# ---------------------------------------------------------------------------


def load_module():
    spec = importlib.util.spec_from_file_location("critic_backend_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    if stat.exists():
        # zombie czeka na reaper (kontener bez init) - proces juz nie dziala
        return stat.read_text(encoding="utf-8").rsplit(")", 1)[1].split()[0] != "Z"
    return True


def wait_dead(pid: int, seconds: float = 5.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.05)
    return not pid_alive(pid)


def run_in_process(
    monkeypatch, capsys, box: Sandbox, args: list[str], *, switch: str | None = None, **module_constants
) -> tuple[int, str, str, float]:
    module = load_module()
    for name, value in module_constants.items():
        assert hasattr(module, name), f"critic_backend.py powinien miec stala modulowa {name} (patrz docstring testu)"
        monkeypatch.setattr(module, name, value)
    for key, value in box.env().items():
        monkeypatch.setenv(key, value)
    for key in ("MONOLYNX_CRITIC", "MONOLYNX_CRITIC_CODEX_MODEL"):
        monkeypatch.delenv(key, raising=False)
    if switch is not None:
        monkeypatch.setenv("MONOLYNX_CRITIC", switch)
    started = time.monotonic()
    code = module.main(args)
    elapsed = time.monotonic() - started
    captured = capsys.readouterr()
    return code, captured.out, captured.err, elapsed


def test_run_timeout_kills_whole_process_group_and_removes_out(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    pid_file = tmp_path / "codex.pid"
    child_file = tmp_path / "child.pid"
    box.install(sleep=300, pid_file=str(pid_file), child_pid_file=str(child_file))  # werdykt zapisany przed zawieszeniem
    box.out.write_text("stary werdykt", encoding="utf-8")
    stale_rejected = Path(f"{box.out}.rejected")
    stale_rejected.write_text("stara odrzucona kopia", encoding="utf-8")

    code, out, err, elapsed = run_in_process(monkeypatch, capsys, box, box.run_args(timeout="2"), MIN_TIMEOUT=1)

    assert code == 2, (out, err)
    assert out.splitlines()[-1] == "CRITIC-RUN: status=error reason=timeout"
    assert "Traceback" not in err
    assert err.strip()
    assert not box.out.exists()
    assert not stale_rejected.exists()
    assert elapsed < 30
    codex_pid = int(pid_file.read_text(encoding="utf-8"))
    child_pid = int(child_file.read_text(encoding="utf-8"))
    assert wait_dead(codex_pid)
    assert wait_dead(child_pid), "proces potomny atrapy przezyl timeout - killpg powinien zabic cala grupe"


def test_run_timeout_escalates_to_sigkill_when_sigterm_is_ignored(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    pid_file = tmp_path / "codex.pid"
    box.install(sleep=300, pid_file=str(pid_file), ignore_term=True)

    code, out, _err, elapsed = run_in_process(monkeypatch, capsys, box, box.run_args(timeout="2"), MIN_TIMEOUT=1, KILL_GRACE_SECONDS=1)

    assert code == 2
    assert out.splitlines()[-1] == "CRITIC-RUN: status=error reason=timeout"
    assert not box.out.exists()
    assert elapsed < 30
    assert wait_dead(int(pid_file.read_text(encoding="utf-8")))


def test_run_in_process_keeps_frozen_line_for_success(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()

    code, out, _err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args(timeout="30"))

    assert code == 0
    assert out.splitlines()[-1] == f"CRITIC-RUN: status=ok out={box.out}"


# ---------------------------------------------------------------------------
# run: luki pokrycia (stary .rejected, blad startu procesu, odczyt werdyktu, ogon stderr, cyfry spoza ASCII, kolejnosc walidacji)
# ---------------------------------------------------------------------------


def stale_rejected_file(box: Sandbox) -> Path:
    path = Path(f"{box.out}.rejected")
    path.write_text("stara odrzucona kopia sprzed uruchomienia", encoding="utf-8")
    return path


def test_run_success_removes_stale_rejected_copy_from_earlier_run(sandbox):
    stale = stale_rejected_file(sandbox)

    result = sandbox.invoke(*sandbox.run_args())

    assert result.returncode == 0, result.stderr
    assert not stale.exists()


def test_run_codex_exit_error_does_not_leave_stale_rejected_copy(sandbox):
    sandbox.install(exec_code=1)
    stale = stale_rejected_file(sandbox)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "exit")
    assert not stale.exists()


def test_run_new_rejected_copy_replaces_stale_one(sandbox):
    sandbox.install(verdict_b64=b64("bez naglowka i punktacji\n"))
    stale_rejected_file(sandbox)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "verdict")
    assert Path(f"{sandbox.out}.rejected").read_text(encoding="utf-8") == "bez naglowka i punktacji\n"


def test_run_process_start_failure_is_missing_error_without_traceback(sandbox):
    # `which` znajduje plik wykonywalny, ale jego interpreter nie istnieje: execve zawodzi dopiero w Popen (OSError).
    (sandbox.bin / "codex").write_text("#!/nonexistent/interpreter\n", encoding="utf-8")
    sandbox.out.write_text("stary werdykt", encoding="utf-8")

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "missing")
    assert not sandbox.out.exists()


def test_run_non_executable_codex_is_missing_error(sandbox):
    (sandbox.bin / "codex").chmod(0o644)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "missing")


def test_run_non_utf8_verdict_file_is_verdict_error_and_bytes_are_kept(sandbox):
    # WHY: werdykt w cp1250 (nie UTF-8) z poprawnym naglowkiem i punktacja ASCII; dekodowanie z `errors="replace"` by go przepuscilo.
    content = "**Code Review**\n\nagent-1: 100 -5 (a.py:1 zażółć gęślą jaźń) = 95/100\n".encode("cp1250")
    sandbox.install(verdict_b64=b64(content))

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "verdict")
    assert not sandbox.out.exists()
    assert Path(f"{sandbox.out}.rejected").read_bytes() == content


@pytest.mark.skipif(os.geteuid() == 0, reason="root czyta plik bez uprawnien")
def test_run_unreadable_verdict_file_is_verdict_error_and_out_does_not_remain(sandbox):
    sandbox.install(out_mode=0)

    result = sandbox.invoke(*sandbox.run_args())

    try:
        assert_run_error(result, "verdict")
        assert not sandbox.out.exists(), "po bledzie werdyktu plik --out nie moze istniec (kontrakt)"
    finally:
        if sandbox.out.exists():
            sandbox.out.chmod(0o644)


def test_run_exit_error_forwards_stripped_stderr_tail_to_stderr_not_stdout(sandbox):
    sandbox.install(exec_code=3)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "exit")
    assert "CODEX-NOISE-STDERR" in result.stderr
    assert "\x1b" not in result.stderr
    assert "[31m" not in result.stderr
    assert "CODEX-NOISE-STDERR" not in result.stdout
    assert "CODEX-NOISE-STDOUT" not in result.stdout


def test_run_exit_error_forwards_only_the_end_of_long_stderr(sandbox):
    sandbox.install(exec_code=3, stderr_lines=50)

    result = sandbox.invoke(*sandbox.run_args())

    assert_run_error(result, "exit")
    assert "tail-line-50" in result.stderr
    assert "tail-line-1\n" not in result.stderr
    assert "CODEX-NOISE-STDERR" not in result.stderr
    assert "tail-line-" not in result.stdout


@pytest.mark.parametrize("value", ["٣٠", chr(0xFF13) + chr(0xFF10), "٩٠٠"])  # arabsko-indyjskie i pelnoszerokie cyfry: int() je przyjmuje
def test_run_timeout_with_non_ascii_digits_is_usage_error(sandbox, value):
    result = sandbox.invoke(*sandbox.run_args(timeout=value))

    assert_usage_error(sandbox, result)


def test_run_input_error_wins_over_disabled_switch(sandbox):
    break_prompt_missing(sandbox)

    result = sandbox.invoke(*sandbox.run_args(), MONOLYNX_CRITIC="claude")

    assert_run_error(result, "input")


def test_run_disabled_switch_wins_over_missing_binary(tmp_path):
    box = Sandbox(tmp_path)  # brak atrapy w PATH

    result = box.invoke(*box.run_args(), MONOLYNX_CRITIC="claude")

    assert_run_error(result, "disabled")


def test_run_invalid_model_wins_over_missing_binary(tmp_path):
    box = Sandbox(tmp_path)  # brak atrapy w PATH

    result = box.invoke(*box.run_args(), MONOLYNX_CRITIC_CODEX_MODEL="has space")

    assert_run_error(result, "input")


# ---------------------------------------------------------------------------
# wartosci domyslne (test z jawnym argumentem nie dowodzi wartosci domyslnej) i sondy bez odpowiedzi
# ---------------------------------------------------------------------------


def recording_popen(waits: list[float | None]):
    """Zastepnik subprocess.Popen: zapisuje limit przekazany do communicate() i sam zapisuje poprawny werdykt do pliku `-o`."""

    class RecordingPopen:
        pid = 0
        returncode = 0

        def __init__(self, argv, **_kwargs):
            self.argv = argv

        def communicate(self, input=None, timeout=None):
            waits.append(timeout)
            Path(self.argv[self.argv.index("-o") + 1]).write_text(VERDICT, encoding="utf-8")
            return b"", b""

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    return RecordingPopen


def test_run_without_timeout_flag_waits_for_default_900_seconds(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    waits: list[float | None] = []
    monkeypatch.setattr(subprocess, "Popen", recording_popen(waits))

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args())

    assert code == 0, (out, err)
    assert waits == [900]


def test_run_default_timeout_comes_from_module_constant(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    waits: list[float | None] = []
    monkeypatch.setattr(subprocess, "Popen", recording_popen(waits))

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args(), DEFAULT_TIMEOUT=1234)

    assert code == 0, (out, err)
    assert waits == [1234]


def test_run_explicit_timeout_overrides_the_default(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    waits: list[float | None] = []
    monkeypatch.setattr(subprocess, "Popen", recording_popen(waits))

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args(timeout="45"))

    assert code == 0, (out, err)
    assert waits == [45]


def recording_probe_run(limits: list[tuple[str, float | None]]):
    def fake_run(argv, **kwargs):
        limits.append((" ".join(argv[1:]), kwargs.get("timeout")))
        stdout = VERSION_STDOUT if argv[1:] == ["--version"] else LOGIN_STATUS
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")

    return fake_run


def test_preflight_probes_use_default_limit_of_30_seconds(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    limits: list[tuple[str, float | None]] = []
    monkeypatch.setattr(subprocess, "run", recording_probe_run(limits))

    code, out, _err, _elapsed = run_in_process(monkeypatch, capsys, box, [])

    assert code == 0
    assert out.splitlines()[-1] == f"CRITIC: backend=codex codex={VERSION} auth=ok model=inherit"
    assert limits == [("--version", 30), ("login status", 30)]


def test_preflight_probe_limit_comes_from_module_constant(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    limits: list[tuple[str, float | None]] = []
    monkeypatch.setattr(subprocess, "run", recording_probe_run(limits))

    run_in_process(monkeypatch, capsys, box, ["preflight"], _PROBE_TIMEOUT=7)

    assert limits == [("--version", 7), ("login status", 7)]


HINT_SWITCH_CLAUDE = "CRITIC-HINT: Wyłącznik MONOLYNX_CRITIC=claude - krytykiem będzie Claude."
HINT_NO_BINARY = "CRITIC-HINT: Brak polecenia codex w PATH - krytykiem będzie Claude."
HINT_NO_VERSION = "CRITIC-HINT: Nie udało się odczytać wersji codex (codex --version) - krytykiem będzie Claude."
HINT_NO_AUTH = "CRITIC-HINT: Codex nie jest zalogowany - krytykiem będzie Claude. Zaloguj: codex login"
HINT_BAD_MODEL = (
    "CRITIC-HINT: Niepoprawna wartość MONOLYNX_CRITIC_CODEX_MODEL"
    " (litery, cyfry i . _ : -, od 1 do 64 znaków, zaczyna się od litery lub cyfry) - krytykiem będzie Claude."
)
HINT_VERSION_SILENT = "CRITIC-HINT: Sonda codex --version nie odpowiedziała w 1 s albo nie dała się uruchomić - krytykiem będzie Claude."
HINT_LOGIN_SILENT = (
    "CRITIC-HINT: Sonda codex login status nie odpowiedziała w 1 s albo nie dała się uruchomić - stan logowania nieznany, krytykiem będzie Claude."
)


def test_preflight_version_probe_without_answer_gets_its_own_hint_and_skips_login_probe(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install(version_sleep=30)

    code, out, err, elapsed = run_in_process(monkeypatch, capsys, box, ["preflight"], _PROBE_TIMEOUT=1)

    assert code == 0, err
    assert out.splitlines() == [HINT_VERSION_SILENT, "CRITIC: backend=claude codex=none auth=missing model=inherit"]
    assert "Traceback" not in err
    assert elapsed < 20
    assert [call["argv"] for call in box.calls()] == [["--version"]]


def test_preflight_login_probe_without_answer_keeps_version_and_gets_its_own_hint(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install(login_sleep=30)

    code, out, err, elapsed = run_in_process(monkeypatch, capsys, box, [], _PROBE_TIMEOUT=1)

    assert code == 0, err
    assert out.splitlines() == [HINT_LOGIN_SILENT, f"CRITIC: backend=claude codex={VERSION} auth=missing model=inherit"]
    assert "Traceback" not in err
    assert elapsed < 20
    assert [call["argv"] for call in box.calls()] == [["--version"], ["login", "status"]]


def test_preflight_not_logged_in_keeps_its_own_hint_text(tmp_path):
    box = Sandbox(tmp_path)
    box.install(login_stdout="Not logged in\n", login_code=1)

    before, fields = parse_preflight(box.invoke("preflight"))

    assert before == [HINT_NO_AUTH]
    assert fields["auth"] == "missing"


def test_preflight_unreadable_version_keeps_its_own_hint_text(tmp_path):
    box = Sandbox(tmp_path)
    box.install(version_stdout="to nie jest numer wersji\n")

    before, fields = parse_preflight(box.invoke("preflight"))

    assert before == [HINT_NO_VERSION]
    assert fields["codex"] == "none"


def test_preflight_missing_binary_keeps_its_own_hint_text(tmp_path):
    before, _fields = parse_preflight(Sandbox(tmp_path).invoke("preflight"))

    assert before == [HINT_NO_BINARY]


# ---------------------------------------------------------------------------
# KILL_GRACE_SECONDS i galezie `except OSError` w procesie (niezalezne od uid: pod rootem chmod nic nie blokuje)
# ---------------------------------------------------------------------------

FAKE_PID = 12345


def hanging_popen(waits: list[float | None]):
    """Zastepnik Popen, ktorego communicate() zawsze przekracza limit; wait() zapisuje limit oczekiwania po SIGTERM."""

    class HangingPopen:
        pid = FAKE_PID
        returncode = None

        def __init__(self, argv, **_kwargs):
            self.argv = argv

        def communicate(self, input=None, timeout=None):
            raise subprocess.TimeoutExpired(self.argv, timeout)

        def wait(self, timeout=None):
            waits.append(timeout)
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    return HangingPopen


def record_killpg(monkeypatch) -> list[tuple[int, int]]:
    """Podmienia os.killpg: nigdy nie wysyla prawdziwego sygnalu (pid jest zmyslony)."""
    sent: list[tuple[int, int]] = []
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: sent.append((pgid, int(sig))))
    return sent


def test_run_timeout_waits_default_5_seconds_between_sigterm_and_sigkill(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    waits: list[float | None] = []
    monkeypatch.setattr(subprocess, "Popen", hanging_popen(waits))
    sent = record_killpg(monkeypatch)

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args(timeout="30"))

    assert code == 2, (out, err)
    assert out.splitlines()[-1] == "CRITIC-RUN: status=error reason=timeout"
    assert waits == [5, None]
    assert sent == [(FAKE_PID, int(signal.SIGTERM)), (FAKE_PID, int(signal.SIGKILL))]


def test_run_timeout_grace_comes_from_module_constant(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    waits: list[float | None] = []
    monkeypatch.setattr(subprocess, "Popen", hanging_popen(waits))
    record_killpg(monkeypatch)

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args(timeout="30"), KILL_GRACE_SECONDS=11)

    assert code == 2, (out, err)
    assert waits == [11, None]


def raise_for(name: str, error: OSError, original):
    """Zastepnik metody Path: rzuca `error` tylko dla pliku o danej nazwie, reszta idzie do oryginalu."""

    def replacement(self, *args, **kwargs):
        if self.name == name:
            raise error
        return original(self, *args, **kwargs)

    return replacement


def test_run_unreadable_prompt_in_process_is_input_error_with_exact_message(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    monkeypatch.setattr(Path, "read_bytes", raise_for("prompt.txt", PermissionError(13, "Permission denied"), Path.read_bytes))

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args())

    assert code == 2, (out, err)
    assert out.splitlines() == ["CRITIC-RUN: status=error reason=input"]
    assert err == f"critic_backend: nie można odczytać pliku promptu {box.prompt}: Permission denied\n"
    assert box.exec_calls() == []


def test_run_unreadable_verdict_in_process_is_verdict_error_and_out_does_not_remain(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    monkeypatch.setattr(Path, "read_bytes", raise_for("verdict.md", PermissionError(13, "Permission denied"), Path.read_bytes))

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args())

    rejected = Path(f"{box.out}.rejected")
    assert code == 2, (out, err)
    assert out.splitlines() == ["CRITIC-RUN: status=error reason=verdict"]
    assert err == f"critic_backend: nie można odczytać pliku werdyktu (Permission denied), kopia: {rejected}\n"
    assert not box.out.exists()
    assert rejected.read_text(encoding="utf-8") == VERDICT


def test_run_stale_out_that_cannot_be_removed_is_input_error_and_codex_is_not_started(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install()
    box.out.write_text("stary werdykt", encoding="utf-8")
    monkeypatch.setattr(Path, "unlink", raise_for("verdict.md", PermissionError(13, "Permission denied"), Path.unlink))

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args())

    assert code == 2, (out, err)
    assert out.splitlines() == ["CRITIC-RUN: status=error reason=input"]
    assert err == f"critic_backend: nie można usunąć poprzedniego wyniku: {box.out}\n"
    assert box.out.read_text(encoding="utf-8") == "stary werdykt"
    assert box.exec_calls() == []


def test_run_rejected_verdict_that_cannot_be_kept_still_removes_out(tmp_path, monkeypatch, capsys):
    box = Sandbox(tmp_path)
    box.install(verdict_b64=b64("bez naglowka i punktacji\n"))

    def failing_replace(src, dst):
        raise PermissionError(13, "Permission denied")

    monkeypatch.setattr(os, "replace", failing_replace)

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args())

    assert code == 2, (out, err)
    assert out.splitlines() == ["CRITIC-RUN: status=error reason=verdict"]
    assert err == "critic_backend: werdykt nie ma nagłówka **Code Review** i punktacji N/100 (N w 0..100), kopii nie udało się zachować\n"
    assert not box.out.exists(), "po bledzie werdyktu plik --out nie moze istniec, takze gdy kopii nie da sie zachowac"
    assert not Path(f"{box.out}.rejected").exists()


def test_run_with_unresolvable_out_path_still_runs_codex(tmp_path, monkeypatch, capsys):
    # _same_file: out nie istnieje, wiec porownanie idzie przez resolve(); OSError z resolve() oznacza "to nie ten sam plik".
    box = Sandbox(tmp_path)
    box.install()
    monkeypatch.setattr(Path, "resolve", raise_for("verdict.md", OSError(40, "Too many levels of symbolic links"), Path.resolve))

    code, out, err, _elapsed = run_in_process(monkeypatch, capsys, box, box.run_args())

    assert code == 0, (out, err)
    assert out.splitlines()[-1] == f"CRITIC-RUN: status=ok out={box.out}"
    assert len(box.exec_calls()) == 1


def test_preflight_probe_that_cannot_start_gets_silent_hint_with_default_limit(tmp_path):
    # _probe: `which` znajduje plik, ale interpreter nie istnieje - Popen rzuca OSError; limit w komunikacie to domyslne 30 s.
    box = Sandbox(tmp_path)
    box.install()
    (box.bin / "codex").write_text("#!/nonexistent/interpreter\n", encoding="utf-8")

    result = box.invoke("preflight")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == [
        HINT_VERSION_SILENT.replace("w 1 s", "w 30 s"),
        "CRITIC: backend=claude codex=none auth=missing model=inherit",
    ]
    no_traceback(result)
