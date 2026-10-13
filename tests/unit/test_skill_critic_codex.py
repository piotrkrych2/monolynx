"""Testy TDD dla MON-274: gałąź "krytyk przez Codex CLI" w skillu `work`.

Kontrakt (Team Manager):
1. Każde wystąpienie `critic_backend.py` i `codex exec` w `SKILL.md` leży WEWNĄTRZ bloku
   `<!-- runtime:claude-code -->` ... `<!-- /runtime:claude-code -->` (skill uruchomiony w Codex nie odpala
   `codex exec` - bezpiecznik przed rekurencją).
2. `SKILL.md` wspomina obie zmienne (`MONOLYNX_CRITIC`, `MONOLYNX_CRITIC_CODEX_MODEL`), zamrożoną linię
   `CRITIC: backend=`, flagi `--prompt-file`, `--out`, `--cwd`, `--timeout` i opisuje fallback na krytyka Claude
   `opus` przy błędzie, timeoucie albo kodzie 2.
3. Nowa zasada o krytyku Codex jest OSTATNIĄ numerowaną zasadą w WAŻNYCH ZASADACH i ma numer 13; zasady 10 i 11
   zaczynają się jak dotąd (numery asertowane też w `test_skill_blokada_zewnetrzna.py` i
   `test_skill_ticket_dependencies.py`).
4. `pipeline.md` opisuje, że przy `backend=codex` score i zamknięcie joba krytyka zapisuje Team Manager.
5. `review-rubric.md` nie zawiera słowa `codex` (rubryka, próg 82 i format werdyktu bez zmian).
6. Kopie dystrybucyjne (`src/monolynx/static/skills/monolynx-work/`) spełniają te same asercje co źródło.

Porównania tekstowe idą przez `_normalize` (bez diakrytyków, lowercase), odporne na redakcję zdań.
"""

import re
import unicodedata
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
PLUGIN_WORK = REPO_ROOT / "plugin/skills/work"
STATIC_WORK = REPO_ROOT / "src/monolynx/static/skills/monolynx-work"

SKILL_PATHS = [PLUGIN_WORK / "SKILL.md", STATIC_WORK / "SKILL.md"]
PIPELINE_PATHS = [PLUGIN_WORK / "pipeline.md", STATIC_WORK / "pipeline.md"]
RUBRIC_PATHS = [PLUGIN_WORK / "review-rubric.md", STATIC_WORK / "review-rubric.md"]

RUNTIME_OPEN = "<!-- runtime:claude-code -->"
RUNTIME_CLOSE = "<!-- /runtime:claude-code -->"
CODEX_BRANCH_MARKERS = ("critic_backend.py", "codex exec")


def _rel(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def _normalize(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _marker_lines(text: str, marker: str) -> list[int]:
    return [index for index, line in enumerate(text.splitlines()) if marker in line]


def _runtime_block_line_ranges(text: str) -> list[range]:
    """Zakresy numerów linii (0-based, włącznie ze znacznikami) bloków runtime:claude-code; znacznik w osobnej linii."""
    ranges: list[range] = []
    start: int | None = None
    for index, line in enumerate(text.splitlines()):
        stripped = line.strip()
        if stripped == RUNTIME_OPEN:
            assert start is None, f"zagnieżdżony blok runtime:claude-code w linii {index + 1}"
            start = index
        elif stripped == RUNTIME_CLOSE:
            assert start is not None, f"znacznik zamykający bez otwarcia w linii {index + 1}"
            ranges.append(range(start, index + 1))
            start = None
    assert start is None, "niezamknięty blok runtime:claude-code"
    return ranges


def _paragraphs(text: str) -> list[str]:
    return [_normalize(chunk) for chunk in re.split(r"\n\s*\n", text) if chunk.strip()]


def _important_rules(text: str) -> dict[int, str]:
    """Numerowane zasady z sekcji WAŻNE ZASADY: numer -> treść (do następnej zasady albo końca pliku)."""
    lines = text.splitlines()
    heading = next(index for index, line in enumerate(lines) if _normalize(line).strip() == "## wazne zasady")
    rules: dict[int, list[str]] = {}
    current: int | None = None
    for line in lines[heading + 1 :]:
        match = re.match(r"^(\d+)\. \*\*", line)
        if match:
            current = int(match.group(1))
            rules[current] = [line]
        elif current is not None:
            rules[current].append(line)
    return {number: "\n".join(body) for number, body in rules.items()}


@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_codex_branch_is_present_in_the_skill(path):
    text = _read(path)

    for marker in CODEX_BRANCH_MARKERS:
        assert _marker_lines(text, marker), f"{_rel(path)} nie wspomina `{marker}`"


@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_every_codex_mention_lives_inside_a_runtime_claude_code_block(path):
    text = _read(path)
    blocks = _runtime_block_line_ranges(text)

    outside = [
        f"{marker} (linia {index + 1})"
        for marker in CODEX_BRANCH_MARKERS
        for index in _marker_lines(text, marker)
        if not any(index in block for block in blocks)
    ]

    assert not outside, f"{_rel(path)}: wystąpienia poza blokiem runtime:claude-code: {outside}"


def test_block_parser_flags_mention_moved_outside_of_the_block():
    text = f"Wstep\n{RUNTIME_OPEN}\nwewnatrz critic_backend.py\n{RUNTIME_CLOSE}\nna zewnatrz codex exec\n"
    blocks = _runtime_block_line_ranges(text)

    assert [index for index in _marker_lines(text, "critic_backend.py") if any(index in block for block in blocks)] == [2]
    assert [index for index in _marker_lines(text, "codex exec") if not any(index in block for block in blocks)] == [4]


@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_skill_documents_variables_frozen_line_and_script_flags(path):
    text = _read(path)

    for needle in (
        "MONOLYNX_CRITIC_CODEX_MODEL",
        "CRITIC: backend=",
        "--prompt-file",
        "--out",
        "--cwd",
        "--timeout",
    ):
        assert needle in text, f"{_rel(path)} nie wspomina `{needle}`"
    # `MONOLYNX_CRITIC` jako osobna zmienna, nie tylko prefiks `MONOLYNX_CRITIC_CODEX_MODEL`.
    assert re.search(r"MONOLYNX_CRITIC(?![A-Za-z0-9_])", text), f"{_rel(path)} nie wspomina zmiennej MONOLYNX_CRITIC"


@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_skill_describes_fallback_to_claude_opus_critic_on_codex_failure(path):
    paragraphs = _paragraphs(_read(path))

    fallback = [
        paragraph
        for paragraph in paragraphs
        if "codex" in paragraph and "opus" in paragraph and "timeout" in paragraph and ("kod 2" in paragraph or "kodzie 2" in paragraph)
    ]

    assert fallback, f"{_rel(path)}: brak akapitu opisującego fallback Codex -> krytyk Claude opus (błąd, timeout, kod 2)"


@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_codex_critic_rule_is_the_last_important_rule_and_numbered_13(path):
    rules = _important_rules(_read(path))

    assert max(rules) == 13, f"{_rel(path)}: ostatnia zasada ma numer {max(rules)}"
    assert sorted(rules) == list(range(1, 14))
    body = _normalize(rules[13])
    assert "codex" in body
    assert "krytyk" in body


@pytest.mark.parametrize("path", SKILL_PATHS, ids=_rel)
def test_rules_10_and_11_keep_their_numbers(path):
    rules = _important_rules(_read(path))

    assert rules[10].startswith("10. **Tryb sesji w tle**")
    assert _normalize(rules[11]).startswith("11. **trwale reguly projektowe")


@pytest.mark.parametrize("path", PIPELINE_PATHS, ids=_rel)
def test_pipeline_says_team_manager_records_critic_score_and_job_for_codex_backend(path):
    paragraphs = _paragraphs(_read(path))

    matching = [
        paragraph
        for paragraph in paragraphs
        if "backend=codex" in paragraph
        and ("team manager" in paragraph or re.search(r"\btm\b", paragraph))
        and "score" in paragraph
        and "job" in paragraph
    ]

    assert matching, f"{_rel(path)}: brak akapitu o zapisie score i zamknięciu joba krytyka przez TM przy backend=codex"


@pytest.mark.parametrize("path", RUBRIC_PATHS, ids=_rel)
def test_review_rubric_does_not_mention_codex(path):
    assert "codex" not in _normalize(_read(path))


@pytest.mark.parametrize("name", ["SKILL.md", "pipeline.md"])
def test_distribution_copy_matches_source_for_the_codex_branch(name):
    source = _read(PLUGIN_WORK / name)
    copy = _read(STATIC_WORK / name)

    for marker in ("critic_backend.py", "codex exec", "MONOLYNX_CRITIC_CODEX_MODEL", "backend=codex"):
        assert source.count(marker) == copy.count(marker), f"{name}: liczba wystąpień `{marker}` różni się między źródłem a kopią"
