"""Testy serwisu wiki -- generate_slug, render_markdown_html, CRUD stron."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.services.wiki import (
    RESERVED_SLUGS,
    create_wiki_page,
    delete_wiki_page,
    extract_wiki_links,
    generate_slug,
    get_backlinks,
    get_breadcrumbs,
    get_outlinks,
    get_page_content,
    get_page_tree,
    render_markdown_html,
    strip_code_spans,
    sync_backlinks,
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

    @patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_creates_page_with_correct_fields(self, mock_upload, mock_embeddings, mock_sync):
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

    async def test_multiple_roots(self):
        """Wiele stron bez rodzica - kazda jako osobny root."""
        root1 = MagicMock()
        root1.id = uuid.uuid4()
        root1.parent_id = None

        root2 = MagicMock()
        root2.id = uuid.uuid4()
        root2.parent_id = None

        root3 = MagicMock()
        root3.id = uuid.uuid4()
        root3.parent_id = None

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [root1, root2, root3]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_page_tree(uuid.uuid4(), mock_db)

        assert len(result) == 3
        assert all(node["children"] == [] for node in result)

    async def test_deeply_nested_tree(self):
        """Trojpoziomowe zagniezdzone drzewo (root -> child -> grandchild)."""
        root = MagicMock()
        root.id = uuid.uuid4()
        root.parent_id = None

        child = MagicMock()
        child.id = uuid.uuid4()
        child.parent_id = root.id

        grandchild = MagicMock()
        grandchild.id = uuid.uuid4()
        grandchild.parent_id = child.id

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [root, child, grandchild]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_page_tree(uuid.uuid4(), mock_db)

        assert len(result) == 1
        assert result[0]["page"] == root
        assert len(result[0]["children"]) == 1
        child_node = result[0]["children"][0]
        assert child_node["page"] == child
        assert len(child_node["children"]) == 1
        assert child_node["children"][0]["page"] == grandchild


# ---------------------------------------------------------------------------
# NOWE TESTY - extract_wiki_links
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExtractWikiLinks:
    """Testy wyodrebniania referencji wikilink z tresci markdown."""

    def test_simple_wikilink(self):
        """Podstawowy [[slug]] - zwraca ref i anchor = slug."""
        result = extract_wiki_links("Tekst z [[moja-strona]].")
        assert result == {"moja-strona": "moja-strona"}

    def test_wikilink_with_alias(self):
        """[[slug|label]] - Obsidian alias: ref = slug, anchor = label."""
        result = extract_wiki_links("Patrz [[api-reference|Dokumentacja API]].")
        assert result == {"api-reference": "Dokumentacja API"}

    def test_wikilink_uuid(self):
        """[[uuid]] - referencja UUID."""
        uid = str(uuid.uuid4())
        result = extract_wiki_links(f"Link do [[{uid}]].")
        assert uid in result
        assert result[uid] == uid

    def test_multiple_wikilinks(self):
        """Wiele wikilink w tresci."""
        result = extract_wiki_links("[[strona-a]] i [[strona-b]] to dwie strony.")
        assert "strona-a" in result
        assert "strona-b" in result

    def test_duplicate_wikilink_first_wins(self):
        """Duplikat referencji - wygrywa pierwsza."""
        result = extract_wiki_links("[[strona]] i znow [[strona|inny anchor]].")
        assert result["strona"] == "strona"

    def test_markdown_link_internal_slug(self):
        """[tekst](slug) gdzie slug jest wzorcem slug - wyciaga jako ref."""
        result = extract_wiki_links("[Moja strona](moja-strona)")
        assert "moja-strona" in result
        assert result["moja-strona"] == "Moja strona"

    def test_markdown_link_http_ignored(self):
        """[tekst](https://...) - linki HTTP sa ignorowane."""
        result = extract_wiki_links("[Google](https://google.com)")
        assert result == {}

    def test_markdown_link_mailto_ignored(self):
        """[tekst](mailto:...) - linki mailto sa ignorowane."""
        result = extract_wiki_links("[Email](mailto:test@example.com)")
        assert result == {}

    def test_markdown_link_anchor_ignored(self):
        """[tekst](#section) - zakotwiczenia sa ignorowane."""
        result = extract_wiki_links("[Sekcja](#rozdzial-1)")
        assert result == {}

    def test_wikilink_inside_fenced_code_ignored(self):
        """[[slug]] wewnatrz bloku kodu fenced jest ignorowany."""
        content = "```\nPrzyklad [[slug-w-kodzie]]\n```\nTekst po kodzie."
        result = extract_wiki_links(content)
        assert "slug-w-kodzie" not in result

    def test_wikilink_inside_inline_code_ignored(self):
        """[[slug]] wewnatrz backtick code span jest ignorowany."""
        content = "Uzyj `[[inline-kod]]` w tresci."
        result = extract_wiki_links(content)
        assert "inline-kod" not in result

    def test_markdown_link_inside_code_ignored(self):
        """[tekst](slug) w bloku kodu nie jest wyciagany."""
        content = "```\n[link w kodzie](link-w-kodzie)\n```"
        result = extract_wiki_links(content)
        assert "link-w-kodzie" not in result

    def test_wikilink_priority_over_markdown_link(self):
        """Jesli ref wystepuje jako wikilink i markdown link, wikilink wygrywa (kolejnosc)."""
        content = "[[shared-ref]] oraz [tekst](shared-ref)"
        result = extract_wiki_links(content)
        # Wikilink przetwarzany pierwszy - anchor = ref
        assert result["shared-ref"] == "shared-ref"

    def test_empty_content(self):
        """Pusta tresc zwraca pusty slownik."""
        assert extract_wiki_links("") == {}

    def test_no_links(self):
        """Tresc bez linkow zwraca pusty slownik."""
        assert extract_wiki_links("Zwykly tekst bez zadnych linkow.") == {}

    def test_empty_wikilink_brackets_ignored(self):
        """[[]] (pusty wikilink) jest ignorowany."""
        result = extract_wiki_links("Tekst [[]] dalej.")
        assert result == {}

    def test_markdown_link_with_path_segment(self):
        """[tekst](/wiki/pages/slug) - ostatni segment jest kluczem."""
        result = extract_wiki_links("[Strona](/wiki/pages/moj-slug)")
        assert "moj-slug" in result

    def test_wikilink_alias_empty_label_uses_ref(self):
        """[[slug|]] - pusty label po | traktuje ref jako anchor."""
        result = extract_wiki_links("[[strona-z-pustym|]]")
        assert "strona-z-pustym" in result
        # pusty label -> anchor = ref
        assert result["strona-z-pustym"] == "strona-z-pustym"


# ---------------------------------------------------------------------------
# NOWE TESTY - strip_code_spans
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestStripCodeSpans:
    """Testy usuwania blokow kodu z tresci markdown."""

    def test_strips_fenced_code(self):
        """Bloki ``` sa zastepowane spacjami."""
        content = "Przed\n```\nkod\n```\nPo"
        result = strip_code_spans(content)
        assert "```" not in result
        assert "kod" not in result

    def test_strips_inline_code(self):
        """Inline `kod` jest zastepowany spacjami."""
        result = strip_code_spans("Tekst `inline code` dalej.")
        assert "inline code" not in result
        assert "Tekst" in result

    def test_plain_text_unchanged(self):
        """Tekst bez blokow kodu jest niezmieniony."""
        text = "Normalny tekst bez kodu."
        result = strip_code_spans(text)
        assert "Normalny tekst" in result


# ---------------------------------------------------------------------------
# NOWE TESTY - sync_backlinks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSyncBacklinks:
    """Testy synchronizacji backlinkow."""

    async def test_empty_content_deletes_old_backlinks(self):
        """Pusta tresc usuwa stare backlinki strony."""
        page = MagicMock()
        page.id = uuid.uuid4()
        page.project_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_result_execute = MagicMock()
        mock_db.execute = AsyncMock(return_value=mock_result_execute)
        mock_db.commit = AsyncMock()

        await sync_backlinks(page=page, content="", db=mock_db)

        # Powinno wywolac delete (przez execute) i commit
        mock_db.execute.assert_awaited()
        mock_db.commit.assert_awaited_once()

    async def test_resolves_slug_reference(self):
        """Referencja po slug jest rozwiazywana do strony."""
        page = MagicMock()
        page.id = uuid.uuid4()
        page.project_id = uuid.uuid4()

        target_id = uuid.uuid4()
        target_slug = "cel-strony"

        # W galezi non-empty sync_backlinks SELECT (resolve) jest PIERWSZY,
        # DELETE drugi; DELETE nie wola .all(), wiec zwracamy wiersz zawsze.
        async def mock_execute(stmt):
            mock_r = MagicMock()
            row = MagicMock()
            row.id = target_id
            row.slug = target_slug
            mock_r.all = MagicMock(return_value=[row])
            return mock_r

        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        content = f"Tekst z [[{target_slug}]] referencja."
        await sync_backlinks(page=page, content=content, db=mock_db)

        mock_db.add.assert_called_once()
        mock_db.commit.assert_awaited()

    async def test_self_link_is_skipped(self):
        """Backlink do samej siebie nie jest tworzony."""
        page_id = uuid.uuid4()
        page = MagicMock()
        page.id = page_id
        page.project_id = uuid.uuid4()

        async def mock_execute(stmt):
            # SELECT (pierwszy) zwraca strone o tym samym ID - self-link do pominiecia
            mock_r = MagicMock()
            row = MagicMock()
            row.id = page_id  # <- sama strona!
            row.slug = "strona-self"
            mock_r.all = MagicMock(return_value=[row])
            return mock_r

        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        content = "Tekst z [[strona-self]] do samej siebie."
        await sync_backlinks(page=page, content=content, db=mock_db)

        # Self-link powinien byc pominiety - add nie wywolane
        mock_db.add.assert_not_called()

    async def test_uuid_reference_resolved(self):
        """Referencja po UUID jest rozpoznawana i rozwiazywana."""
        page = MagicMock()
        page.id = uuid.uuid4()
        page.project_id = uuid.uuid4()

        target_id = uuid.uuid4()
        uid_str = str(target_id)

        async def mock_execute(stmt):
            mock_r = MagicMock()
            row = MagicMock()
            row.id = target_id
            row.slug = "some-slug"
            mock_r.all = MagicMock(return_value=[row])
            return mock_r

        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        content = f"Link do [[{uid_str}]]."
        await sync_backlinks(page=page, content=content, db=mock_db)

        mock_db.add.assert_called_once()

    async def test_anchor_text_stored(self):
        """anchor_text jest poprawnie przekazywany do WikiBacklink."""
        page = MagicMock()
        page.id = uuid.uuid4()
        page.project_id = uuid.uuid4()

        target_id = uuid.uuid4()
        target_slug = "docs"

        async def mock_execute(stmt):
            mock_r = MagicMock()
            row = MagicMock()
            row.id = target_id
            row.slug = target_slug
            mock_r.all = MagicMock(return_value=[row])
            return mock_r

        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        added_objects: list[MagicMock] = []
        mock_db.add = lambda obj: added_objects.append(obj)
        mock_db.commit = AsyncMock()

        content = "Patrz [[docs|Dokumentacja]]."
        await sync_backlinks(page=page, content=content, db=mock_db)

        assert len(added_objects) == 1
        bl = added_objects[0]
        assert bl.anchor_text == "Dokumentacja"
        assert bl.target_page_id == target_id
        assert bl.source_page_id == page.id

    async def test_no_references_no_new_backlinks(self):
        """Gdy brak referencji, delete jest wywolany ale add nie."""
        page = MagicMock()
        page.id = uuid.uuid4()
        page.project_id = uuid.uuid4()

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.all = MagicMock(return_value=[])
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        await sync_backlinks(page=page, content="Zwykly tekst.", db=mock_db)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()

    async def test_unresolved_reference_ignored(self):
        """Referencja do nieistniejcej strony nie tworzy backlinku."""
        page = MagicMock()
        page.id = uuid.uuid4()
        page.project_id = uuid.uuid4()

        execute_calls: list[str] = []

        async def mock_execute(stmt):
            mock_r = MagicMock()
            if not execute_calls:
                execute_calls.append("delete")
                mock_r.all = MagicMock(return_value=[])
            else:
                execute_calls.append("select")
                # Pusta lista - strona nie istnieje
                mock_r.all = MagicMock(return_value=[])
            return mock_r

        mock_db = AsyncMock()
        mock_db.execute = mock_execute
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()

        content = "Link do [[nieistniejaca-strona]]."
        await sync_backlinks(page=page, content=content, db=mock_db)

        mock_db.add.assert_not_called()


# ---------------------------------------------------------------------------
# NOWE TESTY - get_backlinks / get_outlinks
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetBacklinksOutlinks:
    """Testy pobierania backlinki wychodzacych i przychodzacych."""

    async def test_get_backlinks_empty(self):
        """Brak backlinkow zwraca pusta liste."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_backlinks(uuid.uuid4(), mock_db)

        assert result == []

    async def test_get_backlinks_returns_incoming(self):
        """get_backlinks zwraca backlinki przychodzace (target_page_id == page_id)."""
        mock_bl = MagicMock()
        mock_bl.target_page_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_bl]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_backlinks(mock_bl.target_page_id, mock_db)

        assert len(result) == 1
        assert result[0] == mock_bl

    async def test_get_outlinks_empty(self):
        """Brak outlinki zwraca pusta liste."""
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_outlinks(uuid.uuid4(), mock_db)

        assert result == []

    async def test_get_outlinks_returns_outgoing(self):
        """get_outlinks zwraca linki wychodzace (source_page_id == page_id)."""
        mock_bl = MagicMock()
        mock_bl.source_page_id = uuid.uuid4()

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [mock_bl]
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_outlinks(mock_bl.source_page_id, mock_db)

        assert len(result) == 1
        assert result[0] == mock_bl

    async def test_get_backlinks_multiple(self):
        """Wiele backlinkow przychodzacych."""
        mock_bls = [MagicMock() for _ in range(3)]
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = mock_bls
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await get_backlinks(uuid.uuid4(), mock_db)

        assert len(result) == 3


# ---------------------------------------------------------------------------
# NOWE TESTY - RESERVED_SLUGS + create_wiki_page ValueError
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestReservedSlugs:
    """Testy walidacji zarezerwowanych slugow."""

    def test_reserved_slugs_contains_wiki_index(self):
        """RESERVED_SLUGS zawiera 'wiki-index'."""
        assert "wiki-index" in RESERVED_SLUGS

    def test_reserved_slugs_contains_wiki_log(self):
        """RESERVED_SLUGS zawiera 'wiki-log'."""
        assert "wiki-log" in RESERVED_SLUGS

    def test_reserved_slugs_contains_wiki_schema(self):
        """RESERVED_SLUGS zawiera 'wiki-schema'."""
        assert "wiki-schema" in RESERVED_SLUGS

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_create_page_raises_on_wiki_index_title(self, mock_upload):
        """Tworzenie strony o tytule 'Wiki Index' (slug: wiki-index) rzuca ValueError."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        with pytest.raises(ValueError, match="zarezerwowany"):
            await create_wiki_page(
                project_id=uuid.uuid4(),
                project_slug="proj",
                title="Wiki Index",
                content="content",
                user_id=uuid.uuid4(),
                db=mock_db,
            )

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_create_page_raises_on_wiki_log_title(self, mock_upload):
        """Tworzenie strony o tytule 'Wiki Log' (slug: wiki-log) rzuca ValueError."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        with pytest.raises(ValueError, match="zarezerwowany"):
            await create_wiki_page(
                project_id=uuid.uuid4(),
                project_slug="proj",
                title="Wiki Log",
                content="content",
                user_id=uuid.uuid4(),
                db=mock_db,
            )

    @patch("monolynx.services.wiki.upload_markdown")
    async def test_create_page_raises_on_wiki_schema_title(self, mock_upload):
        """Tworzenie strony o tytule 'Wiki Schema' (slug: wiki-schema) rzuca ValueError."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))

        with pytest.raises(ValueError, match="zarezerwowany"):
            await create_wiki_page(
                project_id=uuid.uuid4(),
                project_slug="proj",
                title="Wiki Schema",
                content="content",
                user_id=uuid.uuid4(),
                db=mock_db,
            )

    @patch("monolynx.services.embeddings.update_page_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.sync_backlinks", new_callable=AsyncMock)
    @patch("monolynx.services.wiki.upload_markdown")
    async def test_create_page_non_reserved_title_succeeds(self, mock_upload, mock_sync, mock_emb):
        """Tworzenie strony o niezarezerwowanym tytule dziala normalnie."""
        mock_upload.return_value = "proj/id.md"
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=MagicMock(scalar_one_or_none=MagicMock(return_value=None)))
        mock_db.add = MagicMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Normalny tytul - nie powinien rzucac
        result = await create_wiki_page(
            project_id=uuid.uuid4(),
            project_slug="proj",
            title="Normalna Strona",
            content="content",
            user_id=uuid.uuid4(),
            db=mock_db,
        )

        assert result.title == "Normalna Strona"
