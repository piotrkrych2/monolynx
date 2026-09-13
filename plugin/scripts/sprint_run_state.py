#!/usr/bin/env python3
"""Klasyfikator stanu sprintu dla /monolynx:sprint-run (MON-167).

Laczy trzy zrodla:
  - liste ticketow sprintu (klucz, status, ewentualny assignee),
  - liste sesji agentow w tle/interaktywnych (z `claude agents --json --all`),
  - ogon logu kazdej sesji w tle (pliki `<session_id>.log` w katalogu logow).

... i dla kazdego ticketu wyznacza werdykt: "free" (wolny do podjecia),
"running" (sesja pracuje), "waiting" (sesja czeka na czlowieka),
"done" (ticket w recenzji/zakonczony) albo "needs_human" (rozjazd miedzy
stanem ticketu a stanem sesji, wymaga recznej interwencji).

Stan sesji (session_state) jest wyznaczany z ZAMKNIETEJ listy stanow martwych
(_DEAD_SESSION_STATES) - kazdy inny literal, w tym nieznany jeszcze `claude`
(np. przyszla wersja dodajaca "compacting"), jest traktowany jako zywy
("running"). To wariant odwracalny: falszywe "running" kosztuje najwyzej jeden
tick opoznienia, falszywe "exited" powoduje auto-wznowienie i duplikat sesji
(nieodwracalne) - patrz .claude/rules/external-tool-output.md pkt 3.
session_state_reason zwraca czytelne ostrzezenie, gdy literal nie byl ani na
liscie martwych, ani na liscie znanych zywych stanow, zeby taki przypadek byl
widoczny w raporcie mimo ze werdykt sie nie zmienia.

read_log_tail zdejmuje sekwencje ANSI (kolory, ruch kursora, czyszczenie
ekranu) i zamienia \r na \n, bo realny render TUI (`claude logs`) nadpisuje
linie przez \r zamiast dopisywac \n - patrz tests/fixtures/claude/
logs-tui.2.1.270.txt. Okno n linii to okno n NIEPUSTYCH linii po tym
przetworzeniu, inaczej puste przerysowania wypychaja sensowna tresc poza okno.

Wejscie jest granica systemu (plik JSON od zewnetrznego skryptu) - niepoprawny
JSON, brak pliku i niepoprawny ksztalt nie powinny nigdy rzucic tracebacku,
tylko zakonczyc program czytelnym bledem po polsku.

CLI:
    python3 sprint_run_state.py --slug S --agents-file A --tickets-file T [--logs-dir D]

    --agents-file - (myslnik) czyta liste agentow ze stdin zamiast z pliku.

Kody wyjscia:
    0  - raport wypisany poprawnie na stdout jako JSON.
    2  - blad wejscia: niepoprawny JSON, brak pliku, albo tickets-file/
         agents-file nie jest lista JSON.
    64 - blad uzycia CLI: brak wymaganego argumentu albo nieznany argument.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

# Stany MARTWE - lista zamknieta. "done" jest zaobserwowany w realnym zrzucie
# tests/fixtures/claude/agents-json-all.2.1.270.json (sesja fixture-probe po
# zakonczeniu); pozostale sa dodane z definicji jako terminalne (nie
# zaobserwowane w zadnym fixture, ale nazwy oczywiste jako "koniec zycia
# procesu") - MON-172, .claude/rules/external-tool-output.md pkt 2.
_DEAD_SESSION_STATES = {"exited", "completed", "failed", "stopped", "killed", "done", "error"}

# Stany ZYWE znane z obserwacji - "blocked" jest w fixture wprost (background
# sesja obcego projektu), "busy" tez (obie sesje interactive w fixture maja
# status="busy"). "idle" wystepuje w fixture TYLKO jako "status" na wpisie
# background fixture-probe, ktory ma jednoczesnie state="done" (state wygrywa
# w session_state - patrz _meta.note w fixture o polach state+status
# wspolistniejacych na jednym wpisie) - "idle" jest wiec na tej liscie z
# definicji kontraktu, nie z bezposredniej obserwacji zywego przypadku.
# "working" byl zaobserwowany realnie w trakcie tego samego przebiegu ale nie
# utrwalony w tym konkretnym zrzucie (patrz _meta.note w fixture). "running",
# "waiting", "starting" sa z definicji kontraktu (brief MON-167/172), nie z
# fixture. Literal spoza obu list (patrz session_state_reason) i tak liczy sie
# jako zywy w session_state - ta lista sluzy tylko do odroznienia "znany zywy"
# od "nieznany, ale traktowany jako zywy" w komunikacie ostrzegawczym.
_KNOWN_LIVE_SESSION_STATES = {"running", "working", "blocked", "waiting", "busy", "idle", "starting"}

# Sekwencje ANSI w realnym renderze TUI `claude logs` (tests/fixtures/claude/
# logs-tui.2.1.270.txt): CSI (kolory 24-bit, ruch kursora, czyszczenie ekranu),
# OSC (tytul okna), wybor zestawu znakow, i catch-all na pozostale sekwencje
# ESC+1-znak. Catch-all NIE gwarantuje zdjecia kazdego mozliwego bajtu \x1b -
# "." bez re.DOTALL nie dopasuje ESC bezposrednio przed \n ani samotnego ESC
# na koncu bufora. Nie zmieniamy na re.DOTALL/`\x1b(?:.|$)`, bo w zlapanych
# fixture'ach renderer zawsze konczy sekwencje sterujaca przed \r/\n (nie ma
# takiego rozerwania), a pojedynczy niedopasowany bajt \x1b - gdyby sie
# pojawil - nie wplywa na dopasowanie markerow tekstowych w classify()
# (SPRINT-RUN STOP:, koncowe "?"), wiec nie warto komplikowac regexu pod
# przypadek, ktorego nie zaobserwowano.
_ANSI_RE = re.compile(
    r"\x1b\[[0-9;?]*[A-Za-z]"
    r"|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|\x1b[()][A-Z0-9]"
    r"|\x1b."
)


class _UsageError(Exception):
    """Blad parsowania argumentow CLI (mapowany na kod wyjscia 64)."""


class _InputError(Exception):
    """Blad tresci wejscia: zly JSON, brak pliku, zly ksztalt (kod wyjscia 2)."""


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        # argparse domyslnie wola sys.exit(2) - kolidowaloby z naszym kodem 2
        # dla bledow tresci wejscia, wiec zamieniamy na wyjatek do przechwycenia w main().
        raise _UsageError(message)


def match_session(agents: list[Any], slug: str, key: str) -> dict[str, Any] | None:
    """Znajdz sesje w tle dopasowana po dokladnej nazwie "<slug>-<key>".

    Bierze pod uwage wylacznie wpisy kind == "background" z obecnym polem "id".
    Przy kilku trafieniach zwraca to z najwiekszym "startedAt".
    """
    target_name = f"{slug}-{key}"
    candidates = [
        agent
        for agent in agents
        if isinstance(agent, dict)
        and agent.get("kind") == "background"
        and "id" in agent
        and agent.get("name") == target_name
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda agent: agent.get("startedAt", 0))


def session_state(session: dict[str, Any]) -> str:
    """Zmapuj stan sesji na "running" albo "exited".

    Defensywnie czyta "state" (sesje w tle) albo "status" (sesje interaktywne).
    Brak pola albo pusty string -> "exited" (galaz JAWNA: przy zamknietej
    liscie martwych stanow naiwne "value not in _DEAD_SESSION_STATES" dalo by
    "running" dla braku wartosci, co jest zle). Wartosc na liscie martwych ->
    "exited". Kazda inna wartosc (znana zywa albo nieznana) -> "running" -
    MON-172, patrz komentarz WHY przy _DEAD_SESSION_STATES.
    """
    value = session.get("state") or session.get("status")
    if not value:
        return "exited"
    if value in _DEAD_SESSION_STATES:
        return "exited"
    return "running"


def session_state_reason(session: dict[str, Any]) -> str | None:
    """Ostrzezenie dla literalu stanu spoza znanej listy zywych/martwych.

    Nie zmienia werdyktu (session_state juz zmapowal taki literal na
    "running") - tylko sygnalizuje, ze to nierozpoznana wartosc, zeby
    dyspozytor/czlowiek mogl to zauwazyc zanim `claude` zacznie zwracac ja
    masowo. None gdy literal jest znany (zywy albo martwy) albo gdy pola brak.
    """
    value = session.get("state") or session.get("status")
    if not value:
        return None
    if value in _DEAD_SESSION_STATES or value in _KNOWN_LIVE_SESSION_STATES:
        return None
    return f"nieznany stan '{value}', traktuje jako zywy"


def read_log_tail(logs_dir: Path | str, session_id: str, n: int = 5) -> list[str]:
    """Zwroc ostatnie n NIEPUSTYCH linii pliku logu `<session_id>.log` w logs_dir.

    Brak katalogu albo pliku logu to normalny przypadek (sesja jeszcze nie
    napisala nic do logu) - zwraca wtedy pusta liste, bez wyjatku. Realny
    render TUI (`claude logs`, patrz tests/fixtures/claude/logs-tui.2.1.270.txt)
    zawiera sekwencje ANSI i uzywa \\r zamiast \\n do przerysowan - obie rzeczy
    trzeba zdjac/znormalizowac PRZED podzialem na linie, inaczej okno n linii
    zapelnia sie smieciem zamiast tresci.
    """
    log_path = Path(logs_dir) / f"{session_id}.log"
    if not log_path.is_file():
        return []
    text = log_path.read_text(errors="replace")
    text = _ANSI_RE.sub("", text)
    text = text.replace("\r", "\n")
    lines = [line.strip() for line in text.split("\n")]
    nonempty = [line for line in lines if line]
    return nonempty[-n:] if n > 0 else []


def waits_for_permission(session: dict[str, Any]) -> bool:
    """Czy wpis z `claude agents --json` mowi, ze sesja stoi na promptcie o zgode.

    Pola `status`/`waitingFor` wystepuja takze na wpisach background (fixture
    MON-172); "permission prompt" to jedyny zaobserwowany literal blokady.
    """
    waiting_for = str(session.get("waitingFor") or "").strip().lower()
    return waiting_for == "permission prompt"


def classify(ticket_status: str, session_state_value: str, log_tail: list[str]) -> tuple[str, str]:
    """Wyznacz (werdykt, uzasadnienie) na podstawie stanu ticketu i sesji.

    session_state_value to "none" (brak dopasowanej sesji), "running" albo "exited".
    """
    if session_state_value == "none":
        if ticket_status == "todo":
            return "free", "Brak sesji w tle, ticket ma status todo - mozna podjac prace."
        if ticket_status in ("in_review", "done"):
            return "done", "Brak sesji w tle, ticket jest juz w recenzji albo zakonczony."
        return "needs_human", (
            f"Brak sesji w tle, ale ticket ma status '{ticket_status}' - "
            "sprawdz recznie, czy praca nie utknela przed startem sesji."
        )

    if session_state_value == "exited":
        if ticket_status in ("in_review", "done"):
            return "done", "Sesja w tle zakonczyla dzialanie, ticket trafil do recenzji albo jest gotowy."
        return "needs_human", (
            f"Sesja w tle zakonczyla dzialanie, a ticket ma status '{ticket_status}' - "
            "sprawdz log sesji, praca mogla przerwac sie bez efektu."
        )

    # session_state_value == "running"
    window = log_tail[-5:]
    if any("SPRINT-RUN STOP:" in line for line in window):
        return "waiting", "Log zawiera marker SPRINT-RUN STOP - sesja czeka na decyzje czlowieka."
    last_nonempty = next((line for line in reversed(window) if line.strip()), None)
    if last_nonempty is not None and last_nonempty.rstrip().endswith("?"):
        return "waiting", "Ostatnia linia logu to pytanie - sesja czeka na odpowiedz czlowieka."
    return "running", "Sesja w tle dziala, log nie sygnalizuje oczekiwania na czlowieka."


def summarize(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Zlicz werdykty z build_report w podsumowanie liczbowe."""
    counts = {"remaining": 0, "running": 0, "waiting": 0, "done": 0, "needs_human": 0}
    for row in rows:
        verdict = row["verdict"]
        if verdict == "free":
            counts["remaining"] += 1
        elif verdict in counts:
            counts[verdict] += 1
    return counts


def build_report(slug: str, tickets: list[dict[str, Any]], agents: list[Any], logs_dir: Path | str) -> dict[str, Any]:
    """Zbuduj pelny raport stanu sprintu dla wszystkich ticketow, w kolejnosci wejscia."""
    rows: list[dict[str, Any]] = []
    for ticket in tickets:
        key = ticket["key"]
        ticket_status = ticket["status"]
        session = match_session(agents, slug, key)

        if session is None:
            session_id = None
            session_name = None
            sess_state = "none"
            log_tail: list[str] = []
            state_reason = None
        else:
            # "id" (krotkie, 8-znakowe) to identyfikator, ktorym operuja `claude
            # logs|attach|rm` i pod ktorym skill zapisuje ogon logu <id>.log -
            # "sessionId" (pelny UUID) sluzy tylko do wewnetrznego dopasowania
            # w match_session i nie jest tym samym identyfikatorem plikow.
            session_id = session.get("id")
            session_name = session.get("name")
            sess_state = session_state(session)
            state_reason = session_state_reason(session)
            log_tail = read_log_tail(logs_dir, session_id) if session_id else []

        verdict, reason = classify(ticket_status, sess_state, log_tail)
        if session is not None and sess_state == "running" and waits_for_permission(session):
            # Prompt o zgode (hook "ask" albo zwykle uprawnienie) w sesji bez
            # czlowieka: to jest "waiting", nie "running" - inaczej petla
            # kreci sie w nieskonczonosc (MON-173). Zaobserwowane realnie:
            # {"state": "blocked", "status": "waiting", "waitingFor": "permission prompt"}.
            verdict, reason = "waiting", "sesja czeka na potwierdzenie uprawnien (claude attach)"
        if state_reason:
            reason = f"{reason}; {state_reason}"
        rows.append(
            {
                "key": key,
                "session_id": session_id,
                "session_name": session_name,
                "session_state": sess_state,
                "ticket_status": ticket_status,
                "verdict": verdict,
                "reason": reason,
            }
        )

    return {"slug": slug, "tickets": rows, "summary": summarize(rows)}


def _validate_tickets(tickets: list[Any]) -> str | None:
    """Sprawdz ksztalt kazdego wpisu ticketow przed przekazaniem do build_report.

    Wejscie to granica systemu - element bez pol "key"/"status" albo nie-dict
    ma dac czytelny blad (exit 2), a nie traceback z KeyError. Zwraca komunikat
    po polsku, albo None gdy wszystkie wpisy sa poprawne.
    """
    for index, ticket in enumerate(tickets):
        if not isinstance(ticket, dict):
            return f"tickets-file[{index}]: oczekiwano obiektu JSON, otrzymano {type(ticket).__name__}"
        key = ticket.get("key")
        if not isinstance(key, str) or not key:
            return f"tickets-file[{index}]: brak wymaganego pola 'key' (niepusty string)"
        status = ticket.get("status")
        if not isinstance(status, str) or not status:
            return f"tickets-file[{index}]: brak wymaganego pola 'status' (niepusty string)"
    return None


def _load_json(path_str: str, label: str) -> Any:
    """Wczytaj i sparsuj JSON z pliku (albo stdin, gdy path_str == "-")."""
    if path_str == "-":
        text = sys.stdin.read()
        source = "stdin"
    else:
        path = Path(path_str)
        if not path.is_file():
            raise _InputError(f"{label}: nie znaleziono pliku '{path_str}'")
        text = path.read_text(errors="replace")
        source = path_str
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise _InputError(f"{label}: niepoprawny JSON w '{source}': {exc}") from exc


def main(argv: list[str]) -> int:
    parser = _ArgumentParser(
        prog="sprint_run_state.py",
        description="Klasyfikuj stan ticketow sprintu na podstawie sesji agentow i logow.",
    )
    parser.add_argument("--slug", required=True, help="slug projektu Monolynx")
    parser.add_argument("--agents-file", required=True, help="plik JSON z lista agentow, albo '-' dla stdin")
    parser.add_argument("--tickets-file", required=True, help="plik JSON z lista ticketow sprintu")
    parser.add_argument("--logs-dir", default=None, help="katalog z ogonami logow sesji (<session_id>.log)")

    try:
        args = parser.parse_args(argv)
    except _UsageError as exc:
        print(f"sprint_run_state: {exc}", file=sys.stderr)
        return 64

    try:
        agents = _load_json(args.agents_file, "agents-file")
        if not isinstance(agents, list):
            raise _InputError("agents-file: oczekiwano listy JSON")

        tickets = _load_json(args.tickets_file, "tickets-file")
        if not isinstance(tickets, list):
            raise _InputError("tickets-file: oczekiwano listy JSON")

        ticket_error = _validate_tickets(tickets)
        if ticket_error is not None:
            raise _InputError(ticket_error)
    except _InputError as exc:
        print(f"sprint_run_state: {exc}", file=sys.stderr)
        return 2

    logs_dir = Path(args.logs_dir) if args.logs_dir else Path(".")
    report = build_report(args.slug, tickets, agents, logs_dir)
    print(json.dumps(report, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
