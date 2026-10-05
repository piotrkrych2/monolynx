"""Testy jednostkowe MON-255 -- silnik blokow wpisu bloga (services/blog_blocks.py).

Kontrakt: `render_post_html` (strona), `render_post_markdown` (bliźniak .md) i rejestry
blokow `TEXT_BLOCKS` / `CONTAINER_BLOCKS`.

Sygnatury `to_html` / `to_markdown` pojedynczych blokow sa sprawa wewnetrzna backendu --
testy sprawdzaja tylko `callable` i zachowanie obu funkcji renderujacych.
"""

import dataclasses
import re
from html.parser import HTMLParser
from unittest.mock import patch

import pytest

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

    def test_container_blocks_is_empty_in_this_ticket(self):
        assert CONTAINER_BLOCKS == {}

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
        html = render_post_html(":::faq\n**pogrubione** w kontenerze\n:::")

        assert "<strong>pogrubione</strong>" in html
        assert "w kontenerze" in html
        assert ":::" not in html
        assert "faq" not in html

    def test_unknown_container_markdown_twin_drops_marker_lines_and_keeps_inner(self):
        result = render_post_markdown(":::faq\n**pogrubione** w kontenerze\n:::")

        assert _non_empty_lines(result) == ["**pogrubione** w kontenerze"]

    def test_container_attributes_do_not_leak_into_output(self):
        md = ':::faq title="Pytania" open="true"\nwnętrze kontenera\n:::'

        html = render_post_html(md)
        markdown = render_post_markdown(md)

        assert "wnętrze kontenera" in html
        assert ":::" not in html
        assert "Pytania" not in html
        assert 'open="true"' not in html
        assert _non_empty_lines(markdown) == ["wnętrze kontenera"]

    def test_container_between_paragraphs_keeps_surrounding_text(self):
        html = render_post_html("przed\n\n:::faq\nśrodek\n:::\n\npo")

        assert "przed" in html
        assert "środek" in html
        assert "po" in html
        assert html.index("przed") < html.index("środek") < html.rindex("po")
        assert ":::" not in html

    def test_unclosed_container_renders_as_plain_text_in_html(self):
        html = render_post_html(":::faq\nTekst bez zamknięcia.")

        assert ":::faq" in html
        assert "Tekst bez zamknięcia." in html

    def test_unclosed_container_markdown_twin_is_unchanged(self):
        md = ":::faq\nTekst bez zamknięcia."

        assert render_post_markdown(md).strip() == md

    def test_nested_container_marker_is_plain_text(self):
        md = ":::faq\nzewnątrz\n:::note\nwewnątrz\n:::"

        html = render_post_html(md)
        markdown = render_post_markdown(md)

        assert ":::note" in html
        assert ":::faq" not in html
        assert "zewnątrz" in html
        assert "wewnątrz" in html
        assert _non_empty_lines(markdown) == ["zewnątrz", ":::note", "wewnątrz"]

    def test_empty_container_renders_without_markers(self):
        html = render_post_html(":::faq\n:::")
        markdown = render_post_markdown(":::faq\n:::")

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
            pytest.param(':::faq title="niezamknięty\n:::', id="broken-attribute"),
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
        html = render_post_html("```\n:::faq\nw kodzie bez zamknięcia")

        assert ":::faq" in html
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
        result = strip_block_markers("przed\n:::faq\nwnętrze\n:::\npo")

        assert _non_empty_lines(result) == ["przed", "wnętrze", "po"]

    def test_container_attributes_are_removed_with_the_marker_line(self):
        result = strip_block_markers(':::faq title="Pytania"\nwnętrze\n:::')

        assert "Pytania" not in result
        assert _non_empty_lines(result) == ["wnętrze"]

    def test_unclosed_container_is_unchanged(self):
        source = ":::faq\nTekst bez zamknięcia."

        assert strip_block_markers(source) == source

    def test_markers_inside_code_fence_stay(self):
        source = "```\n> [!NOTE]\n:::faq\nkod\n:::\n```"

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
        source = ":::faq\nwnętrze\n:::"

        with patch("monolynx.services.blog_blocks._resolve_containers", side_effect=RuntimeError("boom")):
            result = render_post_markdown(source)

        assert result.strip() == source

    def test_strip_block_markers_failure_returns_source_unchanged(self):
        source = ":::faq\nwnętrze\n:::"

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
