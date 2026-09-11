"""Testy hooka plugin/hooks/brief_guard.py -- UserPromptSubmit, tryby auto / block / off.

Hook wykonuje logike na poziomie modulu, wiec testujemy go jako subprocess ze
stdin/stdout/stderr, tak jak wywoluje go Claude Code. HOME wskazuje na tmp_path,
zeby marker sesji nie wyciekal miedzy testami ani do prawdziwego ~/.cache.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

HOOK_PATH = Path(__file__).resolve().parents[2] / "plugin" / "hooks" / "brief_guard.py"
SKILL_PATH = Path(__file__).resolve().parents[2] / "plugin" / "skills" / "brief" / "SKILL.md"

assert HOOK_PATH.exists(), f"Brak pliku hooka: {HOOK_PATH} -- sprawdz obraz Docker / checkout repo."
assert SKILL_PATH.exists(), f"Brak skilla brief: {SKILL_PATH} -- hook w trybie auto go wstrzykuje."


def run_hook(payload, home: Path, mode=None):
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(home)}
    if mode is not None:
        env["MONOLYNX_BRIEF_GUARD"] = mode
    stdin_data = payload if isinstance(payload, str) else json.dumps(payload)
    result = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin_data,
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    context = None
    if result.stdout.strip():
        context = json.loads(result.stdout)["hookSpecificOutput"]["additionalContext"]
    return result.returncode, result.stderr, context


def prompt_payload(prompt, session_id="sess-1"):
    return {"hook_event_name": "UserPromptSubmit", "session_id": session_id, "prompt": prompt}


def assert_silent(result):
    code, err, context = result
    assert code == 0
    assert err == ""
    assert context is None


@pytest.mark.unit
class TestAutoModeInjectsBrief:
    def test_bare_polish_imperative_gets_skill_context(self, tmp_path):
        code, err, context = run_hook(prompt_payload("napraw build na CI"), tmp_path)
        assert code == 0
        assert err == ""
        assert context is not None
        assert "/monolynx:brief napraw build na CI" in context
        assert "## KROK 3: Zaproponuj kontrakt" in context  # tresc SKILL.md, nie tylko polecenie
        assert not context.lstrip().startswith("---")  # frontmatter usuniety

    def test_auto_is_default_mode(self, tmp_path):
        _, _, without_env = run_hook(prompt_payload("napraw build"), tmp_path / "a")
        _, _, explicit = run_hook(prompt_payload("napraw build"), tmp_path / "b", mode="auto")
        assert without_env is not None
        assert explicit is not None

    @pytest.mark.parametrize(
        "prompt",
        [
            "fix the worker healthcheck",
            "sprawdź czemu worker pada",
            "W pliku services/auth.py dodaj cache na tokeny",
            "ogarnij CI",
            "Zrób tak, żeby build przechodził",
            "na branchu sprint-12 zaktualizuj CLAUDE.md",
        ],
        ids=["english", "diacritics", "verb_after_context", "ogarnij", "capitalized", "verb_after_branch_note"],
    )
    def test_various_imperatives_are_detected(self, tmp_path, prompt):
        _, _, context = run_hook(prompt_payload(prompt), tmp_path)
        assert context is not None, prompt


@pytest.mark.unit
class TestBlockMode:
    def test_bare_imperative_is_blocked_with_brief_hint(self, tmp_path):
        code, err, context = run_hook(prompt_payload("napraw build na CI"), tmp_path, mode="block")
        assert code == 2
        assert "/monolynx:brief napraw build na CI" in err
        assert context is None

    def test_second_prompt_in_same_session_passes(self, tmp_path):
        first, _, _ = run_hook(prompt_payload("napraw build"), tmp_path, mode="block")
        assert first == 2
        assert_silent(run_hook(prompt_payload("napraw build"), tmp_path, mode="block"))


@pytest.mark.unit
class TestPasses:
    def test_second_prompt_in_same_session_is_silent(self, tmp_path):
        run_hook(prompt_payload("napraw build"), tmp_path)
        assert_silent(run_hook(prompt_payload("dodaj test"), tmp_path))

    def test_new_session_is_evaluated_again(self, tmp_path):
        run_hook(prompt_payload("napraw build", session_id="a"), tmp_path)
        _, _, context = run_hook(prompt_payload("napraw build", session_id="b"), tmp_path)
        assert context is not None

    @pytest.mark.parametrize(
        "prompt",
        [
            "/monolynx:brief napraw build",
            "zrob MON-12",
            "napraw build. Gotowe = pipeline zielony. Nie ruszaj Dockerfile. Pytaj przed pushem.",
            "napraw " + "x" * 250,
            "czemu worker pada po restarcie?",
            "co robimy dzisiaj?",
            "hej, jestem z powrotem",
            "chcialbym porozmawiac o architekturze modulu wiki",
            "test_brief_guard.py ma 14 testow",  # 'test' jako czesc nazwy pliku, nie czasownik
        ],
        ids=["slash", "ticket_key", "contract_words", "too_long", "question", "question_short", "greeting", "no_verb", "verb_inside_identifier"],
    )
    def test_non_bare_prompts_pass(self, tmp_path, prompt):
        assert_silent(run_hook(prompt_payload(prompt), tmp_path))

    @pytest.mark.parametrize("mode", ["off", "false", "0", "OFF"])
    def test_env_off_passes(self, tmp_path, mode):
        assert_silent(run_hook(prompt_payload("napraw build"), tmp_path, mode=mode))

    def test_malformed_json_passes(self, tmp_path):
        assert_silent(run_hook("{not json", tmp_path))

    def test_missing_session_id_passes(self, tmp_path):
        assert_silent(run_hook({"prompt": "napraw build"}, tmp_path))


@pytest.mark.unit
def test_marker_is_written_under_home_cache(tmp_path):
    run_hook(prompt_payload("napraw build", session_id="abc"), tmp_path)
    assert (tmp_path / ".cache" / "monolynx" / "brief-guard" / "abc").exists()
