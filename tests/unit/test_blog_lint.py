"""Testy lintu wpisu bloga pod AI (MON-262) -- services/blog_lint.py.

`lint_post` jest czystą funkcją (bez bazy), więc większość testów to zwykłe testy jednostkowe
na tekście. `lint_post_by_id` używa realnej bazy (db_session z rollbackiem), bo to czysty odczyt
i sprawdzamy w nim uprawnienie, wyszukanie wpisu oraz `has_translation` z zapytania.

Każda reguła ma przypadek łamiący (asercja na `rule`, `level` i `line`) i poprawny. Seed
do testów jest budowany z poprawnego wpisu bazowego `_VALID`, żeby jedyny problem w wyniku
pochodził z testowanej reguły. Znak em-dash zapisujemy jako `chr(0x2014)`.
"""

from __future__ import annotations

import dataclasses
import re
import secrets
import uuid
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services import blog_lint
from monolynx.services.blog_blocks import SourceLine, iter_source_lines
from monolynx.services.blog_lint import (
    LEVEL_ERROR,
    LEVEL_INFO,
    LEVEL_WARNING,
    LintIssue,
    LintMeta,
    LintResult,
    lint_post,
    lint_post_by_id,
)

pytestmark = pytest.mark.unit

EM_DASH = chr(0x2014)
TODAY = date(2026, 10, 6)
_DESCRIPTION = "Opis wpisu bloga o lincie: sprawdza strukturę, grafiki i metadane tak, żeby agent AI mógł go czytać."
_META = LintMeta(description=_DESCRIPTION, last_verified_at=TODAY - timedelta(days=10), has_translation=True)

# Poprawny, minimalny wpis: TLDR z 2 punktami, H2 w formie pytania, samodzielna sekcja.
# Numery linii: 1 TLDR, 2-3 punkty, 4 pusta, 5 H2, 6 pusta, 7 tekst.
_VALID = "> [!TLDR]\n> - Pierwszy punkt streszczenia.\n> - Drugi punkt streszczenia.\n\n## Czym jest lint?\n\nLint sprawdza wpis.\n"
_TLDR_LINES = "> [!TLDR]\n> - Pierwszy punkt streszczenia.\n> - Drugi punkt streszczenia.\n"
_EXEMPLARY_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "blog" / "exemplary-post.md"


def _lint(markdown: str, meta: LintMeta = _META, *, today: date = TODAY) -> LintResult:
    return lint_post(markdown, meta, today=today)


def _rules(result: LintResult) -> list[str]:
    return [issue.rule for issue in result.issues]


def _by_rule(result: LintResult, rule: str) -> list[LintIssue]:
    return [issue for issue in result.issues if issue.rule == rule]


def _only(result: LintResult, rule: str) -> LintIssue:
    found = _by_rule(result, rule)
    assert len(found) == 1, f"oczekiwano jednego problemu {rule}, jest {len(found)}: {result.issues}"
    return found[0]


def _tldr(points: list[str], marker: str = "- ") -> str:
    return "> [!TLDR]\n" + "".join(f"> {marker}{point}\n" for point in points)


def _post_with_body(body: str) -> str:
    """Poprawny wpis z dopisanym `body` po akapicie sekcji (pierwsza linia body to linia 9)."""
    return _VALID + "\n" + body + "\n"


# ---------------------------------------------------------------------------
# Wpis wzorcowy i stałe kontraktu
# ---------------------------------------------------------------------------


class TestExemplaryPost:
    def test_exemplary_post_scores_100_without_issues(self):
        markdown = _EXEMPLARY_PATH.read_text(encoding="utf-8")

        result = _lint(markdown)

        assert result.issues == []
        assert result.score == 100

    def test_valid_base_post_has_no_issues(self):
        result = _lint(_VALID)

        assert result.issues == []
        assert result.score == 100

    def test_exemplary_post_contains_no_literal_em_dash(self):
        assert EM_DASH not in _EXEMPLARY_PATH.read_text(encoding="utf-8")


class TestConstants:
    def test_thresholds_match_contract(self):
        assert blog_lint.LEVEL_ERROR == "error"
        assert blog_lint.LEVEL_WARNING == "warning"
        assert blog_lint.LEVEL_INFO == "info"
        assert blog_lint.ERROR_PENALTY == 20
        assert blog_lint.WARNING_PENALTY == 5
        assert blog_lint.DESCRIPTION_MIN_LENGTH == 50
        assert blog_lint.TLDR_MIN_POINTS == 2
        assert blog_lint.TLDR_MAX_POINTS == 5
        assert blog_lint.IMAGE_ALT_MIN_LENGTH == 11
        assert blog_lint.LAST_VERIFIED_MAX_AGE_DAYS == 180

    def test_referring_pronouns_are_lowercase_and_cover_examples_from_the_spec(self):
        pronouns = blog_lint.REFERRING_PRONOUNS

        assert isinstance(pronouns, tuple)
        assert {"to", "dlatego", "jak wyżej"} <= set(pronouns)
        assert all(p == p.casefold() and p.strip() == p for p in pronouns)

    def test_non_descriptive_link_texts_cover_examples_from_the_ticket(self):
        assert {"tutaj", "kliknij", "here", "link"} <= set(blog_lint.NON_DESCRIPTIVE_LINK_TEXTS)
        assert isinstance(blog_lint.NON_DESCRIPTIVE_LINK_TEXTS, frozenset)

    def test_lint_issue_is_a_frozen_dataclass_with_four_fields(self):
        issue = LintIssue(rule="r", level=LEVEL_ERROR, line=1, message="m")

        assert [f.name for f in dataclasses.fields(issue)] == ["rule", "level", "line", "message"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            issue.line = 2  # type: ignore[misc]

    def test_lint_meta_defaults(self):
        meta = LintMeta()

        assert meta.description == ""
        assert meta.last_verified_at is None
        assert meta.has_translation is False
        with pytest.raises(dataclasses.FrozenInstanceError):
            meta.description = "x"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# description-missing / description-length
# ---------------------------------------------------------------------------


class TestDescriptionRules:
    @pytest.mark.parametrize("description", ["", "   ", "\n\t "])
    def test_empty_description_is_an_error_at_line_zero(self, description):
        result = _lint(_VALID, dataclasses.replace(_META, description=description))

        issue = _only(result, "description-missing")
        assert (issue.level, issue.line) == (LEVEL_ERROR, 0)
        assert _by_rule(result, "description-length") == []

    def test_present_description_has_no_missing_issue(self):
        assert _by_rule(_lint(_VALID), "description-missing") == []

    @pytest.mark.parametrize("length", [1, 49, 301, 400])
    def test_description_outside_range_is_an_error_at_line_zero(self, length):
        result = _lint(_VALID, dataclasses.replace(_META, description="a" * length))

        issue = _only(result, "description-length")
        assert (issue.level, issue.line) == (LEVEL_ERROR, 0)
        assert _by_rule(result, "description-missing") == []

    @pytest.mark.parametrize("length", [50, 51, 299, 300])
    def test_description_within_range_is_accepted(self, length):
        result = _lint(_VALID, dataclasses.replace(_META, description="a" * length))

        assert _by_rule(result, "description-length") == []
        assert _by_rule(result, "description-missing") == []

    def test_length_is_measured_after_strip_below_minimum(self):
        description = "  " + "a" * 49 + "  "

        result = _lint(_VALID, dataclasses.replace(_META, description=description))

        assert len(_by_rule(result, "description-length")) == 1

    def test_length_is_measured_after_strip_at_minimum_and_maximum(self):
        for core in ("a" * 50, "a" * 300):
            result = _lint(_VALID, dataclasses.replace(_META, description=f"  {core}  "))

            assert _by_rule(result, "description-length") == []


# ---------------------------------------------------------------------------
# tldr-missing / tldr-points
# ---------------------------------------------------------------------------


class TestTldrMissing:
    def test_post_starting_with_paragraph_is_an_error_at_that_line(self):
        result = _lint("Wstęp bez streszczenia.\n\n## Czym jest lint?\n\nTekst.\n")

        issue = _only(result, "tldr-missing")
        assert (issue.level, issue.line) == (LEVEL_ERROR, 1)

    def test_line_is_the_first_non_empty_line_after_blank_lines(self):
        result = _lint("\n\nWstęp bez streszczenia.\n")

        assert _only(result, "tldr-missing").line == 3

    def test_single_h1_before_content_is_skipped_and_line_points_at_the_paragraph(self):
        result = _lint("# Tytuł wpisu\n\nWstęp bez streszczenia.\n")

        assert _only(result, "tldr-missing").line == 3

    def test_tldr_after_single_h1_is_accepted(self):
        result = _lint("# Tytuł wpisu\n\n" + _TLDR_LINES + "\n## Czym jest lint?\n\nLint sprawdza wpis.\n")

        assert result.issues == []

    def test_second_h1_is_not_skipped_when_looking_for_tldr(self):
        result = _lint("# Pierwszy\n\n# Drugi\n\n" + _TLDR_LINES)

        assert _only(result, "tldr-missing").line == 3

    def test_empty_content_reports_line_zero(self):
        result = _lint("")

        issue = _only(result, "tldr-missing")
        assert (issue.level, issue.line) == (LEVEL_ERROR, 0)

    def test_whitespace_only_content_reports_line_zero(self):
        assert _only(_lint("  \n \n\t\n"), "tldr-missing").line == 0

    def test_frontmatter_is_skipped_and_line_stays_in_source_numbering(self):
        result = _lint("---\ntitle: x\n---\nWstęp bez streszczenia.\n")

        assert _only(result, "tldr-missing").line == 4

    def test_tldr_after_frontmatter_is_accepted(self):
        result = _lint("---\ntitle: x\n---\n" + _VALID)

        assert _by_rule(result, "tldr-missing") == []

    def test_other_alert_type_is_not_a_tldr(self):
        result = _lint("> [!NOTE]\n> - Pierwszy.\n> - Drugi.\n\n## Czym jest lint?\n\nTekst.\n")

        assert _only(result, "tldr-missing").line == 1

    def test_tldr_in_the_middle_of_the_post_does_not_count(self):
        result = _lint("Wstęp.\n\n" + _TLDR_LINES)

        assert _only(result, "tldr-missing").line == 1

    @pytest.mark.parametrize("heading", ["## Sekcja", "### Sekcja", "###### Sekcja"])
    def test_only_an_h1_is_skipped_before_the_tldr(self, heading):
        result = _lint(f"{heading}\n\n" + _TLDR_LINES)

        assert _only(result, "tldr-missing").line == 1

    def test_tldr_marker_is_case_insensitive(self):
        markdown = "> [!tldr]\n> - Pierwszy punkt streszczenia.\n> - Drugi punkt streszczenia.\n\n## Czym jest lint?\n\nLint sprawdza wpis.\n"

        assert _lint(markdown).issues == []

    def test_content_with_only_a_title_reports_line_zero_without_lint_failed(self):
        result = _lint("# Jedyny nagłówek\n")

        issue = _only(result, "tldr-missing")
        assert (issue.level, issue.line) == (LEVEL_ERROR, 0)
        assert "lint-failed" not in _rules(result)

    def test_content_with_only_a_title_after_frontmatter_reports_line_zero(self):
        result = _lint("---\ntitle: x\n---\n# Jedyny nagłówek\n")

        assert _only(result, "tldr-missing").line == 0
        assert "lint-failed" not in _rules(result)


class TestTldrPoints:
    @pytest.mark.parametrize("count", [0, 1, 6, 7])
    def test_count_outside_range_is_an_error_at_the_marker_line(self, count):
        points = [f"Punkt numer {i} streszczenia." for i in range(count)]
        markdown = "# Tytuł wpisu\n\n" + _tldr(points) + "\n## Czym jest lint?\n\nTekst.\n"

        issue = _only(_lint(markdown), "tldr-points")

        assert (issue.level, issue.line) == (LEVEL_ERROR, 3)

    @pytest.mark.parametrize("count", [2, 3, 5])
    def test_count_within_range_is_accepted(self, count):
        points = [f"Punkt numer {i} streszczenia." for i in range(count)]
        markdown = _tldr(points) + "\n## Czym jest lint?\n\nTekst.\n"

        assert _by_rule(_lint(markdown), "tldr-points") == []

    @pytest.mark.parametrize("marker", ["- ", "* ", "1. "])
    def test_every_list_marker_is_counted(self, marker):
        too_few = _tldr(["Jeden punkt."], marker)
        enough = _tldr(["Pierwszy punkt.", "Drugi punkt."], marker)
        too_many = _tldr([f"Punkt {i}." for i in range(6)], marker)

        assert len(_by_rule(_lint(too_few), "tldr-points")) == 1
        assert _by_rule(_lint(enough), "tldr-points") == []
        assert len(_by_rule(_lint(too_many), "tldr-points")) == 1

    @pytest.mark.parametrize("marker", ["+ ", "1) "])
    def test_plus_and_parenthesis_markers_are_counted_as_points(self, marker):
        too_few = _tldr(["Jeden punkt."], marker)
        enough = _tldr(["Pierwszy punkt.", "Drugi punkt."], marker)

        assert len(_by_rule(_lint(too_few), "tldr-points")) == 1
        assert _by_rule(_lint(enough), "tldr-points") == []

    def test_points_after_the_end_of_the_quote_are_not_counted(self):
        markdown = "> [!TLDR]\n> - Pierwszy.\n> - Drugi.\n\n- Trzeci.\n- Czwarty.\n- Piąty.\n- Szósty.\n"

        assert _by_rule(_lint(markdown), "tldr-points") == []

    def test_missing_tldr_does_not_report_points(self):
        assert _by_rule(_lint("Wstęp.\n"), "tldr-points") == []


# ---------------------------------------------------------------------------
# image-alt-short
# ---------------------------------------------------------------------------


class TestImageAlt:
    @pytest.mark.parametrize("alt", ["", "x", "a" * 10, "  abcde  ", "  " + "a" * 9 + "  "])
    def test_alt_shorter_than_eleven_chars_is_an_error_at_the_image_line(self, alt):
        result = _lint(_post_with_body(f"![{alt}](obraz.png)"))

        issue = _only(result, "image-alt-short")
        assert (issue.level, issue.line) == (LEVEL_ERROR, 9)

    @pytest.mark.parametrize("alt", ["a" * 11, "Schemat obiegu pracy", "  " + "a" * 11 + "  "])
    def test_alt_of_eleven_chars_or_more_is_accepted(self, alt):
        assert _by_rule(_lint(_post_with_body(f"![{alt}](obraz.png)")), "image-alt-short") == []

    def test_each_short_image_is_reported_on_its_own_line(self):
        body = "![a](1.png)\n\n![Pełny tekst alternatywny](2.png)\n\n![b](3.png)"

        lines = [issue.line for issue in _by_rule(_lint(_post_with_body(body)), "image-alt-short")]

        assert lines == [9, 13]

    def test_image_inside_fenced_code_is_ignored(self):
        assert _by_rule(_lint(_post_with_body("```\n![a](1.png)\n```")), "image-alt-short") == []

    def test_a_link_is_not_checked_as_an_image(self):
        assert _by_rule(_lint(_post_with_body("[a](https://example.com/strona)")), "image-alt-short") == []


# ---------------------------------------------------------------------------
# Reguły bloków graficznych (find_block_issues widziane przez lint)
# ---------------------------------------------------------------------------

_MERMAID = "graph LR\nA[Ticket] --> B[Agent]"
_DIAGRAM_TEXT = "Ticket trafia do agenta, agent otwiera zmianę, a wynik ląduje w wiki."
_BAD_BLOCKS = [
    pytest.param(':::graphic name="agent-loop"\n:::', "graphic-missing-description", id="graphic-missing-description"),
    pytest.param(':::graphic name="no-such-component"\nOpis grafiki.\n:::', "graphic-unknown-name", id="graphic-unknown-name"),
    pytest.param(':::chart type="bar" title="T"\nSam tekst bez tabeli.\n:::', "chart-missing-data", id="chart-missing-data"),
    pytest.param(f':::diagram title="T"\n```mermaid\n{_MERMAID}\n```\n:::', "diagram-missing-description", id="diagram-missing-description"),
    pytest.param(f':::diagram title="T"\n{_DIAGRAM_TEXT}\n:::', "diagram-missing-source", id="diagram-missing-source"),
    pytest.param(':::terminal title="T"\n$ ls -la\n:::', "terminal-missing-content", id="terminal-missing-content"),
]
_GOOD_BLOCKS = [
    pytest.param(':::graphic name="agent-loop"\nSchemat pokazuje obieg pracy agenta.\n:::', id="graphic"),
    pytest.param(':::chart type="bar" title="T"\n| A | B |\n|---|---|\n| x | 1 |\n| y | 2 |\n:::', id="chart"),
    pytest.param(f':::diagram title="T"\n{_DIAGRAM_TEXT}\n\n```mermaid\n{_MERMAID}\n```\n:::', id="diagram"),
    pytest.param(':::terminal title="T"\n```console\n$ ls -la\n```\n:::', id="terminal"),
]


class TestBlockRules:
    @pytest.mark.parametrize(("block", "rule"), _BAD_BLOCKS)
    def test_broken_block_is_an_error_at_the_opener_line(self, block, rule):
        result = _lint(_post_with_body(block))

        issue = _only(result, rule)
        assert (issue.level, issue.line) == (LEVEL_ERROR, 9)

    @pytest.mark.parametrize("block", _GOOD_BLOCKS)
    def test_valid_block_gives_no_issues(self, block):
        assert _lint(_post_with_body(block)).issues == []

    def test_opener_line_follows_source_numbering_with_frontmatter(self):
        markdown = "---\ntitle: x\n---\n" + _post_with_body(':::graphic name="agent-loop"\n:::')

        issue = _only(_lint(markdown), "graphic-missing-description")

        assert issue.line == 12

    def test_block_inside_fenced_code_is_not_reported(self):
        markdown = _post_with_body('```\n:::graphic name="agent-loop"\n:::\n```')

        assert _by_rule(_lint(markdown), "graphic-missing-description") == []

    def test_unknown_and_empty_graphic_report_both_rules(self):
        result = _lint(_post_with_body(':::graphic name="no-such-component"\n:::'))

        assert {"graphic-missing-description", "graphic-unknown-name"} <= set(_rules(result))


# ---------------------------------------------------------------------------
# heading-multiple-h1 / heading-level-skip
# ---------------------------------------------------------------------------


class TestHeadingRules:
    def test_second_h1_is_an_error_at_its_own_line(self):
        markdown = "# Tytuł\n\n" + _TLDR_LINES + "\n## Czym jest lint?\n\nTekst.\n\n# Drugi tytuł\n"

        issue = _only(_lint(markdown), "heading-multiple-h1")

        assert (issue.level, issue.line) == (LEVEL_ERROR, 11)

    def test_every_h1_after_the_first_is_reported(self):
        markdown = _VALID + "\n# Pierwszy\n\n# Drugi\n\n# Trzeci\n"

        lines = [i.line for i in _by_rule(_lint(markdown), "heading-multiple-h1")]

        assert lines == [11, 13]

    def test_single_h1_in_content_is_not_reported_even_without_a_title_before_it(self):
        markdown = _VALID + "\n# Jedyny\n"

        assert _by_rule(_lint(markdown), "heading-multiple-h1") == []

    def test_single_h1_is_accepted(self):
        markdown = "# Tytuł\n\n" + _TLDR_LINES + "\n## Czym jest lint?\n\nTekst.\n"

        assert _by_rule(_lint(markdown), "heading-multiple-h1") == []

    def test_h1_inside_fenced_code_is_not_counted(self):
        markdown = "# Tytuł\n\n" + _TLDR_LINES + "\n## Czym jest lint?\n\nTekst.\n\n```bash\n# komentarz\n```\n"

        assert _by_rule(_lint(markdown), "heading-multiple-h1") == []

    def test_heading_deeper_than_previous_plus_one_is_an_error_at_its_line(self):
        markdown = _VALID + "\n#### Zbyt głęboko\n"

        issue = _only(_lint(markdown), "heading-level-skip")

        assert (issue.level, issue.line) == (LEVEL_ERROR, 9)

    def test_first_h3_is_a_skip_because_the_page_title_is_level_one(self):
        markdown = _TLDR_LINES + "\n### Pierwszy nagłówek\n\nTekst.\n"

        issue = _only(_lint(markdown), "heading-level-skip")

        assert issue.line == 5

    def test_h3_right_after_h1_is_a_skip(self):
        markdown = "# Tytuł\n\n" + _TLDR_LINES + "\n### Przeskok\n\nTekst.\n"

        assert _only(_lint(markdown), "heading-level-skip").line == 7

    @pytest.mark.parametrize(
        "headings",
        [
            pytest.param("## A?\n\nTekst.\n\n### B\n\nTekst.\n", id="h2-h3"),
            pytest.param("## A?\n\nTekst.\n\n### B\n\nTekst.\n\n## C\n\nTekst.\n", id="going-up-is-not-a-skip"),
            pytest.param("## A?\n\nTekst.\n\n### B\n\nTekst.\n\n#### C\n\nTekst.\n\n## D\n\nTekst.\n", id="h4-then-up-to-h2"),
        ],
    )
    def test_consecutive_levels_and_going_up_are_accepted(self, headings):
        markdown = _TLDR_LINES + "\n" + headings

        assert _by_rule(_lint(markdown), "heading-level-skip") == []

    def test_heading_inside_fenced_code_is_ignored_for_skips(self):
        markdown = _VALID + "\n```\n#### nie nagłówek\n```\n"

        assert _by_rule(_lint(markdown), "heading-level-skip") == []

    def test_faq_h3_does_not_count_as_a_section_heading(self):
        markdown = "# Tytuł\n\n" + _TLDR_LINES + "\n:::faq\n### Czy to działa?\nTak.\n\n### Czy to boli?\nNie.\n:::\n"

        assert _by_rule(_lint(markdown), "heading-level-skip") == []

    def test_h3_after_a_closed_faq_is_judged_again_from_the_previous_section_heading(self):
        markdown = "# Tytuł\n\n" + _TLDR_LINES + "\n:::faq\n### Czy to działa?\nTak.\n:::\n\n### Po faq\n\nTekst.\n"

        assert _only(_lint(markdown), "heading-level-skip").line == 12


# ---------------------------------------------------------------------------
# h2-starts-with-pronoun / h2-no-question
# ---------------------------------------------------------------------------


def _h2_section(first_line: str) -> str:
    return _TLDR_LINES + f"\n## Czym jest lint?\n\n{first_line}\n"


class TestH2Pronoun:
    @pytest.mark.parametrize("pronoun", list(blog_lint.REFERRING_PRONOUNS))
    def test_every_configured_pronoun_is_flagged_at_the_text_line(self, pronoun):
        result = _lint(_h2_section(f"{pronoun.capitalize()} dalej opis sekcji."))

        issue = _only(result, "h2-starts-with-pronoun")

        assert (issue.level, issue.line) == (LEVEL_WARNING, 7)

    @pytest.mark.parametrize("text", ["To jest wniosek.", "DLATEGO to działa.", "Jak wyżej, tak niżej.", "to jest wniosek."])
    def test_spec_examples_are_flagged_case_insensitively(self, text):
        assert len(_by_rule(_lint(_h2_section(text)), "h2-starts-with-pronoun")) == 1

    @pytest.mark.parametrize("text", ["Tooltip pokazuje opis.", "Tolerancja błędu jest mała.", "Dlategoś nie pasuje."])
    def test_word_that_only_starts_like_a_pronoun_is_not_flagged(self, text):
        assert _by_rule(_lint(_h2_section(text)), "h2-starts-with-pronoun") == []

    def test_self_contained_section_is_not_flagged(self):
        assert _by_rule(_lint(_h2_section("Lint sprawdza wpis i zwraca ocenę.")), "h2-starts-with-pronoun") == []

    def test_line_is_the_first_non_empty_line_of_the_section(self):
        markdown = _TLDR_LINES + "\n## Czym jest lint?\n\n\n\nTo odsyła do poprzedniej sekcji.\n"

        assert _only(_lint(markdown), "h2-starts-with-pronoun").line == 9

    def test_pronoun_in_the_second_line_of_the_section_is_not_flagged(self):
        markdown = _h2_section("Lint sprawdza wpis.\nTo zdanie jest drugie.")

        assert _by_rule(_lint(markdown), "h2-starts-with-pronoun") == []

    def test_only_the_offending_section_is_reported(self):
        markdown = _VALID + "\n## Drugie pytanie?\n\nTo odsyła wstecz.\n"

        issue = _only(_lint(markdown), "h2-starts-with-pronoun")

        assert issue.line == 11

    def test_intro_before_the_first_h2_is_not_checked(self):
        markdown = _TLDR_LINES + "\nTo jest wstęp przed pierwszą sekcją.\n\n## Czym jest lint?\n\nLint sprawdza wpis.\n"

        assert _by_rule(_lint(markdown), "h2-starts-with-pronoun") == []

    def test_code_block_as_first_content_of_the_section_means_the_next_paragraph_is_not_flagged(self):
        markdown = _h2_section("```bash\nmake lint\n```\n\nTo odsyła do kodu powyżej.")

        assert _by_rule(_lint(markdown), "h2-starts-with-pronoun") == []

    def test_pronoun_inside_the_code_block_is_not_flagged_either(self):
        markdown = _h2_section("```\nTo jest kod\n```")

        assert _by_rule(_lint(markdown), "h2-starts-with-pronoun") == []

    def test_pronoun_text_under_h3_is_not_flagged(self):
        markdown = _VALID + "\n### Podsekcja\n\nTo jest podsekcja.\n"

        assert _by_rule(_lint(markdown), "h2-starts-with-pronoun") == []


class TestH2Question:
    def test_no_h2_is_a_warning_at_line_zero(self):
        issue = _only(_lint(_TLDR_LINES + "\nTekst bez sekcji.\n"), "h2-no-question")

        assert (issue.level, issue.line) == (LEVEL_WARNING, 0)

    def test_h2_without_question_mark_is_a_warning_at_the_first_h2(self):
        markdown = _TLDR_LINES + "\n## Pierwsza sekcja\n\nTekst.\n\n## Druga sekcja\n\nTekst.\n"

        issue = _only(_lint(markdown), "h2-no-question")

        assert (issue.level, issue.line) == (LEVEL_WARNING, 5)

    def test_one_question_among_h2_headings_is_enough(self):
        markdown = _TLDR_LINES + "\n## Pierwsza sekcja\n\nTekst.\n\n## Druga sekcja?\n\nTekst.\n"

        assert _by_rule(_lint(markdown), "h2-no-question") == []

    def test_trailing_spaces_after_the_question_mark_are_ignored(self):
        markdown = _TLDR_LINES + "\n## Czym jest lint?   \n\nTekst.\n"

        assert _by_rule(_lint(markdown), "h2-no-question") == []

    def test_question_in_h3_does_not_count(self):
        markdown = _TLDR_LINES + "\n## Sekcja\n\nTekst.\n\n### Czy to działa?\n\nTak.\n"

        assert _only(_lint(markdown), "h2-no-question").line == 5

    def test_h2_inside_fenced_code_is_not_a_heading(self):
        markdown = _TLDR_LINES + "\n## Sekcja\n\nTekst.\n\n```\n## Czy to działa?\n```\n"

        assert _only(_lint(markdown), "h2-no-question").line == 5

    def test_valid_base_has_a_question_h2(self):
        assert _by_rule(_lint(_VALID), "h2-no-question") == []


# ---------------------------------------------------------------------------
# link-non-descriptive
# ---------------------------------------------------------------------------


class TestLinkText:
    @pytest.mark.parametrize("text", ["tutaj", "Kliknij", "here", "LINK", "tutaj!", " tutaj ", "link."])
    def test_non_descriptive_text_is_a_warning_at_the_link_line(self, text):
        result = _lint(_post_with_body(f"Zobacz [{text}](https://example.com/strona)."))

        issue = _only(result, "link-non-descriptive")

        assert (issue.level, issue.line) == (LEVEL_WARNING, 9)

    @pytest.mark.parametrize("text", ["dokumentacja klienta CLI", "tutaj, na stronie projektu", "Linkedin", "kliknięcie"])
    def test_descriptive_text_is_accepted(self, text):
        assert _by_rule(_lint(_post_with_body(f"Zobacz [{text}](https://example.com/strona).")), "link-non-descriptive") == []

    def test_image_with_a_non_descriptive_alt_is_not_a_link(self):
        result = _lint(_post_with_body("![tutaj](obraz.png)"))

        assert _by_rule(result, "link-non-descriptive") == []
        assert len(_by_rule(result, "image-alt-short")) == 1

    def test_link_inside_fenced_code_is_ignored(self):
        assert _by_rule(_lint(_post_with_body("```\n[tutaj](https://example.com)\n```")), "link-non-descriptive") == []

    def test_every_non_descriptive_link_is_reported_on_its_own_line(self):
        body = "[tutaj](https://example.com/a)\n\n[Dokumentacja klienta](https://example.com/b)\n\n[kliknij](https://example.com/c)"

        lines = [i.line for i in _by_rule(_lint(_post_with_body(body)), "link-non-descriptive")]

        assert lines == [9, 13]


# ---------------------------------------------------------------------------
# last-verified-missing / last-verified-stale
# ---------------------------------------------------------------------------


class TestLastVerified:
    def test_missing_date_is_a_warning_at_line_zero(self):
        result = _lint(_VALID, dataclasses.replace(_META, last_verified_at=None))

        issue = _only(result, "last-verified-missing")
        assert (issue.level, issue.line) == (LEVEL_WARNING, 0)
        assert _by_rule(result, "last-verified-stale") == []

    def test_present_date_has_no_missing_issue(self):
        assert _by_rule(_lint(_VALID), "last-verified-missing") == []

    def test_date_older_than_180_days_is_stale_at_line_zero(self):
        meta = dataclasses.replace(_META, last_verified_at=TODAY - timedelta(days=181))

        result = _lint(_VALID, meta)

        issue = _only(result, "last-verified-stale")
        assert (issue.level, issue.line) == (LEVEL_WARNING, 0)
        assert _by_rule(result, "last-verified-missing") == []

    @pytest.mark.parametrize("age_days", [0, 1, 179, 180])
    def test_date_up_to_180_days_old_is_fresh(self, age_days):
        meta = dataclasses.replace(_META, last_verified_at=TODAY - timedelta(days=age_days))

        assert _lint(_VALID, meta).issues == []

    def test_future_date_is_not_stale(self):
        meta = dataclasses.replace(_META, last_verified_at=TODAY + timedelta(days=30))

        assert _by_rule(_lint(_VALID, meta), "last-verified-stale") == []

    def test_age_is_counted_from_the_given_today(self):
        meta = dataclasses.replace(_META, last_verified_at=date(2026, 1, 1))

        assert _by_rule(lint_post(_VALID, meta, today=date(2026, 6, 30)), "last-verified-stale") == []
        assert len(_by_rule(lint_post(_VALID, meta, today=date(2026, 7, 1)), "last-verified-stale")) == 1

    def test_default_today_is_the_real_current_date(self):
        fresh = dataclasses.replace(_META, last_verified_at=date.today() - timedelta(days=10))
        old = dataclasses.replace(_META, last_verified_at=date.today() - timedelta(days=400))

        assert _by_rule(lint_post(_VALID, fresh), "last-verified-stale") == []
        assert len(_by_rule(lint_post(_VALID, old), "last-verified-stale")) == 1


# ---------------------------------------------------------------------------
# em-dash
# ---------------------------------------------------------------------------


class TestEmDash:
    def test_em_dash_in_text_is_a_warning_at_its_line(self):
        result = _lint(_post_with_body(f"Zdanie {EM_DASH} z myślnikiem."))

        issue = _only(result, "em-dash")

        assert (issue.level, issue.line) == (LEVEL_WARNING, 9)

    def test_two_em_dashes_in_one_line_give_one_issue(self):
        result = _lint(_post_with_body(f"Raz {EM_DASH} dwa {EM_DASH} trzy."))

        assert len(_by_rule(result, "em-dash")) == 1

    def test_each_line_with_em_dash_gets_its_own_issue(self):
        body = f"Pierwsza {EM_DASH} linia.\n\nBez myślnika.\n\nDruga {EM_DASH} linia."

        lines = [i.line for i in _by_rule(_lint(_post_with_body(body)), "em-dash")]

        assert lines == [9, 13]

    def test_hyphen_and_en_dash_are_not_flagged(self):
        body = f"Zdanie - z łącznikiem i 2020{chr(0x2013)}2021."

        assert _by_rule(_lint(_post_with_body(body)), "em-dash") == []

    def test_em_dash_inside_fenced_code_is_ignored(self):
        assert _by_rule(_lint(_post_with_body(f"```\nkod {EM_DASH} bez problemu\n```")), "em-dash") == []

    def test_em_dash_in_frontmatter_is_ignored(self):
        markdown = f"---\ntitle: Tytuł {EM_DASH} z myślnikiem\n---\n" + _VALID

        assert _by_rule(_lint(markdown), "em-dash") == []

    def test_em_dash_in_a_heading_is_reported(self):
        markdown = _VALID + f"\n## Sekcja {EM_DASH} pytanie?\n"

        assert _only(_lint(markdown), "em-dash").line == 9


# ---------------------------------------------------------------------------
# translation-missing
# ---------------------------------------------------------------------------


class TestTranslation:
    def test_missing_translation_is_an_info_at_line_zero(self):
        result = _lint(_VALID, dataclasses.replace(_META, has_translation=False))

        issue = _only(result, "translation-missing")
        assert (issue.level, issue.line) == (LEVEL_INFO, 0)

    def test_translation_present_has_no_issue(self):
        assert _by_rule(_lint(_VALID), "translation-missing") == []

    def test_info_does_not_lower_the_score(self):
        result = _lint(_VALID, dataclasses.replace(_META, has_translation=False))

        assert result.score == 100
        assert (result.error_count, result.warning_count, result.info_count) == (0, 0, 1)
        assert result.has_errors is False


# ---------------------------------------------------------------------------
# Fałszywe trafienia: kod ogrodzony
# ---------------------------------------------------------------------------

_FENCE_BODY = (
    "# Drugi H1 w kodzie\n"
    "#### skok poziomu w kodzie\n"
    "## Nagłówek bez pytania w kodzie\n"
    "![a](obraz.png)\n"
    "[tutaj](https://example.com)\n"
    f"kod {EM_DASH} z myślnikiem\n"
    ':::graphic name="agent-loop"\n:::\n'
)


class TestNoFalsePositives:
    @pytest.mark.parametrize("fence", ["```", "~~~", "```python"])
    def test_constructs_inside_a_fenced_block_are_not_reported(self, fence):
        closing = fence[:3]
        markdown = _VALID + f"\n{fence}\n{_FENCE_BODY}{closing}\n"

        assert _lint(markdown).issues == []

    def test_a_real_violation_right_after_the_fence_keeps_its_line(self):
        markdown = _VALID + f"\n```\n{_FENCE_BODY}```\n\n![a](obraz.png)\n"

        issue = _only(_lint(markdown), "image-alt-short")

        assert issue.line == 9 + _FENCE_BODY.count("\n") + 3

    def test_plain_h1_h2_h3_sequence_without_violations_is_clean(self):
        markdown = "# Tytuł\n\n" + _TLDR_LINES + "\n## Czym jest lint?\n\nLint sprawdza wpis.\n\n### Szczegóły\n\nSzczegóły reguł.\n"

        assert _lint(markdown).issues == []

    def test_faq_container_with_h3_questions_is_clean(self):
        markdown = _VALID + "\n:::faq\n### Czy to działa?\nTak.\n\n### Czy to boli?\nNie.\n:::\n"

        assert _lint(markdown).issues == []


# ---------------------------------------------------------------------------
# Numery linii w źródle
# ---------------------------------------------------------------------------


class TestLineNumbers:
    def test_frontmatter_does_not_shift_line_numbers_of_three_rules(self):
        markdown = (
            "---\n"  # 1
            "title: Tytuł\n"  # 2
            "tags: [a, b]\n"  # 3
            "---\n"  # 4
            "> [!TLDR]\n"  # 5
            "> - Pierwszy punkt.\n"  # 6
            "> - Drugi punkt.\n"  # 7
            "\n"  # 8
            "## Czym jest lint?\n"  # 9
            "\n"  # 10
            "Lint sprawdza wpis.\n"  # 11
            "\n"  # 12
            "![a](obraz.png)\n"  # 13
            "\n"  # 14
            "[tutaj](https://example.com)\n"  # 15
            "\n"  # 16
            f"Zdanie {EM_DASH} z myślnikiem.\n"  # 17
            "\n"  # 18
            "#### Zbyt głęboko\n"  # 19
            "\n"  # 20
            ':::graphic name="agent-loop"\n'  # 21
            ":::\n"  # 22
        )

        result = _lint(markdown)

        assert [(i.line, i.rule) for i in result.issues] == [
            (13, "image-alt-short"),
            (15, "link-non-descriptive"),
            (17, "em-dash"),
            (19, "heading-level-skip"),
            (21, "graphic-missing-description"),
        ]

    def test_metadata_rules_use_line_zero(self):
        result = _lint(_VALID, LintMeta())

        assert {(i.rule, i.line) for i in result.issues} == {
            ("description-missing", 0),
            ("last-verified-missing", 0),
            ("translation-missing", 0),
        }

    def test_crlf_line_endings_keep_line_numbers(self):
        markdown = _VALID.replace("\n", "\r\n") + "\r\n![a](obraz.png)\r\n"

        assert _only(_lint(markdown), "image-alt-short").line == 9


# ---------------------------------------------------------------------------
# Odporność: brak wyjątku
# ---------------------------------------------------------------------------


class TestNeverRaises:
    @pytest.mark.parametrize(
        "markdown",
        [
            pytest.param("", id="empty"),
            pytest.param("   \n\n\t \n", id="whitespace"),
            pytest.param("---\ntitle: x\n---", id="only-frontmatter"),
            pytest.param("---\ntitle: x\n---\n", id="only-frontmatter-newline"),
            pytest.param("---\ntitle: x\nbez zamknięcia\n\n## Czym jest lint?\n", id="unclosed-frontmatter"),
            pytest.param(':::graphic name="agent-loop"\nopis bez zamknięcia', id="unclosed-container"),
            pytest.param(":::\n:::\n:::", id="bare-markers"),
            pytest.param("```\nniezamknięty kod\n# nagłówek\n", id="unclosed-fence"),
            pytest.param("\x00\x01 ![](\n[](\n##", id="garbage"),
            pytest.param("# " + "a" * 20000, id="very-long-heading"),
            pytest.param("[" * 5000 + "](" * 5000, id="bracket-storm"),
        ],
    )
    def test_any_text_gives_a_result_with_a_valid_score(self, markdown):
        result = _lint(markdown, LintMeta())

        assert isinstance(result, LintResult)
        assert isinstance(result.issues, list)
        assert 0 <= result.score <= 100
        assert all(isinstance(i, LintIssue) for i in result.issues)

    def test_empty_content_with_default_meta_has_the_expected_issues(self):
        result = _lint("", LintMeta())

        assert sorted(_rules(result)) == [
            "description-missing",
            "h2-no-question",
            "last-verified-missing",
            "tldr-missing",
            "translation-missing",
        ]
        assert result.score == 50

    def test_only_frontmatter_has_no_content_issues_besides_the_missing_tldr(self):
        result = _lint("---\ntitle: x\n---", _META)

        assert sorted(_rules(result)) == ["h2-no-question", "tldr-missing"]

    def test_unclosed_container_is_plain_text_and_gives_no_block_issue(self):
        result = _lint(_post_with_body(':::graphic name="agent-loop"\nopis bez zamknięcia'))

        assert _by_rule(result, "graphic-missing-description") == []

    def test_message_is_polish_text_without_em_dash(self):
        result = _lint("", LintMeta())

        for issue in result.issues:
            assert issue.message.strip()
            assert EM_DASH not in issue.message


# ---------------------------------------------------------------------------
# Ocena, liczniki i sortowanie
# ---------------------------------------------------------------------------

_MIXED = "Wstęp.\n"  # tldr-missing (error, 1), h2-no-question (warning, 0)


class TestScoreAndCounters:
    def test_mixed_result_counts_and_score(self):
        result = _lint(_MIXED, LintMeta())

        assert (result.error_count, result.warning_count, result.info_count) == (2, 2, 1)
        assert result.has_errors is True
        assert result.score == 50

    def test_one_error_costs_twenty_points(self):
        result = _lint(_VALID, dataclasses.replace(_META, description=""))

        assert (result.error_count, result.warning_count, result.info_count) == (1, 0, 0)
        assert result.score == 80
        assert result.has_errors is True

    def test_one_warning_costs_five_points_and_is_not_an_error(self):
        result = _lint(_VALID, dataclasses.replace(_META, last_verified_at=None))

        assert (result.error_count, result.warning_count, result.info_count) == (0, 1, 0)
        assert result.score == 95
        assert result.has_errors is False

    def test_error_and_warning_combine(self):
        meta = dataclasses.replace(_META, description="", last_verified_at=None)

        assert _lint(_VALID, meta).score == 75

    def test_score_never_drops_below_zero(self):
        body = "\n\n".join(f"![a{i}](obraz{i}.png)" for i in range(6))

        result = _lint(body, LintMeta())

        assert result.error_count >= 7
        assert result.score == 0

    def test_exactly_five_errors_give_zero(self):
        body = "> [!TLDR]\n> - Jeden.\n\n## Czym jest lint?\n\nTekst.\n\n![a](1.png)\n\n![b](2.png)\n\n![c](3.png)\n"
        meta = dataclasses.replace(_META, description="")

        result = _lint(body, meta)

        assert result.error_count == 5
        assert result.score == 0

    def test_score_follows_the_formula_for_each_count_combination(self):
        for errors in range(4):
            body = _VALID + "\n" + "\n\n".join(f"![a](o{i}.png)" for i in range(errors)) + "\n"
            for warnings in range(3):
                meta = dataclasses.replace(
                    _META,
                    last_verified_at=None if warnings >= 1 else _META.last_verified_at,
                )
                text = body + "".join(f"\n[tutaj](https://example.com/{i})\n" for i in range(max(0, warnings - 1)))

                result = _lint(text, meta)

                assert result.score == max(0, 100 - 20 * result.error_count - 5 * result.warning_count)
                assert result.error_count == errors
                assert result.warning_count == warnings

    def test_issues_are_sorted_by_line_then_rule(self):
        markdown = (
            "> [!TLDR]\n"  # 1
            "> - Pierwszy punkt.\n"  # 2
            "> - Drugi punkt.\n"  # 3
            "\n"  # 4
            "## Czym jest lint?\n"  # 5
            "\n"  # 6
            f"Zobacz [tutaj](https://example.com/a) ![x](y.png) {EM_DASH} koniec.\n"  # 7
            "\n"  # 8
            f"Druga linia {EM_DASH} z myślnikiem.\n"  # 9
        )

        result = _lint(markdown, LintMeta())

        assert [(i.line, i.rule) for i in result.issues] == [
            (0, "description-missing"),
            (0, "last-verified-missing"),
            (0, "translation-missing"),
            (7, "em-dash"),
            (7, "image-alt-short"),
            (7, "link-non-descriptive"),
            (9, "em-dash"),
        ]

    def test_result_is_a_frozen_dataclass_with_issues_and_score(self):
        result = _lint(_VALID)

        assert dataclasses.is_dataclass(result)
        assert {f.name for f in dataclasses.fields(result)} == {"issues", "score"}
        with pytest.raises(dataclasses.FrozenInstanceError):
            result.score = 1  # type: ignore[misc]

    def test_levels_are_error_warning_info_only(self):
        result = _lint(_MIXED, LintMeta())

        assert {i.level for i in result.issues} == {LEVEL_ERROR, LEVEL_WARNING, LEVEL_INFO}


# ---------------------------------------------------------------------------
# lint_post_by_id
# ---------------------------------------------------------------------------


def _uid() -> str:
    return uuid.uuid4().hex[:8]


async def _make_user(db, tag: str) -> User:
    user = User(email=f"bloglint-{tag}-{_uid()}@example.com", is_superuser=False)
    db.add(user)
    await db.flush()
    return user


async def _make_project(db, tag: str) -> Project:
    project = Project(
        name=f"Blog lint {tag}",
        slug=f"bloglint-{tag}-{_uid()}",
        code="L" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db.add(project)
    await db.flush()
    return project


async def _setup(db, tag: str) -> tuple[User, Project]:
    user = await _make_user(db, tag)
    project = await _make_project(db, tag)
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
    await db.flush()
    return user, project


async def _make_role_member(db, project: Project, user: User, permissions: dict) -> None:
    role = Role(name=f"role-{_uid()}", project_id=project.id, permissions=permissions, is_system=False)
    db.add(role)
    await db.flush()
    db.add(ProjectMember(project_id=project.id, user_id=user.id, role="member", role_id=role.id))
    await db.flush()


async def _make_page(db, project: Project, user: User) -> WikiPage:
    slug = f"strona-{_uid()}"
    page = WikiPage(
        project_id=project.id,
        title=slug,
        slug=slug,
        position=0,
        minio_path=f"{project.slug}/{uuid.uuid4()}.md",
        is_ai_touched=False,
        is_public=False,
        created_by_id=user.id,
        last_edited_by_id=user.id,
    )
    db.add(page)
    await db.flush()
    return page


async def _make_post(db, page: WikiPage, **fields) -> BlogPost:
    post = BlogPost(page_id=page.id, slug=page.slug, **fields)
    db.add(post)
    await db.flush()
    return post


class TestResultFormatting:
    def test_table_row_escapes_a_pipe_in_the_message_so_the_row_keeps_four_cells(self):
        result = LintResult(issues=[LintIssue("em-dash", LEVEL_WARNING, 7, "Użyj a|b zamiast długiego myślnika")], score=95)

        table = blog_lint.format_lint_table(result)

        row = table.splitlines()[2]
        assert row == "| 7 | warning | em-dash | Użyj a\\|b zamiast długiego myślnika |"
        assert len(re.split(r"(?<!\\)\|", row)) == 6

    def test_table_has_header_separator_one_row_per_issue_and_score_line(self):
        issues = [LintIssue("tldr-missing", LEVEL_ERROR, 1, "Brak TLDR"), LintIssue("translation-missing", LEVEL_INFO, 0, "Brak tłumaczenia")]

        table = blog_lint.format_lint_table(LintResult(issues=issues, score=80))

        assert table.splitlines() == [
            "| Linia | Poziom | Reguła | Komunikat |",
            "|---|---|---|---|",
            "| 1 | error | tldr-missing | Brak TLDR |",
            "| 0 | info | translation-missing | Brak tłumaczenia |",
            "",
            "Ocena: 80/100",
        ]

    def test_table_without_issues_is_the_no_problems_text_with_score(self):
        assert blog_lint.format_lint_table(LintResult(issues=[], score=100)) == "Brak problemów.\n\nOcena: 100/100"

    def test_result_dict_has_page_id_as_text_counters_and_issue_dicts(self):
        page_id = uuid.uuid4()
        issues = [
            LintIssue("tldr-missing", LEVEL_ERROR, 1, "Brak TLDR"),
            LintIssue("em-dash", LEVEL_WARNING, 4, "Myślnik"),
            LintIssue("translation-missing", LEVEL_INFO, 0, "Brak tłumaczenia"),
        ]

        data = blog_lint.lint_result_dict(page_id, LintResult(issues=issues, score=75))

        assert data == {
            "page_id": str(page_id),
            "score": 75,
            "error_count": 1,
            "warning_count": 1,
            "info_count": 1,
            "issues": [
                {"rule": "tldr-missing", "level": "error", "line": 1, "message": "Brak TLDR"},
                {"rule": "em-dash", "level": "warning", "line": 4, "message": "Myślnik"},
                {"rule": "translation-missing", "level": "info", "line": 0, "message": "Brak tłumaczenia"},
            ],
        }


class TestLintFailedFallback:
    @pytest.mark.parametrize("target", ["iter_source_lines", "find_block_issues"])
    def test_internal_failure_gives_a_lint_failed_error_instead_of_an_exception(self, target):
        with patch(f"monolynx.services.blog_lint.{target}", side_effect=RuntimeError("boom")):
            result = _lint(_VALID)

        assert isinstance(result, LintResult)
        assert _rules(result) == ["lint-failed"]
        issue = result.issues[0]
        assert (issue.level, issue.line) == (LEVEL_ERROR, 0)
        assert issue.message.strip()
        assert result.has_errors is True
        assert result.score == 80

    def test_failure_in_a_rule_helper_is_also_caught(self):
        with patch("monolynx.services.blog_lint._meta_issues", side_effect=ValueError("boom")):
            result = _lint(_VALID)

        assert _rules(result) == ["lint-failed"]
        assert result.score < 100

    def test_no_failure_means_no_lint_failed_issue(self):
        assert "lint-failed" not in _rules(_lint(_VALID))


class TestIterSourceLines:
    def test_numbers_lines_from_one_with_text_and_defaults(self):
        lines = iter_source_lines("pierwsza\ndruga\n\nczwarta")

        assert lines == [
            SourceLine(1, "pierwsza", False, None),
            SourceLine(2, "druga", False, None),
            SourceLine(3, "", False, None),
            SourceLine(4, "czwarta", False, None),
        ]

    @pytest.mark.parametrize("fence", ["```", "~~~"])
    def test_fenced_code_including_both_fences_is_in_code(self, fence):
        lines = iter_source_lines(f"przed\n{fence}py\nkod\n{fence}\npo")

        assert [(line.number, line.text, line.in_code) for line in lines] == [
            (1, "przed", False),
            (2, f"{fence}py", True),
            (3, "kod", True),
            (4, fence, True),
            (5, "po", False),
        ]
        assert {line.container for line in lines} == {None}

    def test_unclosed_fence_keeps_the_rest_in_code(self):
        lines = iter_source_lines("```\nkod\n# nie nagłówek")

        assert [line.in_code for line in lines] == [True, True, True]

    def test_closed_container_reports_its_kind_on_marker_and_inner_lines(self):
        lines = iter_source_lines(":::faq\n### Czy to działa?\nTak.\n:::\npo kontenerze")

        assert [(line.number, line.text, line.container, line.in_code) for line in lines] == [
            (1, ":::faq", "faq", False),
            (2, "### Czy to działa?", "faq", False),
            (3, "Tak.", "faq", False),
            (4, ":::", "faq", False),
            (5, "po kontenerze", None, False),
        ]

    def test_numbering_continues_across_text_before_and_after_a_container(self):
        lines = iter_source_lines('Wstęp.\n\n:::graphic name="agent-loop"\nOpis.\n:::\n\nKoniec.')

        assert [(line.number, line.container) for line in lines] == [
            (1, None),
            (2, None),
            (3, "graphic"),
            (4, "graphic"),
            (5, "graphic"),
            (6, None),
            (7, None),
        ]

    def test_container_markers_inside_a_fence_do_not_close_the_container(self):
        lines = iter_source_lines(":::faq\n```\n:::\n```\n:::")

        assert [(line.container, line.in_code) for line in lines] == [
            ("faq", False),
            ("faq", True),
            ("faq", True),
            ("faq", True),
            ("faq", False),
        ]

    def test_container_opener_inside_a_fence_is_plain_code(self):
        lines = iter_source_lines("```\n:::faq\n```")

        assert [(line.container, line.in_code) for line in lines] == [(None, True), (None, True), (None, True)]

    def test_unclosed_container_is_plain_text(self):
        lines = iter_source_lines(":::faq\nbrak zamknięcia")

        assert lines == [SourceLine(1, ":::faq", False, None), SourceLine(2, "brak zamknięcia", False, None)]

    @pytest.mark.parametrize("markdown", ["", ":::\n:::\n:::", "\x00", ":::faq"])
    def test_garbage_and_empty_input_never_raise(self, markdown):
        lines = iter_source_lines(markdown)

        assert isinstance(lines, list)
        assert all(isinstance(line, SourceLine) for line in lines)

    def test_empty_text_gives_no_lines(self):
        assert iter_source_lines("") == []

    def test_internal_failure_returns_an_empty_list(self):
        with patch("monolynx.services.blog_blocks._split_containers", side_effect=RuntimeError("boom")):
            assert iter_source_lines("tekst") == []

    def test_source_line_is_a_frozen_dataclass(self):
        line = iter_source_lines("a")[0]

        assert [f.name for f in dataclasses.fields(line)] == ["number", "text", "in_code", "container"]
        with pytest.raises(dataclasses.FrozenInstanceError):
            line.number = 2  # type: ignore[misc]


class TestLintPostById:
    async def test_returns_the_lint_of_content_from_content_fn_with_post_metadata(self, db_session):
        user, project = await _setup(db_session, "ok")
        page = await _make_page(db_session, project, user)
        verified = date.today() - timedelta(days=5)
        await _make_post(db_session, page, description=_DESCRIPTION, last_verified_at=verified)
        calls: list[WikiPage] = []

        def content_fn(p):
            calls.append(p)
            return _VALID

        result = await lint_post_by_id(db_session, user, project, page.id, content_fn=content_fn)

        assert [p.id for p in calls] == [page.id]
        assert isinstance(result, LintResult)
        assert _rules(result) == ["translation-missing"]
        assert result.score == 100

    async def test_description_and_last_verified_from_the_row_reach_the_lint(self, db_session):
        user, project = await _setup(db_session, "meta")
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page, description="", last_verified_at=date.today() - timedelta(days=400))

        result = await lint_post_by_id(db_session, user, project, page.id, content_fn=lambda p: _VALID)

        assert {"description-missing", "last-verified-stale"} <= set(_rules(result))
        assert "last-verified-missing" not in _rules(result)

    async def test_content_is_linted_not_ignored(self, db_session):
        user, project = await _setup(db_session, "content")
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page, description=_DESCRIPTION, last_verified_at=date.today())

        result = await lint_post_by_id(db_session, user, project, page.id, content_fn=lambda p: "Wstęp bez streszczenia.\n")

        assert _only(result, "tldr-missing").line == 1

    async def test_post_with_translation_of_id_has_translation(self, db_session):
        user, project = await _setup(db_session, "tr-child")
        original_page = await _make_page(db_session, project, user)
        await _make_post(db_session, original_page, lang="pl")
        child_page = await _make_page(db_session, project, user)
        await _make_post(db_session, child_page, lang="en", translation_of_id=original_page.id)

        result = await lint_post_by_id(db_session, user, project, child_page.id, content_fn=lambda p: _VALID)

        assert "translation-missing" not in _rules(result)

    async def test_post_pointed_at_by_another_post_has_translation(self, db_session):
        user, project = await _setup(db_session, "tr-parent")
        original_page = await _make_page(db_session, project, user)
        await _make_post(db_session, original_page, lang="pl")
        child_page = await _make_page(db_session, project, user)
        await _make_post(db_session, child_page, lang="en", translation_of_id=original_page.id)

        result = await lint_post_by_id(db_session, user, project, original_page.id, content_fn=lambda p: _VALID)

        assert "translation-missing" not in _rules(result)

    async def test_post_without_any_translation_gets_the_info(self, db_session):
        user, project = await _setup(db_session, "tr-none")
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page)
        other = await _make_page(db_session, project, user)
        await _make_post(db_session, other)

        result = await lint_post_by_id(db_session, user, project, page.id, content_fn=lambda p: _VALID)

        assert _only(result, "translation-missing").level == LEVEL_INFO

    async def test_default_content_fn_reads_wiki_page_content(self, db_session):
        user, project = await _setup(db_session, "default-fn")
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page, description=_DESCRIPTION)

        with patch("monolynx.services.wiki.get_page_content", return_value="# z domyślnego\n\nWstęp.\n") as mock_content:
            result = await lint_post_by_id(db_session, user, project, page.id)

        assert mock_content.call_count == 1
        assert _only(result, "tldr-missing").line == 3

    async def test_nonexistent_page_raises_page_not_found(self, db_session):
        user, project = await _setup(db_session, "missing")

        with pytest.raises(ValueError) as exc_info:
            await lint_post_by_id(db_session, user, project, uuid.uuid4(), content_fn=lambda p: "x")

        assert str(exc_info.value) == "Strona wiki nie istnieje"

    async def test_page_from_other_project_raises_page_not_found(self, db_session):
        user, project = await _setup(db_session, "foreign")
        other_project = await _make_project(db_session, "foreign-other")
        foreign = await _make_page(db_session, other_project, user)
        await _make_post(db_session, foreign)

        with pytest.raises(ValueError) as exc_info:
            await lint_post_by_id(db_session, user, project, foreign.id, content_fn=lambda p: "x")

        assert str(exc_info.value) == "Strona wiki nie istnieje"

    async def test_page_without_post_row_raises_post_not_found_and_fetches_no_content(self, db_session):
        user, project = await _setup(db_session, "norow")
        page = await _make_page(db_session, project, user)
        calls: list[WikiPage] = []

        def content_fn(p):
            calls.append(p)
            return "x"

        with pytest.raises(ValueError) as exc_info:
            await lint_post_by_id(db_session, user, project, page.id, content_fn=content_fn)

        assert str(exc_info.value) == "Wpis bloga nie istnieje"
        assert calls == []

    async def test_role_without_wiki_read_is_refused_with_the_full_text(self, db_session):
        user = await _make_user(db_session, "perm")
        project = await _make_project(db_session, "perm")
        await _make_role_member(db_session, project, user, {"scrum": ["read"]})
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page)

        with pytest.raises(ValueError) as exc_info:
            await lint_post_by_id(db_session, user, project, page.id, content_fn=lambda p: _VALID)

        assert str(exc_info.value) == "Brak uprawnienia wiki:read"

    async def test_role_without_wiki_read_is_refused_even_for_nonexistent_page(self, db_session):
        user = await _make_user(db_session, "perm-missing")
        project = await _make_project(db_session, "perm-missing")
        await _make_role_member(db_session, project, user, {"scrum": ["read"]})

        with pytest.raises(ValueError) as exc_info:
            await lint_post_by_id(db_session, user, project, uuid.uuid4(), content_fn=lambda p: "x")

        assert str(exc_info.value) == "Brak uprawnienia wiki:read"

    async def test_role_with_wiki_read_only_may_lint(self, db_session):
        user = await _make_user(db_session, "perm-read")
        project = await _make_project(db_session, "perm-read")
        await _make_role_member(db_session, project, user, {"wiki": ["read"]})
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page, description=_DESCRIPTION)

        result = await lint_post_by_id(db_session, user, project, page.id, content_fn=lambda p: _VALID)

        assert isinstance(result, LintResult)

    async def test_lint_does_not_commit(self, db_session):
        user, project = await _setup(db_session, "nocommit")
        page = await _make_page(db_session, project, user)
        await _make_post(db_session, page)
        commits: list[int] = []
        original = db_session.commit

        async def spy_commit():
            commits.append(1)
            await original()

        db_session.commit = spy_commit  # type: ignore[method-assign]
        try:
            await lint_post_by_id(db_session, user, project, page.id, content_fn=lambda p: _VALID)
        finally:
            db_session.commit = original  # type: ignore[method-assign]

        assert commits == []
