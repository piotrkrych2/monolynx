"""Testy klasyfikatora stanu sprintu: plugin/scripts/sprint_run_state.py.

TDD: plik sprint_run_state.py jeszcze NIE ISTNIEJE w chwili pisania tych testow.
Te testy SA specyfikacja jego kontraktu dla backend-developera (MON-167):

    match_session(agents, slug, key) -> dict | None
    session_state(session) -> "running" | "exited"
    session_state_reason(session) -> str | None
    read_log_tail(logs_dir, session_id, n=5) -> list[str]
    classify(ticket_status, session_state, log_tail) -> tuple[str, str]
    build_report(slug, tickets, agents, logs_dir) -> dict
    summarize(rows) -> dict
    main(argv: list[str]) -> int

MON-172 (pierwszy realny przebieg dyspozytora) zmienia kontrakt session_state: zamiast
bialej listy stanow zywych (kazdy nieznany literal -> "exited", co mapowalo pracujaca
sesje "working" na martwa i powodowalo auto-wznowienie/duplikat), lista MARTWYCH stanow
jest teraz zamknieta (_DEAD_SESSION_STATES) i kazdy inny literal - w tym nieznany z
przyszlej wersji `claude` - jest traktowany jako zywy (wariant odwracalny, patrz
.claude/rules/external-tool-output.md pkt 3). Nowa funkcja session_state_reason zwraca
czytelne ostrzezenie dla literalu spoza znanej listy zywych/martwych, dopisywane do
"reason" wiersza w build_report. Zbiory literalow sa wyprowadzone z realnego zrzutu
`claude agents --json --all` w tests/fixtures/claude/agents-json-all.2.1.270.json.

Dopoki modul nie istnieje, cala klasa testow jest skipowana (pytest.skip na
poziomie modulu) -- czerwone/skipniete do czasu implementacji, potem zielone.

Kontrakt CLI dla main() (zamrozony, patrz brief Team Managera dla MON-167):
    --slug S --agents-file A --tickets-file T [--logs-dir D]
    --agents-file - czyta stdin.
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

if not STATE_PATH.exists():
    pytest.skip("sprint_run_state.py jeszcze nie zaimplementowany (TDD)", allow_module_level=True)

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

    def test_carriage_return_is_treated_as_line_separator(self, tmp_path):
        """Render TUI nadpisuje linie przez \\r zamiast dopisywac \\n -- patrz
        struktura realnego pliku tests/fixtures/claude/logs-tui.2.1.270.txt."""
        log_file = tmp_path / "sid-cr.log"
        log_file.write_text("linia1\rlinia2\rlinia3")
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


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------


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
