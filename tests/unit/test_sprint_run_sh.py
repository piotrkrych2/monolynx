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
import subprocess
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
        assert "--name testslug-sprint-end" in calls[1]
        assert "--name testslug-retro" in calls[2]
