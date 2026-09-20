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
CODEX_FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "codex"


def load_codex_fixture(name):
    with (CODEX_FIXTURES_DIR / name).open(encoding="utf-8") as fh:
        return json.load(fh)


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


@pytest.mark.unit
class TestOptionalFieldsRobustness:
    """Payload pozbawiony pol opcjonalnych nie moze wywrocic hooka ani
    zablokowac promptu z powodu brakujacego pola (MON-184, punkt 4 zakresu)."""

    def test_missing_prompt_key_entirely_passes(self, tmp_path):
        """Nie tylko pusty prompt (juz pokryte przez missing_session_id) --
        payload w ktorym klucza 'prompt' w ogole nie ma."""
        assert_silent(run_hook({"session_id": "sess-no-prompt"}, tmp_path))

    def test_missing_cwd_field_does_not_affect_decision(self, tmp_path):
        """brief_guard.py nie czyta cwd w ogole -- payload bez tego pola
        (jak kazdy istniejacy test w tym pliku) dziala identycznie jak payload
        z cwd jawnie ustawionym."""
        without_cwd = prompt_payload("napraw build na CI", session_id="no-cwd")
        with_cwd = prompt_payload("napraw build na CI", session_id="with-cwd")
        with_cwd["cwd"] = "/workspace"
        _, _, context_without = run_hook(without_cwd, tmp_path / "a")
        _, _, context_with = run_hook(with_cwd, tmp_path / "b")
        assert context_without is not None
        assert context_with is not None

    def test_only_hook_event_name_and_session_id_passes_silently(self, tmp_path):
        """Payload skrajnie ubogi w pola (brak 'prompt') -- odpowiednik
        wywolania hooka dla eventu, ktorego ten skrypt nie obsluguje."""
        assert_silent(run_hook({"hook_event_name": "SessionStart", "session_id": "sess-x"}, tmp_path))


@pytest.mark.unit
class TestCodexClientContract:
    """Kontrakt UserPromptSubmit Codex CLI -- zweryfikowany wobec dokumentacji
    producenta (https://learn.chatgpt.com/docs/hooks, pobrane 2026-09-20;
    cytaty w tests/fixtures/codex/README.md). Codex CLI nie jest zainstalowany
    w tym srodowisku -- fixture opisuje udokumentowany ksztalt JSON, nie
    przechwycony realny output procesu.

    Potwierdzone w dokumentacji (cytat): pole eventu to 'prompt' (jak w
    Claude Code), a blokowanie promptu dziala przez 'exit code 2 i tresc na
    stderr' -- dokladnie to, co juz robi tryb 'block' w brief_guard.py bez
    zadnych zmian. Testy ponizej dowodza, ze pelny udokumentowany ksztalt
    payloadu Codex (z dodatkowymi polami turn_id/model/permission_mode/
    transcript_path, ktorych Claude Code nie wysyla) nie psuje zadnego z
    dwoch trybow."""

    def test_codex_shaped_payload_auto_mode_injects_brief_context(self, tmp_path):
        payload = load_codex_fixture("userpromptsubmit.json")
        payload.pop("_meta")
        code, err, context = run_hook(payload, tmp_path)
        assert code == 0
        assert err == ""
        assert context is not None
        assert "/monolynx:brief napraw build na CI" in context

    def test_codex_shaped_payload_block_mode_exits_2_with_stderr(self, tmp_path):
        """Zgodne z dokumentacja Codex: 'exit code 2 i tresc bledu na stderr'
        blokuje prompt -- ten sam mechanizm co juz uzywa tryb 'block'."""
        payload = load_codex_fixture("userpromptsubmit.json")
        payload.pop("_meta")
        code, err, context = run_hook(payload, tmp_path, mode="block")
        assert code == 2
        assert "/monolynx:brief napraw build na CI" in err
        assert context is None

    def test_codex_extra_fields_do_not_prevent_pass_through_for_question(self, tmp_path):
        """Payload Codex z pytaniem (konczy sie '?') nadal przechodzi cicho,
        mimo obecnosci pol turn_id/model/permission_mode/transcript_path,
        ktorych Claude Code nie wysyla."""
        payload = load_codex_fixture("userpromptsubmit.json")
        payload.pop("_meta")
        payload["prompt"] = "czemu worker pada po restarcie?"
        assert_silent(run_hook(payload, tmp_path))
