"""Blok `shared:transport` w skillach pluginu i jego skrypty (MON-232).

Kontrakt: kanoniczna tresc bloku to caly plik `plugin/references/transport.md`. Kazdy
`plugin/skills/*/SKILL.md`, ktory wola toole platformy (zawiera `mcp__monolynx__`), ma dokladnie
jedna pare znacznikow `<!-- shared:transport -->` ... `<!-- /shared:transport -->` z trescia
rowna kanonicznej (po normalizacji bialych znakow), taka sama kopia w
`src/monolynx/static/skills/monolynx-<skill>/SKILL.md` (z `<PROJECT_SLUG>` -> `<PROJECT-SLUG>`)
oraz `scripts/monolynx_op.py` i `scripts/monolynx_transport.py` bajt w bajt z `plugin/scripts/`
(w katalogu skilla i w kopii standalone). Blok nie lamie kontraktu runtime (zadnych
bezwarunkowych `ToolSearch(`, `$ARGUMENTS`, `${CLAUDE_PLUGIN_ROOT`) i nie ma pauz U+2014/U+2013.

Testy opisuja zawartosc plikow dokumentacji (nie ma tu zachowania do wykonania); zachowanie
skryptow pokrywaja `test_monolynx_transport.py`, `test_monolynx_op.py` i `test_bash_guard.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ROOT / "plugin" / "skills"
STATIC_SKILLS = ROOT / "src" / "monolynx" / "static" / "skills"
CANONICAL = ROOT / "plugin" / "references" / "transport.md"
SCRIPTS = ROOT / "plugin" / "scripts"
TRANSPORT_SCRIPTS = ("monolynx_op.py", "monolynx_transport.py")

OPEN = "<!-- shared:transport -->"
CLOSE = "<!-- /shared:transport -->"
BLOCK_RE = re.compile(re.escape(OPEN) + r"(.*?)" + re.escape(CLOSE), re.DOTALL)

PLATFORM_SKILLS = sorted(path.parent.name for path in SKILLS.glob("*/SKILL.md") if "mcp__monolynx__" in path.read_text(encoding="utf-8"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def canonical_text() -> str:
    assert CANONICAL.is_file(), f"brak {CANONICAL}"
    text = CANONICAL.read_text(encoding="utf-8")
    assert text.strip() != ""
    return text


def test_canonical_transport_reference_exists_and_is_not_empty():
    assert len(canonical_text().strip()) > 200


def test_platform_skills_are_discovered():
    # work, work-simple i sprint-end to skille, ktore na pewno rozmawiaja z platforma.
    assert {"work", "work-simple", "sprint-end", "setup"} <= set(PLATFORM_SKILLS)


@pytest.mark.parametrize("skill", PLATFORM_SKILLS)
def test_skill_has_exactly_one_transport_block_equal_to_canonical(skill):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")

    assert text.count(OPEN) == 1
    assert text.count(CLOSE) == 1
    assert text.index(OPEN) < text.index(CLOSE)
    match = BLOCK_RE.search(text)
    assert match is not None
    assert normalize(match.group(1)) == normalize(canonical_text())


@pytest.mark.parametrize("skill", PLATFORM_SKILLS)
def test_standalone_copy_has_same_transport_block(skill):
    text = (STATIC_SKILLS / f"monolynx-{skill}" / "SKILL.md").read_text(encoding="utf-8")

    assert text.count(OPEN) == 1
    assert text.count(CLOSE) == 1
    match = BLOCK_RE.search(text)
    assert match is not None
    assert normalize(match.group(1)) == normalize(canonical_text().replace("<PROJECT_SLUG>", "<PROJECT-SLUG>"))


@pytest.mark.parametrize("script", TRANSPORT_SCRIPTS)
@pytest.mark.parametrize("skill", PLATFORM_SKILLS)
def test_skill_ships_byte_identical_transport_scripts(skill, script):
    source = (SCRIPTS / script).read_bytes()

    assert (SKILLS / skill / "scripts" / script).read_bytes() == source
    assert (STATIC_SKILLS / f"monolynx-{skill}" / "scripts" / script).read_bytes() == source


@pytest.mark.parametrize("skill", sorted(path.parent.name for path in SKILLS.glob("*/SKILL.md")))
def test_skill_without_platform_calls_has_no_transport_block(skill):
    text = (SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")
    if "mcp__monolynx__" in text:
        pytest.skip("skill z wywolaniami platformy - pokryty testem bloku")

    assert OPEN not in text
    assert CLOSE not in text


class TestCanonicalBlockContent:
    def test_block_has_no_runtime_contract_violations(self):
        text = canonical_text()

        assert "ToolSearch(" not in text
        assert "$ARGUMENTS" not in text
        assert "${CLAUDE_PLUGIN_ROOT" not in text

    @pytest.mark.parametrize("dash", [chr(0x2014), chr(0x2013)], ids=["em-dash", "en-dash"])
    def test_block_has_no_long_dashes(self, dash):
        assert dash not in canonical_text()

    @pytest.mark.parametrize(
        "literal",
        [
            "OP-FALLBACK: mcp tool=",
            "TRANSPORT:",
            "TRANSPORT-HINT:",
            "MONOLYNX_TRANSPORT=mcp",
            "monolynx_op.py",
            "monolynx_transport.py",
            "<katalog skilla>/scripts/",
        ],
    )
    def test_block_contains_contract_literal(self, literal):
        assert literal in canonical_text()

    def test_block_documents_wrapper_exit_codes(self):
        text = canonical_text()

        # Kody wrappera: 3 = fallback na MCP, 2 = blad operacji, 64 = blad uzycia.
        for code in ("3", "2", "64"):
            assert re.search(rf"(?<!\d){code}(?!\d)", text), code


class TestSetupSkillChecksCli:
    def test_setup_lists_cli_item_with_install_login_update_and_allowlist(self):
        # Blok transportu sam zawiera te slowa, wiec sprawdzamy tresc poza nim.
        outside = BLOCK_RE.sub("", (SKILLS / "setup" / "SKILL.md").read_text(encoding="utf-8"))
        lowered = outside.lower()

        cli_lines = [line for line in outside.splitlines() if re.search(r"\bCLI\b", line) and "monolynx" in line.lower()]
        assert cli_lines, "brak pozycji CLI w checkliscie setup"
        for stem in ("zalogowan", "allowlist"):
            assert stem in lowered, stem
