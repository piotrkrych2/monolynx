"""Testy jednostkowe MON-255, MON-256 -- silnik blokow wpisu bloga (services/blog_blocks.py).

Kontrakt: `render_post_html` (strona), `render_post_markdown` (bliźniak .md), rejestry
blokow `TEXT_BLOCKS` / `CONTAINER_BLOCKS` (9 kontenerow strukturalnych: faq, steps, stats,
compare, details, gallery, video, cta, glossary) i `extract_structured_data` (dane do JSON-LD).

Sygnatury `to_html` / `to_markdown` pojedynczych blokow sa sprawa wewnetrzna backendu --
testy sprawdzaja tylko `callable` i zachowanie obu funkcji renderujacych.
"""

import dataclasses
import re
from html.parser import HTMLParser
from typing import NamedTuple
from unittest.mock import patch

import pytest

from monolynx.services import blog_blocks
from monolynx.services.blog_blocks import (
    CONTAINER_BLOCKS,
    TEXT_BLOCKS,
    BlockDefinition,
    render_post_html,
    render_post_markdown,
    strip_block_markers,
)

# ---------------------------------------------------------------------------
# Helpery: parsowanie wyniku, zeby asercje dotyczyly realnych tagow i atrybutow,
# a nie podciagow (zaescapowany ladunek w tekscie nie jest wstrzyknieciem).
# ---------------------------------------------------------------------------


class _Elements(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.elements: list[tuple[str, dict[str, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.elements.append((tag, {name: value or "" for name, value in attrs}))


def _parse(html: str) -> list[tuple[str, dict[str, str]]]:
    parser = _Elements()
    parser.feed(html)
    parser.close()
    return parser.elements


def _tags(html: str) -> list[str]:
    return [tag for tag, _ in _parse(html)]


def _attr_pairs(html: str) -> list[tuple[str, str, str]]:
    return [(tag, name, value) for tag, attrs in _parse(html) for name, value in attrs.items()]


def _event_attrs(html: str) -> list[str]:
    return [name for _, name, _ in _attr_pairs(html) if name.startswith("on")]


def _unsafe_urls(html: str) -> list[str]:
    return [value for _, name, value in _attr_pairs(html) if name in {"href", "src"} and re.match(r"\s*(javascript|data|vbscript):", value, re.I)]


def _heading_ids(html: str) -> list[tuple[str, str | None]]:
    headings = {"h1", "h2", "h3", "h4", "h5", "h6"}
    return [(tag, attrs.get("id")) for tag, attrs in _parse(html) if tag in headings]


def _all_ids(html: str) -> list[str]:
    return [value for _, name, value in _attr_pairs(html) if name == "id"]


def _non_empty_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


ALERTS = [
    ("NOTE", "Uwaga"),
    ("TIP", "Wskazówka"),
    ("IMPORTANT", "Ważne"),
    ("WARNING", "Ostrzeżenie"),
    ("CAUTION", "Niebezpieczeństwo"),
    ("TLDR", "TL;DR"),
    ("QUOTE", None),
]


# ---------------------------------------------------------------------------
# Kryterium 1: alerty
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCallouts:
    @pytest.mark.parametrize(("marker", "label"), ALERTS)
    def test_alert_renders_aside_with_type_class_and_without_marker(self, marker, label):
        md = f"> [!{marker}]\n> Treść alertu {marker.lower()} dla czytelnika."

        html = render_post_html(md)

        assert f'class="blog-callout blog-callout-{marker.lower()}"' in html
        assert "<aside" in html
        assert f"[!{marker}]" not in html
        assert f"Treść alertu {marker.lower()} dla czytelnika." in html
        # Alert nie moze zostac zwyklym cytatem.
        assert "<blockquote" not in html

    @pytest.mark.parametrize(("marker", "label"), ALERTS)
    def test_alert_has_only_its_own_type_class(self, marker, label):
        html = render_post_html(f"> [!{marker}]\n> Treść.")

        for other, _ in ALERTS:
            if other != marker:
                assert f"blog-callout-{other.lower()}" not in html

    @pytest.mark.parametrize(("marker", "label"), [pair for pair in ALERTS if pair[1] is not None])
    def test_alert_has_polish_title(self, marker, label):
        html = render_post_html(f"> [!{marker}]\n> Treść.")

        assert f'<p class="blog-callout-title">{label}</p>' in html

    def test_quote_alert_has_no_title(self):
        html = render_post_html("> [!QUOTE]\n> Treść cytatu wyróżnionego.")

        assert "blog-callout-quote" in html
        assert "blog-callout-title" not in html
        assert "Treść cytatu wyróżnionego." in html

    @pytest.mark.parametrize(("source", "expected_class"), [("[!note]", "note"), ("[!Tip]", "tip"), ("[!wArNiNg]", "warning")])
    def test_alert_marker_is_case_insensitive(self, source, expected_class):
        html = render_post_html(f"> {source}\n> Treść.")

        assert f"blog-callout-{expected_class}" in html
        assert source not in html

    def test_alert_body_is_rendered_as_markdown(self):
        html = render_post_html("> [!NOTE]\n> To jest **ważne** zdanie.")

        assert "<strong>ważne</strong>" in html
        assert re.search(r"<aside[^>]*>.*<strong>ważne</strong>.*</aside>", html, re.S)

    def test_unknown_marker_stays_plain_blockquote(self):
        html = render_post_html("> [!FOO]\n> Treść nieznanego alertu.")

        assert "<blockquote>" in html
        assert "blog-callout" not in html
        assert "<aside" not in html
        assert "[!FOO]" in html
        assert "Treść nieznanego alertu." in html

    def test_marker_not_in_first_line_stays_plain_blockquote(self):
        html = render_post_html("> Pierwsza linia cytatu.\n> [!NOTE]\n> Reszta.")

        assert "<blockquote>" in html
        assert "blog-callout" not in html

    @pytest.mark.parametrize(("marker", "label"), ALERTS)
    def test_markdown_twin_returns_alert_unchanged(self, marker, label):
        md = f"> [!{marker}]\n> Treść alertu."

        assert render_post_markdown(md).strip() == md


# ---------------------------------------------------------------------------
# Kryterium 2: cytat z autorem
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestQuoteWithAuthor:
    def test_author_line_produces_figure_with_figcaption(self):
        html = render_post_html("> Cytat o testach.\n> -- Jan Kowalski, inżynier")

        assert 'class="blog-quote"' in html
        assert "<figure" in html
        assert "<figcaption>Jan Kowalski, inżynier</figcaption>" in html
        assert "<blockquote>" in html

    def test_author_line_is_not_left_inside_blockquote(self):
        html = render_post_html("> Cytat o testach.\n> -- Jan Kowalski, inżynier")

        quote = re.search(r"<blockquote>(.*?)</blockquote>", html, re.S)
        assert quote is not None
        assert "Cytat o testach." in quote.group(1)
        assert "Jan Kowalski" not in quote.group(1)
        assert "--" not in html

    def test_quote_without_author_line_stays_plain_blockquote(self):
        html = render_post_html("> Cytat bez autora.")

        assert "<blockquote>" in html
        assert "Cytat bez autora." in html
        assert "<figure" not in html
        assert "<figcaption" not in html

    def test_dash_line_not_last_does_not_make_a_figure(self):
        html = render_post_html("> Początek.\n> -- Nie autor, bo nie ostatnia\n> Koniec cytatu.")

        assert "<blockquote>" in html
        assert "<figure" not in html
        assert "<figcaption" not in html

    def test_markdown_twin_keeps_author_line(self):
        md = "> Cytat o testach.\n> -- Jan Kowalski, inżynier"

        assert render_post_markdown(md).strip() == md


# ---------------------------------------------------------------------------
# Kryterium 3: obrazek z podpisem
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFigure:
    def test_standalone_image_with_title_renders_figure_with_caption(self):
        html = render_post_html('![Wykres](https://example.com/y.png "Podpis obrazka")')

        assert 'class="blog-figure"' in html
        assert "<figure" in html
        assert "<figcaption>Podpis obrazka</figcaption>" in html
        assert 'src="https://example.com/y.png"' in html
        assert 'alt="Wykres"' in html
        assert 'loading="lazy"' in html
        assert 'decoding="async"' in html

    def test_image_inside_paragraph_stays_plain_img(self):
        html = render_post_html('Tekst przed ![Wykres](https://example.com/y.png "Podpis") i po.')

        assert "<img" in html
        assert "<figure" not in html
        assert "<figcaption" not in html
        assert "Tekst przed" in html

    def test_standalone_image_without_title_stays_plain_img(self):
        html = render_post_html("![Wykres](https://example.com/y.png)")

        assert "<img" in html
        assert 'src="https://example.com/y.png"' in html
        assert "<figure" not in html
        assert "<figcaption" not in html

    def test_markdown_twin_keeps_image_with_caption(self):
        md = '![Wykres](https://example.com/y.png "Podpis obrazka")'

        assert render_post_markdown(md).strip() == md


# ---------------------------------------------------------------------------
# Kod z nazwa pliku
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCodeBlock:
    def test_fence_with_title_renders_header_title_copy_button_and_language(self):
        html = render_post_html('```python title="app/main.py"\nprint("hi")\n```')

        assert 'class="blog-code"' in html
        assert 'class="blog-code-header"' in html
        title = re.search(r'<span class="blog-code-title">([^<]*)</span>', html)
        assert title is not None
        assert title.group(1) == "app/main.py"
        assert 'class="blog-code-copy"' in html
        assert 'type="button"' in html
        assert "Kopiuj" in html
        assert 'class="language-python"' in html
        assert "print(" in html

    def test_fence_without_title_has_copy_button_but_no_title_span(self):
        html = render_post_html('```python\nprint("hi")\n```')

        assert 'class="blog-code"' in html
        assert "blog-code-title" not in html
        assert 'class="blog-code-copy"' in html
        assert 'class="language-python"' in html

    def test_fence_without_language_has_no_language_class(self):
        html = render_post_html("```\nsurowy tekst\n```")

        assert 'class="blog-code"' in html
        assert "language-" not in html
        assert "surowy tekst" in html

    def test_title_with_spaces_is_kept_whole(self):
        html = render_post_html('```bash title="moje skrypty/run all.sh"\necho 1\n```')

        title = re.search(r'<span class="blog-code-title">([^<]*)</span>', html)
        assert title is not None
        assert title.group(1) == "moje skrypty/run all.sh"
        assert 'class="language-bash"' in html

    def test_script_inside_code_is_escaped(self):
        html = render_post_html("```html\n<script>alert(1)</script>\n```")

        assert "&lt;script&gt;" in html
        assert "script" not in _tags(html)

    def test_container_markers_inside_code_are_not_a_container(self):
        md = "```\n:::note\nkod w środku\n:::\n```"

        html = render_post_html(md)

        assert html.count(":::") == 2
        assert ":::note" in html
        assert "kod w środku" in html
        assert 'class="blog-code"' in html

    def test_markdown_twin_keeps_title_fence_verbatim(self):
        md = '```python title="app/main.py"\nprint("hi")\n```'

        assert render_post_markdown(md).strip() == md

    def test_markdown_twin_keeps_container_markers_inside_code(self):
        md = "```\n:::note\nkod w środku\n:::\n```"

        assert render_post_markdown(md).strip() == md


# ---------------------------------------------------------------------------
# Kryterium 4: kotwice naglowkow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHeadingAnchors:
    def test_h2_h3_h4_get_slug_ids(self):
        html = render_post_html("## Wstęp\n\n### Krok 1: Start\n\n#### Pod-sekcja!")

        assert _heading_ids(html) == [("h2", "wstep"), ("h3", "krok-1-start"), ("h4", "pod-sekcja")]

    def test_polish_characters_are_transliterated(self):
        html = render_post_html("## Zażółć gęślą jaźń")

        assert _heading_ids(html) == [("h2", "zazolc-gesla-jazn")]

    def test_uppercase_polish_l_with_stroke_is_transliterated(self):
        html = render_post_html("## Łódź\n\n### ŁÓDŹ ŻÓŁW")

        assert _heading_ids(html) == [("h2", "lodz"), ("h3", "lodz-zolw")]

    def test_slug_is_built_from_text_not_markup(self):
        html = render_post_html("## **Pogrubiony** tytuł")

        assert _heading_ids(html) == [("h2", "pogrubiony-tytul")]

    def test_symbols_collapse_and_are_trimmed_at_edges(self):
        html = render_post_html("## -- Hello,   World! --")

        assert _heading_ids(html) == [("h2", "hello-world")]

    def test_empty_slug_falls_back_to_sekcja(self):
        html = render_post_html("## !!!")

        assert _heading_ids(html) == [("h2", "sekcja")]

    def test_duplicate_headings_get_numeric_suffixes(self):
        html = render_post_html("## Powtórka\n\ntekst\n\n## Powtórka\n\ntekst\n\n## Powtórka")

        assert _heading_ids(html) == [("h2", "powtorka"), ("h2", "powtorka-2"), ("h2", "powtorka-3")]

    def test_ids_are_unique_across_heading_levels(self):
        html = render_post_html("## Powtórka\n\n### Powtórka\n\n#### Powtórka")

        ids = [heading_id for _, heading_id in _heading_ids(html)]
        assert len(ids) == 3
        assert len(set(ids)) == 3

    def test_empty_slug_duplicates_get_suffix(self):
        html = render_post_html("## !!!\n\n## ???")

        assert _heading_ids(html) == [("h2", "sekcja"), ("h2", "sekcja-2")]

    def test_h1_h5_h6_have_no_id(self):
        html = render_post_html("# Tytuł\n\n##### Piąty\n\n###### Szósty")

        assert _heading_ids(html) == [("h1", None), ("h5", None), ("h6", None)]

    def test_heading_inside_code_fence_does_not_consume_an_id(self):
        html = render_post_html("```\n## Powtórka\n```\n\n## Powtórka")

        assert _heading_ids(html) == [("h2", "powtorka")]

    def test_markdown_twin_keeps_headings_unchanged(self):
        md = "## Wstęp\n\ntekst\n\n### Zażółć gęślą jaźń"

        assert render_post_markdown(md).strip() == md


# ---------------------------------------------------------------------------
# Przypisy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFootnotes:
    MD = "Zdanie z przypisem[^1] oraz drugim[^2].\n\n[^1]: Opis pierwszego przypisu.\n[^2]: Opis drugiego przypisu."

    def test_reference_is_sup_with_anchor_to_note(self):
        html = render_post_html(self.MD)

        pairs = _attr_pairs(html)
        assert ("sup", "id", "blog-fnref-1") in pairs
        assert ("sup", "id", "blog-fnref-2") in pairs
        assert ("a", "href", "#blog-fn-1") in pairs
        assert ("a", "href", "#blog-fn-2") in pairs

    def test_notes_are_listed_in_footnotes_section_at_the_end(self):
        html = render_post_html(self.MD)

        pairs = _attr_pairs(html)
        assert ("section", "class", "blog-footnotes") in pairs
        assert ("li", "id", "blog-fn-1") in pairs
        assert ("li", "id", "blog-fn-2") in pairs
        assert "Opis pierwszego przypisu." in html
        assert "Opis drugiego przypisu." in html
        assert html.index("Zdanie z przypisem") < html.index("blog-footnotes") < html.index("Opis pierwszego przypisu.")

    def test_no_colon_ids_and_no_foreign_classes(self):
        html = render_post_html(self.MD)

        assert all(":" not in value for value in _all_ids(html))
        assert "fn:" not in html
        assert "fnref:" not in html
        classes = [value for _, name, value in _attr_pairs(html) if name == "class"]
        assert classes
        assert all(value.startswith("blog-") for value in classes)

    def test_no_footnotes_section_without_notes(self):
        html = render_post_html("Zwykły tekst bez przypisów.")

        assert "blog-footnotes" not in html
        assert "<sup" not in html

    def test_markdown_twin_keeps_footnotes_unchanged(self):
        assert render_post_markdown(self.MD).strip() == self.MD


# ---------------------------------------------------------------------------
# Kryterium 5: rejestr
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistry:
    def test_text_blocks_contains_required_blocks(self):
        assert {"callout", "quote", "figure", "code", "heading", "footnotes"} <= set(TEXT_BLOCKS)

    def test_container_blocks_has_exactly_the_nine_structural_types(self):
        assert set(CONTAINER_BLOCKS) == {"faq", "steps", "stats", "compare", "details", "gallery", "video", "cta", "glossary"}

    def test_every_registered_block_has_callable_renderers_and_matching_key(self):
        registered = [*TEXT_BLOCKS.items(), *CONTAINER_BLOCKS.items()]

        assert registered
        for key, definition in registered:
            assert isinstance(definition, BlockDefinition)
            assert definition.name == key
            assert callable(definition.to_html), key
            assert callable(definition.to_markdown), key

    def test_valid_definition_keeps_its_fields(self):
        def to_html(*args, **kwargs):
            return ""

        def to_markdown(*args, **kwargs):
            return ""

        definition = BlockDefinition(name="probe", to_html=to_html, to_markdown=to_markdown)

        assert definition.name == "probe"
        assert definition.to_html is to_html
        assert definition.to_markdown is to_markdown

    def test_definition_is_frozen(self):
        definition = BlockDefinition(name="probe", to_html=lambda *a, **k: "", to_markdown=lambda *a, **k: "")

        assert dataclasses.is_dataclass(definition)
        with pytest.raises(dataclasses.FrozenInstanceError):
            definition.name = "other"  # type: ignore[misc]

    @pytest.mark.parametrize(
        "kwargs",
        [
            pytest.param({"to_html": None, "to_markdown": lambda *a, **k: ""}, id="no-to_html"),
            pytest.param({"to_html": lambda *a, **k: "", "to_markdown": None}, id="no-to_markdown"),
            pytest.param({"to_html": None, "to_markdown": None}, id="no-renderers"),
            pytest.param({"to_html": "nie-wywolywalne", "to_markdown": lambda *a, **k: ""}, id="to_html-not-callable"),
            pytest.param({"to_html": lambda *a, **k: "", "to_markdown": 42}, id="to_markdown-not-callable"),
        ],
    )
    def test_definition_without_both_renderers_raises_type_error(self, kwargs):
        with pytest.raises(TypeError):
            BlockDefinition(name="broken", **kwargs)


# ---------------------------------------------------------------------------
# Kryterium 6: wstrzykniecia (kazdy przypadek osobno)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestInjections:
    def test_script_tag_is_removed(self):
        html = render_post_html("Przed.\n\n<script>alert(1)</script>\n\nPo.")

        assert "script" not in _tags(html)
        assert "alert(1)" not in html
        assert "Przed." in html
        assert "Po." in html

    def test_img_onerror_is_removed(self):
        html = render_post_html("Przed.\n\n<img src=x onerror=alert(1)>\n\nPo.")

        assert _event_attrs(html) == []
        assert "onerror" not in html
        assert "Po." in html

    def test_markdown_link_with_javascript_scheme_is_removed(self):
        html = render_post_html("Zobacz [x](javascript:alert(1)) tutaj.")

        assert _unsafe_urls(html) == []
        assert "javascript:" not in html.lower()
        assert "Zobacz" in html
        assert "tutaj." in html

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param('<a href="javascript:alert(1)">klik</a>', id="plain"),
            pytest.param('<a href="JaVaScRiPt:alert(1)">klik</a>', id="mixed-case"),
            pytest.param('<a href="&#106;avascript:alert(1)">klik</a>', id="entity-encoded"),
        ],
    )
    def test_raw_anchor_with_javascript_href_is_removed(self, raw):
        html = render_post_html(f"Przed {raw} po.")

        assert _unsafe_urls(html) == []
        assert "javascript:" not in html.lower()
        assert "klik" in html

    def test_hand_written_callout_class_is_removed(self):
        html = render_post_html('<div class="blog-callout-note">x</div>')

        assert "blog-callout-note" not in html
        assert [name for _, name, _ in _attr_pairs(html) if name == "class"] == []
        assert ">x<" in html

    @pytest.mark.parametrize(
        "raw",
        [
            pytest.param('<aside class="blog-callout blog-callout-tip">x</aside>', id="aside"),
            pytest.param('<figure class="blog-figure">x</figure>', id="figure"),
            pytest.param('<span class="blog-code-title">x</span>', id="span"),
            pytest.param('<section class="blog-footnotes">x</section>', id="section"),
            pytest.param('<p class="evil">x</p>', id="foreign-class"),
        ],
    )
    def test_hand_written_classes_never_reach_the_result(self, raw):
        html = render_post_html(raw)

        assert [value for _, name, value in _attr_pairs(html) if name == "class"] == []

    def test_hand_written_heading_id_is_removed(self):
        html = render_post_html('<h2 id="hack">Ręczny nagłówek</h2>')

        assert ("h2", "hack") not in [(tag, attrs.get("id")) for tag, attrs in _parse(html)]
        assert "hack" not in _all_ids(html)
        assert "Ręczny nagłówek" in html

    def test_hand_written_footnote_id_is_removed(self):
        html = render_post_html('<sup id="blog-fnref-1">1</sup> i <p id="blog-fn-1">akapit</p>')

        assert _all_ids(html) == []

    def test_data_uri_in_raw_img_src_is_removed(self):
        html = render_post_html('<img src="data:image/png;base64,AAAA" alt="x">')

        assert _unsafe_urls(html) == []
        assert "data:" not in html

    def test_data_uri_in_markdown_image_is_removed(self):
        html = render_post_html("![x](data:text/html;base64,AAAA)")

        assert _unsafe_urls(html) == []
        assert "data:" not in html

    def test_data_uri_in_markdown_link_is_removed(self):
        html = render_post_html("Zobacz [x](data:text/html,aaa) tutaj.")

        assert _unsafe_urls(html) == []
        assert "data:" not in html
        assert "tutaj." in html

    @pytest.mark.parametrize(
        "info",
        [
            pytest.param('title="a<script>alert(1)</script>.py"', id="script-in-title"),
            pytest.param('title="x\\"><script>alert(1)</script>"', id="quote-breakout-script"),
            pytest.param('title="x\\"><img src=x onerror=alert(1)>"', id="quote-breakout-img"),
            pytest.param('title="x" onclick="alert(1)"', id="extra-attribute"),
        ],
    )
    def test_fence_title_cannot_inject_markup(self, info):
        html = render_post_html(f"```python {info}\nprint(1)\n```")

        assert "script" not in _tags(html)
        assert "img" not in _tags(html)
        assert _event_attrs(html) == []
        assert 'class="blog-code"' in html

    @pytest.mark.parametrize(
        "caption",
        [
            pytest.param("<script>alert(1)</script>", id="script"),
            pytest.param("<img src=x onerror=alert(1)>", id="img-onerror"),
        ],
    )
    def test_image_caption_cannot_inject_markup(self, caption):
        html = render_post_html(f'![Opis](https://example.com/y.png "{caption}")')

        assert "script" not in _tags(html)
        assert _tags(html).count("img") == 1
        assert _event_attrs(html) == []

    def test_image_alt_cannot_break_out_of_the_attribute(self):
        html = render_post_html('![x" onerror="alert(1)](https://example.com/y.png "Podpis")')

        assert _event_attrs(html) == []
        assert _tags(html).count("img") == 1

    @pytest.mark.parametrize(
        "author",
        [
            pytest.param("<script>alert(1)</script>Autor", id="script"),
            pytest.param("<img src=x onerror=alert(1)>", id="img-onerror"),
        ],
    )
    def test_quote_author_cannot_inject_markup(self, author):
        html = render_post_html(f"> Cytat.\n> -- {author}")

        assert "script" not in _tags(html)
        assert "img" not in _tags(html)
        assert _event_attrs(html) == []

    @pytest.mark.parametrize("marker", ["NOTE", "QUOTE", "TLDR"])
    def test_script_inside_alert_body_is_removed(self, marker):
        html = render_post_html(f"> [!{marker}]\n> <script>alert(1)</script> Treść alertu.")

        assert "script" not in _tags(html)
        assert "alert(1)" not in html
        assert f"blog-callout-{marker.lower()}" in html
        assert "Treść alertu." in html

    def test_safe_link_keeps_href_and_rel(self):
        html = render_post_html("[Przykład](https://example.com)")

        pairs = _attr_pairs(html)
        assert ("a", "href", "https://example.com") in pairs
        assert ("a", "rel", "noopener noreferrer nofollow") in pairs

    def test_only_blog_and_language_classes_and_slug_ids_appear_in_a_full_post(self):
        md = (
            "# Tytuł wpisu\n\n"
            "> [!NOTE]\n> Notatka z **pogrubieniem**.\n\n"
            "> Cytat.\n> -- Jan Kowalski, inżynier\n\n"
            '![Alt](https://example.com/y.png "Podpis")\n\n'
            "## Sekcja pierwsza\n\n"
            '```python title="app/main.py"\nprint("x")\n```\n\n'
            "Przypis[^1].\n\n"
            "### Pod sekcja\n\n"
            '<div class="evil" id="evil">z</div>\n\n'
            "[^1]: Opis."
        )

        html = render_post_html(md)

        classes = [value for _, name, value in _attr_pairs(html) if name == "class"]
        assert classes
        for value in classes:
            assert all(token.startswith(("blog-", "language-")) for token in value.split()), value
        ids = _all_ids(html)
        assert ids
        assert all(re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", value) for value in ids), ids
        assert "evil" not in ids
        assert len(set(ids)) == len(ids)


# ---------------------------------------------------------------------------
# Kryterium 7: kontenery i wpis pusty
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestContainers:
    def test_unknown_container_renders_inner_markdown_without_markers(self):
        html = render_post_html(":::foo\n**pogrubione** w kontenerze\n:::")

        assert "<strong>pogrubione</strong>" in html
        assert "w kontenerze" in html
        assert ":::" not in html
        assert "foo" not in html

    def test_unknown_container_markdown_twin_drops_marker_lines_and_keeps_inner(self):
        result = render_post_markdown(":::foo\n**pogrubione** w kontenerze\n:::")

        assert _non_empty_lines(result) == ["**pogrubione** w kontenerze"]

    def test_container_attributes_do_not_leak_into_output(self):
        md = ':::foo title="Pytania" open="true"\nwnętrze kontenera\n:::'

        html = render_post_html(md)
        markdown = render_post_markdown(md)

        assert "wnętrze kontenera" in html
        assert ":::" not in html
        assert "Pytania" not in html
        assert 'open="true"' not in html
        assert _non_empty_lines(markdown) == ["wnętrze kontenera"]

    def test_container_between_paragraphs_keeps_surrounding_text(self):
        html = render_post_html("przed\n\n:::foo\nśrodek\n:::\n\npo")

        assert "przed" in html
        assert "środek" in html
        assert "po" in html
        assert html.index("przed") < html.index("środek") < html.rindex("po")
        assert ":::" not in html

    def test_unclosed_container_renders_as_plain_text_in_html(self):
        html = render_post_html(":::foo\nTekst bez zamknięcia.")

        assert ":::foo" in html
        assert "Tekst bez zamknięcia." in html

    def test_unclosed_container_markdown_twin_is_unchanged(self):
        md = ":::foo\nTekst bez zamknięcia."

        assert render_post_markdown(md).strip() == md

    def test_nested_container_marker_is_plain_text(self):
        md = ":::foo\nzewnątrz\n:::bar\nwewnątrz\n:::"

        html = render_post_html(md)
        markdown = render_post_markdown(md)

        assert ":::bar" in html
        assert ":::foo" not in html
        assert "zewnątrz" in html
        assert "wewnątrz" in html
        assert _non_empty_lines(markdown) == ["zewnątrz", ":::bar", "wewnątrz"]

    def test_empty_container_renders_without_markers(self):
        html = render_post_html(":::foo\n:::")
        markdown = render_post_markdown(":::foo\n:::")

        assert ":::" not in html
        assert markdown.strip() == ""

    @pytest.mark.parametrize("empty", ["", "   ", "\n\n", " \t\n "])
    def test_empty_post_returns_empty_string(self, empty):
        assert render_post_html(empty) == ""
        assert render_post_markdown(empty) == ""

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param(":::", id="lone-marker"),
            pytest.param(":::\n:::", id="two-markers-no-type"),
            pytest.param(':::foo title="niezamknięty\n:::', id="broken-attribute"),
            pytest.param(':::faq title="niezamknięty\n:::', id="broken-attribute-known-type"),
            pytest.param(':::compare highlight="2\n| a | b |\n|---|---|\n| 1 | 2 |\n:::', id="broken-attribute-compare"),
            pytest.param("> [!NOTE]", id="alert-without-body"),
            pytest.param("> [!NOTE]\n", id="alert-trailing-newline"),
            pytest.param("```", id="lone-fence"),
            pytest.param("```python title=", id="fence-empty-title"),
            pytest.param("```python\nkod bez zamknięcia", id="unclosed-fence"),
            pytest.param("[^1]", id="footnote-reference-without-note"),
            pytest.param("[^1]:", id="footnote-note-without-reference"),
            pytest.param('![](https://example.com/y.png "Podpis")', id="image-empty-alt"),
            pytest.param('![a](b "c"', id="broken-image"),
            pytest.param("<", id="lone-angle"),
            pytest.param("<<<>>>", id="angles"),
            pytest.param("\x00", id="nul"),
            pytest.param("###### ###### ######", id="deep-heading"),
            pytest.param("> > > > > [!NOTE]\n> > > > > x", id="nested-quotes"),
            pytest.param("-- Autor, rola", id="author-line-without-quote"),
            pytest.param("> -- Autor, rola", id="quote-only-author-line"),
        ],
    )
    def test_malformed_input_never_raises_and_never_emits_script(self, source):
        html = render_post_html(source)
        markdown = render_post_markdown(source)

        assert isinstance(html, str)
        assert isinstance(markdown, str)
        assert "script" not in _tags(html)

    def test_unclosed_fence_with_container_marker_inside_does_not_raise(self):
        html = render_post_html("```\n:::foo\nw kodzie bez zamknięcia")

        assert ":::foo" in html
        assert "w kodzie bez zamknięcia" in html


# ---------------------------------------------------------------------------
# strip_block_markers: zrodlo bez znacznikow blokow (streszczenie wpisu)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStripBlockMarkers:
    @pytest.mark.parametrize("marker", [name for name, _ in ALERTS])
    def test_alert_marker_line_is_removed_and_body_stays(self, marker):
        result = strip_block_markers(f"> [!{marker}]\n> Treść alertu {marker}.")

        assert f"[!{marker}]" not in result
        assert _non_empty_lines(result) == [f"> Treść alertu {marker}."]

    def test_unknown_marker_stays(self):
        source = "> [!FOO]\n> Treść."

        assert strip_block_markers(source) == source

    def test_marker_not_in_first_quote_line_stays(self):
        source = "> Pierwsza linia.\n> [!NOTE]\n> Reszta."

        assert strip_block_markers(source) == source

    def test_closed_container_markers_are_removed_and_inner_stays(self):
        result = strip_block_markers("przed\n:::foo\nwnętrze\n:::\npo")

        assert _non_empty_lines(result) == ["przed", "wnętrze", "po"]

    def test_container_attributes_are_removed_with_the_marker_line(self):
        result = strip_block_markers(':::foo title="Pytania"\nwnętrze\n:::')

        assert "Pytania" not in result
        assert _non_empty_lines(result) == ["wnętrze"]

    def test_unclosed_container_is_unchanged(self):
        source = ":::foo\nTekst bez zamknięcia."

        assert strip_block_markers(source) == source

    def test_markers_inside_code_fence_stay(self):
        source = "```\n> [!NOTE]\n:::foo\nkod\n:::\n```"

        assert strip_block_markers(source) == source

    def test_marker_outside_code_is_removed_while_marker_inside_code_stays(self):
        result = strip_block_markers("```\n> [!NOTE]\n```\n\n> [!TIP]\n> Treść.")

        assert "[!TIP]" not in result
        assert "> [!NOTE]" in result
        assert "> Treść." in result

    def test_text_without_markers_is_unchanged(self):
        source = "# Tytuł\n\nZwykły akapit.\n\n> Zwykły cytat."

        assert strip_block_markers(source) == source

    @pytest.mark.parametrize("empty", ["", "   ", "\n\t\n"])
    def test_empty_input_returns_blank_without_raising(self, empty):
        assert strip_block_markers(empty).strip() == ""


# ---------------------------------------------------------------------------
# Ujscia tresci autora: przypisy, sciezki awaryjne, kolizje id, podpisy
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFootnoteContentInjection:
    def test_script_and_event_handler_in_footnote_text_are_removed(self):
        html = render_post_html("Tekst[^1].\n\n[^1]: <script>alert(1)</script><img src=x onerror=alert(2)> opis")

        assert "script" not in _tags(html)
        assert _event_attrs(html) == []
        assert "alert(1)" not in html
        section = re.search(r'<section class="blog-footnotes">(.*?)</section>', html, re.S)
        assert section is not None
        assert "opis" in section.group(1)


@pytest.mark.unit
class TestRenderFailureFallbacks:
    def test_html_renderer_failure_falls_back_to_escaped_text(self):
        with patch("monolynx.services.blog_blocks._render_html", side_effect=RuntimeError("boom")):
            html = render_post_html("<script>alert(1)</script>")

        assert "<script" not in html
        assert "&lt;script&gt;" in html

    def test_markdown_twin_failure_returns_source_unchanged(self):
        source = ":::foo\nwnętrze\n:::"

        with patch("monolynx.services.blog_blocks._resolve_containers", side_effect=RuntimeError("boom")):
            result = render_post_markdown(source)

        assert result.strip() == source

    def test_strip_block_markers_failure_returns_source_unchanged(self):
        source = ":::foo\nwnętrze\n:::"

        with patch("monolynx.services.blog_blocks._resolve_containers", side_effect=RuntimeError("boom")):
            result = strip_block_markers(source)

        assert result == source


@pytest.mark.unit
class TestIdCollisionsAndEscaping:
    def test_heading_slug_colliding_with_footnote_prefix_gets_post_prefix(self):
        html = render_post_html("## blog fn 1\n\nTekst[^1].\n\n[^1]: opis")

        assert _heading_ids(html) == [("h2", "post-blog-fn-1")]
        ids = _all_ids(html)
        assert "blog-fn-1" in ids
        assert "blog-fnref-1" in ids
        assert len(set(ids)) == len(ids)

    def test_ampersand_in_image_caption_is_escaped_once(self):
        html = render_post_html('![Opis](https://example.com/y.png "Tom & Jerry")')

        assert "<figcaption>Tom &amp; Jerry</figcaption>" in html
        assert "&amp;amp;" not in html


@pytest.mark.unit
class TestBlocksInsideAlerts:
    def test_footnote_reference_only_inside_alert_keeps_note(self):
        html = render_post_html("> [!NOTE]\n> Tekst[^1].\n\n[^1]: opis przypisu")

        pairs = _attr_pairs(html)
        assert ("sup", "id", "blog-fnref-1") in pairs
        assert ("li", "id", "blog-fn-1") in pairs
        section = re.search(r'<section class="blog-footnotes">(.*?)</section>', html, re.S)
        assert section is not None
        assert "opis przypisu" in section.group(1)
        assert "blog-callout-note" in html

    def test_heading_inside_alert_gets_unique_slug_id(self):
        html = render_post_html("> [!NOTE]\n> ## Wewnątrz alertu\n> tekst\n\n## Wewnątrz alertu")

        ids = [heading_id for _, heading_id in _heading_ids(html)]
        assert sorted(ids) == ["wewnatrz-alertu", "wewnatrz-alertu-2"]


@pytest.mark.unit
class TestTokenGuard:
    def test_alert_inside_attribute_value_does_not_become_a_block(self):
        source = '<div>\n<img src="https://x/a.png" title="\n\n> [!NOTE]\n> x <b>B</b>\n\n">\n</div>'

        html = render_post_html(source)

        assert "blogblock" not in html
        assert "aside" not in _tags(html)
        assert "blog-callout" not in html
        images = [attrs for tag, attrs in _parse(html) if tag == "img"]
        assert len(images) == 1
        assert set(images[0]) == {"src", "title"}

    def test_fence_inside_attribute_value_does_not_inject_attributes(self):
        fence = '```py title="t"\n" onerror="alert(1)\n```'
        source = f'<div>\n<img src="https://x/a.png" title="\n\n{fence}\n\n">\n</div>'

        html = render_post_html(source)

        assert _event_attrs(html) == []
        assert "blog-code" not in html
        assert "blogblock" not in html


@pytest.mark.unit
class TestLongInputs:
    def test_author_line_with_very_long_trailing_whitespace_renders_clean_caption(self):
        html = render_post_html("> Cytat.\n> -- Jan Kowalski" + " " * 40000)

        assert "<figcaption>Jan Kowalski</figcaption>" in html
        assert "<figure" in html


# ---------------------------------------------------------------------------
# MON-256: kontenery strukturalne. Drzewo DOM do asercji na strukturze (np. "druga kolumna",
# "trzy elementy details"), bo samo szukanie podciagow tego nie rozroznia.
# ---------------------------------------------------------------------------

_VOID_TAGS = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"})


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.children: list[_Node | str] = []

    @property
    def classes(self) -> list[str]:
        return self.attrs.get("class", "").split()

    def elements(self) -> list["_Node"]:
        return [child for child in self.children if isinstance(child, _Node)]

    def descendants(self) -> list["_Node"]:
        found: list[_Node] = []
        for child in self.elements():
            found.append(child)
            found.extend(child.descendants())
        return found

    def text(self) -> str:
        parts = [child if isinstance(child, str) else child.text() for child in self.children]
        return " ".join("".join(parts).split())


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {name: value or "" for name, value in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].children.append(data)


def _dom(html: str) -> _Node:
    tree = _Tree()
    tree.feed(html)
    tree.close()
    return tree.root


def _find(root: _Node, tag: str | None = None, cls: str | None = None) -> list[_Node]:
    return [node for node in root.descendants() if (tag is None or node.tag == tag) and (cls is None or cls in node.classes)]


def _one(root: _Node, tag: str, cls: str | None = None) -> _Node:
    found = _find(root, tag, cls)
    assert len(found) == 1, f"oczekiwano jednego <{tag} class={cls}>, jest {len(found)}"
    return found[0]


def _block_classes(html: str, prefix: str) -> list[str]:
    return sorted({token for _, attrs in _parse(html) for token in attrs.get("class", "").split() if token.startswith(prefix)})


def _inner_lines(source: str) -> list[str]:
    return [line for line in source.splitlines()[1:-1] if line.strip()]


def _marker_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.startswith(":::")]


def _extract(markdown_text: str) -> dict:
    return blog_blocks.extract_structured_data(markdown_text)


class _Sample(NamedTuple):
    wrapper: str
    source: str
    label: str


_FAQ = (
    ":::faq\n"
    "### Jak zacząć?\nZałóż konto i utwórz **projekt**.\n\n"
    "### Co z danymi?\nDane są szyfrowane.\n\n"
    "### Ile to kosztuje?\nPierwszy projekt jest darmowy.\n"
    ":::"
)
_STEPS = (
    ":::steps\n"
    "1. **Zarejestruj konto** Wejdź na stronę i załóż konto.\n"
    "2. **Utwórz projekt** Nadaj mu nazwę.\n"
    "3. **Zaproś zespół** Wyślij zaproszenia.\n"
    "4. **Zacznij pracę** Dodaj pierwszy ticket.\n"
    ":::"
)
_STATS = ":::stats\n- **99,9%** - dostępność usługi\n- **120** - gotowych integracji\n- **3 min** - czas wdrożenia\n:::"
_TABLE = "| Funkcja | Monolynx | Konkurent |\n|---|---|---|\n| Wiki | tak | nie |\n| Scrum | tak | tak |"
_COMPARE = f':::compare highlight="2"\n{_TABLE}\n:::'
_DETAILS = ":::details Czy dane są bezpieczne?\nDane są **szyfrowane** w spoczynku.\n\nDrugi akapit odpowiedzi.\n:::"
_GALLERY = ":::gallery\n![Wieczór](https://example.com/a.png)\n![Ranek](https://example.com/b.png)\n![Południe](https://example.com/c.png)\n:::"
_VIDEO_DESCRIPTION = "Opis wideo o **Monolynx**."
_VIDEO = f":::video\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n\n{_VIDEO_DESCRIPTION}\n:::"
_CTA = ":::cta\nGotowy na zmianę? [Załóż konto](https://monolynx.com/register)\n:::"
_GLOSSARY = (
    ":::glossary\n- **Sprint** - iteracja pracy zespołu\n- **Ticket** - jednostka pracy w backlogu\n- **Burndown** - wykres postępu sprintu\n:::"
)

_SAMPLES = {
    "faq": _Sample("blog-faq", _FAQ, "**FAQ**"),
    "steps": _Sample("blog-steps", _STEPS, "**Kroki**"),
    "stats": _Sample("blog-stats", _STATS, "**Statystyki**"),
    "compare": _Sample("blog-compare", _COMPARE, "**Porównanie**"),
    "details": _Sample("blog-details", _DETAILS, "**Czy dane są bezpieczne?**"),
    "gallery": _Sample("blog-gallery", _GALLERY, "**Galeria**"),
    "video": _Sample("blog-video", _VIDEO, "**Wideo**"),
    "cta": _Sample("blog-cta", _CTA, "**Wezwanie do działania**"),
    "glossary": _Sample("blog-glossary", _GLOSSARY, "**Słownik**"),
}
_EVERY_SAMPLE = [pytest.param(sample, id=name) for name, sample in _SAMPLES.items()]
_BLOCK_NAMES = list(_SAMPLES)

# Zla struktura wnetrza: kontener degraduje do zwyklego markdownu (decyzja 6).
_BROKEN_STRUCTURE = {
    "faq": "Tylko akapit bez pytań.",
    "steps": "- punkt bez numeru\n- drugi punkt",
    "stats": "Akapit zamiast listy statystyk.",
    "compare": "Akapit bez tabeli.",
    "gallery": "Tekst bez obrazków.",
    "glossary": "Akapit zamiast listy pojęć.",
    "video": "Sam opis bez adresu.",
    "cta": "Tekst bez linku.",
}


# ---------------------------------------------------------------------------
# Wspolne wlasciwosci wszystkich 9 kontenerow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestEveryContainerType:
    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_html_block_is_a_single_top_level_element_between_surrounding_paragraphs(self, sample):
        html = render_post_html(f"przed\n\n{sample.source}\n\npo")

        top = _dom(html).elements()
        assert len(top) == 3
        assert (top[0].tag, top[0].text()) == ("p", "przed")
        assert (top[2].tag, top[2].text()) == ("p", "po")
        assert sample.wrapper in top[1].classes
        assert ":::" not in html
        assert "blogblock" not in html

    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_markdown_twin_has_label_then_inner_and_no_marker_lines(self, sample):
        result = render_post_markdown(f"przed\n\n{sample.source}\n\npo")

        assert _non_empty_lines(result) == ["przed", sample.label, *_inner_lines(sample.source), "po"]
        assert _marker_lines(result) == []

    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_opener_attributes_never_become_classes_ids_or_handlers(self, sample):
        opener, _, rest = sample.source.partition("\n")
        source = f'{opener} class="evil" id="evil" onclick="alert(1)"\n{rest}'

        html = render_post_html(source)

        assert "evil" not in [token for _, attrs in _parse(html) for token in attrs.get("class", "").split()]
        assert "evil" not in _all_ids(html)
        assert _event_attrs(html) == []

    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_unclosed_container_stays_plain_text(self, sample):
        source = sample.source.removesuffix("\n:::")

        html = render_post_html(source)

        assert _block_classes(html, sample.wrapper) == []
        assert render_post_markdown(source).strip() == source

    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_container_inside_code_fence_is_not_rendered(self, sample):
        source = f"```\n{sample.source}\n```"

        html = render_post_html(source)

        assert _block_classes(html, sample.wrapper) == []
        assert _block_classes(html, "blog-code") == ["blog-code", "blog-code-copy", "blog-code-header"]
        assert render_post_markdown(source).strip() == source

    @pytest.mark.parametrize("name", _BLOCK_NAMES)
    def test_empty_container_degrades_without_wrapper_label_or_markers(self, name):
        source = f":::{name}\n:::"

        html = render_post_html(source)
        markdown = render_post_markdown(source)

        assert _block_classes(html, _SAMPLES[name].wrapper) == []
        assert ":::" not in html
        assert markdown.strip() == ""

    @pytest.mark.parametrize("name", list(_BROKEN_STRUCTURE))
    def test_wrong_structure_degrades_to_plain_markdown_and_keeps_inner_text(self, name):
        inner = _BROKEN_STRUCTURE[name]
        source = f":::{name}\n{inner}\n:::"

        html = render_post_html(source)
        markdown = render_post_markdown(source)

        assert _block_classes(html, _SAMPLES[name].wrapper) == []
        assert ":::" not in html
        for fragment in (line.removeprefix("- ") for line in inner.splitlines()):
            assert fragment in _dom(html).text()
            assert fragment in markdown
        assert _marker_lines(markdown) == []

    @pytest.mark.parametrize(
        "inner",
        [
            pytest.param("Tekst obok.\n![Ranek](https://example.com/a.png)", id="text-before-image"),
            pytest.param("![Ranek](https://example.com/a.png)\n\nTekst obok.", id="text-after-image"),
        ],
    )
    def test_gallery_with_text_next_to_images_degrades_and_keeps_both(self, inner):
        html = render_post_html(f":::gallery\n{inner}\n:::")
        root = _dom(html)

        assert _block_classes(html, "blog-gallery") == []
        assert "Tekst obok." in root.text()
        assert [img.attrs["src"] for img in _find(root, "img")] == ["https://example.com/a.png"]

    @pytest.mark.parametrize(
        ("name", "inner", "needles"),
        [
            pytest.param(
                "faq", "Wstęp przed pytaniami.\n### Pytanie?\nOdpowiedź.", ["Wstęp przed pytaniami.", "Pytanie?", "Odpowiedź."], id="faq-preamble"
            ),
            pytest.param("faq", "###\nOdpowiedź bez pytania.", ["Odpowiedź bez pytania."], id="faq-empty-heading"),
            pytest.param("steps", "Wstęp przed listą.\n1. **A** opis", ["Wstęp przed listą.", "opis"], id="steps-preamble"),
            pytest.param("stats", "- **1** - opis\nZwykły tekst obok.", ["opis", "Zwykły tekst obok."], id="stats-stray-line"),
            pytest.param("glossary", "- **Termin** - definicja\nZwykły tekst obok.", ["definicja", "Zwykły tekst obok."], id="glossary-stray-line"),
        ],
    )
    def test_stray_text_next_to_structure_degrades_and_keeps_everything(self, name, inner, needles):
        html = render_post_html(f":::{name}\n{inner}\n:::")

        assert _block_classes(html, _SAMPLES[name].wrapper) == []
        for needle in needles:
            assert needle in _dom(html).text()

    def test_heading_line_inside_answer_code_fence_is_not_a_question(self):
        source = ":::faq\n### Jak?\n```\n### Komentarz\n```\nDalej.\n:::"

        items = _find(_dom(render_post_html(source)), "details", "blog-faq-item")

        assert len(items) == 1
        assert _one(items[0], "summary", "blog-faq-question").text() == "Jak?"

    def test_compare_with_highlight_but_no_table_degrades(self):
        source = ':::compare highlight="2"\nAkapit bez tabeli.\n:::'

        html = render_post_html(source)

        assert _block_classes(html, "blog-compare") == []
        assert "Akapit bez tabeli." in html

    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_raw_iframe_inside_and_outside_the_container_is_removed(self, sample):
        lines = sample.source.splitlines()
        outside = '<iframe src="https://evil.example/a"></iframe>'
        inside = '<iframe src="https://evil.example/b"></iframe>'
        source = "\n".join([outside, "", *lines[:-1], "", inside, lines[-1]])

        html = render_post_html(source)

        assert "iframe" not in _tags(html)
        assert [value for _, _, value in _attr_pairs(html) if "evil.example" in value] == []

    def test_block_coexists_with_callout_and_titled_code(self):
        source = f'> [!NOTE]\n> Treść alertu.\n\n{_FAQ}\n\n```python title="a.py"\nprint(1)\n```'

        root = _dom(render_post_html(source))

        assert len(_find(root, "aside", "blog-callout")) == 1
        assert len(_find(root, "details", "blog-faq-item")) == 3
        assert _one(root, "span", "blog-code-title").text() == "a.py"

    def test_faq_questions_do_not_consume_heading_slugs(self):
        html = render_post_html(f"{_FAQ}\n\n## Jak zacząć?")

        assert _heading_ids(html) == [("h2", "jak-zaczac")]

    def test_marker_line_inside_fence_does_not_close_the_container_early(self):
        source = ":::faq\n### Jak?\n```\n:::\n```\nDalej.\n:::"

        root = _dom(render_post_html(source))

        items = _find(root, "details", "blog-faq-item")
        assert len(items) == 1
        assert "Dalej." in items[0].text()


# ---------------------------------------------------------------------------
# faq
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFaqBlock:
    def test_three_questions_render_three_details_in_source_order(self):
        root = _dom(render_post_html(_FAQ))

        items = _find(_one(root, "div", "blog-faq"), "details", "blog-faq-item")
        assert len(items) == 3
        assert [_one(item, "summary", "blog-faq-question").text() for item in items] == ["Jak zacząć?", "Co z danymi?", "Ile to kosztuje?"]
        assert [_one(item, "div", "blog-faq-answer").text() for item in items] == [
            "Załóż konto i utwórz projekt.",
            "Dane są szyfrowane.",
            "Pierwszy projekt jest darmowy.",
        ]

    def test_answer_is_rendered_as_markdown_and_question_heading_is_consumed(self):
        root = _dom(render_post_html(_FAQ))

        faq = _one(root, "div", "blog-faq")
        assert [node.text() for node in _find(faq, "strong")] == ["projekt"]
        assert _find(faq, "h3") == []

    def test_multi_paragraph_answer_stays_in_its_own_item(self):
        source = ":::faq\n### Pierwsze?\nAkapit 1.\n\nAkapit 2.\n\n- punkt\n\n### Drugie?\nKrótko.\n:::"

        root = _dom(render_post_html(source))

        items = _find(root, "details", "blog-faq-item")
        assert len(items) == 2
        first = _one(items[0], "div", "blog-faq-answer")
        assert [node.text() for node in _find(first, "p")] == ["Akapit 1.", "Akapit 2."]
        assert [node.text() for node in _find(first, "li")] == ["punkt"]
        assert _one(items[1], "div", "blog-faq-answer").text() == "Krótko."


# ---------------------------------------------------------------------------
# steps
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStepsBlock:
    def test_four_points_render_four_steps_with_names_in_source_order(self):
        root = _dom(render_post_html(_STEPS))

        steps = _find(_one(root, "ol", "blog-steps"), "li", "blog-step")
        assert len(steps) == 4
        assert [_one(step, "span", "blog-step-name").text() for step in steps] == [
            "Zarejestruj konto",
            "Utwórz projekt",
            "Zaproś zespół",
            "Zacznij pracę",
        ]
        bodies = [_one(step, "div", "blog-step-body").text() for step in steps]
        assert "Wejdź na stronę i załóż konto." in bodies[0]
        assert "Dodaj pierwszy ticket." in bodies[3]

    def test_point_without_bold_start_has_no_step_name(self):
        source = ":::steps\n1. **Pierwszy** opis a\n2. Drugi bez pogrubienia\n3. **Trzeci** opis c\n:::"

        steps = _find(_dom(render_post_html(source)), "li", "blog-step")

        assert len(steps) == 3
        assert [len(_find(step, "span", "blog-step-name")) for step in steps] == [1, 0, 1]
        assert "Drugi bez pogrubienia" in _one(steps[1], "div", "blog-step-body").text()

    def test_bold_in_the_middle_of_a_point_is_not_a_step_name(self):
        source = ":::steps\n1. Zwykły krok z **pogrubieniem** w środku\n2. **Nazwa** opis\n:::"

        steps = _find(_dom(render_post_html(source)), "li", "blog-step")

        assert [len(_find(step, "span", "blog-step-name")) for step in steps] == [0, 1]


# ---------------------------------------------------------------------------
# stats
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStatsBlock:
    def test_each_list_item_is_a_stat_with_value_and_label_in_source_order(self):
        root = _dom(render_post_html(_STATS))

        stats = _find(_one(root, "div", "blog-stats"), "div", "blog-stat")
        assert len(stats) == 3
        assert [_one(stat, "span", "blog-stat-value").text() for stat in stats] == ["99,9%", "120", "3 min"]
        assert [_one(stat, "span", "blog-stat-label").text() for stat in stats] == ["dostępność usługi", "gotowych integracji", "czas wdrożenia"]


# ---------------------------------------------------------------------------
# compare
# ---------------------------------------------------------------------------


def _compare(highlight: str | None) -> str:
    attribute = "" if highlight is None else f' highlight="{highlight}"'
    return f":::compare{attribute}\n{_TABLE}\n:::"


def _compare_rows(html: str) -> list[list[_Node]]:
    table = _one(_dom(html), "table")
    return [[cell for cell in row.elements() if cell.tag in {"th", "td"}] for row in _find(table, "tr")]


def _highlighted_columns(html: str) -> list[int]:
    rows = _compare_rows(html)
    return sorted({index for row in rows for index, cell in enumerate(row) if "blog-compare-highlight" in cell.classes})


@pytest.mark.unit
class TestCompareBlock:
    def test_table_is_wrapped_and_keeps_its_cells(self):
        html = render_post_html(_compare(None))

        _one(_dom(html), "div", "blog-compare")
        rows = _compare_rows(html)
        assert [cell.text() for cell in rows[0]] == ["Funkcja", "Monolynx", "Konkurent"]
        assert [[cell.tag for cell in row] for row in rows] == [["th"] * 3, ["td"] * 3, ["td"] * 3]
        assert [cell.text() for cell in rows[1]] == ["Wiki", "tak", "nie"]

    def test_highlight_two_marks_second_column_in_header_and_body_cells(self):
        html = render_post_html(_COMPARE)

        rows = _compare_rows(html)
        assert len(rows) == 3
        assert _highlighted_columns(html) == [1]
        assert all("blog-compare-highlight" in row[1].classes for row in rows)
        assert all("blog-compare-highlight" not in row[index].classes for row in rows for index in (0, 2))

    @pytest.mark.parametrize(("highlight", "column"), [("1", 0), ("2", 1), ("3", 2)])
    def test_highlight_is_one_indexed_up_to_the_last_column(self, highlight, column):
        assert _highlighted_columns(render_post_html(_compare(highlight))) == [column]

    @pytest.mark.parametrize("highlight", [None, "abc", "0", "-1", "4", "99", ""])
    def test_invalid_or_out_of_range_highlight_marks_no_cell_but_renders_the_table(self, highlight):
        html = render_post_html(_compare(highlight))

        _one(_dom(html), "div", "blog-compare")
        assert len(_compare_rows(html)) == 3
        assert _highlighted_columns(html) == []


# ---------------------------------------------------------------------------
# details
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestDetailsBlock:
    def test_title_goes_to_summary_and_body_is_rendered_markdown(self):
        root = _dom(render_post_html(_DETAILS))

        details = _one(root, "details", "blog-details")
        assert _one(details, "summary", "blog-details-summary").text() == "Czy dane są bezpieczne?"
        body = _one(details, "div", "blog-details-body")
        assert [node.text() for node in _find(body, "p")] == ["Dane są szyfrowane w spoczynku.", "Drugi akapit odpowiedzi."]
        assert [node.text() for node in _find(body, "strong")] == ["szyfrowane"]
        assert "Czy dane" not in body.text()

    def test_title_without_text_falls_back_to_szczegoly(self):
        html = render_post_html(":::details\nTreść ukryta.\n:::")

        details = _one(_dom(html), "details", "blog-details")
        assert _one(details, "summary", "blog-details-summary").text() == "Szczegóły"
        assert "Treść ukryta." in _one(details, "div", "blog-details-body").text()

    def test_markdown_twin_without_title_uses_szczegoly_label(self):
        result = render_post_markdown(":::details\nTreść ukryta.\n:::")

        assert _non_empty_lines(result) == ["**Szczegóły**", "Treść ukryta."]

    def test_title_with_special_characters_is_escaped_exactly_once(self):
        html = render_post_html(":::details Tom & Jerry <3\nTreść.\n:::")

        summary = _one(_dom(html), "summary", "blog-details-summary")
        assert summary.text() == "Tom & Jerry <3"

    def test_title_with_markup_does_not_create_elements_or_handlers(self):
        html = render_post_html(':::details x"><img src=x onerror=alert(1)>\nTreść.\n:::')

        assert _event_attrs(html) == []
        assert _tags(html).count("details") == 1
        assert _tags(html).count("summary") == 1


# ---------------------------------------------------------------------------
# gallery
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGalleryBlock:
    @pytest.mark.parametrize("separator", ["\n", "\n\n"], ids=["same-paragraph", "separate-paragraphs"])
    def test_each_image_is_a_figure_with_src_alt_and_lazy_loading_in_source_order(self, separator):
        images = ["![Wieczór](https://example.com/a.png)", "![Ranek](https://example.com/b.png)", "![Południe](https://example.com/c.png)"]
        source = ":::gallery\n" + separator.join(images) + "\n:::"

        gallery = _one(_dom(render_post_html(source)), "div", "blog-gallery")

        figures = _find(gallery, "figure", "blog-gallery-item")
        assert len(figures) == 3
        imgs = [_one(figure, "img") for figure in figures]
        assert [img.attrs["alt"] for img in imgs] == ["Wieczór", "Ranek", "Południe"]
        assert [img.attrs["src"] for img in imgs] == ["https://example.com/a.png", "https://example.com/b.png", "https://example.com/c.png"]
        assert [img.attrs.get("loading") for img in imgs] == ["lazy"] * 3

    def test_image_with_empty_alt_still_has_an_alt_attribute(self):
        html = render_post_html(":::gallery\n![](https://example.com/a.png)\n:::")

        imgs = _find(_dom(html), "img")
        assert len(imgs) == 1
        assert "alt" in imgs[0].attrs

    def test_alt_cannot_break_out_of_the_attribute(self):
        html = render_post_html(':::gallery\n![x" onerror="alert(1)](https://example.com/a.png)\n:::')

        assert _event_attrs(html) == []
        assert len(_find(_dom(html), "img")) == 1

    @pytest.mark.parametrize("src", ["javascript:alert(1)", "data:image/png;base64,AAAA"])
    def test_unsafe_image_source_never_reaches_the_result(self, src):
        html = render_post_html(f":::gallery\n![x]({src})\n:::")

        assert _unsafe_urls(html) == []
        assert _block_classes(html, "blog-gallery") == []

    def test_image_with_rejected_source_is_skipped_while_valid_one_stays(self):
        html = render_post_html(":::gallery\n![zły](javascript:alert(1))\n![dobry](https://example.com/ok.png)\n:::")

        gallery = _one(_dom(html), "div", "blog-gallery")
        figures = _find(gallery, "figure", "blog-gallery-item")
        assert len(figures) == 1
        assert _one(figures[0], "img").attrs["src"] == "https://example.com/ok.png"
        assert _unsafe_urls(html) == []


# ---------------------------------------------------------------------------
# video
# ---------------------------------------------------------------------------

_VIDEO_TEXT = "Opis wideo o Monolynx."
_VIDEO_ACCEPTED = [
    pytest.param("https://youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", id="youtube.com"),
    pytest.param("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", id="www.youtube.com"),
    pytest.param("https://m.youtube.com/watch?v=dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", id="m.youtube.com"),
    pytest.param("https://youtu.be/dQw4w9WgXcQ", "youtube", "dQw4w9WgXcQ", id="youtu.be"),
    pytest.param("https://vimeo.com/123456789", "vimeo", "123456789", id="vimeo.com"),
    pytest.param("https://www.vimeo.com/123456789", "vimeo", "123456789", id="www.vimeo.com"),
    pytest.param("https://player.vimeo.com/video/123456789", "vimeo", "123456789", id="player.vimeo.com"),
    # Granice identyfikatora: YouTube 6..20 znakow [A-Za-z0-9_-], Vimeo cyfry.
    pytest.param("https://www.youtube.com/watch?v=abcdef", "youtube", "abcdef", id="youtube-id-min-6"),
    pytest.param(f"https://www.youtube.com/watch?v={'a' * 20}", "youtube", "a" * 20, id="youtube-id-max-20"),
    pytest.param("https://www.youtube.com/watch?v=abc_-DEF12", "youtube", "abc_-DEF12", id="youtube-id-charset"),
    pytest.param("https://vimeo.com/1", "vimeo", "1", id="vimeo-id-single-digit"),
]
# Poprawny schemat https, ale host spoza dokladnej listy albo zly id: renderuje sie opis + zwykly link.
_VIDEO_LINKED_REJECTS = [
    pytest.param("https://evil.com/watch?v=dQw4w9WgXcQ", id="foreign-host"),
    pytest.param("https://youtube.com.evil.com/watch?v=dQw4w9WgXcQ", id="youtube-as-subdomain-prefix"),
    pytest.param("https://www.youtube.com.evil.com/watch?v=dQw4w9WgXcQ", id="www-youtube-as-subdomain-prefix"),
    pytest.param("https://vimeo.com.evil.com/123456789", id="vimeo-as-subdomain-prefix"),
    pytest.param("https://evil.com/youtube.com/watch?v=dQw4w9WgXcQ", id="whitelisted-host-in-path"),
    pytest.param("https://notyoutube.com/watch?v=dQw4w9WgXcQ", id="host-suffix-match"),
    pytest.param("https://evilvimeo.com/123456789", id="vimeo-suffix-match"),
    pytest.param("https://youtube.com@evil.com/watch?v=dQw4w9WgXcQ", id="userinfo-trick"),
    pytest.param("https://music.youtube.com/watch?v=dQw4w9WgXcQ", id="subdomain-not-on-list"),
    pytest.param("https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ", id="nocookie-not-on-list"),
    pytest.param("http://www.youtube.com/watch?v=dQw4w9WgXcQ", id="http-youtube"),
    pytest.param("http://youtu.be/dQw4w9WgXcQ", id="http-youtu.be"),
    pytest.param("http://vimeo.com/123456789", id="http-vimeo"),
    pytest.param("https://www.youtube.com/watch?v=abcde", id="youtube-id-too-short-5"),
    pytest.param(f"https://www.youtube.com/watch?v={'a' * 21}", id="youtube-id-too-long-21"),
    pytest.param("https://user@www.youtube.com/watch?v=dQw4w9WgXcQ", id="userinfo-on-whitelisted-host"),
    pytest.param("https://www.youtube.com:8443/watch?v=dQw4w9WgXcQ", id="port-on-whitelisted-host"),
    pytest.param("https://www.youtube.com/embed/x?v=dQw4w9WgXcQ", id="youtube-path-not-watch"),
    pytest.param("https://www.youtube.com/watch?v=dQw4w9WgXcQ&v=abcdefgh", id="youtube-double-v"),
    pytest.param("https://www.youtube.com/watch", id="youtube-no-v"),
    pytest.param("https://www.youtube.com/watch?v=", id="youtube-empty-v"),
    pytest.param("https://www.youtube.com/watch?v=abc.defghi", id="youtube-id-bad-char"),
    pytest.param("https://youtu.be/abcde", id="youtu.be-id-too-short"),
    pytest.param("https://vimeo.com/12a45", id="vimeo-id-not-digits"),
    pytest.param("https://player.vimeo.com/video/abc", id="player-vimeo-id-not-digits"),
]
# Schemat inny niz http/https: ani blok, ani link.
_VIDEO_UNLINKED_REJECTS = [
    pytest.param("javascript:alert(1)", id="javascript"),
    pytest.param("ftp://www.youtube.com/watch?v=dQw4w9WgXcQ", id="ftp"),
    pytest.param("data:text/html;base64,AAAA", id="data"),
    pytest.param("vbscript:msgbox(1)", id="vbscript"),
]


def _video(url: str, description: str = _VIDEO_DESCRIPTION) -> str:
    return f":::video\n{url}\n\n{description}\n:::"


@pytest.mark.unit
class TestVideoBlock:
    @pytest.mark.parametrize(("url", "provider", "video_id"), _VIDEO_ACCEPTED)
    def test_whitelisted_host_renders_click_to_play_placeholder(self, url, provider, video_id):
        root = _dom(render_post_html(_video(url)))

        block = _one(root, "div", "blog-video")
        assert block.attrs["data-provider"] == provider
        assert block.attrs["data-video-id"] == video_id
        play = _one(block, "a", "blog-video-play")
        assert play.attrs["href"] == url
        assert play.attrs["target"] == "_blank"
        assert {"noopener", "noreferrer", "nofollow"} <= set(play.attrs["rel"].split())
        caption = _one(block, "div", "blog-video-caption")
        assert caption.text() == _VIDEO_TEXT
        assert [node.text() for node in _find(caption, "strong")] == ["Monolynx"]

    @pytest.mark.parametrize(("url", "provider", "video_id"), _VIDEO_ACCEPTED[:7])
    def test_whitelisted_video_makes_no_external_request_before_click(self, url, provider, video_id):
        html = render_post_html(_video(url))

        assert {"iframe", "img", "video", "embed", "object", "source", "script"}.isdisjoint(_tags(html))
        assert [name for _, name, _ in _attr_pairs(html) if name in {"src", "srcset", "data-src", "poster"}] == []
        assert "ytimg" not in html
        assert "vimeocdn" not in html

    @pytest.mark.parametrize("url", _VIDEO_LINKED_REJECTS)
    def test_rejected_address_renders_description_and_plain_link_without_video_markup(self, url):
        html = render_post_html(_video(url))
        root = _dom(html)

        assert _block_classes(html, "blog-video") == []
        assert "data-video-id" not in html
        assert "data-provider" not in html
        assert {"iframe", "img"}.isdisjoint(_tags(html))
        assert _VIDEO_TEXT in root.text()
        links = _find(root, "a")
        assert [link.attrs["href"] for link in links] == [url]
        assert "nofollow" in links[0].attrs["rel"].split()

    @pytest.mark.parametrize("url", _VIDEO_UNLINKED_REJECTS)
    def test_non_http_scheme_renders_description_only_without_link(self, url):
        html = render_post_html(_video(url))
        root = _dom(html)

        assert _block_classes(html, "blog-video") == []
        assert "data-video-id" not in html
        assert _find(root, "a") == []
        assert _unsafe_urls(html) == []
        assert _VIDEO_TEXT in root.text()

    @pytest.mark.parametrize(
        "url",
        [
            pytest.param('https://evil.example.com/"onmouseover="alert(1)', id="foreign-host"),
            pytest.param('https://www.youtube.com/watch?v=abcdef"onmouseover="alert(1)', id="whitelisted-host-bad-id"),
        ],
    )
    def test_quote_breakout_in_address_never_adds_an_attribute(self, url):
        html = render_post_html(_video(url))

        assert _event_attrs(html) == []
        assert _block_classes(html, "blog-video") == []

    def test_unparsable_address_renders_description_only_without_address_or_link(self):
        html = render_post_html(_video("https://[bad/watch?v=dQw4w9WgXcQ", "Opis."))
        root = _dom(html)

        assert _block_classes(html, "blog-video") == []
        assert _find(root, "a") == []
        assert root.text() == "Opis."
        assert "bad" not in html

    def test_video_without_address_keeps_only_the_description(self):
        html = render_post_html(":::video\nSam opis bez adresu.\n:::")

        assert _block_classes(html, "blog-video") == []
        assert "Sam opis bez adresu." in _dom(html).text()
        assert _find(_dom(html), "a") == []


# ---------------------------------------------------------------------------
# cta
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCtaBlock:
    @pytest.mark.parametrize(
        "inner",
        [
            pytest.param("Gotowy na zmianę? [Załóż konto](https://monolynx.com/register)", id="link-inside-paragraph"),
            pytest.param("Gotowy na zmianę?\n\n[Załóż konto](https://monolynx.com/register)", id="link-in-own-paragraph"),
        ],
    )
    def test_paragraph_and_exactly_one_link_render_a_button(self, inner):
        root = _dom(render_post_html(f":::cta\n{inner}\n:::"))

        cta = _one(root, "div", "blog-cta")
        button = _one(cta, "a", "blog-cta-button")
        assert button.attrs["href"] == "https://monolynx.com/register"
        assert button.text() == "Załóż konto"
        assert "Gotowy na zmianę?" in _one(cta, "p", "blog-cta-text").text()

    def test_no_link_renders_inner_as_plain_markdown(self):
        html = render_post_html(":::cta\nTekst bez **linku**.\n:::")
        root = _dom(html)

        assert _block_classes(html, "blog-cta") == []
        assert _find(root, "a") == []
        assert [node.text() for node in _find(root, "strong")] == ["linku"]
        assert "Tekst bez linku." in root.text()

    def test_link_with_empty_label_renders_inner_as_plain_markdown(self):
        html = render_post_html(":::cta\nZachęta [](https://example.com/start)\n:::")

        assert _block_classes(html, "blog-cta") == []
        assert "Zachęta" in _dom(html).text()

    def test_sole_link_without_paragraph_renders_inner_as_plain_markdown(self):
        html = render_post_html(":::cta\n[Załóż konto](https://example.com/start)\n:::")
        root = _dom(html)

        assert _block_classes(html, "blog-cta") == []
        links = _find(root, "a")
        assert [(link.attrs["href"], link.text()) for link in links] == [("https://example.com/start", "Załóż konto")]

    def test_paragraph_with_link_plus_list_renders_inner_as_plain_markdown(self):
        html = render_post_html(":::cta\nZachęta [Załóż konto](https://example.com/start)\n\n- punkt pierwszy\n- punkt drugi\n:::")
        root = _dom(html)

        assert _block_classes(html, "blog-cta") == []
        assert [node.text() for node in _find(root, "li")] == ["punkt pierwszy", "punkt drugi"]
        assert [link.attrs["href"] for link in _find(root, "a")] == ["https://example.com/start"]

    def test_two_links_render_inner_as_plain_markdown_with_both_links(self):
        html = render_post_html(":::cta\nZobacz [pierwszy](https://example.com/1) albo [drugi](https://example.com/2).\n:::")
        root = _dom(html)

        assert _block_classes(html, "blog-cta") == []
        assert [link.attrs["href"] for link in _find(root, "a")] == ["https://example.com/1", "https://example.com/2"]
        assert [link.text() for link in _find(root, "a")] == ["pierwszy", "drugi"]

    @pytest.mark.parametrize("url", ["javascript:alert(1)", "data:text/html,aaa"])
    def test_single_link_with_unsafe_scheme_never_yields_an_unsafe_href(self, url):
        html = render_post_html(f":::cta\nKliknij [tutaj]({url})\n:::")

        assert _unsafe_urls(html) == []
        assert "javascript:" not in html.lower()

    def test_href_with_quotes_stays_inside_the_attribute(self):
        html = render_post_html(':::cta\nKliknij [tutaj](https://example.com/?a="onclick="alert(1))\n:::')

        assert _event_attrs(html) == []
        for node in _find(_dom(html), "a"):
            assert set(node.attrs) <= {"class", "href", "rel", "target", "title"}


# ---------------------------------------------------------------------------
# glossary
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGlossaryBlock:
    def test_each_list_item_is_a_dt_dd_pair_in_source_order(self):
        glossary = _one(_dom(render_post_html(_GLOSSARY)), "dl", "blog-glossary")

        elements = glossary.elements()
        assert [node.tag for node in elements] == ["dt", "dd"] * 3
        assert [node.text() for node in elements[0::2]] == ["Sprint", "Ticket", "Burndown"]
        assert [node.text() for node in elements[1::2]] == ["iteracja pracy zespołu", "jednostka pracy w backlogu", "wykres postępu sprintu"]


# ---------------------------------------------------------------------------
# Wstrzykniecia przez kazde ujscie tresci autora w kontenerach
# ---------------------------------------------------------------------------

_PAYLOADS = [
    pytest.param("<script>alert(1)</script>", id="script"),
    pytest.param("<img src=x onerror=alert(1)>", id="img-onerror"),
    pytest.param('"><img src=x onerror=alert(1)>', id="quote-breakout"),
    pytest.param('<iframe src="https://evil.example/x"></iframe>', id="iframe"),
]
_INJECTION_SITES = [
    pytest.param(":::details {p}\nTreść.\n:::", id="details-title"),
    pytest.param(":::details Tytuł\n{p}\n:::", id="details-body"),
    pytest.param(":::faq\n### {p}\nOdpowiedź.\n:::", id="faq-question"),
    pytest.param(":::faq\n### Pytanie?\n{p}\n:::", id="faq-answer"),
    pytest.param(":::steps\n1. **{p}** opis kroku\n2. **Drugi** opis\n:::", id="step-name"),
    pytest.param(":::steps\n1. **Nazwa** {p}\n2. **Drugi** opis\n:::", id="step-body"),
    pytest.param(":::stats\n- **{p}** - opis\n:::", id="stat-value"),
    pytest.param(":::stats\n- **100** - {p}\n:::", id="stat-label"),
    pytest.param(':::compare highlight="{p}"\n| a | b |\n|---|---|\n| 1 | 2 |\n:::', id="compare-highlight"),
    pytest.param(':::compare highlight="2"\n| a | b |\n|---|---|\n| {p} | 2 |\n:::', id="compare-cell"),
    pytest.param(":::gallery\n![{p}](https://example.com/a.png)\n:::", id="gallery-alt"),
    pytest.param(":::glossary\n- **{p}** - definicja\n:::", id="glossary-term"),
    pytest.param(":::glossary\n- **Termin** - {p}\n:::", id="glossary-definition"),
    pytest.param(":::video\nhttps://www.youtube.com/watch?v=dQw4w9WgXcQ\n\n{p}\n:::", id="video-description"),
    pytest.param(":::video\nhttps://www.youtube.com/watch?v={p}\n\nOpis.\n:::", id="video-id"),
    pytest.param(":::video\nhttps://evil.example.com/{p}\n\nOpis.\n:::", id="video-foreign-url"),
    pytest.param(":::cta\nZachęta {p} [Kliknij](https://example.com)\n:::", id="cta-text"),
    pytest.param(":::cta\nZachęta [{p}](https://example.com)\n:::", id="cta-label"),
]


@pytest.mark.unit
class TestInjectionsInContainers:
    @pytest.mark.parametrize("payload", _PAYLOADS)
    @pytest.mark.parametrize("template", _INJECTION_SITES)
    def test_payload_never_becomes_script_iframe_handler_or_unsafe_url(self, template, payload):
        source = template.replace("{p}", payload)

        html = render_post_html(source)
        markdown = render_post_markdown(source)

        assert {"script", "iframe"}.isdisjoint(_tags(html))
        assert _event_attrs(html) == []
        assert _unsafe_urls(html) == []
        assert "blogblock" not in html
        assert isinstance(markdown, str)


# ---------------------------------------------------------------------------
# Zagniezdzenie, awarie rendererow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNestedContainers:
    def test_container_inside_container_is_plain_text_of_the_outer_one(self):
        source = ":::faq\n### Pytanie?\nOdpowiedź.\n:::cta\n[Załóż konto](https://example.com)\n:::"

        html = render_post_html(source)
        markdown = render_post_markdown(source)

        root = _dom(html)
        assert len(_find(root, "details", "blog-faq-item")) == 1
        assert _block_classes(html, "blog-cta") == []
        assert ":::cta" in root.text()
        assert _non_empty_lines(markdown) == [
            "**FAQ**",
            "### Pytanie?",
            "Odpowiedź.",
            ":::cta",
            "[Załóż konto](https://example.com)",
        ]

    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_container_inside_attribute_value_does_not_become_a_block(self, sample):
        source = f'<div>\n<img src="https://x/a.png" title="\n\n{sample.source}\n\n">\n</div>'

        html = render_post_html(source)

        assert "blogblock" not in html
        assert _block_classes(html, "blog-") == []
        assert {"details", "summary"}.isdisjoint(_tags(html))

    def test_extra_closing_marker_after_container_does_not_raise(self):
        source = f"{_FAQ}\n:::\n"

        root = _dom(render_post_html(source))

        assert len(_find(root, "details", "blog-faq-item")) == 3


@pytest.mark.unit
class TestContainerRendererFailures:
    @staticmethod
    def _definition(name: str, to_html, to_markdown) -> BlockDefinition:
        return BlockDefinition(name=name, to_html=to_html, to_markdown=to_markdown)

    @staticmethod
    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    def test_registered_html_renderer_output_is_used_for_that_type(self):
        probe = self._definition("faq", lambda *a, **k: '<div class="probe-block">x</div>', lambda *a, **k: "")

        with patch.dict(CONTAINER_BLOCKS, {"faq": probe}):
            html = render_post_html(_FAQ)

        assert len(_find(_dom(html), "div", "probe-block")) == 1
        assert _block_classes(html, "blog-faq") == []

    def test_registered_markdown_renderer_output_is_used_for_that_type(self):
        probe = self._definition("faq", lambda *a, **k: '<div class="probe-block">x</div>', lambda *a, **k: "ZAMIENNIK-MD")

        with patch.dict(CONTAINER_BLOCKS, {"faq": probe}):
            result = render_post_markdown(_FAQ)

        assert _non_empty_lines(result) == ["ZAMIENNIK-MD"]

    def test_html_renderer_exception_degrades_only_that_container(self):
        broken = self._definition("faq", self._boom, self._boom)

        with patch.dict(CONTAINER_BLOCKS, {"faq": broken}):
            html = render_post_html(f"przed\n\n{_FAQ}\n\npo")

        top = _dom(html).elements()
        assert (top[0].tag, top[0].text()) == ("p", "przed")
        assert (top[-1].tag, top[-1].text()) == ("p", "po")
        assert "Jak zacząć?" in _dom(html).text()
        assert ":::" not in html
        assert _block_classes(html, "blog-faq") == []

    def test_markdown_renderer_exception_degrades_only_that_container(self):
        broken = self._definition("faq", self._boom, self._boom)

        with patch.dict(CONTAINER_BLOCKS, {"faq": broken}):
            result = render_post_markdown(f"przed\n\n{_FAQ}\n\npo")

        assert _marker_lines(result) == []
        assert _non_empty_lines(result) == ["przed", *_inner_lines(_FAQ), "po"]


# ---------------------------------------------------------------------------
# strip_block_markers dla znanych kontenerow
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStripMarkersOfStructuralContainers:
    @pytest.mark.parametrize("sample", _EVERY_SAMPLE)
    def test_marker_lines_are_removed_and_inner_text_stays(self, sample):
        result = strip_block_markers(f"przed\n\n{sample.source}\n\npo")

        assert _marker_lines(result) == []
        lines = _non_empty_lines(result)
        assert lines[0] == "przed"
        assert lines[-1] == "po"
        for inner in _inner_lines(sample.source):
            assert inner in result


# ---------------------------------------------------------------------------
# extract_structured_data
# ---------------------------------------------------------------------------

_EMPTY_DATA = {"faq": [], "steps": [], "videos": []}


@pytest.mark.unit
class TestExtractStructuredData:
    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("", id="empty"),
            pytest.param("Zwykły tekst bez bloków.", id="plain-text"),
            pytest.param("# Nagłówek\n\n> [!NOTE]\n> Alert.\n\n> Cytat.\n> -- Autor", id="text-blocks-only"),
            pytest.param(":::foo\nnieznany typ\n:::", id="unknown-container"),
            pytest.param(_GLOSSARY, id="glossary-is-not-extracted"),
            pytest.param(_STATS, id="stats-are-not-extracted"),
            pytest.param(_COMPARE, id="compare-is-not-extracted"),
        ],
    )
    def test_always_returns_three_keys_with_empty_lists_when_nothing_to_extract(self, source):
        assert _extract(source) == _EMPTY_DATA

    def test_faq_with_three_questions_gives_three_plain_text_pairs_in_source_order(self):
        data = _extract(_FAQ)

        assert data["faq"] == [
            {"question": "Jak zacząć?", "answer": "Załóż konto i utwórz projekt."},
            {"question": "Co z danymi?", "answer": "Dane są szyfrowane."},
            {"question": "Ile to kosztuje?", "answer": "Pierwszy projekt jest darmowy."},
        ]
        assert data["steps"] == []
        assert data["videos"] == []

    def test_faq_answer_has_no_html_even_when_author_wrote_some(self):
        data = _extract(":::faq\n### Pytanie?\nOdpowiedź <script>alert(1)</script> z kodem.\n:::")

        assert len(data["faq"]) == 1
        assert "<script" not in data["faq"][0]["answer"]
        assert "Odpowiedź" in data["faq"][0]["answer"]

    def test_steps_with_four_points_gives_four_entries_with_name_and_text(self):
        data = _extract(_STEPS)

        assert data["steps"] == [
            {"name": "Zarejestruj konto", "text": "Wejdź na stronę i załóż konto."},
            {"name": "Utwórz projekt", "text": "Nadaj mu nazwę."},
            {"name": "Zaproś zespół", "text": "Wyślij zaproszenia."},
            {"name": "Zacznij pracę", "text": "Dodaj pierwszy ticket."},
        ]

    def test_step_without_bold_start_has_empty_name_and_whole_point_as_text(self):
        data = _extract(":::steps\n1. **Pierwszy** opis a\n2. Drugi bez pogrubienia\n3. Zwykły krok z **pogrubieniem** w środku\n:::")

        assert [step["name"] for step in data["steps"]] == ["Pierwszy", "", ""]
        assert data["steps"][1]["text"] == "Drugi bez pogrubienia"
        assert data["steps"][2]["text"] == "Zwykły krok z pogrubieniem w środku"

    @pytest.mark.parametrize(("url", "provider", "video_id"), _VIDEO_ACCEPTED)
    def test_whitelisted_video_is_listed_with_url_and_plain_description(self, url, provider, video_id):
        assert _extract(_video(url))["videos"] == [{"url": url, "description": _VIDEO_TEXT}]

    @pytest.mark.parametrize("url", [*_VIDEO_LINKED_REJECTS, *_VIDEO_UNLINKED_REJECTS])
    def test_video_outside_whitelist_is_not_listed(self, url):
        assert _extract(_video(url))["videos"] == []

    def test_videos_keep_source_order(self):
        source = f"{_video('https://vimeo.com/123456789', 'Drugi.')}\n\n{_video('https://youtu.be/dQw4w9WgXcQ', 'Pierwszy.')}"

        assert _extract(source)["videos"] == [
            {"url": "https://vimeo.com/123456789", "description": "Drugi."},
            {"url": "https://youtu.be/dQw4w9WgXcQ", "description": "Pierwszy."},
        ]

    def test_all_three_kinds_are_collected_from_one_post(self):
        data = _extract(f"{_FAQ}\n\n{_STEPS}\n\n{_VIDEO}\n\n{_GLOSSARY}")

        assert len(data["faq"]) == 3
        assert len(data["steps"]) == 4
        assert len(data["videos"]) == 1
        assert set(data) == {"faq", "steps", "videos"}

    @pytest.mark.parametrize("source", [_FAQ, _STEPS, _VIDEO], ids=["faq", "steps", "video"])
    def test_container_inside_code_fence_is_skipped(self, source):
        assert _extract(f"```\n{source}\n```") == _EMPTY_DATA

    def test_container_after_a_code_fence_is_still_extracted(self):
        inside = ":::faq\n### Wewnątrz?\nKod.\n:::"

        data = _extract(f"```\n{inside}\n```\n\n{_FAQ}")

        assert [item["question"] for item in data["faq"]] == ["Jak zacząć?", "Co z danymi?", "Ile to kosztuje?"]

    @pytest.mark.parametrize("source", [_FAQ, _STEPS, _VIDEO], ids=["faq", "steps", "video"])
    def test_unclosed_container_is_not_extracted(self, source):
        assert _extract(source.removesuffix("\n:::")) == _EMPTY_DATA

    @pytest.mark.parametrize(
        "source",
        [
            pytest.param("\x00", id="nul"),
            pytest.param(":::", id="lone-marker"),
            pytest.param(":::faq", id="lone-opener"),
            pytest.param(":::faq\n:::", id="empty-faq"),
            pytest.param(":::faq\n###\n:::", id="empty-question"),
            pytest.param(":::steps\n1.\n:::", id="empty-step"),
            pytest.param(":::steps\n1. **\n:::", id="broken-bold"),
            pytest.param(":::video\nhttps://\n:::", id="url-without-host"),
            pytest.param(":::video\n:::\n:::video\n:::", id="empty-videos"),
            pytest.param("```", id="lone-fence"),
            pytest.param("<<<>>>", id="angles"),
            pytest.param(f":::faq\n### {'x' * 10000}\n{'y' * 10000}\n:::", id="very-long"),
        ],
    )
    def test_garbage_input_never_raises_and_keeps_the_three_keys(self, source):
        data = _extract(source)

        assert set(data) == {"faq", "steps", "videos"}
        assert all(isinstance(value, list) for value in data.values())
        assert isinstance(render_post_html(source), str)
        assert isinstance(render_post_markdown(source), str)

    def test_unparsable_video_address_does_not_discard_other_data_of_the_post(self):
        source = f":::faq\n### Pytanie?\nOdpowiedź.\n:::\n\n{_video('https://[bad/watch?v=dQw4w9WgXcQ', 'Opis.')}"

        data = _extract(source)

        assert data["faq"] == [{"question": "Pytanie?", "answer": "Odpowiedź."}]
        assert data["videos"] == []

    def test_internal_failure_returns_three_empty_lists_instead_of_raising(self):
        with patch("monolynx.services.blog_blocks._split_containers", side_effect=RuntimeError("boom")):
            data = _extract(_FAQ)

        assert data == _EMPTY_DATA
