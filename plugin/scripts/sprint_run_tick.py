#!/usr/bin/env python3
"""Tick dyspozytora sprintu w jednym procesie (MON-190).

Stdlib only, styl `sprint_run_state.py`. WHY jednego skryptu: tick uruchomiony
przez `claude -p` ma jedna regule uprawnien `Bash(python3:*)` (plus
`Bash(claude:*)`) - kazda komenda subprocess wewnatrz TEGO skryptu nie
przechodzi przez uprawnienia Claude Code (to zwykly proces potomny narzedzia
Bash), wiec logika, ktora wczesniej byla inline bashem w SKILL.md
(`${...}`, `&&`, `|`, `>`) i byla odrzucana przez klasyfikator trybu -p,
przenosi sie tutaj.

Dwie podkomendy:

  preflight [--project-dir DIR] [--runtime claude|codex]
      Sprawdza srodowisko procesu (nie shella wywolujacego) przed startem
      jakiejkolwiek sesji w tle: git, `claude --version`, `claude agents
      --json --all`, ksztalt `.claude/settings*.json` i flagi MONOLYNX_* w
      `os.environ`. Incydent 18.09: plik settings z `permissions` w zlym
      ksztalcie jest po cichu ignorowany przez Claude Code w trybie `-p`
      (razem z `env`), wiec preflight musi to wykryc PRZED tickiem, nie
      pozwolic sesji w tle utknac na pierwszym pytaniu bez odpowiedzi.
      Dodatkowo (MON-197) ustala `BASE_BRANCH` (lancuch fallbacku
      MONOLYNX_BASE_BRANCH -> MONOLYNX_MR_TARGET -> `git symbolic-ref
      refs/remotes/origin/HEAD` -> `glab repo view`) i `BASE_BRANCH_STALE`
      (rozjazd GLOWNEGO CHECKOUTU dyspozytora, nie worktree ticketu, wobec
      tego brancha) - obie wartosci sa informacyjne, nigdy nie dopisuja do
      `problems` i nie blokuja ticku same z siebie.

      `--runtime` (MON-185, domyslnie "claude") wybiera backend sesji. Dla
      "codex" preflight deleguje do `sprint_run_backend.preflight("codex",
      ...)` (`codex --version` + `codex login status`) i NIGDY nie czyta
      `.claude/settings*.json` ani MONOLYNX_SPRINT_PERMISSION_MODE (MON-183:
      to pojecie jest specyficzne dla Claude Code permission modes). Flagi
      AUTOTEST/AUTOCOMMIT/AUTOPUSH i rozwiazanie brancha bazowego pozostaja
      wspolne dla obu runtime - gatuja dzialania samego ticku (git/testy),
      niezaleznie od tego, ktory backend prowadzi sesje robocza.

  state --slug S --tickets-json JSON [--tick-dir DIR] [--backend claude|codex]
      Zbiera stan sesji w tle i ogony ich logow, zapisuje je w TICK_DIR
      (`.git/monolynx/tick/`, NIGDY w /tmp) i deleguje klasyfikacje werdyktow
      do `sprint_run_state.build_report`/`sprint_run_backend.classify_normalized`
      (logika werdyktow poza zakresem tego ticketu - MON-191).

      `--backend` (MON-185, domyslnie "claude") wybiera zrodlo stanu. Dla
      "claude" zachowanie jest dokladnie dzisiejsze (`claude agents --json
      --all` + `claude logs <id>`). Dla "codex" zrodlem jest WYLACZNIE rejestr
      wlasny dyspozytora (`sprint_run_backend`, plik `sessions.json` w
      TICK_DIR) - `codex exec` nie ma odpowiednika `claude agents --json
      --all`, wiec ta sciezka nigdy nie wywoluje binarki `claude`.

  start --backend claude|codex --slug S --key K --model M --prompt P [--cwd DIR] [--tick-dir DIR]
      Punkt wejscia, ktorego adapterowi `sprint_run_backend` brakowalo do
      MON-190/MON-191 (iteracja 1 z 3, MON-185): bez tej podkomendy
      `sprint_run_backend.start()` nie jest wolany przez nic poza testami i
      zadna sesja Codex nigdy nie startuje.

      `--backend claude` konczy sie kodem 64 bez zadnego efektu - sesje
      Claude startuja bezposrednio w KROK 4 skilla (`claude --bg --worktree
      ...`), zeby przejsc przez system uprawnien Claude Code; wywolanie
      `claude --bg` z wnetrza TEGO skryptu (subprocess procesu tick)
      omijaloby te uprawnienia (MON-191/MON-192).

      `--backend codex`: preflight (`sprint_run_backend.preflight("codex",
      ...)`) idzie PRZED jakimkolwiek dotknieciem dysku (tick-dir, rejestr) -
      awaria preflightu (kod 2) nie zostawia zadnego sladu, nawet gdy rejestr
      juz istnieje. Nastepnie best-effort `claude agents --json --all` (brak
      binarki `claude` NIE jest bledem - `claude_agents=None`) i sprawdzenie
      rejestru: zywy wpis (po `<slug>:<KEY>`, niezaleznie od modelu i
      backendu) -> `ALREADY-RUNNING <identity> <runtime_id>`, kod 0, zero
      nowych procesow. W przeciwnym razie `sprint_run_backend.start()` spawnuje
      proces, zapisuje rejestr i wypisuje `STARTED <identity> <runtime_id>
      <pid>`, kod 0.

  stop --backend claude|codex --slug S --key K [--tick-dir DIR]
      Usuwa wlasny wpis rejestru (SIGTERM do wlasnego PID, inne wpisy
      nietkniete) i wypisuje `STOPPED <identity>` (kod 0) albo, gdy wpisu nie
      ma, `NOT-FOUND <identity>` (rowniez kod 0 - brak sesji do zatrzymania
      nie jest bledem uzycia).

      Wyjscia `start`/`stop` (`STARTED ...`, `ALREADY-RUNNING ...`, `STOPPED
      ...`, `NOT-FOUND ...`) sa zamrozonym kontraktem asertowanym doslownie w
      testach - nie zmieniac formatu bez rownoczesnej zmiany testow.

Bezpieczenstwo: `preflight` wypisuje na stdout WYLACZNIE klucze z ustalonej
listy (patrz `_KEY_ORDER` nizej) - nigdy calego `env`, ani wartosci zmiennych
spoza tej listy. Incydent 18.09: `printenv` w ticku wypisal do transkryptu i
logu petli klucz OpenAI i token GitLaba.

Kody wyjscia (wszystkie podkomendy):
    0  - sukces (start: rowniez ALREADY-RUNNING; stop: rowniez NOT-FOUND).
    2  - blad wejscia/srodowiska (preflight: problemy znalezione; state: zly
         JSON, zly ksztalt ticketow, `claude agents` nie dziala; start: preflight
         codex nieudany, katalog ticku niedostepny).
    64 - blad uzycia CLI (brak/nieznana podkomenda, brak wymaganego argumentu,
         nieznany --backend, start --backend claude).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

import sprint_run_backend
import sprint_run_state

_SUBPROCESS_TIMEOUT = 30

# Kolejnosc i komplet kluczy, ktore preflight wolno wypisac na stdout - patrz
# sekcja "Bezpieczenstwo" w docstringu modulu. Zadna inna zmienna srodowiskowa
# (a w szczegolnosci caly `env` z pliku settings) nie trafia na stdout.
_KEY_ORDER = (
    "SLUG",
    "MODE",
    "PARALLEL",
    "SPRINT_MODEL",
    "PERMISSION_MODE",
    "AUTOTEST",
    "AUTOCOMMIT",
    "AUTOPUSH",
    "AUTOMR",
    "CONTRACT",
    "SPRINT_RUN",
    "CLAUDE_VERSION",
    "PROJECT_DIR",
    "TICK_DIR",
    "BASE_BRANCH",
    "BASE_BRANCH_STALE",
    "RUNTIME",
)

_REQUIRED_AUTOFLAGS = ("MONOLYNX_AUTOTEST", "MONOLYNX_AUTOCOMMIT", "MONOLYNX_AUTOPUSH")
_SETTINGS_FILENAMES = ("settings.json", "settings.local.json")

# MON-195: default zmieniony z "bypassPermissions" na "auto" - patrz check w
# _cmd_preflight. `claude --permission-mode` (sprawdzone `claude --help`)
# przyjmuje "auto" jako legalna wartosc.
_DEFAULT_PERMISSION_MODE = "auto"


class _UsageError(Exception):
    """Blad parsowania argumentow CLI (mapowany na kod wyjscia 64)."""


class _GitError(Exception):
    """Katalog nie jest repozytorium git albo `git` nie odpowiada."""


def _resolve_tick_dir(tick_dir_arg: str | None) -> Path:
    """`--tick-dir` jawny -> uzyj go wprost; inaczej `<absolutny-git-dir>/monolynx/tick`
    (ten sam katalog, ktory `_cmd_preflight` tworzy jako `TICK_DIR`). Wspolne
    dla `state`/`start`/`stop`, zeby wszystkie trzy podkomendy patrzyly na ten
    sam rejestr `sessions.json` bez trzech osobnych implementacji tej samej
    sciezki. Rzuca `_GitError`, gdy katalog nie jest repozytorium git.
    """
    if tick_dir_arg:
        return Path(tick_dir_arg)
    git_dir = _run_git(["rev-parse", "--absolute-git-dir"])
    return Path(git_dir) / "monolynx" / "tick"


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise _UsageError(message)


def _run_git(args: list[str], cwd: str | None = None) -> str:
    try:
        result = subprocess.run(["git", *args], capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT, cwd=cwd)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        raise _GitError(str(exc)) from exc
    if result.returncode != 0:
        raise _GitError(result.stderr.strip() or f"kod wyjscia {result.returncode}")
    return result.stdout.strip()


def _run_claude_version() -> tuple[str | None, str | None]:
    """Zwroc (wersja, problem) - dokladnie jedno z nich jest None."""
    try:
        result = subprocess.run(["claude", "--version"], capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None, "claude --version nie odpowiada"
    if result.returncode != 0:
        return None, "claude --version nie odpowiada"
    first_line = next((line.strip() for line in result.stdout.splitlines() if line.strip()), "")
    return first_line, None


def _run_claude_agents(cwd: str | None = None) -> tuple[list[Any] | None, str | None]:
    """Zwroc (lista agentow, problem) - dokladnie jedno z nich jest None."""
    try:
        result = subprocess.run(
            ["claude", "agents", "--json", "--all"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            cwd=cwd,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return None, f"claude agents --json --all nie dziala: {exc}"
    if result.returncode != 0:
        reason = next(
            (line.strip() for line in result.stderr.splitlines() if line.strip()),
            f"kod wyjscia {result.returncode}",
        )
        return None, f"claude agents --json --all nie dziala: {reason}"
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        return None, f"claude agents --json --all nie dziala: odpowiedz nie jest poprawnym JSON ({exc})"
    if not isinstance(data, list):
        return None, "claude agents --json --all nie dziala: odpowiedz nie jest lista JSON"
    return data, None


def _check_settings_file(toplevel: Path, filename: str, problems: list[str], env_true_in: dict[str, str]) -> None:
    """Zwaliduj jeden plik `.claude/<filename>`, dopisujac problemy do `problems`.

    `env_true_in` zbiera, w ktorym pliku dana flaga MONOLYNX_* miala wartosc
    "true" w polu `env` - uzywane pozniej do podpowiedzi "jest w pliku, ale
    proces jej nie widzi" (plik odrzucony przez Claude Code w trybie -p).
    """
    rel = f".claude/{filename}"
    file_path = toplevel / ".claude" / filename
    if not file_path.is_file():
        return
    try:
        text = file_path.read_text()
    except OSError as exc:
        problems.append(f"{rel}: nie mozna odczytac pliku ({exc})")
        return
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        problems.append(f"{rel}: niepoprawny JSON ({exc})")
        return
    if not isinstance(data, dict):
        problems.append(f"{rel}: oczekiwany obiekt JSON")
        return

    if "permissions" in data:
        perms = data["permissions"]
        if not isinstance(perms, dict):
            problems.append(rel + ': permissions musi byc obiektem {"allow": [...]}')
        else:
            for key in ("allow", "deny", "ask"):
                if key not in perms:
                    continue
                value = perms[key]
                if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                    problems.append(f"{rel}: permissions.{key} musi byc lista napisow")

    if "env" in data:
        env_data = data["env"]
        if not isinstance(env_data, dict):
            problems.append(f"{rel}: env musi byc obiektem")
        else:
            for flag_name, flag_value in env_data.items():
                if flag_name in env_true_in:
                    continue
                if flag_value == "true" or flag_value is True:
                    env_true_in[flag_name] = rel


def _run_glab_default_branch(cwd: str) -> tuple[str | None, str | None]:
    """Zwroc (nazwa domyslnego brancha, problem) - dokladnie jedno z nich jest None."""
    try:
        result = subprocess.run(
            ["glab", "repo", "view", "-F", "json", "--jq", ".default_branch"],
            capture_output=True,
            text=True,
            timeout=_SUBPROCESS_TIMEOUT,
            cwd=cwd,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        return None, f"glab repo view nie dziala: {exc}"
    if result.returncode != 0:
        reason = next(
            (line.strip() for line in result.stderr.splitlines() if line.strip()),
            f"kod wyjscia {result.returncode}",
        )
        return None, f"glab repo view nie dziala: {reason}"
    name = result.stdout.strip().strip('"')
    if not name:
        return None, "glab repo view: pusta odpowiedz"
    return name, None


def _resolve_base_branch(process_env: Mapping[str, str], toplevel: str | None) -> str:
    """Ustal branch bazowy: `MONOLYNX_BASE_BRANCH` -> `MONOLYNX_MR_TARGET` ->
    default brancha originu (`git symbolic-ref`, potem `glab repo view`) - ten
    sam lancuch co dla brancha integracyjnego MR (MON-192). Brak wyniku ->
    pusty string, NIGDY zgadywanie "main" - repo moze uzywac "master",
    "develop" albo czegokolwiek innego jako galezi glownej.
    """
    base_branch = process_env.get("MONOLYNX_BASE_BRANCH", "").strip()
    if base_branch:
        return base_branch
    mr_target = process_env.get("MONOLYNX_MR_TARGET", "").strip()
    if mr_target:
        return mr_target
    if toplevel is None:
        return ""
    try:
        symbolic_ref = _run_git(["-C", toplevel, "symbolic-ref", "refs/remotes/origin/HEAD"])
    except _GitError:
        symbolic_ref = ""
    prefix = "refs/remotes/origin/"
    if symbolic_ref.startswith(prefix) and symbolic_ref[len(prefix) :]:
        return symbolic_ref[len(prefix) :]
    default_branch, _ = _run_glab_default_branch(toplevel)
    return default_branch or ""


def _check_base_branch_stale(toplevel: str | None, base_branch: str) -> str:
    """"true"/"false"/"" (nieznane) - rozjazd GLOWNEGO CHECKOUTU dyspozytora
    (katalog roboczy procesu ticku, NIE worktree ticketu, ktory dopiero
    powstanie) wobec brancha bazowego: inny branch niz `base_branch`, albo ten
    sam branch, ale za `origin/<base_branch>`. `claude --bg --worktree` bierze
    HEAD tego checkoutu w chwili startu sesji, wiec rozjazd tutaj dziedziczy
    kazda nowo wystartowana sesja.

    Brak mozliwosci sprawdzenia (brak `toplevel`, nieustalony `base_branch`,
    `git` nieodpowiadajacy, brak sieci przy `fetch`) -> "" (nieznane), NIGDY
    "true" z zalozenia - falszywy alarm zatrzymalby caly dispatch w trybie
    batch bez realnego powodu.
    """
    if toplevel is None or not base_branch:
        return ""
    try:
        current_branch = _run_git(["-C", toplevel, "rev-parse", "--abbrev-ref", "HEAD"])
    except _GitError:
        return ""
    if current_branch != base_branch:
        return "true"
    try:
        _run_git(["-C", toplevel, "fetch", "origin", base_branch])
        local_sha = _run_git(["-C", toplevel, "rev-parse", "HEAD"])
        remote_sha = _run_git(["-C", toplevel, "rev-parse", f"origin/{base_branch}"])
    except _GitError:
        return ""
    return "false" if local_sha == remote_sha else "true"


def _cmd_preflight(argv: list[str]) -> int:
    parser = _ArgumentParser(prog="sprint_run_tick.py preflight")
    parser.add_argument("--project-dir", default=".")
    parser.add_argument("--runtime", default="claude", choices=list(sprint_run_backend._SUPPORTED_BACKENDS))
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"sprint_run_tick: {exc}", file=sys.stderr)
        return 64

    problems: list[str] = []
    warnings: list[str] = []

    # MON-185: MONOLYNX_SPRINT_PERMISSION_MODE i .claude/settings*.json sa
    # pojeciami specyficznymi dla Claude Code (MON-183) - ta galaz ich w ogole
    # nie sprawdza dla --runtime codex, zeby unikniecie tych plikow bylo
    # kompletne, nie tylko "nie blokujace".
    if args.runtime == "claude":
        # MON-195: pierwszy check, przed reszta preflightu. `bypassPermissions`
        # (i rownowazny `--dangerously-skip-permissions` podany wprost jako
        # tryb) w sesji auto trafia na domyslna regule soft_deny "Create Unsafe
        # Agents" - tick przechodzil cala reszte preflightu i odpadal dopiero w
        # KROKU 4 bez startu zadnej sesji (19.09, MON-191/MON-192). Substring,
        # nie rownosc, bo dopuszczalne jest tez np. "--dangerously-skip-permissions=true".
        permission_mode_raw = os.environ.get("MONOLYNX_SPRINT_PERMISSION_MODE", _DEFAULT_PERMISSION_MODE)
        # Nieznana wartosc (literowka, przyszly tryb) przechodzi bez
        # ostrzezenia - falszywe zatrzymanie wstrzymuje caly sprint, falszywe
        # przepuszczenie konczy sie jedna odmowa klasyfikatora w KROK 4
        # (external-tool-output.md pkt 3): blokujemy wylacznie te dwa znane,
        # niebezpieczne literaly.
        if permission_mode_raw == "bypassPermissions" or "dangerously-skip-permissions" in permission_mode_raw:
            problems.append(
                "MONOLYNX_SPRINT_PERMISSION_MODE="
                + permission_mode_raw
                + " blokuje sesje w tle w trybie auto (regula soft_deny \"Create Unsafe Agents\"); "
                "ustaw MONOLYNX_SPRINT_PERMISSION_MODE=auto w env sledzonego .claude/settings.json "
                "albo swiadoma regule autoMode.allow w ~/.claude/settings.json"
            )

    # Dalsze kroki zalezne od sciezki repo (pliki .claude/settings*.json, katalog
    # ticku) sa pomijane nizej, gdy ten katalog nie jest repozytorium git.
    toplevel: str | None = None
    git_dir: str | None = None
    try:
        toplevel = _run_git(["-C", args.project_dir, "rev-parse", "--show-toplevel"])
        git_dir = _run_git(["-C", args.project_dir, "rev-parse", "--absolute-git-dir"])
    except _GitError:
        problems.append(f"katalog {args.project_dir} nie jest repozytorium git")

    claude_version: str | None = None
    env_true_in: dict[str, str] = {}
    if args.runtime == "claude":
        claude_version, version_problem = _run_claude_version()
        if version_problem:
            problems.append(version_problem)

        # Wynik samej listy agentow nie jest tu potrzebny - liczy sie tylko, ze
        # komenda dziala; podkomenda `state` uzywa go pozniej osobnym wywolaniem.
        _, agents_problem = _run_claude_agents()
        if agents_problem:
            problems.append(agents_problem)

        if toplevel is not None:
            toplevel_path = Path(toplevel)
            for filename in _SETTINGS_FILENAMES:
                _check_settings_file(toplevel_path, filename, problems, env_true_in)
    else:
        # codex: brak claude/agents/settings - delegacja do adaptera wspolnego
        # (codex --version + codex login status), bez skutkow ubocznych.
        _, codex_problems = sprint_run_backend.preflight("codex", env=os.environ)
        problems.extend(codex_problems)

    # os.environ, nie zawartosc pliku settings - to jest to, co faktycznie
    # widzi TEN proces (plik odrzucony przez Claude Code w trybie -p dalej
    # bylby "widziany" przy czytaniu z dysku, ale nie w srodowisku procesu).
    process_env = os.environ
    slug = process_env.get("MONOLYNX_PROJECT_SLUG", "")
    if not slug:
        problems.append("brak MONOLYNX_PROJECT_SLUG w srodowisku procesu")

    flag_values: dict[str, str | None] = {}
    for flag in _REQUIRED_AUTOFLAGS:
        value = process_env.get(flag)
        flag_values[flag] = value
        if value != "true":
            shown = value if value is not None else "(brak)"
            message = f"{flag}={shown} w srodowisku procesu, wymagane true"
            hint_file = env_true_in.get(flag)
            if hint_file:
                message += f"; jest w {hint_file}, ale proces jej nie widzi - plik mogl zostac odrzucony przez Claude Code"
            problems.append(message)

    automr_value = process_env.get("MONOLYNX_AUTOMR")
    if automr_value != "true":
        warnings.append("MONOLYNX_AUTOMR nie jest true - merge request trzeba bedzie zalozyc recznie")

    tick_dir: str | None = None
    if git_dir is not None:
        tick_dir_path = Path(git_dir) / "monolynx" / "tick"
        try:
            tick_dir_path.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            problems.append(f"nie mozna utworzyc katalogu ticku {tick_dir_path}: {exc}")
        else:
            tick_dir = str(tick_dir_path)

    base_branch = _resolve_base_branch(process_env, toplevel)
    base_branch_stale = _check_base_branch_stale(toplevel, base_branch)

    values = {
        "SLUG": slug,
        "MODE": "batch" if process_env.get("MONOLYNX_SPRINT_RUN_MODE") == "batch" else "interactive",
        "PARALLEL": process_env.get("MONOLYNX_SPRINT_PARALLEL", "2"),
        "SPRINT_MODEL": process_env.get("MONOLYNX_SPRINT_MODEL", "opus"),
        "PERMISSION_MODE": process_env.get("MONOLYNX_SPRINT_PERMISSION_MODE", _DEFAULT_PERMISSION_MODE),
        "AUTOTEST": flag_values["MONOLYNX_AUTOTEST"] or "false",
        "AUTOCOMMIT": flag_values["MONOLYNX_AUTOCOMMIT"] or "false",
        "AUTOPUSH": flag_values["MONOLYNX_AUTOPUSH"] or "false",
        "AUTOMR": automr_value or "false",
        "CONTRACT": process_env.get("MONOLYNX_CONTRACT", "ask"),
        "SPRINT_RUN": process_env.get("MONOLYNX_SPRINT_RUN", "0"),
        "CLAUDE_VERSION": claude_version or "",
        "PROJECT_DIR": toplevel or "",
        "TICK_DIR": tick_dir or "",
        "BASE_BRANCH": base_branch,
        "BASE_BRANCH_STALE": base_branch_stale,
        "RUNTIME": args.runtime,
    }
    for key in _KEY_ORDER:
        print(f"{key}={values[key]}")
    for warning in warnings:
        print(f"WARN: {warning}")
    for problem in problems:
        print(f"ERROR: {problem}")

    if problems:
        print(f"PREFLIGHT: failed problems={len(problems)}")
        return 2
    print("PREFLIGHT: ok")
    return 0


def _clean_stale_logs(logs_dir: Path) -> None:
    """Usun pojedynczo pliki `*.log` z poprzedniego ticku (bez rekurencji)."""
    if not logs_dir.is_dir():
        return
    for old_log in logs_dir.glob("*.log"):
        if old_log.is_file():
            old_log.unlink()


def _write_session_log(logs_dir: Path, session_id: str) -> None:
    """Uruchom `claude logs <session_id>`, zdejmij ANSI/\\r i zapisz ostatnie 60 niepustych linii."""
    try:
        result = subprocess.run(["claude", "logs", session_id], capture_output=True, text=True, timeout=_SUBPROCESS_TIMEOUT)
        raw = result.stdout + result.stderr
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
        # Awaria samego wywolania (timeout, brak binarki) nie przerywa ticku -
        # zapisujemy powod jako "log" tej sesji, klasyfikator (build_report)
        # i tak nie znajdzie w nim markera stopu ani pytania, wiec potraktuje
        # to jako "running" (zgodnie z kontraktem: brak logu nie jest dowodem
        # oczekiwania na czlowieka).
        raw = str(exc)

    # Skoki kolumny na spacje przed ogolnym strip ANSI, a ogon tak dlugi jak okno
    # build_report - inaczej plik zapisany tutaj skleja "SPRINT-RUN STOP:" i ucina
    # blok odpowiedzi asystenta ponizej kotwicy (MON-191).
    text = sprint_run_state._CSI_CURSOR_GAP_RE.sub(" ", raw)
    text = sprint_run_state._ANSI_RE.sub("", text)
    text = text.replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    nonempty = [line for line in lines if line][-sprint_run_state._LOG_TAIL_LINES :]
    log_path = logs_dir / f"{session_id}.log"
    log_path.write_text("\n".join(nonempty) + ("\n" if nonempty else ""))


def _state_codex(slug: str, tickets: list[Any], tick_dir: Path) -> dict[str, Any]:
    """Raport stanu z rejestru wlasnego dyspozytora (sprint_run_backend) - `codex
    exec` nie ma odpowiednika `claude agents --json --all`, wiec ta sciezka
    NIGDY nie wywoluje binarki `claude` (MON-185).

    Rejestr jest najpierw odswiezony przez status() dla kazdego wpisu codex i
    zapisany z powrotem - analogicznie do tego, jak podkomenda `state` dla
    claude zawsze pobiera swiezy `claude agents --json --all` przed
    klasyfikacja, zeby kolejny start() (find_active_session) widzial aktualny
    stan, a nie ten sprzed poprzedniego ticku.
    """
    registry_path = tick_dir / "sessions.json"
    registry = sprint_run_backend.load_registry(registry_path)

    refreshed: dict[str, dict[str, Any]] = {}
    for identity, record in registry.items():
        refreshed[identity] = sprint_run_backend.status(record) if record.get("backend") == "codex" else record
    sprint_run_backend.save_registry(registry_path, refreshed)

    rows = []
    for ticket in tickets:
        key = ticket["key"]
        ticket_status = ticket["status"]
        identity = sprint_run_backend.session_identity(slug, key)
        record = refreshed.get(identity)
        log_tail = sprint_run_backend.logs(record, n=60) if record else []
        verdict, reason = sprint_run_backend.classify_normalized(ticket_status, record, log_tail)
        rows.append(
            {
                "key": key,
                "session_id": record.get("runtime_id") if record else None,
                "session_name": record.get("name") if record else None,
                "session_state": record.get("state") if record else "none",
                "ticket_status": ticket_status,
                "verdict": verdict,
                "reason": reason,
                "backend": "codex",
            }
        )
    return {"slug": slug, "tickets": rows, "summary": sprint_run_state.summarize(rows)}


def _cmd_state(argv: list[str]) -> int:
    parser = _ArgumentParser(prog="sprint_run_tick.py state")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--tickets-json", required=True)
    parser.add_argument("--tick-dir", default=None)
    parser.add_argument("--backend", default="claude", choices=list(sprint_run_backend._SUPPORTED_BACKENDS))
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"sprint_run_tick: {exc}", file=sys.stderr)
        return 64

    # Walidacja wejscia PRZED jakimkolwiek efektem ubocznym (zapis, subprocess) -
    # patrz .claude/rules/input-boundary-elements.md.
    try:
        tickets = json.loads(args.tickets_json)
    except json.JSONDecodeError as exc:
        print(f"sprint_run_tick: tickets-json: niepoprawny JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(tickets, list):
        print("sprint_run_tick: tickets-json: oczekiwano listy JSON", file=sys.stderr)
        return 2
    ticket_error = sprint_run_state._validate_tickets(tickets)
    if ticket_error is not None:
        print(f"sprint_run_tick: {ticket_error}", file=sys.stderr)
        return 2

    try:
        tick_dir = _resolve_tick_dir(args.tick_dir)
    except _GitError as exc:
        print(f"sprint_run_tick: nie mozna ustalic katalogu ticku: {exc}", file=sys.stderr)
        return 2
    try:
        tick_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"sprint_run_tick: nie mozna utworzyc katalogu ticku {tick_dir}: {exc}", file=sys.stderr)
        return 2

    if args.backend == "codex":
        report = _state_codex(args.slug, tickets, tick_dir)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    agents, agents_problem = _run_claude_agents()
    if agents_problem:
        print(f"sprint_run_tick: {agents_problem}", file=sys.stderr)
        return 2
    if agents is None:
        # Nie powinno wystapic - _run_claude_agents() zwraca dokladnie jedno
        # z (agents, problem) jako None - ale zawezamy typ jawnie zamiast
        # polegac na tym kontrakcie po drugiej stronie granicy funkcji.
        print("sprint_run_tick: claude agents --json --all nie dziala: brak odpowiedzi", file=sys.stderr)
        return 2

    (tick_dir / "tickets.json").write_text(json.dumps(tickets, ensure_ascii=False, indent=2))
    (tick_dir / "agents.json").write_text(json.dumps(agents, ensure_ascii=False, indent=2))

    logs_dir = tick_dir / "logs"
    _clean_stale_logs(logs_dir)
    logs_dir.mkdir(parents=True, exist_ok=True)

    for ticket in tickets:
        session = sprint_run_state.match_session(agents, args.slug, ticket["key"])
        if session is None:
            continue
        session_id = session.get("id")
        if not isinstance(session_id, str) or not session_id:
            # "id" moze byc nieobecne, None, albo (wpis zdeformowany przez
            # zewnetrzne narzedzie) liczba/inny typ - pomijamy taki wpis
            # jawnie, zamiast przekazac nie-string do subprocess.run()/nazwy
            # pliku, gdzie skonczyloby sie to TypeError zamiast kodu 0/2.
            continue
        _write_session_log(logs_dir, session_id)

    report = sprint_run_state.build_report(args.slug, tickets, agents, logs_dir)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def _cmd_start(argv: list[str]) -> int:
    parser = _ArgumentParser(prog="sprint_run_tick.py start")
    parser.add_argument("--backend", required=True, choices=list(sprint_run_backend._SUPPORTED_BACKENDS))
    parser.add_argument("--slug", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--cwd", default=".")
    parser.add_argument("--tick-dir", default=None)
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"sprint_run_tick: {exc}", file=sys.stderr)
        return 64

    if args.backend == "claude":
        # MON-191/MON-192: sesje Claude startuja bezposrednio w KROK 4 skilla
        # (`claude --bg --worktree ...`), zeby przejsc przez system uprawnien
        # Claude Code - wywolanie `claude --bg` z wnetrza tego skryptu
        # (subprocess procesu tick) omijaloby te uprawnienia. Zero efektow.
        print(
            "sprint_run_tick: start --backend claude nie jest wspierany - sesje Claude startuje "
            "bezposrednio KROK 4 skilla (`claude --bg --worktree ...`), zeby przejsc przez system "
            "uprawnien Claude Code; wywolanie `claude --bg` z wnetrza tego skryptu (subprocess) "
            "omijaloby je (MON-191/MON-192).",
            file=sys.stderr,
        )
        return 64

    identity = sprint_run_backend.session_identity(args.slug, args.key)

    # Preflight PRZED jakimkolwiek dotknieciem dysku (tick-dir, rejestr) - awaria
    # nie zostawia sladu nawet gdy rejestr juz istnieje (MON-185 iteracja 2, pkt 6).
    ok, problems = sprint_run_backend.preflight("codex", env=os.environ)
    if not ok:
        for problem in problems:
            print(f"sprint_run_tick: {problem}", file=sys.stderr)
        return 2

    try:
        tick_dir = _resolve_tick_dir(args.tick_dir)
    except _GitError as exc:
        print(f"sprint_run_tick: nie mozna ustalic katalogu ticku: {exc}", file=sys.stderr)
        return 2
    try:
        tick_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"sprint_run_tick: nie mozna utworzyc katalogu ticku {tick_dir}: {exc}", file=sys.stderr)
        return 2

    registry_path = tick_dir / "sessions.json"

    # Best-effort - brak binarki `claude` (albo jej awaria) NIE jest bledem
    # tutaj (AC4): find_active_session traktuje None jak "zrodlo niedostepne"
    # i spada na sam rejestr wlasny dyspozytora.
    agents, agents_problem = _run_claude_agents()
    claude_agents = agents if agents_problem is None else None

    registry = sprint_run_backend.load_registry(registry_path)
    existing = sprint_run_backend.find_active_session(
        slug=args.slug, key=args.key, registry=registry, claude_agents=claude_agents
    )
    if existing is not None:
        print(f"ALREADY-RUNNING {identity} {existing.get('runtime_id', '')}")
        return 0

    record = sprint_run_backend.start(
        "codex",
        slug=args.slug,
        key=args.key,
        model=args.model,
        prompt=args.prompt,
        cwd=args.cwd,
        registry_path=registry_path,
        claude_agents=claude_agents,
    )
    print(f"STARTED {identity} {record['runtime_id']} {record['pid']}")
    return 0


def _cmd_stop(argv: list[str]) -> int:
    parser = _ArgumentParser(prog="sprint_run_tick.py stop")
    parser.add_argument("--backend", required=True, choices=list(sprint_run_backend._SUPPORTED_BACKENDS))
    parser.add_argument("--slug", required=True)
    parser.add_argument("--key", required=True)
    parser.add_argument("--tick-dir", default=None)
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"sprint_run_tick: {exc}", file=sys.stderr)
        return 64

    identity = sprint_run_backend.session_identity(args.slug, args.key)

    try:
        tick_dir = _resolve_tick_dir(args.tick_dir)
    except _GitError as exc:
        print(f"sprint_run_tick: nie mozna ustalic katalogu ticku: {exc}", file=sys.stderr)
        return 2

    registry_path = tick_dir / "sessions.json"
    registry = sprint_run_backend.load_registry(registry_path)
    # Dopasowanie WYLACZNIE po tozsamosci (slug:key) - na (slug,key) moze
    # istniec najwyzej jeden zywy wpis rejestru niezaleznie od backendu, wiec
    # --backend sluzy tu tylko walidacji CLI/symetrii, nie filtrowaniu.
    entry = registry.get(identity)
    if entry is None:
        print(f"NOT-FOUND {identity}")
        return 0

    sprint_run_backend.stop(entry, registry_path=registry_path)
    print(f"STOPPED {identity}")
    return 0


def main(argv: list[str]) -> int:
    if not argv:
        print("sprint_run_tick: brak podkomendy (preflight|state|start|stop)", file=sys.stderr)
        return 64
    subcommand, rest = argv[0], argv[1:]
    if subcommand == "preflight":
        return _cmd_preflight(rest)
    if subcommand == "state":
        return _cmd_state(rest)
    if subcommand == "start":
        return _cmd_start(rest)
    if subcommand == "stop":
        return _cmd_stop(rest)
    print(f"sprint_run_tick: nieznana podkomenda '{subcommand}' (preflight|state|start|stop)", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
