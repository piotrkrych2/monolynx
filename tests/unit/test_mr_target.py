"""Testy regresyjne dla MON-192: AUTOMR musi celowac w branch docelowy projektu, nie na sztywno w 'main'.

Zrodlem prawdy jest tresc skilli `work` i `work-simple` (plus ich wygenerowane
kopie w `src/monolynx/static/skills/`, ktore serwuje `install_monolynx_skills`).
Blok AUTOMR w obu skillach mial dotychczas zaszyty literal `--target-branch main`,
co lamalo projekty mergujace do innego brancha niz `main` (np. plusbus-app ->
`develop`). Docelowo branch pochodzi z `MONOLYNX_MR_TARGET`, a bez niej z
wykrycia domyslnego brancha repozytorium (`git symbolic-ref
refs/remotes/origin/HEAD`, fallback `glab repo view`).
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SOURCE_SKILL_PATHS = [
    REPO_ROOT / "plugin/skills/work/SKILL.md",
    REPO_ROOT / "plugin/skills/work-simple/SKILL.md",
]

STATIC_COPY_PATHS = [
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work/SKILL.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work-simple/SKILL.md",
]

ALL_SKILL_MD_PATHS = SOURCE_SKILL_PATHS + STATIC_COPY_PATHS

MR_CREATE_TARGET_PATTERN = re.compile(r"glab mr create[^\n]*--target-branch\s+(\S+)")
MR_TARGET_TABLE_ROW_PATTERN = re.compile(r"^\|\s*`MONOLYNX_MR_TARGET`", re.MULTILINE)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.unit
@pytest.mark.parametrize("path", ALL_SKILL_MD_PATHS, ids=_rel)
def test_skill_md_has_no_hardcoded_target_branch_main(path: Path) -> None:
    """Zaden plik SKILL.md (zrodlowy ani wygenerowana kopia) nie zawiera juz literalu '--target-branch main'."""
    content = path.read_text(encoding="utf-8")
    assert "--target-branch main" not in content, (
        f"{_rel(path)} wciaz zawiera literal '--target-branch main' - AUTOMR ma celowac w branch docelowy projektu, nie na sztywno w main."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", SOURCE_SKILL_PATHS, ids=_rel)
def test_mr_create_command_targets_resolved_branch_placeholder(path: Path) -> None:
    """Komenda tworzaca MR uzywa placeholdera brancha docelowego, nie literalu 'main'."""
    content = path.read_text(encoding="utf-8")
    match = MR_CREATE_TARGET_PATTERN.search(content)
    assert match is not None, f"Nie znaleziono komendy 'glab mr create ... --target-branch <...>' w {_rel(path)}."
    target = match.group(1).rstrip("`")
    assert target != "main", f"Komenda MR w {_rel(path)} wciaz celuje na sztywno w 'main'."
    assert target.startswith("<") and target.endswith(">"), (
        f"Branch docelowy w komendzie MR ({_rel(path)}) powinien byc placeholderem ustalanym w runtime, nie literalem: {target!r}."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", SOURCE_SKILL_PATHS, ids=_rel)
def test_skill_variable_table_lists_mr_target(path: Path) -> None:
    """Tabela zmiennych skilla ma wiersz opisujacy MONOLYNX_MR_TARGET."""
    content = path.read_text(encoding="utf-8")
    assert MR_TARGET_TABLE_ROW_PATTERN.search(content), f"{_rel(path)}: brak wiersza tabeli zmiennych zaczynajacego sie od '| `MONOLYNX_MR_TARGET`'."


@pytest.mark.unit
@pytest.mark.parametrize("path", SOURCE_SKILL_PATHS, ids=_rel)
def test_skill_config_echo_reports_mr_target(path: Path) -> None:
    """Linia 'echo' w sekcji Konfiguracja skilla wypisuje tez MONOLYNX_MR_TARGET, jak pozostale flagi."""
    content = path.read_text(encoding="utf-8")
    assert "MR_TARGET=${MONOLYNX_MR_TARGET" in content, (
        f"{_rel(path)}: linia konfiguracyjna 'echo' nie wypisuje MR_TARGET=${{MONOLYNX_MR_TARGET...}}."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", SOURCE_SKILL_PATHS, ids=_rel)
def test_skill_documents_target_branch_fallback_chain(path: Path) -> None:
    """Opis ustalania brancha docelowego wymienia lancuch fallbacku: MONOLYNX_MR_TARGET -> symbolic-ref -> glab repo view."""
    content = path.read_text(encoding="utf-8")
    assert "MONOLYNX_MR_TARGET" in content, f"{_rel(path)}: brak wzmianki o MONOLYNX_MR_TARGET w opisie AUTOMR."
    assert "git symbolic-ref refs/remotes/origin/HEAD" in content, (
        f"{_rel(path)}: brak wykrywania domyslnego brancha przez 'git symbolic-ref refs/remotes/origin/HEAD'."
    )
    assert "glab repo view" in content, f"{_rel(path)}: brak fallbacku 'glab repo view' przy ustalaniu brancha docelowego."


@pytest.mark.unit
def test_readme_variable_table_lists_mr_target() -> None:
    """Sekcja '## Zmienne konfiguracyjne' w plugin/README.md ma wiersz opisujacy MONOLYNX_MR_TARGET."""
    path = REPO_ROOT / "plugin/README.md"
    content = path.read_text(encoding="utf-8")
    assert MR_TARGET_TABLE_ROW_PATTERN.search(content), (
        "plugin/README.md: brak wiersza tabeli zmiennych zaczynajacego sie od '| `MONOLYNX_MR_TARGET`'."
    )
