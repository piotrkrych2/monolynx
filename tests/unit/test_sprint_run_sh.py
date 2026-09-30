"""Testy petli zewnetrznej plugin/scripts/sprint_run.sh.

Skrypt JUZ ISTNIEJE (kontrakt zamrozony -- patrz naglowek pliku sprint_run.sh),
wiec test twardo wymaga jego obecnosci (assert, nie skip), analogicznie do
tests/unit/test_bash_guard.py.

Srodowisko testowe symuluje kontener dev, w ktorym NIE MA prawdziwego `git`
ani `claude` w PATH poza tym, co przygotuje test -- oba sa stubowane w
katalogu `fakebin` dodanym na poczatku PATH. Fake `git` odpowiada na
`rev-parse --git-dir` / `rev-parse --show-toplevel` sciezkami tymczasowego
projektu; fake `claude` zapisuje otrzymane argumenty do pliku (FAKE_LOG),
wypisuje zawartosc pliku statusu (FAKE_STATUS) na stdout (linia
SPRINT-RUN: remaining=... running=... waiting=...) i zrzuca zmienne
MONOLYNX_* oraz CLAUDE_CODE_EXPERIMENTAL_* ze srodowiska do pliku
(FAKE_ENV_DUMP), zeby zweryfikowac eksport flag trybu w tle
(MON-165/MON-167/MON-172: MONOLYNX_SPRINT_RUN_MODE i
CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS).

Kazde wywolanie skryptu dostaje `--interval 0`, zeby ewentualne `sleep`
miedzy tickami nie spowalnialy testow (patrz `to_seconds` w sprint_run.sh:
"0" -> 0 sekund).
"""

import os
import re
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "plugin" / "scripts" / "sprint_run.sh"

assert SCRIPT.exists(), f"Brak pliku skryptu: {SCRIPT} -- sprawdz obraz Docker / checkout repo."

GIT_SCRIPT_TEMPLATE = """#!/usr/bin/env bash
case "$1 $2" in
  "rev-parse --git-dir") echo "{git_dir}" ;;
  "rev-parse --show-toplevel") echo "{project_dir}" ;;
  *) exit 0 ;;
esac
"""

CLAUDE_SCRIPT = """#!/usr/bin/env bash
echo "raport dzialania agenta w tle"
if [ -n "${FAKE_STATUS:-}" ] && [ -f "${FAKE_STATUS:-}" ]; then
  cat "$FAKE_STATUS"
fi
if [ -n "${FAKE_LOG:-}" ]; then
  printf 'ARGS: %s\\n' "$*" >> "$FAKE_LOG"
fi
if [ -n "${FAKE_ENV_DUMP:-}" ]; then
  env | grep '^MONOLYNX_\\|^CLAUDE_CODE_EXPERIMENTAL' > "$FAKE_ENV_DUMP" || true
fi
exit 0
"""


@pytest.fixture
def sandbox(tmp_path):
    """Przygotuj tymczasowy projekt git (symulowany) + fakebin z git i claude."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    git_dir = project_dir / ".git"

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()

    git_script = fakebin / "git"
    git_script.write_text(GIT_SCRIPT_TEMPLATE.format(git_dir=git_dir, project_dir=project_dir))
    git_script.chmod(0o755)

    fake_log = tmp_path / "fake_claude_calls.log"
    fake_status = tmp_path / "fake_status.txt"
    fake_env_dump = tmp_path / "fake_claude_env.txt"
    fake_status.write_text("")

    return SimpleNamespace(
        tmp_path=tmp_path,
        project_dir=project_dir,
        git_dir=git_dir,
        fakebin=fakebin,
        fake_log=fake_log,
        fake_status=fake_status,
        fake_env_dump=fake_env_dump,
    )


def _write_claude(sandbox, include_claude=True):
    if not include_claude:
        return
    claude_script = sandbox.fakebin / "claude"
    claude_script.write_text(CLAUDE_SCRIPT)
    claude_script.chmod(0o755)


def write_status(sandbox, text):
    sandbox.fake_status.write_text(text if text.endswith("\n") else text + "\n")


def arg_lines(sandbox):
    if not sandbox.fake_log.exists():
        return []
    return [line for line in sandbox.fake_log.read_text().splitlines() if line.startswith("ARGS:")]


def _base_env(sandbox, slug, env_overrides):
    env = {
        "PATH": f"{sandbox.fakebin}:/usr/local/bin:/usr/bin:/bin",
        "FAKE_STATUS": str(sandbox.fake_status),
        "FAKE_LOG": str(sandbox.fake_log),
        "FAKE_ENV_DUMP": str(sandbox.fake_env_dump),
    }
    if slug is not None:
        env["MONOLYNX_PROJECT_SLUG"] = slug
    if env_overrides:
        env.update(env_overrides)
    return env


def run_script(sandbox, args, env_overrides=None, timeout=30, slug: str | None = "testslug", include_claude=True):
    _write_claude(sandbox, include_claude=include_claude)
    env = _base_env(sandbox, slug, env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), "--interval", "0", *args],
        cwd=str(sandbox.project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def run_script_raw(sandbox, args, env_overrides=None, timeout=30, slug: str | None = "testslug", include_claude=True):
    """Jak run_script, ale BEZ automatycznego dopisania `--interval 0` -- do testowania
    parsowania argumentow samego skryptu (np. `--interval` bez wartosci na koncu)."""
    _write_claude(sandbox, include_claude=include_claude)
    env = _base_env(sandbox, slug, env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        cwd=str(sandbox.project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


@pytest.mark.unit
class TestSprintRunSh:
    def test_script_file_exists(self):
        """Redundantne z modulowym assertem powyzej -- czytelny test w raporcie CI."""
        assert SCRIPT.is_file()

    def test_sprint_wykonany_exits_zero_and_logs(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=3")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        assert "sprint wykonany" in result.stdout
        calls = arg_lines(sandbox)
        assert len(calls) == 1
        assert "-p /monolynx:sprint-run 2" in calls[0]
        assert "--name testslug-sprint-run-opus" in calls[0]

    def test_once_flag_exits_zero_after_single_tick_even_with_remaining_work(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=2 running=2 waiting=0 done=0")

        result = run_script(sandbox, ["--once"])

        assert result.returncode == 0
        assert len(arg_lines(sandbox)) == 1

    def test_only_waiting_sessions_left_exits_4(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=1 done=2")

        result = run_script(sandbox, [])

        assert result.returncode == 4

    def test_missing_status_line_three_times_exits_2(self, sandbox):
        write_status(sandbox, "brak linii statusu w tej odpowiedzi")

        result = run_script(sandbox, [])

        assert result.returncode == 2
        assert len(arg_lines(sandbox)) == 3

    def test_max_ticks_reached_exits_3(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=2 running=1 waiting=0 done=0")

        result = run_script(sandbox, ["--max-ticks", "2"])

        assert result.returncode == 3
        assert len(arg_lines(sandbox)) == 2

    def test_unknown_argument_exits_64(self, sandbox):
        result = run_script(sandbox, ["--foo"])

        assert result.returncode == 64

    def test_interval_flag_without_value_exits_64(self, sandbox):
        """`--interval` jako ostatni argument bez wartosci -- guard w skrypcie, nie
        `shift: shift count out of range` spod `set -u`."""
        result = run_script_raw(sandbox, ["--interval"])

        assert result.returncode == 64

    def test_claude_missing_from_path_exits_69(self, sandbox):
        result = run_script(sandbox, [], include_claude=False)

        assert result.returncode == 69

    def test_no_slug_anywhere_exits_78(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], slug=None, env_overrides={"MONOLYNX_PROJECT_SLUG": ""})

        assert result.returncode == 78

    def test_slug_fallback_from_claude_settings_json(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=1")
        settings_dir = sandbox.project_dir / ".claude"
        settings_dir.mkdir()
        (settings_dir / "settings.json").write_text('{"env": {"MONOLYNX_PROJECT_SLUG": "sluginplik"}}')

        result = run_script(sandbox, [], slug=None, env_overrides={"MONOLYNX_PROJECT_SLUG": ""})

        assert result.returncode == 0
        calls = arg_lines(sandbox)
        assert len(calls) == 1
        assert "--name sluginplik-sprint-run-opus" in calls[0]

    def test_pid_file_with_live_process_exits_75(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")
        state_dir = sandbox.git_dir / "monolynx"
        state_dir.mkdir(parents=True)
        (state_dir / "sprint-run.pid").write_text(str(os.getpid()))

        result = run_script(sandbox, [])

        assert result.returncode == 75

    def test_exports_background_mode_flags(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        dump = sandbox.fake_env_dump.read_text()
        assert "MONOLYNX_SPRINT_RUN=1" in dump
        assert "MONOLYNX_CONTRACT=auto" in dump
        assert "MONOLYNX_AUTOTEST=true" in dump
        assert "MONOLYNX_SPRINT_PARALLEL=2" in dump

    def test_external_automr_false_override_is_preserved(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], env_overrides={"MONOLYNX_AUTOMR": "false"})

        assert result.returncode == 0
        dump = sandbox.fake_env_dump.read_text()
        assert "MONOLYNX_AUTOMR=false" in dump

    def test_slots_flag_is_forwarded_to_prompt_and_env(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, ["--slots", "3"])

        assert result.returncode == 0
        calls = arg_lines(sandbox)
        assert "/monolynx:sprint-run 3" in calls[0]
        dump = sandbox.fake_env_dump.read_text()
        assert "MONOLYNX_SPRINT_PARALLEL=3" in dump

    def test_exports_sprint_run_mode_batch(self, sandbox):
        """MON-172: MONOLYNX_SPRINT_RUN_MODE=batch to stala kontraktu (nie ${VAR:-default})
        -- skille czytaja ja zeby wiedziec ze pracuja pod dyspozytorem w tle, nie w
        sesji interaktywnej."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        dump = sandbox.fake_env_dump.read_text()
        assert "MONOLYNX_SPRINT_RUN_MODE=batch" in dump

    def test_sprint_run_mode_is_not_overridable_from_environment(self, sandbox):
        """MONOLYNX_SPRINT_RUN_MODE jest stala kontraktu -- w przeciwienstwie do innych
        flag MONOLYNX_* (ktore uzywaja ${VAR:-default}) nie wolno jej nadpisac z
        zewnatrz, zeby dyspozytor nie mogl przez pomylke uruchomic skilla w innym trybie."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], env_overrides={"MONOLYNX_SPRINT_RUN_MODE": "custom"})

        assert result.returncode == 0
        dump = sandbox.fake_env_dump.read_text()
        assert "MONOLYNX_SPRINT_RUN_MODE=batch" in dump

    def test_exports_agent_teams_flag_enabled_by_default(self, sandbox):
        """CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS domyslnie 1 -- sesje w tle dziedzicza
        srodowisko procesu (nie shell), wiec bez tego skille zespolowe (Agent Teams)
        nie wlaczylyby sie w sesjach startowanych przez sprint_run.sh (MON-172)."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        dump = sandbox.fake_env_dump.read_text()
        assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1" in dump

    def test_external_agent_teams_override_is_preserved(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], env_overrides={"CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS": "0"})

        assert result.returncode == 0
        dump = sandbox.fake_env_dump.read_text()
        assert "CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=0" in dump

    def test_default_model_is_opus_for_tick_and_work_sessions(self, sandbox):
        """Bez --model `claude -p` bierze domyslny model konta (bywal to najdrozszy
        model) - tick idzie na opus, a sesje work dostaja MONOLYNX_SPRINT_MODEL=opus."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, ["--close"])

        assert result.returncode == 0
        calls = arg_lines(sandbox)
        assert len(calls) == 3
        assert all("--model opus" in call for call in calls)
        dump = sandbox.fake_env_dump.read_text()
        assert "MONOLYNX_SPRINT_MODEL=opus" in dump

    def test_model_overrides_from_environment_are_preserved(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(
            sandbox,
            [],
            env_overrides={"MONOLYNX_SPRINT_TICK_MODEL": "sonnet", "MONOLYNX_SPRINT_MODEL": "fable"},
        )

        assert result.returncode == 0
        calls = arg_lines(sandbox)
        assert "--model sonnet" in calls[0]
        assert "--name testslug-sprint-run-sonnet" in calls[0]
        dump = sandbox.fake_env_dump.read_text()
        assert "MONOLYNX_SPRINT_MODEL=fable" in dump

    def test_close_flag_runs_sprint_end_and_retro_after_sprint_done(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=5")

        result = run_script(sandbox, ["--close"])

        assert result.returncode == 0
        calls = arg_lines(sandbox)
        assert len(calls) == 3
        assert "--name testslug-sprint-run-opus" in calls[0]
        assert "--name testslug-sprint-end-opus" in calls[1]
        assert "--name testslug-retro-opus" in calls[2]

    def test_run_claude_gets_allowed_tools_and_permission_prompts_none(self, sandbox):
        """MON-190: tick w `claude -p` startuje w swiezym projekcie bez recznych
        regul permissions.allow -- uprawnienia daje `--allowedTools` przekazywane
        przez sprint_run.sh, a `--permission-prompts none` odrzuca natychmiast i
        widocznie w logu (nikt nie odpowie na prompt w trybie -p)."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        calls = arg_lines(sandbox)
        assert len(calls) == 1
        call = calls[0]
        assert "--allowedTools" in call
        assert "Bash(claude:*)" in call
        assert "Bash(python3:*)" in call
        assert "mcp__monolynx" in call
        assert "mcp__claude_ai_Monolynx" in call
        assert "mcp__plugin_monolynx_monolynx" in call
        assert "--permission-prompts none" in call

    def test_default_allowed_tools_includes_sprint_run_flagged_claude_invocation(self, sandbox):
        """MON-207: `sprint-run/SKILL.md` KROK 4 startuje sesje robocze wprost jako
        `MONOLYNX_SPRINT_RUN=1 claude --bg ...` (przypisanie zmiennej inline zamiast
        polegania na tracked `.claude/settings.json`, patrz P1/P8). `Bash(claude:*)`
        pasuje tylko do polecen zaczynajacych sie od doslownego `claude`, wiec bez
        osobnej reguly to konkretne wywolanie nie byloby objete domyslnym allowedTools."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        call = arg_lines(sandbox)[0]
        assert "Bash(MONOLYNX_SPRINT_RUN=1 claude:*)" in call

    def test_allowed_tools_overridable_via_env(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], env_overrides={"MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS": "Read"})

        assert result.returncode == 0
        calls = arg_lines(sandbox)
        call = calls[0]
        assert "--allowedTools Read" in call
        assert "Bash(claude:*)" not in call

    def test_start_log_does_not_dump_full_allowed_tools_list(self, sandbox):
        """Log startu opisuje tylko czy lista jest wlasna czy domyslna -- nie
        wypisuje jej w calosci (MON-190 KROK 1 - zakaz drukowania calego env/listy
        narzedzi do transkryptu i logu petli, patrz tez incydent printenv 18.09)."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        log_path = sandbox.git_dir / "monolynx" / "sprint-run.log"
        start_lines = [line for line in log_path.read_text().splitlines() if "start:" in line]
        assert start_lines
        assert "mcp__monolynx" not in start_lines[0]
        assert "Bash(claude:*)" not in start_lines[0]
        assert "allowed_tools=default" in start_lines[0]

    def test_start_log_marks_custom_allowed_tools_without_printing_them(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], env_overrides={"MONOLYNX_SPRINT_TICK_ALLOWED_TOOLS": "Read"})

        assert result.returncode == 0
        log_path = sandbox.git_dir / "monolynx" / "sprint-run.log"
        start_lines = [line for line in log_path.read_text().splitlines() if "start:" in line]
        assert start_lines
        assert "Read" not in start_lines[0]
        assert "allowed_tools=custom" in start_lines[0]


# ---------------------------------------------------------------------------
# MON-185: wybor runtime dyspozytora (MONOLYNX_SPRINT_RUNTIME=claude|codex).
# Fake `codex` na wzor fake `claude` juz istniejacego w tym pliku -- osobna
# funkcja, zeby NIE dotykac `_write_claude`/CLAUDE_SCRIPT uzywanych przez
# wszystkie istniejace (zielone) testy powyzej. Kazdy istniejacy test dziala
# bez MONOLYNX_SPRINT_RUNTIME, wiec sprawdza rownolegle, ze domyslna wartoscia
# jest "claude" (brak regresji).
# ---------------------------------------------------------------------------

CODEX_SCRIPT = """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  echo "${FAKE_CODEX_VERSION_OUTPUT:-codex-cli 0.155.1}"
  exit 0
fi
# Ksztalt odwzorowuje realne przechwycenie `codex exec` bez --json (MON-185 code
# review, krytyk, DRUGI przeglad: nie wystarczy przeniesc jedna linie na stderr --
# fakebin ma odtwarzac KSZTALT obu strumieni z tests/fixtures/codex/
# exec-plain.0.155.1.{stdout,stderr}.txt, nie tylko sama linie statusu). Literaly
# ponizej (klucze naglowka, ANSI, linie hook:) NIE sa kopia bajt-w-bajt fixture'u --
# tresc jest atrapa sterowana FAKE_STATUS jak reszta tego skryptu -- ale odwzorowuja
# jego ksztalt: obecnosc kodow ANSI, obecnosc naglowka sesji (workdir/model/sandbox/
# session id) i TO, ze finalna odpowiedz pojawia sie na stderr TAKZE (nie tylko na
# stdout) -- to wlasne ustalenie z TestCodexPlainStatusLineFixtureContract nizej:
# stderr SAM w sobie daje jedno dopasowanie grep na realnym przechwyceniu, bo codex
# renderuje finalna odpowiedz na oba strumienie, nie tylko przez echo promptu. Dzieki
# temu asercja o `tail -1` przy `2>&1` (w sprint_run.sh) przestaje zalezec od atrapy
# innej niz realny ksztalt.
printf '\033[1mworkdir:\033[0m /fake/workdir\n' >&2
printf '\033[1mmodel:\033[0m fake-model\n' >&2
printf '\033[1mprovider:\033[0m openai\n' >&2
printf '\033[1msandbox:\033[0m read-only\n' >&2
printf '\033[1msession id:\033[0m 00000000-0000-0000-0000-000000000000\n' >&2
printf -- '--------\n' >&2
printf '\033[1mhook:\033[0m \033[2mUserPromptSubmit\033[0m\n' >&2
printf '\033[1mhook:\033[0m \033[2mUserPromptSubmit\033[0m Completed\n' >&2
echo "raport dzialania agenta w tle (codex)" >&2
if [ -n "${FAKE_STATUS:-}" ] && [ -f "${FAKE_STATUS:-}" ]; then
  # Finalna odpowiedz idzie na OBA strumienie -- tak jak w realnym przechwyceniu
  # (patrz komentarz WHY wyzej): stdout jest zrodlem prawdy dla samego skryptu,
  # stderr jest tu wylacznie po to, zeby test kontraktu tail -1 (sprint_run.sh
  # laczy 2>&1) opieral sie na ksztalcie zblizonym do prawdziwego, nie na atrapie
  # pokazujacej status wylacznie na jednym strumieniu.
  cat "$FAKE_STATUS" >&2
  cat "$FAKE_STATUS"
fi
if [ -n "${FAKE_LOG:-}" ]; then
  printf 'ARGS[codex]: %s\\n' "$*" >> "$FAKE_LOG"
fi
if [ -n "${FAKE_ENV_DUMP:-}" ]; then
  env | grep '^MONOLYNX_\\|^CLAUDE_CODE_EXPERIMENTAL' > "$FAKE_ENV_DUMP" || true
fi
exit 0
"""

# Wariant CLAUDE_SCRIPT z prefiksem ARGS[claude]: (istniejacy CLAUDE_SCRIPT w tym
# pliku pisze goly "ARGS: ..." -- ten runtime-owy wariant jest uzyty TYLKO przez
# nowe testy w tej sekcji, przez osobna fixture ponizej, zeby nie zmienic
# zachowania widocznego dla `arg_lines()` w istniejacych (zielonych) testach).
CLAUDE_SCRIPT_RUNTIME = """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  echo "${FAKE_CLAUDE_VERSION_OUTPUT:-2.1.270 (Claude Code)}"
  exit 0
fi
echo "raport dzialania agenta w tle"
if [ -n "${FAKE_STATUS:-}" ] && [ -f "${FAKE_STATUS:-}" ]; then
  cat "$FAKE_STATUS"
fi
if [ -n "${FAKE_LOG:-}" ]; then
  printf 'ARGS[claude]: %s\\n' "$*" >> "$FAKE_LOG"
fi
if [ -n "${FAKE_ENV_DUMP:-}" ]; then
  env | grep '^MONOLYNX_\\|^CLAUDE_CODE_EXPERIMENTAL' > "$FAKE_ENV_DUMP" || true
fi
exit 0
"""


def _write_runtime_binaries(sandbox, include_claude=True, include_codex=True):
    if include_claude:
        claude_script = sandbox.fakebin / "claude"
        claude_script.write_text(CLAUDE_SCRIPT_RUNTIME)
        claude_script.chmod(0o755)
    if include_codex:
        codex_script = sandbox.fakebin / "codex"
        codex_script.write_text(CODEX_SCRIPT)
        codex_script.chmod(0o755)


def run_script_runtime(sandbox, args, env_overrides=None, timeout=30, slug="testslug", include_claude=True, include_codex=True):
    """Jak run_script(), ale z osobnym zestawem fakebin (ARGS[claude]:/ARGS[codex]:
    prefiksowane), zeby jednoznacznie odroznic ktory runtime zostal wywolany."""
    _write_runtime_binaries(sandbox, include_claude=include_claude, include_codex=include_codex)
    env = _base_env(sandbox, slug, env_overrides)
    return subprocess.run(
        ["bash", str(SCRIPT), "--interval", "0", *args],
        cwd=str(sandbox.project_dir),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def runtime_calls(sandbox, prefix):
    if not sandbox.fake_log.exists():
        return []
    marker = f"ARGS[{prefix}]:"
    return [line for line in sandbox.fake_log.read_text().splitlines() if line.startswith(marker)]


@pytest.mark.unit
class TestSprintRunRuntimeSelection:
    def test_default_runtime_without_env_var_invokes_claude(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script_runtime(sandbox, [])

        assert result.returncode == 0
        assert len(runtime_calls(sandbox, "claude")) == 1
        assert runtime_calls(sandbox, "codex") == []

    def test_runtime_codex_invokes_codex_not_claude(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script_runtime(sandbox, [], env_overrides={"MONOLYNX_SPRINT_RUNTIME": "codex"})

        assert result.returncode == 0, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert len(runtime_calls(sandbox, "codex")) == 1
        assert runtime_calls(sandbox, "claude") == []

    def test_runtime_codex_still_parses_status_line_and_finishes(self, sandbox):
        """Kontrakt statusu SPRINT-RUN: remaining=... running=... waiting=... jest
        WSPOLNY dla obu runtime (jeden algorytm dyspozytora) -- petla konczy sie
        tak samo, niezaleznie ktory proces wypisal te linie."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=1 done=2")

        result = run_script_runtime(sandbox, [], env_overrides={"MONOLYNX_SPRINT_RUNTIME": "codex"})

        assert result.returncode == 4

    def test_runtime_codex_missing_binary_exits_69(self, sandbox):
        """Fakebin ma tylko `claude` (bez `codex`) -- wybor runtime=codex ma dac
        ten sam kod 69 co dzisiejszy brak `claude` (test_claude_missing_from_path_exits_69),
        nie przejsc cicho po prostu uzywajac claude."""
        result = run_script_runtime(sandbox, [], env_overrides={"MONOLYNX_SPRINT_RUNTIME": "codex"}, include_codex=False)

        assert result.returncode == 69

    def test_runtime_claude_explicit_missing_binary_exits_69(self, sandbox):
        result = run_script_runtime(sandbox, [], env_overrides={"MONOLYNX_SPRINT_RUNTIME": "claude"}, include_claude=False)

        assert result.returncode == 69


@pytest.mark.unit
class TestSprintRunCleanupOwnResourcesOnly:
    """Przerwanie petli (SIGTERM) sprzata WYLACZNIE wlasny PID_FILE -- nigdy cudze
    zasoby (inny plik w tym samym STATE_DIR, np. rejestr innej sesji/dyspozytora)."""

    def test_sigterm_removes_only_own_pid_file_leaves_foreign_file_untouched(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=1 running=1 waiting=0 done=0")
        claude_script = sandbox.fakebin / "claude"
        claude_script.write_text(
            "#!/usr/bin/env bash\n"
            'sleep "${FAKE_CLAUDE_SLEEP:-5}"\n'
            'echo "raport dzialania agenta w tle"\n'
            'if [ -n "${FAKE_STATUS:-}" ] && [ -f "${FAKE_STATUS:-}" ]; then cat "$FAKE_STATUS"; fi\n'
            "exit 0\n"
        )
        claude_script.chmod(0o755)

        state_dir = sandbox.git_dir / "monolynx"
        state_dir.mkdir(parents=True)
        foreign_file = state_dir / "other-dispatcher.pid"
        foreign_file.write_text("999999")

        env = _base_env(sandbox, "testslug", None)
        proc = subprocess.Popen(
            ["bash", str(SCRIPT), "--interval", "0"],
            cwd=str(sandbox.project_dir),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        pid_file = state_dir / "sprint-run.pid"
        try:
            for _ in range(100):
                if pid_file.exists():
                    break
                time.sleep(0.1)
            assert pid_file.exists(), "PID_FILE nie powstal w czasie -- sprawdz sciezke STATE_DIR"

            proc.send_signal(signal.SIGTERM)
            proc.wait(timeout=20)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=10)

        assert not pid_file.exists(), "wlasny PID_FILE ma byc usuniety po SIGTERM"
        assert foreign_file.exists(), "cudzy plik w tym samym STATE_DIR nie moze zostac dotkniety"
        assert foreign_file.read_text() == "999999"


# ---------------------------------------------------------------------------
# MON-185 code review (krytyk, sprint_run.sh:176-186, bez odjecia punktow ale
# realne): galaz `codex` w run_agent() nie przekierowywala stdin. `codex exec`
# czyta stdin, gdy nie jest terminalem -- potwierdzone przechwyceniem
# (tests/fixtures/codex/exec-plain.0.155.1.stderr.txt: linia
# "Reading additional input from stdin..." pojawia sie NAWET przy < /dev/null).
# Tick odpalony ze stdin bedacym otwartym pipe (typowe dla `claude --bg`
# uruchamiajacego pipe'y albo dla `/loop` w sesji interaktywnej) zawiesza sie
# bez zadnego wyjscia. `_spawn_codex` w adapterze (sprint_run_backend.py) juz
# robi to poprawnie (stdin=subprocess.DEVNULL) -- petla nie.
# ---------------------------------------------------------------------------

CODEX_STDIN_PROBE_SCRIPT = """#!/usr/bin/env bash
if [ "${1:-}" = "--version" ]; then
  echo "${FAKE_CODEX_VERSION_OUTPUT:-codex-cli 0.155.1}"
  exit 0
fi
if [ -n "${FAKE_STDIN_PROBE:-}" ]; then
  # WHY czas, nie kod wyjscia `read -t`: zweryfikowane empirycznie (MON-185) -- w tym
  # bashu `read -t 2` zwraca rc=1 ROWNIEZ przy realnym timeout (nie tylko przy EOF),
  # wiec kod wyjscia NIE odroznia "dostalem EOF natychmiast" od "czekalem 2s i
  # przekroczylem limit". Jedynym niezawodnym sygnalem jest zmierzony czas.
  start_ns=$(date +%s%N)
  IFS= read -r -t 2 _line
  end_ns=$(date +%s%N)
  elapsed_ms=$(( (end_ns - start_ns) / 1000000 ))
  if [ "$elapsed_ms" -ge 1500 ]; then
    echo "TIMEOUT elapsed_ms=$elapsed_ms" > "$FAKE_STDIN_PROBE"
  else
    echo "EOF elapsed_ms=$elapsed_ms" > "$FAKE_STDIN_PROBE"
  fi
fi
if [ -n "${FAKE_STATUS:-}" ] && [ -f "${FAKE_STATUS:-}" ]; then
  cat "$FAKE_STATUS"
fi
exit 0
"""


@pytest.fixture
def stdin_probe_sandbox(tmp_path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    git_dir = project_dir / ".git"

    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    git_script = fakebin / "git"
    git_script.write_text(GIT_SCRIPT_TEMPLATE.format(git_dir=git_dir, project_dir=project_dir))
    git_script.chmod(0o755)
    codex_script = fakebin / "codex"
    codex_script.write_text(CODEX_STDIN_PROBE_SCRIPT)
    codex_script.chmod(0o755)

    fake_status = tmp_path / "fake_status.txt"
    fake_status.write_text("SPRINT-RUN: remaining=0 running=0 waiting=0 done=0\n")

    return SimpleNamespace(
        tmp_path=tmp_path,
        project_dir=project_dir,
        fakebin=fakebin,
        fake_status=fake_status,
        probe_file=tmp_path / "stdin_probe.txt",
    )


@pytest.mark.unit
class TestCodexExecStdinRedirection:
    """Test deterministyczny (nie zejscie do asercji na tresci zrodla): fake `codex`
    probuje przeczytac jedna linie ze swojego stdin z timeoutem 2s (`read -t 2`) i
    mierzy zmierzony CZAS (nie kod wyjscia -- zweryfikowane empirycznie, ze `read -t`
    zwraca rc=1 zarowno przy natychmiastowym EOF, jak i przy realnym przekroczeniu
    timeoutu, wiec sam kod wyjscia nie odroznia tych przypadkow, patrz komentarz WHY
    przy CODEX_STDIN_PROBE_SCRIPT). Proces Pythona trzyma otwarty pipe jako stdin
    calego `bash sprint_run.sh` (nigdy nie zamykany ani nie zapisywany w trakcie
    testu). Jesli `codex exec` w skrypcie odziedziczy ten pipe (brak jawnego
    `< /dev/null` na jego wywolaniu), `read -t 2` zawiesi sie na pelne ~2s (TIMEOUT).
    Jesli `codex` dostanie /dev/null (poprawka `< /dev/null` zastosowana), `read`
    zwroci EOF niemal natychmiast (<1.5s) -- niezaleznie od tego, co siedzi w
    faktycznym stdin calej petli."""

    def test_codex_command_does_not_inherit_open_stdin_pipe(self, stdin_probe_sandbox):
        sandbox = stdin_probe_sandbox
        env = {
            "PATH": f"{sandbox.fakebin}:/usr/local/bin:/usr/bin:/bin",
            "MONOLYNX_PROJECT_SLUG": "testslug",
            "MONOLYNX_SPRINT_RUNTIME": "codex",
            "FAKE_STATUS": str(sandbox.fake_status),
            "FAKE_STDIN_PROBE": str(sandbox.probe_file),
        }
        proc = subprocess.Popen(
            ["bash", str(SCRIPT), "--interval", "0", "--once"],
            cwd=str(sandbox.project_dir),
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            proc.wait(timeout=15)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            if proc.stdin:
                proc.stdin.close()

        assert sandbox.probe_file.exists(), (
            f"fake codex sie nie odpalil w ogole -- returncode={proc.returncode} stderr={proc.stderr.read() if proc.stderr else None!r}"
        )
        probe_result = sandbox.probe_file.read_text().strip()
        assert probe_result.startswith("EOF"), (
            f"oczekiwano EOF (stdin jawnie przekierowane na /dev/null przy wywolaniu "
            f"`codex exec`), dostano {probe_result!r} -- brak `< /dev/null` pozwala mu "
            f"odziedziczyc otwarty stdin petli, co w realnym uzyciu (np. `/loop`) "
            f"zawiesza tick bez zadnego wyjscia"
        )


# ---------------------------------------------------------------------------
# MON-185 code review (krytyk): kontrakt wyciagania linii SPRINT-RUN musi byc
# oparty na PRZECHWYCENIU, nie na wymyslonym formacie (.claude/rules/
# external-tool-output.md pkt 1). Fixture:
# tests/fixtures/codex/exec-plain.0.155.1.{stdout,stderr}.txt -- realne
# `codex exec` BEZ --json (sciezka uzywana przez ta petle), patrz README w tym
# katalogu dla pelnej proweniencji (komenda, wersja, data, kod wyjscia).
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "codex"
PLAIN_STDOUT = FIXTURES_DIR / "exec-plain.0.155.1.stdout.txt"
PLAIN_STDERR = FIXTURES_DIR / "exec-plain.0.155.1.stderr.txt"

assert PLAIN_STDOUT.exists() and PLAIN_STDERR.exists(), (
    "brak fixture'ow realnego przechwycenia `codex exec` bez --json -- patrz tests/fixtures/codex/README.md"
)


def _status_line_grep_pattern():
    """Wyciaga doslowne wyrazenie -E z linii `grep -E '...'` w sprint_run.sh, zeby test
    byl spiety z REALNYM, aktualnym kontraktem skryptu, a nie z recznie skopiowana
    kopia wzorca, ktora moglaby sie z nim rozjechac przy zmianie."""
    match = re.search(r"grep -E '([^']+)'", SCRIPT.read_text())
    assert match, "sprint_run.sh nie zawiera oczekiwanego wzorca `grep -E '...'` do wyciagniecia linii statusu"
    return match.group(1)


@pytest.mark.unit
class TestCodexPlainStatusLineFixtureContract:
    def test_status_line_extractable_from_stdout_without_stripping_ansi(self):
        """Fixture nie zawiera zadnych kodow ANSI (patrz README) -- to dowodzi, ze skrypt
        NIE musi zdejmowac ANSI przed `grep`, w przeciwienstwie do renderu TUI `claude
        logs` (pkt 4 reguly external-tool-output.md). Surowe bajty stdout wystarczaja."""
        pattern = _status_line_grep_pattern()
        result = subprocess.run(
            ["bash", "-c", f"grep -E '{pattern}' | tail -1"],
            input=PLAIN_STDOUT.read_bytes(),
            capture_output=True,
            timeout=10,
        )
        assert result.stdout.decode() == "SPRINT-RUN: remaining=2 running=1 waiting=0 done=3\n"

    def test_stderr_alone_also_matches_because_codex_renders_final_answer_there_too(self):
        """Dokumentujemy fakt zaobserwowany w przechwyceniu, nie zakladany: stderr SAM w
        sobie DAJE jedno dopasowanie tego samego wyrazenia -- nie przez echo promptu
        (linia promptu zaczyna sie od "Reply with...", wiec kotwica ^ jej nie lapie), tylko
        dlatego, ze codex renderuje finalna odpowiedz agenta takze na stderr (TUI). To NIE
        czyni stderr samego w sobie wiarygodnym zrodlem statusu dla skryptu -- skrypt nigdy
        nie parsuje samego stderr, tylko polaczony strumien `2>&1` + `tail -1`
        (test_combined_stream_needs_tail_for_determinism nizej)."""
        pattern = _status_line_grep_pattern()
        result = subprocess.run(
            ["bash", "-c", f"grep -cE '{pattern}'"],
            input=PLAIN_STDERR.read_bytes(),
            capture_output=True,
            timeout=10,
        )
        count = int(result.stdout.decode().strip() or "0")
        assert count == 1, (
            f"oczekiwano dokladnie 1 dopasowania w samym stderr (echo finalnej odpowiedzi), "
            f"dostano {count} -- jesli to sie zmienilo, zrewiduj rowniez uzasadnienie tail -1 "
            f"w test_combined_stream_needs_tail_for_determinism"
        )

    def test_combined_stream_needs_tail_for_determinism(self):
        """sprint_run.sh laczy strumienie przez `2>&1` (patrz run_agent() w skrypcie) --
        na polaczonym, realnym przechwyceniu grep daje WIECEJ NIZ JEDNA pasujaca linie
        (stdout + echo na stderr, patrz test wyzej), wiec bez `tail -1` `status_line` w
        skrypcie bylby wielowierszowy, co psuje dalsze parsowanie pol
        (remaining=/running=/waiting=/done=). `tail -1` redukuje do jednej linii -- test
        dowodzi, ze to konieczne, nie kosmetyczne."""
        pattern = _status_line_grep_pattern()
        combined = PLAIN_STDOUT.read_bytes() + PLAIN_STDERR.read_bytes()

        without_tail = subprocess.run(
            ["bash", "-c", f"grep -E '{pattern}'"],
            input=combined,
            capture_output=True,
            timeout=10,
        )
        matches = [line for line in without_tail.stdout.decode().splitlines() if line]
        assert len(matches) >= 2, (
            f"oczekiwano co najmniej 2 dopasowan na polaczonym strumieniu (stdout + render "
            f"stderr), dostano {matches!r} -- bez tego `tail -1` w skrypcie jest niepotrzebny "
            f"i test powinien to odzwierciedlic"
        )

        with_tail = subprocess.run(
            ["bash", "-c", f"grep -E '{pattern}' | tail -1"],
            input=combined,
            capture_output=True,
            timeout=10,
        )
        assert with_tail.stdout.decode() == "SPRINT-RUN: remaining=2 running=1 waiting=0 done=3\n"


# ---------------------------------------------------------------------------
# MON-222: sprint z samymi ticketami needs-local i sesja waiting nie jest
# "wykonany" (remaining>0, running=0, waiting>0), a petla mowi to glosno; limit
# sesji waiting MONOLYNX_SPRINT_MAX_WAITING widoczny w logu startu i w env ticku.
# ---------------------------------------------------------------------------


def _start_line(sandbox):
    log_path = sandbox.git_dir / "monolynx" / "sprint-run.log"
    lines = [line for line in log_path.read_text().splitlines() if "start:" in line]
    assert lines, "brak linii start: w logu petli"
    return lines[0]


@pytest.mark.unit
class TestSprintRunNothingRunningWithRemainingWork:
    def test_remaining_work_with_only_waiting_session_is_not_treated_as_finished(self, sandbox):
        """Dwa tickety needs-local (remaining=2, nikt ich nie startuje w tle) i
        jedna sesja waiting: petla nie moze oglosic sprintu wykonanym (kod 0) ani
        zakonczyc sie kodem 4 (tylko waiting) - biegnie do bezpiecznika
        --max-ticks (kod 3)."""
        write_status(sandbox, "SPRINT-RUN: remaining=2 running=0 waiting=1 done=3")

        result = run_script(sandbox, ["--max-ticks", "2"])

        assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert len(arg_lines(sandbox)) == 2
        assert "sprint wykonany" not in result.stdout

    def test_remaining_work_with_nothing_running_logs_explicit_warning(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=2 running=0 waiting=1 done=3")

        result = run_script(sandbox, ["--max-ticks", "2"])

        assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        warning_lines = [line for line in result.stdout.splitlines() if "nic nie biegnie" in line]
        assert warning_lines, f"brak ostrzezenia o tym, ze nic nie biegnie: {result.stdout!r}"
        log_text = (sandbox.git_dir / "monolynx" / "sprint-run.log").read_text()
        assert "nic nie biegnie" in log_text

    def test_no_nothing_running_warning_while_a_session_is_running(self, sandbox):
        """Ostrzezenie tylko gdy running=0 - przy pracujacej sesji petla robi
        to, co ma robic, i nie krzyczy."""
        write_status(sandbox, "SPRINT-RUN: remaining=2 running=1 waiting=1 done=0")

        result = run_script(sandbox, ["--max-ticks", "2"])

        assert result.returncode == 3, f"stdout={result.stdout!r} stderr={result.stderr!r}"
        assert "nic nie biegnie" not in result.stdout

    def test_only_waiting_and_no_remaining_still_exits_4_without_nothing_running_warning(self, sandbox):
        """Regresja: istniejacy kontrakt (remaining=0 running=0 waiting>0 -> kod 4)
        zostaje, a ostrzezenie 'nic nie biegnie' dotyczy wylacznie remaining>0."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=1 done=2")

        result = run_script(sandbox, [])

        assert result.returncode == 4
        assert "nic nie biegnie" not in result.stdout


@pytest.mark.unit
class TestSprintRunMaxWaiting:
    def test_start_log_reports_default_max_waiting_of_two(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        assert "max_waiting=2" in _start_line(sandbox)

    def test_start_log_reports_max_waiting_from_environment(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], env_overrides={"MONOLYNX_SPRINT_MAX_WAITING": "5"})

        assert result.returncode == 0
        start_line = _start_line(sandbox)
        assert "max_waiting=5" in start_line
        assert "max_waiting=2" not in start_line

    def test_max_waiting_is_exported_to_the_tick_process(self, sandbox):
        """Tick czyta limit z env (preflight -> MAX_WAITING), wiec petla musi go
        przekazac dalej, takze gdy uzytkownik go nie ustawil (domyslne 2)."""
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [])

        assert result.returncode == 0
        assert "MONOLYNX_SPRINT_MAX_WAITING=2" in sandbox.fake_env_dump.read_text().splitlines()

    def test_external_max_waiting_override_is_preserved_in_tick_env(self, sandbox):
        write_status(sandbox, "SPRINT-RUN: remaining=0 running=0 waiting=0 done=0")

        result = run_script(sandbox, [], env_overrides={"MONOLYNX_SPRINT_MAX_WAITING": "5"})

        assert result.returncode == 0
        assert "MONOLYNX_SPRINT_MAX_WAITING=5" in sandbox.fake_env_dump.read_text().splitlines()
