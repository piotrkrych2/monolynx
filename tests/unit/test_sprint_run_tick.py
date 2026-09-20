r"""Testy skryptu plugin/scripts/sprint_run_tick.py (MON-190): preflight i state.

Skrypt jest wywolywany jako podproces, nigdy importowany -- to odzwierciedla
realny kontrakt: tick w `claude -p` ma tylko regule Bash(python3:*), wiec
kazde wywolanie z jego wnetrza (w tym samego siebie) musi przejsc przez
subprocess. Kazdy test tutaj robi to samo: `python3 <sciezka> <podkomenda> ...`
przez subprocess.run, sprawdzajac kod wyjscia i stdout/stderr tak, jak zobaczylby
je proces `claude -p`.

Fake `git` i `claude` w PATH (fakebin), na wzor tests/unit/test_sprint_run_sh.py i
tests/unit/test_sprint_run_state.py -- preflight i state woalja oba narzedzia
przez subprocess, wiec testy musza je podstawic, zeby dzialac bez prawdziwego
`claude` w kontenerze CI. Srodowisko budowane od zera w kazdym wywolaniu (nie
dziedziczy MONOLYNX_*/OPENAI_API_KEY/... z procesu testow) -- preflight ma
czytac dokladnie to, co widzi proces `claude -p`, nie to, co przypadkiem jest w
powloce uruchamiajacej pytest; to samo zalozenie sprawdza test bezpieczenstwa,
ktory upewnia sie, ze sekrety z env i z plikow settings nigdy nie trafiaja na
stdout/stderr preflightu.

Sesja/ticket uzyte w testach `state`: slug "fixture", klucz "probe" -- pasuje do
sesji "fixture-probe" (id 9cf45f1d, state=done) w tests/fixtures/claude/
agents-json-all.2.1.270.json (patrz tez test_sprint_run_state.py::TestAgentsFixture::
test_match_session_finds_probe_by_name, ktory dokumentuje to samo dopasowanie).
Realne `claude agents --json --all` zwraca gola liste agentow -- plik fixture ma
dodatkowy wrapper {"_meta": ..., "agents": [...]} tylko po to, zeby udokumentowac
komende/wersje/date przechwycenia (patrz external-tool-output.md), wiec testy
odpakowuja "agents" przed uzyciem jako wyjscie fake `claude`
(write_agents_list_fixture); surowy plik fixture (obiekt, nie lista) sluzy z
kolei do symulowania zepsutego/niespodziewanego ksztaltu wyjscia w
test_claude_agents_returns_json_object_instead_of_list_exits_2.

`claude logs` renderuje TUI (kolory, ruch kursora, `\r` zamiast `\n` miedzy
przerysowaniami zamiast normalnych linii) -- asercja w
test_writes_tick_files_and_returns_report_with_clean_log ("zapisany log nie
zawiera `\x1b` ani `\r`") sprawdza, ze sprint_run_tick.py zdejmuje to PRZED
zapisem na dysk, analogicznie do juz istniejacego
sprint_run_state.read_log_tail; fixture logs-tui.2.1.270.txt to realny zrzut
takiego renderu (patrz tests/fixtures/claude/logs-tui.2.1.270.txt).
"""

import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "sprint_run_tick.py"
FIXTURES_DIR = Path(__file__).resolve().parents[2] / "tests" / "fixtures" / "claude"

GIT_SCRIPT_TEMPLATE = """#!/usr/bin/env bash
case "$*" in
  "-C {project_dir} rev-parse --show-toplevel"|"rev-parse --show-toplevel")
    echo "{project_dir}" ;;
  "-C {project_dir} rev-parse --absolute-git-dir"|"rev-parse --absolute-git-dir")
    echo "{git_dir}" ;;
  *)
    echo "git-stub: nieobslugiwane argumenty: $*" >&2
    exit 128 ;;
esac
"""

# Fake `claude`: obsluguje --version, agents --json --all i logs <id>, wszystko
# sterowalne zmiennymi srodowiskowymi FAKE_CLAUDE_* przekazywanymi per-test.
CLAUDE_SCRIPT = """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  exit_code="${FAKE_CLAUDE_VERSION_EXIT:-0}"
  if [ "$exit_code" != "0" ]; then
    echo "claude-stub: --version zakonczone bledem (symulacja testu)" >&2
    exit "$exit_code"
  fi
  echo "${FAKE_CLAUDE_VERSION_OUTPUT:-2.1.270 (Claude Code)}"
  exit 0
fi
if [ "${1:-}" = "agents" ] && [ "${2:-}" = "--json" ] && [ "${3:-}" = "--all" ]; then
  exit_code="${FAKE_CLAUDE_AGENTS_EXIT:-0}"
  if [ -n "${FAKE_CLAUDE_AGENTS_FILE:-}" ]; then
    cat "$FAKE_CLAUDE_AGENTS_FILE"
  else
    echo "[]"
  fi
  exit "$exit_code"
fi
if [ "${1:-}" = "logs" ]; then
  id="${2:-}"
  if [ -n "${FAKE_CLAUDE_LOGS_CALL_LOG:-}" ]; then
    printf '%s\\n' "$id" >> "$FAKE_CLAUDE_LOGS_CALL_LOG"
  fi
  safe_id="$(printf '%s' "$id" | tr -cd '[:alnum:]_')"
  var="FAKE_CLAUDE_LOGS_${safe_id}"
  exit_var="FAKE_CLAUDE_LOGS_EXIT_${safe_id}"
  file="${!var:-}"
  exit_code="${!exit_var:-0}"
  if [ -n "$file" ]; then
    cat "$file"
  else
    echo "claude-stub: brak zarejestrowanego logu dla id '$id'" >&2
  fi
  exit "$exit_code"
fi
echo "claude-stub: nieobslugiwane argumenty: $*" >&2
exit 127
"""


@pytest.fixture
def tick_sandbox(tmp_path):
    """Tymczasowy projekt git (symulowany) + fakebin z `git` i `claude`."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    git_dir = project_dir / ".git"
    git_dir.mkdir()

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()

    git_script = fakebin / "git"
    git_script.write_text(GIT_SCRIPT_TEMPLATE.format(project_dir=project_dir, git_dir=git_dir))
    git_script.chmod(0o755)

    claude_script = fakebin / "claude"
    claude_script.write_text(CLAUDE_SCRIPT)
    claude_script.chmod(0o755)

    return SimpleNamespace(
        tmp_path=tmp_path,
        project_dir=project_dir,
        git_dir=git_dir,
        fakebin=fakebin,
    )


def _base_env(sandbox, extra=None):
    """Srodowisko budowane od zera -- nigdy nie dziedziczy MONOLYNX_*/sekretow z pytest."""
    env = {"PATH": f"{sandbox.fakebin}:/usr/local/bin:/usr/bin:/bin"}
    if extra:
        env.update(extra)
    return env


def full_flags_env(extra=None):
    """Komplet flag wymaganych przez preflight -- kazdy test bierze kopie i modyfikuje."""
    env = {
        "MONOLYNX_PROJECT_SLUG": "testslug",
        "MONOLYNX_AUTOTEST": "true",
        "MONOLYNX_AUTOCOMMIT": "true",
        "MONOLYNX_AUTOPUSH": "true",
        "MONOLYNX_AUTOMR": "true",
    }
    if extra:
        env.update(extra)
    return env


def run_preflight(sandbox, project_dir=None, env_extra=None, timeout=30):
    project_dir = project_dir or sandbox.project_dir
    env = _base_env(sandbox, env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "preflight", "--project-dir", str(project_dir)],
        cwd=str(sandbox.project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_state(sandbox, slug, tickets=None, tickets_json=None, tick_dir=None, env_extra=None, timeout=30):
    env = _base_env(sandbox, env_extra)
    payload = tickets_json if tickets_json is not None else json.dumps(tickets if tickets is not None else [])
    args = [sys.executable, str(SCRIPT), "state", "--slug", slug, "--tickets-json", payload]
    if tick_dir is not None:
        args += ["--tick-dir", str(tick_dir)]
    return subprocess.run(
        args,
        cwd=str(sandbox.project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def error_lines(result):
    return [line for line in result.stdout.splitlines() if line.startswith("ERROR:")]


def warn_lines(result):
    return [line for line in result.stdout.splitlines() if line.startswith("WARN:")]


def _assert_permission_mode_fix_hint(line):
    """Kontrakt MON-195 (nie doslowne brzmienie zdania - formulacja moze sie
    zmienic bez psucia testu): ERROR wskazuje nazwe zmiennej z sugerowana
    wartoscia `auto` oraz wzmianke o regule `autoMode.allow`."""
    assert "MONOLYNX_SPRINT_PERMISSION_MODE" in line
    assert "PERMISSION_MODE=auto" in line
    assert "autoMode.allow" in line


def write_agents_list_fixture(tmp_path, fixture_path):
    """Rozpakuj klucz "agents" z pelnego zrzutu fixture do osobnego pliku.

    Realne `claude agents --json --all` zwraca gola liste agentow, NIE obiekt
    {"_meta": ..., "agents": [...]} -- ten wrapper istnieje tylko w pliku fixture,
    zeby udokumentowac komende/wersje/date przechwycenia (patrz tez
    test_sprint_run_state.py, ktory robi dokladnie to samo rozpakowanie przed
    uzyciem fixture w testach). Podanie calego pliku fixture wprost jako
    FAKE_CLAUDE_AGENTS_FILE symulowaloby zamiast tego zepsuty ksztalt (obiekt
    zamiast listy) -- patrz test_claude_agents_returns_json_object_instead_of_list_exits_2.
    """
    data = json.loads(fixture_path.read_text())
    out_path = tmp_path / "agents-list.json"
    out_path.write_text(json.dumps(data["agents"]))
    return out_path


@pytest.mark.unit
class TestPreflight:
    def test_full_environment_exits_zero_and_reports_all_keys(self, tick_sandbox):
        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        lines = result.stdout.splitlines()
        assert lines, "preflight nie wypisal nic na stdout"
        assert lines[-1] == "PREFLIGHT: ok"
        assert not error_lines(result)

        by_key = dict(line.split("=", 1) for line in lines if "=" in line and not line.startswith(("WARN:", "ERROR:")))
        assert by_key["SLUG"] == "testslug"
        assert by_key["MODE"] == "interactive"
        assert by_key["AUTOTEST"] == "true"
        assert by_key["AUTOCOMMIT"] == "true"
        assert by_key["AUTOPUSH"] == "true"
        assert by_key["AUTOMR"] == "true"
        assert "2.1.270" in by_key["CLAUDE_VERSION"]
        assert by_key["PROJECT_DIR"] == str(tick_sandbox.project_dir)

        tick_dir = Path(by_key["TICK_DIR"])
        assert tick_dir == tick_sandbox.git_dir / "monolynx" / "tick"
        assert tick_dir.is_dir()

    def test_invalid_json_in_settings_json_reports_error_and_exit_2(self, tick_sandbox):
        claude_dir = tick_sandbox.project_dir / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{niepoprawny json")

        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 2
        lines = result.stdout.splitlines()
        assert lines[-1].startswith("PREFLIGHT: failed problems=")
        errs = error_lines(result)
        assert errs
        assert any(".claude/settings.json" in line for line in errs)

    def test_invalid_json_in_settings_local_json_reports_error_and_exit_2(self, tick_sandbox):
        claude_dir = tick_sandbox.project_dir / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.local.json").write_text("{niepoprawny json")

        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 2
        errs = error_lines(result)
        assert any(".claude/settings.local.json" in line for line in errs)

    def test_permissions_as_list_of_strings_is_rejected_like_18_09_incident(self, tick_sandbox):
        """Dokladnie ten blad co 18.09: `permissions` jako lista napisow zamiast
        obiektu {"allow": [...]}. Claude Code po cichu ignoruje taki plik w trybie
        -p -- preflight ma go wykryc jako problem, zanim tick w ogole wystartuje."""
        claude_dir = tick_sandbox.project_dir / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"permissions": ["Bash(claude:*)", "Read"]}))

        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 2
        errs = error_lines(result)
        assert any("permissions" in line for line in errs)

    @pytest.mark.parametrize("missing_flag", ["MONOLYNX_AUTOTEST", "MONOLYNX_AUTOCOMMIT", "MONOLYNX_AUTOPUSH"])
    def test_missing_required_autoflag_exits_2_and_names_it(self, tick_sandbox, missing_flag):
        env_extra = full_flags_env()
        del env_extra[missing_flag]

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 2
        errs = error_lines(result)
        assert any(missing_flag in line for line in errs)

    def test_missing_project_slug_exits_2(self, tick_sandbox):
        env_extra = full_flags_env()
        del env_extra["MONOLYNX_PROJECT_SLUG"]

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 2
        errs = error_lines(result)
        assert any("MONOLYNX_PROJECT_SLUG" in line for line in errs)

    def test_flag_true_in_settings_file_but_absent_from_process_env_hints_at_file(self, tick_sandbox):
        claude_dir = tick_sandbox.project_dir / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"env": {"MONOLYNX_AUTOTEST": "true"}}))
        env_extra = full_flags_env()
        del env_extra["MONOLYNX_AUTOTEST"]

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 2
        errs = error_lines(result)
        hint = next((line for line in errs if "MONOLYNX_AUTOTEST" in line), None)
        assert hint is not None, f"brak wpisu o MONOLYNX_AUTOTEST w: {errs}"
        assert "settings.json" in hint
        assert "nie widzi" in hint

    def test_missing_automr_gives_warning_not_error_and_exits_zero(self, tick_sandbox):
        env_extra = full_flags_env()
        del env_extra["MONOLYNX_AUTOMR"]

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0
        lines = result.stdout.splitlines()
        assert lines[-1] == "PREFLIGHT: ok"
        warns = warn_lines(result)
        assert any("MONOLYNX_AUTOMR" in line for line in warns)

    def test_mode_is_batch_when_sprint_run_mode_env_is_batch(self, tick_sandbox):
        env_extra = full_flags_env({"MONOLYNX_SPRINT_RUN_MODE": "batch"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0
        assert "MODE=batch" in result.stdout.splitlines()

    def test_mode_is_interactive_when_sprint_run_mode_env_is_absent(self, tick_sandbox):
        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 0
        assert "MODE=interactive" in result.stdout.splitlines()

    def test_permission_mode_defaults_to_auto_and_passes_preflight(self, tick_sandbox):
        """MON-195: domyslny tryb startu sesji zmienia sie z `bypassPermissions`
        na `auto` -- ten pierwszy byl odrzucany w sesji auto domyslna regula
        soft_deny "Create Unsafe Agents", wiec tick przechodzil caly preflight
        i odpadal dopiero w KROKU 4 bez startu zadnej sesji."""
        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        lines = result.stdout.splitlines()
        assert lines[-1] == "PREFLIGHT: ok"
        assert "PERMISSION_MODE=auto" in lines
        assert not error_lines(result)

    def test_permission_mode_bypass_permissions_blocks_preflight_first_and_with_fix_hint(self, tick_sandbox):
        """Rozstrzygniecie Team Managera: nowy check jest PIERWSZYM elementem
        listy `problems`, wiec przy kilku jednoczesnych problemach jego ERROR:
        jest zawsze pierwsza linia ERROR: w wyjsciu (tu razem z brakiem
        MONOLYNX_AUTOTEST, zeby faktycznie wymusic kolejnosc, a nie tylko
        jedyny mozliwy problem w danym przebiegu)."""
        env_extra = full_flags_env({"MONOLYNX_SPRINT_PERMISSION_MODE": "bypassPermissions"})
        del env_extra["MONOLYNX_AUTOTEST"]

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 2
        lines = result.stdout.splitlines()
        assert lines[-1].startswith("PREFLIGHT: failed problems=")
        errs = error_lines(result)
        assert len(errs) >= 2, f"oczekiwano co najmniej 2 problemow (permission mode + brak AUTOTEST): {errs}"
        _assert_permission_mode_fix_hint(errs[0])

    def test_permission_mode_auto_explicit_passes_preflight(self, tick_sandbox):
        """Jawne nadpisanie bezpieczna wartoscia dalej dziala (default nie jest
        wymuszony na sztywno)."""
        env_extra = full_flags_env({"MONOLYNX_SPRINT_PERMISSION_MODE": "auto"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        lines = result.stdout.splitlines()
        assert lines[-1] == "PREFLIGHT: ok"
        assert "PERMISSION_MODE=auto" in lines

    def test_permission_mode_dangerously_skip_permissions_blocks_preflight(self, tick_sandbox):
        """Blokada obejmuje nie tylko literal `bypassPermissions`, ale kazda
        wartosc zawierajaca `dangerously-skip-permissions` (np. flaga CLI
        przekazana wprost jako tryb)."""
        env_extra = full_flags_env({"MONOLYNX_SPRINT_PERMISSION_MODE": "--dangerously-skip-permissions"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 2
        lines = result.stdout.splitlines()
        assert lines[-1].startswith("PREFLIGHT: failed problems=")
        errs = error_lines(result)
        assert errs
        _assert_permission_mode_fix_hint(errs[0])

    def test_permission_mode_override_with_accept_edits_passes_preflight(self, tick_sandbox):
        """Nadpisanie innym legalnym trybem (nie tylko `auto`) przechodzi bez
        zmian -- zmienna nadal nadpisuje default, blokowana jest tylko
        wartosc bypass/dangerously-skip."""
        env_extra = full_flags_env({"MONOLYNX_SPRINT_PERMISSION_MODE": "acceptEdits"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        lines = result.stdout.splitlines()
        assert lines[-1] == "PREFLIGHT: ok"
        assert "PERMISSION_MODE=acceptEdits" in lines

    def test_never_leaks_secret_env_values_in_output(self, tick_sandbox):
        claude_dir = tick_sandbox.project_dir / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text(json.dumps({"env": {"OPENAI_API_KEY": "sekret-w-pliku", "GITLAB_TOKEN": "glpat-w-pliku"}}))
        env_extra = full_flags_env({"OPENAI_API_KEY": "sekret-123", "GITLAB_TOKEN": "glpat-xyz"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        combined = result.stdout + result.stderr
        assert "sekret-123" not in combined
        assert "glpat-xyz" not in combined
        assert "sekret-w-pliku" not in combined
        assert "glpat-w-pliku" not in combined

    def test_claude_version_failure_reports_error_and_exit_2(self, tick_sandbox):
        env_extra = full_flags_env({"FAKE_CLAUDE_VERSION_EXIT": "1"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 2
        errs = error_lines(result)
        assert any("claude" in line.lower() and "version" in line.lower() for line in errs)

    def test_no_subcommand_exits_64(self, tick_sandbox):
        env = _base_env(tick_sandbox, full_flags_env())

        result = subprocess.run(
            [sys.executable, str(SCRIPT)],
            cwd=str(tick_sandbox.project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 64

    def test_unknown_subcommand_exits_64(self, tick_sandbox):
        env = _base_env(tick_sandbox, full_flags_env())

        result = subprocess.run(
            [sys.executable, str(SCRIPT), "frobnicate"],
            cwd=str(tick_sandbox.project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 64


@pytest.mark.unit
class TestState:
    """Testy podkomendy `state`. Slug/klucz dobrane pod fixture agentow (patrz
    docstring modulu): "fixture" + "probe" pasuje do sesji "fixture-probe"
    (id 9cf45f1d) w tests/fixtures/claude/agents-json-all.2.1.270.json."""

    AGENTS_FIXTURE = FIXTURES_DIR / "agents-json-all.2.1.270.json"
    LOGS_TUI_FIXTURE = FIXTURES_DIR / "logs-tui.2.1.270.txt"
    PROBE_SESSION_ID = "9cf45f1d"

    def test_writes_tick_files_and_returns_report_with_clean_log(self, tick_sandbox):
        tick_dir = tick_sandbox.tmp_path / "tick"
        tickets = [{"key": "probe", "status": "in_review", "assignee": None}]
        agents_list_file = write_agents_list_fixture(tick_sandbox.tmp_path, self.AGENTS_FIXTURE)
        env_extra = {
            "FAKE_CLAUDE_AGENTS_FILE": str(agents_list_file),
            f"FAKE_CLAUDE_LOGS_{self.PROBE_SESSION_ID}": str(self.LOGS_TUI_FIXTURE),
        }

        result = run_state(tick_sandbox, "fixture", tickets=tickets, tick_dir=tick_dir, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        payload = json.loads(result.stdout)
        assert "tickets" in payload
        assert "summary" in payload
        assert payload["tickets"][0]["key"] == "probe"

        assert (tick_dir / "tickets.json").is_file()
        assert json.loads((tick_dir / "tickets.json").read_text()) == tickets
        assert (tick_dir / "agents.json").is_file()

        log_file = tick_dir / "logs" / f"{self.PROBE_SESSION_ID}.log"
        assert log_file.is_file(), "sesja fixture-probe powinna dopasowac ticket 'probe' i dostac zapisany log"
        content = log_file.read_text()
        assert "\x1b" not in content, "log ma zawierac zdjete sekwencje ANSI (patrz uwaga w docstringu modulu)"
        assert "\r" not in content, "\\r ma byc zamieniony na \\n przed zapisem"

    def test_invalid_json_in_tickets_json_exits_2(self, tick_sandbox):
        result = run_state(tick_sandbox, "fixture", tickets_json="{niepoprawny json")

        assert result.returncode == 2
        assert result.stderr.strip()

    def test_ticket_element_missing_key_field_exits_2(self, tick_sandbox):
        result = run_state(tick_sandbox, "fixture", tickets=[{"status": "todo"}])

        assert result.returncode == 2
        assert result.stderr.strip()

    def test_claude_agents_nonzero_exit_gives_exit_2(self, tick_sandbox):
        tickets = [{"key": "probe", "status": "todo", "assignee": None}]

        result = run_state(tick_sandbox, "fixture", tickets=tickets, env_extra={"FAKE_CLAUDE_AGENTS_EXIT": "1"})

        assert result.returncode == 2

    def test_claude_agents_returns_json_object_instead_of_list_exits_2(self, tick_sandbox):
        """Realne `claude agents --json --all` zwraca gola liste. AGENTS_FIXTURE
        na dysku ma ksztalt {"_meta": ..., "agents": [...]} (wrapper dokumentacyjny
        fixture, patrz write_agents_list_fixture) -- podane wprost jako wyjscie
        `claude agents` symuluje zepsuty/niespodziewany ksztalt (obiekt zamiast
        listy), ktory state ma potraktowac jako blad wejscia, nie probowac
        iterowac po slowniku jak po liscie."""
        tickets = [{"key": "probe", "status": "todo", "assignee": None}]

        result = run_state(
            tick_sandbox,
            "fixture",
            tickets=tickets,
            env_extra={"FAKE_CLAUDE_AGENTS_FILE": str(self.AGENTS_FIXTURE)},
        )

        assert result.returncode == 2

    @pytest.mark.parametrize("bad_id", [123, None], ids=["id-int", "id-null"])
    def test_agent_entry_with_non_string_id_is_skipped_without_crashing(self, tick_sandbox, bad_id):
        """Wejscie jest granica systemu (.claude/rules/input-boundary-elements.md):
        wpis 'claude agents' ktorego 'id' nie jest napisem (liczba albo null) ma
        byc pominiety, nie uzyty do zbudowania nazwy pliku logu ani do wywolania
        `claude logs <id>`. Nazwa wpisu CELOWO pasuje do ticketu ("fixture-probe"
        dla klucza "probe"), zeby wymusic sciezke dopasowania sesji, a nie
        "brak sesji" -- inaczej test nie sprawdzalby niczego."""
        tick_dir = tick_sandbox.tmp_path / "tick"
        agents = [
            {
                "id": bad_id,
                "cwd": "/repo",
                "kind": "background",
                "startedAt": 1000,
                "sessionId": "sid-broken-id",
                "name": "fixture-probe",
                "state": "running",
            }
        ]
        agents_file = tick_sandbox.tmp_path / "agents-bad-id.json"
        agents_file.write_text(json.dumps(agents))
        call_log = tick_sandbox.tmp_path / "claude_logs_calls.txt"
        tickets = [{"key": "probe", "status": "todo", "assignee": None}]

        result = run_state(
            tick_sandbox,
            "fixture",
            tickets=tickets,
            tick_dir=tick_dir,
            env_extra={
                "FAKE_CLAUDE_AGENTS_FILE": str(agents_file),
                "FAKE_CLAUDE_LOGS_CALL_LOG": str(call_log),
            },
        )

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "Traceback" not in result.stderr

        payload = json.loads(result.stdout)
        assert payload["tickets"][0]["key"] == "probe"

        assert not call_log.exists() or call_log.read_text() == "", (
            f"claude logs nie powinno byc wywolane dla wpisu z nienapisowym id: {call_log.read_text() if call_log.exists() else ''}"
        )
        logs_dir = tick_dir / "logs"
        if logs_dir.is_dir():
            assert not list(logs_dir.iterdir()), "nie powinno powstac zadnego pliku logu dla tego wpisu"

    def test_removes_stale_log_files_before_writing_new_ones(self, tick_sandbox):
        tick_dir = tick_sandbox.tmp_path / "tick"
        logs_dir = tick_dir / "logs"
        logs_dir.mkdir(parents=True)
        old_log = logs_dir / "old.log"
        old_log.write_text("stary log z poprzedniego ticku\n")

        tickets = [{"key": "probe", "status": "todo", "assignee": None}]
        result = run_state(tick_sandbox, "fixture", tickets=tickets, tick_dir=tick_dir)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert not old_log.exists()
