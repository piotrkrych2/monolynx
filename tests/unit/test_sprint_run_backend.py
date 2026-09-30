r"""Testy adaptera backendow sesji sprintu: plugin/scripts/sprint_run_backend.py (MON-185).

TDD: plik sprint_run_backend.py jeszcze NIE ISTNIEJE w chwili pisania tych testow.
Te testy SA specyfikacja jego kontraktu dla backend-developera. Cel MON-185:
uruchamiac i monitorowac tickety sprintu w osobnych sesjach Codex ORAZ Claude Code,
przy JEDNYM algorytmie dyspozytora (klasyfikator werdyktow w sprint_run_state.py
pozostaje NIEZMIENIONY, patrz test_sprint_run_state.py) i wspolnych kontraktach
stanow.

Kontrakt modulu (adapter MINIMALNY -- wybor backendu po nazwie stringowej "claude"
albo "codex", bez fabryki wtyczek: dwie implementacje to za malo na abstrakcje):

    _SUPPORTED_BACKENDS = ("claude", "codex")
    class UnsupportedBackendError(ValueError): ...

    session_identity(slug: str, key: str) -> str
        "<slug>:<key>" -- tozsamosc sesji NIEZALEZNA od modelu i backendu (patrz
        TestFindActiveSessionIdentity nizej: zmiana modelu ani zmiana runtime nie
        moze spowodowac duplikatu dla ticketu z zywa sesja).

    normalize_record(backend, *, slug, key, model, runtime_id, pid, name, state,
                      started_at, updated_at=None, log_path=None, thread_id=None,
                      warning=None) -> dict
        Zbuduj ZNORMALIZOWANY rekord sesji -- jeden ksztalt dla obu backendow.
        `state` musi byc jedno z: "running", "waiting", "success", "failure"
        (kontrakt adaptera; NIE mylic z session_state_value sprint_run_state.py,
        ktore jest "none"/"running"/"exited" -- bridging robi classify_normalized,
        patrz nizej i TestClassifyNormalizedBridge). Nieznana wartosc `state` ->
        ValueError (walidacja na granicy konstrukcji wspolnego ksztaltu).

    load_registry(registry_path: Path) -> dict[str, dict]
    save_registry(registry_path: Path, registry: dict[str, dict]) -> None
        Rejestr wlasny dyspozytora: {session_identity(slug,key): normalized_record}.
        Zrodlo prawdy dla Codex (`codex exec` nie ma odpowiednika `claude agents
        --json --all` -- potwierdzone: `~/.codex/session_index.jsonl` NIE zawiera
        sesji uruchomionych przez `codex exec`, patrz brief). Dla Claude rejestr
        jest pomocniczy (zrodlem prawdy zostaje `claude agents --json --all` przez
        sprint_run_state.match_session), ale wpis jest tez zapisywany dla spojnosci
        listowania. Uszkodzony/brakujacy plik -> {} (rejestr jest odtwarzalnym
        cache'em, nie jedynym zrodlem prawdy -- uszkodzenie nie ma wywalac ticku).

    parse_codex_jsonl(text: str) -> dict
        Parsuj JSONL `codex exec --json` (granica systemu, patrz
        .claude/rules/external-tool-output.md pkt 1 i 3 -- fixture z przechwycenia
        w tests/fixtures/codex/exec-json.0.155.1.jsonl, pierwsza linia to naglowek
        _meta, NIE czesc realnego wyjscia). Zwraca:
            {"thread_id": str | None, "turn_completed": bool,
             "messages": list[str], "errors": list[str], "unknown_types": list[str]}
        Linia niepoprawna jako JSON jest pomijana (nie rzuca wyjatku). Nieznany
        "type" trafia do unknown_types -- NIE zmienia turn_completed, NIE rzuca
        wyjatku (wariant odwracalny, pkt 3 external-tool-output.md).

    is_process_alive(pid: int | None) -> bool
        os.kill(pid, 0). None -> False.

    preflight(backend: str, *, env: Mapping[str, str]) -> tuple[bool, list[str]]
        (ok, problems) -- BEZ SKUTKOW UBOCZNYCH (zadna sesja nie startuje, zaden
        wpis rejestru nie powstaje). backend="claude": jak dotychczasowy check w
        sprint_run_tick.py (`claude --version`, `MONOLYNX_SPRINT_PERMISSION_MODE`
        != bypassPermissions/dangerously-skip-permissions). backend="codex":
        `codex --version` + `codex login status` zawiera "Logged in" -- NIGDY nie
        czyta .claude/settings*.json ani nie sprawdza MONOLYNX_SPRINT_PERMISSION_MODE
        (MON-183: to pojecie jest specyficzne dla Claude Code permission modes,
        Codex ma wlasny --sandbox/--dangerously-bypass-approvals-and-sandbox).
        Nieznany backend -> UnsupportedBackendError (blad glosny, nie ok=False).

    find_active_session(*, slug, key, registry, claude_agents=None) -> dict | None
        Zwroc ZYWA (running/waiting) znormalizowana sesje dla (slug,key),
        NIEZALEZNIE od backendu/modelu w rekordzie. Sprawdza NAJPIERW
        claude_agents (gdy podane) przez sprint_run_state.match_session +
        sprint_run_state.session_state, POTEM rejestr wlasny. None gdy brak.

    start(backend, *, slug, key, model, prompt, cwd, registry_path,
          claude_agents=None, permission_mode=None) -> dict
        IDEMPOTENTNY: find_active_session() first -- jesli zywa sesja juz
        istnieje dla (slug,key), zwraca ISTNIEJACY rekord bez spawnu nowego
        procesu (niezaleznie od tego, czy zadany `backend`/`model` sie zgadza
        z zapisanym -- MON-185 AC3). W przeciwnym razie spawnuje NOWY proces w
        tle (subprocess.Popen, BEZ .wait()/.communicate() -- funkcja wraca
        natychmiast) i zapisuje nowy wpis w rejestrze. claude: `claude --bg
        --name <slug>-<key>-<model> --model <model> ...`. codex: `codex exec
        --json --sandbox <tryb> -C <cwd> --skip-git-repo-check <prompt>`,
        stdin=DEVNULL, BEZ `-m/--model` (potwierdzone: `codex exec -m` nie
        przyjmuje aliasow opus/sonnet, MONOLYNX_SPRINT_MODEL nie moze isc tam
        literalnie) i BEZ `--name` (flaga nie istnieje w codex exec).

    status(record: dict) -> dict
        Nowy rekord ze SWIEZO wyliczonym `state`. codex: proces zywy
        (is_process_alive) -> "running" (NIEZALEZNIE od tresci JSONL, w tym
        nieznanych typow zdarzen -- wariant odwracalny). Proces martwy ->
        JSONL ma turn.completed -> "success"; JSONL BEZ turn.completed
        (utrata procesu) -> "failure", NIGDY "success" (MON-185 AC4). codex
        NIGDY nie zwraca "waiting" (brak natywnego sygnalu oczekiwania na
        zgode pod Codex -- MON-184: PreToolUse bez agent_id/agent_type,
        `waiting` dla Codex idzie WYLACZNIE przez marker SPRINT-RUN STOP: w
        tresci logu, obslugiwany przez classify() na poziomie
        sprint_run_state, nie przez adapter). claude: delegacja do
        sprint_run_state.session_state (mapowanie dead/live) plus flaga
        oczekiwania na zgode (waits_for_permission, MON-173) -> "waiting".

    logs(record: dict, n: int = 60) -> list[str]
        Ostatnie n niepustych linii tresci, znormalizowane dla obu backendow.

    resume(backend, record, *, prompt=None) -> dict
        codex: `codex exec resume <thread_id albo nazwa> [prompt] --last`
        (potwierdzona sygnatura z `codex exec resume --help`, brief MON-185).
        claude: NIEWSPIERANE (NotImplementedError, po polsku) -- sesje `claude
        --bg` sa dlugozyjace, `resume` nie ma odpowiednika w kontrakcie
        dyspozytora Claude (to nie jest ta sama operacja co `claude attach`).

    stop(record: dict, *, registry_path: Path) -> None
        Usuwa WYLACZNIE wlasny wpis z rejestru -- dopasowanie po
        session_identity ORAZ runtime_id/pid rekordu (nie tylko po kluczu).
        Wpis, ktory nie zgadza sie pid/runtime_id (np. rejestr zmieniony przez
        inny proces w miedzyczasie) NIE jest usuwany. Jesli proces zyje i jego
        PID zgadza sie z rekordem, wysyla SIGTERM WYLACZNIE do tego PID.

    list_sessions(backend, *, registry_path, claude_agents=None) -> list[dict]
        Znormalizowana lista aktywnych sesji backendu (odswiezone przez
        status()).

    classify_normalized(ticket_status: str, record: dict | None,
                         log_tail: list[str]) -> tuple[str, str]
        BRIDGE do sprint_run_state.classify -- classify/summarize w
        sprint_run_state.py POZOSTAJA NIEZMIENIONE (zero regresji backendu
        Claude, patrz test_sprint_run_state.py). Mapuje record["state"]
        (running/waiting -> "running", success/failure -> "exited", None ->
        "none") na session_state_value klasyczengo kontraktu i deleguje do
        sprint_run_state.classify(). Marker "SPRINT-RUN STOP:" w log_tail
        dziala IDENTYCZNIE dla obu backendow, bo to ta sama funkcja classify()
        -- werdykt to "waiting" (frozen enum sprint_run_state, NIE nowa
        wartosc "needs_human": "needs_human" pozostaje zarezerwowane dla
        rozjazdu ticket/sesja per klasyczny kontrakt, patrz
        TestClassifyNormalizedStopMarker nizej).

Modul MUSI wstawiac katalog wlasnego pliku na poczatek sys.path (analogicznie do
sprint_run_tick.py) PRZED `import sprint_run_state`, zeby dzialac zarowno jako
import wprost (te testy), jak i jako zaleznosc sprint_run_tick.py.

Wymogi implementacyjne pod testowalnosc (zeby monkeypatch ponizej mial co
podmieniac): modul importuje `subprocess`, `os` i `signal` BEZPOSREDNIO (`import
subprocess`, nie `from subprocess import Popen`) -- testy podmieniaja
`backend.subprocess.Popen` i `backend.os.kill`, i odwoluja sie do
`backend.signal.SIGTERM`.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_PATH = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "sprint_run_backend.py"
STATE_PATH = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "sprint_run_state.py"
CODEX_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "codex"
CLAUDE_FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "claude"

if not BACKEND_PATH.exists() or not STATE_PATH.exists():
    pytest.skip("sprint_run_backend.py jeszcze nie zaimplementowany (TDD)", allow_module_level=True)


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


state = _load(STATE_PATH, "sprint_run_state")
backend = _load(BACKEND_PATH, "sprint_run_backend")


def _codex_jsonl_body() -> str:
    """Fixture realna (patrz tests/fixtures/codex/README.md) bez pierwszej linii _meta."""
    lines = (CODEX_FIXTURES_DIR / "exec-json.0.155.1.jsonl").read_text().splitlines()
    return "\n".join(lines[1:]) + "\n"


def _spawn_dead_pid() -> int:
    """Zwroc PID procesu, ktory na pewno juz nie zyje -- odpalamy i czekamy na koniec,
    zamiast zgadywac magiczna liczbe (przenosnie i deterministycznie). `subprocess.run()`
    zwraca CompletedProcess (bez atrybutu .pid) -- trzeba Popen + wait()."""
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


class _NonBlockingPopenStub:
    """Podstawa pod fake subprocess.Popen -- start() NIE MOZE czekac na koniec procesu
    (dyspozytor odpala sesje w tle i wraca natychmiast, patrz docstring modulu)."""

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self.pid = 987654

    def wait(self, *a, **kw):
        raise AssertionError("start() nie moze wolac wait() -- musi wrocic natychmiast (proces w tle)")

    def communicate(self, *a, **kw):
        raise AssertionError("start() nie moze wolac communicate() -- musi wrocic natychmiast (proces w tle)")

    def poll(self):
        return None


def bg_claude_agent(name, session_id="sid-1", started_at=1000.0, agent_state="running", agent_id="a1"):
    """Ten sam ksztalt co bg_agent() w test_sprint_run_state.py -- powielony tutaj celowo
    (przydzial pliku nie obejmuje wspoldzielonego helpera w conftest.py, patrz raport)."""
    return {
        "id": agent_id,
        "cwd": "/repo",
        "kind": "background",
        "startedAt": started_at,
        "sessionId": session_id,
        "name": name,
        "state": agent_state,
    }


# ---------------------------------------------------------------------------
# session_identity
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionIdentity:
    def test_format_is_slug_colon_key(self):
        assert backend.session_identity("monolynx", "MON-1") == "monolynx:MON-1"

    def test_different_slug_gives_different_identity(self):
        assert backend.session_identity("monolynx", "MON-1") != backend.session_identity("inny", "MON-1")

    def test_different_key_gives_different_identity(self):
        assert backend.session_identity("monolynx", "MON-1") != backend.session_identity("monolynx", "MON-2")


# ---------------------------------------------------------------------------
# normalize_record
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNormalizeRecord:
    def _base_kwargs(self, **overrides):
        kwargs = dict(
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=123,
            name="monolynx-MON-1-opus",
            state="running",
            started_at=1000.0,
        )
        kwargs.update(overrides)
        return kwargs

    @pytest.mark.parametrize("valid_state", ["running", "waiting", "success", "failure"])
    def test_valid_states_are_accepted(self, valid_state):
        record = backend.normalize_record("claude", **self._base_kwargs(state=valid_state))
        assert record["state"] == valid_state
        assert record["backend"] == "claude"
        assert record["slug"] == "monolynx"
        assert record["key"] == "MON-1"

    def test_unknown_state_raises_value_error(self):
        with pytest.raises(ValueError):
            backend.normalize_record("claude", **self._base_kwargs(state="bogus"))

    def test_unsupported_backend_raises(self):
        with pytest.raises(backend.UnsupportedBackendError):
            backend.normalize_record("gpt5", **self._base_kwargs())

    def test_optional_fields_default_to_none(self):
        record = backend.normalize_record("codex", **self._base_kwargs())
        assert record.get("thread_id") is None
        assert record.get("log_path") is None
        assert record.get("warning") is None


# ---------------------------------------------------------------------------
# load_registry / save_registry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryPersistence:
    def test_missing_file_returns_empty_dict(self, tmp_path):
        assert backend.load_registry(tmp_path / "brak.json") == {}

    def test_round_trip_preserves_content(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=123,
            name=None,
            state="running",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {"monolynx:MON-1": record})

        loaded = backend.load_registry(registry_path)

        assert loaded == {"monolynx:MON-1": record}

    def test_corrupted_json_returns_empty_dict_not_raise(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        registry_path.write_text("{niepoprawny json")

        assert backend.load_registry(registry_path) == {}

    def test_non_object_json_returns_empty_dict(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        registry_path.write_text("[1, 2, 3]")

        assert backend.load_registry(registry_path) == {}


# ---------------------------------------------------------------------------
# parse_codex_jsonl (fixture z przechwycenia -- external-tool-output.md pkt 1)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestParseCodexJsonlRealFixture:
    """tests/fixtures/codex/exec-json.0.155.1.jsonl -- realne przechwycenie
    `codex exec --json`, codex-cli 0.155.1, 2026-09-20 (patrz README fixture)."""

    def test_thread_id_extracted_from_thread_started(self):
        result = backend.parse_codex_jsonl(_codex_jsonl_body())
        assert result["thread_id"] == "01a0bfd0-dc52-7430-936f-8b7e86639558"

    def test_turn_completed_is_true(self):
        result = backend.parse_codex_jsonl(_codex_jsonl_body())
        assert result["turn_completed"] is True

    def test_agent_message_text_is_collected(self):
        result = backend.parse_codex_jsonl(_codex_jsonl_body())
        assert "PONG" in result["messages"]

    def test_error_item_is_collected_as_error_not_exception(self):
        """item.completed z item.type == "error" (ostrzezenie o skroconych opisach
        skilli, NIE fatalny blad turn) jest zebrany do errors[], parsowanie sie nie
        wywraca i turn_completed zostaje True (patrz README: ten error jest
        kosmetyczny, sesja i tak skonczyla sie sukcesem)."""
        result = backend.parse_codex_jsonl(_codex_jsonl_body())
        assert any("skills context budget" in err for err in result["errors"])
        assert result["turn_completed"] is True

    def test_no_unknown_types_in_real_fixture(self):
        result = backend.parse_codex_jsonl(_codex_jsonl_body())
        assert result["unknown_types"] == []


@pytest.mark.unit
class TestParseCodexJsonlRobustness:
    """Granica systemu (external-tool-output.md pkt 1 i 3): typ zdarzenia spoza
    znanego zestawu, albo linia niepoprawna jako JSON, nie moze rzucic wyjatku ani
    dac falszywego turn_completed/error."""

    def test_unknown_event_type_is_collected_not_raised(self):
        text = (
            '{"type":"thread.started","thread_id":"t1"}\n'
            '{"type":"something.new.from.future.codex","foo":"bar"}\n'
            '{"type":"turn.completed","usage":{}}\n'
        )
        result = backend.parse_codex_jsonl(text)
        assert result["thread_id"] == "t1"
        assert result["turn_completed"] is True
        assert "something.new.from.future.codex" in result["unknown_types"]

    def test_unknown_type_alone_does_not_mark_turn_completed(self):
        """Wariant odwracalny (pkt 3): typ nierozpoznany NIE jest cicho traktowany
        jako zakonczenie tury -- bez wyraznego turn.completed, turn_completed
        zostaje False."""
        text = '{"type":"thread.started","thread_id":"t1"}\n{"type":"weird.unknown"}\n'
        result = backend.parse_codex_jsonl(text)
        assert result["turn_completed"] is False
        assert "weird.unknown" in result["unknown_types"]

    def test_malformed_json_line_is_skipped_not_raised(self):
        text = '{"type":"thread.started","thread_id":"t1"}\n{to nie jest poprawny json\n{"type":"turn.completed"}\n'
        result = backend.parse_codex_jsonl(text)
        assert result["thread_id"] == "t1"
        assert result["turn_completed"] is True

    def test_empty_text_returns_defaults_without_raising(self):
        result = backend.parse_codex_jsonl("")
        assert result["thread_id"] is None
        assert result["turn_completed"] is False
        assert result["messages"] == []
        assert result["errors"] == []

    def test_only_thread_started_has_no_turn_completed(self):
        """Podstawa scenariusza 'utrata procesu': JSONL bez turn.completed w ogole."""
        text = '{"type":"thread.started","thread_id":"t1"}\n{"type":"turn.started"}\n'
        result = backend.parse_codex_jsonl(text)
        assert result["turn_completed"] is False


# ---------------------------------------------------------------------------
# is_process_alive
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsProcessAlive:
    def test_own_process_pid_is_alive(self):
        assert backend.is_process_alive(os.getpid()) is True

    def test_none_is_not_alive(self):
        assert backend.is_process_alive(None) is False

    def test_finished_process_pid_is_not_alive(self):
        dead_pid = _spawn_dead_pid()
        assert backend.is_process_alive(dead_pid) is False


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


@pytest.fixture
def fakebin(tmp_path):
    """Katalog PATH z atrapami `claude`/`codex` -- sterowalny env FAKE_*, na wzor
    tests/unit/test_sprint_run_tick.py. `codex exec` MARKER_FILE dowodzi, ze
    preflight nigdy nie startuje realnej sesji (tylko --version/login status)."""
    fakebin_dir = tmp_path / "fakebin"
    fakebin_dir.mkdir()

    claude_script = fakebin_dir / "claude"
    claude_script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "--version" ]; then\n'
        '  exit_code="${FAKE_CLAUDE_VERSION_EXIT:-0}"\n'
        '  [ "$exit_code" = "0" ] && echo "${FAKE_CLAUDE_VERSION_OUTPUT:-2.1.270 (Claude Code)}"\n'
        '  exit "$exit_code"\n'
        "fi\n"
        'echo "claude-stub: nieobslugiwane argumenty: $*" >&2\n'
        "exit 127\n"
    )
    claude_script.chmod(0o755)

    codex_script = fakebin_dir / "codex"
    codex_script.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "${1:-}" = "--version" ]; then\n'
        '  exit_code="${FAKE_CODEX_VERSION_EXIT:-0}"\n'
        '  [ "$exit_code" = "0" ] && echo "${FAKE_CODEX_VERSION_OUTPUT:-codex-cli 0.155.1}"\n'
        '  exit "$exit_code"\n'
        "fi\n"
        'if [ "${1:-}" = "login" ] && [ "${2:-}" = "status" ]; then\n'
        '  exit_code="${FAKE_CODEX_LOGIN_EXIT:-0}"\n'
        '  [ "$exit_code" = "0" ] && echo "${FAKE_CODEX_LOGIN_OUTPUT:-Logged in using ChatGPT}"\n'
        '  exit "$exit_code"\n'
        "fi\n"
        'if [ "${1:-}" = "exec" ]; then\n'
        '  [ -n "${FAKE_CODEX_EXEC_MARKER:-}" ] && touch "$FAKE_CODEX_EXEC_MARKER"\n'
        "  exit 0\n"
        "fi\n"
        'echo "codex-stub: nieobslugiwane argumenty: $*" >&2\n'
        "exit 127\n"
    )
    codex_script.chmod(0o755)

    return fakebin_dir


def _env_with_path(fakebin, extra=None):
    env = {"PATH": f"{fakebin}:/usr/bin:/bin"}
    if extra:
        env.update(extra)
    return env


@pytest.mark.unit
class TestPreflight:
    def test_claude_ok_with_version_and_safe_permission_mode(self, fakebin, monkeypatch):
        monkeypatch.setenv("PATH", str(fakebin))
        ok, problems = backend.preflight("claude", env=_env_with_path(fakebin))
        assert ok is True
        assert problems == []

    def test_claude_bypass_permissions_is_a_problem(self, fakebin):
        env = _env_with_path(fakebin, {"MONOLYNX_SPRINT_PERMISSION_MODE": "bypassPermissions"})
        ok, problems = backend.preflight("claude", env=env)
        assert ok is False
        assert any("PERMISSION_MODE" in p or "bypassPermissions" in p for p in problems)

    def test_claude_missing_binary_is_a_problem(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        ok, problems = backend.preflight("claude", env={"PATH": str(empty_dir)})
        assert ok is False
        assert problems

    def test_codex_ok_when_version_and_login_succeed(self, fakebin):
        ok, problems = backend.preflight("codex", env=_env_with_path(fakebin))
        assert ok is True
        assert problems == []

    def test_codex_missing_binary_is_a_problem(self, tmp_path):
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        ok, problems = backend.preflight("codex", env={"PATH": str(empty_dir)})
        assert ok is False
        assert any("codex" in p.lower() for p in problems)

    def test_codex_not_logged_in_is_a_problem(self, fakebin):
        env = _env_with_path(fakebin, {"FAKE_CODEX_LOGIN_OUTPUT": "Not logged in"})
        ok, problems = backend.preflight("codex", env=env)
        assert ok is False
        assert any("login" in p.lower() or "zalogowan" in p.lower() for p in problems)

    def test_codex_ignores_claude_permission_mode_variable(self, fakebin):
        """MON-183: preflight Codex nie zna pojecia MONOLYNX_SPRINT_PERMISSION_MODE
        (to Claude Code permission mode) -- bypassPermissions nie jest dla Codex
        problemem, bo to pojecie po prostu go nie dotyczy."""
        env = _env_with_path(fakebin, {"MONOLYNX_SPRINT_PERMISSION_MODE": "bypassPermissions"})
        ok, problems = backend.preflight("codex", env=env)
        assert ok is True
        assert problems == []

    def test_codex_preflight_never_starts_a_session(self, fakebin, tmp_path):
        """Preflight bez skutkow ubocznych: `codex exec` (spawn sesji) nie jest
        NIGDY wolany przez preflight -- marker pliku, ktory fake `codex exec`
        by utworzyl, pozostaje nieutworzony."""
        marker = tmp_path / "exec-was-called.marker"
        env = _env_with_path(fakebin, {"FAKE_CODEX_EXEC_MARKER": str(marker)})

        backend.preflight("codex", env=env)

        assert not marker.exists()

    def test_unsupported_backend_raises(self, fakebin):
        with pytest.raises(backend.UnsupportedBackendError):
            backend.preflight("gpt5", env=_env_with_path(fakebin))

    def test_never_leaks_secret_env_values(self, fakebin):
        env = _env_with_path(fakebin, {"OPENAI_API_KEY": "sekret-123", "FAKE_CODEX_LOGIN_OUTPUT": "Not logged in"})
        ok, problems = backend.preflight("codex", env=env)
        assert ok is False
        combined = " ".join(problems)
        assert "sekret-123" not in combined


# ---------------------------------------------------------------------------
# start / find_active_session -- idempotencja, tozsamosc, restart
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFindActiveSession:
    def test_none_when_nothing_matches(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        assert backend.find_active_session(slug="monolynx", key="MON-1", registry=backend.load_registry(registry_path)) is None

    def test_finds_live_claude_agent_session(self):
        agents = [bg_claude_agent("monolynx-MON-1-opus")]
        result = backend.find_active_session(slug="monolynx", key="MON-1", registry={}, claude_agents=agents)
        assert result is not None
        assert result["backend"] == "claude"

    def test_finds_live_registry_entry_for_codex(self):
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-2",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        registry = {backend.session_identity("monolynx", "MON-2"): record}
        result = backend.find_active_session(slug="monolynx", key="MON-2", registry=registry)
        assert result is not None
        assert result["backend"] == "codex"

    def test_dead_registry_entry_is_not_returned_as_active(self):
        dead_pid = _spawn_dead_pid()
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-3",
            model="opus",
            runtime_id="rt-1",
            pid=dead_pid,
            name=None,
            state="failure",
            started_at=1000.0,
        )
        registry = {backend.session_identity("monolynx", "MON-3"): record}
        result = backend.find_active_session(slug="monolynx", key="MON-3", registry=registry)
        assert result is None


@pytest.mark.unit
class TestStartIdempotency:
    def test_second_start_for_same_ticket_does_not_spawn_new_process(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "registry.json"
        spawn_calls = []

        def fake_popen(args, **kwargs):
            spawn_calls.append(args)
            return _NonBlockingPopenStub(args, **kwargs)

        monkeypatch.setattr(backend.subprocess, "Popen", fake_popen)

        first = backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="prompt",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )
        second = backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="prompt",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )

        assert len(spawn_calls) == 1
        assert first["runtime_id"] == second["runtime_id"]

    def test_model_change_does_not_restart_ticket_with_live_session(self, tmp_path, monkeypatch):
        """MON-185 AC3: zmiana modelu nie moze spowodowac duplikatu."""
        registry_path = tmp_path / "registry.json"
        spawn_calls = []
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: spawn_calls.append(args) or _NonBlockingPopenStub(args, **kwargs),
        )

        backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="p",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )
        backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="sonnet",
            prompt="p",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )

        assert len(spawn_calls) == 1

    def test_runtime_change_does_not_restart_ticket_with_live_session(self, tmp_path, monkeypatch):
        """MON-185 AC3: zmiana backendu (claude<->codex) miedzy tickami nie moze
        spowodowac duplikatu, gdy poprzednia sesja wciaz zyje."""
        registry_path = tmp_path / "registry.json"
        spawn_calls = []
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: spawn_calls.append(args) or _NonBlockingPopenStub(args, **kwargs),
        )

        first = backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="p",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )
        second = backend.start(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="p",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )

        assert len(spawn_calls) == 1
        assert second["runtime_id"] == first["runtime_id"]

    def test_claude_agents_live_session_blocks_codex_start(self, tmp_path, monkeypatch):
        """Restart dyspozytora: gdy `claude agents` pokazuje zywa sesje dla ticketu,
        start(backend='codex', ...) nie spawnuje nowego procesu Codex."""
        registry_path = tmp_path / "registry.json"
        spawn_calls = []
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: spawn_calls.append(args) or _NonBlockingPopenStub(args, **kwargs),
        )
        agents = [bg_claude_agent("monolynx-MON-9-opus")]

        result = backend.start(
            "codex",
            slug="monolynx",
            key="MON-9",
            model="opus",
            prompt="p",
            cwd=str(tmp_path),
            registry_path=registry_path,
            claude_agents=agents,
        )

        assert spawn_calls == []
        assert result["backend"] == "claude"

    def test_dead_session_is_replaced_by_a_new_start(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "registry.json"
        dead_pid = _spawn_dead_pid()
        dead_record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-4",
            model="opus",
            runtime_id="rt-old",
            pid=dead_pid,
            name=None,
            state="failure",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-4"): dead_record})
        spawn_calls = []
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: spawn_calls.append(args) or _NonBlockingPopenStub(args, **kwargs),
        )

        result = backend.start(
            "codex",
            slug="monolynx",
            key="MON-4",
            model="opus",
            prompt="p",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )

        assert len(spawn_calls) == 1
        assert result["runtime_id"] != "rt-old"

    def test_restart_of_dispatcher_finds_active_work_via_fresh_registry_load(self, tmp_path, monkeypatch):
        """Symulacja restartu: druga 'instancja' dyspozytora czyta rejestr TYLKO z
        dysku (load_registry swiezo), bez zadnego stanu w pamieci wspoldzielonego
        z pierwsza instancja."""
        registry_path = tmp_path / "registry.json"
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: _NonBlockingPopenStub(args, **kwargs),
        )
        backend.start(
            "codex",
            slug="monolynx",
            key="MON-5",
            model="opus",
            prompt="p",
            cwd=str(tmp_path),
            registry_path=registry_path,
        )

        fresh_registry = backend.load_registry(registry_path)
        found = backend.find_active_session(slug="monolynx", key="MON-5", registry=fresh_registry)

        assert found is not None
        assert found["key"] == "MON-5"


@pytest.mark.unit
class TestStartSpawnsCodexProcess:
    def test_stdin_is_devnull_and_process_is_backgrounded(self, tmp_path, monkeypatch):
        captured = {}

        def fake_popen(args, **kwargs):
            captured["args"] = args
            captured["kwargs"] = kwargs
            return _NonBlockingPopenStub(args, **kwargs)

        monkeypatch.setattr(backend.subprocess, "Popen", fake_popen)

        backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="zrob cos",
            cwd=str(tmp_path),
            registry_path=tmp_path / "registry.json",
        )

        assert captured["kwargs"].get("stdin") == subprocess.DEVNULL

    def test_stdout_and_stderr_are_separate_files(self, tmp_path, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: captured.update(kwargs=kwargs) or _NonBlockingPopenStub(args, **kwargs),
        )

        backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="zrob cos",
            cwd=str(tmp_path),
            registry_path=tmp_path / "registry.json",
        )

        stdout_target = captured["kwargs"].get("stdout")
        stderr_target = captured["kwargs"].get("stderr")
        assert stdout_target is not None
        assert stderr_target is not None
        assert stdout_target is not stderr_target

    def test_codex_args_never_pass_model_alias_to_m_flag(self, tmp_path, monkeypatch):
        """Potwierdzony fakt: `codex exec -m` nie przyjmuje aliasow opus/sonnet --
        MONOLYNX_SPRINT_MODEL (uzywane tylko do identity/nazewnictwa) nie moze
        trafic literalnie do `-m`/`--model`."""
        captured = {}
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: captured.update(args=args) or _NonBlockingPopenStub(args, **kwargs),
        )

        backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="zrob cos",
            cwd=str(tmp_path),
            registry_path=tmp_path / "registry.json",
        )

        args = captured["args"]
        assert "-m" not in args
        assert "--model" not in args
        assert "opus" not in args

    def test_codex_args_never_pass_name_flag(self, tmp_path, monkeypatch):
        """`codex exec` nie ma flagi --name (potwierdzone `codex exec --help`)."""
        captured = {}
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: captured.update(args=args) or _NonBlockingPopenStub(args, **kwargs),
        )

        backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="zrob cos",
            cwd=str(tmp_path),
            registry_path=tmp_path / "registry.json",
        )

        assert "--name" not in captured["args"]

    def test_codex_args_include_json_and_skip_git_repo_check(self, tmp_path, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: captured.update(args=args) or _NonBlockingPopenStub(args, **kwargs),
        )

        backend.start(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="zrob cos",
            cwd=str(tmp_path),
            registry_path=tmp_path / "registry.json",
        )

        args = captured["args"]
        assert args[0] == "codex"
        assert "exec" in args
        assert "--json" in args
        assert "--skip-git-repo-check" in args


@pytest.mark.unit
class TestStartSpawnsClaudeProcess:
    def test_claude_args_include_bg_name_and_model(self, tmp_path, monkeypatch):
        captured = {}
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: captured.update(args=args) or _NonBlockingPopenStub(args, **kwargs),
        )

        backend.start(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="/monolynx:work MON-1",
            cwd=str(tmp_path),
            registry_path=tmp_path / "registry.json",
        )

        args = captured["args"]
        assert args[0] == "claude"
        assert "--bg" in args
        assert "--name" in args
        name_index = args.index("--name")
        assert args[name_index + 1] == "monolynx-MON-1-opus"
        assert "--model" in args
        model_index = args.index("--model")
        assert args[model_index + 1] == "opus"

    def test_claude_process_is_not_awaited(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: _NonBlockingPopenStub(args, **kwargs),
        )

        result = backend.start(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            prompt="/monolynx:work MON-1",
            cwd=str(tmp_path),
            registry_path=tmp_path / "registry.json",
        )

        assert result["backend"] == "claude"


# ---------------------------------------------------------------------------
# stop -- cleanup dotyka WYLACZNIE zasobow wlasnej sesji
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStop:
    def test_removes_own_entry_from_registry(self, tmp_path, monkeypatch):
        """os.kill zamockowane -- record.pid == os.getpid() (proces testu pytest),
        a stop() dla dopasowanej tozsamosci/pid faktycznie wola os.kill(pid, SIGTERM)
        (patrz test_sends_sigterm_only_to_own_matching_pid). Bez mocka ten test
        wyslalby SIGTERM do wlasnego procesu pytest (zaobserwowane realnie: kod
        wyjscia -15, bez wyjatku do zlapania)."""
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: None)
        registry_path = tmp_path / "registry.json"
        own = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-own",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): own})

        backend.stop(own, registry_path=registry_path)

        assert backend.session_identity("monolynx", "MON-1") not in backend.load_registry(registry_path)

    def test_leaves_other_identities_untouched(self, tmp_path, monkeypatch):
        """os.kill zamockowane -- patrz uwaga WHY w test_removes_own_entry_from_registry."""
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: None)
        registry_path = tmp_path / "registry.json"
        own = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-own",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        other = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-2",
            model="opus",
            runtime_id="rt-other",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=2000.0,
        )
        backend.save_registry(
            registry_path,
            {
                backend.session_identity("monolynx", "MON-1"): own,
                backend.session_identity("monolynx", "MON-2"): other,
            },
        )

        backend.stop(own, registry_path=registry_path)

        remaining = backend.load_registry(registry_path)
        assert backend.session_identity("monolynx", "MON-2") in remaining
        assert remaining[backend.session_identity("monolynx", "MON-2")] == other

    def test_does_not_remove_entry_replaced_by_another_process(self, tmp_path, monkeypatch):
        """Rejestr zostal w miedzyczasie zastapiony innym wpisem pod tym samym kluczem
        (inny runtime_id/pid) -- stop() ma wykryc niezgodnosc i NIC nie usuwac,
        zamiast slepo kasowac po samym kluczu tozsamosci. os.kill zamockowane
        defensywnie: rekord przekazany do stop() ma pid=111111 (nie powinien nigdy
        trafic do os.kill), ale wpis W REJESTRZE ma pid=os.getpid() -- gdyby
        implementacja kiedys zaczela czytac PID z rejestru zamiast z przekazanego
        rekordu, ten test nie moze przy okazji ubic procesu testowego."""
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: None)
        registry_path = tmp_path / "registry.json"
        stale_reference = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-stale",
            pid=111111,
            name=None,
            state="running",
            started_at=1000.0,
        )
        current_in_registry = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-current",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=5000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): current_in_registry})

        backend.stop(stale_reference, registry_path=registry_path)

        remaining = backend.load_registry(registry_path)
        assert backend.session_identity("monolynx", "MON-1") in remaining
        assert remaining[backend.session_identity("monolynx", "MON-1")]["runtime_id"] == "rt-current"

    def test_sends_sigterm_only_to_own_matching_pid(self, tmp_path, monkeypatch):
        """WHY filtrowanie po sygnale, nie surowa lista `killed`: od poprawki Zadania 1
        (uzupelnienie, krytyk -- stan running+martwy PID rowniez nie ma zabijac) stop()
        najpierw sonduje `is_process_alive(pid)`, co samo w sobie jest `os.kill(pid, 0)`.
        Ten test monkeypatchuje `backend.os.kill` globalnie, wiec przechwytuje TAKZE ta
        probe (sygnal 0), nie tylko finalny SIGTERM. Filtrujemy po sygnale, zeby
        asercja mowila o tym, co faktycznie ma znaczenie: dokladnie jeden SIGTERM pod
        ten jeden pid -- niezaleznie od liczby wewnetrznych sond zywotnosci."""
        registry_path = tmp_path / "registry.json"
        own = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-own",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): own})
        killed = []
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: killed.append((pid, sig)))

        backend.stop(own, registry_path=registry_path)

        sigterm_calls = [call for call in killed if call[1] == backend.signal.SIGTERM]
        assert sigterm_calls == [(os.getpid(), backend.signal.SIGTERM)]

    def test_stale_reference_never_triggers_kill(self, tmp_path, monkeypatch):
        registry_path = tmp_path / "registry.json"
        stale_reference = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-stale",
            pid=111111,
            name=None,
            state="running",
            started_at=1000.0,
        )
        current_in_registry = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-current",
            pid=222222,
            name=None,
            state="running",
            started_at=5000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): current_in_registry})
        killed = []
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: killed.append((pid, sig)))

        backend.stop(stale_reference, registry_path=registry_path)

        assert killed == []

    def test_terminal_state_success_does_not_send_sigterm(self, tmp_path, monkeypatch):
        """MON-185 code review (krytyk, -10 backend-developerowi, sprint_run_backend.py:664-671):
        stop() wysylal SIGTERM pod pid z rejestru bez sprawdzenia stanu rekordu. To sciezka
        DOMYSLNA, nie skrajna: KROK 3 skilla woal stop() przy werdykcie `done`, czyli
        DOKLADNIE wtedy, gdy proces Codeksa juz nie zyje -- system mogl w miedzyczasie
        przydzielic ten PID komukolwiek innemu, wiec sygnal lecialby w obcy proces
        uzytkownika (ticket wprost zabrania zabijania cudzych procesow). Rekord w stanie
        terminalnym (success) nie ma kogo ubijac -- os.kill nie powinien byc wolany w ogole.
        Dowodem jest brak wywolania os.kill (licznik == 0), nie rekonstrukcja realnego
        recyklingu PID przez system (niedeterministyczne, wiec go nie symulujemy)."""
        registry_path = tmp_path / "registry.json"
        own = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-own",
            pid=os.getpid(),
            name=None,
            state="success",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): own})
        killed = []
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: killed.append((pid, sig)))

        backend.stop(own, registry_path=registry_path)

        assert killed == []
        assert backend.session_identity("monolynx", "MON-1") not in backend.load_registry(registry_path)

    def test_terminal_state_failure_does_not_send_sigterm(self, tmp_path, monkeypatch):
        """Jak test_terminal_state_success_does_not_send_sigterm, dla stanu failure --
        rownie terminalny, ten sam wymog braku os.kill."""
        registry_path = tmp_path / "registry.json"
        own = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-own",
            pid=os.getpid(),
            name=None,
            state="failure",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): own})
        killed = []
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: killed.append((pid, sig)))

        backend.stop(own, registry_path=registry_path)

        assert killed == []
        assert backend.session_identity("monolynx", "MON-1") not in backend.load_registry(registry_path)

    def test_running_state_still_sends_sigterm_exactly_once_regression(self, tmp_path, monkeypatch):
        """Kotwica antyregresyjna: poprawka dla stanow terminalnych (success/failure) i dla
        running+martwy PID nie moze zepsuc przerwania faktycznie dzialajacej sesji -- stan
        running z zywym procesem nadal ma wyslac dokladnie jeden SIGTERM pod ten jeden pid.
        Duplikuje intencje test_sends_sigterm_only_to_own_matching_pid celowo, jako osobna
        kotwica dla tej konkretnej zmiany w stop(). Filtrujemy po sygnale -- patrz WHY w
        test_sends_sigterm_only_to_own_matching_pid: `killed` zawiera tez sonde
        is_process_alive (os.kill(pid, 0))."""
        registry_path = tmp_path / "registry.json"
        own = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-own",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): own})
        killed = []
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: killed.append((pid, sig)))

        backend.stop(own, registry_path=registry_path)

        sigterm_calls = [call for call in killed if call[1] == backend.signal.SIGTERM]
        assert sigterm_calls == [(os.getpid(), backend.signal.SIGTERM)]
        assert backend.session_identity("monolynx", "MON-1") not in backend.load_registry(registry_path)

    def test_running_state_with_dead_process_does_not_send_sigterm(self, tmp_path, monkeypatch):
        """Uzupelnienie Zadania 1 (krytyk, po drugim review): stan `running` NIE
        wystarcza sam w sobie -- rekord moze byc nieodswiezony (np. `sprint_run_tick.py
        stop` wywolane samodzielnie, bez poprzedzajacego `state`, ktore normalnie
        przelicza status i zapisuje rejestr przed klasyfikacja w sciezce skilla). Jesli
        proces pod tym pid juz nie zyje, system mogl przydzielic ten pid komukolwiek
        innemu -- os.kill nie moze polecieć. `is_process_alive` juz istnieje w adapterze
        i nie parsuje niczyjego wyjscia (samo os.kill(pid, 0)), wiec nie doklada dlugu
        wobec external-tool-output.md. Podmieniamy `is_process_alive` bezposrednio
        (nie zabijamy realnych procesow, nie symulujemy recyklingu pid -- niedeterministyczne).

        Swiadoma granica (zapisana tu, zeby nastepny czytelnik nie wzial braku testu za
        przeoczenie): PID przejety przez INNY zywy proces nadal dostanie sygnal przy
        stanie `running` -- weryfikacja tozsamosci procesu wymagalaby czytania /proc albo
        `ps`, czyli nowego parsera cudzego wyjscia razem z fixture'em, co jest wieksza
        powierzchnia niz problem, ktory ten test zamyka."""
        monkeypatch.setattr(backend, "is_process_alive", lambda pid: False)
        registry_path = tmp_path / "registry.json"
        own = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-own",
            pid=999999,
            name=None,
            state="running",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-1"): own})
        killed = []
        monkeypatch.setattr(backend.os, "kill", lambda pid, sig: killed.append((pid, sig)))

        backend.stop(own, registry_path=registry_path)

        assert killed == []
        assert backend.session_identity("monolynx", "MON-1") not in backend.load_registry(registry_path)


# ---------------------------------------------------------------------------
# status -- mapowanie stanu Codex (running/waiting/success/failure), utrata procesu
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStatusCodex:
    def _record(self, pid, log_path=None, state="running"):
        return backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=pid,
            name=None,
            state=state,
            started_at=1000.0,
            log_path=str(log_path) if log_path else None,
        )

    def test_alive_process_is_running_regardless_of_log_content(self, tmp_path):
        """Proces zywy -> running, NIEZALEZNIE od tresci JSONL (nawet gdy zawiera
        wylacznie nieznane typy zdarzen -- wariant odwracalny)."""
        log_path = tmp_path / "codex.jsonl"
        log_path.write_text('{"type":"totally.unknown"}\n')
        record = self._record(pid=os.getpid(), log_path=log_path)

        result = backend.status(record)

        assert result["state"] == "running"

    def test_dead_process_with_turn_completed_is_success(self, tmp_path):
        log_path = tmp_path / "codex.jsonl"
        log_path.write_text(_codex_jsonl_body())
        dead_pid = _spawn_dead_pid()
        record = self._record(pid=dead_pid, log_path=log_path)

        result = backend.status(record)

        assert result["state"] == "success"

    def test_dead_process_without_turn_completed_is_failure_not_success(self, tmp_path):
        """MON-185 AC4 -- utrata procesu: PID nie zyje, JSONL nie ma turn.completed
        -> failure, NIGDY success."""
        log_path = tmp_path / "codex.jsonl"
        log_path.write_text('{"type":"thread.started","thread_id":"t1"}\n{"type":"turn.started"}\n')
        dead_pid = _spawn_dead_pid()
        record = self._record(pid=dead_pid, log_path=log_path)

        result = backend.status(record)

        assert result["state"] == "failure"

    def test_dead_process_with_missing_log_file_is_failure_without_crashing(self, tmp_path):
        dead_pid = _spawn_dead_pid()
        record = self._record(pid=dead_pid, log_path=tmp_path / "brak.jsonl")

        result = backend.status(record)

        assert result["state"] == "failure"

    def test_codex_status_never_returns_waiting_when_alive(self, tmp_path):
        """Codex nie ma natywnego sygnalu 'czeka na zgode' (MON-184) -- adapter
        status() dla codex nigdy nie zwraca 'waiting'; marker SPRINT-RUN STOP:
        obsluguje classify_normalized na poziomie tresci logu, nie tutaj."""
        record = self._record(pid=os.getpid(), log_path=None)
        result = backend.status(record)
        assert result["state"] != "waiting"

    def test_codex_status_never_returns_waiting_when_dead(self, tmp_path):
        record = self._record(pid=_spawn_dead_pid(), log_path=None)
        result = backend.status(record)
        assert result["state"] != "waiting"


@pytest.mark.unit
class TestStatusClaude:
    def _record(self, agent_state="running", waiting_for_permission=False):
        record = backend.normalize_record(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="a1111111",
            pid=None,
            name="monolynx-MON-1-opus",
            state="running",
            started_at=1000.0,
        )
        record["_raw_agent"] = bg_claude_agent("monolynx-MON-1-opus", agent_state=agent_state)
        if waiting_for_permission:
            record["_raw_agent"].update({"status": "waiting", "waitingFor": "permission prompt"})
        return record

    def test_dead_claude_state_maps_to_failure(self):
        record = self._record(agent_state="exited")
        result = backend.status(record)
        assert result["state"] == "failure"

    def test_live_claude_state_maps_to_running(self):
        record = self._record(agent_state="working")
        result = backend.status(record)
        assert result["state"] == "running"

    def test_permission_prompt_maps_to_waiting(self):
        """MON-173, ale na poziomie adaptera: Claude MOZE natywnie zglosic 'waiting'
        (w przeciwienstwie do Codex, patrz TestStatusCodex.test_codex_status_never_returns_waiting)."""
        record = self._record(agent_state="blocked", waiting_for_permission=True)
        result = backend.status(record)
        assert result["state"] == "waiting"


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLogs:
    def test_codex_logs_extract_messages_from_jsonl(self, tmp_path):
        log_path = tmp_path / "codex.jsonl"
        log_path.write_text(_codex_jsonl_body())
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
            log_path=str(log_path),
        )

        tail = backend.logs(record, n=10)

        assert any("PONG" in line for line in tail)

    def test_claude_logs_strip_ansi_like_read_log_tail(self, tmp_path):
        """Parytet z sprint_run_state.read_log_tail -- ten sam fixture realny."""
        fixture_path = CLAUDE_FIXTURES_DIR / "logs-tui.2.1.270.txt"
        record = backend.normalize_record(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="a1111111",
            pid=None,
            name="monolynx-MON-1-opus",
            state="running",
            started_at=1000.0,
            log_path=str(fixture_path),
        )

        tail = backend.logs(record, n=5)

        assert tail
        assert all("\x1b" not in line for line in tail)

    def test_claude_logs_equal_render_log_of_spinner_fixture(self):
        """MON-227: parytet z render_log (emulacja ekranu, filtr spinnera, dedup) na realnym
        fixture logs-spinner-redraw.2.1.284.txt -- nie tylko "bez bajtow ESC"."""
        fixture_path = CLAUDE_FIXTURES_DIR / "logs-spinner-redraw.2.1.284.txt"
        record = backend.normalize_record(
            "claude",
            slug="monolynx",
            key="MON-227",
            model="opus",
            runtime_id="a2222222",
            pid=None,
            name="monolynx-MON-227-opus",
            state="running",
            started_at=1000.0,
            log_path=str(fixture_path),
        )
        expected = state.render_log(fixture_path.read_bytes().decode("utf-8", errors="replace"))

        tail = backend.logs(record, n=60)

        assert tail == expected[-60:]
        assert backend.logs(record, n=3) == expected[-3:]
        assert not [line for line in tail if "Blanching" in line]
        assert "Logi dostępne tylko dla żywych sesji." in "\n".join(tail)

    def test_missing_log_path_returns_empty_list(self):
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        assert backend.logs(record, n=10) == []


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestResume:
    def test_codex_resume_uses_thread_id_and_last_flag(self, tmp_path, monkeypatch):
        """Sygnatura potwierdzona (nie zgadywana): `codex exec resume [SESSION_ID]
        [PROMPT] --last`."""
        captured = {}
        monkeypatch.setattr(
            backend.subprocess,
            "Popen",
            lambda args, **kwargs: captured.update(args=args) or _NonBlockingPopenStub(args, **kwargs),
        )
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
            thread_id="01a0bfd0-dc52-7430-936f-8b7e86639558",
        )

        backend.resume("codex", record)

        args = captured["args"]
        assert args[0] == "codex"
        assert "exec" in args
        assert "resume" in args
        assert "01a0bfd0-dc52-7430-936f-8b7e86639558" in args
        assert "--last" in args

    def test_claude_resume_is_not_supported(self):
        record = backend.normalize_record(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="a1111111",
            pid=None,
            name="monolynx-MON-1-opus",
            state="running",
            started_at=1000.0,
        )
        with pytest.raises(NotImplementedError):
            backend.resume("claude", record)


# ---------------------------------------------------------------------------
# list_sessions -- sloty liczone lacznie niezaleznie od backendu
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListSessions:
    def test_merges_claude_and_codex_sessions(self, tmp_path):
        registry_path = tmp_path / "registry.json"
        codex_record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-2",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        backend.save_registry(registry_path, {backend.session_identity("monolynx", "MON-2"): codex_record})
        claude_agents = [bg_claude_agent("monolynx-MON-1-opus")]

        sessions = backend.list_sessions("codex", registry_path=registry_path, claude_agents=claude_agents)

        assert any(s["key"] == "MON-2" and s["backend"] == "codex" for s in sessions)


# ---------------------------------------------------------------------------
# classify_normalized -- bridge do sprint_run_state.classify (klasyfikator wspolny)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyNormalizedBridge:
    """classify()/summarize() w sprint_run_state.py POZOSTAJA NIEZMIENIONE -- ten
    bridge tylko mapuje ksztalt, zero nowej logiki werdyktow (zero regresji Claude,
    patrz test_sprint_run_state.py)."""

    def test_none_record_todo_ticket_is_free(self):
        verdict, reason = backend.classify_normalized("todo", None, [])
        assert verdict == "free"
        assert reason

    def test_running_codex_record_normal_log_is_running(self):
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        verdict, _ = backend.classify_normalized("in_progress", record, ["czytam pliki", "pisze testy"])
        assert verdict == "running"

    def test_success_codex_record_in_review_ticket_is_done(self):
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="success",
            started_at=1000.0,
        )
        verdict, _ = backend.classify_normalized("in_review", record, [])
        assert verdict == "done"

    def test_failure_codex_record_in_progress_ticket_is_needs_human(self):
        record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="failure",
            started_at=1000.0,
        )
        verdict, reason = backend.classify_normalized("in_progress", record, [])
        assert verdict == "needs_human"
        assert "failure" in reason.lower()

    def test_running_claude_record_and_running_codex_record_give_same_verdict(self):
        """Ten sam werdykt niezaleznie od pochodzenia rekordu (Claude vs Codex) dla
        rownowaznego stanu i logu -- to jest istota 'jednego algorytmu dyspozytora'."""
        claude_record = backend.normalize_record(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="a1111111",
            pid=None,
            name="monolynx-MON-1-opus",
            state="running",
            started_at=1000.0,
        )
        codex_record = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-2",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        log_tail = ["czytam pliki", "pisze testy"]

        claude_verdict, _ = backend.classify_normalized("in_progress", claude_record, log_tail)
        codex_verdict, _ = backend.classify_normalized("in_progress", codex_record, log_tail)

        assert claude_verdict == codex_verdict == "running"


@pytest.mark.unit
class TestClassifyNormalizedStopMarker:
    """Marker 'SPRINT-RUN STOP:' dziala identycznie dla obu backendow, bo
    classify_normalized deleguje do TEJ SAMEJ sprint_run_state.classify() (frozen
    enum werdyktow: free/running/waiting/done/needs_human -- STOP marker daje
    'waiting', NIE nowa wartosc; patrz test_sprint_run_state.py::TestClassify dla
    kontraktu zrodlowego, tutaj tylko potwierdzamy ze bridge go nie psuje)."""

    @pytest.mark.parametrize("origin_backend", ["claude", "codex"])
    def test_stop_marker_gives_waiting_regardless_of_backend(self, origin_backend):
        pid = os.getpid() if origin_backend == "codex" else None
        record = backend.normalize_record(
            origin_backend,
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="rt-1",
            pid=pid,
            name="monolynx-MON-1-opus" if origin_backend == "claude" else None,
            state="running",
            started_at=1000.0,
        )
        log_tail = ["robie rzeczy", "SPRINT-RUN STOP: brak toolchain"]

        verdict, reason = backend.classify_normalized("in_progress", record, log_tail)

        assert verdict == "waiting"
        assert reason


@pytest.mark.unit
class TestClassifyNormalizedMixedSlots:
    """Sloty (podsumowanie run/waiting/done/needs_human/remaining) liczone lacznie,
    niezaleznie od backendu -- test na poziomie summarize() z sprint_run_state.py,
    zasilany werdyktami z classify_normalized dla mieszanych rekordow."""

    def test_summary_counts_claude_and_codex_verdicts_together(self):
        claude_running = backend.normalize_record(
            "claude",
            slug="monolynx",
            key="MON-1",
            model="opus",
            runtime_id="a1111111",
            pid=None,
            name="monolynx-MON-1-opus",
            state="running",
            started_at=1000.0,
        )
        codex_running = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-2",
            model="opus",
            runtime_id="rt-1",
            pid=os.getpid(),
            name=None,
            state="running",
            started_at=1000.0,
        )
        codex_done = backend.normalize_record(
            "codex",
            slug="monolynx",
            key="MON-3",
            model="opus",
            runtime_id="rt-2",
            pid=os.getpid(),
            name=None,
            state="success",
            started_at=1000.0,
        )

        rows = []
        for ticket_status, record in [("in_progress", claude_running), ("in_progress", codex_running), ("in_review", codex_done)]:
            verdict, reason = backend.classify_normalized(ticket_status, record, [])
            rows.append({"key": record["key"], "verdict": verdict, "reason": reason})

        summary = state.summarize(rows)

        assert summary["running"] == 2
        assert summary["done"] == 1
