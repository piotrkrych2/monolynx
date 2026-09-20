#!/usr/bin/env python3
"""Adapter backendow sesji sprintu: Claude Code i Codex (MON-185).

Cel: uruchamiac i monitorowac tickety sprintu w osobnych sesjach Codex ORAZ
Claude Code, przy JEDNYM algorytmie dyspozytora. Klasyfikator werdyktow
(classify/summarize w sprint_run_state.py) POZOSTAJE NIEZMIENIONY - ten modul
tylko normalizuje stan obu backendow do wspolnego ksztaltu i mapuje go na
argumenty, ktore classify() juz dzis przyjmuje (patrz classify_normalized
nizej, oraz tests/unit/test_sprint_run_state.py::TestClassifyBackendAgnostic).

Adapter minimalny: wybor backendu po nazwie stringowej "claude" albo "codex",
bez fabryki wtyczek (dwie implementacje to za malo na abstrakcje klasowa).

Rejestr wlasny dyspozytora (load_registry/save_registry, plik JSON
{session_identity: normalized_record}) jest zrodlem prawdy dla Codex - `codex
exec` nie ma odpowiednika `claude agents --json --all`. Potwierdzone:
`~/.codex/session_index.jsonl` NIE zawiera sesji uruchomionych przez `codex
exec` (brief MON-185). Dla Claude rejestr jest pomocniczy (prawdziwym
zrodlem zostaje `claude agents --json --all` przez
sprint_run_state.match_session), ale kazdy nowo wystartowany wpis jest tez w
nim zapisywany dla spojnosci listowania (list_sessions).

`codex exec -m/--model` NIE przyjmuje aliasow "opus"/"sonnet" (potwierdzone) -
model sluzy tu WYLACZNIE do tozsamosci/nazewnictwa, nigdy nie trafia do
argumentow procesu codex. `codex exec` NIE MA flagi `--name` (potwierdzone
`codex exec --help`).

Tozsamosc sesji (session_identity: "<slug>:<key>") jest NIEZALEZNA od modelu
i od backendu - zmiana modelu miedzy tickami albo zmiana runtime (claude<->
codex) dla ticketu z zywa sesja nie moze spowodowac duplikatu (MON-185 AC3):
find_active_session/start() patrza WYLACZNIE na (slug,key), nigdy na model
czy backend zadany w biezacym wywolaniu.

Codex nie ma natywnego sygnalu "czeka na zgode" (MON-184: PreToolUse pod
Codex nie niesie agent_id/agent_type, `permissionDecision:"ask"` fails-open -
patrz tests/fixtures/codex/README.md). status() dla codex dlatego NIGDY nie
zwraca "waiting" - jedyny sygnal oczekiwania na czlowieka pod Codex to marker
tekstowy "SPRINT-RUN STOP:" w tresci logu, wykrywany przez
sprint_run_state.classify() na poziomie tresci (dziala identycznie dla obu
backendow, bo classify_normalized deleguje do TEJ SAMEJ funkcji).

Wymogi implementacyjne pod testowalnosc (tests/unit/test_sprint_run_backend.py
podmienia `sprint_run_backend.subprocess.Popen` i `sprint_run_backend.os.kill`):
modul importuje `subprocess`, `os` i `signal` BEZPOSREDNIO, nigdy przez
`from ... import ...`.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sprint_run_state

_SUPPORTED_BACKENDS = ("claude", "codex")

# Kontrakt adaptera (NIE mylic z session_state_value sprint_run_state.py, ktore
# jest "none"/"running"/"exited" - bridging robi classify_normalized nizej).
_VALID_STATES = ("running", "waiting", "success", "failure")
_LIVE_STATES = ("running", "waiting")
_EXITED_STATES = ("success", "failure")

_SUBPROCESS_TIMEOUT = 30

# MON-195 (duplikat stalej z sprint_run_tick.py - modul ten jest CELOWO
# samodzielny, bez zaleznosci od sprint_run_tick.py, zeby uniknac importu
# cyklicznego: to sprint_run_tick.py importuje TEN modul, nie odwrotnie).
_DEFAULT_CLAUDE_PERMISSION_MODE = "auto"

# Literaly zdarzen JSONL `codex exec --json` wyprowadzone z realnego
# przechwycenia tests/fixtures/codex/exec-json.0.155.1.jsonl (patrz README w
# tym samym katalogu) - WSZYSTKIE cztery typy top-level ("thread.started",
# "turn.started", "item.completed", "turn.completed") i oba typy item
# ("agent_message", "error") wystepuja w tym jednym pliku. Typ spoza tych
# zbiorow trafia do unknown_types (wariant odwracalny, external-tool-output.md
# pkt 3) zamiast rzucac wyjatek albo byc cicho zignorowany.
_KNOWN_TOP_TYPES = frozenset({"thread.started", "turn.started", "turn.completed", "item.completed"})
_KNOWN_ITEM_TYPES = frozenset({"agent_message", "error"})


class UnsupportedBackendError(ValueError):
    """Nazwa backendu spoza _SUPPORTED_BACKENDS - blad glosny, nigdy ok=False cichy."""


# ---------------------------------------------------------------------------
# Tozsamosc i normalizacja rekordu
# ---------------------------------------------------------------------------


def session_identity(slug: str, key: str) -> str:
    """"<slug>:<key>" - tozsamosc niezalezna od modelu i backendu."""
    return f"{slug}:{key}"


def normalize_record(
    backend: str,
    *,
    slug: str,
    key: str,
    model: str,
    runtime_id: str,
    pid: int | None,
    name: str | None,
    state: str,
    started_at: float,
    updated_at: float | None = None,
    log_path: str | None = None,
    thread_id: str | None = None,
    warning: str | None = None,
) -> dict[str, Any]:
    """Zbuduj znormalizowany rekord sesji - jeden ksztalt dla obu backendow."""
    if backend not in _SUPPORTED_BACKENDS:
        raise UnsupportedBackendError(backend)
    if state not in _VALID_STATES:
        raise ValueError(f"nieznany stan sesji '{state}' (oczekiwano jednego z {_VALID_STATES})")
    return {
        "backend": backend,
        "slug": slug,
        "key": key,
        "model": model,
        "runtime_id": runtime_id,
        "pid": pid,
        "name": name,
        "state": state,
        "started_at": started_at,
        "updated_at": updated_at,
        "log_path": log_path,
        "thread_id": thread_id,
        "warning": warning,
    }


# ---------------------------------------------------------------------------
# Rejestr wlasny dyspozytora
# ---------------------------------------------------------------------------


def load_registry(registry_path: Path) -> dict[str, dict[str, Any]]:
    """Wczytaj rejestr - uszkodzony/brakujacy plik daje {}, nigdy wyjatek.

    Rejestr jest odtwarzalnym cache'em (dla Claude prawdziwym zrodlem jest
    `claude agents`, dla Codex kolejny tick po prostu zobaczy ticket jako
    wolny i uruchomi go ponownie) - uszkodzenie pliku nie ma wywalac ticku.
    """
    path = Path(registry_path)
    if not path.is_file():
        return {}
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def save_registry(registry_path: Path, registry: dict[str, dict[str, Any]]) -> None:
    """Zapisz rejestr atomowo (plik tymczasowy + replace), zeby zbieg tickow
    nie zostawil polowicznie zapisanego JSON-a."""
    path = Path(registry_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(path.name + ".tmp")
    tmp_path.write_text(json.dumps(registry, ensure_ascii=False, indent=2))
    tmp_path.replace(path)


# ---------------------------------------------------------------------------
# Parsowanie wyjscia `codex exec --json` (granica systemu)
# ---------------------------------------------------------------------------


def parse_codex_jsonl(text: str) -> dict[str, Any]:
    """Parsuj JSONL `codex exec --json` - linia niepoprawna jako JSON jest
    pomijana (nie rzuca wyjatku), typ nieznany trafia do unknown_types bez
    zmiany turn_completed (external-tool-output.md pkt 1 i 3)."""
    result: dict[str, Any] = {
        "thread_id": None,
        "turn_completed": False,
        "messages": [],
        "errors": [],
        "unknown_types": [],
    }
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(obj, dict):
            continue
        obj_type = obj.get("type")

        if obj_type == "thread.started":
            thread_id = obj.get("thread_id")
            if isinstance(thread_id, str):
                result["thread_id"] = thread_id
        elif obj_type == "turn.started":
            pass
        elif obj_type == "turn.completed":
            result["turn_completed"] = True
        elif obj_type == "item.completed":
            item = obj.get("item")
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "agent_message":
                text_value = item.get("text")
                if isinstance(text_value, str):
                    result["messages"].append(text_value)
            elif item_type == "error":
                message = item.get("message")
                if isinstance(message, str):
                    result["errors"].append(message)
            elif item_type not in _KNOWN_ITEM_TYPES:
                result["unknown_types"].append(f"item.completed:{item_type}")
        elif isinstance(obj_type, str) and obj_type not in _KNOWN_TOP_TYPES:
            result["unknown_types"].append(obj_type)

    return result


# ---------------------------------------------------------------------------
# Zywotnosc procesu
# ---------------------------------------------------------------------------


def is_process_alive(pid: int | None) -> bool:
    """os.kill(pid, 0) - sygnal zerowy, tylko sonduje istnienie procesu, nie
    dostarcza go (bezpieczne takze wobec wlasnego pid)."""
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Proces istnieje, ale nalezy do innego uzytkownika - to wciaz "zywy".
        return True
    except OSError:
        return False
    return True


# ---------------------------------------------------------------------------
# Preflight - BEZ SKUTKOW UBOCZNYCH
# ---------------------------------------------------------------------------


def _command_version_ok(binary: str, env: Mapping[str, str]) -> bool:
    try:
        result = subprocess.run(
            [binary, "--version"], env=dict(env), capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _preflight_claude(env: Mapping[str, str]) -> tuple[bool, list[str]]:
    """Jak dotychczasowy check w sprint_run_tick.py: `claude --version` +
    MONOLYNX_SPRINT_PERMISSION_MODE != bypassPermissions/dangerously-skip-permissions."""
    problems: list[str] = []
    permission_mode_raw = env.get("MONOLYNX_SPRINT_PERMISSION_MODE", _DEFAULT_CLAUDE_PERMISSION_MODE)
    if permission_mode_raw == "bypassPermissions" or "dangerously-skip-permissions" in permission_mode_raw:
        problems.append(
            "MONOLYNX_SPRINT_PERMISSION_MODE="
            + permission_mode_raw
            + " blokuje sesje w tle w trybie auto (regula soft_deny \"Create Unsafe Agents\"); "
            "ustaw MONOLYNX_SPRINT_PERMISSION_MODE=auto w env sledzonego .claude/settings.json "
            "albo swiadoma regule autoMode.allow w ~/.claude/settings.json"
        )
    if not _command_version_ok("claude", env):
        problems.append("claude --version nie odpowiada (brak binarki 'claude' w PATH?)")
    return (not problems), problems


def _preflight_codex(env: Mapping[str, str]) -> tuple[bool, list[str]]:
    """`codex --version` + `codex login status` zawiera "Logged in" - NIGDY
    nie czyta .claude/settings*.json ani MONOLYNX_SPRINT_PERMISSION_MODE
    (MON-183: to pojecie jest specyficzne dla Claude Code permission modes,
    Codex ma wlasny --sandbox/--dangerously-bypass-approvals-and-sandbox).

    Sprawdzenie przez case-sensitive substring "Logged in" (nie
    .lower()) jest CELOWE: "Not logged in" zawiera "logged in" (male l) ale
    NIE zawiera "Logged in" (duze L) - realny output zalogowanej sesji zaczyna
    sie duza litera ("Logged in using ChatGPT", brief MON-185), a
    case-insensitive porownanie dawaloby falszywy pozytyw dla "Not logged in".
    """
    problems: list[str] = []
    if not _command_version_ok("codex", env):
        problems.append("codex --version nie odpowiada (brak binarki 'codex' w PATH?)")
        return False, problems
    try:
        result = subprocess.run(
            ["codex", "login", "status"], env=dict(env), capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        problems.append("codex login status nie odpowiada")
        return False, problems
    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0 or "Logged in" not in output:
        problems.append(
            "codex nie jest zalogowany (codex login status) - uruchom 'codex login' przed startem sprintu"
        )
    return (not problems), problems


def preflight(backend: str, *, env: Mapping[str, str]) -> tuple[bool, list[str]]:
    """(ok, problems) - bez skutkow ubocznych: zadna sesja nie startuje, zaden
    wpis rejestru nie powstaje."""
    if backend not in _SUPPORTED_BACKENDS:
        raise UnsupportedBackendError(backend)
    if backend == "claude":
        return _preflight_claude(env)
    return _preflight_codex(env)


# ---------------------------------------------------------------------------
# find_active_session - identyfikacja niezalezna od backendu/modelu
# ---------------------------------------------------------------------------


def _claude_record_from_agent(session: dict[str, Any], slug: str, key: str) -> dict[str, Any] | None:
    """Zbuduj rekord znormalizowany z wpisu `claude agents --json --all`, o ile
    sesja jest zywa (running/waiting). Zwraca None dla sesji martwej - wywolujacy
    ma wtedy sprawdzic rejestr wlasny zamiast zwracac od razu None."""
    state_value = sprint_run_state.session_state(session)
    if state_value != "running":
        return None
    final_state = "waiting" if sprint_run_state.waits_for_permission(session) else "running"
    name = session.get("name")
    # Model wyciagniety z konwencji nazwy "<slug>-<key>-<model>" (patrz
    # .claude/rules/session-name-model.md) - nie jest krytyczny dla tozsamosci
    # (session_identity nie zalezy od modelu), tylko informacyjny.
    model = ""
    prefix = f"{slug}-{key}-"
    if isinstance(name, str) and name.startswith(prefix):
        model = name[len(prefix) :]
    runtime_id = session.get("id")
    record = normalize_record(
        "claude",
        slug=slug,
        key=key,
        model=model,
        runtime_id=str(runtime_id) if runtime_id else "",
        pid=None,
        name=name if isinstance(name, str) else None,
        state=final_state,
        started_at=float(session.get("startedAt") or 0.0),
    )
    record["_raw_agent"] = session
    return record


def find_active_session(
    *, slug: str, key: str, registry: dict[str, dict[str, Any]], claude_agents: list[Any] | None = None
) -> dict[str, Any] | None:
    """Zwroc zywa (running/waiting) znormalizowana sesje dla (slug,key),
    niezaleznie od backendu/modelu w rekordzie. Sprawdza NAJPIERW claude_agents
    (gdy podane), POTEM rejestr wlasny.

    Rejestr jest trzymany "na wiare" (stan zapisany przy ostatnim start()/
    status()), a nie przeliczany na nowo tutaj (np. przez is_process_alive) -
    odswiezanie nalezy do list_sessions()/status(), wywolywanych na poczatku
    kazdego ticku. Gdyby find_active_session sonduje pid, znany testowy PID
    (proces przechowany w rekordzie, ale nie faktycznie zywy) dawalby falszywe
    "wolny ticket" przy kazdym restarcie dyspozytora zamiast odnalezc pracujaca
    sesje z jej wlasnego ostatniego zapisu stanu.
    """
    if claude_agents is not None:
        session = sprint_run_state.match_session(claude_agents, slug, key)
        if session is not None:
            record = _claude_record_from_agent(session, slug, key)
            if record is not None:
                return record

    entry = registry.get(session_identity(slug, key))
    if entry is not None and entry.get("state") in _LIVE_STATES:
        return entry
    return None


# ---------------------------------------------------------------------------
# start - idempotentny, spawnuje proces w tle bez czekania na koniec
# ---------------------------------------------------------------------------


def _spawn_codex(*, slug: str, key: str, model: str, prompt: str, cwd: str, registry_path: Path) -> dict[str, Any]:
    runtime_id = uuid.uuid4().hex
    logs_dir = Path(registry_path).parent / "codex-logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    base = f"{slug}-{key}-{runtime_id}"
    stdout_path = logs_dir / f"{base}.jsonl"
    stderr_path = logs_dir / f"{base}.stderr.log"

    # Sandbox NIGDY domyslnie na --dangerously-bypass-approvals-and-sandbox ani
    # --approve-for-me (zakaz projektu) - "workspace-write" jest odpowiednikiem
    # trybu acceptEdits uzywanego dla Claude, nadpisywalny swiadomie.
    sandbox_mode = os.environ.get("MONOLYNX_SPRINT_CODEX_SANDBOX", "workspace-write")
    # --worktree: przelacznik BEZ wartosci (AC2 - swiezy worktree na ticket,
    # zarzadzany przez samego Codeksa, w odroznieniu od `claude --bg --worktree
    # <KEY>`, gdzie wartosc podaje wywolujacy). `codex exec --help` (codex-cli
    # 0.155.1, zweryfikowane przez technical-writera w tym tickecie, MON-185):
    #   --worktree
    #       Run the session in a new managed Git worktree
    #   -C, --cd <DIR> / --add-dir <DIR> / -s, --sandbox <SANDBOX_MODE>
    # - flagi z wartoscia maja placeholder w pomocy, --worktree go nie ma.
    # Podanie mu wartosci (poprzedni stan tego kodu) przesuwaloby `key` na
    # pierwszy argument pozycyjny (`codex exec [OPTIONS] [PROMPT]`), a `prompt`
    # trafialby jako nadmiarowy drugi - `codex exec` odrzucalby wywolanie
    # natychmiast (brak turn.completed w JSONL, status() -> "failure").
    args = [
        "codex",
        "exec",
        "--json",
        "--sandbox",
        sandbox_mode,
        "-C",
        str(cwd),
        "--worktree",
        "--skip-git-repo-check",
        prompt,
    ]
    stdout_file = open(stdout_path, "wb")
    stderr_file = open(stderr_path, "wb")
    try:
        # `codex exec` czyta stdin, jesli jest dostepny ("Reading additional
        # input from stdin...") - DEVNULL zapobiega zawiechnieciu procesu w tle
        # bez kontrolujacego terminala (brief MON-185).
        proc = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=stdout_file, stderr=stderr_file)
    finally:
        # Proces dziedziczy wlasna kopie deskryptora - zamykamy nasza, zeby nie
        # kumulowac otwartych plikow przy wielu sesjach w jednym ticku.
        stdout_file.close()
        stderr_file.close()

    return normalize_record(
        "codex",
        slug=slug,
        key=key,
        model=model,
        runtime_id=runtime_id,
        pid=proc.pid,
        name=None,
        state="running",
        started_at=time.time(),
        log_path=str(stdout_path),
    )


def _spawn_claude(
    *, slug: str, key: str, model: str, prompt: str, cwd: str, permission_mode: str | None = None
) -> dict[str, Any]:
    runtime_id = uuid.uuid4().hex
    name = f"{slug}-{key}-{model}"
    args = ["claude", "--bg", "--worktree", key, "--name", name, "--model", model]
    if permission_mode:
        args += ["--permission-mode", permission_mode]
    args.append(prompt)
    proc = subprocess.Popen(args, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return normalize_record(
        "claude",
        slug=slug,
        key=key,
        model=model,
        runtime_id=runtime_id,
        pid=proc.pid,
        name=name,
        state="running",
        started_at=time.time(),
    )


def start(
    backend: str,
    *,
    slug: str,
    key: str,
    model: str,
    prompt: str,
    cwd: str,
    registry_path: Path,
    claude_agents: list[Any] | None = None,
    permission_mode: str | None = None,
) -> dict[str, Any]:
    """Idempotentny: jesli zywa sesja juz istnieje dla (slug,key), zwraca ja
    bez spawnu nowego procesu - niezaleznie od tego, czy zadany backend/model
    sie zgadza z zapisanym (MON-185 AC3)."""
    if backend not in _SUPPORTED_BACKENDS:
        raise UnsupportedBackendError(backend)

    registry = load_registry(registry_path)
    existing = find_active_session(slug=slug, key=key, registry=registry, claude_agents=claude_agents)
    if existing is not None:
        return existing

    if backend == "codex":
        record = _spawn_codex(slug=slug, key=key, model=model, prompt=prompt, cwd=cwd, registry_path=registry_path)
    else:
        record = _spawn_claude(slug=slug, key=key, model=model, prompt=prompt, cwd=cwd, permission_mode=permission_mode)

    registry[session_identity(slug, key)] = record
    save_registry(registry_path, registry)
    return record


# ---------------------------------------------------------------------------
# status - swiezo wyliczony stan
# ---------------------------------------------------------------------------


def _status_codex(record: dict[str, Any]) -> str:
    """Proces zywy -> running niezaleznie od tresci JSONL (nawet same nieznane
    typy zdarzen - wariant odwracalny). Proces martwy: JSONL z turn.completed
    -> success; bez turn.completed (utrata procesu) -> failure, NIGDY success
    (MON-185 AC4). Codex nie ma natywnego sygnalu oczekiwania na zgode
    (MON-184) - ta funkcja NIGDY nie zwraca "waiting"."""
    if is_process_alive(record.get("pid")):
        return "running"
    log_path = record.get("log_path")
    if not log_path:
        return "failure"
    path = Path(log_path)
    if not path.is_file():
        return "failure"
    text = path.read_text(errors="replace")
    parsed = parse_codex_jsonl(text)
    return "success" if parsed["turn_completed"] else "failure"


def _status_claude(record: dict[str, Any]) -> str:
    """Delegacja do sprint_run_state.session_state (mapowanie dead/live) plus
    flaga oczekiwania na zgode (waits_for_permission, MON-173)."""
    raw = record.get("_raw_agent")
    if raw is None:
        # Brak danych zywych (np. rekord z rejestru bez claude_agents w tym
        # wywolaniu) - trzymamy sie ostatnio zapisanego stanu zamiast zgadywac.
        stored = record.get("state")
        return stored if stored in _VALID_STATES else "failure"
    state_value = sprint_run_state.session_state(raw)
    if state_value == "exited":
        return "failure"
    if sprint_run_state.waits_for_permission(raw):
        return "waiting"
    return "running"


def status(record: dict[str, Any]) -> dict[str, Any]:
    """Nowy rekord ze swiezo wyliczonym `state`."""
    backend = record.get("backend")
    if backend not in _SUPPORTED_BACKENDS:
        raise UnsupportedBackendError(backend)
    result = dict(record)
    result["state"] = _status_codex(record) if backend == "codex" else _status_claude(record)
    return result


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


def logs(record: dict[str, Any], n: int = 60) -> list[str]:
    """Ostatnie n niepustych linii tresci, znormalizowane dla obu backendow.

    Galaz `claude` ponizej jest w praktyce martwa: ani `_spawn_claude`, ani
    `_claude_record_from_agent` nie ustawiaja `log_path` (Claude Code nie
    zapisuje logu sesji do pliku, ktory ten adapter mogby wskazac), wiec dla
    backendu claude ta funkcja zawsze trafia w `if not log_path: return []`
    wyzej i nigdy nie dochodzi do galezi ponizej. Sciezka `claude` korzysta
    zamiast tego z `_write_session_log` w `sprint_run_tick.py` (`claude logs
    <id>` + zapis do pliku w TICK_DIR), poza tym adapterem. Galaz zostaje
    tutaj jako spojny interfejs (`logs()` dziala dla obu backendow z rekordu),
    na wypadek gdyby `log_path` zaczelo byc kiedys ustawiane."""
    log_path = record.get("log_path")
    if not log_path:
        return []
    path = Path(log_path)
    if not path.is_file():
        return []
    text = path.read_text(errors="replace")

    if record.get("backend") == "codex":
        parsed = parse_codex_jsonl(text)
        lines = [*parsed["messages"], *parsed["errors"]]
        nonempty = [line.strip() for line in lines if line and line.strip()]
        return nonempty[-n:] if n > 0 else []

    # claude: parytet z sprint_run_state.read_log_tail (ANSI + \r -> \n) -
    # reuzywamy jej regexy prywatne zamiast duplikowac je tutaj, na wzor
    # sprint_run_tick.py::_write_session_log.
    text = sprint_run_state._CSI_CURSOR_GAP_RE.sub(" ", text)
    text = sprint_run_state._ANSI_RE.sub("", text)
    text = text.replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    nonempty = [line for line in lines if line]
    return nonempty[-n:] if n > 0 else []


# ---------------------------------------------------------------------------
# resume
# ---------------------------------------------------------------------------


def resume(backend: str, record: dict[str, Any], *, prompt: str | None = None) -> dict[str, Any]:
    """codex: `codex exec resume <thread_id albo nazwa> [prompt] --last`.
    claude: NIEWSPIERANE - sesje `claude --bg` sa dlugozyjace, resume nie ma
    odpowiednika w kontrakcie dyspozytora Claude (nie to samo co `claude attach`)."""
    if backend not in _SUPPORTED_BACKENDS:
        raise UnsupportedBackendError(backend)
    if backend == "claude":
        raise NotImplementedError(
            "resume nie jest wspierane dla claude -- sesje `claude --bg` sa dlugozyjace i nie "
            "maja odpowiednika 'resume' w kontrakcie dyspozytora (to nie to samo co `claude attach`)."
        )

    target = record.get("thread_id") or record.get("name") or record.get("runtime_id") or ""
    args = ["codex", "exec", "resume", target]
    if prompt:
        args.append(prompt)
    args.append("--last")
    proc = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return normalize_record(
        "codex",
        slug=record.get("slug", ""),
        key=record.get("key", ""),
        model=record.get("model", ""),
        runtime_id=record.get("runtime_id", ""),
        pid=proc.pid,
        name=record.get("name"),
        state="running",
        started_at=time.time(),
        thread_id=record.get("thread_id"),
    )


# ---------------------------------------------------------------------------
# stop - cleanup dotyka WYLACZNIE zasobow wlasnej sesji
# ---------------------------------------------------------------------------


def stop(record: dict[str, Any], *, registry_path: Path) -> None:
    """Usuwa wylacznie wlasny wpis z rejestru - dopasowanie po
    session_identity ORAZ runtime_id/pid rekordu (nie tylko po kluczu). Wpis,
    ktory nie zgadza sie pid/runtime_id (rejestr zmieniony przez inny proces
    w miedzyczasie) nie jest usuwany ani zabijany.

    SIGTERM leci WYLACZNIE, gdy wpis W REJESTRZE (nie przekazany `record`) ma
    stan zywy (running/waiting) I `is_process_alive(pid)` potwierdza, ze pod
    tym PID nadal siedzi jakis proces (MON-185 code review, krytyk -10,
    sprint_run_backend.py:664-671 przed ta poprawka). To sciezka DOMYSLNA, nie
    skrajna: KROK 3 skilla wola `stop` przy werdykcie `done`, czyli dokladnie
    wtedy, gdy proces Codeksa juz nie zyje - system mogl w miedzyczasie
    przydzielic ten PID dowolnemu procesowi uzytkownika, a ticket wprost
    zabrania zabijania cudzych procesow. Stan terminalny (success/failure) w
    ogole nie probuje `os.kill` - nie ma kogo ubijac. Stan zywy, ale PID juz
    martwy (proces skonczyl sie miedzy ostatnim odswiezeniem rejestru a tym
    wywolaniem `stop`) rowniez nie probuje `os.kill` - liczy sie stan wpisu W
    REJESTRZE, nie stan `record` przekazanego przez wywolujacego (ktory moze
    byc nieodswiezony, gdy `stop` jest wolane bez poprzedzajacego `state`).

    Granica, ktorej ta poprawka NIE zamyka (swiadomie, `is_process_alive`
    tylko `os.kill(pid, 0)`, bez weryfikacji tozsamosci procesu przez /proc
    czy `ps` - nowy parser cudzego wyjscia z wymogiem fixture'u, wieksza
    powierzchnia niz problem): jesli PID zostal w miedzyczasie przejety przez
    INNY ZYWY proces, a wpis w rejestrze wciaz ma stan zywy, SIGTERM nadal
    poleci pod ten PID. Wyscig identyczny jak przed poprawka, tylko okno
    czasowe wezsze (od ostatniego odswiezenia stanu, nie od poczatku swiata)."""
    identity = session_identity(record.get("slug", ""), record.get("key", ""))
    registry = load_registry(registry_path)
    current = registry.get(identity)
    if current is None:
        return
    if current.get("runtime_id") != record.get("runtime_id") or current.get("pid") != record.get("pid"):
        return

    del registry[identity]
    save_registry(registry_path, registry)

    pid = current.get("pid")
    if pid is not None and current.get("state") in _LIVE_STATES and is_process_alive(pid):
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            # Proces mogl juz zakonczyc dzialanie miedzy is_process_alive a tym
            # wywolaniem - cleanup nie ma sie wywalic na tym wyscigu.
            pass


# ---------------------------------------------------------------------------
# list_sessions
# ---------------------------------------------------------------------------


def list_sessions(
    backend: str, *, registry_path: Path, claude_agents: list[Any] | None = None
) -> list[dict[str, Any]]:
    """Znormalizowana lista aktywnych sesji DANEGO backendu (odswiezone przez
    status()). Rejestr MOZE zawierac wpisy backendu claude - `start()` je tam
    zapisuje, gdy o to poprosic - ale w praktyce `sprint_run_tick.py start
    --backend claude` jest celowo odrzucany kodem 64 (sesje Claude startuja
    bezposrednio w KROK 4 skilla, nie przez ten adapter - MON-191/MON-192),
    wiec dzisiaj zaden realny wpis backendu claude nie trafia do rejestru tym
    kanalem. Przegladanie samego rejestru (bez rekonstrukcji sesji z
    claude_agents od zera) wystarcza na wypadek, gdyby to sie zmienilo."""
    if backend not in _SUPPORTED_BACKENDS:
        raise UnsupportedBackendError(backend)

    registry = load_registry(registry_path)
    sessions: list[dict[str, Any]] = []
    for record in registry.values():
        if record.get("backend") != backend:
            continue
        working_record = record
        if backend == "claude" and claude_agents is not None:
            raw = sprint_run_state.match_session(claude_agents, record.get("slug", ""), record.get("key", ""))
            if raw is not None:
                working_record = dict(record)
                working_record["_raw_agent"] = raw
        refreshed = status(working_record)
        if refreshed.get("state") in _LIVE_STATES:
            sessions.append(refreshed)
    return sessions


# ---------------------------------------------------------------------------
# classify_normalized - bridge do sprint_run_state.classify
# ---------------------------------------------------------------------------


def classify_normalized(ticket_status: str, record: dict[str, Any] | None, log_tail: list[str]) -> tuple[str, str]:
    """Mapuje record["state"] (running/waiting -> "running", success/failure ->
    "exited", None -> "none") na session_state_value klasycznego kontraktu i
    deleguje do sprint_run_state.classify() - classify()/summarize() POZOSTAJA
    NIEZMIENIONE (zero regresji backendu Claude). Reason jest wzbogacony o
    backend/state rekordu (analogicznie do state_reason w build_report), zeby
    powod byl czytelny niezaleznie od tego, ktory backend go wyprodukowal."""
    if record is None:
        return sprint_run_state.classify(ticket_status, "none", log_tail)

    state_value = record.get("state")
    if state_value in _LIVE_STATES:
        session_state_value = "running"
    elif state_value in _EXITED_STATES:
        session_state_value = "exited"
    else:
        session_state_value = "none"

    verdict, reason = sprint_run_state.classify(ticket_status, session_state_value, log_tail)
    backend_name = record.get("backend", "?")
    reason = f"{reason} (backend={backend_name}, state={state_value})"
    return verdict, reason
