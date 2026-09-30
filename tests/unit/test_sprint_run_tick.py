r"""Testy skryptu plugin/scripts/sprint_run_tick.py (MON-190): preflight, state,
start/stop (MON-185) i sync-base (MON-142, osobny fake `git` z logiem wywolan).

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

MON-197 (`TestBaseBranchResolution`): fake `git` rozszerzony o `symbolic-ref
refs/remotes/origin/HEAD`, `rev-parse --abbrev-ref HEAD`, `fetch origin
<branch>` i `rev-parse origin/<branch>`, sterowalne zmiennymi
FAKE_GIT_ORIGIN_HEAD_BRANCH / FAKE_GIT_CURRENT_BRANCH / FAKE_GIT_FETCH_EXIT /
FAKE_GIT_LOCAL_SHA / FAKE_GIT_REMOTE_SHA -- ta sama konwencja co reszta
fakebina w tym pliku (kazda galaz sterowana osobna zmienna z sensownym
defaultem, zeby testy niezwiazane z brancha bazowego mogly jej nie ustawiac).
`glab` CELOWO nie ma fakebina: obraz Dockera aplikacji go nie instaluje
(sprawdzone w Dockerfile), wiec brak `glab` w PATH testu odzwierciedla realne
srodowisko, w ktorym `_run_glab_default_branch` konczy sie FileNotFoundError,
nie sztucznym stubem.
"""

import json
import os
import subprocess
import sys
import time
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
  "-C {project_dir} symbolic-ref refs/remotes/origin/HEAD"|"symbolic-ref refs/remotes/origin/HEAD")
    if [ -n "${{FAKE_GIT_ORIGIN_HEAD_BRANCH:-}}" ]; then
      echo "refs/remotes/origin/${{FAKE_GIT_ORIGIN_HEAD_BRANCH}}"
    else
      echo "git-stub: symbolic-ref refs/remotes/origin/HEAD nieustawiony (symulacja testu)" >&2
      exit 128
    fi ;;
  "-C {project_dir} rev-parse --abbrev-ref HEAD"|"rev-parse --abbrev-ref HEAD")
    echo "${{FAKE_GIT_CURRENT_BRANCH:-main}}" ;;
  "-C {project_dir} fetch origin "*)
    exit_code="${{FAKE_GIT_FETCH_EXIT:-0}}"
    if [ "$exit_code" != "0" ]; then
      echo "git-stub: fetch nie dziala (symulacja testu)" >&2
    fi
    exit "$exit_code" ;;
  "-C {project_dir} rev-parse HEAD")
    echo "${{FAKE_GIT_LOCAL_SHA:-sha-local}}" ;;
  "-C {project_dir} rev-parse origin/"*)
    echo "${{FAKE_GIT_REMOTE_SHA:-sha-remote}}" ;;
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
class TestBaseBranchResolution:
    """MON-197: preflight ustala BASE_BRANCH (lancuch MONOLYNX_BASE_BRANCH ->
    MONOLYNX_MR_TARGET -> `git symbolic-ref refs/remotes/origin/HEAD` -> `glab
    repo view`) i BASE_BRANCH_STALE (rozjazd GLOWNEGO checkoutu dyspozytora
    wobec tego brancha, nie worktree ticketu). Testy wykonuja realna sciezke
    `_resolve_base_branch` / `_check_base_branch_stale` przez subprocess -
    tekstowa obecnosc kluczy w zrodle (test_base_branch.py) nie dowodzi, ze
    lancuch fallbacku faktycznie dziala."""

    def test_base_branch_from_monolynx_base_branch_env(self, tick_sandbox):
        env_extra = full_flags_env({"MONOLYNX_BASE_BRANCH": "develop"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "BASE_BRANCH=develop" in result.stdout.splitlines()

    def test_different_base_branch_and_mr_target_fail_preflight(self, tick_sandbox):
        """MON-219: jeden branch docelowy dla sprint-run i mr-queue. Obie
        zmienne ustawione i rozne to blad konfiguracji (wczesniej MON-197:
        BASE_BRANCH po cichu wygrywal), bo mr-queue merguje do MR_TARGET, a
        sesje startuja z BASE_BRANCH - rozbieznosc zostawialaby checkout
        dyspozytora za branchem, do ktorego trafiaja merge."""
        env_extra = full_flags_env({"MONOLYNX_BASE_BRANCH": "develop", "MONOLYNX_MR_TARGET": "release"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 2, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        errs = error_lines(result)
        assert len(errs) == 1
        assert "MONOLYNX_BASE_BRANCH" in errs[0]
        assert "MONOLYNX_MR_TARGET" in errs[0]
        assert result.stdout.splitlines()[-1] == "PREFLIGHT: failed problems=1"

    def test_equal_base_branch_and_mr_target_pass_preflight(self, tick_sandbox):
        env_extra = full_flags_env({"MONOLYNX_BASE_BRANCH": "develop", "MONOLYNX_MR_TARGET": "develop"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert error_lines(result) == []
        lines = result.stdout.splitlines()
        assert "BASE_BRANCH=develop" in lines
        assert lines[-1] == "PREFLIGHT: ok"

    def test_base_branch_and_mr_target_differing_only_by_whitespace_pass_preflight(self, tick_sandbox):
        env_extra = full_flags_env({"MONOLYNX_BASE_BRANCH": " develop ", "MONOLYNX_MR_TARGET": "develop"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "BASE_BRANCH=develop" in result.stdout.splitlines()

    def test_only_mr_target_set_passes_preflight_and_becomes_base_branch(self, tick_sandbox):
        env_extra = full_flags_env({"MONOLYNX_MR_TARGET": "release"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert error_lines(result) == []
        assert "BASE_BRANCH=release" in result.stdout.splitlines()

    def test_base_branch_falls_back_to_mr_target_when_unset(self, tick_sandbox):
        env_extra = full_flags_env({"MONOLYNX_MR_TARGET": "release"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "BASE_BRANCH=release" in result.stdout.splitlines()

    def test_base_branch_from_git_symbolic_ref_when_no_env_vars_set(self, tick_sandbox):
        env_extra = full_flags_env({"FAKE_GIT_ORIGIN_HEAD_BRANCH": "trunk"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "BASE_BRANCH=trunk" in result.stdout.splitlines()

    def test_base_branch_empty_when_no_fallback_available_never_guesses_main(self, tick_sandbox):
        """Ani MONOLYNX_BASE_BRANCH, ani MONOLYNX_MR_TARGET nie sa ustawione,
        `git symbolic-ref refs/remotes/origin/HEAD` nie ma czego zwrocic (fake
        git konczy sie bledem), a `glab` nie jest zainstalowany w PATH testu
        (tak jak w obrazie Dockera aplikacji) - wynik ma byc pustym stringiem,
        NIGDY zgadywaniem 'main': repo moze uzywac 'master', 'develop' albo
        czegokolwiek innego jako galezi glownej."""
        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        lines = result.stdout.splitlines()
        assert "BASE_BRANCH=" in lines
        assert "BASE_BRANCH=main" not in lines

    def test_base_branch_stale_true_when_current_branch_differs(self, tick_sandbox):
        env_extra = full_flags_env({"MONOLYNX_BASE_BRANCH": "main", "FAKE_GIT_CURRENT_BRANCH": "feature-123"})

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "BASE_BRANCH_STALE=true" in result.stdout.splitlines()

    def test_base_branch_stale_false_when_same_branch_and_matching_sha(self, tick_sandbox):
        env_extra = full_flags_env(
            {
                "MONOLYNX_BASE_BRANCH": "main",
                "FAKE_GIT_CURRENT_BRANCH": "main",
                "FAKE_GIT_LOCAL_SHA": "deadbeef",
                "FAKE_GIT_REMOTE_SHA": "deadbeef",
            }
        )

        result = run_preflight(tick_sandbox, env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "BASE_BRANCH_STALE=false" in result.stdout.splitlines()


# MON-142: fake `git` dla `sync-base`. Osobny od GIT_SCRIPT_TEMPLATE, bo
# loguje kazde wywolanie do FAKE_GIT_CALLS - testy asertuja nie tylko wynik,
# ale tez to, ze skrypt nigdy nie wola reset/rebase/checkout/pull/switch.
# Obraz Dockera aplikacji nie ma prawdziwego `git` (python:3.12-slim), wiec
# realne repo z bare originem nie zadzialaloby w CI.
SYNC_BASE_GIT_TEMPLATE = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$FAKE_GIT_CALLS"
case "$*" in
  "-C {project_dir} rev-parse --show-toplevel")
    if [ "${{FAKE_GIT_NOT_REPO:-0}}" = "1" ]; then
      echo "fatal: not a git repository" >&2
      exit 128
    fi
    echo "{project_dir}" ;;
  "-C {project_dir} rev-parse --abbrev-ref HEAD")
    echo "${{FAKE_GIT_CURRENT_BRANCH:-main}}" ;;
  "-C {project_dir} status --porcelain --untracked-files=no")
    printf '%s' "${{FAKE_GIT_STATUS:-}}" ;;
  "-C {project_dir} fetch origin "*)
    exit "${{FAKE_GIT_FETCH_EXIT:-0}}" ;;
  "-C {project_dir} rev-parse HEAD")
    echo "${{FAKE_GIT_LOCAL_SHA:-sha-local}}" ;;
  "-C {project_dir} rev-parse origin/"*)
    echo "${{FAKE_GIT_REMOTE_SHA:-sha-remote}}" ;;
  "-C {project_dir} merge-base --is-ancestor HEAD origin/"*)
    exit "${{FAKE_GIT_HEAD_IS_ANCESTOR_EXIT:-0}}" ;;
  "-C {project_dir} merge-base --is-ancestor origin/"*" HEAD")
    exit "${{FAKE_GIT_REMOTE_IS_ANCESTOR_EXIT:-1}}" ;;
  "-C {project_dir} merge --ff-only origin/"*)
    exit "${{FAKE_GIT_MERGE_EXIT:-0}}" ;;
  *)
    echo "git-stub: nieobslugiwane argumenty: $*" >&2
    exit 128 ;;
esac
"""

_FORBIDDEN_GIT_WORDS = ("reset", "rebase", "checkout", "pull", "switch")


@pytest.fixture
def sync_base_sandbox(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    git_script = fakebin / "git"
    git_script.write_text(SYNC_BASE_GIT_TEMPLATE.format(project_dir=project_dir))
    git_script.chmod(0o755)
    return SimpleNamespace(tmp_path=tmp_path, project_dir=project_dir, fakebin=fakebin, calls=tmp_path / "git-calls.log")


def run_sync_base(sandbox, base_branch="main", env_extra=None, extra_args=None):
    env = _base_env(sandbox, {"FAKE_GIT_CALLS": str(sandbox.calls), **(env_extra or {})})
    args = [sys.executable, str(SCRIPT), "sync-base", "--project-dir", str(sandbox.project_dir)]
    if base_branch is not None:
        args += ["--base-branch", base_branch]
    args += extra_args or []
    return subprocess.run(args, cwd=str(sandbox.project_dir), env=env, capture_output=True, text=True, timeout=30)


def git_calls(sandbox):
    if not sandbox.calls.exists():
        return []
    prefix = f"-C {sandbox.project_dir} "
    return [line.removeprefix(prefix) for line in sandbox.calls.read_text().splitlines()]


def assert_no_forbidden_git(calls):
    for call in calls:
        subcommand = call.split()[0]
        assert subcommand not in _FORBIDDEN_GIT_WORDS, f"sync-base wywolal zakazane git {call!r}"


@pytest.mark.unit
class TestSyncBase:
    """MON-142: `sync-base` przewija glowny checkout dyspozytora do
    `origin/<BASE_BRANCH>` wylacznie przez fetch + merge --ff-only, tylko gdy
    checkout stoi na branchu bazowym i sledzone pliki sa czyste. Wyjscie to
    jedna zamrozona linia `SYNC-BASE: ok|skipped|failed reason=<powod>`."""

    def test_fast_forwards_when_on_base_branch_clean_and_behind(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, base_branch="develop", env_extra={"FAKE_GIT_CURRENT_BRANCH": "develop"})

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.splitlines() == ["SYNC-BASE: ok reason=fast-forwarded"]
        calls = git_calls(sync_base_sandbox)
        assert "fetch origin develop" in calls
        assert "merge --ff-only origin/develop" in calls
        assert calls.index("fetch origin develop") < calls.index("merge --ff-only origin/develop")
        assert_no_forbidden_git(calls)

    def test_up_to_date_does_not_merge(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, env_extra={"FAKE_GIT_LOCAL_SHA": "abc", "FAKE_GIT_REMOTE_SHA": "abc"})

        assert result.returncode == 0
        assert result.stdout.splitlines() == ["SYNC-BASE: ok reason=up-to-date"]
        calls = git_calls(sync_base_sandbox)
        assert "fetch origin main" in calls
        assert not [call for call in calls if call.startswith("merge ")]

    def test_other_branch_is_skipped_without_fetch(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, env_extra={"FAKE_GIT_CURRENT_BRANCH": "feature-1"})

        assert result.returncode == 0
        assert result.stdout.splitlines() == ["SYNC-BASE: skipped reason=not-on-base-branch"]
        calls = git_calls(sync_base_sandbox)
        assert not [call for call in calls if call.startswith(("fetch", "merge"))]
        assert_no_forbidden_git(calls)

    def test_dirty_tracked_files_are_skipped_without_fetch(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, env_extra={"FAKE_GIT_STATUS": " M src/app.py\n"})

        assert result.returncode == 0
        assert result.stdout.splitlines() == ["SYNC-BASE: skipped reason=dirty-tracked-files"]
        calls = git_calls(sync_base_sandbox)
        assert "status --porcelain --untracked-files=no" in calls
        assert not [call for call in calls if call.startswith(("fetch", "merge"))]

    def test_local_commits_ahead_are_skipped(self, sync_base_sandbox):
        env = {"FAKE_GIT_HEAD_IS_ANCESTOR_EXIT": "1", "FAKE_GIT_REMOTE_IS_ANCESTOR_EXIT": "0"}

        result = run_sync_base(sync_base_sandbox, env_extra=env)

        assert result.returncode == 0
        assert result.stdout.splitlines() == ["SYNC-BASE: skipped reason=local-ahead"]
        assert not [call for call in git_calls(sync_base_sandbox) if call.startswith("merge ")]

    def test_diverged_history_is_skipped(self, sync_base_sandbox):
        env = {"FAKE_GIT_HEAD_IS_ANCESTOR_EXIT": "1", "FAKE_GIT_REMOTE_IS_ANCESTOR_EXIT": "1"}

        result = run_sync_base(sync_base_sandbox, env_extra=env)

        assert result.returncode == 0
        assert result.stdout.splitlines() == ["SYNC-BASE: skipped reason=diverged"]
        calls = git_calls(sync_base_sandbox)
        assert not [call for call in calls if call.startswith("merge ")]
        assert_no_forbidden_git(calls)

    def test_fetch_failure_is_failed_exit_2_without_merge(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, env_extra={"FAKE_GIT_FETCH_EXIT": "1"})

        assert result.returncode == 2
        assert result.stdout.splitlines() == ["SYNC-BASE: failed reason=fetch-failed"]
        assert not [call for call in git_calls(sync_base_sandbox) if call.startswith("merge ")]

    def test_rejected_fast_forward_is_failed_exit_2(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, env_extra={"FAKE_GIT_MERGE_EXIT": "128"})

        assert result.returncode == 2
        assert result.stdout.splitlines() == ["SYNC-BASE: failed reason=merge-failed"]
        assert_no_forbidden_git(git_calls(sync_base_sandbox))

    def test_not_a_git_repo_is_failed_exit_2(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, env_extra={"FAKE_GIT_NOT_REPO": "1"})

        assert result.returncode == 2
        assert result.stdout.splitlines() == ["SYNC-BASE: failed reason=not-a-git-repo"]

    def test_missing_base_branch_is_usage_error_64(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, base_branch=None)

        assert result.returncode == 64
        assert result.stdout == ""
        assert git_calls(sync_base_sandbox) == []

    def test_blank_base_branch_is_usage_error_64(self, sync_base_sandbox):
        result = run_sync_base(sync_base_sandbox, base_branch="  ")

        assert result.returncode == 64
        assert git_calls(sync_base_sandbox) == []


@pytest.mark.unit
class TestState:
    """Testy podkomendy `state`. Slug/klucz dobrane pod fixture agentow (patrz
    docstring modulu): "fixture" + "probe" pasuje do sesji "fixture-probe"
    (id 9cf45f1d) w tests/fixtures/claude/agents-json-all.2.1.270.json."""

    AGENTS_FIXTURE = FIXTURES_DIR / "agents-json-all.2.1.270.json"
    LOGS_TUI_FIXTURE = FIXTURES_DIR / "logs-tui.2.1.270.txt"
    LOGS_SPINNER_FIXTURE = FIXTURES_DIR / "logs-spinner-redraw.2.1.284.txt"
    LOGS_STOP_FIXTURE = FIXTURES_DIR / "logs-stop-tui.2.1.278.txt"
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
        assert "\r" not in content, "\\r ma byc zinterpretowany jako powrot karetki (render_log) przed zapisem"

    def _run_state_with_running_probe(self, tick_sandbox, logs_fixture):
        """Zywa sesja "fixture-probe" (id 9cf45f1d) z logiem = surowy fixture `claude logs`.

        Wpis agenta z fixture agents-json-all ma state=done, wiec build_report nie
        klasyfikowalby jej po logu; tutaj ta sama sesja jest "running".
        """
        agents = [
            {
                "id": self.PROBE_SESSION_ID,
                "kind": "background",
                "startedAt": 1,
                "sessionId": "9cf45f1d-76c2-48f0-a4b2-328ec7a5466d",
                "name": "fixture-probe",
                "state": "running",
            }
        ]
        agents_file = tick_sandbox.tmp_path / "agents-running.json"
        agents_file.write_text(json.dumps(agents))
        tick_dir = tick_sandbox.tmp_path / "tick"
        tickets = [{"key": "probe", "status": "in_progress", "assignee": None}]
        env_extra = {
            "FAKE_CLAUDE_AGENTS_FILE": str(agents_file),
            f"FAKE_CLAUDE_LOGS_{self.PROBE_SESSION_ID}": str(logs_fixture),
        }
        result = run_state(tick_sandbox, "fixture", tickets=tickets, tick_dir=tick_dir, env_extra=env_extra)
        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        log_file = tick_dir / "logs" / f"{self.PROBE_SESSION_ID}.log"
        return json.loads(result.stdout), log_file.read_text().splitlines()

    def test_spinner_redraw_fixture_is_written_without_spinner_frames_or_duplicates(self, tick_sandbox):
        """MON-227: plik w TICK_DIR/logs i tak trafia do komentarza ticketu przy
        needs_human - ma byc czytelny (fixture logs-spinner-redraw.2.1.284.txt)."""
        _, lines = self._run_state_with_running_probe(tick_sandbox, self.LOGS_SPINNER_FIXTURE)

        assert lines
        assert not [line for line in lines if "Blanching" in line]
        assert not [line for line in lines if line[:1] in "✢✳✶✻✽·"]
        assert len([line for line in lines if "Sesje z ticketu" in line]) == 1
        assert len([line for line in lines if "Researcher pracuje." in line]) == 1
        content = "\n".join(lines)
        assert "Logi dostępne tylko dla żywych sesji." in content
        assert "dostępn  tylko" not in content
        assert "Bash( laude" not in content
        assert "\x1b" not in content

    def test_raw_stop_fixture_written_by_tick_is_classified_waiting(self, tick_sandbox):
        """MON-191 po MON-227: surowy fixture -> _write_session_log (render, ogon) ->
        build_report (read_log_tail + classify) daje "waiting", a plik ma jedna linie
        markera."""
        payload, lines = self._run_state_with_running_probe(tick_sandbox, self.LOGS_STOP_FIXTURE)

        assert payload["tickets"][0]["verdict"] == "waiting"
        stop_lines = [line for line in lines if line.startswith("SPRINT-RUN STOP:")]
        assert stop_lines == ["SPRINT-RUN STOP: fixture probe MON-191 - czekam na decyzję człowieka w sprawie migracji bazy danych przed merge"]
        assert "Nie uzywaj zadnych narzedzi" in "\n".join(lines)

    def test_written_log_keeps_only_last_60_lines_of_plain_output(self, tick_sandbox, tmp_path):
        """Ogon 60 linii dziala na czystym tekscie dluzszym niz ekran (emulator nie
        obcina do wysokosci ekranu, zapis nie przekracza _LOG_TAIL_LINES)."""
        plain = tmp_path / "plain.txt"
        plain.write_text("\n".join(f"linia {i}" for i in range(1, 101)) + "\n")

        _, lines = self._run_state_with_running_probe(tick_sandbox, plain)

        assert lines == [f"linia {i}" for i in range(41, 101)]

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


# ---------------------------------------------------------------------------
# MON-185: wybor runtime (--runtime claude|codex) w preflight, wybor backendu
# (--backend claude|codex) w state. Domyslna wartosc obu flag to "claude" --
# kazdy istniejacy test powyzej (bez --runtime/--backend) sprawdza wiec
# jednoczesnie, ze default nie zmienia zachowania (brak regresji).
#
# Fake `codex`: analogiczny do fake `claude` powyzej, ale bez podkomendy
# `agents`/`logs` (Codex nie ma odpowiednika `claude agents --json --all` --
# potwierdzone w briefie: `~/.codex/session_index.jsonl` nie zawiera sesji
# `codex exec`). Obsluguje `--version` i `login status`, sterowalne tymi
# samymi konwencjami FAKE_CODEX_* co reszta fakebina w tym pliku.
# ---------------------------------------------------------------------------

CODEX_SCRIPT = """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  exit_code="${FAKE_CODEX_VERSION_EXIT:-0}"
  if [ "$exit_code" != "0" ]; then
    echo "codex-stub: --version zakonczone bledem (symulacja testu)" >&2
    exit "$exit_code"
  fi
  echo "${FAKE_CODEX_VERSION_OUTPUT:-codex-cli 0.155.1}"
  exit 0
fi
if [ "${1:-}" = "login" ] && [ "${2:-}" = "status" ]; then
  exit_code="${FAKE_CODEX_LOGIN_EXIT:-0}"
  if [ "$exit_code" != "0" ]; then
    echo "codex-stub: login status zakonczone bledem (symulacja testu)" >&2
    exit "$exit_code"
  fi
  echo "${FAKE_CODEX_LOGIN_OUTPUT:-Logged in using ChatGPT}"
  exit 0
fi
if [ "${1:-}" = "exec" ]; then
  # MON-185 (iteracja 3): symuluje dlugozyjaca sesje w tle -- proces spi
  # FAKE_CODEX_EXEC_SLEEP sekund (domyslnie dosc dlugo, zeby PID byl wciaz
  # zywy przy DRUGIM wywolaniu `start` w testach idempotencji), potem konczy
  # sie sam. To jest jedyny proces, ktoremu testy CLI start/stop pozwalaja
  # wyslac realny SIGTERM -- nigdy nie jest to proces pytest (patrz uwaga w
  # TestCliStopCommand nizej).
  echo '{"type":"thread.started","thread_id":"fake-thread-cli"}'
  sleep "${FAKE_CODEX_EXEC_SLEEP:-4}"
  echo '{"type":"turn.completed","usage":{}}'
  exit 0
fi
echo "codex-stub: nieobslugiwane argumenty: $*" >&2
exit 127
"""


@pytest.fixture
def tick_sandbox_with_codex(tick_sandbox):
    """tick_sandbox + fake `codex` w tym samym fakebin (obok istniejacego `claude`)."""
    codex_script = tick_sandbox.fakebin / "codex"
    codex_script.write_text(CODEX_SCRIPT)
    codex_script.chmod(0o755)
    return tick_sandbox


def run_preflight_runtime(sandbox, runtime, project_dir=None, env_extra=None, timeout=30):
    project_dir = project_dir or sandbox.project_dir
    env = _base_env(sandbox, env_extra)
    return subprocess.run(
        [sys.executable, str(SCRIPT), "preflight", "--project-dir", str(project_dir), "--runtime", runtime],
        cwd=str(sandbox.project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.unit
class TestPreflightRuntimeSelection:
    def test_default_runtime_without_flag_is_claude_and_reported(self, tick_sandbox):
        """Brak regresji: bez --runtime zachowanie identyczne jak dotychczas, ale
        preflight teraz dodatkowo raportuje jawnie RUNTIME=claude."""
        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "RUNTIME=claude" in result.stdout.splitlines()

    def test_explicit_runtime_claude_behaves_like_default(self, tick_sandbox):
        result = run_preflight_runtime(tick_sandbox, "claude", env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "RUNTIME=claude" in result.stdout.splitlines()

    def test_runtime_codex_ok_without_claude_binary_present(self, tick_sandbox_with_codex):
        """MON-185: preflight Codex nie wymaga binarki `claude` w ogole -- usuwamy
        fake `claude` z fakebin (tylko `codex` zostaje), zeby udowodnic ze sciezka
        codex nigdy jej nie woala."""
        (tick_sandbox_with_codex.fakebin / "claude").unlink()

        result = run_preflight_runtime(tick_sandbox_with_codex, "codex", env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "RUNTIME=codex" in result.stdout.splitlines()
        assert not error_lines(result)

    def test_runtime_codex_missing_binary_exits_2(self, tick_sandbox):
        """Fakebin ma tylko `claude` (bez `codex`) -- wybor --runtime codex ma
        skonczyc sie bledem preflightu, nie cichym przejsciem."""
        result = run_preflight_runtime(tick_sandbox, "codex", env_extra=full_flags_env())

        assert result.returncode == 2
        errs = error_lines(result)
        assert any("codex" in line.lower() for line in errs)

    def test_runtime_codex_not_logged_in_exits_2(self, tick_sandbox_with_codex):
        env_extra = full_flags_env({"FAKE_CODEX_LOGIN_OUTPUT": "Not logged in"})

        result = run_preflight_runtime(tick_sandbox_with_codex, "codex", env_extra=env_extra)

        assert result.returncode == 2
        errs = error_lines(result)
        assert any("login" in line.lower() or "zalogowan" in line.lower() for line in errs)

    def test_runtime_codex_ignores_malformed_claude_settings_json(self, tick_sandbox_with_codex):
        """MON-183: preflight Codex NIE czyta .claude/settings.json -- plik
        zepsuty, ktory dla --runtime claude jest bledem (patrz
        test_invalid_json_in_settings_json_reports_error_and_exit_2), nie ma tu
        zadnego wplywu."""
        claude_dir = tick_sandbox_with_codex.project_dir / ".claude"
        claude_dir.mkdir()
        (claude_dir / "settings.json").write_text("{niepoprawny json")

        result = run_preflight_runtime(tick_sandbox_with_codex, "codex", env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert not any(".claude/settings.json" in line for line in error_lines(result))

    def test_runtime_codex_ignores_claude_bypass_permissions_variable(self, tick_sandbox_with_codex):
        """MONOLYNX_SPRINT_PERMISSION_MODE opisuje tryby Claude Code -- dla Codex
        (--sandbox / --dangerously-bypass-approvals-and-sandbox) to pojecie po
        prostu nie ma zastosowania, wiec bypassPermissions nie jest tu bledem."""
        env_extra = full_flags_env({"MONOLYNX_SPRINT_PERMISSION_MODE": "bypassPermissions"})

        result = run_preflight_runtime(tick_sandbox_with_codex, "codex", env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert not error_lines(result)

    def test_runtime_codex_still_requires_autoflags(self, tick_sandbox_with_codex):
        """Flagi AUTOTEST/AUTOCOMMIT/AUTOPUSH gatuja dzialania samego ticku
        (git push/testy/commit wykonywane przez /monolynx:work), niezaleznie od
        tego, ktory runtime prowadzi sesje robocza -- pozostaja wymagane."""
        env_extra = full_flags_env()
        del env_extra["MONOLYNX_AUTOTEST"]

        result = run_preflight_runtime(tick_sandbox_with_codex, "codex", env_extra=env_extra)

        assert result.returncode == 2
        errs = error_lines(result)
        assert any("MONOLYNX_AUTOTEST" in line for line in errs)

    def test_runtime_codex_never_leaks_secret_env_values(self, tick_sandbox_with_codex):
        env_extra = full_flags_env({"FAKE_CODEX_LOGIN_OUTPUT": "Not logged in", "OPENAI_API_KEY": "sekret-codex-123"})

        result = run_preflight_runtime(tick_sandbox_with_codex, "codex", env_extra=env_extra)

        combined = result.stdout + result.stderr
        assert "sekret-codex-123" not in combined

    def test_unknown_runtime_value_exits_2_or_64_not_silently_ok(self, tick_sandbox):
        """Wartosc runtime spoza {claude, codex} ma jawnie zawiescz (blad wejscia
        albo uzycia), nigdy cicho przejsc jako 'ok' -- literal spoza domeny nie
        jest tym samym co nieznany-ale-bezpieczny wariant z external-tool-output.md
        pkt 3 (to NASZ wybor uzytkownika, nie odczyt cudzego narzedzia)."""
        result = run_preflight_runtime(tick_sandbox, "gpt5", env_extra=full_flags_env())

        assert result.returncode in (2, 64)


def run_state_backend(sandbox, slug, backend, tickets=None, tick_dir=None, env_extra=None, timeout=30):
    env = _base_env(sandbox, env_extra)
    payload = json.dumps(tickets if tickets is not None else [])
    args = [sys.executable, str(SCRIPT), "state", "--slug", slug, "--tickets-json", payload, "--backend", backend]
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


@pytest.mark.unit
class TestStateBackendSelection:
    def test_default_backend_without_flag_is_claude_matches_existing_behavior(self, tick_sandbox):
        """Brak regresji: `state` bez --backend dziala dokladnie jak dzisiaj
        (woala `claude agents --json --all`)."""
        tickets = [{"key": "probe", "status": "todo", "assignee": None}]

        result = run_state(tick_sandbox, "fixture", tickets=tickets)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    def test_backend_codex_does_not_require_claude_binary(self, tick_sandbox_with_codex):
        """`state --backend codex` nie moze zalezec od `claude agents --json
        --all` -- usuwamy fake `claude`, zeby udowodnic, ze codex idzie inna
        sciezka (wlasny rejestr dyspozytora, nie zewnetrzne API klienta)."""
        (tick_sandbox_with_codex.fakebin / "claude").unlink()
        tickets = [{"key": "MON-1", "status": "todo", "assignee": None}]

        result = run_state_backend(tick_sandbox_with_codex, "monolynx", "codex", tickets=tickets)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        payload = json.loads(result.stdout)
        assert payload["tickets"][0]["key"] == "MON-1"

    def test_backend_claude_explicit_flag_matches_default(self, tick_sandbox):
        tickets = [{"key": "probe", "status": "todo", "assignee": None}]

        result = run_state_backend(tick_sandbox, "fixture", "claude", tickets=tickets)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"

    def test_unknown_backend_value_does_not_exit_zero(self, tick_sandbox):
        tickets = [{"key": "MON-1", "status": "todo", "assignee": None}]

        result = run_state_backend(tick_sandbox, "monolynx", "gpt5", tickets=tickets)

        assert result.returncode != 0


# ---------------------------------------------------------------------------
# MON-185 (iteracja 3): podkomendy CLI `start`/`stop` -- punkt wejscia
# brakujacy przy poprzednich iteracjach (wykryte przez technical-writer):
# sprint_run_backend.start()/stop() istnieja jako biblioteka, ale nic w CLI
# ich nie wola, wiec `MONOLYNX_SPRINT_RUNTIME=codex` nigdy nie zapelnia
# rejestru. Kontrakt ponizej jest ZAMROZONY przez Team Managera -- literalne
# linie wyjscia sa czescia specyfikacji, nie szczegolem implementacyjnym:
#
#   python3 sprint_run_tick.py start --backend codex --slug S --key K
#       --model M --prompt P [--cwd DIR]
#   python3 sprint_run_tick.py stop  --backend codex --slug S --key K
#
#   STARTED <identity> <runtime_id> <pid>        (start, nowy spawn, kod 0)
#   ALREADY-RUNNING <identity> <runtime_id>       (start, zywa sesja juz jest, kod 0)
#   STOPPED <identity>                            (stop, wpis usuniety, kod 0)
#   NOT-FOUND <identity>                          (stop, brak wpisu, kod 0)
#
# `start --backend claude` -> kod 64: backend claude startuje BEZPOSREDNIO w
# KROK 4 skilla (`claude --bg`, patrz istniejacy sprint_run.sh) -- przepuszczenie
# tego przez subprocess tego skryptu omijaloby uprawnienia Claude Code
# (MON-191/MON-192), wiec CLI ma to jawnie odrzucic, nie probowac.
#
# UWAGA PROJEKTOWA (do potwierdzenia przez krytyka/TM, patrz raport koncowy):
# testy `stop` NIE moga zamockowac `os.kill` tak jak TestStop w
# test_sprint_run_backend.py, bo tutaj produkcyjny kod dziala w OSOBNYM
# PROCESIE (subprocess.run wywoluje sprint_run_tick.py jako podproces) --
# monkeypatch pytest nie przekracza granicy procesu. Zamiast tego kazdy test
# `stop`, ktory faktycznie wysyla SIGTERM, celuje WYLACZNIE w PID
# jednorazowego fake `codex exec` (CODEX_SCRIPT powyzej, gałąź `exec`),
# wystartowanego chwile wczesniej przez ten sam test przez `start` -- NIGDY w
# proces pytest ani jakikolwiek dzielony zasob. To jest rownowazna gwarancja
# bezpieczenstwa (nigdy nie zabijamy czegos, na czym nam zalezy), tylko
# osiagnieta innym mechanizmem niz monkeypatch.
# ---------------------------------------------------------------------------


def run_cli_start(sandbox, backend, slug, key, model="opus", prompt="/monolynx:work KEY", cwd=None, env_extra=None, timeout=30):
    env = _base_env(sandbox, env_extra)
    args = [
        sys.executable,
        str(SCRIPT),
        "start",
        "--backend",
        backend,
        "--slug",
        slug,
        "--key",
        key,
        "--model",
        model,
        "--prompt",
        prompt,
    ]
    if cwd is not None:
        args += ["--cwd", str(cwd)]
    return subprocess.run(args, cwd=str(sandbox.project_dir), env=env, capture_output=True, text=True, timeout=timeout)


def run_cli_stop(sandbox, backend, slug, key, env_extra=None, timeout=30):
    env = _base_env(sandbox, env_extra)
    args = [sys.executable, str(SCRIPT), "stop", "--backend", backend, "--slug", slug, "--key", key]
    return subprocess.run(args, cwd=str(sandbox.project_dir), env=env, capture_output=True, text=True, timeout=timeout)


def _pid_is_zombie(pid):
    """Linux-only: /proc/<pid>/stat trzecie pole po ostatnim ')' to stan
    procesu. 'Z' = zombie -- wpis zostaje w tablicy procesow, ale proces
    juz nie zyje. comm (drugie pole, w nawiasach) moze zawierac spacje,
    wiec parsujemy od ostatniego ')', nie przez naiwny split()."""
    try:
        with open(f"/proc/{pid}/stat") as fh:
            stat = fh.read()
    except FileNotFoundError:
        return None
    after_comm = stat.rsplit(")", 1)[-1].split()
    return after_comm[0] == "Z" if after_comm else None


def _wait_for_pid_gone(pid, timeout=15):
    """Proces uznajemy za zakonczony gdy zniknie z tablicy procesow ALBO
    (na Linuksie) przejdzie w stan zombie.

    WHY: w kontenerze testowym `docker compose run` PID 1 nie zniwuje
    osieroconych dzieci (fake `codex exec` jest spawnowany przez proces
    ktory konczy sie natychmiast, wiec dziecko trafia pod PID 1). Zabity
    SIGTERM-em proces zostaje wiec zombie ('Z' w /proc/<pid>/stat) zamiast
    zniknac calkowicie. `os.kill(pid, 0)` na zombie nadal sie udaje --
    zombie to wpis w tablicy procesow, nie zywy proces -- wiec sam
    `os.kill` nigdy nie zobaczy zakonczenia. Zweryfikowane empirycznie
    (MON-185, iteracja 5): `stat=['9', '(sh)', 'Z']` dla zabitego fake
    codeksa. Na macOS (/proc nie istnieje) wracamy do samego os.kill."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return True
        else:
            zombie = _pid_is_zombie(pid)
            if zombie:
                return True
            if zombie is None and not os.path.exists("/proc"):
                # /proc niedostepny (np. macOS) -- brak sposobu odroznienia
                # zombie od zywego procesu, polegamy wylacznie na os.kill.
                pass
        time.sleep(0.1)
    return False


def _wait_for_pid_alive(pid, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            time.sleep(0.05)
    return False


@pytest.mark.unit
class TestCliStartCommand:
    def test_backend_claude_is_rejected_with_exit_64(self, tick_sandbox):
        """Backend claude startuje sesje bezposrednio w KROK 4 skilla
        (`claude --bg`) -- przepuszczenie tego przez subprocess tego skryptu
        omijaloby uprawnienia Claude Code. CLI ma to odrzucic, nie probowac."""
        result = run_cli_start(tick_sandbox, "claude", "monolynx", "MON-1")

        assert result.returncode == 64
        assert "claude" in result.stderr.lower()

    def test_codex_start_on_free_ticket_spawns_and_prints_started(self, tick_sandbox_with_codex):
        result = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-1")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        line = result.stdout.strip().splitlines()[-1]
        parts = line.split()
        assert parts[0] == "STARTED"
        assert parts[1] == "monolynx:MON-1"
        assert parts[3].isdigit()
        pid = int(parts[3])
        assert _wait_for_pid_alive(pid), "PID zwrocony przez STARTED powinien byc zywym procesem fake codex"

    def test_second_start_for_same_ticket_is_already_running_no_second_spawn(self, tick_sandbox_with_codex):
        first = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-2")
        assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"
        first_pid = int(first.stdout.strip().splitlines()[-1].split()[3])

        second = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-2")

        assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"
        line = second.stdout.strip().splitlines()[-1]
        parts = line.split()
        assert parts[0] == "ALREADY-RUNNING"
        assert parts[1] == "monolynx:MON-2"
        # Dowod braku drugiego spawnu: proces z PIERWSZEGO startu jest nadal
        # zywy i to jego runtime_id/pid sa raportowane (identycznosc przez
        # sprawdzenie ze proces nie zostal zduplikowany -- drugi PID by byl
        # inny gdyby nastapil nowy spawn; tu weryfikujemy posrednio przez
        # fakt ze PIERWSZY proces nie zostal przerwany).
        assert _wait_for_pid_alive(first_pid)

    def test_model_change_between_starts_is_already_running(self, tick_sandbox_with_codex):
        first = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-3", model="opus")
        assert first.returncode == 0, f"stdout={first.stdout!r} stderr={first.stderr!r}"

        second = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-3", model="sonnet")

        assert second.returncode == 0, f"stdout={second.stdout!r} stderr={second.stderr!r}"
        assert second.stdout.strip().splitlines()[-1].startswith("ALREADY-RUNNING monolynx:MON-3")

    def test_live_claude_agent_session_blocks_codex_start_ac3(self, tick_sandbox_with_codex):
        """AC3: ticket ma zywa sesje Claude widoczna w `claude agents --json
        --all` -- `start --backend codex` ma zwrocic ALREADY-RUNNING bez
        spawnu procesu codex, best-effort skonsultowawszy `claude agents`
        (fake `claude` istnieje w tym fakebin)."""
        agents_file = tick_sandbox_with_codex.tmp_path / "agents.json"
        agents_file.write_text(
            json.dumps(
                [
                    {
                        "id": "a1111111",
                        "cwd": "/repo",
                        "kind": "background",
                        "startedAt": 1000,
                        "sessionId": "sid-1",
                        "name": "monolynx-MON-4-opus",
                        "state": "running",
                    }
                ]
            )
        )
        env_extra = {"FAKE_CLAUDE_AGENTS_FILE": str(agents_file)}

        result = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-4", env_extra=env_extra)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.strip().splitlines()[-1].startswith("ALREADY-RUNNING monolynx:MON-4")

    def test_missing_claude_binary_is_not_an_error_for_codex_start(self, tick_sandbox_with_codex):
        """Brak binarki `claude` NIE jest bledem dla `start --backend codex` --
        to tylko brak jednego (opcjonalnego) zrodla sygnalu, rejestr wlasny
        wystarcza."""
        (tick_sandbox_with_codex.fakebin / "claude").unlink()

        result = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-5")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.strip().splitlines()[-1].startswith("STARTED monolynx:MON-5")

    def test_preflight_failed_missing_codex_binary_exits_2_with_no_side_effects(self, tick_sandbox):
        """Fakebin ma tylko `claude`, brak `codex` -- preflight dla backend=codex
        ma zawiesc: kod 2, stderr z przyczyna, ZERO skutkow ubocznych (zaden
        proces nie zostal wystartowany)."""
        result = run_cli_start(tick_sandbox, "codex", "monolynx", "MON-6")

        assert result.returncode == 2
        assert result.stderr.strip()
        assert "codex" in result.stderr.lower()

    def test_preflight_failed_not_logged_in_exits_2_with_no_side_effects(self, tick_sandbox_with_codex):
        env_extra = {"FAKE_CODEX_LOGIN_OUTPUT": "Not logged in"}

        result = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-7", env_extra=env_extra)

        assert result.returncode == 2
        assert result.stderr.strip()

    def test_missing_required_argument_exits_64(self, tick_sandbox_with_codex):
        env = _base_env(tick_sandbox_with_codex, None)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "start", "--backend", "codex", "--slug", "monolynx"],
            cwd=str(tick_sandbox_with_codex.project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 64

    def test_unknown_backend_value_exits_64(self, tick_sandbox_with_codex):
        result = run_cli_start(tick_sandbox_with_codex, "gpt5", "monolynx", "MON-8")

        assert result.returncode == 64


@pytest.mark.unit
class TestCliStopCommand:
    """Zaden test w tej klasie nie kieruje SIGTERM w proces pytest ani zaden
    dzielony zasob -- celem jest zawsze jednorazowy proces fake `codex exec`
    wystartowany przez `start` chwile wczesniej w tym samym tescie (patrz
    uwaga projektowa w naglowku sekcji)."""

    def test_stop_on_own_entry_terminates_the_real_child_and_removes_registry_entry(self, tick_sandbox_with_codex):
        start_result = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-9")
        assert start_result.returncode == 0, f"stdout={start_result.stdout!r} stderr={start_result.stderr!r}"
        pid = int(start_result.stdout.strip().splitlines()[-1].split()[3])
        assert _wait_for_pid_alive(pid)

        stop_result = run_cli_stop(tick_sandbox_with_codex, "codex", "monolynx", "MON-9")

        assert stop_result.returncode == 0, f"stdout={stop_result.stdout!r} stderr={stop_result.stderr!r}"
        assert stop_result.stdout.strip() == "STOPPED monolynx:MON-9"
        assert _wait_for_pid_gone(pid), "proces fake codex mial dostac SIGTERM i zakonczyc sie"

        # Po stop() wpis ma zniknac -- kolejny start() dla tego samego ticketu
        # ma wiec spawnowac NOWY proces (dowod, ze rejestr faktycznie zostal
        # oczyszczony, nie tylko ze proces zdechl).
        restart_result = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-9")
        assert restart_result.returncode == 0
        assert restart_result.stdout.strip().splitlines()[-1].startswith("STARTED monolynx:MON-9")
        new_pid = int(restart_result.stdout.strip().splitlines()[-1].split()[3])
        assert new_pid != pid

    def test_stop_on_missing_entry_is_not_found_and_leaves_registry_untouched(self, tick_sandbox_with_codex):
        result = run_cli_stop(tick_sandbox_with_codex, "codex", "monolynx", "MON-NIEISTNIEJE")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert result.stdout.strip() == "NOT-FOUND monolynx:MON-NIEISTNIEJE"

    def test_stop_does_not_affect_other_tickets_entry(self, tick_sandbox_with_codex):
        """Cudzy wpis (inny ticket) pozostaje nietkniety -- analogiczny wymog co
        test_leaves_other_identities_untouched w test_sprint_run_backend.py,
        tu zweryfikowany end-to-end przez CLI: drugi ticket dalej daje
        ALREADY-RUNNING po stop() pierwszego."""
        started_a = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-10")
        assert started_a.returncode == 0
        pid_a = int(started_a.stdout.strip().splitlines()[-1].split()[3])
        started_b = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-11")
        assert started_b.returncode == 0

        stop_a = run_cli_stop(tick_sandbox_with_codex, "codex", "monolynx", "MON-10")
        assert stop_a.returncode == 0
        assert stop_a.stdout.strip() == "STOPPED monolynx:MON-10"
        _wait_for_pid_gone(pid_a)

        still_running_b = run_cli_start(tick_sandbox_with_codex, "codex", "monolynx", "MON-11")
        assert still_running_b.returncode == 0
        assert still_running_b.stdout.strip().splitlines()[-1].startswith("ALREADY-RUNNING monolynx:MON-11")

    def test_missing_required_argument_exits_64(self, tick_sandbox_with_codex):
        env = _base_env(tick_sandbox_with_codex, None)
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "stop", "--backend", "codex", "--slug", "monolynx"],
            cwd=str(tick_sandbox_with_codex.project_dir),
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )

        assert result.returncode == 64

    def test_unknown_backend_value_exits_64(self, tick_sandbox_with_codex):
        result = run_cli_stop(tick_sandbox_with_codex, "gpt5", "monolynx", "MON-12")

        assert result.returncode == 64


# ---------------------------------------------------------------------------
# MON-222: sesje waiting poza pula slotow. preflight raportuje MAX_WAITING
# (zmienna MONOLYNX_SPRINT_MAX_WAITING, domyslnie 2), `state --slots N
# [--max-waiting M]` dokleja do raportu klucz "dispatch" z
# sprint_run_state.dispatch_capacity. Bez --slots raport nie ma tego klucza
# (kompatybilnosc wsteczna - skill `next` czyta `state` bez slotow).
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestPreflightMaxWaiting:
    def test_max_waiting_defaults_to_two_and_follows_parallel_line(self, tick_sandbox):
        result = run_preflight(tick_sandbox, env_extra=full_flags_env())

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        lines = result.stdout.splitlines()
        assert "MAX_WAITING=2" in lines
        parallel_index = next(i for i, line in enumerate(lines) if line.startswith("PARALLEL="))
        assert lines[parallel_index + 1] == "MAX_WAITING=2"

    def test_max_waiting_reads_positive_integer_from_env(self, tick_sandbox):
        result = run_preflight(tick_sandbox, env_extra=full_flags_env({"MONOLYNX_SPRINT_MAX_WAITING": "4"}))

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "MAX_WAITING=4" in result.stdout.splitlines()

    @pytest.mark.parametrize("raw", ["abc", "0", "-1", "2.5", ""])
    def test_max_waiting_invalid_value_falls_back_to_default(self, tick_sandbox, raw):
        result = run_preflight(tick_sandbox, env_extra=full_flags_env({"MONOLYNX_SPRINT_MAX_WAITING": raw}))

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "MAX_WAITING=2" in result.stdout.splitlines()

    def test_max_waiting_reported_for_codex_runtime_too(self, tick_sandbox_with_codex):
        result = run_preflight_runtime(tick_sandbox_with_codex, "codex", env_extra=full_flags_env({"MONOLYNX_SPRINT_MAX_WAITING": "3"}))

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "MAX_WAITING=3" in result.stdout.splitlines()


def run_state_dispatch(sandbox, tickets, slots=None, max_waiting=None, backend=None, env_extra=None, timeout=30):
    """`state` z opcjonalnymi --slots/--max-waiting/--backend. Wartosci podawane
    jako napisy, zeby test mogl wyslac tez zla wartosc (np. "x")."""
    env = _base_env(sandbox, env_extra)
    args = [sys.executable, str(SCRIPT), "state", "--slug", "fixture", "--tickets-json", json.dumps(tickets)]
    if slots is not None:
        args += ["--slots", str(slots)]
    if max_waiting is not None:
        args += ["--max-waiting", str(max_waiting)]
    if backend is not None:
        args += ["--backend", backend]
    return subprocess.run(args, cwd=str(sandbox.project_dir), env=env, capture_output=True, text=True, timeout=timeout)


def _bg_session(name, session_id):
    return {
        "id": session_id,
        "cwd": "/repo",
        "kind": "background",
        "startedAt": 1000,
        "sessionId": f"full-{session_id}",
        "name": name,
        "state": "running",
    }


def _dispatch_env(sandbox, running_keys=(), waiting_keys=()):
    """Env dla fake `claude`: sesje w tle dla podanych kluczy ticketow. Sesja
    `waiting` to sesja zywa z markerem SPRINT-RUN STOP w logu (werdykt liczy
    build_report, nie ten helper)."""
    agents = []
    env = {}
    for key in running_keys:
        agents.append(_bg_session(f"fixture-{key}", f"r{key}"))
    for key in waiting_keys:
        session_id = f"w{key}"
        agents.append(_bg_session(f"fixture-{key}", session_id))
        log_file = sandbox.tmp_path / f"{session_id}.txt"
        log_file.write_text("robie rzeczy\nSPRINT-RUN STOP: czekam na decyzje\n")
        env[f"FAKE_CLAUDE_LOGS_{session_id}"] = str(log_file)
    agents_file = sandbox.tmp_path / "dispatch-agents.json"
    agents_file.write_text(json.dumps(agents))
    env["FAKE_CLAUDE_AGENTS_FILE"] = str(agents_file)
    return env


def _in_progress(*keys):
    return [{"key": key, "status": "in_progress", "assignee": None} for key in keys]


@pytest.mark.unit
class TestStateDispatchCapacity:
    def test_report_without_slots_has_no_dispatch_key(self, tick_sandbox):
        result = run_state_dispatch(tick_sandbox, [{"key": "probe", "status": "todo", "assignee": None}])

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "dispatch" not in json.loads(result.stdout)

    def test_slots_adds_dispatch_with_defaults_when_no_sessions(self, tick_sandbox):
        result = run_state_dispatch(tick_sandbox, [{"key": "probe", "status": "todo", "assignee": None}], slots=3)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert json.loads(result.stdout)["dispatch"] == {
            "slots": 3,
            "max_waiting": 2,
            "free_slots": 3,
            "waiting_blocked": False,
            "can_start": 3,
        }

    def test_running_session_takes_a_slot_waiting_session_does_not(self, tick_sandbox):
        """Kluczowy przypadek MON-222 przez realna sciezke state: 1 running +
        1 waiting przy slots=2 daje wolny slot 1. Odwrocone liczenie
        slots - (running + waiting) dalo by 0."""
        env = _dispatch_env(tick_sandbox, running_keys=["a"], waiting_keys=["b"])

        result = run_state_dispatch(tick_sandbox, _in_progress("a", "b"), slots=2, env_extra=env)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        payload = json.loads(result.stdout)
        assert payload["summary"]["running"] == 1
        assert payload["summary"]["waiting"] == 1
        assert payload["dispatch"] == {
            "slots": 2,
            "max_waiting": 2,
            "free_slots": 1,
            "waiting_blocked": False,
            "can_start": 1,
        }

    def test_max_waiting_option_blocks_start_when_reached(self, tick_sandbox):
        env = _dispatch_env(tick_sandbox, waiting_keys=["b"])

        result = run_state_dispatch(tick_sandbox, _in_progress("b"), slots=2, max_waiting=1, env_extra=env)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        payload = json.loads(result.stdout)
        assert payload["summary"]["waiting"] == 1
        assert payload["dispatch"]["max_waiting"] == 1
        assert payload["dispatch"]["waiting_blocked"] is True
        assert payload["dispatch"]["free_slots"] == 2
        assert payload["dispatch"]["can_start"] == 0

    def test_default_max_waiting_two_allows_one_waiting_session(self, tick_sandbox):
        """Granica >=: przy jednej sesji waiting i limicie 2 (domyslnym) dispatch
        nie jest zablokowany - test wiaze ten sam stan co poprzedni, ale bez
        --max-waiting."""
        env = _dispatch_env(tick_sandbox, waiting_keys=["b"])

        result = run_state_dispatch(tick_sandbox, _in_progress("b"), slots=2, env_extra=env)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        payload = json.loads(result.stdout)
        assert payload["dispatch"]["waiting_blocked"] is False
        assert payload["dispatch"]["can_start"] == 2

    def test_two_waiting_sessions_reach_default_limit(self, tick_sandbox):
        env = _dispatch_env(tick_sandbox, waiting_keys=["b", "c"])

        result = run_state_dispatch(tick_sandbox, _in_progress("b", "c"), slots=3, env_extra=env)

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        payload = json.loads(result.stdout)
        assert payload["summary"]["waiting"] == 2
        assert payload["dispatch"]["waiting_blocked"] is True
        assert payload["dispatch"]["can_start"] == 0

    def test_invalid_max_waiting_falls_back_to_default_two(self, tick_sandbox):
        result = run_state_dispatch(tick_sandbox, [{"key": "probe", "status": "todo", "assignee": None}], slots=2, max_waiting="abc")

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert json.loads(result.stdout)["dispatch"]["max_waiting"] == 2

    @pytest.mark.parametrize("bad_slots", ["0", "-1", "x", "1.5"])
    def test_non_positive_or_non_integer_slots_exits_64(self, tick_sandbox, bad_slots):
        result = run_state_dispatch(tick_sandbox, [{"key": "probe", "status": "todo", "assignee": None}], slots=bad_slots)

        assert result.returncode == 64

    def test_codex_backend_also_reports_dispatch(self, tick_sandbox_with_codex):
        (tick_sandbox_with_codex.fakebin / "claude").unlink()

        result = run_state_dispatch(
            tick_sandbox_with_codex,
            [{"key": "MON-1", "status": "todo", "assignee": None}],
            slots=3,
            backend="codex",
        )

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert json.loads(result.stdout)["dispatch"] == {
            "slots": 3,
            "max_waiting": 2,
            "free_slots": 3,
            "waiting_blocked": False,
            "can_start": 3,
        }
