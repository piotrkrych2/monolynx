"""Testy TDD dla MON-200: skille pluginu maja czytac blokery ticketow z MON-198.

MON-198 dodal do MCP: `create_ticket(..., blocked_by_ids: list[str] | None)` oraz
sekcje w wyniku `get_ticket` z liniami `Blokowany przez: MON-5 | todo | Tytul`
i `Blokuje: MON-7 | in_progress | Tytul` (sekcja tylko gdy sa relacje). Bloker
jest zamkniety wylacznie gdy status == `done`.

MON-207 zmienia naglowek tej sekcji z `## Zaleznosci` na `## Blokery (relacja)`
w `plugin/skills/{sprint-run,work,work-simple}/SKILL.md` (i ich kopiach
dystrybucyjnych) - odroznia relacje strukturalna (`blocked_by_ids`) od prozy
autora w opisie ticketu (naglowek `## Zaleznosci` recznie wpisany w opisie przez
`ticket-create`) i od komentarza `**Zaleznosci:**`, ktore nie sa relacja.
Zakres MON-207 obejmowal wprost tylko te trzy skille, ale technical-writer
przestawil na nowy naglowek tez `next` i `ticket-review` (spojnosc calego
plugina), wiec teraz WSZYSTKIE skille odczytujace sekcje relacji z `get_ticket`
sprawdzaja `## Blokery (relacja)`. `## Zaleznosci` zostaje w tresci skilli
wylacznie jako opis prozy szablonu opisu/komentarza (nie jako sprawdzany
naglowek strukturalny).

Zrodlem prawdy sa tresci `plugin/skills/ticket-create/SKILL.md`,
`plugin/skills/work/SKILL.md`, `plugin/skills/work-simple/SKILL.md`,
`plugin/skills/sprint-run/SKILL.md`, `plugin/skills/next/SKILL.md`,
`plugin/skills/ticket-review/SKILL.md` (plus ich kopie dystrybucyjne w
`src/monolynx/static/skills/`) oraz `src/monolynx/features.py`.

Teksty skilli bywaja pisane z polskimi diakrytykami albo bez, wiec porownania
przechodza przez `_normalize` (usuniecie diakrytykow + lowercase) zamiast
prostego `in` na surowym tekscie.
"""

import re
import unicodedata
from pathlib import Path

import pytest

from monolynx.features import _feature_scrum

REPO_ROOT = Path(__file__).resolve().parents[2]

TICKET_CREATE_SKILL = REPO_ROOT / "plugin/skills/ticket-create/SKILL.md"
TICKET_CREATE_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-ticket-create/SKILL.md"
TICKET_CREATE_PATHS = [TICKET_CREATE_SKILL, TICKET_CREATE_STATIC]

WORK_SKILL = REPO_ROOT / "plugin/skills/work/SKILL.md"
WORK_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-work/SKILL.md"
WORK_PATHS = [WORK_SKILL, WORK_STATIC]

WORK_SIMPLE_SKILL = REPO_ROOT / "plugin/skills/work-simple/SKILL.md"
WORK_SIMPLE_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-work-simple/SKILL.md"
WORK_SIMPLE_PATHS = [WORK_SIMPLE_SKILL, WORK_SIMPLE_STATIC]

WORK_AND_SIMPLE_PATHS = WORK_PATHS + WORK_SIMPLE_PATHS

SPRINT_RUN_SKILL = REPO_ROOT / "plugin/skills/sprint-run/SKILL.md"
SPRINT_RUN_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-sprint-run/SKILL.md"
SPRINT_RUN_PATHS = [SPRINT_RUN_SKILL, SPRINT_RUN_STATIC]

NEXT_SKILL = REPO_ROOT / "plugin/skills/next/SKILL.md"
NEXT_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-next/SKILL.md"
NEXT_PATHS = [NEXT_SKILL, NEXT_STATIC]

TICKET_REVIEW_SKILL = REPO_ROOT / "plugin/skills/ticket-review/SKILL.md"
TICKET_REVIEW_STATIC = REPO_ROOT / "src/monolynx/static/skills/monolynx-ticket-review/SKILL.md"
TICKET_REVIEW_PATHS = [TICKET_REVIEW_SKILL, TICKET_REVIEW_STATIC]

CREATE_TICKET_CALL_PATTERN = re.compile(r"(?ms)mcp__monolynx__create_ticket\(.*?^\)")
KROK4_SECTION_PATTERN = re.compile(r"(?ms)^## KROK 4:.*?(?=^## KROK 5:)")

# regula "Tryb sesji w tle" ma numer 10 w work/SKILL.md (nastepna regula: 11) i
# numer 21 w work-simple/SKILL.md (nastepna regula: 22) - ustalone w tresci
# obu plikow, nie zgadywane.
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


def _extract_create_ticket_call(content: str) -> str:
    match = CREATE_TICKET_CALL_PATTERN.search(content)
    assert match is not None, "Nie znaleziono bloku wywolania mcp__monolynx__create_ticket(...)."
    return match.group(0)


@pytest.mark.unit
@pytest.mark.parametrize("path", TICKET_CREATE_PATHS, ids=_rel)
def test_ticket_create_call_passes_blocked_by_ids(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    call_block = _extract_create_ticket_call(content)
    assert "blocked_by_ids" in call_block, f"{_rel(path)}: blok create_ticket(...) nie przekazuje blocked_by_ids."


@pytest.mark.unit
@pytest.mark.parametrize("path", TICKET_CREATE_PATHS, ids=_rel)
def test_ticket_create_skill_drops_dependency_comment_workaround(path: Path) -> None:
    content = _normalize(path.read_text(encoding="utf-8"))
    assert "**zaleznosci:**" not in content, (
        f"{_rel(path)}: wciaz zawiera obejscie '**Zaleznosci:**' w komentarzu add_comment zamiast blocked_by_ids."
    )
    assert "ukonczone lub w review" not in content, f"{_rel(path)}: wciaz zawiera zdanie 'ukonczone lub w review' - obejscie sprzed blocked_by_ids."


@pytest.mark.unit
@pytest.mark.parametrize("path", WORK_AND_SIMPLE_PATHS, ids=_rel)
def test_work_skills_document_ticket_dependencies_section(path: Path) -> None:
    """MON-207: naglowek relacji w work/work-simple to '## Blokery (relacja)', nie '## Zaleznosci'."""
    content = _normalize(path.read_text(encoding="utf-8"))
    assert "## blokery (relacja)" in content, f"{_rel(path)}: brak naglowka '## Blokery (relacja)' z wyniku get_ticket."
    assert "blokowany przez" in content, f"{_rel(path)}: brak wzmianki o linii 'Blokowany przez'."


@pytest.mark.unit
@pytest.mark.parametrize("path", WORK_AND_SIMPLE_PATHS, ids=_rel)
def test_work_skills_mention_unclosed_blocker(path: Path) -> None:
    content = _normalize(path.read_text(encoding="utf-8"))
    assert "niezamkniety bloker" in content, f"{_rel(path)}: brak frazy 'niezamkniety bloker'."


@pytest.mark.unit
@pytest.mark.parametrize(
    "path,rule_pattern",
    BACKGROUND_MODE_RULE_CASES,
    ids=[_rel(path) for path, _ in BACKGROUND_MODE_RULE_CASES],
)
def test_background_session_mode_rule_blocks_on_open_blocker(path: Path, rule_pattern: re.Pattern[str]) -> None:
    content = path.read_text(encoding="utf-8")
    match = rule_pattern.search(content)
    assert match is not None, f"Nie znaleziono reguly 'Tryb sesji w tle' w {_rel(path)}."
    assert "bloker" in _normalize(match.group(0)), f"{_rel(path)}: regula 'Tryb sesji w tle' nie wspomina blokera jako powodu SPRINT-RUN STOP."


@pytest.mark.unit
@pytest.mark.parametrize("path", SPRINT_RUN_PATHS, ids=_rel)
def test_sprint_run_krok4_dispatch_respects_ticket_dependencies(path: Path) -> None:
    """MON-207: naglowek relacji w sprint-run to '## Blokery (relacja)', nie '## Zaleznosci'."""
    content = path.read_text(encoding="utf-8")
    match = KROK4_SECTION_PATTERN.search(content)
    assert match is not None, f"Nie znaleziono sekcji 'KROK 4' w {_rel(path)}."
    section = _normalize(match.group(0))
    assert "## blokery (relacja)" in section, f"{_rel(path)}: KROK 4 nie wspomina sekcji '## Blokery (relacja)'."
    assert "blokowany przez" in section, f"{_rel(path)}: KROK 4 nie wspomina linii 'Blokowany przez'."
    assert "niezamkniety bloker" in section, f"{_rel(path)}: KROK 4 nie wspomina frazy 'niezamkniety bloker'."


@pytest.mark.unit
@pytest.mark.parametrize("path", NEXT_PATHS, ids=_rel)
def test_next_skill_reads_ticket_dependencies(path: Path) -> None:
    content = _normalize(path.read_text(encoding="utf-8"))
    assert "## zaleznosci" in content, f"{_rel(path)}: brak naglowka '## Zaleznosci'."
    assert "blokowany przez" in content, f"{_rel(path)}: brak wzmianki o linii 'Blokowany przez'."


@pytest.mark.unit
@pytest.mark.parametrize("path", TICKET_REVIEW_PATHS, ids=_rel)
def test_ticket_review_skill_checks_ticket_dependencies(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    assert "blocked_by_ids" in content, f"{_rel(path)}: brak wzmianki o blocked_by_ids."
    assert "## zaleznosci" in _normalize(content), f"{_rel(path)}: brak naglowka '## Zaleznosci'."


@pytest.mark.unit
@pytest.mark.parametrize(
    "lang,title_needle",
    [("pl", "zaleznosci"), ("en", "dependencies")],
    ids=["pl", "en"],
)
def test_features_scrum_dependencies_entry_mentions_skills_respecting_blockers(lang: str, title_needle: str) -> None:
    data = _feature_scrum(lang)
    matching = [feature for feature in data["features"] if title_needle in _normalize(feature["title"])]
    assert matching, f"features.py ({lang}): brak wpisu features[] o zaleznosciach miedzy ticketami."
    desc = _normalize(matching[0]["desc"])
    assert "sprint-run" in desc, f"features.py ({lang}): opis wpisu o zaleznosciach nie wspomina sprint-run."
    assert "work" in desc, f"features.py ({lang}): opis wpisu o zaleznosciach nie wspomina work."


# ---------------------------------------------------------------------------
# MON-207: sprint-run przekazuje MONOLYNX_SPRINT_RUN=1 wprost w komendzie startu
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("path", SPRINT_RUN_PATHS, ids=_rel)
def test_sprint_run_start_command_sets_sprint_run_env_inline(path: Path) -> None:
    """MON-207 usuwa MONOLYNX_SPRINT_RUN z tracked .claude/settings.json (main nie
    moze dziedziczyc flagi sprintu), wiec komenda startu sesji roboczej musi
    ja przekazac wprost jako prefiks - inaczej worker nie wie, ze jest w trybie
    dyspozytora, i bash_guard.py traktuje jego Bash jak main session (ask/deny
    zamiast reguly 10/21 z SPRINT-RUN STOP)."""
    content = path.read_text(encoding="utf-8")
    assert "MONOLYNX_SPRINT_RUN=1 claude --bg" in content, f"{_rel(path)}: komenda startu 'claude --bg' nie ustawia MONOLYNX_SPRINT_RUN=1 inline."


# ---------------------------------------------------------------------------
# MON-207 (P4-P6): work/work-simple dokumentuja tylko `ruff check --no-fix`
# jako tryb read-only dozwolony bez posrednictwa Team Managera - `ruff check`
# bez flagi moze wykonac auto-fix i bash_guard.py go juz nie przepuszcza.
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("path", WORK_AND_SIMPLE_PATHS, ids=_rel)
def test_work_skills_document_ruff_check_no_fix_not_bare(path: Path) -> None:
    content = path.read_text(encoding="utf-8")
    assert "`ruff check` (bez flag)" not in content, (
        f"{_rel(path)}: wciaz opisuje 'ruff check' (bez flag) jako dozwolony tryb read-only - "
        "bash_guard.py juz go nie przepuszcza (moze wykonac auto-fix), dozwolone jest tylko --no-fix."
    )
    assert "ruff check --no-fix" in content, f"{_rel(path)}: brak wzmianki o dozwolonym trybie 'ruff check --no-fix'."
