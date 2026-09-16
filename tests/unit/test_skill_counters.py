"""Testy regresyjne spójności liczników skilli (MON-179).

Źródłem prawdy jest liczba katalogów plugin/skills/*/ zawierających SKILL.md.
Walidator pluginu sprawdza tylko obecność nazw skilli, nie liczb w tekście,
więc liczniki w kodzie i dokumentacji (main.py PL i EN, mcp_guide.html,
how-to-use-monolynx.md, plugin/README.md, agent-bootstrap.md) rozjeżdżały się
niezauważone (np. "20 skilli" w README przy 21 katalogach). Każdy z tych
liczników musi być zgodny z liczbą katalogów, a plugin/skills/help/SKILL.md
ma pozostać krótką mapą skilli w grupach, z '/monolynx:next' jako pierwszą
wzmianką w treści.
"""

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "plugin" / "skills"
HELP_SKILL_PATH = SKILLS_DIR / "help" / "SKILL.md"


def _skill_dirs() -> list[Path]:
    return sorted(p for p in SKILLS_DIR.iterdir() if p.is_dir() and (p / "SKILL.md").exists())


def _skill_names() -> set[str]:
    return {p.name for p in _skill_dirs()}


def _skill_count() -> int:
    return len(_skill_dirs())


@dataclass(frozen=True)
class CounterLocation:
    """Jedno miejsce w kodzie/dokumentacji, które trzyma liczbę skilli jako tekst."""

    label: str
    relpath: str
    pattern: str


COUNTER_LOCATIONS = [
    CounterLocation("main.py (EN)", "src/monolynx/main.py", r"all (\d+) ticket"),
    CounterLocation("main.py (PL)", "src/monolynx/main.py", r"wszystkie (\d+) skilli"),
    CounterLocation(
        "mcp_guide.html",
        "src/monolynx/templates/dashboard/profile/mcp_guide.html",
        r"Dostajesz (\d+) skilli",
    ),
    CounterLocation(
        "how-to-use-monolynx.md",
        "src/monolynx/static/how-to-use-monolynx.md",
        r"Full set of (\d+) skills",
    ),
    CounterLocation("plugin/README.md", "plugin/README.md", r"\*\*(\d+) skilli\*\*"),
    CounterLocation(
        "agent-bootstrap.md",
        "src/monolynx/static/agent-bootstrap.md",
        r"Komplet (\d+) skilli",
    ),
]


@pytest.mark.unit
def test_every_skill_subdirectory_has_skill_md() -> None:
    """Każdy podkatalog plugin/skills ma plik SKILL.md, żeby liczenie po SKILL.md nie gubiło cicho żadnego skilla."""
    all_dirs = {p for p in SKILLS_DIR.iterdir() if p.is_dir()}
    without_skill_md = sorted(p.name for p in all_dirs if not (p / "SKILL.md").exists())
    assert not without_skill_md, f"Katalogi w plugin/skills bez SKILL.md: {without_skill_md}"


@pytest.mark.unit
@pytest.mark.parametrize("location", COUNTER_LOCATIONS, ids=lambda loc: loc.label)
def test_skill_counter_matches_skill_directory_count(location: CounterLocation) -> None:
    """Licznik skilli znaleziony wzorcem w pliku jest równy liczbie katalogów plugin/skills/*/SKILL.md."""
    path = REPO_ROOT / location.relpath
    content = path.read_text(encoding="utf-8")
    match = re.search(location.pattern, content)
    assert match is not None, f"Wzorzec {location.pattern!r} nie został znaleziony w {location.relpath} (zmieniło się zdanie?)."
    found = int(match.group(1))
    expected = _skill_count()
    assert found == expected, f"{location.relpath}: licznik w tekście to {found}, a katalogów plugin/skills/*/SKILL.md jest {expected}."


@pytest.mark.unit
def test_how_to_use_monolynx_skill_list_matches_skill_directories() -> None:
    """Zbiór nazw skilli z linii 'Full set of N skills: ...' w how-to-use-monolynx.md jest równy katalogom plugin/skills."""
    path = REPO_ROOT / "src/monolynx/static/how-to-use-monolynx.md"
    content = path.read_text(encoding="utf-8")
    target_line = next(
        (line for line in content.splitlines() if "Full set of" in line and "skills" in line),
        None,
    )
    assert target_line is not None, "Nie znaleziono linii 'Full set of N skills: ...' w how-to-use-monolynx.md."
    listed = set(re.findall(r"monolynx:([A-Za-z0-9-]+)", target_line))
    expected = _skill_names()
    assert listed == expected, f"Lista skilli w how-to-use-monolynx.md {listed} różni się od katalogów plugin/skills {expected}."


@pytest.mark.unit
def test_help_skill_is_at_most_100_lines() -> None:
    """Plik plugin/skills/help/SKILL.md ma co najwyżej 100 linii, bo ma być mapą skilli, a nie pełnym przewodnikiem."""
    lines = HELP_SKILL_PATH.read_text(encoding="utf-8").splitlines()
    assert len(lines) <= 100, f"plugin/skills/help/SKILL.md ma {len(lines)} linii, limit to 100."


@pytest.mark.unit
def test_help_skill_lists_every_skill_as_slash_command() -> None:
    """Każdy skill z plugin/skills ma w help/SKILL.md wzmiankę /monolynx:<nazwa>, nie licząc dopasowania jako prefiks dłuższej nazwy."""
    content = HELP_SKILL_PATH.read_text(encoding="utf-8")
    missing = [name for name in sorted(_skill_names()) if not re.search(rf"/monolynx:{re.escape(name)}(?![A-Za-z0-9-])", content)]
    assert not missing, f"plugin/skills/help/SKILL.md nie wymienia skilli: {missing}"


@pytest.mark.unit
def test_help_skill_mentions_next_before_any_other_skill() -> None:
    """Pierwsza wzmianka /monolynx: w treści help/SKILL.md (po frontmatterze) to /monolynx:next - pierwsza linia mapy skilli."""
    content = HELP_SKILL_PATH.read_text(encoding="utf-8")
    body_match = re.match(r"^---\n.*?\n---\n(.*)$", content, re.DOTALL)
    assert body_match is not None, "plugin/skills/help/SKILL.md nie ma poprawnego frontmatteru YAML (brak zamykającego '---')."
    body = body_match.group(1)
    first_mention = re.search(r"/monolynx:[A-Za-z0-9-]+", body)
    assert first_mention is not None, "plugin/skills/help/SKILL.md nie ma żadnej wzmianki /monolynx: w treści po frontmatterze."
    assert first_mention.group(0) == "/monolynx:next", (
        f"Pierwsza wzmianka /monolynx: w help/SKILL.md to {first_mention.group(0)!r}, oczekiwano '/monolynx:next'."
    )
