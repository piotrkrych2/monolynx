"""Testy regresyjne dla MON-192: AUTOMR musi celowac w branch docelowy projektu, nie na sztywno w 'main'.

Zrodlem prawdy dla samej komendy `glab mr create ... --target-branch <...>`
i lancucha fallbacku brancha docelowego jest plik `commit-mr.md` skilli `work`
i `work-simple` (po MON-188 wydzielony ze wspolnej tresci SKILL.md), plus jego
wygenerowane kopie w `src/monolynx/static/skills/`, ktore serwuje
`install_monolynx_skills`. `SKILL.md` samych skilli wciaz odpowiada za ogolna
tabele zmiennych i linie startowego `echo` (patrz `test_skill_variable_table_lists_mr_target`
i `test_skill_config_echo_reports_mr_target`) - to zostalo w miejscu.
Blok AUTOMR mial dotychczas zaszyty literal `--target-branch main`, co lamalo
projekty mergujace do innego brancha niz `main` (np. plusbus-app -> `develop`).
Docelowo branch pochodzi z `MONOLYNX_MR_TARGET`, a bez niej z wykrycia
domyslnego brancha repozytorium (`git symbolic-ref refs/remotes/origin/HEAD`,
fallback `glab repo view`).
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

# commit-mr.md to zrodlo prawdy dla komendy MR i jej lancucha fallbacku od MON-188 -
# SKILL.md samych skilli nie zawiera juz tej tresci (patrz docstring modulu).
COMMIT_MR_SOURCE_PATHS = [
    REPO_ROOT / "plugin/skills/work/commit-mr.md",
    REPO_ROOT / "plugin/skills/work-simple/commit-mr.md",
]

COMMIT_MR_STATIC_PATHS = [
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work/commit-mr.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work-simple/commit-mr.md",
]

ALL_COMMIT_MR_PATHS = COMMIT_MR_SOURCE_PATHS + COMMIT_MR_STATIC_PATHS

# Wszystkie pliki, w ktorych literal '--target-branch main' moglby wjechac -
# SKILL.md (zrodla + kopie) ORAZ commit-mr.md (zrodla + kopie), bo to on
# faktycznie niesie komende MR od MON-188.
ALL_HARDCODED_MAIN_GUARD_PATHS = SOURCE_SKILL_PATHS + STATIC_COPY_PATHS + ALL_COMMIT_MR_PATHS

MR_CREATE_TARGET_PATTERN = re.compile(r"glab mr create[^\n]*--target-branch\s+(\S+)")
MR_TARGET_TABLE_ROW_PATTERN = re.compile(r"^\|\s*`MONOLYNX_MR_TARGET`", re.MULTILINE)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.unit
@pytest.mark.parametrize("path", ALL_HARDCODED_MAIN_GUARD_PATHS, ids=_rel)
def test_no_hardcoded_target_branch_main(path: Path) -> None:
    """Zaden plik SKILL.md ani commit-mr.md (zrodlowy ani wygenerowana kopia) nie zawiera literalu '--target-branch main'."""
    content = path.read_text(encoding="utf-8")
    assert "--target-branch main" not in content, (
        f"{_rel(path)} wciaz zawiera literal '--target-branch main' - AUTOMR ma celowac w branch docelowy projektu, nie na sztywno w main."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", ALL_COMMIT_MR_PATHS, ids=_rel)
def test_mr_create_command_targets_resolved_branch_placeholder(path: Path) -> None:
    """Komenda tworzaca MR (w commit-mr.md) uzywa placeholdera brancha docelowego, nie literalu 'main'."""
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
@pytest.mark.parametrize("path", COMMIT_MR_SOURCE_PATHS, ids=_rel)
def test_skill_documents_target_branch_fallback_chain(path: Path) -> None:
    """Opis ustalania brancha docelowego MR (w commit-mr.md) wymienia lancuch fallbacku: MONOLYNX_MR_TARGET -> symbolic-ref -> glab repo view.

    UWAGA: SKILL.md ma WLASNY, inny lancuch fallbacku - dla brancha BAZOWEGO
    (`MONOLYNX_BASE_BRANCH`, punkt startu pracy), ktory tez wspomina
    MONOLYNX_MR_TARGET/symbolic-ref/glab repo view jako swoje fallbacki (2b).
    To inna funkcja niz branch docelowy MR - test musi czytac commit-mr.md,
    inaczej przypadkiem zweryfikuje niewlasciwa sekcje (patrz MON-188 review).
    """
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
