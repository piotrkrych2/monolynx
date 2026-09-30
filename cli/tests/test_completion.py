"""Testy komendy `monolynx completion <shell>` (MON-141).

Kontrakt: skrypt completion trafia na stdout i jest generowany dla nazwy, pod którą
wywołano program (`monolynx` -> `_MONOLYNX_COMPLETE`, `mnx` -> `_MNX_COMPLETE`, inna nazwa
-> skrypt dla `monolynx`). Nieznana powłoka -> `usage_error`: komunikat na stderr, kod 2,
pusty stdout. Komenda nie wymaga konfiguracji ani tokenu.
"""

from __future__ import annotations

import re

import pytest
from monolynx_cli.main import app

pytestmark = pytest.mark.unit

SHELLS = ["bash", "zsh", "fish"]
_SHELL_MARKER = {"bash": "complete", "zsh": "compdef", "fish": "complete"}
_WIDE = {"COLUMNS": "200", "TERMINAL_WIDTH": "200"}


@pytest.mark.parametrize("shell", SHELLS)
def test_completion_default_prog_prints_script_for_monolynx(runner, shell):
    result = runner.invoke(app, ["completion", shell], prog_name="monolynx")

    assert result.exit_code == 0
    assert result.stdout.strip() != ""
    assert "_MONOLYNX_COMPLETE" in result.stdout
    assert _SHELL_MARKER[shell] in result.stdout
    assert "_MNX_COMPLETE" not in result.stdout


@pytest.mark.parametrize("shell", SHELLS)
def test_completion_invoked_as_mnx_prints_script_for_mnx(runner, shell):
    result = runner.invoke(app, ["completion", shell], prog_name="mnx")

    assert result.exit_code == 0
    assert "_MNX_COMPLETE" in result.stdout
    assert _SHELL_MARKER[shell] in result.stdout
    assert "_MONOLYNX_COMPLETE" not in result.stdout


@pytest.mark.parametrize("shell", SHELLS)
def test_completion_unknown_prog_name_falls_back_to_monolynx_script(runner, shell):
    result = runner.invoke(app, ["completion", shell], prog_name="jakis-inny-program")

    assert result.exit_code == 0
    assert "_MONOLYNX_COMPLETE" in result.stdout
    assert "_JAKIS_INNY_PROGRAM_COMPLETE" not in result.stdout


def test_completion_scripts_differ_between_shells(runner):
    outputs = {shell: runner.invoke(app, ["completion", shell], prog_name="monolynx").stdout for shell in SHELLS}

    assert len(set(outputs.values())) == len(SHELLS)
    assert "compdef" in outputs["zsh"]
    assert "compdef" not in outputs["bash"]
    assert "compdef" not in outputs["fish"]


def test_completion_unknown_shell_exits_2_with_message_on_stderr(runner):
    result = runner.invoke(app, ["completion", "powershell-xyz"], prog_name="monolynx")

    assert result.exit_code == 2
    assert result.stderr.strip() != ""
    assert result.stdout == ""
    assert "_MONOLYNX_COMPLETE" not in result.stderr


def test_completion_unknown_shell_is_controlled_exit_not_crash(runner):
    result = runner.invoke(app, ["completion", "powershell-xyz"], prog_name="monolynx")

    assert isinstance(result.exception, SystemExit)


@pytest.mark.parametrize("shell", SHELLS)
def test_completion_works_without_config_and_token(runner, monkeypatch, isolated_config, shell):
    for name in ("MONOLYNX_TOKEN", "MONOLYNX_ENDPOINT", "MONOLYNX_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    assert not isolated_config.exists()

    result = runner.invoke(app, ["completion", shell], prog_name="monolynx")

    assert result.exit_code == 0
    assert "_MONOLYNX_COMPLETE" in result.stdout
    assert not isolated_config.exists()


def test_completion_help_exits_0_and_mentions_shells(runner):
    result = runner.invoke(app, ["completion", "--help"], env=_WIDE)

    assert result.exit_code == 0
    assert "completion" in result.stdout.lower()
    for shell in SHELLS:
        assert shell in result.stdout


def test_root_help_lists_completion_command_row(runner):
    # Podciag "completion" wystepuje takze we flagach --install-completion/--show-completion,
    # wiec szukamy wiersza komendy: linia zaczynajaca sie (po ramce rich) od samego "completion".
    result = runner.invoke(app, ["--help"], env=_WIDE)

    assert result.exit_code == 0
    assert re.search(r"^[\s│|]*completion\s", result.stdout, flags=re.MULTILINE)
