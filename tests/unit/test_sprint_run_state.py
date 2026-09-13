"""Testy klasyfikatora stanu sprintu: plugin/scripts/sprint_run_state.py.

TDD: plik sprint_run_state.py jeszcze NIE ISTNIEJE w chwili pisania tych testow.
Te testy SA specyfikacja jego kontraktu dla backend-developera (MON-167):

    match_session(agents, slug, key) -> dict | None
    session_state(session) -> "running" | "exited"
    read_log_tail(logs_dir, session_id, n=5) -> list[str]
    classify(ticket_status, session_state, log_tail) -> tuple[str, str]
    build_report(slug, tickets, agents, logs_dir) -> dict
    summarize(rows) -> dict
    main(argv: list[str]) -> int

Dopoki modul nie istnieje, cala klasa testow jest skipowana (pytest.skip na
poziomie modulu) -- czerwone/skipniete do czasu implementacji, potem zielone.

Kontrakt CLI dla main() (zamrozony, patrz brief Team Managera dla MON-167):
    --slug S --agents-file A --tickets-file T [--logs-dir D]
    --agents-file - czyta stdin.
Exit: 0 OK; 2 blad wejscia (niepoprawny JSON, brak pliku, tickets nie-lista);
64 blad uzycia (brak wymaganych argumentow) -- main() ZWRACA 64, nie rzuca
SystemExit (argparse domyslnie rzucalby, wiec implementacja MUSI to przechwycic).

Ksztalt agents-file: sesje tla maja klucze
{"id","cwd","kind":"background","startedAt","sessionId","name","state"};
sesje interaktywne maja {"pid","cwd","kind":"interactive","startedAt",
"sessionId","name","status","waitingFor"}. "id" tylko w background,
"state" tylko background, "status" tylko interactive.
"""

import importlib.util
import io
import json
import sys
from pathlib import Path

import pytest

STATE_PATH = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "sprint_run_state.py"

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

    def test_different_slug_does_not_match(self):
        agents = [bg_agent("inny-projekt-MON-1")]
        result = state.match_session(agents, "monolynx", "MON-1")
        assert result is None


# ---------------------------------------------------------------------------
# session_state
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSessionState:
    @pytest.mark.parametrize("live_state", ["running", "working", "blocked", "waiting", "busy", "idle", "starting"])
    def test_live_whitelist_states_are_running(self, live_state):
        session = bg_agent("x", agent_state=live_state)
        assert state.session_state(session) == "running"

    @pytest.mark.parametrize("dead_state", ["exited", "completed", "failed", "stopped", "killed", "unknown"])
    def test_dead_states_are_exited(self, dead_state):
        session = bg_agent("x", agent_state=dead_state)
        assert state.session_state(session) == "exited"

    def test_missing_state_field_is_exited(self):
        session = bg_agent("x")
        del session["state"]
        assert state.session_state(session) == "exited"

    def test_interactive_session_uses_status_field_defensively(self):
        """Defensywnie: state or status -- sesja interaktywna z 'idle' liczy sie jako running."""
        session = interactive_agent("x", status="idle")
        assert state.session_state(session) == "running"

    def test_interactive_session_dead_status_is_exited(self):
        session = interactive_agent("x", status="exited")
        assert state.session_state(session) == "exited"


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

    def test_empty_lines_are_preserved_in_tail(self, tmp_path):
        log_file = tmp_path / "sid-3.log"
        log_file.write_text("a\n\nb\n\n\n")
        tail = state.read_log_tail(tmp_path, "sid-3", n=5)
        assert tail[-1] == ""


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
