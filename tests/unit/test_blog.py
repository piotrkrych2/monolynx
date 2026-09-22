"""Testy jednostkowe MON-201 -- sanityzacja markdown (nh3) dla publicznego /blog/{slug}
i odczyt publicznych stron wiki (get_public_page_by_slug).

Zanim implementacja powstanie, import ponizej ma sie NIE powiesc (render_markdown_public
i get_public_page_by_slug jeszcze nie istnieja w monolynx.services.wiki) -- to jest
zamierzone, TDD: testy sa specyfikacja dla backend-developera.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from monolynx.blog import templates as blog_templates
from monolynx.services.wiki import (
    get_public_page_by_slug,
    render_markdown_html,
    render_markdown_public,
)


@pytest.mark.unit
class TestRenderMarkdownPublicSanitization:
    """render_markdown_public() -- markdown -> HTML -> nh3.clean() dla publicznego bloga.

    W przeciwienstwie do render_markdown_html() (dashboard, chroniony sesja + RBAC),
    ta funkcja obsluguje trese pisana przez userow ktorej finalny odbiorca (nieautoryzowany
    czytelnik /blog/{slug}) nie jest chroniony niczym innym -- sanityzacja jest jedyna linia
    obrony przed XSS.
    """

    def test_strips_script_tag(self):
        """Tag <script> jest usuwany z wyjscia."""
        raw = "Tresc artykulu.\n\n<script>alert(1)</script>\n\nDalej."
        result = render_markdown_public(raw)

        assert "<script" not in result
        assert "alert(1)" not in result

    def test_strips_onerror_event_attribute(self):
        """Atrybut zdarzenia onerror jest usuwany (np. <img onerror=...>)."""
        raw = '<img src="x" onerror="alert(1)">'
        result = render_markdown_public(raw)

        assert "onerror" not in result

    def test_strips_onclick_event_attribute(self):
        """Atrybut zdarzenia onclick jest usuwany."""
        raw = '<div onclick="alert(1)">Klik tutaj</div>'
        result = render_markdown_public(raw)

        assert "onclick" not in result

    def test_strips_onload_event_attribute(self):
        """Atrybut zdarzenia onload jest usuwany (np. <svg onload=...>)."""
        raw = '<svg onload="alert(1)"></svg>'
        result = render_markdown_public(raw)

        assert "onload" not in result

    def test_strips_javascript_href(self):
        """Href z protokolem javascript: jest usuwany z linku."""
        raw = "[Kliknij tutaj](javascript:alert(1))"
        result = render_markdown_public(raw)

        assert "javascript:" not in result

    def test_preserves_headers(self):
        """Naglowki markdown (h1, h2) sa zachowane."""
        result = render_markdown_public("# Tytul glowny\n\n## Podtytul")

        assert "<h1" in result
        assert "Tytul glowny" in result
        assert "<h2" in result
        assert "Podtytul" in result

    def test_preserves_lists(self):
        """Listy nieuporzadkowane sa zachowane."""
        result = render_markdown_public("- pierwszy punkt\n- drugi punkt")

        assert "<li>" in result
        assert "pierwszy punkt" in result
        assert "drugi punkt" in result

    def test_preserves_code_and_pre(self):
        """Blok kodu (<pre><code>) jest zachowany."""
        result = render_markdown_public("```python\nprint('hello')\n```")

        assert "<pre" in result
        assert "<code" in result
        assert "print" in result

    def test_preserves_inline_code(self):
        """Kod inline (<code>) jest zachowany."""
        result = render_markdown_public("Uzyj `pip install monolynx`.")

        assert "<code>pip install monolynx</code>" in result

    def test_preserves_table(self):
        """Tabela markdown jest zachowana."""
        md = "| Kolumna1 | Kolumna2 |\n|----------|----------|\n| A        | B        |"
        result = render_markdown_public(md)

        assert "<table>" in result
        assert "Kolumna1" in result
        assert "A" in result

    def test_preserves_safe_link(self):
        """Link z bezpiecznym href (https) jest zachowany wraz z href."""
        result = render_markdown_public("[Example](https://example.com)")

        assert 'href="https://example.com"' in result
        assert "Example" in result

    def test_preserves_bold_text(self):
        """Pogrubienie jest zachowane."""
        result = render_markdown_public("**bardzo wazne**")

        assert "<strong>bardzo wazne</strong>" in result


@pytest.mark.unit
class TestRenderMarkdownHtmlUnchanged:
    """Test regresyjny (MON-201): render_markdown_html() (dashboard) NIE zyskuje sanityzacji.

    Ticket wymaga, zeby render_markdown_html() zostalo NIEZMIENIONE -- ten test nie
    sprawdza nowej funkcjonalnosci, tylko potwierdza ze dashboard (chroniony sesja + RBAC)
    zachowuje dotychczasowe zachowanie (surowy HTML przechodzi bez zmian, bo python-markdown
    domyslnie nie sanityzuje). Nie rozszerzac tego testu o asercje sanityzacji.
    """

    def test_raw_script_passes_through_unsanitized(self):
        raw = "Tresc.\n\n<script>alert(1)</script>\n\nDalej."
        result = render_markdown_html(raw)

        assert "<script>alert(1)</script>" in result


@pytest.mark.unit
class TestGetPublicPageBySlug:
    """get_public_page_by_slug() -- odczyt strony wiki po slugu, filtr is_public=True.

    Uzywane przez GET /blog/{slug} (brak sesji, brak project_slug w URL) -- funkcja
    musi zwracac None (nigdy nie rzucac) dla kazdego przypadku, ktory endpoint mapuje na 404.
    """

    async def test_returns_page_when_is_public_true(self):
        mock_page = MagicMock()
        mock_page.slug = "architektura"
        mock_page.is_public = True

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=mock_page)))

        result = await get_public_page_by_slug("architektura", mock_db)

        assert result is mock_page

    async def test_returns_none_when_page_is_private(self):
        """Strona istnieje, ale is_public=False -- funkcja zwraca None, nie rzuca wyjatku.

        Zapytanie filtruje WikiPage.is_public.is_(True) na poziomie SQL, wiec prywatna
        strona nigdy nie trafia do wyniku SELECT -- stad scalar_one_or_none() == None.
        """
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        result = await get_public_page_by_slug("prywatna-strona", mock_db)

        assert result is None

    async def test_returns_none_when_slug_does_not_exist(self):
        """Nieistniejacy slug -- None, bez wyjatku."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        result = await get_public_page_by_slug("nie-istnieje-taki-slug", mock_db)

        assert result is None

    async def test_queries_with_correct_slug_argument(self):
        """Funkcja wywoluje db.execute -- kontrakt: przyjmuje slug jako str i db jako AsyncSession."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        await get_public_page_by_slug("dowolny-slug", mock_db)

        mock_db.execute.assert_awaited_once()


# ---------------------------------------------------------------------------
# MON-202: blok {% block canonical %} w layoucie, og:type=article,
# article:published_time i meta_description z _extract_summary() dla wpisu.
#
# Renderujemy szablony Jinja2 bezposrednio (blog_templates.get_template(...).render(...)),
# bez klienta HTTP i bez bazy -- to jest test kontraktu szablonu, nie endpointu.
# Import ponizej (monolynx.blog) juz dzis sie udaje (MON-201 scalone), wiec RED tych
# testow bierze sie z TRESCI renderu, nie z ImportError: dopoki MON-202 nie jest
# zaimplementowane, blog/page.html nie ma og:type=article, article:published_time ani
# meta_description z parametru (zamiast statycznego tekstu "<tytul> - wiki projektu
# Monolynx") -- ponizsze asercje tresci musza wiec zawodzic.
# ---------------------------------------------------------------------------


def _render_blog_page(**overrides):
    """Renderuje blog/page.html z minimalnym, kompletnym kontekstem."""
    context = {
        "lang": "en",
        "app_url": "http://localhost:8000",
        "page_title": "Testowy Wpis MON202",
        "page_slug": "testowy-wpis-mon202",
        "content_html": "<p>Tresc testowa.</p>",
        "breadcrumbs": [],
        "children": [],
        "backlinks": [],
        "updated_at": datetime(2026, 1, 15, 10, 30, tzinfo=UTC),
        "created_at": datetime(2026, 1, 10, 9, 0, tzinfo=UTC),
        "meta_description": "Streszczenie artykulu testowego MON202 XYZ.",
    }
    context.update(overrides)
    return blog_templates.get_template("blog/page.html").render(context)


def _render_landing_layout(**overrides):
    """Renderuje layouts/landing.html samodzielnie (bez dziedziczenia po nim) --
    sprawdza wartosci domyslne blokow (og:type, brak canonical), ktore dostaje kazda
    strona NIE nadpisujaca tych blokow (np. landing, /features/*)."""
    context = {"lang": "en", "app_url": "http://localhost:8000"}
    context.update(overrides)
    return blog_templates.get_template("layouts/landing.html").render(context)


@pytest.mark.unit
class TestBlogPageCanonicalBlock:
    """Kryterium 2 (+ ochrona przed regresja przy wydzielaniu {% block canonical %}
    do layoutu): dokladnie jeden rel="canonical" w <head> strony wpisu."""

    def test_canonical_appears_exactly_once(self):
        result = _render_blog_page()

        assert result.count('rel="canonical"') == 1

    def test_canonical_points_to_app_url_plus_blog_slug(self):
        result = _render_blog_page(app_url="http://localhost:8000", page_slug="testowy-wpis-mon202")

        assert 'href="http://localhost:8000/blog/testowy-wpis-mon202"' in result


@pytest.mark.unit
class TestBlogPageOgType:
    """Kryterium: strona wpisu ma og:type=article + article:published_time."""

    def test_og_type_is_article(self):
        result = _render_blog_page()

        assert 'property="og:type" content="article"' in result

    def test_article_published_time_matches_created_at_isoformat(self):
        created = datetime(2026, 1, 10, 9, 0, tzinfo=UTC)
        result = _render_blog_page(created_at=created)

        assert f'property="article:published_time" content="{created.isoformat()}"' in result


@pytest.mark.unit
class TestBlogPageMetaDescriptionFromSummary:
    """Kryterium 3: og:title/og:description/og:url wpisu biora sie z tresci strony,
    nie z wartosci domyslnych layoutu. meta_description pochodzi z _extract_summary(),
    nie ze statycznego tekstu "<tytul> - wiki projektu Monolynx"."""

    def test_meta_description_tag_uses_provided_summary(self):
        result = _render_blog_page(meta_description="Streszczenie artykulu testowego MON202 XYZ.")

        assert '<meta name="description" content="Streszczenie artykulu testowego MON202 XYZ.">' in result

    def test_og_description_uses_provided_summary(self):
        result = _render_blog_page(meta_description="Streszczenie artykulu testowego MON202 XYZ.")

        assert 'property="og:description" content="Streszczenie artykulu testowego MON202 XYZ."' in result

    def test_twitter_description_uses_provided_summary(self):
        result = _render_blog_page(meta_description="Streszczenie artykulu testowego MON202 XYZ.")

        assert 'name="twitter:description" content="Streszczenie artykulu testowego MON202 XYZ."' in result

    def test_static_layout_default_text_is_gone(self):
        """Regresja: stary staly tekst "<tytul> - wiki projektu Monolynx" / "project wiki"
        nie moze juz byc zrodlem opisu -- meta_description z parametru go zastepuje."""
        result = _render_blog_page(meta_description="Streszczenie artykulu testowego MON202 XYZ.")

        assert "wiki projektu Monolynx" not in result
        assert "Monolynx project wiki" not in result

    def test_og_title_uses_page_title_not_layout_default(self):
        result = _render_blog_page(page_title="Testowy Wpis MON202")

        assert 'property="og:title" content="Testowy Wpis MON202"' in result
        assert "AI-first Project Platform" not in result


@pytest.mark.unit
class TestLandingLayoutDefaultsUnaffected:
    """Regresja: strony NIE nadpisujace blokow og_type/canonical (landing, /features/*)
    maja nadal og:type=website i brak wlasnego rel="canonical"."""

    def test_og_type_defaults_to_website(self):
        result = _render_landing_layout()

        assert 'property="og:type" content="website"' in result
        assert 'property="og:type" content="article"' not in result

    def test_no_canonical_tag_when_not_overridden(self):
        result = _render_landing_layout()

        assert 'rel="canonical"' not in result
