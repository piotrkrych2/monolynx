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

  preflight [--project-dir DIR]
      Sprawdza srodowisko procesu (nie shella wywolujacego) przed startem
      jakiejkolwiek sesji w tle: git, `claude --version`, `claude agents
      --json --all`, ksztalt `.claude/settings*.json` i flagi MONOLYNX_* w
      `os.environ`. Incydent 18.09: plik settings z `permissions` w zlym
      ksztalcie jest po cichu ignorowany przez Claude Code w trybie `-p`
      (razem z `env`), wiec preflight musi to wykryc PRZED tickiem, nie
      pozwolic sesji w tle utknac na pierwszym pytaniu bez odpowiedzi.

  state --slug S --tickets-json JSON [--tick-dir DIR]
      Zbiera stan sesji w tle (`claude agents --json --all`) i ogony ich
      logow (`claude logs <id>`), zapisuje je w TICK_DIR (`.git/monolynx/
      tick/`, NIGDY w /tmp) i deleguje klasyfikacje werdyktow do
      `sprint_run_state.build_report` (logika werdyktow poza zakresem tego
      ticketu - MON-191).

Bezpieczenstwo: `preflight` wypisuje na stdout WYLACZNIE klucze z ustalonej
listy (patrz `_KEY_ORDER` nizej) - nigdy calego `env`, ani wartosci zmiennych
spoza tej listy. Incydent 18.09: `printenv` w ticku wypisal do transkryptu i
logu petli klucz OpenAI i token GitLaba.

Kody wyjscia (obie podkomendy):
    0  - sukces.
    2  - blad wejscia/srodowiska (preflight: problemy znalezione; state: zly
         JSON, zly ksztalt ticketow, `claude agents` nie dziala).
    64 - blad uzycia CLI (brak/nieznana podkomenda, brak wymaganego argumentu).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

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
)

_REQUIRED_AUTOFLAGS = ("MONOLYNX_AUTOTEST", "MONOLYNX_AUTOCOMMIT", "MONOLYNX_AUTOPUSH")
_SETTINGS_FILENAMES = ("settings.json", "settings.local.json")


class _UsageError(Exception):
    """Blad parsowania argumentow CLI (mapowany na kod wyjscia 64)."""


class _GitError(Exception):
    """Katalog nie jest repozytorium git albo `git` nie odpowiada."""


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


def _cmd_preflight(argv: list[str]) -> int:
    parser = _ArgumentParser(prog="sprint_run_tick.py preflight")
    parser.add_argument("--project-dir", default=".")
    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"sprint_run_tick: {exc}", file=sys.stderr)
        return 64

    problems: list[str] = []
    warnings: list[str] = []

    # Dalsze kroki zalezne od sciezki repo (pliki .claude/settings*.json, katalog
    # ticku) sa pomijane nizej, gdy ten katalog nie jest repozytorium git.
    toplevel: str | None = None
    git_dir: str | None = None
    try:
        toplevel = _run_git(["-C", args.project_dir, "rev-parse", "--show-toplevel"])
        git_dir = _run_git(["-C", args.project_dir, "rev-parse", "--absolute-git-dir"])
    except _GitError:
        problems.append(f"katalog {args.project_dir} nie jest repozytorium git")

    claude_version, version_problem = _run_claude_version()
    if version_problem:
        problems.append(version_problem)

    # Wynik samej listy agentow nie jest tu potrzebny - liczy sie tylko, ze
    # komenda dziala; podkomenda `state` uzywa go pozniej osobnym wywolaniem.
    _, agents_problem = _run_claude_agents()
    if agents_problem:
        problems.append(agents_problem)

    env_true_in: dict[str, str] = {}
    if toplevel is not None:
        toplevel_path = Path(toplevel)
        for filename in _SETTINGS_FILENAMES:
            _check_settings_file(toplevel_path, filename, problems, env_true_in)

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

    values = {
        "SLUG": slug,
        "MODE": "batch" if process_env.get("MONOLYNX_SPRINT_RUN_MODE") == "batch" else "interactive",
        "PARALLEL": process_env.get("MONOLYNX_SPRINT_PARALLEL", "2"),
        "SPRINT_MODEL": process_env.get("MONOLYNX_SPRINT_MODEL", "opus"),
        "PERMISSION_MODE": process_env.get("MONOLYNX_SPRINT_PERMISSION_MODE", "bypassPermissions"),
        "AUTOTEST": flag_values["MONOLYNX_AUTOTEST"] or "false",
        "AUTOCOMMIT": flag_values["MONOLYNX_AUTOCOMMIT"] or "false",
        "AUTOPUSH": flag_values["MONOLYNX_AUTOPUSH"] or "false",
        "AUTOMR": automr_value or "false",
        "CONTRACT": process_env.get("MONOLYNX_CONTRACT", "ask"),
        "SPRINT_RUN": process_env.get("MONOLYNX_SPRINT_RUN", "0"),
        "CLAUDE_VERSION": claude_version or "",
        "PROJECT_DIR": toplevel or "",
        "TICK_DIR": tick_dir or "",
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


def _cmd_state(argv: list[str]) -> int:
    parser = _ArgumentParser(prog="sprint_run_tick.py state")
    parser.add_argument("--slug", required=True)
    parser.add_argument("--tickets-json", required=True)
    parser.add_argument("--tick-dir", default=None)
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

    if args.tick_dir:
        tick_dir = Path(args.tick_dir)
    else:
        try:
            git_dir = _run_git(["rev-parse", "--absolute-git-dir"])
        except _GitError as exc:
            print(f"sprint_run_tick: nie mozna ustalic katalogu ticku: {exc}", file=sys.stderr)
            return 2
        tick_dir = Path(git_dir) / "monolynx" / "tick"
    try:
        tick_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        print(f"sprint_run_tick: nie mozna utworzyc katalogu ticku {tick_dir}: {exc}", file=sys.stderr)
        return 2

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


def main(argv: list[str]) -> int:
    if not argv:
        print("sprint_run_tick: brak podkomendy (preflight|state)", file=sys.stderr)
        return 64
    subcommand, rest = argv[0], argv[1:]
    if subcommand == "preflight":
        return _cmd_preflight(rest)
    if subcommand == "state":
        return _cmd_state(rest)
    print(f"sprint_run_tick: nieznana podkomenda '{subcommand}' (preflight|state)", file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
