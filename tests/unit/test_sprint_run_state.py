"""Testy klasyfikatora stanu sprintu: plugin/scripts/sprint_run_state.py.

Testy kontraktu modulu (MON-167):

    match_session(agents, slug, key) -> dict | None
    session_state(session) -> "running" | "exited"
    session_state_reason(session) -> str | None
    read_log_tail(logs_dir, session_id, n=5) -> list[str]
    classify(ticket_status, session_state, log_tail) -> tuple[str, str]
    build_report(slug, tickets, agents, logs_dir, state_dir=None, stall_ticks=DEFAULT_STALL_TICKS) -> dict
    summarize(rows) -> dict
    parse_max_waiting(raw) -> int                          (MON-222)
    parse_stall_ticks(raw) -> int                          (MON-252)
    dispatch_capacity(summary, slots, max_waiting) -> dict (MON-222)
    main(argv: list[str]) -> int

MON-252 (sesje, ktore tylko czekaja, a klasyfikator widzial je jako `running`): `classify`
zdejmuje `waiting` wejsciem uzytkownika (`<znak promptu U+276F> <tekst>`), rozpoznaje numerowane opcje z fraza
decyzji i fraze oczekiwania w ostatniej wypowiedzi, a `build_report` ze `state_dir` liczy
kolejne ticki z niezmienionym ogonem logu (plik stall.json) i po `stall_ticks` zamienia
`running` na `waiting`. Logi sesji tych przypadkow to fixture'y
tests/fixtures/claude/logs-*.2.1.288.txt (ogony po render_log odzyskane z transkryptu
dyspozytora, naglowek w pierwszej linii).

MON-172 (pierwszy realny przebieg dyspozytora) zmienia kontrakt session_state: zamiast
bialej listy stanow zywych (kazdy nieznany literal -> "exited", co mapowalo pracujaca
sesje "working" na martwa i powodowalo auto-wznowienie/duplikat), lista MARTWYCH stanow
jest teraz zamknieta (_DEAD_SESSION_STATES) i kazdy inny literal - w tym nieznany z
przyszlej wersji `claude` - jest traktowany jako zywy (wariant odwracalny, patrz
.claude/rules/external-tool-output.md pkt 3). Nowa funkcja session_state_reason zwraca
czytelne ostrzezenie dla literalu spoza znanej listy zywych/martwych, dopisywane do
"reason" wiersza w build_report. Zbiory literalow sa wyprowadzone z realnego zrzutu
`claude agents --json --all` w tests/fixtures/claude/agents-json-all.2.1.270.json.

Kontrakt CLI dla main() (zamrozony, patrz brief Team Managera dla MON-167):
    --slug S --agents-file A --tickets-file T [--logs-dir D] [--state-dir D]
    --agents-file - czyta stdin. Prog zastoju z MONOLYNX_SPRINT_STALL_TICKS (parse_stall_ticks).
Exit: 0 OK; 2 blad wejscia (niepoprawny JSON, brak pliku, tickets nie-lista);
64 blad uzycia (brak wymaganych argumentow) -- main() ZWRACA 64, nie rzuca
SystemExit (argparse domyslnie rzucalby, wiec implementacja MUSI to przechwycic).

Ksztalt agents-file (patrz realny zrzut w tests/fixtures/claude/agents-json-all.2.1.270.json,
klucz "_meta.note"): pola wspolne obu rodzajow to "cwd", "kind", "startedAt", "sessionId",
"name". Background ma ZAWSZE "id" i "state"; interactive ma ZAWSZE "pid" i "status". Pola
NIE sa rozlaczne miedzy rodzajami: fixture pokazuje wpis background lokalnie odpalonej
sesji (probe pracujacej w tym samym repo) z RAZEM "pid" i "id" oraz RAZEM "state" i
"status" naraz -- dlatego session_state() czyta `state or status` defensywnie, a nie
zaklada ktore pole jest obecne. "id" wystepuje tylko przy background (interactive go nie
ma). "waitingFor" NIE jest gwarantowane -- fixture interactive go nie zawiera, helper
interactive_agent() w tym pliku dodaje je tylko dla wygody parametryzacji testow.
"""

import importlib.util
import io
import json
import re
import sys
from pathlib import Path

import pytest

STATE_PATH = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "sprint_run_state.py"
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "claude"

_spec = importlib.util.spec_from_file_location("sprint_run_state", STATE_PATH)
assert _spec is not None and _spec.loader is not None
state = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(state)


def bg_agent(name, session_id="sid-1", started_at=1000, agent_state="running", agent_id="a1"):
    return {
        "id": agent_id,
        "cwd": "/repo",
        "kind": "background",
        "startedAt": started_at,
        "sessionId": session_id,
        "name": name,
        "state": agent_state,
    }


def interactive_agent(name, session_id="sid-int", started_at=1000, status="idle"):
    return {
        "pid": 55047,
        "cwd": "/repo",
        "kind": "interactive",
        "startedAt": started_at,
        "sessionId": session_id,
        "name": name,
        "status": status,
        "waitingFor": "input needed",
    }


# ---------------------------------------------------------------------------
# match_session
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMatchSession:
    def test_exact_name_match_returns_session(self):
        agents = [bg_agent("monolynx-MON-1")]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is not None
        assert result["sessionId"] == "sid-1"

    def test_no_prefix_match_mon1_does_not_match_mon10(self):
        """MON-1 nie moze dopasowac sesji nazwanej monolynx-MON-10 (prefiks, nie rownosc)."""
        agents = [bg_agent("monolynx-MON-10")]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is None

    def test_interactive_same_name_is_ignored(self):
        """Tylko kind == background jest brane pod uwage przy dopasowaniu."""
        agents = [interactive_agent("monolynx-MON-1")]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is None

    def test_duplicate_names_picks_newest_started_at(self):
        agents = [
            bg_agent("monolynx-MON-1", session_id="sid-old", started_at=100),
            bg_agent("monolynx-MON-1", session_id="sid-new", started_at=500),
        ]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is not None
        assert result["sessionId"] == "sid-new"

    def test_entry_without_id_is_skipped(self):
        entry_no_id = bg_agent("monolynx-MON-1")
        del entry_no_id["id"]
        result = state.match_session([entry_no_id], "monolynx", "MON-1")
        assert result is None

    def test_empty_agent_list_returns_none(self):
        assert state.match_session([], "monolynx", "MON-1") is None

    def test_name_with_model_suffix_matches(self):
        agents = [bg_agent("monolynx-MON-1-opus")]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is not None
        assert result["sessionId"] == "sid-1"

    def test_model_suffix_does_not_match_mon10_for_mon1(self):
        agents = [bg_agent("monolynx-MON-10-opus")]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is None

    def test_any_model_suffix_matches_and_newest_wins(self):
        """Zmiana MONOLYNX_SPRINT_MODEL miedzy tickami nie moze ukryc zywej sesji
        z innym modelem w nazwie - inaczej tick startuje duplikat."""
        agents = [
            bg_agent("monolynx-MON-1-fable", session_id="sid-old", started_at=100),
            bg_agent("monolynx-MON-1-claude-opus-5", session_id="sid-new", started_at=500),
        ]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is not None
        assert result["sessionId"] == "sid-new"

    def test_different_slug_does_not_match(self):
        agents = [bg_agent("inny-projekt-MON-1")]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is None


# ---------------------------------------------------------------------------
# session_state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionState:
    # Deadlista jest zamknieta (MON-172) - wszystko poza nia, w tym stany nieznane
    # jeszcze `claude` (np. "compacting", literal z przyszlej wersji), jest "running".
    # "working" i "blocked" pochodza z realnego zrzutu w
    # tests/fixtures/claude/agents-json-all.2.1.270.json.
    @pytest.mark.parametrize(
        "live_or_unknown_state",
        [
            "running",
            "working",
            "blocked",
            "waiting",
            "busy",
            "idle",
            "starting",
            "compacting",
            "jakis-nowy-stan-z-przyszlosci",
        ],
    )
    def test_live_and_unknown_states_are_running(self, live_or_unknown_state):
        session = bg_agent("x", agent_state=live_or_unknown_state)
        assert state.session_state(session) == "running"

    @pytest.mark.parametrize("dead_state", ["exited", "completed", "failed", "stopped", "killed", "done", "error"])
    def test_dead_states_are_exited(self, dead_state):
        session = bg_agent("x", agent_state=dead_state)
        assert state.session_state(session) == "exited"

    def test_missing_state_field_is_exited(self):
        session = bg_agent("x")
        del session["state"]
        assert state.session_state(session) == "exited"

    def test_empty_string_state_is_exited(self):
        session = bg_agent("x", agent_state="")
        assert state.session_state(session) == "exited"

    def test_interactive_session_uses_status_field_defensively(self):
        """Defensywnie: state or status -- sesja interaktywna z 'busy' liczy sie jako running."""
        session = interactive_agent("x", status="busy")
        assert state.session_state(session) == "running"

    def test_interactive_session_dead_status_is_exited(self):
        session = interactive_agent("x", status="exited")
        assert state.session_state(session) == "exited"


@pytest.mark.unit
class TestSessionStateReason:
    """session_state_reason: ostrzezenie tylko dla literalu spoza znanej listy zywych/martwych."""

    @pytest.mark.parametrize("unknown_state", ["compacting", "jakis-nowy-stan-z-przyszlosci"])
    def test_unknown_state_reason_names_the_literal(self, unknown_state):
        session = bg_agent("x", agent_state=unknown_state)
        reason = state.session_state_reason(session)
        assert reason is not None
        assert "nieznany stan" in reason
        assert unknown_state in reason

    @pytest.mark.parametrize("known_live_state", ["running", "working", "blocked", "waiting", "busy", "idle", "starting"])
    def test_known_live_state_has_no_reason(self, known_live_state):
        session = bg_agent("x", agent_state=known_live_state)
        assert state.session_state_reason(session) is None

    @pytest.mark.parametrize("dead_state", ["exited", "completed", "failed", "stopped", "killed", "done", "error"])
    def test_dead_state_has_no_reason(self, dead_state):
        session = bg_agent("x", agent_state=dead_state)
        assert state.session_state_reason(session) is None

    def test_missing_state_field_has_no_reason(self):
        session = bg_agent("x")
        del session["state"]
        assert state.session_state_reason(session) is None


@pytest.mark.unit
class TestAgentsFixture:
    """Utrwala ksztalt realnego wyjscia `claude agents --json --all` (MON-172).

    Patrz .claude/rules/external-tool-output.md: fixture z przechwycenia realnego
    przebiegu, nie z wyobrazenia autora. Fixture w
    tests/fixtures/claude/agents-json-all.2.1.270.json zawiera sesje background
    "done" (probe fixture-probe) i "blocked" (sesja obcego projektu) oraz dwie
    sesje interactive "busy" -- wszystkie literaly sa juz na znanej liscie
    (zywej albo martwej), zaden nie powinien wywolywac sciezki "nieznany stan".
    """

    @staticmethod
    def _load():
        path = FIXTURES_DIR / "agents-json-all.2.1.270.json"
        return json.loads(path.read_text())

    def test_meta_has_command_version_and_capture_date(self):
        meta = self._load()["_meta"]
        assert meta["command"] == "claude agents --json --all"
        assert meta["tool_version"] == "2.1.270"
        assert meta["captured"]

    def test_every_background_entry_has_8_hex_id_and_state(self):
        fixture = self._load()
        background_entries = [a for a in fixture["agents"] if a["kind"] == "background"]
        assert background_entries
        for agent in background_entries:
            assert re.fullmatch(r"[0-9a-f]{8}", agent["id"]), agent
            assert "state" in agent

    def test_every_interactive_entry_has_status_and_pid(self):
        fixture = self._load()
        interactive_entries = [a for a in fixture["agents"] if a["kind"] == "interactive"]
        assert interactive_entries
        for agent in interactive_entries:
            assert "status" in agent
            assert "pid" in agent

    def test_match_session_finds_probe_by_name(self):
        fixture = self._load()
        result = state.match_session(fixture["agents"], "fixture", "probe")
        assert result is not None
        assert result["name"] == "fixture-probe"

    def test_no_entry_in_fixture_has_unknown_state_reason(self):
        fixture = self._load()
        for agent in fixture["agents"]:
            assert state.session_state_reason(agent) is None, agent

    def test_every_entry_classifies_as_running_or_exited(self):
        fixture = self._load()
        for agent in fixture["agents"]:
            assert state.session_state(agent) in ("running", "exited")


# ---------------------------------------------------------------------------
# read_log_tail
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReadLogTail:
    def test_missing_logs_dir_returns_empty_list(self, tmp_path):
        missing_dir = tmp_path / "brak"
        assert state.read_log_tail(missing_dir, "sid-1") == []

    def test_missing_log_file_returns_empty_list(self, tmp_path):
        assert state.read_log_tail(tmp_path, "sid-nieznany") == []

    def test_returns_last_n_lines_of_file_with_more_lines(self, tmp_path):
        log_file = tmp_path / "sid-1.log"
        lines = [f"linia {i}" for i in range(1, 9)]
        log_file.write_text("\n".join(lines) + "\n")
        tail = state.read_log_tail(tmp_path, "sid-1", n=5)
        assert tail == lines[-5:]

    def test_file_with_fewer_lines_than_n_returns_all(self, tmp_path):
        log_file = tmp_path / "sid-2.log"
        log_file.write_text("tylko jedna linia\n")
        tail = state.read_log_tail(tmp_path, "sid-2", n=5)
        assert tail == ["tylko jedna linia"]

    def test_empty_lines_are_dropped_from_tail(self, tmp_path):
        """MON-172: ogon z realnego renderu TUI (`claude logs`) ma przerysowania i puste
        linie po zdjeciu ANSI/\\r (patrz tests/fixtures/claude/logs-tui.2.1.270.txt) --
        okno n linii ma byc oknem n NIEPUSTYCH linii, inaczej marker STOP albo pytanie
        moze wypasc poza okno mimo ze logicznie jest ostatnia sensowna linia."""
        log_file = tmp_path / "sid-3.log"
        log_file.write_text("a\n\nb\n\n\n")
        tail = state.read_log_tail(tmp_path, "sid-3", n=5)
        assert tail == ["a", "b"]

    def test_returns_last_n_nonempty_lines_skipping_blanks(self, tmp_path):
        log_file = tmp_path / "sid-4.log"
        log_file.write_text("l1\n\nl2\nl3\n\nl4\nl5\nl6\n")
        tail = state.read_log_tail(tmp_path, "sid-4", n=3)
        assert tail == ["l4", "l5", "l6"]

    def test_ansi_codes_are_stripped_and_stop_marker_is_found(self, tmp_path):
        """Sekwencje CSI (kolor, czyszczenie ekranu) syntetyczne, na wzor realnego
        renderu -- po zdjeciu ANSI marker SPRINT-RUN STOP jest widoczny i klasyfikacja
        rozpoznaje oczekiwanie na czlowieka."""
        log_file = tmp_path / "sid-ansi.log"
        log_file.write_bytes(b"\x1b[2J\x1b[HSPRINT-RUN \x1b[32mSTOP:\x1b[0m brak toolchain\n")
        tail = state.read_log_tail(tmp_path, "sid-ansi", n=5)
        assert any("SPRINT-RUN STOP:" in line for line in tail)
        assert all("\x1b" not in line for line in tail)
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "waiting"

    def test_carriage_return_with_cursor_down_starts_new_line(self, tmp_path):
        """Render TUI przechodzi do nastepnej linii przez \\r + \\x1b[1B (powrot karetki i
        kursor w dol), nie przez samo \\n -- patrz struktura realnego pliku
        tests/fixtures/claude/logs-tui.2.1.270.txt. Goly \\r to powrot karetki (nadpisanie)."""
        log_file = tmp_path / "sid-cr.log"
        log_file.write_bytes(b"linia1\r\x1b[1Blinia2\r\x1b[1Blinia3")
        tail = state.read_log_tail(tmp_path, "sid-cr", n=5)
        assert tail == ["linia1", "linia2", "linia3"]

    def test_real_logs_tui_fixture_tail_has_no_ansi_bytes(self, tmp_path):
        """Fixture realna z `claude logs` (MON-172): po przetworzeniu zaden element
        ogona nie zawiera bajtu ESC ani nie jest pusty, mimo ANSI i przerysowan
        w oryginale (patrz logs_fixtures._meta w agents-json-all.2.1.270.json)."""
        fixture_path = FIXTURES_DIR / "logs-tui.2.1.270.txt"
        copy_path = tmp_path / "sid-fixture.log"
        copy_path.write_bytes(fixture_path.read_bytes())
        tail = state.read_log_tail(tmp_path, "sid-fixture", n=5)
        assert tail
        assert all("\x1b" not in line for line in tail)
        assert all(line for line in tail)

    def test_unknown_id_fixture_classifies_as_running_not_waiting(self, tmp_path):
        """Fixture realna `claude logs <nieistniejacy-id>` (MON-172): tresc bledu nie
        zawiera markera STOP ani pytania, wiec sesja "running" zostaje "running"."""
        fixture_path = FIXTURES_DIR / "logs-unknown-id.2.1.270.txt"
        copy_path = tmp_path / "sid-unknown.log"
        copy_path.write_bytes(fixture_path.read_bytes())
        tail = state.read_log_tail(tmp_path, "sid-unknown", n=5)
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "running"

    def test_real_stop_fixture_restores_space_between_sprint_run_and_stop(self, tmp_path):
        """MON-191: `claude logs` przesuwa kursor miedzy 'SPRINT-RUN' i 'STOP:' sekwencja
        \\x1b[NG (skok kolumny), NIE spacja -- emulacja ekranu zostawia luke N komorek jako
        N spacji, wiec marker zachowuje spacje miedzy 'SPRINT-RUN' i 'STOP:' i pasuje do
        _STOP_MARKER_RE. Patrz tests/fixtures/claude/logs-stop-tui.2.1.278.txt
        (naglowek pliku: komenda `claude logs 20a4496b`, `claude 2.1.278`, 2026-09-19).

        Asercja celuje w KONKRETNA linie odpowiedzi asystenta (zaczyna sie od
        'SPRINT-RUN STOP:'), nie w dowolne wystapienie substringu -- fixture zawiera
        tez echo promptu sondy ('Druga linia: SPRINT-RUN STOP: fixture probe...')
        ktore ma marker ze zwykla spacja JUZ w surowym tekscie (bez udzialu
        \\x1b[NG), wiec samo `"SPRINT-RUN STOP:" in line` przechodziloby nawet gdyby
        emulacja kursora byla wylaczona (nie zabijaloby mutanta)."""
        fixture_path = FIXTURES_DIR / "logs-stop-tui.2.1.278.txt"
        copy_path = tmp_path / "sid-stop.log"
        copy_path.write_bytes(fixture_path.read_bytes())
        tail = state.read_log_tail(tmp_path, "sid-stop", n=60)
        assert any(line.startswith("SPRINT-RUN STOP: fixture probe MON-191 - czekam na decyzję człowieka") for line in tail)
        assert all("\x1b" not in line for line in tail)
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "waiting"

    def test_cursor_forward_g_and_c_sequences_become_space(self, tmp_path):
        """Test syntetyczny celowany wprost w emulacje kursora (MON-191, niezalezny od
        fixture realnego, ktory uzywa wylacznie wariantu G): \\x1b[<n>G (ustawienie
        kolumny kursora) i \\x1b[<n>C (przesuniecie kursora w prawo) zostawiaja luke
        N komorek, czyli N spacji -- tu dokladnie jedna, wiec marker zachowuje
        pojedyncza spacje."""
        log_file_g = tmp_path / "sid-g.log"
        # MON-227: luka o szerokosci N komorek to N spacji (emulator ekranu); po 10 znakach
        # kursor stoi w kol. 11, wiec skok na kol. 12 zostawia dokladnie jedna komorke.
        log_file_g.write_bytes(b"SPRINT-RUN\x1b[12GSTOP: przyklad g\n")
        tail_g = state.read_log_tail(tmp_path, "sid-g", n=5)
        assert tail_g == ["SPRINT-RUN STOP: przyklad g"]

        log_file_c = tmp_path / "sid-c.log"
        log_file_c.write_bytes(b"SPRINT-RUN\x1b[1CSTOP: przyklad c\n")
        tail_c = state.read_log_tail(tmp_path, "sid-c", n=5)
        assert tail_c == ["SPRINT-RUN STOP: przyklad c"]


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# render_log (MON-227)
# ---------------------------------------------------------------------------

SPINNER_FIXTURE_NAME = "logs-spinner-redraw.2.1.284.txt"
STOP_FIXTURE_NAME = "logs-stop-tui.2.1.278.txt"
_SPINNER_GLYPH_LINE_RE = re.compile(r"^[✢✳✶✻✽·]")
_TIMER_TOKEN_STATUS_RE = re.compile(r"\d+s · ↓ \d")


def _fixture_raw(name):
    """Surowe wyjscie `claude logs` BEZ tlumaczenia \\r na \\n.

    Text-mode read_text()/subprocess text=True zamienia \\r na \\n (universal newlines),
    a render TUI uzywa \\r jako powrotu karetki - emulator musi dostac \\r nietkniete.
    """
    return (FIXTURES_DIR / name).read_bytes().decode("utf-8", errors="replace")


@pytest.mark.unit
class TestRenderLogSpinnerFixture:
    """MON-227: render_log na realnym zrzucie tests/fixtures/claude/logs-spinner-redraw.2.1.284.txt
    (`claude logs ecfb94a4`, claude 2.1.284, zywa sesja w tle). Ekran koncowy po emulacji
    kursora (CSI B/C/G/H/K + \\r) zawiera tekst bez zgubionych liter i bez zdublowanych
    blokow; naiwny strip ANSI (stary potok) daje "Bash( laude", "zmian .", "analiz  ticketu"
    i kazdy blok po 2-3 razy (przerysowanie od \\x1b[H dopisuje kopie)."""

    @pytest.fixture
    def lines(self):
        return state.render_log(_fixture_raw(SPINNER_FIXTURE_NAME))

    def test_lines_are_nonempty_stripped_and_free_of_control_characters(self, lines):
        assert lines
        assert all(line and line == line.strip() for line in lines)
        assert all("\x1b" not in line and "\r" not in line for line in lines)

    def test_no_line_is_a_spinner_frame_or_timer_status(self, lines):
        """Ekran koncowy fixture ma wiersz "· Blanching…  1m 58s · ↓ 8.0k tokens)" oraz
        wiersz statusu subagenta z licznikiem "45s · ↓ 109" -- oba maja zniknac."""
        assert not [line for line in lines if "Blanching" in line]
        assert not [line for line in lines if _SPINNER_GLYPH_LINE_RE.match(line)]
        assert not [line for line in lines if _TIMER_TOKEN_STATUS_RE.search(line)]
        assert not [line for line in lines if line == "thinking" or "· thinking)" in line]

    @pytest.mark.parametrize(
        "line_marker",
        [
            "Sesje z ticketu",
            "Researcher pracuje.",
            "Tryb sesji w tle",
            "Logi dostępne tylko",
            "\u276f /monolynx:work MON-227",
        ],
    )
    def test_assistant_block_line_appears_exactly_once(self, lines, line_marker):
        assert len([line for line in lines if line_marker in line]) == 1

    @pytest.mark.parametrize(
        ("intact", "corrupted"),
        [
            ("Logi dostępne tylko dla żywych sesji.", "dostępn  tylko"),
            ("Ticket MON-227, TRYB: zmiana. Bloker MON-222 done. Branch worktree-MON-227 pasuje.", "zmian ."),
            ("Explore(Researcher - analiza ticketu) Sonnet 5.5", "analiz  ticketu"),
            ("Bash(claude agents --json --all 2>&1 | head -c 3000)", "Bash( laude"),
        ],
    )
    def test_words_keep_letters_skipped_by_cursor_jumps(self, lines, intact, corrupted):
        """Ink przerysowuje tylko zmienione komorki i przeskakuje kursorem (G/C) nad
        niezmienionymi literami -- poprawny tekst istnieje wylacznie na ekranie po
        emulacji, nie w strumieniu po zdjeciu sekwencji."""
        joined = "\n".join(lines)
        assert intact in joined
        assert corrupted not in joined

    def test_line_redrawn_from_column_four_does_not_lose_its_first_letter(self, lines):
        """Druga kopia wiersza "Ticket MON-227..." jest w strumieniu rysowana od kolumny 4
        ("icket ..."); litera "T" z kolumny 3 zostaje z wczesniejszego rysowania."""
        assert not [line for line in lines if line.startswith("icket")]

    def test_lines_are_in_display_order(self, lines):
        joined = "\n".join(lines)
        positions = [
            joined.index("Tryb sesji w tle"),
            joined.index("Ticket MON-227, TRYB"),
            joined.index("Researcher pracuje."),
            joined.index("Sesje z ticketu"),
            joined.index("Logi dostępne tylko"),
        ]
        assert positions == sorted(positions)

    def test_render_of_rendered_output_is_unchanged(self, lines):
        assert state.render_log("\n".join(lines)) == lines


@pytest.mark.unit
class TestRenderLogStopFixture:
    """MON-227: ten sam render_log na starszym fixture MON-191 (claude 2.1.278)."""

    @pytest.fixture
    def lines(self):
        return state.render_log(_fixture_raw(STOP_FIXTURE_NAME))

    def test_prompt_echo_keeps_words_split_by_cursor_jumps(self, lines):
        """Echo promptu ma w strumieniu "fixture. N[54G uzywaj" (litera "i"/"e" z
        wczesniejszego rysowania) -- naiwny strip daje "N  uzywaj"."""
        joined = "\n".join(lines)
        assert "Nie uzywaj zadnych narzedzi" in joined
        assert "N  uzywaj" not in joined
        assert "Tr cking" not in joined

    def test_stop_marker_line_is_intact_and_single(self, lines):
        stop_lines = [line for line in lines if line.startswith("SPRINT-RUN STOP:")]
        assert stop_lines == ["SPRINT-RUN STOP: fixture probe MON-191 - czekam na decyzję człowieka w sprawie migracji bazy danych przed merge"]

    def test_assistant_answer_block_is_not_duplicated(self, lines):
        answers = [line for line in lines if line.startswith("⏺ Brak odpowiedzi użytkownika")]
        assert len(answers) == 1

    def test_render_of_rendered_output_is_unchanged(self, lines):
        assert state.render_log("\n".join(lines)) == lines

    def test_stop_marker_verdict_is_still_waiting(self, lines):
        verdict, _ = state.classify("in_progress", "running", lines[-60:])
        assert verdict == "waiting"


@pytest.mark.unit
class TestRenderLogTerminalSemantics:
    """MON-227: kontrakt emulatora na syntetycznych wejsciach (jedno zachowanie na test)."""

    def test_cursor_jump_over_unchanged_cells_keeps_them(self):
        """Rysowanie "Tr" i "ing" z skokiem nad "ack" nie moze zgubic srodka."""
        assert state.render_log("Tracking\rTr\x1b[6Ging") == ["Tracking"]

    def test_gap_of_one_cell_is_one_space(self):
        assert state.render_log("SPRINT-RUN\x1b[12GSTOP: x") == ["SPRINT-RUN STOP: x"]

    def test_cursor_forward_over_empty_cells_gives_spaces(self):
        assert state.render_log("SPRINT-RUN\x1b[1CSTOP: x") == ["SPRINT-RUN STOP: x"]

    def test_carriage_return_without_line_feed_overwrites_the_line(self):
        assert state.render_log("abcdef\rXY") == ["XYcdef"]

    def test_cursor_down_moves_to_next_row_and_blank_rows_are_dropped(self):
        assert state.render_log("a\r\x1b[1Bb\r\x1b[2Bc") == ["a", "b", "c"]

    def test_cursor_home_then_text_overwrites_from_top_left(self):
        assert state.render_log("old text\x1b[Hnew") == ["new text"]

    def test_absolute_cursor_position_overwrites_cells(self):
        assert state.render_log("abcdef\x1b[1;3HXY") == ["abXYef"]

    def test_erase_to_end_of_line_clears_the_tail(self):
        assert state.render_log("abcdef\r\x1b[3G\x1b[K") == ["ab"]

    def test_erase_then_rewrite_does_not_leave_stale_tail(self):
        assert state.render_log("dluga stara linia\r\x1b[K" + "krotka") == ["krotka"]

    def test_color_sequences_do_not_shift_columns(self):
        assert state.render_log("\x1b[38;2;1;2;3mred\x1b[0m ok") == ["red ok"]

    def test_line_feed_starts_next_line_and_trailing_blanks_are_stripped(self):
        assert state.render_log("a   \n\n   \nb\n") == ["a", "b"]

    def test_long_plain_text_is_not_truncated_to_a_screen(self):
        """Plik logu zapisany przez poprzedni tick (do 60 linii) to zwykly tekst -
        emulator nie moze gubic linii ponad wysokosc ekranu (idempotencja)."""
        plain = [f"linia {i}" for i in range(1, 121)]
        assert state.render_log("\n".join(plain)) == plain

    def test_single_adjacent_repeated_line_is_kept(self):
        """Zwijane sa tylko bloki co najmniej 2 linii; pojedyncza powtorzona linia
        (klamra zamykajaca) to tresc."""
        assert state.render_log("{\n}\n}\n") == ["{", "}", "}"]

    def test_cursor_down_is_clamped_to_screen_height_set_by_absolute_position(self):
        """Wysokosc ekranu (5) wynika z \\x1b[5;1H; \\x1b[99B nie wychodzi poza dolny wiersz,
        wiec tekst nadpisuje "old" z wiersza 5 zamiast trafic do wiersza 100."""
        assert state.render_log("\x1b[5;1Hold\x1b[H\x1b[99BNEW") == ["NEW"]

    @pytest.mark.parametrize("dropped", ["thinking", "(2s · thinking)"])
    def test_thinking_status_line_is_dropped(self, dropped):
        assert state.render_log(f"tresc odpowiedzi\n{dropped}\n") == ["tresc odpowiedzi"]

    def test_line_starting_with_thinking_but_longer_is_kept(self):
        assert state.render_log("thinking about it\n") == ["thinking about it"]

    def test_empty_input_gives_empty_list(self):
        assert state.render_log("") == []

    def test_adjacent_identical_multiline_block_is_collapsed(self):
        block = ["⏺ Odpowiedź asystenta", "  pierwsza linia szczegółów", "  druga linia szczegółów"]
        raw = "\n".join([*block, "", *block])
        assert state.render_log(raw) == [line.strip() for line in block]

    def test_identical_status_line_under_different_tool_calls_is_kept(self):
        raw = "\n".join(
            [
                "⏺ Bash(pierwsze)",
                "  ⎿  Allowed by auto mode classifier",
                "⏺ Bash(drugie)",
                "  ⎿  Allowed by auto mode classifier",
            ]
        )
        assert state.render_log(raw) == [
            "⏺ Bash(pierwsze)",
            "⎿  Allowed by auto mode classifier",
            "⏺ Bash(drugie)",
            "⎿  Allowed by auto mode classifier",
        ]

    def test_plain_text_is_idempotent(self):
        raw = "a\n\n  b  \n⏺ c\n  ⎿  d"
        once = state.render_log(raw)
        assert state.render_log("\n".join(once)) == once

    def test_collapse_reaches_fixed_point_when_collapse_exposes_earlier_repeat(self):
        """Zwiniecie w srodku moze utworzyc powtorzenie zaczynajace sie wczesniej -
        bez restartu od poczatku wynik zostawialby blok "B C C A" dwa razy."""
        assert state.render_log("\n".join(list("BCCCCABCCACCAB"))) == list("BCCAB")

    def test_csi_with_private_prefix_does_not_raise(self):
        assert state.render_log("\x1b[?5H") == []


@pytest.mark.unit
class TestReadLogTailUsesRenderLog:
    """MON-227: read_log_tail zwraca ostatnie n linii z render_log, nie z naiwnego stripu."""

    def test_fixture_tail_equals_tail_of_rendered_fixture(self, tmp_path):
        (tmp_path / "sid-spin.log").write_bytes((FIXTURES_DIR / SPINNER_FIXTURE_NAME).read_bytes())
        expected = state.render_log(_fixture_raw(SPINNER_FIXTURE_NAME))
        assert state.read_log_tail(tmp_path, "sid-spin", n=60) == expected[-60:]
        assert state.read_log_tail(tmp_path, "sid-spin", n=3) == expected[-3:]

    def test_carriage_return_is_not_translated_to_newline_when_reading_the_file(self, tmp_path):
        """Odczyt pliku w trybie tekstowym zamienia \\r na \\n i emulator widzialby dwie
        linie ("Tracking", "Tr ing") zamiast jednej."""
        (tmp_path / "sid-cr2.log").write_bytes(b"Tracking\rTr\x1b[6Ging\n")
        assert state.read_log_tail(tmp_path, "sid-cr2", n=5) == ["Tracking"]

    def test_spinner_fixture_has_no_spinner_line_in_tail(self, tmp_path):
        (tmp_path / "sid-spin2.log").write_bytes((FIXTURES_DIR / SPINNER_FIXTURE_NAME).read_bytes())
        tail = state.read_log_tail(tmp_path, "sid-spin2", n=60)
        assert not [line for line in tail if "Blanching" in line]

    def test_question_in_last_block_is_waiting_after_spinner_filter(self, tmp_path):
        raw = "⏺ Migracja gotowa.\n  Czy mam uruchomić migrację na produkcji?\n\n✢ Blanching… 4s · ↓ 12 tokens\n\u276f\n"
        (tmp_path / "sid-q.log").write_text(raw)
        tail = state.read_log_tail(tmp_path, "sid-q", n=60)
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "waiting"

    def test_question_in_older_block_is_running_after_spinner_filter(self, tmp_path):
        raw = "⏺ Czy kontynuować?\n\n⏺ Kontynuuję pracę.\n\n✢ Blanching… 4s · ↓ 12 tokens\n\u276f\n"
        (tmp_path / "sid-q2.log").write_text(raw)
        tail = state.read_log_tail(tmp_path, "sid-q2", n=60)
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "running"

    def test_build_report_with_raw_stop_fixture_is_waiting_via_render_log(self, tmp_path):
        tickets = [{"key": "MON-1", "status": "in_progress", "assignee": None}]
        agents = [bg_agent("monolynx-MON-1", agent_id="a2222222", session_id="uuid-2", agent_state="running")]
        (tmp_path / "a2222222.log").write_bytes((FIXTURES_DIR / STOP_FIXTURE_NAME).read_bytes())
        report = state.build_report("monolynx", tickets, agents, tmp_path)
        assert report["tickets"][0]["verdict"] == "waiting"


@pytest.mark.unit
class TestClassify:
    def test_none_todo_is_free(self):
        verdict, reason = state.classify("todo", "none", [])
        assert verdict == "free"
        assert isinstance(reason, str) and reason

    def test_none_in_review_is_done(self):
        verdict, reason = state.classify("in_review", "none", [])
        assert verdict == "done"
        assert reason

    def test_none_done_is_done(self):
        verdict, _ = state.classify("done", "none", [])
        assert verdict == "done"

    def test_none_in_progress_is_needs_human(self):
        """Brak sesji ale ticket w in_progress (np. sesja zabita recznie) -> needs_human."""
        verdict, reason = state.classify("in_progress", "none", [])
        assert verdict == "needs_human"
        assert reason

    def test_none_backlog_is_needs_human(self):
        verdict, _ = state.classify("backlog", "none", [])
        assert verdict == "needs_human"

    def test_exited_in_review_is_done(self):
        verdict, _ = state.classify("in_review", "exited", [])
        assert verdict == "done"

    def test_exited_done_is_done(self):
        verdict, _ = state.classify("done", "exited", [])
        assert verdict == "done"

    def test_exited_in_progress_is_needs_human(self):
        verdict, _ = state.classify("in_progress", "exited", [])
        assert verdict == "needs_human"

    def test_exited_todo_is_needs_human(self):
        """Sesja zakonczona, ale ticket nadal todo -- sesja zdechla bez pracy -> needs_human."""
        verdict, _ = state.classify("todo", "exited", [])
        assert verdict == "needs_human"

    def test_running_with_stop_marker_in_log_is_waiting(self):
        log_tail = ["robie rzeczy", "SPRINT-RUN STOP: brak toolchain", ""]
        verdict, reason = state.classify("in_progress", "running", log_tail)
        assert verdict == "waiting"
        assert reason

    def test_running_last_nonempty_line_ends_with_question_mark_is_waiting(self):
        log_tail = ["robie rzeczy", "", "Kontynuowac?"]
        verdict, _ = state.classify("in_progress", "running", log_tail)
        assert verdict == "waiting"

    def test_running_question_outside_5_line_window_is_running(self):
        """Pytanie na 6. linii od konca (poza oknem tail=5) nie wplywa na klasyfikacje."""
        log_tail = ["robie krok 1", "robie krok 2", "robie krok 3", "robie krok 4", "gotowe, koncze"]
        verdict, _ = state.classify("in_progress", "running", log_tail)
        assert verdict == "running"

    def test_running_empty_log_is_running(self):
        verdict, reason = state.classify("in_progress", "running", [])
        assert verdict == "running"
        assert reason

    def test_running_normal_progress_log_is_running(self):
        log_tail = ["czytam pliki", "pisze testy", "uruchamiam lint"]
        verdict, _ = state.classify("in_progress", "running", log_tail)
        assert verdict == "running"

    def test_reason_is_always_nonempty_string(self):
        for status in ("todo", "in_progress", "in_review", "done", "backlog"):
            for sess_state in ("none", "exited", "running"):
                verdict, reason = state.classify(status, sess_state, [])
                assert isinstance(verdict, str) and verdict
                assert isinstance(reason, str) and reason


# ---------------------------------------------------------------------------
# classify -- marker STOP odporny na zgubiona spacje (MON-191)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyStopMarkerWhitespace:
    """MON-191: `claude logs` gubi spacje w dlugich liniach (kursor skacze kolumna
    zamiast wypisac spacje), wiec marker przychodzi czasem jako 'SPRINT-RUNSTOP:'.
    Dopasowanie tolerancyjne na biale znaki miedzy 'SPRINT-RUN' i 'STOP:', ale
    dwukropek po STOP jest nadal wymagany -- inaczej proza wspominajaca marker
    (np. tresc komentarza na tickecie) falszywie dawalaby "waiting"."""

    def test_marker_without_space_is_waiting(self):
        tail = ["robie rzeczy", "SPRINT-RUNSTOP: brak toolchain"]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "waiting"

    def test_marker_with_space_is_still_waiting(self):
        tail = ["robie rzeczy", "SPRINT-RUN STOP: brak toolchain"]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "waiting"

    def test_no_marker_is_running(self):
        tail = ["robie rzeczy", "kontynuuje prace"]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "running"

    def test_prose_mentioning_marker_without_colon_is_not_waiting(self):
        """Proza bez dwukropka po STOP (np. komentarz na tickecie cytujacy marker)
        nie ma wygladac jak zakonczenie tury."""
        tail = ["dodalem komentarz SPRINT-RUN STOP na tickecie", "kontynuuje"]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "running"


# ---------------------------------------------------------------------------
# classify -- okno z kotwica "⏺" (ostatni blok odpowiedzi asystenta, MON-191)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyAnchorWindow:
    """Gdy log_tail zawiera linie zaczynajaca sie od '⏺' (poczatek bloku odpowiedzi
    asystenta w renderze TUI), oknem klasyfikacji jest ostatni taki blok (od OSTATNIEJ
    linii '⏺' do konca), nie ostatnie 5 linii -- inaczej linie spinnera/statusu/znaku
    zachety promptu ('Symbioting...', '| ctx:...', znak zachety) wypychaja marker albo
    pytanie poza stale okno 5."""

    def test_marker_in_anchor_block_after_spinner_and_status_lines_is_waiting(self):
        """Marker jest 3. linia bloku, ale linie spinnera/statusu/promptu po nim jest
        tyle, ze wypada poza dotychczasowe stale okno ostatnich 5 linii -- tylko
        okno kotwiczone do '⏺' go widzi."""
        tail = [
            "stara tresc sprzed bloku",
            "⏺",
            "SPRINT-RUN STOP: czekam na decyzje czlowieka",
            "Symbioting…",
            "✽ Symbioting… 113 tokens",
            "| ctx:24% | limit sesji:13% | limit tyg:15%",
            "Symbioting… 38",
            "Symbioting… 63",
            "\u276f",
        ]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "waiting"

    def test_marker_in_older_block_with_newer_anchor_without_marker_is_running(self):
        """Regresja z briefu MON-191: marker STOP z WCZESNIEJSZEGO bloku, po ktorym
        sesja zostala wznowiona i ma NOWY blok '⏺' bez markera -- ma liczyc sie jako
        running, nie jako wciaz czekajaca na starej decyzji."""
        tail = [
            "⏺",
            "SPRINT-RUN STOP: stara decyzja",
            "\u276f",
            "⏺",
            "kontynuuje prace po wznowieniu",
            "Symbioting…",
        ]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "running"

    def test_no_anchor_falls_back_to_last_5_lines(self):
        """Bez linii '⏺' w ogonie zachowanie jak dotychczas (MON-172): okno to
        ostatnie 5 linii -- marker na 6. linii od konca nie wplywa na werdykt."""
        tail = ["SPRINT-RUN STOP: stare", "b", "c", "d", "e", "f"]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "running"


@pytest.mark.unit
class TestClassifyAnchorQuestionMark:
    """Heurystyka pytania z kotwica '⏺': waiting gdy KTORAKOLWIEK linia bloku (po
    rstrip) konczy sie '?', nie tylko ostatnia niepusta linia calego ogona -- w
    renderze TUI po pytaniu ida linie spinnera/statusu/promptu, wiec ostatnia
    niepusta linia calego ogona to prawie nigdy samo pytanie."""

    def test_question_inside_anchor_block_with_trailing_spinner_lines_is_waiting(self):
        tail = [
            "⏺",
            "Czy chcesz kontynuowac migracje bazy danych?",
            "Symbioting…",
            "| ctx:10% | limit sesji:5%",
            "\u276f",
        ]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "waiting"

    def test_question_outside_anchor_block_is_running(self):
        """Pytanie sprzed ostatniego bloku '⏺' nie wplywa na werdykt nowego bloku."""
        tail = [
            "Czy chcesz kontynuowac?",
            "⏺",
            "kontynuuje inna prace, bez pytania",
        ]
        verdict, _ = state.classify("in_progress", "running", tail)
        assert verdict == "running"


# ---------------------------------------------------------------------------
# summarize
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSummarize:
    def test_empty_list_gives_all_zeros(self):
        summary = state.summarize([])
        assert summary == {"remaining": 0, "running": 0, "waiting": 0, "done": 0, "needs_human": 0}

    def test_mixed_verdicts_counted_correctly(self):
        rows = [
            {"verdict": "free"},
            {"verdict": "free"},
            {"verdict": "running"},
            {"verdict": "waiting"},
            {"verdict": "done"},
            {"verdict": "done"},
            {"verdict": "done"},
            {"verdict": "needs_human"},
        ]
        summary = state.summarize(rows)
        assert summary == {"remaining": 2, "running": 1, "waiting": 1, "done": 3, "needs_human": 1}


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestWaitsForPermission:
    """Sesja na promptcie o zgode to `waiting`, nie `running` (MON-173).

    Zaobserwowane realnie (2026-09-13, claude 2.1.270, sesje monolynx-MON-149 i MON-166):
    {"state": "blocked", "status": "waiting", "waitingFor": "permission prompt"} na wpisie background.
    """

    def test_permission_prompt_is_detected(self):
        session = bg_agent("monolynx-MON-1", agent_state="blocked")
        session.update({"status": "waiting", "waitingFor": "permission prompt"})
        assert state.waits_for_permission(session) is True

    def test_missing_or_other_waiting_for_is_not_detected(self):
        assert state.waits_for_permission(bg_agent("monolynx-MON-1")) is False
        session = bg_agent("monolynx-MON-1")
        session["waitingFor"] = "input needed"
        assert state.waits_for_permission(session) is False

    def test_build_report_marks_permission_prompt_session_as_waiting(self, tmp_path):
        tickets = [{"key": "MON-1", "status": "in_progress", "assignee": None}]
        session = bg_agent("monolynx-MON-1", agent_id="a1111111", agent_state="blocked")
        session.update({"status": "waiting", "waitingFor": "permission prompt"})
        report = state.build_report("monolynx", tickets, [session], tmp_path)
        row = report["tickets"][0]
        assert row["session_state"] == "running"
        assert row["verdict"] == "waiting"
        assert "claude attach" in row["reason"]
        assert report["summary"]["waiting"] == 1


class TestBuildReport:
    def test_three_tickets_full_scenario(self, tmp_path):
        tickets = [
            {"key": "MON-1", "status": "todo", "assignee": None},
            {"key": "MON-2", "status": "in_progress", "assignee": "backend-developer"},
            {"key": "MON-3", "status": "in_review", "assignee": None},
        ]
        agents = [
            bg_agent(
                "monolynx-MON-2",
                agent_id="a2222222",
                session_id="uuid-mon-2",
                agent_state="running",
            ),
            bg_agent(
                "monolynx-MON-3",
                agent_id="a3333333",
                session_id="uuid-mon-3",
                agent_state="exited",
            ),
        ]
        # Log jest nazwany po krotkim "id" z wpisu agenta (tak jak `claude logs <id>`),
        # NIE po UUID z "sessionId" -- to rozroznienie utrwala test_log_file_named_by_session_id_uuid_is_not_read.
        log_file = tmp_path / "a2222222.log"
        log_file.write_text("pracuje\nSPRINT-RUN STOP: potrzebuje decyzji\n")

        report = state.build_report("monolynx", tickets, agents, tmp_path)

        assert report["slug"] == "monolynx"
        assert [row["key"] for row in report["tickets"]] == ["MON-1", "MON-2", "MON-3"]

        row1, row2, row3 = report["tickets"]

        assert row1["session_id"] is None
        assert row1["session_name"] is None
        assert row1["session_state"] == "none"
        assert row1["verdict"] == "free"

        assert row2["session_id"] == "a2222222"
        assert row2["session_name"] == "monolynx-MON-2"
        assert row2["session_state"] == "running"
        assert row2["verdict"] == "waiting"

        assert row3["session_id"] == "a3333333"
        assert row3["session_state"] == "exited"
        assert row3["verdict"] == "done"

        assert report["summary"] == {"remaining": 1, "running": 0, "waiting": 1, "done": 1, "needs_human": 0}

    def test_preserves_input_order(self, tmp_path):
        tickets = [
            {"key": "MON-9", "status": "todo", "assignee": None},
            {"key": "MON-1", "status": "todo", "assignee": None},
            {"key": "MON-5", "status": "todo", "assignee": None},
        ]
        report = state.build_report("monolynx", tickets, [], tmp_path)
        assert [row["key"] for row in report["tickets"]] == ["MON-9", "MON-1", "MON-5"]

    def test_no_session_leaves_ids_null(self, tmp_path):
        tickets = [{"key": "MON-1", "status": "todo", "assignee": None}]
        report = state.build_report("monolynx", tickets, [], tmp_path)
        row = report["tickets"][0]
        assert row["session_id"] is None
        assert row["session_name"] is None

    def test_log_file_named_by_session_id_uuid_is_not_read(self, tmp_path):
        """Log ma nazwe <id>.log (krotki identyfikator z wpisu agenta), NIE <sessionId>.log
        (UUID) -- plik zapisany pod UUID nie jest odnajdywany, wiec marker STOP w nim nie
        wplywa na werdykt (utrwala poprawke z iteracji 3: session_id w raporcie to pole "id")."""
        tickets = [{"key": "MON-4", "status": "in_progress", "assignee": None}]
        agents = [
            bg_agent(
                "monolynx-MON-4",
                agent_id="a4444444",
                session_id="11111111-2222-3333-4444-555555555555",
                agent_state="running",
            )
        ]
        wrong_log_file = tmp_path / "11111111-2222-3333-4444-555555555555.log"
        wrong_log_file.write_text("SPRINT-RUN STOP: to nie powinno byc przeczytane\n")

        report = state.build_report("monolynx", tickets, agents, tmp_path)

        row = report["tickets"][0]
        assert row["session_id"] == "a4444444"
        assert row["verdict"] == "running"

    def test_unknown_session_state_reason_is_appended_to_row_reason(self, tmp_path):
        """MON-172: literal stanu spoza znanej listy (np. 'compacting' w przyszlej
        wersji `claude`) nie zmienia werdyktu (running), ale dokleja ostrzezenie do
        reason, zeby dyspozytor/czlowiek widzial ze stan byl nierozpoznany."""
        tickets = [{"key": "MON-7", "status": "in_progress", "assignee": None}]
        agents = [bg_agent("monolynx-MON-7", agent_id="a7777777", agent_state="compacting")]

        report = state.build_report("monolynx", tickets, agents, tmp_path)

        row = report["tickets"][0]
        assert row["session_state"] == "running"
        assert row["verdict"] == "running"
        assert "nieznany stan" in row["reason"]
        assert "compacting" in row["reason"]


@pytest.mark.unit
class TestBuildReportRealStopFixture:
    """MON-191: build_report musi czytac ogon dluzszy niz 5 linii, zeby caly blok
    odpowiedzi asystenta (kotwica '⏺' ... marker STOP ... linie spinnera/statusu/
    promptu) zmiescil sie w oknie klasyfikacji. Fixture:
    tests/fixtures/claude/logs-stop-tui.2.1.278.txt (naglowek: komenda, wersja CLI,
    data przechwycenia). Marker jest 17. niepusta linia od konca (po znormalizowaniu
    ANSI/\\r), a stale okno 5 linii z poprzedniej wersji go nie widzi."""

    def test_running_session_with_real_stop_fixture_log_is_waiting(self, tmp_path):
        tickets = [{"key": "MON-1", "status": "in_progress", "assignee": None}]
        agents = [
            bg_agent(
                "monolynx-MON-1",
                agent_id="a1111111",
                session_id="uuid-mon-1",
                agent_state="running",
            )
        ]
        fixture_path = FIXTURES_DIR / "logs-stop-tui.2.1.278.txt"
        log_file = tmp_path / "a1111111.log"
        log_file.write_bytes(fixture_path.read_bytes())

        report = state.build_report("monolynx", tickets, agents, tmp_path)

        row = report["tickets"][0]
        assert row["session_id"] == "a1111111"
        assert row["session_state"] == "running"
        assert row["verdict"] == "waiting"
        assert report["summary"]["waiting"] == 1


# ---------------------------------------------------------------------------
# main (CLI)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMainCli:
    def _write_json(self, path, data):
        path.write_text(json.dumps(data))

    def test_valid_input_exits_zero_and_prints_json(self, tmp_path, capsys):
        agents_file = tmp_path / "agents.json"
        tickets_file = tmp_path / "tickets.json"
        self._write_json(agents_file, [])
        self._write_json(tickets_file, [{"key": "MON-1", "status": "todo", "assignee": None}])

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 0
        captured = capsys.readouterr()
        payload = json.loads(captured.out)
        assert payload["slug"] == "monolynx"
        assert payload["tickets"][0]["key"] == "MON-1"

    def test_agents_file_dash_reads_stdin(self, tmp_path, capsys, monkeypatch):
        tickets_file = tmp_path / "tickets.json"
        self._write_json(tickets_file, [{"key": "MON-1", "status": "todo", "assignee": None}])
        monkeypatch.setattr(sys, "stdin", io.StringIO("[]"))

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                "-",
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["tickets"][0]["key"] == "MON-1"

    def test_invalid_json_in_tickets_file_exits_2(self, tmp_path):
        agents_file = tmp_path / "agents.json"
        tickets_file = tmp_path / "tickets.json"
        self._write_json(agents_file, [])
        tickets_file.write_text("{to nie jest poprawny json")

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 2

    def test_missing_tickets_file_exits_2(self, tmp_path):
        agents_file = tmp_path / "agents.json"
        self._write_json(agents_file, [])
        missing_file = tmp_path / "nie-istnieje.json"

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(missing_file),
            ]
        )

        assert exit_code == 2

    def test_tickets_file_not_a_list_exits_2(self, tmp_path):
        agents_file = tmp_path / "agents.json"
        tickets_file = tmp_path / "tickets.json"
        self._write_json(agents_file, [])
        self._write_json(tickets_file, {"not": "a list"})

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 2

    def test_ticket_entry_missing_key_field_exits_2(self, tmp_path, capsys):
        agents_file = tmp_path / "agents.json"
        tickets_file = tmp_path / "tickets.json"
        self._write_json(agents_file, [])
        self._write_json(tickets_file, [{"status": "todo"}])

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 2
        assert capsys.readouterr().err.strip()

    def test_ticket_entry_not_a_dict_exits_2(self, tmp_path, capsys):
        agents_file = tmp_path / "agents.json"
        tickets_file = tmp_path / "tickets.json"
        self._write_json(agents_file, [])
        self._write_json(tickets_file, ["MON-1"])

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 2
        assert capsys.readouterr().err.strip()

    def test_missing_slug_argument_exits_64(self, tmp_path):
        agents_file = tmp_path / "agents.json"
        tickets_file = tmp_path / "tickets.json"
        self._write_json(agents_file, [])
        self._write_json(tickets_file, [])

        exit_code = state.main(
            [
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 64

    def test_missing_logs_dir_argument_works_with_empty_logs(self, tmp_path, capsys):
        agents_file = tmp_path / "agents.json"
        tickets_file = tmp_path / "tickets.json"
        self._write_json(agents_file, [bg_agent("monolynx-MON-1", agent_state="running")])
        self._write_json(tickets_file, [{"key": "MON-1", "status": "in_progress", "assignee": None}])

        exit_code = state.main(
            [
                "--slug",
                "monolynx",
                "--agents-file",
                str(agents_file),
                "--tickets-file",
                str(tickets_file),
            ]
        )

        assert exit_code == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["tickets"][0]["session_state"] == "running"
        assert payload["tickets"][0]["verdict"] == "running"


# ---------------------------------------------------------------------------
# MON-185: classify()/summarize() sa juz BACKEND-AGNOSTYCZNE z konstrukcji --
# przyjmuja tylko stringi (session_state_value, log_tail) i dicty z kluczem
# "verdict", nigdy nie widza "backend"/"runtime". Te testy NIE importuja
# plugin/scripts/sprint_run_backend.py (adapter ma wlasne testy w
# tests/unit/test_sprint_run_backend.py) -- celowo, zeby ten plik zalezal
# tylko od sprint_run_state.py. Dowodza WPROST na publicznym API
# sprint_run_state.py, ze classify()/summarize() NIE WYMAGAJA zadnej zmiany
# dla MON-185: bridge w sprint_run_backend.classify_normalized (patrz jego
# docstring) tylko mapuje znormalizowany rekord Codex/Claude na te same
# argumenty, ktore ponizsze testy juz cwicza bezposrednio.
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClassifyBackendAgnostic:
    """classify(ticket_status, session_state_value, log_tail) nie ma pojecia
    'backend' -- session_state_value to juz DZIS wspolny 3-stanowy kontrakt
    ('none'/'running'/'exited'), do ktorego adapter Codex (MON-185) bedzie sie
    musial zmapowac (running/waiting -> 'running', success/failure ->
    'exited'). Te testy pokazuja, ze wynik jest identyczny niezaleznie od tego,
    jaki backend logicznie "wyprodukowal" dany session_state_value/log_tail --
    czyli ze klasyfikator nie wymaga zadnej zmiany kodu dla MON-185."""

    def test_exited_state_from_codex_success_mapping_matches_claude_exited(self):
        """Rekord Codex w stanie 'success' bridguje sie do session_state_value
        'exited' -- identycznie jak martwa sesja Claude. Werdykt (i caly kod
        classify()) jest wiec juz dzis wspolny dla obu backendow."""
        verdict_claude, _ = state.classify("in_review", "exited", [])
        verdict_codex_success, _ = state.classify("in_review", "exited", [])
        assert verdict_claude == verdict_codex_success == "done"

    def test_running_state_from_codex_waiting_mapping_matches_claude_running(self):
        """Rekord Codex w stanie adaptera 'waiting' bridguje sie RAZEM z 'running'
        do session_state_value 'running' (Codex nie ma natywnego sygnalu
        oczekiwania na zgode -- MON-184; jedyny sygnal 'waiting' dla Codex to
        marker SPRINT-RUN STOP: w tresci logu, ktory classify() juz dzis
        wykrywa z log_tail, ponizej)."""
        log_tail = ["robie rzeczy", "SPRINT-RUN STOP: brak toolchain"]
        verdict, _ = state.classify("in_progress", "running", log_tail)
        assert verdict == "waiting"

    def test_stop_marker_verdict_is_waiting_regardless_of_log_origin(self):
        """SPRINT-RUN STOP: daje werdykt 'waiting' (frozen enum sprint_run_state:
        free/running/waiting/done/needs_human) -- classify() czyta WYLACZNIE
        tresc log_tail (stringi), nigdy zadnego pola 'backend', wiec ten sam log
        pochodzacy z Claude (`claude logs`) albo z Codex (`codex exec --json`,
        po ekstrakcji tekstu odpowiedzi) daje identyczny werdykt."""
        log_from_claude_origin = ["kontynuuje prace", "SPRINT-RUN STOP: czekam na decyzje"]
        log_from_codex_origin = ["kontynuuje prace", "SPRINT-RUN STOP: czekam na decyzje"]

        verdict_a, _ = state.classify("in_progress", "running", log_from_claude_origin)
        verdict_b, _ = state.classify("in_progress", "running", log_from_codex_origin)

        assert verdict_a == verdict_b == "waiting"

    def test_summarize_counts_rows_regardless_of_extra_backend_key(self):
        """summarize() czyta WYLACZNIE klucz 'verdict' -- dodatkowe pola w
        wierszu (np. 'backend': 'codex' dopisane przez bridge) sa dla niej
        niewidoczne, wiec liczenie slotow jest juz dzis lacznie dla mieszanych
        backendow bez zadnej zmiany kodu."""
        rows = [
            {"key": "MON-1", "verdict": "running", "backend": "claude"},
            {"key": "MON-2", "verdict": "running", "backend": "codex"},
            {"key": "MON-3", "verdict": "done", "backend": "codex"},
            {"key": "MON-4", "verdict": "free", "backend": "claude"},
        ]
        summary = state.summarize(rows)
        assert summary == {"remaining": 1, "running": 2, "waiting": 0, "done": 1, "needs_human": 0}

    def test_needs_human_verdict_reachable_regardless_of_which_backend_finished_the_session(self):
        """Sesja martwa/zakonczona (session_state_value 'exited', do ktorego
        bridguje sie zarowno Claude 'exited' jak i Codex 'success'/'failure')
        z ticketem wciaz w toku -> 'needs_human', niezaleznie od tego, ktory
        backend prowadzil sesje -- ten sam werdykt co dzisiejszy kontrakt Claude
        (patrz TestClassify.test_exited_in_progress_is_needs_human)."""
        verdict, reason = state.classify("in_progress", "exited", [])
        assert verdict == "needs_human"
        assert reason


# ---------------------------------------------------------------------------
# MON-222: sesje waiting poza pula slotow + limit MONOLYNX_SPRINT_MAX_WAITING
# ---------------------------------------------------------------------------


def _summary(running=0, waiting=0, remaining=0, done=0, needs_human=0):
    return {"remaining": remaining, "running": running, "waiting": waiting, "done": done, "needs_human": needs_human}


@pytest.mark.unit
class TestParseMaxWaiting:
    def test_default_constant_is_two(self):
        assert state.DEFAULT_MAX_WAITING == 2

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("3", 3), (" 5 ", 5), ("1", 1), ("2", 2), ("10", 10)],
    )
    def test_positive_integer_is_returned(self, raw, expected):
        assert state.parse_max_waiting(raw) == expected

    @pytest.mark.parametrize("raw", [None, "", "   ", "abc", "0", "-1", "2.5", "\u00b2", "\u0663"])
    def test_invalid_or_non_positive_falls_back_to_default(self, raw):
        assert state.parse_max_waiting(raw) == state.DEFAULT_MAX_WAITING == 2


@pytest.mark.unit
class TestDispatchCapacity:
    def test_waiting_session_does_not_consume_a_slot(self):
        """Regresja MON-138: sesja czekajaca na czlowieka nie moze zabierac slotu
        przez cala noc - wolne = sloty - running, bez waiting."""
        result = state.dispatch_capacity(_summary(running=0, waiting=1), slots=2, max_waiting=2)

        assert result["free_slots"] == 2
        assert result["waiting_blocked"] is False
        assert result["can_start"] == 2

    def test_running_session_consumes_a_slot_waiting_does_not(self):
        result = state.dispatch_capacity(_summary(running=1, waiting=1), slots=2, max_waiting=2)

        assert result["free_slots"] == 1
        assert result["can_start"] == 1

    def test_all_slots_taken_by_running_gives_zero(self):
        result = state.dispatch_capacity(_summary(running=1), slots=1, max_waiting=2)

        assert result["free_slots"] == 0
        assert result["can_start"] == 0
        assert result["waiting_blocked"] is False

    def test_running_above_slots_is_clamped_to_zero_not_negative(self):
        result = state.dispatch_capacity(_summary(running=3), slots=2, max_waiting=2)

        assert result["free_slots"] == 0
        assert result["can_start"] == 0

    def test_waiting_at_limit_blocks_start_but_free_slots_still_computed(self):
        result = state.dispatch_capacity(_summary(running=0, waiting=2), slots=3, max_waiting=2)

        assert result["waiting_blocked"] is True
        assert result["free_slots"] == 3
        assert result["can_start"] == 0

    def test_waiting_above_limit_blocks_start(self):
        result = state.dispatch_capacity(_summary(running=1, waiting=5), slots=3, max_waiting=2)

        assert result["waiting_blocked"] is True
        assert result["free_slots"] == 2
        assert result["can_start"] == 0

    def test_waiting_just_below_limit_does_not_block(self):
        """Granica: waiting == max blokuje (>=), waiting == max-1 nie."""
        result = state.dispatch_capacity(_summary(waiting=1), slots=2, max_waiting=2)

        assert result["waiting_blocked"] is False
        assert result["can_start"] == 2

    def test_limit_of_one_blocks_at_single_waiting_session(self):
        result = state.dispatch_capacity(_summary(waiting=1), slots=2, max_waiting=1)

        assert result["waiting_blocked"] is True
        assert result["can_start"] == 0

    def test_result_has_exactly_the_contract_keys_and_echoes_inputs(self):
        result = state.dispatch_capacity(_summary(running=1), slots=4, max_waiting=3)

        assert set(result) == {"slots", "max_waiting", "free_slots", "waiting_blocked", "can_start"}
        assert result["slots"] == 4
        assert result["max_waiting"] == 3

    def test_ignores_other_summary_keys_and_does_not_mutate_input(self):
        summary = _summary(running=1, waiting=0, remaining=7, done=9, needs_human=4)
        snapshot = dict(summary)

        result = state.dispatch_capacity(summary, slots=3, max_waiting=2)

        assert summary == snapshot
        assert result["free_slots"] == 2
        assert result["can_start"] == 2


# ---------------------------------------------------------------------------
# MON-252: sesje, ktore tylko czekaja (wejscie uzytkownika, numerowane opcje,
# fraza oczekiwania, detektor zastoju)
#
# Fixture'y tests/fixtures/claude/logs-*.2.1.288.txt to segmenty jednej sesji
# przepisane doslownie z ogonow logow odzyskanych z transkryptu dyspozytora
# (sesje MON-244/MON-249 usuniete, `claude logs` ich juz nie zwraca). Naglowek w
# pierwszej linii pliku podaje sesje i zrodlo. Stan "pytanie przed odpowiedzia
# uzytkownika" nie zostal przechwycony osobno: testy budują go z realnego
# fixture przez odciecie ostatniej linii `<znak promptu U+276F> <tekst>` (_question_state).
#
# Literaly: "Czekam", "Decyzja dla Ciebie", "<znak promptu U+276F> <tekst>", "SPRINT-RUN STOP:" i linia
# podpowiedzi TUI "1: Learn more   2: Knew this already   0: Dismiss" sa
# ZAOBSERWOWANE w fixture'ach. Frazy "waiting for", "wybierz" i "opcja 1" pochodza
# Z DEFINICJI TICKETU MON-252, nie z fixture - testy wejsc z tymi frazami budują
# linie sztucznie (komentarz przy kazdym).
# ---------------------------------------------------------------------------

STALL_FIXTURE_NAME = "logs-stall-czekam.2.1.288.txt"
WAITING_PHRASE_FIXTURE_NAME = "logs-waiting-phrase.2.1.288.txt"
DECISION_FIXTURE_NAME = "logs-decision-options-answered.2.1.288.txt"
STOP_ANSWERED_FIXTURE_NAME = "logs-stop-answered.2.1.288.txt"
TIP_FIXTURE_NAME = "logs-tip-numbered.2.1.288.txt"

SESSION_ID = "a1111111"

CZEKAM_LINE = "⏺ Czekam."
PIPELINE_WAIT_LINE = "⏺ Czekam na pipeline MR !158 (2909085940)."
STATUS_LINE = "MON-244 | worktree-MON-244 | Opus 5.5 | ctx:34% | limit sesji:9% | limit tyg:30%"
MODE_LINE = "⏵⏵ auto mode on · ← 6 agents · 1 shell"
AUTO_COMPACT_LINE = "3% until auto-compact"
LIMIT_FRAGMENT_LINE = "limit tyg:31%"
RECAP_TAIL_LINE = "pełny przebieg, gdy cudzy kontener skończy, potem commit, push i MR. (disable recaps in /config)"
DECISION_PHRASE_LINE = "Decyzja dla Ciebie - jak domknąć review:"
# Jedyna widoczna opcja z przechwycenia 13946c6f (rec-9309): druga linia listy, pierwsza poza ogonem.
SINGLE_OPTION_LINE = (
    "2. Powtórka pełnego przebiegu lokalnie (ok. 20-70 min) - sensowna dopiero, gdy żadna inna sesja nie używa bazy testowej. "
    "Review nadal zostaje po Twojej stronie."
)
# Wypowiedz z przechwycenia 0b8e0d22 zawinieta przez terminal na dwie linie: "Czekam" stoi w srodku drugiej
# linii, a po nim recap.
WRAPPED_WAIT_LINES = [
    (
        "⏺ Pełny przebieg nr 1: 1786 failed / 1280 errors - błędy sqlalchemy w setupie, bo równolegle inna sesja sprintu "
        "odpaliła pełne testy na wspólnej bazie open_sentry_test. To kolizja środowiska, nie kod"
    ),
    "(zawężone testy grafu i CLI zielone). Czekam na zwolnienie bazy i powtarzam pełny przebieg.",
    (
        "※ recap: MON-248 (odczyt grafu przez API v2 i CLI): kod, testy i dokumentacja gotowe, ale pełny przebieg testów padł "
        "przez kolizję na wspólnej bazie testowej. Następny krok: powtórzyć pełne"
    ),
    "testy po zwolnieniu bazy, potem review krytyka.",
]


def _fixture_lines(name):
    """Linie logu z fixture 2.1.288 bez naglowka `# claude logs ...`.

    Fixture to ogon po render_log (zwykly tekst, bez sekwencji sterujacych), czyli to,
    co sprint_run_tick.py zapisuje w TICK_DIR/logs/<id>.log - czytamy go jak ten plik.
    """
    lines = (FIXTURES_DIR / name).read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("# claude logs "), name
    return lines[1:]


def _replace_line(lines, old, new):
    assert lines.count(old) == 1, old
    return [new if line == old else line for line in lines]


def _question_state(name):
    """Fixture bez ostatniej linii `<znak promptu U+276F> <tekst>`: stan sprzed odpowiedzi uzytkownika."""
    lines = _fixture_lines(name)
    # Realny log ma po `znak promptu` twardą spację (U+00A0), nie zwykłą.
    assert lines[-1].startswith("\u276f") and lines[-1][1:].strip(), f"{name}: ostatnia linia fixture nie jest wejściem użytkownika: {lines[-1]!r}"
    return lines[:-1]


def _stall_lines(first_line="⏺ Sprawdzam wynik pelnego przebiegu."):
    """Ogon sesji MON-244 z fixture stall; `⏺ Czekam.` podmienione, zeby fraza oczekiwania
    nie dawala werdyktu `waiting` i zastoj byl jedynym sygnalem."""
    return _replace_line(_fixture_lines(STALL_FIXTURE_NAME), CZEKAM_LINE, first_line)


def _volatile_lines():
    """Ogon stall z dodatkowymi liniami zmiennymi TUI z przechwycenia waiting-phrase
    (`N% until auto-compact`, urwany fragment statusu `limit tyg:..%`)."""
    lines = _stall_lines()
    prompt = lines.index("\u276f")
    return [*lines[:prompt], AUTO_COMPACT_LINE, "\u276f", LIMIT_FRAGMENT_LINE, *lines[prompt + 1 :]]


def _verdict(lines):
    return state.classify("in_progress", "running", lines)


_RUNNING_REASON = _verdict(["czytam pliki"])[1]


class _Ticker:
    """Kolejne ticki dyspozytora: ten sam katalog logow i stanu, jedna sesja w tle.

    state_dir lezy obok logs_dir (tak jak TICK_DIR/stall.json obok TICK_DIR/logs), bo logs_dir
    jest czyszczony co tick. stall_ticks=None pomija argument, czyli sprawdza wartosc domyslna.
    """

    def __init__(self, tmp_path, stall_ticks=None, with_state_dir=True):
        self.logs_dir = tmp_path / "logs"
        self.logs_dir.mkdir()
        self.state_dir = tmp_path / "state" if with_state_dir else None
        if self.state_dir is not None:
            self.state_dir.mkdir()
        self.stall_ticks = stall_ticks

    def run(self, log_lines, *, session_id=SESSION_ID, agent_state="running", ticket_status="in_progress"):
        if log_lines is not None:
            (self.logs_dir / f"{session_id}.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")
        tickets = [{"key": "MON-1", "status": ticket_status, "assignee": None}]
        agents = [bg_agent("monolynx-MON-1", session_id=f"uuid-{session_id}", agent_id=session_id, agent_state=agent_state)]
        if self.stall_ticks is None:
            report = state.build_report("monolynx", tickets, agents, self.logs_dir, state_dir=self.state_dir)
        else:
            report = state.build_report("monolynx", tickets, agents, self.logs_dir, state_dir=self.state_dir, stall_ticks=self.stall_ticks)
        return report["tickets"][0]

    def stall_path(self):
        assert self.state_dir is not None
        return self.state_dir / "stall.json"

    def stall_data(self):
        return json.loads(self.stall_path().read_text(encoding="utf-8"))


@pytest.mark.unit
class TestParseStallTicks:
    def test_default_constant_is_two(self):
        assert state.DEFAULT_STALL_TICKS == 2

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [("3", 3), (" 3 ", 3), ("1", 1), ("2", 2), ("10", 10)],
    )
    def test_positive_integer_is_returned(self, raw, expected):
        assert state.parse_stall_ticks(raw) == expected

    # "\u00b2" (indeks gorny) i "\u0663" (cyfra arabsko-indyjska) przechodza str.isdigit(), ale int() na pierwszym
    # rzuca ValueError, a drugi daje 3 - wartosc spoza ASCII ma wrocic do domyslnej bez wyjatku.
    @pytest.mark.parametrize("raw", [None, "", "   ", "abc", "0", "-1", "2.5", "\u00b2", "\u0663"])
    def test_invalid_or_non_positive_falls_back_to_default(self, raw):
        assert state.parse_stall_ticks(raw) == state.DEFAULT_STALL_TICKS == 2


@pytest.mark.unit
class TestClassifyUserInput:
    """Wejscie uzytkownika `<znak promptu U+276F> <tekst>` zdejmuje `waiting`: sygnaly liczą sie tylko z linii po nim."""

    @pytest.mark.parametrize(
        ("fixture_name", "reason_fragment"),
        [
            pytest.param(STOP_ANSWERED_FIXTURE_NAME, "SPRINT-RUN STOP", id="marker-stop"),
            pytest.param(DECISION_FIXTURE_NAME, "numerowane opcje", id="numerowane-opcje"),
        ],
    )
    def test_answer_turns_waiting_session_into_running(self, fixture_name, reason_fragment):
        answered = _fixture_lines(fixture_name)
        question = _question_state(fixture_name)

        verdict, reason = _verdict(question)
        assert verdict == "waiting"
        assert reason_fragment in reason
        assert _verdict(answered) == ("running", _RUNNING_REASON)

    @pytest.mark.parametrize("fixture_name", [STOP_ANSWERED_FIXTURE_NAME, DECISION_FIXTURE_NAME])
    @pytest.mark.parametrize("empty_prompt", ["\u276f", "  \u276f  "])
    def test_empty_prompt_does_not_clear_waiting(self, fixture_name, empty_prompt):
        # Pusty prompt TUI to chrome, nie odpowiedz: pytanie nadal czeka.
        assert _verdict([*_question_state(fixture_name), empty_prompt])[0] == "waiting"

    @pytest.mark.parametrize("fixture_name", [STOP_ANSWERED_FIXTURE_NAME, DECISION_FIXTURE_NAME])
    def test_real_user_input_with_non_breaking_space_clears_waiting(self, fixture_name):
        # Realne wejście z przechwycenia ma `<znak promptu>\xa0<tekst>`; rozpoznanie tylko po `znak promptu` + zwykłej spacji by je pominęło.
        answered = _fixture_lines(fixture_name)
        assert answered[-1].startswith("\u276f\xa0")

        assert _verdict(answered) == ("running", _RUNNING_REASON)

    @pytest.mark.parametrize("fixture_name", [STOP_ANSWERED_FIXTURE_NAME, DECISION_FIXTURE_NAME])
    def test_prompt_with_only_a_non_breaking_space_is_not_user_input(self, fixture_name):
        assert _verdict([*_question_state(fixture_name), "\u276f\xa0"])[0] == "waiting"

    def test_indented_user_input_counts(self):
        assert _verdict([*_question_state(STOP_ANSWERED_FIXTURE_NAME), "  \u276f zgoda"]) == ("running", _RUNNING_REASON)

    def test_prompt_char_in_the_middle_of_a_line_is_not_user_input(self):
        verdict, _ = _verdict([*_question_state(STOP_ANSWERED_FIXTURE_NAME), "wynik \u276f ok"])
        assert verdict == "waiting"

    @pytest.mark.parametrize(
        ("fixture_name", "new_block", "reason_fragment"),
        [
            pytest.param(STOP_ANSWERED_FIXTURE_NAME, ["⏺ Zrobić też drugi test?"], "pytanie", id="pytajnik"),
            pytest.param(
                STOP_ANSWERED_FIXTURE_NAME,
                ["⏺ Zatrzymuję się.", "SPRINT-RUN STOP: brak toolchain"],
                "SPRINT-RUN STOP",
                id="marker-stop",
            ),
            # Linie z "wybierz" i listą zbudowane sztucznie: fraza "wybierz" pochodzi z definicji ticketu.
            pytest.param(
                DECISION_FIXTURE_NAME,
                ["⏺ Mam dwa warianty.", "Wybierz wariant:", "1. wariant A", "2. wariant B"],
                "numerowane opcje",
                id="numerowane-opcje",
            ),
        ],
    )
    def test_signal_after_user_input_is_waiting_again(self, fixture_name, new_block, reason_fragment):
        answered = _fixture_lines(fixture_name)
        assert _verdict(answered)[0] == "running"

        verdict, reason = _verdict([*answered, *new_block])

        assert verdict == "waiting"
        assert reason_fragment in reason

    def test_wait_phrase_after_user_input_is_waiting_again(self):
        lines = _replace_line(_fixture_lines(STALL_FIXTURE_NAME), "\u276f", "\u276f sprawdź status")
        assert _verdict(lines)[0] == "running"

        verdict, reason = _verdict([*lines, "⏺ Czekam na CI."])

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    def test_only_the_last_user_input_counts(self):
        lines = ["\u276f pierwsze", "SPRINT-RUN STOP: x", "\u276f drugie"]
        assert _verdict(lines) == ("running", _RUNNING_REASON)

    @pytest.mark.parametrize(
        "lines",
        [
            pytest.param(["⏺ Czekam na CI.", "\u276f czy to działa?"], id="z-kotwica"),
            pytest.param(["pracuję nad testami", "\u276f czy to działa?"], id="bez-kotwicy"),
        ],
    )
    def test_question_mark_in_user_input_is_not_an_assistant_question(self, lines):
        assert _verdict(lines) == ("running", _RUNNING_REASON)


@pytest.mark.unit
class TestClassifyNumberedOptions:
    """Numerowane opcje + fraza decyzji = `waiting` takze bez pytajnika."""

    @staticmethod
    def _options_block(phrase_line, first="1. pierwszy wariant", second="2. drugi wariant"):
        return ["⏺ Mam dwa warianty dalszej pracy.", phrase_line, first, second, "Rekomenduję pierwszy."]

    def test_real_decision_without_question_mark_is_waiting(self):
        lines = _question_state(DECISION_FIXTURE_NAME)
        assert not any(line.rstrip().endswith("?") for line in lines)

        verdict, reason = _verdict(lines)

        # Fraza stoi 6. linią od końca, poza oknem 5 linii: wykrycie musi patrzeć na cały ogon.
        assert verdict == "waiting"
        assert "numerowane opcje" in reason

    # "Decyzja dla Ciebie" zaobserwowana w fixture; "wybierz" i "opcja 1" z definicji ticketu.
    @pytest.mark.parametrize(
        "phrase_line",
        [
            DECISION_PHRASE_LINE,
            "DECYZJA DLA CIEBIE - wybór wariantu:",
            "Wybierz wariant:",
            "wybierz jeden z dwóch",
            "Najlepsza będzie opcja 1 (szybsza).",
            "OPCJA 1 jest szybsza.",
        ],
    )
    @pytest.mark.parametrize(("first", "second"), [("1. pierwszy wariant", "2. drugi wariant"), ("1) pierwszy wariant", "2) drugi wariant")])
    def test_numbered_list_with_decision_phrase_is_waiting(self, phrase_line, first, second):
        verdict, reason = _verdict(self._options_block(phrase_line, first, second))

        assert verdict == "waiting"
        assert "numerowane opcje" in reason

    def test_three_options_with_phrase_are_waiting(self):
        lines = [DECISION_PHRASE_LINE, "1. A", "2. B", "3. C"]
        assert _verdict(lines)[0] == "waiting"

    def test_list_without_decision_phrase_is_running(self):
        lines = ["⏺ Poniżej dwa warianty dalszej pracy:", "1. pierwszy wariant", "2. drugi wariant"]
        assert _verdict(lines) == ("running", _RUNNING_REASON)

    @pytest.mark.parametrize("phrase_line", [DECISION_PHRASE_LINE, "Wybierz wariant:", "Najlepsza będzie opcja 1."])
    def test_decision_phrase_without_list_is_running(self, phrase_line):
        assert _verdict(["⏺ Mam dwa warianty dalszej pracy.", phrase_line]) == ("running", _RUNNING_REASON)

    def test_real_phrase_with_digit_in_the_middle_of_a_line_is_running(self):
        # Realne linie sesji MON-244 sprzed markera: "Decyzja dla Ciebie: ... zgoda na 4. poprawkę ..." bez listy.
        lines = _fixture_lines(STOP_ANSWERED_FIXTURE_NAME)[:3]
        assert any("Decyzja dla Ciebie" in line for line in lines)
        assert _verdict(lines) == ("running", _RUNNING_REASON)

    def test_digit_not_at_the_start_of_a_line_is_not_an_option(self):
        lines = [DECISION_PHRASE_LINE, "krok 1. pierwszy wariant", "krok 2. drugi wariant"]
        assert _verdict(lines) == ("running", _RUNNING_REASON)

    def test_single_option_with_decision_phrase_is_running(self):
        assert _verdict([DECISION_PHRASE_LINE, SINGLE_OPTION_LINE]) == ("running", _RUNNING_REASON)

    def test_tui_tip_line_is_not_an_option_list(self):
        tip = _fixture_lines(TIP_FIXTURE_NAME)
        assert "1: Learn more   2: Knew this already   0: Dismiss" in tip

        assert _verdict([*tip, DECISION_PHRASE_LINE]) == ("running", _RUNNING_REASON)

    def test_tui_tip_line_is_not_counted_as_the_second_option(self):
        # Jedna prawdziwa opcja + linia podpowiedzi TUI: gdyby podpowiedz liczyla sie jako opcja, bylyby dwie.
        tip = _fixture_lines(TIP_FIXTURE_NAME)

        assert _verdict([DECISION_PHRASE_LINE, SINGLE_OPTION_LINE, *tip]) == ("running", _RUNNING_REASON)


@pytest.mark.unit
class TestClassifyWaitingPhrase:
    """Ostatnia wypowiedz asystenta konczy sie fraza oczekiwania = `waiting` (zastoj bez pytania)."""

    def test_czekam_fixture_with_empty_prompt_is_waiting(self):
        lines = _fixture_lines(STALL_FIXTURE_NAME)
        assert "\u276f" in lines

        verdict, reason = _verdict(lines)

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    def test_waiting_phrase_fixture_with_tui_hint_is_waiting(self):
        verdict, reason = _verdict(_fixture_lines(WAITING_PHRASE_FIXTURE_NAME))

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    def test_wrapped_utterance_with_wait_sentence_in_the_second_line_is_waiting(self):
        verdict, reason = _verdict(WRAPPED_WAIT_LINES)

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    # Trzy pierwsze zdania z przechwyconych sesji MON-244/MON-248; reszta wariantów zbudowana sztucznie
    # ("waiting for" pochodzi z definicji ticketu, małe litery i wielkość liter to sprawdzenie wymagania z kontraktu).
    @pytest.mark.parametrize(
        "line",
        [
            pytest.param(CZEKAM_LINE, id="czekam-kropka"),
            pytest.param(PIPELINE_WAIT_LINE, id="czekam-na-pipeline"),
            pytest.param("⏺ Pełny przebieg wystartował (ok. 25 min). Czekam na wynik.", id="po-innym-zdaniu"),
            pytest.param("⏺ czekam na wynik.", id="male-litery"),
            pytest.param("⏺ Uruchomiłem testy, waiting for CI.", id="waiting-for"),
            pytest.param("⏺ Now Waiting For the pipeline.", id="waiting-for-wielkosc-liter"),
        ],
    )
    def test_wait_phrase_in_last_sentence_is_waiting(self, line):
        verdict, reason = _verdict([line])

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    # Warianty zbudowane sztucznie: "Czekam" w nieostatnim zdaniu oraz granica słowa
    # ("Zaczekamy", "Poczekam", "Czekamy", "awaiting for", "waiting forever").
    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("⏺ Czekam na CI. Pipeline zielony, merguję MR.", id="czekam-w-srodku-wypowiedzi"),
            pytest.param("⏺ Zaczekamy na pipeline.", id="zaczekamy"),
            pytest.param("⏺ Poczekam na pipeline.", id="poczekam"),
            pytest.param("⏺ Czekamy na pipeline.", id="czekamy"),
            pytest.param("⏺ Done, not awaiting for anything.", id="awaiting-for"),
            pytest.param("⏺ Stop waiting forever on this.", id="waiting-forever"),
        ],
    )
    def test_phrase_not_ending_the_utterance_or_not_a_whole_word_is_running(self, line):
        assert _verdict([line]) == ("running", _RUNNING_REASON)

    def test_earlier_utterance_with_wait_phrase_is_ignored(self):
        assert _verdict([CZEKAM_LINE, "⏺ Pipeline zielony, merguję MR."]) == ("running", _RUNNING_REASON)

    def test_wait_phrase_in_recap_below_the_utterance_is_ignored(self):
        # W fixture recap pod wypowiedzią zawiera "Czekam na wynik pipeline"; sama wypowiedź jest podmieniona.
        lines = _replace_line(_fixture_lines(WAITING_PHRASE_FIXTURE_NAME), PIPELINE_WAIT_LINE, "⏺ Pipeline MR !158 jest zielony, merguję.")
        assert any("Czekam" in line for line in lines)

        assert _verdict(lines) == ("running", _RUNNING_REASON)

    def test_wait_phrase_cleared_by_user_input_is_running(self):
        lines = _replace_line(_fixture_lines(STALL_FIXTURE_NAME), "\u276f", "\u276f sprawdź status")

        assert _verdict(lines) == ("running", _RUNNING_REASON)

    def test_stop_marker_wins_over_wait_phrase_reason(self):
        verdict, reason = _verdict([CZEKAM_LINE, "SPRINT-RUN STOP: brak toolchain"])

        assert verdict == "waiting"
        assert "SPRINT-RUN STOP" in reason

    # Kazdy znacznik konca wypowiedzi (_ends_utterance) ma wlasne wejscie, na ktorym jest JEDYNYM powodem
    # wyniku: wypowiedz `⏺ Czekam.` i zaraz po niej dokladnie jedna linia chrome z fixture'a, bez recapu.
    # Bez tego znacznika linia chrome wchodzi do sklejonej wypowiedzi, ostatnie zdanie przestaje byc
    # "Czekam" i wynik to `running`. Pusty prompt jest pokryty w _ends_utterance dwa razy (krotka
    # "prompt" i linia zmienna), wiec usuniecie tylko jednego z nich nie zmienia wyniku - przypadek
    # sprawdza zachowanie, nie rozroznia tych dwoch.
    @pytest.mark.parametrize(
        ("fixture", "prefix", "nth"),
        [
            pytest.param(STALL_FIXTURE_NAME, "※ recap", 0, id="recap"),
            pytest.param(STALL_FIXTURE_NAME, "─", 0, id="ramka-z-nazwa-sesji"),
            pytest.param(STALL_FIXTURE_NAME, "─", 1, id="ramka-zwykla"),
            pytest.param(STALL_FIXTURE_NAME, "⏵⏵", 0, id="linia-trybu"),
            pytest.param(STALL_FIXTURE_NAME, "MON-244 |", 0, id="linia-statusu"),
            pytest.param(STALL_FIXTURE_NAME, "\u276f", 0, id="pusty-prompt"),
            pytest.param(WAITING_PHRASE_FIXTURE_NAME, "[-]", 0, id="nawias-minus"),
            pytest.param(WAITING_PHRASE_FIXTURE_NAME, "✦", 0, id="podpowiedz-tui"),
            pytest.param(WAITING_PHRASE_FIXTURE_NAME, "3% until", 0, id="auto-compact"),
            pytest.param(WAITING_PHRASE_FIXTURE_NAME, "limit tyg:", 0, id="urwany-fragment-statusu"),
        ],
    )
    def test_each_utterance_end_marker_alone_ends_the_utterance(self, fixture, prefix, nth):
        chrome_lines = [line for line in _fixture_lines(fixture) if line.startswith(prefix)]
        assert len(chrome_lines) > nth, f"{fixture}: brak linii zaczynajacej sie od {prefix!r}"

        verdict, reason = _verdict([CZEKAM_LINE, chrome_lines[nth]])

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    def test_stall_fixture_without_recap_is_ended_by_the_frame(self):
        lines = _fixture_lines(STALL_FIXTURE_NAME)
        recap = lines.index(next(line for line in lines if line.startswith("※ recap")))
        # Odciete dwie linie to realny recap (naglowek + zawinieta kontynuacja), a nastepna linia to ramka.
        assert lines[recap + 1] == RECAP_TAIL_LINE
        assert lines[recap + 2].startswith("─")

        verdict, reason = _verdict([*lines[:recap], *lines[recap + 2 :]])

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    def test_waiting_phrase_fixture_without_recap_is_ended_by_the_bracket_marker(self):
        lines = _fixture_lines(WAITING_PHRASE_FIXTURE_NAME)
        recap = lines.index(next(line for line in lines if line.startswith("※ recap")))
        assert lines[recap + 1] == "krok to merge po zielonym CI."
        assert lines[recap + 2] == "[-]"

        verdict, reason = _verdict([*lines[:recap], *lines[recap + 2 :]])

        assert verdict == "waiting"
        assert reason.startswith("brak postępu")

    # Ogon bez kotwicy `⏺` nie ma "ostatniej wypowiedzi asystenta": zdanie na koncu to moze byc dowolna
    # linia (wyjscie narzedzia), wiec fraza oczekiwania nie daje `waiting`.
    @pytest.mark.parametrize(
        "line",
        [
            pytest.param("Uruchomiłem testy. Czekam na CI.", id="czekam"),
            pytest.param("Uruchomiłem testy, waiting for CI.", id="waiting-for"),
        ],
    )
    def test_wait_phrase_without_anchor_is_running(self, line):
        assert _verdict([line]) == ("running", _RUNNING_REASON)


@pytest.mark.unit
class TestBuildReportSessionFixtures:
    """Te same logi przez plik <id>.log: read_log_tail (render_log) -> classify."""

    @pytest.mark.parametrize(
        ("lines", "verdict", "reason_fragment"),
        [
            pytest.param(_fixture_lines(STALL_FIXTURE_NAME), "waiting", "brak postępu", id="czekam"),
            pytest.param(_fixture_lines(WAITING_PHRASE_FIXTURE_NAME), "waiting", "brak postępu", id="fraza-oczekiwania"),
            pytest.param(_question_state(DECISION_FIXTURE_NAME), "waiting", "numerowane opcje", id="opcje-bez-odpowiedzi"),
            pytest.param(_fixture_lines(DECISION_FIXTURE_NAME), "running", "Sesja w tle dziala", id="opcje-z-odpowiedzia"),
            pytest.param(_question_state(STOP_ANSWERED_FIXTURE_NAME), "waiting", "SPRINT-RUN STOP", id="stop-bez-odpowiedzi"),
            pytest.param(_fixture_lines(STOP_ANSWERED_FIXTURE_NAME), "running", "Sesja w tle dziala", id="stop-z-odpowiedzia"),
            pytest.param(_fixture_lines(TIP_FIXTURE_NAME), "running", "Sesja w tle dziala", id="podpowiedz-tui"),
        ],
    )
    def test_log_file_is_classified(self, tmp_path, lines, verdict, reason_fragment):
        row = _Ticker(tmp_path, with_state_dir=False).run(lines)

        assert row["verdict"] == verdict
        assert reason_fragment in row["reason"]


@pytest.mark.unit
class TestStallDetector:
    @pytest.mark.parametrize("stall_ticks", [1, 2, 3, 4])
    def test_verdict_flips_to_waiting_exactly_at_the_threshold(self, tmp_path, stall_ticks):
        ticker = _Ticker(tmp_path, stall_ticks=stall_ticks)
        lines = _stall_lines()

        for _ in range(stall_ticks):
            row = ticker.run(lines)
            assert row["verdict"] == "running"
            assert row["reason"] == _RUNNING_REASON
        row = ticker.run(lines)

        assert row["verdict"] == "waiting"
        assert row["reason"] == f"brak postępu od {stall_ticks} ticków"
        assert ticker.stall_data()[SESSION_ID]["ticks"] == stall_ticks

    def test_default_threshold_is_two_ticks_when_stall_ticks_is_omitted(self, tmp_path):
        ticker = _Ticker(tmp_path)
        lines = _stall_lines()

        rows = [ticker.run(lines) for _ in range(3)]

        assert [row["verdict"] for row in rows] == ["running", "running", "waiting"]
        assert rows[2]["reason"] == "brak postępu od 2 ticków"

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            pytest.param("⏺ Sprawdzam wynik pelnego przebiegu.", "⏺ Sprawdzam wynik testow.", id="tresc-odpowiedzi"),
            pytest.param(RECAP_TAIL_LINE, "pełny przebieg i review krytyka. (disable recaps in /config)", id="recap"),
        ],
    )
    def test_changed_tail_resets_the_counter(self, tmp_path, old, new):
        ticker = _Ticker(tmp_path, stall_ticks=2)
        before = _stall_lines()
        after = _replace_line(before, old, new)

        ticker.run(before)
        ticker.run(before)
        hash_before = ticker.stall_data()[SESSION_ID]["hash"]
        assert ticker.stall_data()[SESSION_ID]["ticks"] == 1

        assert ticker.run(after)["verdict"] == "running"
        entry = ticker.stall_data()[SESSION_ID]
        assert entry["ticks"] == 0
        assert entry["hash"] != hash_before
        assert ticker.run(after)["verdict"] == "running"
        assert ticker.run(after)["verdict"] == "waiting"

    def test_returning_to_an_earlier_tail_does_not_restore_its_counter(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=2)
        first = _stall_lines()
        second = _replace_line(first, "⏺ Sprawdzam wynik pelnego przebiegu.", "⏺ Sprawdzam wynik testow.")

        ticker.run(first)
        ticker.run(first)
        ticker.run(second)
        row = ticker.run(first)

        assert row["verdict"] == "running"
        assert ticker.stall_data()[SESSION_ID]["ticks"] == 0

    def test_new_session_starts_from_zero(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=1)
        lines = _stall_lines()
        ticker.run(lines)
        assert ticker.run(lines)["verdict"] == "waiting"

        row = ticker.run(lines, session_id="b2222222")

        assert row["verdict"] == "running"
        assert ticker.stall_data()["b2222222"]["ticks"] == 0

    @pytest.mark.parametrize(
        ("old", "new"),
        [
            pytest.param(STATUS_LINE, STATUS_LINE.replace("ctx:34%", "ctx:41%"), id="status-ctx"),
            pytest.param(STATUS_LINE, STATUS_LINE.replace("limit sesji:9%", "limit sesji:12%"), id="status-limit-sesji"),
            pytest.param(STATUS_LINE, STATUS_LINE.replace("limit tyg:30%", "limit tyg:33%"), id="status-limit-tygodnia"),
            pytest.param(AUTO_COMPACT_LINE, "4% until auto-compact", id="until-auto-compact"),
            pytest.param(LIMIT_FRAGMENT_LINE, "limit tyg:32%", id="urwany-fragment-statusu"),
            pytest.param(MODE_LINE, MODE_LINE.replace("6 agents", "7 agents"), id="linia-trybu"),
        ],
    )
    def test_volatile_tui_line_does_not_reset_the_counter(self, tmp_path, old, new):
        ticker = _Ticker(tmp_path, stall_ticks=2)
        base = _volatile_lines()
        variant = _replace_line(base, old, new)

        ticker.run(base)
        ticker.run(variant)
        row = ticker.run(base)

        assert row["verdict"] == "waiting"
        assert row["reason"] == "brak postępu od 2 ticków"


@pytest.mark.unit
class TestStallStateFile:
    def test_file_lives_in_state_dir_with_hash_and_ticks_per_session(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=3)
        lines = _stall_lines()

        ticker.run(lines)
        first = ticker.stall_data()
        ticker.run(lines)
        second = ticker.stall_data()

        assert set(first) == {SESSION_ID}
        assert set(first[SESSION_ID]) == {"hash", "ticks"}
        assert re.fullmatch(r"[0-9a-f]{64}", first[SESSION_ID]["hash"])
        assert type(first[SESSION_ID]["ticks"]) is int
        assert first[SESSION_ID]["ticks"] == 0
        assert second[SESSION_ID] == {"hash": first[SESSION_ID]["hash"], "ticks": 1}
        assert not (ticker.logs_dir / "stall.json").exists()

    def test_file_keeps_only_sessions_from_the_current_run(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        for session_id in ("a1111111", "b2222222"):
            (logs_dir / f"{session_id}.log").write_text("\n".join(_stall_lines()) + "\n", encoding="utf-8")
        tickets = [
            {"key": "MON-1", "status": "in_progress", "assignee": None},
            {"key": "MON-2", "status": "in_progress", "assignee": None},
        ]
        agents = [
            bg_agent("monolynx-MON-1", session_id="uuid-1", agent_id="a1111111"),
            bg_agent("monolynx-MON-2", session_id="uuid-2", agent_id="b2222222"),
        ]

        state.build_report("monolynx", tickets, agents, logs_dir, state_dir=state_dir)
        both = json.loads((state_dir / "stall.json").read_text(encoding="utf-8"))
        state.build_report("monolynx", tickets, agents[:1], logs_dir, state_dir=state_dir)
        remaining = json.loads((state_dir / "stall.json").read_text(encoding="utf-8"))

        assert set(both) == {"a1111111", "b2222222"}
        assert set(remaining) == {"a1111111"}

    def test_without_state_dir_nothing_is_written_and_verdict_never_flips(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=1, with_state_dir=False)
        lines = _stall_lines()

        rows = [ticker.run(lines) for _ in range(4)]

        assert [row["verdict"] for row in rows] == ["running"] * 4
        assert list(tmp_path.rglob("stall.json")) == []

    def test_build_report_called_without_state_dir_keeps_the_old_behaviour(self, tmp_path):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir()
        (logs_dir / f"{SESSION_ID}.log").write_text("\n".join(_stall_lines()) + "\n", encoding="utf-8")
        tickets = [{"key": "MON-1", "status": "in_progress", "assignee": None}]
        agents = [bg_agent("monolynx-MON-1", agent_id=SESSION_ID)]

        verdicts = [state.build_report("monolynx", tickets, agents, logs_dir)["tickets"][0]["verdict"] for _ in range(4)]

        assert verdicts == ["running"] * 4
        assert list(tmp_path.rglob("stall.json")) == []

    @pytest.mark.parametrize("log_lines", [None, []], ids=["brak-logu", "pusty-log"])
    def test_session_without_log_tail_has_no_entry_and_stays_running(self, tmp_path, log_lines):
        ticker = _Ticker(tmp_path, stall_ticks=1)

        rows = [ticker.run(log_lines) for _ in range(3)]

        assert [row["verdict"] for row in rows] == ["running"] * 3
        assert ticker.stall_data() == {}

    def test_exited_session_keeps_its_verdict(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=1)
        lines = _stall_lines()

        rows = [ticker.run(lines, agent_state="exited", ticket_status="in_review") for _ in range(3)]

        assert [row["verdict"] for row in rows] == ["done"] * 3

    def test_existing_waiting_verdict_keeps_its_own_reason(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=1)
        lines = _question_state(STOP_ANSWERED_FIXTURE_NAME)

        for _ in range(3):
            row = ticker.run(lines)
            assert row["verdict"] == "waiting"
            assert "SPRINT-RUN STOP" in row["reason"]
            assert "brak postępu od" not in row["reason"]

    def test_stall_reason_keeps_the_unknown_state_note(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=1)
        lines = _stall_lines()

        ticker.run(lines, agent_state="compacting")
        row = ticker.run(lines, agent_state="compacting")

        assert row["verdict"] == "waiting"
        assert row["reason"] == "brak postępu od 1 ticków; nieznany stan 'compacting', traktuje jako zywy"

    def test_unwritable_state_file_does_not_break_the_report(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=1)
        ticker.stall_path().mkdir()
        lines = _stall_lines()

        rows = [ticker.run(lines) for _ in range(3)]

        assert [row["verdict"] for row in rows] == ["running"] * 3

    @pytest.mark.parametrize("raw", ["{niepoprawny json", "[]", "null", '"tekst"', "42", ""], ids=lambda raw: raw or "pusty")
    def test_corrupt_state_file_is_treated_as_missing(self, tmp_path, raw):
        ticker = _Ticker(tmp_path, stall_ticks=1)
        ticker.stall_path().write_text(raw, encoding="utf-8")

        row = ticker.run(_stall_lines())

        assert row["verdict"] == "running"
        assert ticker.stall_data()[SESSION_ID]["ticks"] == 0

    # Hash we wpisie to prawdziwy hash ogona, wiec poprawny wpis dalby `waiting` (stall_ticks=1); zly wpis
    # ma byc potraktowany jak brak: `running` i licznik od zera (-5, 7.0 i True przeszlyby jako liczby).
    @pytest.mark.parametrize(
        "make_entry",
        [
            pytest.param(lambda digest: {"hash": digest, "ticks": True}, id="ticks-bool"),
            pytest.param(lambda digest: {"hash": digest, "ticks": -5}, id="ticks-ujemne"),
            pytest.param(lambda digest: {"hash": digest, "ticks": 7.0}, id="ticks-float"),
            pytest.param(lambda digest: {"hash": digest, "ticks": "5"}, id="ticks-napis"),
            pytest.param(lambda digest: {"hash": digest, "ticks": None}, id="ticks-null"),
            pytest.param(lambda digest: {"hash": digest}, id="brak-ticks"),
            pytest.param(lambda digest: {"hash": 5, "ticks": 3}, id="hash-liczba"),
            pytest.param(lambda digest: {"hash": None, "ticks": 3}, id="hash-null"),
            pytest.param(lambda digest: {"ticks": 3}, id="brak-hash"),
            pytest.param(lambda digest: [], id="wpis-lista"),
            pytest.param(lambda digest: "x", id="wpis-napis"),
            pytest.param(lambda digest: None, id="wpis-null"),
        ],
    )
    def test_malformed_entry_is_treated_as_missing(self, tmp_path, make_entry):
        ticker = _Ticker(tmp_path, stall_ticks=1)
        lines = _stall_lines()
        ticker.run(lines)
        digest = ticker.stall_data()[SESSION_ID]["hash"]
        ticker.stall_path().write_text(json.dumps({SESSION_ID: make_entry(digest)}), encoding="utf-8")

        row = ticker.run(lines)

        assert row["verdict"] == "running"
        assert ticker.stall_data()[SESSION_ID] == {"hash": digest, "ticks": 0}

    def test_valid_stored_entry_is_continued(self, tmp_path):
        ticker = _Ticker(tmp_path, stall_ticks=5)
        lines = _stall_lines()
        ticker.run(lines)
        digest = ticker.stall_data()[SESSION_ID]["hash"]
        ticker.stall_path().write_text(json.dumps({SESSION_ID: {"hash": digest, "ticks": 4}}), encoding="utf-8")

        row = ticker.run(lines)

        assert row["verdict"] == "waiting"
        assert row["reason"] == "brak postępu od 5 ticków"


@pytest.mark.unit
class TestMainStall:
    STALL_ENV = "MONOLYNX_SPRINT_STALL_TICKS"

    def _tick(self, tmp_path, capsys, extra_args):
        logs_dir = tmp_path / "logs"
        logs_dir.mkdir(exist_ok=True)
        (logs_dir / f"{SESSION_ID}.log").write_text("pracuję nad testami\n", encoding="utf-8")
        agents_file = tmp_path / "agents.json"
        agents_file.write_text(json.dumps([bg_agent("monolynx-MON-1", session_id="uuid-1", agent_id=SESSION_ID)]), encoding="utf-8")
        tickets_file = tmp_path / "tickets.json"
        tickets_file.write_text(json.dumps([{"key": "MON-1", "status": "in_progress", "assignee": None}]), encoding="utf-8")
        argv = [
            "--slug",
            "monolynx",
            "--agents-file",
            str(agents_file),
            "--tickets-file",
            str(tickets_file),
            "--logs-dir",
            str(logs_dir),
            *extra_args,
        ]

        assert state.main(argv) == 0
        return json.loads(capsys.readouterr().out)["tickets"][0]

    def test_state_dir_option_writes_stall_file(self, tmp_path, capsys, monkeypatch):
        monkeypatch.delenv(self.STALL_ENV, raising=False)
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        self._tick(tmp_path, capsys, ["--state-dir", str(state_dir)])

        data = json.loads((state_dir / "stall.json").read_text(encoding="utf-8"))
        assert set(data) == {SESSION_ID}

    def test_without_state_dir_option_no_file_is_written_and_verdict_stays_running(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setenv(self.STALL_ENV, "1")
        monkeypatch.chdir(tmp_path)

        rows = [self._tick(tmp_path, capsys, []) for _ in range(3)]

        assert [row["verdict"] for row in rows] == ["running"] * 3
        assert list(tmp_path.rglob("stall.json")) == []

    @pytest.mark.parametrize(
        ("raw", "threshold"),
        [
            pytest.param("1", 1, id="1"),
            pytest.param("3", 3, id="3"),
            pytest.param(None, 2, id="brak-zmiennej"),
            pytest.param("abc", 2, id="napis"),
            pytest.param("0", 2, id="zero"),
        ],
    )
    def test_threshold_comes_from_environment(self, tmp_path, capsys, monkeypatch, raw, threshold):
        if raw is None:
            monkeypatch.delenv(self.STALL_ENV, raising=False)
        else:
            monkeypatch.setenv(self.STALL_ENV, raw)
        state_dir = tmp_path / "state"
        state_dir.mkdir()

        for _ in range(threshold):
            assert self._tick(tmp_path, capsys, ["--state-dir", str(state_dir)])["verdict"] == "running"
        row = self._tick(tmp_path, capsys, ["--state-dir", str(state_dir)])

        assert row["verdict"] == "waiting"
        assert row["reason"] == f"brak postępu od {threshold} ticków"
