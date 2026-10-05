"""Silnik blokow wpisu bloga (MON-255).

`render_post_html` buduje HTML strony wpisu, `render_post_markdown` bliźniak `.md`
(markdown dla AI), `strip_block_markers` usuwa ze zrodla znaczniki blokow (alert, kontenery),
zeby streszczenie wpisu (meta description, zajawka listy) liczylo sie z tresci. Rejestry
`TEXT_BLOCKS` i `CONTAINER_BLOCKS` (pusty) opisuja bloki. Bloki tekstowe (alerty GitHub, cytat z autorem, obrazek z podpisem, kod
z nazwa pliku, kotwice naglowkow, przypisy) sa zapisane jako zwykly, czytelny markdown;
HTML generuja wylacznie renderery z tego modulu.

Kolejnosc `render_post_html` (sanityzacja jest jedna, na treści autora):
1. kontenery `:::typ` (nieznany typ: sam srodek) i wyciecie blokow ze zrodla (kod, cytaty,
   definicje przypisow) na tokeny-zastepniki;
2. markdown -> HTML -> `nh3.clean()` (bez `class` i `id`, schematy http/https/mailto);
3. obrazki z podpisem na juz oczyszczonym HTML;
4. podmiana tokenow na HTML blokow, tylko w postaci `<p>token</p>` (token gdziekolwiek indziej
   jest usuwany): kazda wartosc od autora w bloku jest albo escapowana (`html.escape`), albo
   pochodzi z `nh3` (tresc alertu i cytatu), albo to wartosc ze slownika rendererow. Tokeny
   maja losowy nonce, wiec autor nie zasymuluje bloku;
5. kotwice naglowkow i odnosniki przypisow na calosci (takze w alertach i cytatach).
"""

from __future__ import annotations

import html
import logging
import re
import unicodedata
import uuid
from collections.abc import Callable
from dataclasses import dataclass

import markdown
import nh3

from monolynx.services.wiki import _PUBLIC_ALLOWED_TAGS, _PUBLIC_URL_SCHEMES

logger = logging.getLogger("monolynx.blog_blocks")


@dataclass(frozen=True)
class BlockDefinition:
    """Definicja bloku: nazwa i dwa renderery (strona HTML, bliźniak markdown)."""

    name: str
    to_html: Callable[..., str]
    to_markdown: Callable[..., str]

    def __post_init__(self) -> None:
        if not callable(self.to_html) or not callable(self.to_markdown):
            raise TypeError(f"Block '{self.name}' needs callable to_html and to_markdown")


_ALERT_TITLES: dict[str, str | None] = {
    "note": "Uwaga",
    "tip": "Wskazówka",
    "important": "Ważne",
    "warning": "Ostrzeżenie",
    "caution": "Niebezpieczeństwo",
    "tldr": "TL;DR",
    "quote": None,
}

_MD_EXTENSIONS = ["fenced_code", "tables", "nl2br", "sane_lists"]
_CLEAN_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title"},
    "img": {"src", "alt", "title"},
}

_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_CONTAINER_OPEN = re.compile(r'^:::([A-Za-z][\w-]*)((?:[ \t]+[A-Za-z][\w-]*="[^"]*")*)[ \t]*$')
_QUOTE_LINE = re.compile(r"^ {0,3}>")
_QUOTE_PREFIX = re.compile(r"^ {0,3}> ?")
_ALERT_MARKER = re.compile(r"^\[!([A-Za-z]+)\][ \t]*$")
_AUTHOR_LINE = re.compile(r"^--[ \t]+(\S.*)$")
_FENCE_LANG = re.compile(r"^\s*([\w#.+-]+)(?=\s|$)")
_FENCE_TITLE = re.compile(r'title="([^"]*)"')
_FOOTNOTE_DEF = re.compile(r"^\[\^([\w-]+)\]:[ \t]?(.*)$")
_FOOTNOTE_REF = re.compile(r"\[\^([\w-]+)\]")
_HEADING = re.compile(r"<h([2-4])>(.*?)</h\1>", re.DOTALL)
# Tag z atrybutami w cudzyslowach: `>` wewnatrz wartosci atrybutu nie konczy tagu.
_TAG = re.compile(r"(<(?:[^>\"']|\"[^\"]*\"|'[^']*')*>)")
_STANDALONE_IMG = re.compile(r'<p>(<img(?:\s+[\w-]+="[^"]*")*\s*/?>)</p>')
_ATTR = re.compile(r'([\w-]+)="([^"]*)"')

_Fence = tuple[str, int, str]


def _clean(markdown_text: str) -> str:
    """Markdown autora -> HTML -> nh3; jedyne miejsce, przez ktore tresc autora trafia do wyniku."""
    rendered: str = markdown.markdown(markdown_text, extensions=_MD_EXTENSIONS)
    return nh3.clean(
        rendered,
        tags=_PUBLIC_ALLOWED_TAGS,
        attributes=_CLEAN_ATTRIBUTES,
        url_schemes=_PUBLIC_URL_SCHEMES,
        link_rel="noopener noreferrer nofollow",
        clean_content_tags={"script", "style"},
    )


def _open_fence(line: str) -> _Fence | None:
    match = _FENCE.match(line)
    if match is None:
        return None
    marker, info = match.groups()
    if marker[0] == "`" and "`" in info:
        return None
    return marker[0], len(marker), info.strip()


def _closes_fence(line: str, fence: _Fence) -> bool:
    match = _FENCE.match(line)
    return match is not None and match.group(1)[0] == fence[0] and len(match.group(1)) >= fence[1] and not match.group(2).strip()


def _resolve_containers(lines: list[str], *, keep_blank: bool) -> list[str]:
    """Usuwa linie `:::typ` / `:::` domknietych kontenerow, zostawiajac srodek.

    Rejestr kontenerow jest dzis pusty, wiec kazdy typ jest "nieznany" (kolejne tickety
    podepna tu renderery). Zagniezdzony `:::typ` i niedomkniety kontener zostaja zwyklym
    tekstem; linie wewnatrz bloku kodu nie sa markerami.
    """
    out: list[str] = []
    fence: _Fence | None = None
    opener: int | None = None
    for line in lines:
        if fence is not None:
            out.append(line)
            if _closes_fence(line, fence):
                fence = None
            continue
        fence = _open_fence(line)
        if fence is not None:
            out.append(line)
            continue
        if opener is None:
            if _CONTAINER_OPEN.match(line):
                opener = len(out)
            out.append(line)
        elif line.strip() == ":::":
            if keep_blank:
                out[opener] = ""
                out.append("")
            else:
                del out[opener]
            opener = None
        else:
            out.append(line)
    return out


def _callout_html(kind: str, body_html: str) -> str:
    title = _ALERT_TITLES[kind]
    title_html = f'<p class="blog-callout-title">{title}</p>' if title else ""
    return f'<aside class="blog-callout blog-callout-{kind}">{title_html}{body_html}</aside>'


def _quote_html(body_html: str, author: str) -> str:
    return f'<figure class="blog-quote"><blockquote>{body_html}</blockquote><figcaption>{html.escape(author)}</figcaption></figure>'


def _code_html(lang: str | None, title: str | None, code: str) -> str:
    language = f' class="language-{lang}"' if lang else ""
    title_html = f'<span class="blog-code-title">{html.escape(title)}</span>' if title else ""
    return (
        '<div class="blog-code"><div class="blog-code-header">'
        f'{title_html}<button type="button" class="blog-code-copy">Kopiuj</button></div>'
        f"<pre><code{language}>{html.escape(code, quote=False)}</code></pre></div>"
    )


def _figure_html(attrs: dict[str, str], caption: str) -> str:
    # Wartosci atrybutow z nh3 sa juz escapowane, ale podpis zmienia kontekst (atrybut ->
    # tresc elementu), wiec nie polegamy na serializerze: odescapowujemy i escapujemy jawnie.
    def escaped(value: str) -> str:
        return html.escape(html.unescape(value), quote=True)

    return (
        f'<figure class="blog-figure"><img src="{escaped(attrs["src"])}" alt="{escaped(attrs.get("alt", ""))}" '
        f'loading="lazy" decoding="async"><figcaption>{escaped(caption)}</figcaption></figure>'
    )


def _footnotes_html(items: list[tuple[int, str]]) -> str:
    rendered = []
    for number, body in items:
        inner = body[3:-4] if body.startswith("<p>") and body.endswith("</p>") and body.count("<p>") == 1 else body
        rendered.append(f'<li id="blog-fn-{number}">{inner} <a href="#blog-fnref-{number}">↩</a></li>')
    return f'<section class="blog-footnotes"><ol>{"".join(rendered)}</ol></section>'


def _slug(text: str) -> str:
    folded = text.replace("ł", "l").replace("Ł", "L")
    ascii_text = unicodedata.normalize("NFKD", folded).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-") or "sekcja"


def _anchor_headings(rendered: str) -> str:
    used: set[str] = set()

    def add_id(match: re.Match[str]) -> str:
        level, inner = match.groups()
        base = _slug(html.unescape(re.sub(r"<[^>]*>", "", inner)))
        if base.startswith("blog-"):
            # `blog-fn-*` / `blog-fnref-*` naleza do przypisow.
            base = f"post-{base}"
        slug, counter = base, 2
        while slug in used:
            slug = f"{base}-{counter}"
            counter += 1
        used.add(slug)
        return f'<h{level} id="{slug}">{inner}</h{level}>'

    return _HEADING.sub(add_id, rendered)


def _caption_standalone_images(rendered: str) -> str:
    def to_figure(match: re.Match[str]) -> str:
        attrs = dict(_ATTR.findall(match.group(1)))
        if not attrs.get("title") or "src" not in attrs:
            return match.group(0)
        return _figure_html(attrs, attrs["title"])

    return _STANDALONE_IMG.sub(to_figure, rendered)


def _link_footnotes(rendered: str, definitions: dict[str, str]) -> tuple[str, dict[str, int]]:
    """Zamienia `[^etykieta]` w tekscie (nie w kodzie, nie w linku) na `<sup>` z kotwica."""
    order: dict[str, int] = {}

    def to_sup(match: re.Match[str]) -> str:
        label = match.group(1)
        if label not in definitions:
            return match.group(0)
        first = label not in order
        number = order.setdefault(label, len(order) + 1)
        sup_id = f' id="blog-fnref-{number}"' if first else ""
        return f'<sup{sup_id}><a href="#blog-fn-{number}">{number}</a></sup>'

    parts = _TAG.split(rendered)
    skip = 0
    for index, part in enumerate(parts):
        if index % 2:
            name = re.match(r"</?([A-Za-z0-9]+)", part)
            if name and name.group(1).lower() in {"code", "pre", "a"}:
                skip = max(0, skip + (-1 if part.startswith("</") else 1))
        elif part and not skip:
            parts[index] = _FOOTNOTE_REF.sub(to_sup, part)
    return "".join(parts), order


def _quote_block(inner: list[str]) -> str | None:
    """HTML alertu albo cytatu z autorem; None, gdy to zwykly cytat."""
    marker = _ALERT_MARKER.match(inner[0]) if inner else None
    if marker is not None and marker.group(1).lower() in _ALERT_TITLES:
        return _callout_html(marker.group(1).lower(), _clean("\n".join(inner[1:])))
    body = list(inner)
    while body and not body[-1].strip():
        body.pop()
    author = _AUTHOR_LINE.match(body[-1]) if len(body) >= 2 else None
    if author is not None and "\n".join(body[:-1]).strip():
        return _quote_html(_clean("\n".join(body[:-1])), author.group(1).rstrip())
    return None


def _extract_blocks(lines: list[str], nonce: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    """Wycina z zrodla bloki z wlasnym rendererem; zwraca zrodlo z tokenami, bloki i przypisy."""
    out: list[str] = []
    blocks: dict[str, str] = {}
    definitions: dict[str, str] = {}

    def park(block_html: str) -> None:
        token = f"blogblock{nonce}n{len(blocks)}n"
        blocks[token] = block_html
        out.extend(["", token, ""])

    index = 0
    while index < len(lines):
        line = lines[index]
        fence = _open_fence(line)
        if fence is not None:
            end = next((j for j in range(index + 1, len(lines)) if _closes_fence(lines[j], fence)), None)
            if end is None:
                out.extend(lines[index:])
                break
            lang = _FENCE_LANG.match(fence[2])
            title = _FENCE_TITLE.search(fence[2])
            park(_code_html(lang.group(1) if lang else None, title.group(1) if title else None, "\n".join(lines[index + 1 : end])))
            index = end + 1
        elif _QUOTE_LINE.match(line):
            end = index
            while end < len(lines) and _QUOTE_LINE.match(lines[end]):
                end += 1
            block = _quote_block([_QUOTE_PREFIX.sub("", quoted) for quoted in lines[index:end]])
            if block is None:
                out.extend(lines[index:end])
            else:
                park(block)
            index = end
        elif (definition := _FOOTNOTE_DEF.match(line)) is not None:
            definitions.setdefault(definition.group(1), definition.group(2))
            out.append("")
            index += 1
        else:
            out.append(line)
            index += 1
    return out, blocks, definitions


def _render_html(source: str) -> str:
    nonce = uuid.uuid4().hex
    lines = _resolve_containers(source.splitlines(), keep_blank=True)
    lines, blocks, definitions = _extract_blocks(lines, nonce)
    rendered = _clean("\n".join(lines))
    rendered = _caption_standalone_images(rendered)
    for token, block_html in blocks.items():
        rendered = rendered.replace(f"<p>{token}</p>", block_html)
    # Token poza samodzielnym akapitem (np. w surowym bloku HTML autora) to blok, ktorego nie
    # wolno wstawic w nieznany kontekst, np. w wartosc atrybutu.
    rendered = re.sub(rf"blogblock{nonce}n\d+n", "", rendered)
    rendered = _anchor_headings(rendered)
    rendered, order = _link_footnotes(rendered, definitions)
    if order:
        label_by_number = {number: label for label, number in order.items()}
        rendered += _footnotes_html([(number, _clean(definitions[label_by_number[number]])) for number in sorted(label_by_number)])
    return rendered


def render_post_html(markdown_text: str) -> str:
    """Wpis (markdown z blokami) -> bezpieczny HTML strony; nigdy nie rzuca wyjatku."""
    if not markdown_text.strip():
        return ""
    try:
        return _render_html(markdown_text)
    except Exception:
        logger.exception("Blad renderowania wpisu bloga")
        return f"<p>{html.escape(markdown_text)}</p>"


def render_post_markdown(markdown_text: str) -> str:
    """Wpis -> markdown bliźniaka `.md`: bloki tekstowe bez zmian, kontenery bez markerow."""
    if not markdown_text.strip():
        return ""
    try:
        return "\n".join(_resolve_containers(markdown_text.splitlines(), keep_blank=False))
    except Exception:
        logger.exception("Blad renderowania markdownu wpisu bloga")
        return markdown_text


def strip_block_markers(markdown_text: str) -> str:
    """Zrodlo bez znacznikow blokow (alert `> [!TYP]`, markery kontenerow); kod zostaje.

    Do streszczen: `> [!TLDR]` nie moze trafic do meta description. Nie rzuca wyjatku.
    """
    try:
        out: list[str] = []
        fence: _Fence | None = None
        for line in _resolve_containers(markdown_text.splitlines(), keep_blank=False):
            if fence is not None:
                if _closes_fence(line, fence):
                    fence = None
            elif (fence := _open_fence(line)) is None:
                marker = _ALERT_MARKER.match(_QUOTE_PREFIX.sub("", line)) if _QUOTE_LINE.match(line) else None
                starts_run = not out or not _QUOTE_LINE.match(out[-1])
                if marker is not None and starts_run and marker.group(1).lower() in _ALERT_TITLES:
                    continue
            out.append(line)
        return "\n".join(out)
    except Exception:
        logger.exception("Blad usuwania znacznikow blokow wpisu bloga")
        return markdown_text


def _verbatim(source: str) -> str:
    """Bloki tekstowe sa juz czytelnym markdownem, wiec bliźniak .md zostawia je bez zmian."""
    return source


def _text_block(name: str, to_html: Callable[..., str]) -> tuple[str, BlockDefinition]:
    return name, BlockDefinition(name=name, to_html=to_html, to_markdown=_verbatim)


TEXT_BLOCKS: dict[str, BlockDefinition] = dict(
    [
        _text_block("callout", _callout_html),
        _text_block("quote", _quote_html),
        _text_block("figure", _figure_html),
        _text_block("code", _code_html),
        _text_block("heading", _anchor_headings),
        _text_block("footnotes", _footnotes_html),
    ]
)

CONTAINER_BLOCKS: dict[str, BlockDefinition] = {}
