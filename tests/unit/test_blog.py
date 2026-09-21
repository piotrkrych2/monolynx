"""Testy jednostkowe MON-201 -- sanityzacja markdown (nh3) dla publicznego /blog/{slug}
i odczyt publicznych stron wiki (get_public_page_by_slug).

Zanim implementacja powstanie, import ponizej ma sie NIE powiesc (render_markdown_public
i get_public_page_by_slug jeszcze nie istnieja w monolynx.services.wiki) -- to jest
zamierzone, TDD: testy sa specyfikacja dla backend-developera.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

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
