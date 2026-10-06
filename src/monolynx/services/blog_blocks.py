"""Silnik blokow wpisu bloga (MON-255, MON-256, MON-257).

`render_post_html` buduje HTML strony wpisu, `render_post_markdown` bliźniak `.md`
(markdown dla AI), `strip_block_markers` usuwa ze zrodla znaczniki blokow (alert, kontenery),
zeby streszczenie wpisu (meta description, zajawka listy) liczylo sie z tresci,
`extract_structured_data` wyciaga z kontenerow `faq`, `steps` i `video` dane do JSON-LD,
`collect_block_assets` mowi, jakie zasoby dolaczyc do strony (biblioteka Mermaid, komponenty
z `BLOG_GRAPHICS`), a `find_block_issues` wskazuje braki w blokach graficznych (lint).
Rejestry `TEXT_BLOCKS` i `CONTAINER_BLOCKS` opisuja bloki. Bloki tekstowe (alerty GitHub, cytat
z autorem, obrazek z podpisem, kod z nazwa pliku, kotwice naglowkow, przypisy) sa zapisane jako
zwykly, czytelny markdown. Kontenery `:::typ ... :::` (faq, steps, stats, compare, details,
gallery, video, cta, glossary oraz graficzne chart, diagram, terminal, graphic) tez: ich wnetrze
to markdown autora, a renderery z rejestru buduja z niego strukture. Bloki graficzne maja tekst
jako zrodlo (tabela, opis, kod), a grafika jest warstwa nad nim: wykres to SVG z `blog_chart`,
diagram renderuje w przegladarce Mermaid ze zrodla, terminal to okno CSS z trescia w HTML,
grafika z rejestru to opis podmieniany przez komponent z repozytorium. HTML generuja wylacznie
renderery z tego modulu.

Kolejnosc `render_post_html` (sanityzacja jest jedna, na treści autora):
1. kontenery `:::typ`: znany typ (rejestr) renderuje swoj HTML i parkuje go na tokenie, nieznany
   typ zostawia sam srodek; potem wyciecie blokow ze zrodla (kod, cytaty, definicje przypisow)
   na tokeny-zastepniki;
2. markdown -> HTML -> `nh3.clean()` (bez `class` i `id`, schematy http/https/mailto);
3. obrazki z podpisem na juz oczyszczonym HTML;
4. podmiana tokenow na HTML blokow, tylko w postaci `<p>token</p>` (token gdziekolwiek indziej
   jest usuwany): kazda wartosc od autora w bloku jest albo escapowana (`html.escape`), albo
   pochodzi z `nh3` (tresc alertu, cytatu i kontenera), albo to wartosc ze slownika rendererow
   (nazwa grafiki z `BLOG_GRAPHICS`, SVG zbudowany przez `blog_chart` z danych sparsowanych).
   Tokeny maja losowy nonce, wiec autor nie zasymuluje bloku;
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
from urllib.parse import parse_qs, urlsplit

import markdown
import nh3

from monolynx.services.blog_chart import parse_chart_table, render_chart_svg
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
# `:::details Tytul`: tytul to reszta linii, wiec nie pasuje do `_CONTAINER_OPEN` (atrybuty klucz="wartosc").
_DETAILS_OPEN = re.compile(r":::details(?=[ \t]|$)")
_QUESTION_OPEN = re.compile(r"###[ \t]")
_STEP_ITEM = re.compile(r"^ {0,3}\d{1,9}[.)][ \t]+(.*)$")
_STEP_NAME = re.compile(r"^\*\*([^\n]+?)\*\*[ \t]*(.*)$", re.DOTALL)
_TERM_ITEM = re.compile(r"^ {0,3}[-*+][ \t]+\*\*([^\n]+?)\*\*[ \t]+-[ \t]+(\S.*)$")
_HIGHLIGHT = re.compile(r"[0-9]{1,4}")
_TABLE_ROW = re.compile(r"<tr>.*?</tr>", re.DOTALL)
_TABLE_CELL = re.compile(r"<(th|td)>")
_IMG_TAG = re.compile(r'<img(?:\s+[\w-]+="[^"]*")*\s*/?>')
_ANCHOR = re.compile(r'<a((?:\s+[\w-]+="[^"]*")*)\s*>(.*?)</a>', re.DOTALL)
_ANCHOR_OPEN = re.compile(r"<a\b")
_PARAGRAPH = re.compile(r"<p>(.*?)</p>", re.DOTALL)
_ADDRESS = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:\S*")
_BLOCK_TAG = re.compile(r"</?(?:p|li|ul|ol|br|div|h[1-6]|blockquote|pre|table|thead|tbody|tr|th|td|hr)\b[^>]*>")
_ANY_TAG = re.compile(r"<[^>]*>")
_YOUTUBE_ID = re.compile(r"[A-Za-z0-9_-]{6,20}")
_VIMEO_ID = re.compile(r"[0-9]+")

# Dokladna biala lista hostow wideo (porownanie calego hosta, nie podciagu): host -> dostawca.
_VIDEO_HOSTS = {
    "youtube.com": "youtube",
    "www.youtube.com": "youtube",
    "m.youtube.com": "youtube",
    "youtu.be": "youtube",
    "vimeo.com": "vimeo",
    "www.vimeo.com": "vimeo",
    "player.vimeo.com": "vimeo",
}

# Zamknieta lista komponentow z `static/blog/graphics/<nazwa>.js|.css`; nowy komponent to pliki i wpis tutaj (MR).
BLOG_GRAPHICS: frozenset[str] = frozenset({"agent-loop"})

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

    Wolana na odcinkach zrodla bez znanych kontenerow (te wydziela `_split_containers`), wiec
    kazdy typ jest tu "nieznany". Zagniezdzony `:::typ` i niedomkniety kontener zostaja zwyklym
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


@dataclass(frozen=True)
class _Container:
    """Domkniety kontener znanego typu; `attrs` to reszta linii otwarcia (dla `details`: tytul), `line` to jej numer (od 1)."""

    kind: str
    attrs: str
    inner: list[str]
    line: int = 0


_Part = list[str] | _Container


def _container_opener(line: str) -> tuple[str, str] | None:
    """(typ, atrybuty) dla linii otwarcia kontenera; `:::details Tytul` ma tytul zamiast atrybutow."""
    if _DETAILS_OPEN.match(line):
        return "details", line[len(":::details") :].strip()
    match = _CONTAINER_OPEN.match(line)
    if match is None:
        return None
    return match.group(1), match.group(2)


def _split_containers(lines: list[str]) -> list[_Part]:
    """Dzieli zrodlo na odcinki zwyklych linii i domkniete kontenery typow z `CONTAINER_BLOCKS`.

    Te same zasady co `_resolve_containers`: bez zagniezdzania, kod ogrodzony pomija markery,
    niedomkniety kontener jest zwyklym tekstem. Domkniety kontener nieznanego typu zostaje w
    odcinku zwyklych linii razem z markerami, wiec dalej obsluguje go `_resolve_containers`.
    """
    parts: list[_Part] = []
    plain: list[str] = []
    inner: list[str] = []
    opened: tuple[str, str, str, int] | None = None
    fence: _Fence | None = None
    for number, line in enumerate(lines, start=1):
        target = plain if opened is None else inner
        if fence is not None:
            target.append(line)
            if _closes_fence(line, fence):
                fence = None
            continue
        fence = _open_fence(line)
        if fence is not None:
            target.append(line)
        elif opened is None:
            found = _container_opener(line)
            if found is None:
                plain.append(line)
            else:
                opened = (found[0], found[1], line, number)
                inner = []
        elif line.strip() == ":::":
            kind, attrs, open_line, open_number = opened
            if kind in CONTAINER_BLOCKS:
                if plain:
                    parts.append(plain)
                    plain = []
                parts.append(_Container(kind, attrs, inner, open_number))
            else:
                plain.extend([open_line, *inner, line])
            opened = None
            inner = []
        else:
            inner.append(line)
    if opened is not None:
        plain.extend([opened[2], *inner])
    if plain:
        parts.append(plain)
    return parts


def _expand_containers(lines: list[str], render: Callable[[_Container], list[str]], *, keep_blank: bool) -> list[str]:
    """Znane kontenery zastepuje liniami z `render`, resztę przepuszcza przez `_resolve_containers`."""
    out: list[str] = []
    for part in _split_containers(lines):
        if isinstance(part, _Container):
            out.extend(render(part))
        else:
            out.extend(_resolve_containers(part, keep_blank=keep_blank))
    return out


def _has_content(lines: list[str]) -> bool:
    return any(line.strip() for line in lines)


def _inline(text: str) -> str:
    """Markdown autora -> oczyszczony HTML bez opakowujacego akapitu (do `span`, `summary`, `dt`)."""
    rendered = _clean(text)
    if rendered.startswith("<p>") and rendered.endswith("</p>") and rendered.count("<p>") == 1:
        return rendered[3:-4]
    return rendered


def _plain(text: str) -> str:
    """Markdown autora -> goly tekst (bez znacznikow markdown i HTML) do danych strukturalnych."""
    spaced = _BLOCK_TAG.sub(" ", _clean(text))
    return " ".join(html.unescape(_ANY_TAG.sub("", spaced)).split())


def _degrade(container: _Container) -> str:
    """Wnetrze o zlej strukturze: zwykly markdown bez opakowania bloku."""
    return _clean("\n".join(container.inner))


def _parse_faq(inner: list[str]) -> list[tuple[str, list[str]]] | None:
    """Pary (pytanie, linie odpowiedzi); pytanie to naglowek `###`. None, gdy brak struktury FAQ."""
    items: list[tuple[str, list[str]]] = []
    fence: _Fence | None = None
    for line in inner:
        if fence is not None:
            if _closes_fence(line, fence):
                fence = None
        elif _QUESTION_OPEN.match(line) and line[3:].strip():
            items.append((line[3:].strip(), []))
            continue
        else:
            fence = _open_fence(line)
        if items:
            items[-1][1].append(line)
        elif line.strip():
            return None
    return items or None


def _parse_steps(inner: list[str]) -> list[str] | None:
    """Teksty punktow listy numerowanej wraz z liniami kontynuacji. None, gdy brak listy."""
    items: list[list[str]] = []
    for line in inner:
        item = _STEP_ITEM.match(line)
        if item is not None:
            items.append([item.group(1)])
        elif items:
            items[-1].append(line)
        elif line.strip():
            return None
    return ["\n".join(item).strip() for item in items] or None


def _split_step(text: str) -> tuple[str, str]:
    """(nazwa, reszta): nazwa to pogrubiony poczatek punktu, pusta, gdy go nie ma."""
    match = _STEP_NAME.match(text)
    if match is None:
        return "", text
    return match.group(1).strip(), match.group(2).strip()


def _parse_terms(inner: list[str]) -> list[tuple[str, str]] | None:
    """Pary (wartosc, opis) z listy `- **wartosc** - opis`. Kazda niepusta linia musi pasowac."""
    items: list[tuple[str, str]] = []
    for line in inner:
        if not line.strip():
            continue
        match = _TERM_ITEM.match(line)
        if match is None:
            return None
        items.append((match.group(1).strip(), match.group(2).strip()))
    return items or None


def _video_source(url: str) -> tuple[str, str] | None:
    """(dostawca, id) dla adresu https z hosta z `_VIDEO_HOSTS` i poprawnym id; inaczej None."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    # `netloc == hostname` odrzuca userinfo (`youtube.com@evil.com`) i port.
    host = parts.hostname
    if parts.scheme != "https" or host is None or parts.netloc.lower() != host:
        return None
    provider = _VIDEO_HOSTS.get(host)
    if provider is None:
        return None
    if host == "youtu.be":
        video_id = parts.path[1:]
        return (provider, video_id) if _YOUTUBE_ID.fullmatch(video_id) else None
    if provider == "youtube":
        values = parse_qs(parts.query).get("v", [])
        if parts.path != "/watch" or len(values) != 1 or not _YOUTUBE_ID.fullmatch(values[0]):
            return None
        return provider, values[0]
    prefix = "/video/" if host == "player.vimeo.com" else "/"
    video_id = parts.path.removeprefix(prefix) if parts.path.startswith(prefix) else ""
    return (provider, video_id) if _VIMEO_ID.fullmatch(video_id) else None


def _split_video(inner: list[str]) -> tuple[str, str] | None:
    """(adres, opis): pierwsza niepusta linia to adres (jeden token z schematem), reszta to opis."""
    start = next((index for index, line in enumerate(inner) if line.strip()), None)
    if start is None or not _ADDRESS.fullmatch(inner[start].strip()):
        return None
    return inner[start].strip(), "\n".join(inner[start + 1 :]).strip()


def _is_http(url: str) -> bool:
    try:
        return urlsplit(url).scheme.lower() in {"http", "https"}
    except ValueError:
        return False


def _faq_html(container: _Container) -> str:
    items = _parse_faq(container.inner)
    if items is None:
        return _degrade(container)
    rendered = "".join(
        f'<details class="blog-faq-item"><summary class="blog-faq-question">{_inline(question)}</summary>'
        f'<div class="blog-faq-answer">{_clean("\n".join(answer))}</div></details>'
        for question, answer in items
    )
    return f'<div class="blog-faq">{rendered}</div>'


def _steps_html(container: _Container) -> str:
    items = _parse_steps(container.inner)
    if items is None:
        return _degrade(container)
    rendered = []
    for text in items:
        name, body = _split_step(text)
        name_html = _inline(name) if name else ""
        name_span = f'<span class="blog-step-name">{name_html}</span>' if name_html else ""
        rendered.append(f'<li class="blog-step">{name_span}<div class="blog-step-body">{_clean(body)}</div></li>')
    return f'<ol class="blog-steps">{"".join(rendered)}</ol>'


def _stats_html(container: _Container) -> str:
    items = _parse_terms(container.inner)
    if items is None:
        return _degrade(container)
    rendered = "".join(
        f'<div class="blog-stat"><span class="blog-stat-value">{_inline(value)}</span><span class="blog-stat-label">{_inline(label)}</span></div>'
        for value, label in items
    )
    return f'<div class="blog-stats">{rendered}</div>'


def _mark_cell(row: str, column: int) -> str:
    index = 0

    def mark(match: re.Match[str]) -> str:
        nonlocal index
        index += 1
        return f'<{match.group(1)} class="blog-compare-highlight">' if index == column else match.group(0)

    return _TABLE_CELL.sub(mark, row)


def _compare_html(container: _Container) -> str:
    rendered = _clean("\n".join(container.inner))
    first_row = _TABLE_ROW.search(rendered)
    if "<table>" not in rendered or first_row is None:
        return rendered
    raw = dict(_ATTR.findall(container.attrs)).get("highlight", "")
    column = int(raw) if _HIGHLIGHT.fullmatch(raw) else 0
    if 1 <= column <= len(_TABLE_CELL.findall(first_row.group(0))):
        rendered = _TABLE_ROW.sub(lambda row: _mark_cell(row.group(0), column), rendered)
    return f'<div class="blog-compare">{rendered}</div>'


def _details_html(container: _Container) -> str:
    title = html.escape(container.attrs or "Szczegóły")
    return (
        f'<details class="blog-details"><summary class="blog-details-summary">{title}</summary>'
        f'<div class="blog-details-body">{_clean("\n".join(container.inner))}</div></details>'
    )


def _gallery_html(container: _Container) -> str:
    rendered = _clean("\n".join(container.inner))
    # Tekst obok obrazkow zginalby przy budowie siatki, wiec taki blok zostaje zwyklym markdownem.
    if _ANY_TAG.sub("", _IMG_TAG.sub("", rendered)).strip():
        return rendered
    figures = []
    for tag in _IMG_TAG.findall(rendered):
        attrs = dict(_ATTR.findall(tag))
        if "src" not in attrs:
            continue
        figures.append(
            '<figure class="blog-gallery-item">'
            f'<img src="{html.escape(html.unescape(attrs["src"]))}" alt="{html.escape(html.unescape(attrs.get("alt", "")))}" loading="lazy">'
            "</figure>"
        )
    if not figures:
        return rendered
    return f'<div class="blog-gallery">{"".join(figures)}</div>'


def _video_html(container: _Container) -> str:
    parsed = _split_video(container.inner)
    if parsed is None:
        return _degrade(container)
    url, description = parsed
    source = _video_source(url)
    if source is None:
        link = ""
        if _is_http(url):
            escaped = html.escape(url)
            link = f'<p><a href="{escaped}" rel="noopener noreferrer nofollow">{escaped}</a></p>'
        return _clean(description) + link
    provider, video_id = source
    # Serwer nie emituje `<iframe>` ani obrazka z hosta wideo: ramke buduje JS po kliknieciu,
    # wylacznie z `data-provider` i `data-video-id`, wiec przed kliknieciem nie ma zadan zewnetrznych.
    return (
        f'<div class="blog-video" data-provider="{provider}" data-video-id="{html.escape(video_id)}">'
        f'<a class="blog-video-play" href="{html.escape(url)}" rel="noopener noreferrer nofollow" target="_blank">Odtwórz wideo</a>'
        f'<div class="blog-video-caption">{_clean(description)}</div></div>'
    )


def _cta_html(container: _Container) -> str:
    rendered = _clean("\n".join(container.inner))
    link = _ANCHOR.search(rendered)
    if link is None or len(_ANCHOR_OPEN.findall(rendered)) != 1:
        return rendered
    href = dict(_ATTR.findall(link.group(1))).get("href")
    label = link.group(2).strip()
    remaining = rendered[: link.start()] + rendered[link.end() :]
    paragraphs = [text.strip() for text in _PARAGRAPH.findall(remaining) if text.strip()]
    # `href` znika, gdy nh3 odrzuci schemat (`javascript:`); inna tresc niz akapity nie miesci sie w CTA.
    if not href or not label or not paragraphs or _PARAGRAPH.sub("", remaining).strip():
        return rendered
    return (
        f'<div class="blog-cta"><p class="blog-cta-text">{" ".join(paragraphs)}</p>'
        f'<a class="blog-cta-button" href="{html.escape(html.unescape(href))}" rel="noopener noreferrer nofollow">{label}</a></div>'
    )


def _glossary_html(container: _Container) -> str:
    items = _parse_terms(container.inner)
    if items is None:
        return _degrade(container)
    rendered = "".join(f"<dt>{_inline(term)}</dt><dd>{_inline(definition)}</dd>" for term, definition in items)
    return f'<dl class="blog-glossary">{rendered}</dl>'


def _split_fenced(lines: list[str]) -> tuple[list[str], str, list[str]] | None:
    """(linie poza blokiem, jezyk malymi literami, tresc) pierwszego domknietego bloku ogrodzonego; None, gdy go nie ma."""
    for index, line in enumerate(lines):
        fence = _open_fence(line)
        if fence is None:
            continue
        end = next((j for j in range(index + 1, len(lines)) if _closes_fence(lines[j], fence)), None)
        if end is None:
            return None
        lang = _FENCE_LANG.match(fence[2])
        return [*lines[:index], *lines[end + 1 :]], lang.group(1).lower() if lang else "", lines[index + 1 : end]
    return None


def _diagram_parts(inner: list[str]) -> tuple[list[str], list[str] | None]:
    """(opis, zrodlo mermaid): zrodlo to niepusty pierwszy blok ogrodzony `mermaid`, inaczej None."""
    fenced = _split_fenced(inner)
    if fenced is None:
        return inner, None
    outside, lang, content = fenced
    if lang != "mermaid":
        # Blok w innym jezyku to tresc autora: zostaje w opisie, nie znika.
        return inner, None
    return outside, content if _has_content(content) else None


def _terminal_content(inner: list[str]) -> list[str] | None:
    """Tresc pierwszego bloku ogrodzonego (dowolny jezyk); None, gdy brak bloku albo jest pusty."""
    fenced = _split_fenced(inner)
    return fenced[2] if fenced is not None and _has_content(fenced[2]) else None


def _text_escape(text: str) -> str:
    """`html.escape` plus `[` jako encja: `_link_footnotes` biegnie po wstawieniu blokow i zamienilaby `[^x]` w ich tekscie."""
    return html.escape(text).replace("[", "&#91;")


def _chart_html(container: _Container) -> str:
    attrs = dict(_ATTR.findall(container.attrs))
    title = attrs.get("title", "")
    source = "\n".join(container.inner)
    caption = f"<figcaption>{_text_escape(title)}</figcaption>" if title else ""
    table_html = _clean(source)
    data = parse_chart_table(source)
    chart_type = attrs.get("type", "")
    svg = render_chart_svg(chart_type, title, data) if data is not None else None
    if svg is None:
        return f'<figure class="blog-chart">{caption}{table_html}</figure>'
    # `chart_type` jest tu jednym z `CHART_TYPES`: inaczej `render_chart_svg` zwrocilby None.
    return (
        f'<figure class="blog-chart blog-chart-{chart_type}">{caption}{svg}'
        f'<details class="blog-chart-data"><summary>Dane wykresu</summary>{table_html}</details></figure>'
    )


def _diagram_html(container: _Container) -> str:
    title = dict(_ATTR.findall(container.attrs)).get("title", "")
    description, source = _diagram_parts(container.inner)
    caption = f"<figcaption>{_text_escape(title)}</figcaption>" if title else ""
    description_html = f'<div class="blog-diagram-description">{_clean(chr(10).join(description))}</div>' if _has_content(description) else ""
    if source is None:
        if not description_html:
            return _degrade(container)
        return f'<figure class="blog-diagram">{caption}{description_html}</figure>'
    # Zrodlo bez klasy `language-mermaid`: slowo `mermaid` w HTML strony ma oznaczac tylko skrypt biblioteki.
    return (
        f'<figure class="blog-diagram" data-blog-diagram>{caption}{description_html}'
        '<div class="blog-diagram-canvas" hidden></div>'
        f'<pre class="blog-diagram-source"><code>{html.escape(chr(10).join(source), quote=False)}</code></pre></figure>'
    )


def _terminal_html(container: _Container) -> str:
    content = _terminal_content(container.inner)
    if content is None:
        return _degrade(container)
    title = dict(_ATTR.findall(container.attrs)).get("title", "")
    return (
        '<figure class="blog-terminal" data-blog-terminal>'
        f'<div class="blog-terminal-bar"><span class="blog-terminal-title">{_text_escape(title)}</span></div>'
        f'<pre class="blog-terminal-body"><code>{html.escape(chr(10).join(content), quote=False)}</code></pre></figure>'
    )


def _graphic_html(container: _Container) -> str:
    name = dict(_ATTR.findall(container.attrs)).get("name", "")
    # Atrybut dostaje wylacznie nazwa z rejestru (dokladne dopasowanie), wiec nazwa autora nigdy nie trafia do sciezki pliku.
    marker = f' data-blog-graphic="{name}"' if name in BLOG_GRAPHICS else ""
    return f'<div class="blog-graphic"{marker}><div class="blog-graphic-description">{_clean(chr(10).join(container.inner))}</div></div>'


def _html_container(container: _Container, nonce: str, blocks: dict[str, str]) -> list[str]:
    """Renderuje znany kontener i parkuje jego HTML na tokenie (wstawianym po nh3, jak cytat)."""
    block = ""
    if _has_content(container.inner):
        try:
            block = CONTAINER_BLOCKS[container.kind].to_html(container)
        except Exception:
            logger.exception("Blad renderowania kontenera '%s' wpisu bloga", container.kind)
            block = _degrade(container)
    token = f"blogblock{nonce}c{len(blocks)}c"
    blocks[token] = block
    return ["", token, ""]


def _markdown_container(container: _Container) -> list[str]:
    if not _has_content(container.inner):
        return []
    try:
        text = CONTAINER_BLOCKS[container.kind].to_markdown(container)
    except Exception:
        logger.exception("Blad renderowania markdownu kontenera '%s' wpisu bloga", container.kind)
        return list(container.inner)
    return ["", *text.splitlines(), ""]


def _labelled_markdown(label: str) -> Callable[[_Container], str]:
    def to_markdown(container: _Container) -> str:
        return f"{label}\n\n" + "\n".join(container.inner)

    return to_markdown


def _details_markdown(container: _Container) -> str:
    return f"**{container.attrs or 'Szczegóły'}**\n\n" + "\n".join(container.inner)


def _titled_markdown(title: str, body: str) -> str:
    return f"**{title}**\n\n{body}" if title else body


def _markdown_fence(lang: str, content: list[str]) -> str:
    """Blok ogrodzony z plotem dluzszym niz kazdy ciag backtickow w tresci, zeby jej nie zamknela."""
    longest = max((len(run) for line in content for run in re.findall(r"`+", line)), default=0)
    fence = "`" * max(3, longest + 1)
    return "\n".join([f"{fence}{lang}", *content, fence])


def _chart_markdown(container: _Container) -> str:
    title = dict(_ATTR.findall(container.attrs)).get("title", "")
    return _titled_markdown(title, "\n".join(container.inner))


def _diagram_markdown(container: _Container) -> str:
    title = dict(_ATTR.findall(container.attrs)).get("title", "")
    description, source = _diagram_parts(container.inner)
    if source is None:
        return _titled_markdown(title, "\n".join(description if _has_content(description) else container.inner))
    parts = ["\n".join(description).strip(), _markdown_fence("mermaid", source)]
    return _titled_markdown(title, "\n\n".join(part for part in parts if part))


def _terminal_markdown(container: _Container) -> str:
    title = dict(_ATTR.findall(container.attrs)).get("title", "")
    content = _terminal_content(container.inner)
    if content is None:
        return _titled_markdown(title, "\n".join(container.inner))
    return _titled_markdown(title, _markdown_fence("console", content))


def _graphic_markdown(container: _Container) -> str:
    return "\n".join(container.inner)


def _render_html(source: str) -> str:
    nonce = uuid.uuid4().hex
    container_blocks: dict[str, str] = {}
    lines = _expand_containers(source.splitlines(), lambda container: _html_container(container, nonce, container_blocks), keep_blank=True)
    lines, blocks, definitions = _extract_blocks(lines, nonce)
    blocks = {**container_blocks, **blocks}
    rendered = _clean("\n".join(lines))
    rendered = _caption_standalone_images(rendered)
    for token, block_html in blocks.items():
        rendered = rendered.replace(f"<p>{token}</p>", block_html)
    # Token poza samodzielnym akapitem (np. w surowym bloku HTML autora) to blok, ktorego nie
    # wolno wstawic w nieznany kontekst, np. w wartosc atrybutu.
    rendered = re.sub(rf"blogblock{nonce}[nc]\d+[nc]", "", rendered)
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
    """Wpis -> markdown bliźniaka `.md`: bloki tekstowe bez zmian, kontenery bez markerow.

    Znany kontener zastepuje wynik jego `to_markdown` (etykieta + wnetrze), nieznany: sam srodek.
    """
    if not markdown_text.strip():
        return ""
    try:
        return "\n".join(_expand_containers(markdown_text.splitlines(), _markdown_container, keep_blank=False))
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
        for line in _expand_containers(markdown_text.splitlines(), lambda container: list(container.inner), keep_blank=False):
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


def extract_structured_data(markdown_text: str) -> dict[str, list[dict[str, str]]]:
    """Dane z kontenerow `faq`, `steps` i `video` (tekst bez markdownu i HTML) do JSON-LD.

    Zawsze zwraca trzy klucze (puste listy, gdy brak danych); nie rzuca wyjatku. Kontenery w
    kodzie ogrodzonym i niedomkniete sa pomijane (ten sam parser co render), tak samo wideo spoza
    bialej listy hostow.
    """
    try:
        faq: list[dict[str, str]] = []
        steps: list[dict[str, str]] = []
        videos: list[dict[str, str]] = []
        for part in _split_containers(markdown_text.splitlines()):
            if not isinstance(part, _Container):
                continue
            if part.kind == "faq":
                for question, answer in _parse_faq(part.inner) or []:
                    faq.append({"question": _plain(question), "answer": _plain("\n".join(answer))})
            elif part.kind == "steps":
                for text in _parse_steps(part.inner) or []:
                    name, body = _split_step(text)
                    steps.append({"name": _plain(name), "text": _plain(body)})
            elif part.kind == "video":
                parsed = _split_video(part.inner)
                if parsed is not None and _video_source(parsed[0]) is not None:
                    videos.append({"url": parsed[0], "description": _plain(parsed[1])})
        return {"faq": faq, "steps": steps, "videos": videos}
    except Exception:
        logger.exception("Blad wyciagania danych strukturalnych wpisu bloga")
        return {"faq": [], "steps": [], "videos": []}


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


def _container_block(name: str, to_html: Callable[..., str], to_markdown: Callable[..., str]) -> tuple[str, BlockDefinition]:
    return name, BlockDefinition(name=name, to_html=to_html, to_markdown=to_markdown)


CONTAINER_BLOCKS: dict[str, BlockDefinition] = dict(
    [
        _container_block("faq", _faq_html, _labelled_markdown("**FAQ**")),
        _container_block("steps", _steps_html, _labelled_markdown("**Kroki**")),
        _container_block("stats", _stats_html, _labelled_markdown("**Statystyki**")),
        _container_block("compare", _compare_html, _labelled_markdown("**Porównanie**")),
        _container_block("details", _details_html, _details_markdown),
        _container_block("gallery", _gallery_html, _labelled_markdown("**Galeria**")),
        _container_block("video", _video_html, _labelled_markdown("**Wideo**")),
        _container_block("cta", _cta_html, _labelled_markdown("**Wezwanie do działania**")),
        _container_block("glossary", _glossary_html, _labelled_markdown("**Słownik**")),
        _container_block("chart", _chart_html, _chart_markdown),
        _container_block("diagram", _diagram_html, _diagram_markdown),
        _container_block("terminal", _terminal_html, _terminal_markdown),
        _container_block("graphic", _graphic_html, _graphic_markdown),
    ]
)


@dataclass(frozen=True)
class BlockAssets:
    """Zasoby strony wpisu: czy ma diagram (biblioteka Mermaid) i ktore komponenty grafik dolaczyc."""

    has_diagram: bool
    graphics: tuple[str, ...]


def collect_block_assets(markdown_text: str) -> BlockAssets:
    """Zasoby wpisu liczone ta sama sciezka co render (kontenery w kodzie i niedomkniete sie nie licza); nie rzuca."""
    try:
        has_diagram = False
        graphics: list[str] = []
        for part in _split_containers(markdown_text.splitlines()):
            if not isinstance(part, _Container):
                continue
            if part.kind == "diagram":
                has_diagram = has_diagram or _diagram_parts(part.inner)[1] is not None
            elif part.kind == "graphic" and _has_content(part.inner):
                name = dict(_ATTR.findall(part.attrs)).get("name", "")
                if name in BLOG_GRAPHICS and name not in graphics:
                    graphics.append(name)
        return BlockAssets(has_diagram=has_diagram, graphics=tuple(graphics))
    except Exception:
        logger.exception("Blad zbierania zasobow blokow wpisu bloga")
        return BlockAssets(has_diagram=False, graphics=())


@dataclass(frozen=True)
class BlockIssue:
    """Problem w bloku graficznym wpisu (lint); `level` to zawsze `error`, `line` to linia otwarcia kontenera."""

    rule: str
    level: str
    line: int
    message: str


def _container_issues(container: _Container) -> list[tuple[str, str]]:
    """(regula, komunikat) dla jednego kontenera graficznego."""
    issues: list[tuple[str, str]] = []
    if container.kind == "graphic":
        if not _has_content(container.inner):
            issues.append(("graphic-missing-description", "Blok :::graphic wymaga akapitu z opisem grafiki."))
        if dict(_ATTR.findall(container.attrs)).get("name", "") not in BLOG_GRAPHICS:
            issues.append(("graphic-unknown-name", "Blok :::graphic wymaga atrybutu name z nazwą komponentu z rejestru BLOG_GRAPHICS."))
    elif container.kind == "chart":
        if parse_chart_table("\n".join(container.inner)) is None:
            issues.append(("chart-missing-data", "Blok :::chart wymaga tabeli z etykietami w pierwszej kolumnie i liczbami w kolejnych."))
    elif container.kind == "diagram":
        description, source = _diagram_parts(container.inner)
        if not _has_content(description):
            issues.append(("diagram-missing-description", "Blok :::diagram wymaga akapitu z opisem diagramu."))
        if source is None:
            issues.append(("diagram-missing-source", "Blok :::diagram wymaga niepustego bloku ```mermaid."))
    elif container.kind == "terminal" and _terminal_content(container.inner) is None:
        issues.append(("terminal-missing-content", "Blok :::terminal wymaga niepustego bloku ogrodzonego z treścią terminala."))
    return issues


def find_block_issues(markdown_text: str) -> list[BlockIssue]:
    """Braki w blokach graficznych (opis, dane, zrodlo, nazwa z rejestru) do lintu wpisu; nie rzuca."""
    try:
        return [
            BlockIssue(rule=rule, level="error", line=part.line, message=message)
            for part in _split_containers(markdown_text.splitlines())
            if isinstance(part, _Container)
            for rule, message in _container_issues(part)
        ]
    except Exception:
        logger.exception("Blad lintu blokow graficznych wpisu bloga")
        return []
