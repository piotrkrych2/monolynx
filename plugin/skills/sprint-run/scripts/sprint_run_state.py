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

read_log_tail bierze linie z render_log: minimalnego emulatora ekranu terminala
(tests/fixtures/claude/logs-tui.2.1.270.txt, logs-spinner-redraw.2.1.284.txt),
bo `claude logs` to strumien roznicowych przerysowan TUI, nie tekst. Okno n linii
to okno n NIEPUSTYCH linii po emulacji, inaczej puste przerysowania wypychaja
sensowna tresc poza okno.

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

# Emulator ekranu zamiast regexu (MON-227). `claude logs` (Ink) nie wypisuje tekstu, tylko
# roznicowe przerysowania: rysuje wylacznie zmienione komorki i przeskakuje kursorem
# (CSI n G, n C, n B, r;c H, \x1b[H, K) nad niezmienionymi. Strip sekwencji gubil wiec
# litery ("Bash( laude", "zmian .", "N  uzywaj" w tests/fixtures/claude/
# logs-spinner-redraw.2.1.284.txt i logs-stop-tui.2.1.278.txt), a kazde przerysowanie od
# \x1b[H dopisywalo w strumieniu kolejna kopie bloku. Poprawny tekst istnieje tylko na
# ekranie po zastosowaniu ruchu kursora. Wczesniejsze regexy G/C -> spacja (MON-191)
# lataly to punktowo i nie obejmowaly reszty sekwencji. Zbior obslugiwanych sekwencji to
# to, co pojawia sie w fixture'ach (H, B, C, G, K, J, m) plus tanie A/D/f/ESC 7/8.
_CSI_RE = re.compile(r"\x1b\[([0-9;?]*)[ -/]*([@-~])")
_OTHER_ESC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Z0-9]|\x1b[78]|\x1b[^\[\]()78]")
_ESC_TOKEN_RE = re.compile(_CSI_RE.pattern + "|" + _OTHER_ESC_RE.pattern)

# Linie samego spinnera/statusu, nie tresci odpowiedzi (MON-227): glif spinnera na
# poczatku ("✢ Blanching…", "· Blanching… 1m 58s · ↓ 8.0k tokens)"), licznik czasu i
# tokenow takze w wierszu statusu subagenta ("45s · ↓ 109 tokens") oraz "thinking".
_SPINNER_LINE_RE = re.compile(r"^[✢✳✶✻✽·]|\d+s · ↓ \d|^thinking$|· thinking\)")


def _csi_params(raw_params: str) -> list[int]:
    """Parametry CSI jako liczby; puste i niecyfrowe ("?" w "\\x1b[?5H") daja 0 (= domyslna)."""
    return [int(p) if p.isdigit() else 0 for p in raw_params.replace("?", "").split(";")]


def _screen_rows(raw: str) -> list[str]:
    """Zastosuj sekwencje sterujace terminala i zwroc tekst wierszy ekranu ze scrollbackiem.

    Komorka bez zapisu to spacja, \\r wraca karetka bez zmiany wiersza, \\n = kolumna 1
    + nastepny wiersz. Wiersze nie sa obcinane do wysokosci ekranu (scrollback), zeby
    zwykly tekst (plik logu z poprzedniego ticku) przechodzil bez strat. Wysokosc
    ekranu jest znana tylko z najwiekszego wiersza w CSI r;c H (fixture: \\x1b[50;1H)
    i ogranicza wylacznie CSI B - `claude logs` nie scrolluje ruchem kursora w dol.
    """
    rows: list[list[str]] = [[]]
    row = col = 0
    saved = (0, 0)
    height = max((_csi_params(m.group(1))[0] for m in _CSI_RE.finditer(raw) if m.group(2) in "Hf"), default=0)

    def put(ch: str) -> None:
        nonlocal col
        line = rows[row]
        if len(line) < col:
            line.extend(" " * (col - len(line)))
        if col < len(line):
            line[col] = ch
        else:
            line.append(ch)
        col += 1

    def goto(new_row: int) -> None:
        nonlocal row
        row = max(0, new_row)
        while len(rows) <= row:
            rows.append([])

    pos = 0
    for token in _ESC_TOKEN_RE.finditer(raw):
        for ch in raw[pos : token.start()]:
            if ch == "\n":
                col = 0
                goto(row + 1)
            elif ch == "\r":
                col = 0
            elif ch == "\b":
                col = max(0, col - 1)
            elif ch >= " " and ch != "\x7f":
                put(ch)
        pos = token.end()
        final = token.group(2)
        if final is None:
            if token.group(0) == "\x1b7":
                saved = (row, col)
            elif token.group(0) == "\x1b8":
                goto(saved[0])
                col = saved[1]
            continue
        params = _csi_params(token.group(1))
        n = params[0] or 1
        if final in "Hf":
            goto(n - 1)
            col = max(0, (params[1] if len(params) > 1 and params[1] else 1) - 1)
        elif final == "A":
            goto(row - n)
        elif final == "B":
            goto(min(row + n, height - 1) if height else row + n)
        elif final == "C":
            col += n
        elif final == "D":
            col = max(0, col - n)
        elif final == "G":
            col = n - 1
        elif final == "K":
            mode = params[0]
            line = rows[row]
            if mode == 0:
                del line[col:]
            elif mode == 1:
                line[:col] = [" "] * min(col, len(line))
            else:
                line.clear()
        elif final == "J":
            if params[0] == 0:
                del rows[row][col:]
                del rows[row + 1 :]
            elif params[0] in (2, 3):
                rows[:] = [[]]
                row = 0
    for ch in raw[pos:]:
        if ch == "\n":
            col = 0
            goto(row + 1)
        elif ch == "\r":
            col = 0
        elif ch >= " " and ch != "\x7f":
            put(ch)
    return ["".join(line) for line in rows]


def _collapse_adjacent_repeats(lines: list[str]) -> list[str]:
    """Zwin bezposrednio powtorzony blok co najmniej 2 linii do jednego.

    WHY min. 2 linie: pojedyncza powtorzona linia ("}", "  ⎿  Allowed by auto mode
    classifier" pod dwoma wywolaniami narzedzi) bywa trescia, a nie artefaktem
    przerysowania - zwijamy tylko bloki, tak jak zdublowane odpowiedzi asystenta.
    """
    start = 0
    while start < len(lines):
        for size in range(2, (len(lines) - start) // 2 + 1):
            if lines[start] == lines[start + size] and lines[start : start + size] == lines[start + size : start + 2 * size]:
                # Restart od 0: zwiniecie moze utworzyc nowe powtorzenie zaczynajace sie
                # wczesniej (B C C C C A B C C A ... -> B C C A B C C A ...); tylko
                # przejscie do punktu stalego daje wynik idempotentny.
                del lines[start + size : start + 2 * size]
                start = 0
                break
        else:
            start += 1
    return lines


def render_log(raw: str) -> list[str]:
    """Surowe wyjscie `claude logs` -> czytelne, niepuste linie w kolejnosci wyswietlania.

    Emuluje ekran (_screen_rows), odrzuca linie spinnera/statusu (_SPINNER_LINE_RE)
    i zwija bezposrednio powtorzone bloki. Nie rusza linii odpowiedzi, wiec marker
    `SPRINT-RUN STOP:` i pytanie w ostatnim bloku zostaja widoczne dla classify
    (MON-191). Idempotentna na tekscie bez sekwencji sterujacych.
    """
    stripped = (row.strip() for row in _screen_rows(raw))
    lines = [line for line in stripped if line and not _SPINNER_LINE_RE.search(line)]
    return _collapse_adjacent_repeats(lines)


# Marker odporny na zgubiona spacje miedzy "SPRINT-RUN" i "STOP:" (MON-191) - render
# TUI czasem zjada spacje mimo emulacji kursora w render_log (inne warianty przesuniecia),
# wiec dopasowanie dopuszcza zero albo wiecej bialych znakow miedzy czlonami. Dwukropek
# po STOP zostaje wymagany, zeby proza tylko wspominajaca marker (np. tresc komentarza
# cytujaca go) nie dawala falszywego "waiting".
_STOP_MARKER_RE = re.compile(r"SPRINT-RUN\s*STOP:")

# Dlugosc ogona logu czytanego przez build_report (nie domyslne n=5 samego
# read_log_tail). Blok odpowiedzi asystenta miedzy kotwica "⏺" a koncem moze byc
# dluzszy niz 5 linii przez linie spinnera/statusu/promptu miedzy nimi - w fixture
# logs-stop-tui.2.1.278.txt marker jest 17. niepusta linia od konca. 60 to margines
# ponad zaobserwowana dlugosc, nie wyliczona dokladna wartosc.
_LOG_TAIL_LINES = 60


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
    """Znajdz sesje w tle ticketu: nazwa "<slug>-<key>-<model>" albo "<slug>-<key>".

    Sufiks modelu jest dowolny. WHY: tick po zmianie MONOLYNX_SPRINT_MODEL albo
    sesja nazwana przed dodaniem modelu do nazwy musza nadal byc widoczne - inaczej
    ticket wyglada na wolny i dostaje druga sesje (duplikat, nieodwracalny).
    Separator "-" po kluczu chroni przed dopasowaniem MON-1 do MON-10.

    Bierze pod uwage wylacznie wpisy kind == "background" z obecnym polem "id".
    Przy kilku trafieniach zwraca to z najwiekszym "startedAt".
    """
    base_name = f"{slug}-{key}"
    candidates = [
        agent
        for agent in agents
        if isinstance(agent, dict)
        and agent.get("kind") == "background"
        and "id" in agent
        and isinstance(agent.get("name"), str)
        and (agent["name"] == base_name or agent["name"].startswith(f"{base_name}-"))
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
    """Zwroc ostatnie n linii render_log pliku logu `<session_id>.log` w logs_dir.

    Brak katalogu albo pliku logu to normalny przypadek (sesja jeszcze nie
    napisala nic do logu) - zwraca wtedy pusta liste, bez wyjatku. Realny
    render TUI (`claude logs`, patrz tests/fixtures/claude/logs-tui.2.1.270.txt)
    to roznicowe przerysowania z ruchem kursora, wiec linie daje dopiero
    render_log (emulacja ekranu), a nie strip ANSI.
    """
    log_path = Path(logs_dir) / f"{session_id}.log"
    if not log_path.is_file():
        return []
    # Bajty, nie read_text(): tryb tekstowy tlumaczy \r na \n, a emulator potrzebuje
    # \r jako powrotu karetki (MON-227).
    text = log_path.read_bytes().decode("utf-8", errors="replace")
    lines = render_log(text)
    return lines[-n:] if n > 0 else []


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

    Dla "running" oknem klasyfikacji jest ostatni blok odpowiedzi asystenta: od
    OSTATNIEJ linii zaczynajacej sie od "⏺" (poczatek bloku w renderze TUI) do konca
    ogona - linie spinnera/statusu/promptu ("Symbioting…", "| ctx:...", "❯") miedzy
    kotwica a markerem/pytaniem wypychalyby je poza stale okno 5 linii (MON-191,
    tests/fixtures/claude/logs-stop-tui.2.1.278.txt). Brak kotwicy w ogonie -> stare
    zachowanie sprzed MON-191, okno to ostatnie 5 linii.
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
    anchor_indices = [i for i, line in enumerate(log_tail) if line.startswith("⏺")]
    if anchor_indices:
        window = log_tail[anchor_indices[-1] :]
    else:
        window = log_tail[-5:]

    if any(_STOP_MARKER_RE.search(line) for line in window):
        return "waiting", "Log zawiera marker SPRINT-RUN STOP - sesja czeka na decyzje czlowieka."

    if anchor_indices:
        # Kotwica: sprawdzamy KAZDA linie bloku, nie tylko ostatnia niepusta - po
        # pytaniu w renderze TUI ida linie spinnera/statusu/promptu, wiec ostatnia
        # niepusta linia calego ogona to prawie nigdy samo pytanie (MON-191).
        if any(line.rstrip().endswith("?") for line in window if line.strip()):
            return "waiting", "Blok odpowiedzi zawiera pytanie - sesja czeka na odpowiedz czlowieka."
    else:
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


DEFAULT_MAX_WAITING = 2


def parse_max_waiting(raw: str | None) -> int:
    """Limit zywych sesji waiting z MONOLYNX_SPRINT_MAX_WAITING; niepoprawna wartosc -> domyslny."""
    if raw is None:
        return DEFAULT_MAX_WAITING
    text = raw.strip()
    if text.isascii() and text.isdigit() and int(text) > 0:
        return int(text)
    return DEFAULT_MAX_WAITING


def dispatch_capacity(summary: dict[str, int], slots: int, max_waiting: int) -> dict[str, Any]:
    """Ile sesji wolno wystartowac w tym ticku.

    Sloty zajmuja tylko sesje running; waiting ma osobny limit. MON-138: sesja
    waiting trzymala slot cala noc i sprint stal.
    """
    free_slots = max(0, slots - summary["running"])
    waiting_blocked = summary["waiting"] >= max_waiting
    return {
        "slots": slots,
        "max_waiting": max_waiting,
        "free_slots": free_slots,
        "waiting_blocked": waiting_blocked,
        "can_start": 0 if waiting_blocked else free_slots,
    }


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
            log_tail = read_log_tail(logs_dir, session_id, n=_LOG_TAIL_LINES) if session_id else []

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
