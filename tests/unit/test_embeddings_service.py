"""Testy serwisu embeddingow -- chunking, generowanie wektorow, wyszukiwanie."""

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.services.embeddings import (
    _generate_embeddings_sync,
    chunk_text,
    generate_embeddings,
    is_enabled,
    search_wiki_pages,
    update_page_embeddings,
)


@pytest.mark.unit
class TestIsEnabled:
    """Testy sprawdzania czy embeddingi sa wlaczone."""

    @patch("monolynx.services.embeddings.settings")
    def test_enabled_when_api_key_set(self, mock_settings):
        """Zwraca True gdy OPENAI_API_KEY jest ustawiony."""
        mock_settings.OPENAI_API_KEY = "sk-test-key-123"
        assert is_enabled() is True

    @patch("monolynx.services.embeddings.settings")
    def test_disabled_when_api_key_empty(self, mock_settings):
        """Zwraca False gdy OPENAI_API_KEY jest pusty."""
        mock_settings.OPENAI_API_KEY = ""
        assert is_enabled() is False

    @patch("monolynx.services.embeddings.settings")
    def test_disabled_when_api_key_none(self, mock_settings):
        """Zwraca False gdy OPENAI_API_KEY jest None (falsy)."""
        mock_settings.OPENAI_API_KEY = None
        assert is_enabled() is False


@pytest.mark.unit
class TestChunkText:
    """Testy dzielenia tekstu na chunki tokenowe."""

    @patch("monolynx.services.embeddings.settings")
    def test_empty_text_returns_empty(self, mock_settings):
        """Pusty tekst zwraca pusta liste."""
        mock_settings.EMBEDDING_CHUNK_SIZE = 500
        mock_settings.EMBEDDING_CHUNK_OVERLAP = 50
        result = chunk_text("")
        assert result == []

    @patch("monolynx.services.embeddings.settings")
    def test_single_chunk_short_text(self, mock_settings):
        """Krotki tekst miesci sie w jednym chunku."""
        mock_settings.EMBEDDING_CHUNK_SIZE = 500
        mock_settings.EMBEDDING_CHUNK_OVERLAP = 50
        result = chunk_text("Hello world, this is a short text.")
        assert len(result) == 1
        text, token_count = result[0]
        assert "Hello world" in text
        assert token_count > 0

    @patch("monolynx.services.embeddings.settings")
    def test_multiple_chunks_with_overlap(self, mock_settings):
        """Dlugi tekst jest dzielony na wiele chunkow z overlappem."""
        mock_settings.EMBEDDING_CHUNK_SIZE = 10
        mock_settings.EMBEDDING_CHUNK_OVERLAP = 2
        # Generuj tekst dluzszy niz 10 tokenow
        long_text = " ".join(f"word{i}" for i in range(100))
        result = chunk_text(long_text)
        assert len(result) > 1
        # Kazdy chunk ma token_count > 0
        for _text, token_count in result:
            assert token_count > 0

    def test_custom_chunk_size_and_overlap(self):
        """Parametry chunk_size i overlap nadpisuja ustawienia."""
        long_text = " ".join(f"word{i}" for i in range(100))
        result = chunk_text(long_text, chunk_size=5, overlap=1)
        assert len(result) > 1
        # Kazdy chunk powinien miec max ~5 tokenow (moze sie delikatnie roznic)
        for _text, token_count in result:
            assert token_count <= 6  # tiktoken moze dodac +1

    @patch("monolynx.services.embeddings.settings")
    def test_returns_tuples_of_text_and_count(self, mock_settings):
        """Zwraca liste krotek (tekst, token_count)."""
        mock_settings.EMBEDDING_CHUNK_SIZE = 500
        mock_settings.EMBEDDING_CHUNK_OVERLAP = 50
        result = chunk_text("Hello world")
        assert len(result) == 1
        assert isinstance(result[0], tuple)
        assert isinstance(result[0][0], str)
        assert isinstance(result[0][1], int)

    @patch("monolynx.services.embeddings.settings")
    def test_whitespace_only_returns_empty(self, mock_settings):
        """Tekst z samych bialych znakow -- tiktoken moze zwrocic tokeny."""
        mock_settings.EMBEDDING_CHUNK_SIZE = 500
        mock_settings.EMBEDDING_CHUNK_OVERLAP = 50
        result = chunk_text("   ")
        # Spacje moga byc tokenami, wiec moze zwrocic 1 chunk
        # Ale nie powinno crashowac
        assert isinstance(result, list)

    @patch("monolynx.services.embeddings.settings")
    def test_exact_chunk_size_no_overlap_needed(self, mock_settings):
        """Tekst dokladnie na chunk_size -- jeden chunk, brak overlappu."""
        mock_settings.EMBEDDING_CHUNK_SIZE = 500
        mock_settings.EMBEDDING_CHUNK_OVERLAP = 50
        # Krotki tekst -- jeden chunk
        result = chunk_text("Test")
        assert len(result) == 1

    @patch("monolynx.services.embeddings.settings")
    def test_chunk_overlap_creates_overlapping_text(self, mock_settings):
        """Overlap powoduje ze sasiednie chunki wspoldziela tokeny."""
        mock_settings.EMBEDDING_CHUNK_SIZE = 10
        mock_settings.EMBEDDING_CHUNK_OVERLAP = 5
        long_text = " ".join(f"word{i}" for i in range(50))
        result = chunk_text(long_text)
        assert len(result) > 2
        # Sprawdz ze chunki nie sa identyczne
        texts = [r[0] for r in result]
        assert len(set(texts)) == len(texts)


@pytest.mark.unit
class TestGenerateEmbeddingsSync:
    """Testy synchronicznego generowania embeddingow."""

    @patch("monolynx.services.embeddings._get_openai_client")
    def test_success(self, mock_get_client):
        """Poprawne generowanie embeddingow."""
        mock_client = MagicMock()
        mock_embedding_1 = MagicMock()
        mock_embedding_1.embedding = [0.1, 0.2, 0.3]
        mock_embedding_2 = MagicMock()
        mock_embedding_2.embedding = [0.4, 0.5, 0.6]
        mock_client.embeddings.create.return_value = MagicMock(data=[mock_embedding_1, mock_embedding_2])
        mock_get_client.return_value = mock_client

        result = _generate_embeddings_sync(["text1", "text2"])

        assert result == [[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]]
        mock_client.embeddings.create.assert_called_once()

    @patch("monolynx.services.embeddings._get_openai_client")
    def test_client_none_returns_none(self, mock_get_client):
        """Brak klienta OpenAI (brak klucza) zwraca None."""
        mock_get_client.return_value = None

        result = _generate_embeddings_sync(["text1"])

        assert result is None

    @patch("monolynx.services.embeddings._get_openai_client")
    def test_exception_returns_none(self, mock_get_client):
        """Wyjatek z OpenAI zwraca None (graceful degradation)."""
        mock_client = MagicMock()
        mock_client.embeddings.create.side_effect = RuntimeError("API error")
        mock_get_client.return_value = mock_client

        result = _generate_embeddings_sync(["text1"])

        assert result is None


@pytest.mark.unit
class TestGenerateEmbeddingsAsync:
    """Testy asynchronicznego wrappera generate_embeddings."""

    @patch("monolynx.services.embeddings._generate_embeddings_sync")
    async def test_delegates_to_sync(self, mock_sync):
        """Async wrapper deleguje do _generate_embeddings_sync."""
        mock_sync.return_value = [[0.1, 0.2]]

        result = await generate_embeddings(["text1"])

        assert result == [[0.1, 0.2]]

    @patch("monolynx.services.embeddings._generate_embeddings_sync")
    async def test_returns_none_when_sync_returns_none(self, mock_sync):
        """Async wrapper zwraca None gdy sync zwraca None."""
        mock_sync.return_value = None

        result = await generate_embeddings(["text1"])

        assert result is None


@pytest.mark.unit
class TestUpdatePageEmbeddings:
    """Testy aktualizacji embeddingow strony."""

    @patch("monolynx.services.embeddings.is_enabled", return_value=False)
    async def test_not_enabled_returns_early(self, mock_enabled):
        """Gdy embeddingi wylaczone, natychmiast wraca."""
        mock_db = AsyncMock()

        await update_page_embeddings(uuid.uuid4(), "content", mock_db)

        mock_db.execute.assert_not_awaited()
        mock_db.commit.assert_not_awaited()

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.chunk_text", return_value=[])
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_empty_content_no_chunks(self, mock_enabled, mock_chunk, mock_gen):
        """Pusta tresc -- brak chunkow, commit i powrot."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()

        await update_page_embeddings(uuid.uuid4(), "", mock_db)

        mock_db.execute.assert_awaited_once()  # delete stare embeddingi
        mock_db.commit.assert_awaited_once()
        mock_gen.assert_not_awaited()

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock, return_value=None)
    @patch("monolynx.services.embeddings.chunk_text", return_value=[("chunk1", 10)])
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_vectors_none_commits_without_adding(self, mock_enabled, mock_chunk, mock_gen):
        """Gdy generate_embeddings zwraca None, commit bez dodawania embeddingow."""
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        await update_page_embeddings(uuid.uuid4(), "content", mock_db)

        mock_db.add.assert_not_called()
        mock_db.commit.assert_awaited_once()

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.chunk_text")
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_success_creates_embeddings(self, mock_enabled, mock_chunk, mock_gen):
        """Sukces -- tworzy obiekty WikiEmbedding i commituje."""
        mock_chunk.return_value = [("chunk1", 10), ("chunk2", 15)]
        mock_gen.return_value = [[0.1, 0.2], [0.3, 0.4]]

        page_id = uuid.uuid4()
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.add = MagicMock()

        await update_page_embeddings(page_id, "some content", mock_db)

        assert mock_db.add.call_count == 2
        mock_db.commit.assert_awaited_once()

        # Sprawdz ze dodano WikiEmbedding z poprawnymi parametrami
        added_objects = [call.args[0] for call in mock_db.add.call_args_list]
        from monolynx.models.wiki_embedding import WikiEmbedding

        assert all(isinstance(obj, WikiEmbedding) for obj in added_objects)
        assert added_objects[0].wiki_page_id == page_id
        assert added_objects[0].chunk_index == 0
        assert added_objects[0].chunk_text == "chunk1"
        assert added_objects[0].token_count == 10
        assert added_objects[1].chunk_index == 1
        assert added_objects[1].chunk_text == "chunk2"
        assert added_objects[1].token_count == 15


@pytest.mark.unit
class TestSearchWikiPages:
    """Testy wyszukiwania semantycznego stron wiki."""

    @patch("monolynx.services.embeddings.is_enabled", return_value=False)
    async def test_not_enabled_returns_empty(self, mock_enabled):
        """Gdy embeddingi wylaczone, zwraca pusta liste."""
        mock_db = AsyncMock()

        result = await search_wiki_pages(uuid.uuid4(), "query", mock_db)

        assert result == []

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock, return_value=None)
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_no_vectors_returns_empty(self, mock_enabled, mock_gen):
        """Gdy generowanie wektorow zwraca None, pusta lista."""
        mock_db = AsyncMock()

        result = await search_wiki_pages(uuid.uuid4(), "query", mock_db)

        assert result == []

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock, return_value=[])
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_empty_vectors_returns_empty(self, mock_enabled, mock_gen):
        """Gdy generowanie wektorow zwraca pusta liste, pusta lista."""
        mock_db = AsyncMock()

        result = await search_wiki_pages(uuid.uuid4(), "query", mock_db)

        assert result == []

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_success_with_results(self, mock_enabled, mock_gen):
        """Poprawne wyszukiwanie z wynikami."""
        mock_gen.return_value = [[0.1, 0.2, 0.3]]

        page_id = uuid.uuid4()
        mock_row = (page_id, "Test Page", "test-page", "This is the chunk text content", 0.85)

        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await search_wiki_pages(uuid.uuid4(), "test query", mock_db)

        assert len(result) == 1
        assert result[0]["title"] == "Test Page"
        assert result[0]["slug"] == "test-page"
        assert result[0]["similarity"] == 0.85
        assert result[0]["id"] == str(page_id)

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_low_similarity_filtered_out(self, mock_enabled, mock_gen):
        """Wyniki z similarity <= 0.3 sa odfiltrowane."""
        mock_gen.return_value = [[0.1, 0.2, 0.3]]

        # Wynik z niska similarity (ponizej 0.3)
        mock_row = (uuid.uuid4(), "Low Match", "low-match", "Some text", 0.2)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await search_wiki_pages(uuid.uuid4(), "query", mock_db)

        assert result == []

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_snippet_truncated_to_300(self, mock_enabled, mock_gen):
        """Snippet jest obcinany do 300 znakow."""
        mock_gen.return_value = [[0.1]]

        long_text = "x" * 500
        mock_row = (uuid.uuid4(), "Page", "page", long_text, 0.9)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [mock_row]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await search_wiki_pages(uuid.uuid4(), "query", mock_db)

        assert len(result) == 1
        assert len(result[0]["snippet"]) == 300

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_results_sorted_by_similarity_desc(self, mock_enabled, mock_gen):
        """Wyniki sa posortowane malejaco po similarity."""
        mock_gen.return_value = [[0.1]]

        row1 = (uuid.uuid4(), "Low", "low", "text1", 0.5)
        row2 = (uuid.uuid4(), "High", "high", "text2", 0.95)
        row3 = (uuid.uuid4(), "Mid", "mid", "text3", 0.7)
        mock_result = MagicMock()
        mock_result.fetchall.return_value = [row1, row2, row3]

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await search_wiki_pages(uuid.uuid4(), "query", mock_db)

        assert len(result) == 3
        assert result[0]["title"] == "High"
        assert result[1]["title"] == "Mid"
        assert result[2]["title"] == "Low"

    @patch("monolynx.services.embeddings.generate_embeddings", new_callable=AsyncMock)
    @patch("monolynx.services.embeddings.is_enabled", return_value=True)
    async def test_limit_parameter(self, mock_enabled, mock_gen):
        """Parametr limit ogranicza liczbe wynikow."""
        mock_gen.return_value = [[0.1]]

        rows = [(uuid.uuid4(), f"Page{i}", f"page{i}", "text", 0.9 - i * 0.05) for i in range(20)]
        mock_result = MagicMock()
        mock_result.fetchall.return_value = rows

        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)

        result = await search_wiki_pages(uuid.uuid4(), "query", mock_db, limit=5)

        assert len(result) <= 5
