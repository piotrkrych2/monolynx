"""Lint wpisu bloga pod AI (MON-262).

`lint_post` to czysta funkcja (tekst + metadane, bez bazy i sieci), która nigdy nie rzuca:
zwraca `LintResult` z listą problemów `LintIssue(rule, level, line, message)` i oceną 0-100
(`100 - 20 * bledy - 5 * ostrzezenia`, informacje nie obnizaja oceny). `line` to numer linii
zrodla od 1, takze gdy na poczatku jest frontmatter `---...---` (jest pomijany w analizie,
ale nie przesuwa numeracji); problemy metadanych i bez miejsca w tresci maja `line = 0`.
`lint_post_by_id` laduje wpis (czysty odczyt, bez commitu) i sklada metadane z wiersza `BlogPost`.
`lint_result_dict` i `format_lint_table` buduja wynik dla MCP i API v2 z jednego miejsca.

Reguly blokow graficznych pochodza z `blog_blocks.find_block_issues`, a kod ogrodzony i
kontener `:::faq` rozpoznaje `blog_blocks.iter_source_lines`.
"""

from __future__ import annotations

import logging
import re
import string
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.blog_post import BlogPost
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_page import WikiPage
from monolynx.services.blog import DESCRIPTION_MAX_LENGTH
from monolynx.services.blog_blocks import SourceLine, find_block_issues, iter_source_lines
from monolynx.services.permissions import ensure_permission

logger = logging.getLogger("monolynx.services.blog_lint")

LEVEL_ERROR = "error"
LEVEL_WARNING = "warning"
LEVEL_INFO = "info"

ERROR_PENALTY = 20
WARNING_PENALTY = 5
DESCRIPTION_MIN_LENGTH = 50
TLDR_MIN_POINTS = 2
TLDR_MAX_POINTS = 5
IMAGE_ALT_MIN_LENGTH = 11
LAST_VERIFIED_MAX_AGE_DAYS = 180

# Zaimki i zwroty odsylajace do poprzedniej sekcji; male litery, dopasowanie po granicy slowa.
REFERRING_PRONOUNS: tuple[str, ...] = (
    "to",
    "ta",
    "ten",
    "te",
    "ci",
    "dlatego",
    "zatem",
    "więc",
    "jak wyżej",
    "jak wspomniano",
    "jak powyżej",
    "this",
    "that",
    "these",
    "those",
    "it",
    "therefore",
    "thus",
    "as above",
    "as mentioned",
)
NON_DESCRIPTIVE_LINK_TEXTS: frozenset[str] = frozenset(
    {"tutaj", "tu", "kliknij", "kliknij tutaj", "czytaj więcej", "więcej", "link", "here", "click here", "click", "read more", "more"}
)

_EM_DASH = chr(0x2014)
_HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(\S.*?)(?:[ \t]+#+)?[ \t]*$")
_TLDR_MARKER = re.compile(r"^ {0,3}> ?\[!tldr\][ \t]*$", re.IGNORECASE)
_QUOTE_LINE = re.compile(r"^ {0,3}>[ \t]?(.*)$")
_LIST_POINT = re.compile(r"^(?:[-*+]|\d{1,9}[.)])[ \t]+\S")
_IMAGE = re.compile(r"!\[([^\[\]]*)\]\(")
_LINK = re.compile(r"(?<!!)\[([^\[\]]*)\]\(")
_PRONOUN = re.compile(r"(?:" + "|".join(re.escape(p) for p in sorted(REFERRING_PRONOUNS, key=len, reverse=True)) + r")(?!\w)")
_PUNCTUATION = string.punctuation + string.whitespace


@dataclass(frozen=True)
class LintIssue:
    rule: str
    level: str
    line: int
    message: str


@dataclass(frozen=True)
class LintMeta:
    description: str = ""
    last_verified_at: date | None = None
    has_translation: bool = False


@dataclass(frozen=True)
class LintResult:
    issues: list[LintIssue]
    score: int

    @property
    def error_count(self) -> int:
        return sum(1 for i in self.issues if i.level == LEVEL_ERROR)

    @property
    def warning_count(self) -> int:
        return sum(1 for i in self.issues if i.level == LEVEL_WARNING)

    @property
    def info_count(self) -> int:
        return sum(1 for i in self.issues if i.level == LEVEL_INFO)

    @property
    def has_errors(self) -> bool:
        return self.error_count > 0


def _score(issues: list[LintIssue]) -> int:
    errors = sum(1 for i in issues if i.level == LEVEL_ERROR)
    warnings = sum(1 for i in issues if i.level == LEVEL_WARNING)
    return max(0, 100 - ERROR_PENALTY * errors - WARNING_PENALTY * warnings)


def _blank_frontmatter(markdown: str) -> str:
    """Tekst z frontmatterem zastapionym pustymi liniami, zeby numery linii zrodla sie nie przesunely."""
    lines = markdown.splitlines()
    if not lines or lines[0].strip() != "---":
        return "\n".join(lines)
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return "\n".join([""] * (index + 1) + lines[index + 1 :])
    return "\n".join(lines)


def _meta_issues(meta: LintMeta, today: date) -> list[LintIssue]:
    issues: list[LintIssue] = []
    description = meta.description.strip()
    if not description:
        issues.append(LintIssue("description-missing", LEVEL_ERROR, 0, "Wpis nie ma opisu (description)."))
    elif not DESCRIPTION_MIN_LENGTH <= len(description) <= DESCRIPTION_MAX_LENGTH:
        issues.append(
            LintIssue(
                "description-length",
                LEVEL_ERROR,
                0,
                f"Opis ma {len(description)} znaków, a powinien mieć od {DESCRIPTION_MIN_LENGTH} do {DESCRIPTION_MAX_LENGTH}.",
            )
        )
    if meta.last_verified_at is None:
        issues.append(LintIssue("last-verified-missing", LEVEL_WARNING, 0, "Wpis nie ma daty ostatniej weryfikacji treści (last_verified_at)."))
    elif (today - meta.last_verified_at).days > LAST_VERIFIED_MAX_AGE_DAYS:
        issues.append(
            LintIssue(
                "last-verified-stale",
                LEVEL_WARNING,
                0,
                f"Treść zweryfikowano ponad {LAST_VERIFIED_MAX_AGE_DAYS} dni temu ({meta.last_verified_at.isoformat()}).",
            )
        )
    if not meta.has_translation:
        issues.append(LintIssue("translation-missing", LEVEL_INFO, 0, "Wpis nie ma tłumaczenia na drugi język (pl i en)."))
    return issues


def _tldr_issues(lines: list[SourceLine]) -> list[LintIssue]:
    nonempty = [line for line in lines if line.text.strip()]
    if not nonempty:
        return [LintIssue("tldr-missing", LEVEL_ERROR, 0, "Wpis powinien zaczynać się od bloku > [!TLDR] z 2-5 punktami.")]
    title = _HEADING.match(nonempty[0].text)
    index = 1 if title is not None and len(title.group(1)) == 1 else 0
    first = nonempty[index] if index < len(nonempty) else None
    if first is None or not _TLDR_MARKER.match(first.text):
        missing_line = first.number if first is not None else 0
        return [LintIssue("tldr-missing", LEVEL_ERROR, missing_line, "Wpis powinien zaczynać się od bloku > [!TLDR] z 2-5 punktami.")]
    points = 0
    for following in lines[first.number :]:
        quoted = _QUOTE_LINE.match(following.text)
        if quoted is None:
            break
        if _LIST_POINT.match(quoted.group(1).strip()):
            points += 1
    if not TLDR_MIN_POINTS <= points <= TLDR_MAX_POINTS:
        return [
            LintIssue(
                "tldr-points",
                LEVEL_ERROR,
                first.number,
                f"Blok TLDR ma {points} punktów, a powinien mieć od {TLDR_MIN_POINTS} do {TLDR_MAX_POINTS}.",
            )
        ]
    return []


def _heading_issues(lines: list[SourceLine]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    previous = 1
    seen_h1 = False
    first_h2: int | None = None
    has_question = False
    awaiting_text = False
    for line in lines:
        if line.in_code:
            if line.text.strip():
                awaiting_text = False
            continue
        heading = None if line.container == "faq" else _HEADING.match(line.text)
        if heading is None:
            if awaiting_text and line.text.strip():
                awaiting_text = False
                if _PRONOUN.match(line.text.strip().casefold()):
                    issues.append(
                        LintIssue(
                            "h2-starts-with-pronoun",
                            LEVEL_WARNING,
                            line.number,
                            "Sekcja H2 zaczyna się od zaimka odsyłającego do poprzedniej, a powinna być samodzielna.",
                        )
                    )
            continue
        level = len(heading.group(1))
        awaiting_text = level == 2
        if level == 1:
            if seen_h1:
                issues.append(LintIssue("heading-multiple-h1", LEVEL_ERROR, line.number, "Wpis ma więcej niż jeden nagłówek H1 (tytuł)."))
            seen_h1 = True
        elif level > previous + 1:
            issues.append(
                LintIssue(
                    "heading-level-skip",
                    LEVEL_ERROR,
                    line.number,
                    f"Nagłówek H{level} po nagłówku H{previous} przeskakuje poziom.",
                )
            )
        previous = level
        if level == 2:
            if first_h2 is None:
                first_h2 = line.number
            if heading.group(2).rstrip().endswith("?"):
                has_question = True
    if not has_question:
        issues.append(LintIssue("h2-no-question", LEVEL_WARNING, first_h2 or 0, "Żaden nagłówek H2 nie ma formy pytania."))
    return issues


def _inline_issues(lines: list[SourceLine]) -> list[LintIssue]:
    issues: list[LintIssue] = []
    for line in lines:
        if line.in_code:
            continue
        for image in _IMAGE.finditer(line.text):
            if len(image.group(1).strip()) < IMAGE_ALT_MIN_LENGTH:
                issues.append(
                    LintIssue(
                        "image-alt-short",
                        LEVEL_ERROR,
                        line.number,
                        f"Tekst alternatywny obrazka ma mniej niż {IMAGE_ALT_MIN_LENGTH} znaków.",
                    )
                )
        for link in _LINK.finditer(line.text):
            if link.group(1).strip(_PUNCTUATION).casefold() in NON_DESCRIPTIVE_LINK_TEXTS:
                issues.append(LintIssue("link-non-descriptive", LEVEL_WARNING, line.number, "Tekst linku nie opisuje celu (np. „tutaj”, „kliknij”)."))
        if _EM_DASH in line.text:
            issues.append(LintIssue("em-dash", LEVEL_WARNING, line.number, "Linia zawiera długi myślnik, użyj zwykłego łącznika (-)."))
    return issues


def lint_post(markdown: str, meta: LintMeta, *, today: date | None = None) -> LintResult:
    """Lint treści wpisu i metadanych; czysta funkcja, nie rzuca wyjątku."""
    try:
        text = _blank_frontmatter(markdown)
        lines = iter_source_lines(text)
        issues = _meta_issues(meta, today or date.today())
        issues += _tldr_issues(lines)
        issues += _heading_issues(lines)
        issues += _inline_issues(lines)
        issues += [LintIssue(b.rule, b.level, b.line, b.message) for b in find_block_issues(text)]
    except Exception:
        logger.exception("Blad lintu wpisu bloga")
        issues = [LintIssue("lint-failed", LEVEL_ERROR, 0, "Nie udało się przetworzyć treści wpisu.")]
    issues.sort(key=lambda i: (i.line, i.rule))
    return LintResult(issues=issues, score=_score(issues))


async def lint_post_by_id(
    db: AsyncSession,
    user: User,
    project: Project,
    page_id: uuid.UUID,
    *,
    content_fn: Callable[[WikiPage], str] | None = None,
) -> LintResult:
    """Lint wpisu ze strony wiki. Czysty odczyt: nie commituje, brak wiersza `BlogPost` to błąd."""
    await ensure_permission(db, user.id, project.id, "wiki", "read")

    page = (await db.execute(select(WikiPage).where(WikiPage.id == page_id, WikiPage.project_id == project.id))).scalar_one_or_none()
    if page is None:
        raise ValueError("Strona wiki nie istnieje")

    post = (await db.execute(select(BlogPost).where(BlogPost.page_id == page.id))).scalar_one_or_none()
    if post is None:
        raise ValueError("Wpis bloga nie istnieje")

    translated_by = (await db.execute(select(BlogPost.page_id).where(BlogPost.translation_of_id == page.id).limit(1))).scalar_one_or_none()

    if content_fn is None:
        # Import w czasie wywołania: services/wiki.py importuje services/blog.py (cykl).
        from monolynx.services.wiki import get_page_content

        content_fn = get_page_content

    meta = LintMeta(
        description=post.description or "",
        last_verified_at=post.last_verified_at,
        has_translation=post.translation_of_id is not None or translated_by is not None,
    )
    return lint_post(content_fn(page), meta)


def lint_result_dict(page_id: uuid.UUID, result: LintResult) -> dict[str, Any]:
    """Wynik lintu jako słownik wspólny dla MCP i API v2 (MCP dodaje `table`)."""
    return {
        "page_id": str(page_id),
        "score": result.score,
        "error_count": result.error_count,
        "warning_count": result.warning_count,
        "info_count": result.info_count,
        "issues": [{"rule": i.rule, "level": i.level, "line": i.line, "message": i.message} for i in result.issues],
    }


def format_lint_table(result: LintResult) -> str:
    """Tabela markdown problemów i linia oceny (kompaktowy tekst dla agenta)."""
    if not result.issues:
        return f"Brak problemów.\n\nOcena: {result.score}/100"
    rows = ["| Linia | Poziom | Reguła | Komunikat |", "|---|---|---|---|"]
    rows += [f"| {i.line} | {i.level} | {i.rule} | {i.message.replace('|', chr(92) + '|')} |" for i in result.issues]
    return "\n".join(rows) + f"\n\nOcena: {result.score}/100"
