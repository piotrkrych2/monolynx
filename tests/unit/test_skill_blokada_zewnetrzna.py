"""Testy TDD dla MON-223: blokada poza zasiegiem agenta konczy ture zamiast kolejnych iteracji krytyka.

Kontrakt (Team Manager):
1. `plugin/skills/work/review-rubric.md` (i kopie) wprowadza znacznik
   `BLOKADA-ZEWNETRZNA` z trzema klasami (srodowisko, uprawnienia, decyzja
   czlowieka) i wymogiem jednozdaniowego powodu. Prog 82 i tabela odjec bez zmian.
2. Wspolny blok `<!-- shared:blokada-zewnetrzna -->` wystepuje dokladnie raz w
   `work/SKILL.md` i dokladnie raz w `work-simple/SKILL.md` (oraz kopiach
   dystrybucyjnych), z identyczna trescia po normalizacji whitespace (ta sama
   normalizacja co `check_shared_blocks` w walidatorze).
3. Regula sesji w tle (work: 10, work-simple: 21) wymienia blokade zewnetrzna
   wsrod przypadkow `SPRINT-RUN STOP`.
4. `plugin/README.md`: `### Unreleased` jako pierwszy naglowek pod `## Changelog`
   z punktem `- MON-223:`; walidator spojnosci przechodzi na repo.

Porownania tekstowe ida przez `_normalize` (bez diakrytykow, lowercase).
"""

import importlib.util
import re
import unicodedata
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

VALIDATOR_PATH = REPO_ROOT / "scripts" / "validate_plugin_consistency.py"
_spec = importlib.util.spec_from_file_location("validate_plugin_consistency", VALIDATOR_PATH)
assert _spec is not None and _spec.loader is not None
validator = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(validator)

WORK_SKILL = REPO_ROOT / "plugin/skills/work/SKILL.md"
WORK_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-work/SKILL.md"
WORK_SIMPLE_SKILL = REPO_ROOT / "plugin/skills/work-simple/SKILL.md"
WORK_SIMPLE_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-work-simple/SKILL.md"
SKILL_PATHS = [WORK_SKILL, WORK_STATIC, WORK_SIMPLE_SKILL, WORK_SIMPLE_STATIC]

RUBRIC_PATHS = [
    REPO_ROOT / "plugin/skills/work/review-rubric.md",
    REPO_ROOT / "plugin/skills/work-simple/review-rubric.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work/review-rubric.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work-simple/review-rubric.md",
]

README = REPO_ROOT / "plugin/README.md"
PLUGIN_JSON_CLAUDE = REPO_ROOT / "plugin/.claude-plugin/plugin.json"

BLOCK_OPEN = "<!-- shared:blokada-zewnetrzna -->"
BLOCK_CLOSE = "<!-- /shared:blokada-zewnetrzna -->"

BACKGROUND_MODE_RULE_CASES = [
    (WORK_SKILL, re.compile(r"(?ms)^10\. \*\*Tryb sesji w tle\*\*.*?(?=^11\. )")),
    (WORK_STATIC, re.compile(r"(?ms)^10\. \*\*Tryb sesji w tle\*\*.*?(?=^11\. )")),
    (WORK_SIMPLE_SKILL, re.compile(r"(?ms)^21\. \*\*Tryb sesji w tle\*\*.*?(?=^22\. )")),
    (WORK_SIMPLE_STATIC, re.compile(r"(?ms)^21\. \*\*Tryb sesji w tle\*\*.*?(?=^22\. )")),
]


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    without_diacritics = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return without_diacritics.lower()


def _blocks(path: Path) -> list[str]:
    """Tresci wszystkich blokow shared:blokada-zewnetrzna w pliku (znormalizowany whitespace)."""
    lines = path.read_text(encoding="utf-8").splitlines()
    found: list[str] = []
    current: list[str] | None = None
    for line in lines:
        stripped = line.strip()
        if stripped == BLOCK_OPEN:
            current = []
        elif stripped == BLOCK_CLOSE:
            assert current is not None, f"{_rel(path)}: znacznik zamykajacy bez otwierajacego."
            found.append(validator._normalize_shared_content("\n".join(current)))
            current = None
        elif current is not None:
            current.append(line)
    assert current is None, f"{_rel(path)}: niezamkniety blok shared:blokada-zewnetrzna."
    return found


def _single_block(path: Path) -> str:
    blocks = _blocks(path)
    assert len(blocks) == 1, f"{_rel(path)}: oczekiwano DOKLADNIE jednego bloku shared:blokada-zewnetrzna, jest {len(blocks)}."
    return blocks[0]


# --- 1. review-rubric.md ---------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("path", RUBRIC_PATHS, ids=_rel)
def test_rubric_defines_external_blocker_marker(path: Path) -> None:
    content = _normalize(path.read_text(encoding="utf-8"))
    assert "blokada-zewnetrzna" in content, f"{_rel(path)}: brak znacznika BLOKADA-ZEWNETRZNA."


@pytest.mark.unit
@pytest.mark.parametrize("path", RUBRIC_PATHS, ids=_rel)
def test_rubric_lists_three_blocker_classes_and_reason(path: Path) -> None:
    content = _normalize(path.read_text(encoding="utf-8"))
    # wszystkie trzy klasy i wymog powodu w tym samym akapicie co znacznik
    marker_at = content.index("blokada-zewnetrzna")
    window = content[marker_at : marker_at + 1500]
    assert "srodowisk" in window, f"{_rel(path)}: brak klasy 'srodowisko' przy znaczniku."
    assert "uprawnien" in window, f"{_rel(path)}: brak klasy 'uprawnienia' przy znaczniku."
    assert "decyzj" in window and "czlowiek" in window, f"{_rel(path)}: brak klasy 'decyzja czlowieka' przy znaczniku."
    assert "powod" in window, f"{_rel(path)}: brak wymogu powodu przy znaczniku."


@pytest.mark.unit
@pytest.mark.parametrize("path", RUBRIC_PATHS, ids=_rel)
def test_rubric_keeps_threshold_and_deduction_table_unchanged(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    assert "Prog zaliczenia: **82**" in content, f"{_rel(path)}: prog 82 zmieniony lub usuniety."
    assert "| Zapis do DB bez `db.commit()` | -30 |" in content, f"{_rel(path)}: zmieniony wiersz tabeli odjec."


# --- 2. wspolny blok w work / work-simple -----------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_skill_has_exactly_one_shared_block(path: Path) -> None:
    _single_block(path)


@pytest.mark.unit
@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_shared_block_content_covers_contract(path: Path) -> None:
    block = _normalize(_single_block(path))
    assert "blokada-zewnetrzna" in block, f"{_rel(path)}: blok bez znacznika BLOKADA-ZEWNETRZNA."
    assert "monolynx_sprint_run=1" in block, f"{_rel(path)}: blok bez MONOLYNX_SPRINT_RUN=1."
    assert "sprint-run stop:" in block, f"{_rel(path)}: blok bez linii 'SPRINT-RUN STOP:'."
    assert "needs-local" in block, f"{_rel(path)}: blok bez sugestii etykiety needs-local."
    assert "komentarz" in block, f"{_rel(path)}: blok bez komentarza do ticketu."
    assert "opcj" in block, f"{_rel(path)}: blok bez opcji decyzji."
    assert "interaktywn" in block, f"{_rel(path)}: blok bez opisu trybu interaktywnego."


@pytest.mark.unit
@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_shared_block_has_no_step_numbers(path: Path) -> None:
    """Blok jest wspolny dla dwoch skilli o roznej numeracji - nie moze odwolywac sie do krokow/faz."""
    block = _normalize(_single_block(path))
    assert "krok " not in block, f"{_rel(path)}: blok zawiera numer kroku."
    assert "faza " not in block, f"{_rel(path)}: blok zawiera numer fazy."


@pytest.mark.unit
def test_shared_block_identical_across_work_and_work_simple_and_copies() -> None:
    variants = {_rel(path): _single_block(path) for path in SKILL_PATHS}
    assert len(set(variants.values())) == 1, f"Tresc bloku rozna miedzy plikami: {sorted(variants)}."


@pytest.mark.unit
def test_validator_check_shared_blocks_accepts_work_skills() -> None:
    files = {_rel(path): path.read_text(encoding="utf-8") for path in SKILL_PATHS}
    violations = validator.check_shared_blocks(files)
    assert [v for v in violations if "blokada-zewnetrzna" in v] == []


# --- 3. regula sesji w tle ---------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    "path,rule_pattern",
    BACKGROUND_MODE_RULE_CASES,
    ids=[_rel(path) for path, _ in BACKGROUND_MODE_RULE_CASES],
)
def test_background_session_rule_lists_external_blocker(path: Path, rule_pattern: re.Pattern[str]) -> None:
    match = rule_pattern.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"Nie znaleziono reguly 'Tryb sesji w tle' w {_rel(path)}."
    rule = _normalize(match.group(0))
    assert "blokada-zewnetrzna" in rule or "blokada zewnetrzna" in rule, f"{_rel(path)}: regula nie wymienia blokady zewnetrznej."


# --- 4. changelog i wersja ---------------------------------------------------


def _changelog_headings(readme: str) -> list[str]:
    after = readme.split("## Changelog", 1)[1]
    return [line for line in after.splitlines() if line.startswith("### ")]


@pytest.mark.unit
def test_readme_changelog_starts_with_unreleased() -> None:
    headings = _changelog_headings(README.read_text(encoding="utf-8"))
    assert headings, "Brak naglowkow ### pod ## Changelog."
    assert headings[0].strip() == "### Unreleased", f"Pierwszy naglowek changelogu: {headings[0]!r}."


@pytest.mark.unit
def test_readme_unreleased_section_has_mon_223_entry() -> None:
    readme = README.read_text(encoding="utf-8")
    after = readme.split("## Changelog", 1)[1]
    match = re.search(r"(?ms)^### Unreleased\n(.*?)(?=^### )", after)
    assert match is not None, "Brak sekcji ### Unreleased."
    assert re.search(r"(?m)^- MON-223: \S", match.group(1)), "Brak punktu '- MON-223: ...' pod ### Unreleased."


@pytest.mark.unit
def test_plugin_consistency_validator_passes_on_repo_versions() -> None:
    readme = README.read_text(encoding="utf-8")
    plugin_json = PLUGIN_JSON_CLAUDE.read_text(encoding="utf-8")
    assert validator.check_version_matches_changelog(plugin_json, readme) == []
    assert validator.check_unreleased_entries(readme) == []
