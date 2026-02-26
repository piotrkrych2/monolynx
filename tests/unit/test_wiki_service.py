"""Testy serwisu wiki -- generate_slug, render_markdown_html, CRUD stron."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.services.wiki import (
    create_wiki_page,
    delete_wiki_page,
    generate_slug,
    get_breadcrumbs,
    get_page_content,
    get_page_tree,
    render_markdown_html,
    update_wiki_page,
)


@pytest.mark.unit
class TestGenerateSlug:
    """Testy generowania sluga z tytulu strony."""

    def test_simple_title(self):
        """Prosty tytul bez polskich znakow."""
        assert generate_slug("Hello World") == "hello-world"

    def test_polish_characters(self):
        """Polskie znaki sa zamieniane na ASCII."""
        assert generate_slug("Ząbkowice Śląskie") == "zabkowice-slaskie"

    def test_all_polish_chars(self):
        """Wszystkie polskie znaki diakrytyczne."""
        result = generate_slug("ąćęłńóśźż")
        assert result == "acelnoszz"

    def test_uppercase_polish_chars(self):
        """Wielkie litery polskie (lowercase first, then replace)."""
        result = generate_slug("ĄĆĘŁŃÓŚŹŻ")
        assert result == "acelnoszz"

    def test_special_characters_removed(self):
        """Znaki specjalne sa usuwane."""
        assert generate_slug("Hello! @World# $2024") == "hello-world-2024"

    def test_multiple_spaces_collapsed(self):
        """Wiele spacji zamieniane na jeden myslnik."""
        assert generate_slug("Hello    World") == "hello-world"

    def test_multiple_dashes_collapsed(self):
        """Wiele myslnikow zamieniane na jeden."""
        assert generate_slug("hello---world") == "hello-world"

    def test_leading_trailing_dashes_stripped(self):
        """Myslniki na poczatku i koncu sa usuwane."""
        assert generate_slug("--hello--") == "hello"

    def test_empty_string_returns_strona(self):
        """Pusty string zwraca 'strona'."""
        assert generate_slug("") == "strona"

    def test_only_special_chars_returns_strona(self):
        """String tylko ze znakow specjalnych zwraca 'strona'."""
        assert generate_slug("!!!@@@###") == "strona"

    def test_underscores_removed(self):
        """Podkreslenia sa usuwane (regex [^a-z0-9\\s-] przed [\\s_]+)."""
        assert generate_slug("hello_world") == "helloworld"

    def test_whitespace_stripped(self):
        """Biale znaki na poczatku i koncu sa obcinane."""
        assert generate_slug("  Hello World  ") == "hello-world"

    def test_numbers_preserved(self):
        """Cyfry sa zachowywane."""
        assert generate_slug("Sprint 42") == "sprint-42"

    def test_mixed_polish_and_special(self):
        """Mix polskich znakow i znakow specjalnych."""
        assert generate_slug("Żółta łódź! (2024)") == "zolta-lodz-2024"


@pytest.mark.unit
class TestRenderMarkdownHtml:
    """Testy renderowania markdown do HTML."""

    def test_header(self):
        """Naglowek h1 (toc extension dodaje id)."""
        result = render_markdown_html("# Hello")
        assert "<h1" in result
        assert "Hello</h1>" in result

    def test_header_h2(self):
        """Naglowek h2 (toc extension dodaje id)."""
        result = render_markdown_html("## Subtitle")
        assert "<h2" in result
        assert "Subtitle</h2>" in result

    def test_bold_text(self):
        """Pogrubiony tekst."""
        result = render_markdown_html("**bold text**")
        assert "<strong>bold text</strong>" in result

    def test_italic_text(self):
        """Tekst kursywa."""
        result = render_markdown_html("*italic*")
        assert "<em>italic</em>" in result

    def test_link(self):
        """Link w markdown."""
        result = render_markdown_html("[Example](https://example.com)")
        assert 'href="https://example.com"' in result
        assert "Example" in result

    def test_fenced_code_block(self):
        """Blok kodu fenced."""
        md = "```python\nprint('hello')\n```"
        result = render_markdown_html(md)
        assert "<code" in result
        assert "print" in result

    def test_table(self):
        """Tabela markdown."""
        md = "| Col1 | Col2 |\n|------|------|\n| A    | B    |"
        result = render_markdown_html(md)
        assert "<table>" in result
        assert "<th>" in result
        assert "Col1" in result
        assert "<td>" in result
        assert "A" in result

    def test_unordered_list(self):
        """Lista nieuporządkowana."""
        result = render_markdown_html("- item 1\n- item 2")
        assert "<li>" in result
        assert "item 1" in result

    def test_empty_string(self):
        """Pusty string zwraca pusty string."""
        result = render_markdown_html("")
        assert result == ""

    def test_plain_text(self):
        """Zwykly tekst bez formatowania."""
        result = render_markdown_html("Hello world")
        assert "Hello world" in result

    def test_inline_code(self):
        """Kod inline."""
        result = render_markdown_html("`code here`")
        assert "<code>code here</code>" in result


@pytest.mark.unit
class TestGetPageContent:
    """Testy pobierania tresci strony z MinIO."""

    @patch("monolynx.services.wiki.get_markdown")
    def test_returns_content_from_minio(self, mock_get_markdown):
        """Pobiera tresc z MinIO po minio_path."""
        mock_get_markdown.return_value = "# Hello World"
        page = MagicMock()
        page.minio_path = "project-slug/page-id.md"

        result = get_page_content(page)

        assert result == "# Hello World"
        mock_get_markdown.assert_called_once_with("project-slug/page-id.md")

    @patch("monolynx.services.wiki.get_markdown")
    def test_returns_empty_content(self, mock_get_markdown):
        """Pusty plik markdown."""
        mock_get_markdown.return_value = ""
        page = MagicMock()
        page.minio_path = "slug/id.md"

        result = get_page_content(page)

        assert result == ""


@pytest.mark.unit
class TestCreateWikiPage:
    """Testy tworzenia strony wiki."""

    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_creates_page_with_correct_fields(self, mock_upload, mock_embeddings):
        """Tworzy strone z poprawnymi polami."""
        mock_upload.return_value = "test-project/some-id.md"

        project_id = uuid.uuid4()
        user_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await create_wiki_page(
            project_id=project_id,
            project_slug="test-project",
            title="Testowa Strona",
            content="# Hello",
            user_id=user_id,
            db=mock_db,
        )

        assert result.title == "Testowa Strona"
        assert result.project_id == project_id
        assert result.created_by_id == user_id
        assert result.last_edited_by_id == user_id
        assert result.minio_path == "test-project/some-id.md"
        assert result.is_ai_touched is False
        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited_once()
        mock_db.refresh.assert_awaited_once()
        mock_upload.assert_called_once()

    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_creates_page_with_parent(self, mock_upload, mock_embeddings):
        """Tworzy strone z rodzicem."""
        mock_upload.return_value = "proj/id.md"
        parent_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await create_wiki_page(
            project_id=uuid.uuid4(),
            project_slug="proj",
            title="Child Page",
            content="Content",
            user_id=uuid.uuid4(),
            parent_id=parent_id,
            db=mock_db,
        )

        assert result.parent_id == parent_id

    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_creates_page_with_ai_flag(self, mock_upload, mock_embeddings):
        """Tworzy strone z flaga is_ai."""
        mock_upload.return_value = "proj/id.md"

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await create_wiki_page(
            project_id=uuid.uuid4(),
            project_slug="proj",
            title="AI Page",
            content="Generated content",
            user_id=uuid.uuid4(),
            is_ai=True,
            db=mock_db,
        )

        assert result.is_ai_touched is True

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_embedding_failure_does_not_crash(self, mock_upload):
        """Blad embeddingow nie przerywa tworzenia strony."""
        mock_upload.return_value = "proj/id.md"

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        with patch(
            "monolynx.services.embeddings.update_page_embeddings",
            new_callable=AsyncMock,
            side_effect=RuntimeError("OpenAI unavailable"),
        ):
            result = await create_wiki_page(
                project_id=uuid.uuid4(),
                project_slug="proj",
                title="Page",
                content="Content",
                user_id=uuid.uuid4(),
                db=mock_db,
            )

        # Strona zostala utworzona pomimo bledu embeddingow
        assert result.title == "Page"
        mock_db.add.assert_called_once()

    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_creates_page_with_position(self, mock_upload, mock_embeddings):
        """Tworzy strone z pozycja."""
        mock_upload.return_value = "proj/id.md"

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await create_wiki_page(
            project_id=uuid.uuid4(),
            project_slug="proj",
            title="Positioned",
            content="Content",
            user_id=uuid.uuid4(),
            position=5,
            db=mock_db,
        )

        assert result.position == 5


@pytest.mark.unit
class TestUpdateWikiPage:
    """Testy aktualizacji strony wiki."""

    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_update_title(self, mock_upload, mock_embeddings):
        """Aktualizuje tytul strony i slug."""
        page = MagicMock()
        page.title = "Old Title"
        page.slug = "old-title"
        page.project_id = uuid.uuid4()
        page.id = uuid.uuid4()
        page.minio_path = "proj/page.md"

        user_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        result = await update_wiki_page(
            page=page,
            project_slug="proj",
            title="New Title",
            user_id=user_id,
            db=mock_db,
        )

        assert result.title == "New Title"
        assert result.slug == "new-title"
        assert result.last_edited_by_id == user_id

    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_update_content(self, mock_upload, mock_embeddings):
        """Aktualizuje tresc strony w MinIO i embeddingi."""
        page = MagicMock()
        page.title = "Title"
        page.id = uuid.uuid4()
        page.project_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await update_wiki_page(
            page=page,
            project_slug="proj",
            content="# Updated content",
            user_id=uuid.uuid4(),
            db=mock_db,
        )

        mock_upload.assert_called_once_with("proj", page.id, "# Updated content")
        mock_embeddings.assert_awaited_once_with(page.id, "# Updated content", mock_db)

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_update_position(self, mock_upload):
        """Aktualizuje pozycje strony."""
        page = MagicMock()
        page.title = "Title"
        page.id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await update_wiki_page(
            page=page,
            project_slug="proj",
            position=10,
            user_id=uuid.uuid4(),
            db=mock_db,
        )

        assert page.position == 10
        mock_upload.assert_not_called()

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_update_sets_ai_flag(self, mock_upload):
        """Ustawia flage is_ai_touched gdy is_ai=True."""
        page = MagicMock()
        page.title = "Title"
        page.id = uuid.uuid4()
        page.is_ai_touched = False

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await update_wiki_page(
            page=page,
            project_slug="proj",
            user_id=uuid.uuid4(),
            is_ai=True,
            db=mock_db,
        )

        assert page.is_ai_touched is True

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_update_without_changes(self, mock_upload):
        """Aktualizacja bez zmian -- tylko last_edited_by_id."""
        page = MagicMock()
        page.title = "Title"
        page.id = uuid.uuid4()

        user_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await update_wiki_page(
            page=page,
            project_slug="proj",
            user_id=user_id,
            db=mock_db,
        )

        assert page.last_edited_by_id == user_id
        mock_upload.assert_not_called()
        mock_db.commit.assert_awaited_once()

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_same_title_no_slug_update(self, mock_upload):
        """Jesli tytul nie zmienil sie (po strip), slug nie jest aktualizowany."""
        page = MagicMock()
        page.title = "Title"
        page.slug = "title"
        page.id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        await update_wiki_page(
            page=page,
            project_slug="proj",
            title="Title",
            user_id=uuid.uuid4(),
            db=mock_db,
        )

        # Slug nie powinien byc zmieniony
        assert page.slug == "title"


@pytest.mark.unit
class TestDeleteWikiPage:
    """Testy usuwania strony wiki."""

    @patch("monolynx.services.wiki._collect_descendants", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.delete_object")
    async def test_delete_page_without_children(self, mock_delete_obj, mock_collect):
        """Usuwanie strony bez potomkow."""
        mock_collect.return_value = []
        page = MagicMock()
        page.id = uuid.uuid4()
        page.minio_path = "proj/page.md"

        mock_db = AsyncMock()
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        await delete_wiki_page(page, mock_db)

        mock_delete_obj.assert_called_once_with("proj/page.md")
        mock_db.delete.assert_awaited_once_with(page)
        mock_db.commit.assert_awaited_once()

    @patch("monolynx.services.wiki._collect_descendants", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.delete_object")
    async def test_delete_page_with_children(self, mock_delete_obj, mock_collect):
        """Usuwanie strony z potomkami -- usuwa pliki MinIO wszystkich."""
        child1 = MagicMock()
        child1.minio_path = "proj/child1.md"
        child2 = MagicMock()
        child2.minio_path = "proj/child2.md"
        mock_collect.return_value = [child1, child2]

        page = MagicMock()
        page.id = uuid.uuid4()
        page.minio_path = "proj/parent.md"

        mock_db = AsyncMock()
        mock_db.delete = AsyncMock()
        mock_db.commit = AsyncMock()

        await delete_wiki_page(page, mock_db)

        assert mock_delete_obj.call_count == 3
        mock_delete_obj.assert_any_call("proj/parent.md")
        mock_delete_obj.assert_any_call("proj/child1.md")
        mock_delete_obj.assert_any_call("proj/child2.md")
        mock_db.delete.assert_awaited_once_with(page)


@pytest.mark.unit
class TestGetBreadcrumbs:
    """Testy budowania breadcrumbs."""

    async def test_root_page_returns_single_item(self):
        """Strona bez rodzica zwraca liste z jednym elementem."""
        page = MagicMock()
        page.parent_id = None

        mock_db = AsyncMock()

        result = await get_breadcrumbs(page, mock_db)

        assert result == [page]
        mock_db.execute.assert_not_awaited()

    async def test_page_with_parent(self):
        """Strona z rodzicem zwraca [parent, page]."""
        parent = MagicMock()
        parent.id = uuid.uuid4()
        parent.parent_id = None

        page = MagicMock()
        page.parent_id = parent.id

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = parent
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_breadcrumbs(page, mock_db)

        assert result == [parent, page]

    async def test_page_with_grandparent(self):
        """Strona z dziadkiem zwraca [grandparent, parent, page]."""
        grandparent = MagicMock()
        grandparent.id = uuid.uuid4()
        grandparent.parent_id = None

        parent = MagicMock()
        parent.id = uuid.uuid4()
        parent.parent_id = grandparent.id

        page = MagicMock()
        page.parent_id = parent.id

        mock_db = AsyncMock()
        call_count = 0
        results = [parent, grandparent]

        async def mock_execute(stmt):
            nonlocal call_count
            mock_result = MagicMock()
            mock_result.scalar_one_or_none.return_value = results[call_count]
            call_count += 1
            return mock_result

        mock_db.execute = mock_execute

        result = await get_breadcrumbs(page, mock_db)

        assert result == [grandparent, parent, page]

    async def test_broken_parent_chain_stops(self):
        """Jesli rodzic nie istnieje (None), breadcrumbs sie zatrzymuja."""
        page = MagicMock()
        page.parent_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = None
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_breadcrumbs(page, mock_db)

        assert result == [page]


@pytest.mark.unit
class TestGetPageTree:
    """Testy budowania drzewa stron."""

    async def test_empty_project_returns_empty_list(self):
        """Projekt bez stron zwraca pusta liste."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_page_tree(uuid.uuid4(), mock_db)

        assert result == []

    async def test_flat_pages_returned_as_tree(self):
        """Strony bez hierarchii zwracane jako flat lista drzewa."""
        page1 = MagicMock()
        page1.id = uuid.uuid4()
        page1.parent_id = None
        page1.title = "Page 1"
        page1.position = 0

        page2 = MagicMock()
        page2.id = uuid.uuid4()
        page2.parent_id = None
        page2.title = "Page 2"
        page2.position = 1

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [page1, page2]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_page_tree(uuid.uuid4(), mock_db)

        assert len(result) == 2
        assert result[0]["page"] == page1
        assert result[0]["children"] == []
        assert result[1]["page"] == page2

    async def test_nested_pages_tree(self):
        """Strony z hierarchia tworza zagniezdzone drzewo."""
        parent = MagicMock()
        parent.id = uuid.uuid4()
        parent.parent_id = None

        child = MagicMock()
        child.id = uuid.uuid4()
        child.parent_id = parent.id

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [parent, child]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_page_tree(uuid.uuid4(), mock_db)

        assert len(result) == 1
        assert result[0]["page"] == parent
        assert len(result[0]["children"]) == 1
        assert result[0]["children"][0]["page"] == child
