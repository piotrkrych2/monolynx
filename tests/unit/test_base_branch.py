"""Testy regresyjne dla MON-197: branch bazowy i integracyjny z konfiguracji projektu, nie z literalu 'main'/'master'.

Zrodlem prawdy jest tresc skilli `work`, `work-simple`, `wiki-ingest`, `wiki-init`,
`wiki-sync-merge` oraz skryptu dyspozytora `sprint_run_tick.py` (plus wygenerowane
kopie skilli w `src/monolynx/static/skills/`, ktore serwuje `install_monolynx_skills`).

Analogicznie do MON-192 (`MONOLYNX_MR_TARGET` dla brancha integracyjnego, celu MR)
punkt startu brancha roboczego / worktree ma pochodzic z nowej zmiennej
`MONOLYNX_BASE_BRANCH`, z lancuchem fallbacku `MONOLYNX_BASE_BRANCH` ->
`MONOLYNX_MR_TARGET` -> wykrycie domyslnego brancha repozytorium (`git
symbolic-ref refs/remotes/origin/HEAD`, potem `glab repo view`) - zamiast
zaszytego na sztywno `git checkout main`. Skille wiki maja dzis twardy warunek
wstepny "tylko branch main/master" dla zapisu (create/update/delete stron);
bramka ma sprawdzac branch integracyjny z konfiguracji, nie literal. Dyspozytor
sprintu (`sprint_run_tick.py`) ma zweryfikowac w preflighcie, ze lokalny punkt
startu jest aktualny wobec `origin`.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

WORK_SKILL_PATHS = [
    REPO_ROOT / "plugin/skills/work/SKILL.md",
    REPO_ROOT / "plugin/skills/work-simple/SKILL.md",
]

WORK_STATIC_COPY_PATHS = [
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work/SKILL.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work-simple/SKILL.md",
]

ALL_WORK_SKILL_MD_PATHS = WORK_SKILL_PATHS + WORK_STATIC_COPY_PATHS

# work-simple deleguje branchowanie do `work` i nie robi checkoutu sam - komenda
# z placeholderem dotyczy wylacznie `work` (zrodlo + kopia static).
WORK_CHECKOUT_SKILL_PATHS = [
    REPO_ROOT / "plugin/skills/work/SKILL.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-work/SKILL.md",
]

WIKI_GATE_SKILL_PATHS = [
    REPO_ROOT / "plugin/skills/wiki-ingest/SKILL.md",
    REPO_ROOT / "plugin/skills/wiki-init/SKILL.md",
    REPO_ROOT / "plugin/skills/wiki-sync-merge/SKILL.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-wiki-ingest/SKILL.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-wiki-init/SKILL.md",
    REPO_ROOT / "src/monolynx/static/skills/monolynx-wiki-sync-merge/SKILL.md",
]

SPRINT_RUN_TICK_SCRIPT = REPO_ROOT / "plugin/scripts/sprint_run_tick.py"
SPRINT_RUN_SKILL_PATH = REPO_ROOT / "plugin/skills/sprint-run/SKILL.md"
README_PATH = REPO_ROOT / "plugin/README.md"

BASE_BRANCH_TABLE_ROW_PATTERN = re.compile(r"^\|\s*`MONOLYNX_BASE_BRANCH`", re.MULTILINE)
MR_TARGET_TABLE_ROW_PATTERN = re.compile(r"^\|\s*`MONOLYNX_MR_TARGET`", re.MULTILINE)
WORK_CHECKOUT_COMMAND_PATTERN = re.compile(r"git checkout\s+(\S+)\s*&&\s*git pull origin\s+(\S+)\s*&&\s*git checkout -b feature-")

# Lancuch fallbacku identyczny dla brancha roboczego (MONOLYNX_BASE_BRANCH) i
# brancha integracyjnego MR (MONOLYNX_MR_TARGET, MON-192) - obie zmienne spadaja
# na to samo wykrycie domyslnego brancha repozytorium.
FALLBACK_CHAIN_STRINGS = (
    "MONOLYNX_BASE_BRANCH",
    "MONOLYNX_MR_TARGET",
    "git symbolic-ref refs/remotes/origin/HEAD",
    "glab repo view",
)

HARDCODED_CHECKOUT_LITERALS = (
    "git checkout main",
    "git checkout master",
    "git pull origin main",
    "git pull origin master",
)


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


@pytest.mark.unit
@pytest.mark.parametrize("path", ALL_WORK_SKILL_MD_PATHS, ids=_rel)
def test_work_skill_md_has_no_hardcoded_checkout_main_or_master(path: Path) -> None:
    """Zaden plik SKILL.md skilli pracy (zrodlowy ani kopia static) nie zawiera literalu checkoutu/pull main/master."""
    content = path.read_text(encoding="utf-8")
    for literal in HARDCODED_CHECKOUT_LITERALS:
        assert literal not in content, (
            f"{_rel(path)} wciaz zawiera literal '{literal}' - punkt startu brancha roboczego ma pochodzic "
            "z konfiguracji projektu (MONOLYNX_BASE_BRANCH), nie na sztywno."
        )


@pytest.mark.unit
@pytest.mark.parametrize("path", WORK_CHECKOUT_SKILL_PATHS, ids=_rel)
def test_work_checkout_command_targets_resolved_base_branch_placeholder(path: Path) -> None:
    """Komenda tworzaca branch roboczy w `work` uzywa placeholdera brancha bazowego, nie literalu 'main'."""
    content = path.read_text(encoding="utf-8")
    match = WORK_CHECKOUT_COMMAND_PATTERN.search(content)
    assert match is not None, f"Nie znaleziono komendy 'git checkout <...> && git pull origin <...> && git checkout -b feature-...' w {_rel(path)}."
    checkout_target, pull_target = match.group(1), match.group(2)
    assert checkout_target == pull_target, (
        f"Checkout i pull w {_rel(path)} powinny celowac w ten sam branch bazowy: {checkout_target!r} != {pull_target!r}."
    )
    assert checkout_target not in ("main", "master"), f"Komenda checkoutu w {_rel(path)} wciaz celuje na sztywno w {checkout_target!r}."
    assert checkout_target.startswith("<") and checkout_target.endswith(">"), (
        f"Branch bazowy w komendzie checkoutu ({_rel(path)}) powinien byc placeholderem ustalanym w runtime, nie literalem: {checkout_target!r}."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", WORK_SKILL_PATHS, ids=_rel)
def test_work_skill_variable_table_lists_base_branch(path: Path) -> None:
    """Tabela zmiennych skilla ma wiersz opisujacy MONOLYNX_BASE_BRANCH."""
    content = path.read_text(encoding="utf-8")
    assert BASE_BRANCH_TABLE_ROW_PATTERN.search(content), (
        f"{_rel(path)}: brak wiersza tabeli zmiennych zaczynajacego sie od '| `MONOLYNX_BASE_BRANCH`'."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", WORK_SKILL_PATHS, ids=_rel)
def test_work_skill_config_echo_reports_base_branch(path: Path) -> None:
    """Linia 'echo' w sekcji Konfiguracja skilla wypisuje tez MONOLYNX_BASE_BRANCH, jak pozostale flagi."""
    content = path.read_text(encoding="utf-8")
    assert "BASE_BRANCH=${MONOLYNX_BASE_BRANCH" in content, (
        f"{_rel(path)}: linia konfiguracyjna 'echo' nie wypisuje BASE_BRANCH=${{MONOLYNX_BASE_BRANCH...}}."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", WORK_SKILL_PATHS, ids=_rel)
def test_work_skill_documents_base_branch_fallback_chain(path: Path) -> None:
    """Opis ustalania brancha bazowego wymienia lancuch fallbacku MONOLYNX_BASE_BRANCH -> MONOLYNX_MR_TARGET -> symbolic-ref -> glab repo view."""
    content = path.read_text(encoding="utf-8")
    for needle in FALLBACK_CHAIN_STRINGS:
        assert needle in content, f"{_rel(path)}: brak '{needle}' w opisie ustalania brancha bazowego."


@pytest.mark.unit
@pytest.mark.parametrize("path", WIKI_GATE_SKILL_PATHS, ids=_rel)
def test_wiki_skill_has_no_hardcoded_main_master_gate(path: Path) -> None:
    """Warunek wstepny zapisu do wiki nie jest juz zaszyty na sztywno w frazie 'tylko branch main/master'."""
    content = path.read_text(encoding="utf-8")
    assert "tylko branch main/master" not in content, (
        f"{_rel(path)} wciaz zawiera literal 'tylko branch main/master' - bramka zapisu ma sprawdzac "
        "branch integracyjny z konfiguracji projektu, nie literal."
    )


@pytest.mark.unit
@pytest.mark.parametrize("path", WIKI_GATE_SKILL_PATHS, ids=_rel)
def test_wiki_skill_documents_base_branch_fallback_chain(path: Path) -> None:
    """Warunek wstepny wiki dokumentuje ten sam lancuch fallbacku co skille pracy."""
    content = path.read_text(encoding="utf-8")
    for needle in FALLBACK_CHAIN_STRINGS:
        assert needle in content, f"{_rel(path)}: brak '{needle}' w warunku wstepnym zapisu do wiki."


@pytest.mark.unit
def test_wiki_sync_merge_summary_has_no_hardcoded_main_master_gate() -> None:
    """Pozycja 'Tylko main/master' w sekcji 'Wazne zasady' wiki-sync-merge tez nie jest juz literalem."""
    path = REPO_ROOT / "plugin/skills/wiki-sync-merge/SKILL.md"
    content = path.read_text(encoding="utf-8")
    assert "Tylko main/master" not in content, (
        f"{_rel(path)}: sekcja 'Wazne zasady' wciaz zawiera literal 'Tylko main/master' obok gatingu warunku wstepnego."
    )


@pytest.mark.unit
def test_sprint_run_tick_preflight_prints_base_branch_key() -> None:
    """sprint_run_tick.py preflight ma wypisywac klucz BASE_BRANCH= (analogicznie do reszty _KEY_ORDER)."""
    content = SPRINT_RUN_TICK_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'"BASE_BRANCH"', content), (
        "sprint_run_tick.py: brak klucza 'BASE_BRANCH' w _KEY_ORDER / values preflightu - punkt startu ma byc "
        "widoczny w wyjsciu ticku, tak jak reszta zmiennych konfiguracyjnych."
    )


@pytest.mark.unit
def test_sprint_run_tick_preflight_flags_stale_base_branch() -> None:
    """sprint_run_tick.py preflight ma sygnalizowac rozjazd brancha bazowego wobec origin kluczem BASE_BRANCH_STALE=."""
    content = SPRINT_RUN_TICK_SCRIPT.read_text(encoding="utf-8")
    assert re.search(r'"BASE_BRANCH_STALE"', content), (
        "sprint_run_tick.py: brak klucza 'BASE_BRANCH_STALE' - preflight ma wykrywac rozjazd lokalnego "
        "brancha bazowego wobec origin, zeby dyspozytor nie startowal sesji z przestarzalego punktu startu."
    )


@pytest.mark.unit
def test_sprint_run_skill_documents_base_branch_and_stop() -> None:
    """SKILL.md sprint-run wspomina MONOLYNX_BASE_BRANCH i blokade SPRINT-RUN STOP przy rozjezdzie brancha bazowego."""
    content = SPRINT_RUN_SKILL_PATH.read_text(encoding="utf-8")
    assert "MONOLYNX_BASE_BRANCH" in content, f"{_rel(SPRINT_RUN_SKILL_PATH)}: brak wzmianki o MONOLYNX_BASE_BRANCH."
    assert "SPRINT-RUN STOP" in content, f"{_rel(SPRINT_RUN_SKILL_PATH)}: brak blokady 'SPRINT-RUN STOP' przy rozjezdzie brancha bazowego."


@pytest.mark.unit
def test_readme_variable_table_lists_base_branch() -> None:
    """Sekcja '## Zmienne konfiguracyjne' w plugin/README.md ma wiersz opisujacy MONOLYNX_BASE_BRANCH."""
    content = README_PATH.read_text(encoding="utf-8")
    assert BASE_BRANCH_TABLE_ROW_PATTERN.search(content), (
        "plugin/README.md: brak wiersza tabeli zmiennych zaczynajacego sie od '| `MONOLYNX_BASE_BRANCH`'."
    )


@pytest.mark.unit
def test_readme_documents_branch_mode_base_branch_and_mr_target_roles() -> None:
    """README rozgranicza role trzech zmiennych osobnym akapitem, nie tylko wzmianka o nazwie:
    MONOLYNX_BRANCH_MODE waliduje nazwe brancha, MONOLYNX_BASE_BRANCH to punkt startu,
    MONOLYNX_MR_TARGET to cel MR."""
    content = README_PATH.read_text(encoding="utf-8")
    assert "MONOLYNX_BRANCH_MODE" in content, "plugin/README.md: brak wzmianki o MONOLYNX_BRANCH_MODE."
    assert BASE_BRANCH_TABLE_ROW_PATTERN.search(content), "plugin/README.md: brak wiersza tabeli MONOLYNX_BASE_BRANCH."
    assert MR_TARGET_TABLE_ROW_PATTERN.search(content), "plugin/README.md: brak wiersza tabeli MONOLYNX_MR_TARGET."
    assert "**punkt startu**" in content, "plugin/README.md: brak akapitu opisujacego MONOLYNX_BASE_BRANCH jako 'punkt startu' brancha roboczego."
    assert "**cel MR**" in content, "plugin/README.md: brak akapitu opisujacego MONOLYNX_MR_TARGET jako 'cel MR' - rozgraniczenie od punktu startu."
