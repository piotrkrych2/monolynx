"""Testy klienta MinIO -- upload, download, delete plikow wiki i zalacznikow."""

import io
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from monolynx.services.minio_client import (
    delete_object,
    ensure_bucket,
    get_attachment,
    get_markdown,
    upload_attachment,
    upload_markdown,
)


@pytest.mark.unit
class TestEnsureBucket:
    """Testy tworzenia bucketu MinIO."""

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_bucket_exists_no_create(self, mock_get_client, mock_settings):
        """Bucket istnieje -- nie tworzy nowego."""
        mock_settings.MINIO_BUCKET = "test-bucket"
        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = True
        mock_get_client.return_value = mock_client

        ensure_bucket()

        mock_client.bucket_exists.assert_called_once_with("test-bucket")
        mock_client.make_bucket.assert_not_called()

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_bucket_not_exists_creates(self, mock_get_client, mock_settings):
        """Bucket nie istnieje -- tworzy nowy."""
        mock_settings.MINIO_BUCKET = "test-bucket"
        mock_client = MagicMock()
        mock_client.bucket_exists.return_value = False
        mock_get_client.return_value = mock_client

        ensure_bucket()

        mock_client.bucket_exists.assert_called_once_with("test-bucket")
        mock_client.make_bucket.assert_called_once_with("test-bucket")

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_exception_does_not_raise(self, mock_get_client, mock_settings):
        """Wyjatek MinIO nie przerywa aplikacji (loguje blad)."""
        mock_settings.MINIO_BUCKET = "test-bucket"
        mock_client = MagicMock()
        mock_client.bucket_exists.side_effect = Exception("Connection refused")
        mock_get_client.return_value = mock_client

        # Nie powinno rzucic wyjatku
        ensure_bucket()


@pytest.mark.unit
class TestUploadMarkdown:
    """Testy uploadu treści markdown do MinIO."""

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_returns_correct_path(self, mock_get_client, mock_settings):
        """Zwraca poprawna sciezke obiektu."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        page_id = uuid4()

        result = upload_markdown("my-project", page_id, "# Hello")

        assert result == f"my-project/{page_id}.md"

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_calls_put_object_with_correct_params(self, mock_get_client, mock_settings):
        """Wywoluje put_object z poprawnymi parametrami."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        page_id = uuid4()
        content = "# Test Content\n\nSome paragraph."

        upload_markdown("proj", page_id, content)

        mock_client.put_object.assert_called_once()
        call_args = mock_client.put_object.call_args
        assert call_args[0][0] == "wiki-bucket"
        assert call_args[0][1] == f"proj/{page_id}.md"
        # Sprawdz ze data jest BytesIO z UTF-8
        data_arg = call_args[0][2]
        assert isinstance(data_arg, io.BytesIO)
        assert data_arg.read() == content.encode("utf-8")
        assert call_args.kwargs["length"] == len(content.encode("utf-8"))
        assert call_args.kwargs["content_type"] == "text/markdown; charset=utf-8"

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_handles_unicode_content(self, mock_get_client, mock_settings):
        """Poprawnie koduje tresc z polskimi znakami."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        page_id = uuid4()
        content = "# Żółta łódź\n\nPolskie znaki: ąćęłńóśźż"

        result = upload_markdown("proj", page_id, content)

        assert result == f"proj/{page_id}.md"
        call_args = mock_client.put_object.call_args
        assert call_args.kwargs["length"] == len(content.encode("utf-8"))

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_empty_content(self, mock_get_client, mock_settings):
        """Upload pustej tresci."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        page_id = uuid4()

        result = upload_markdown("proj", page_id, "")

        assert result == f"proj/{page_id}.md"
        call_args = mock_client.put_object.call_args
        assert call_args.kwargs["length"] == 0


@pytest.mark.unit
class TestGetMarkdown:
    """Testy pobierania treści markdown z MinIO."""

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_reads_and_decodes_content(self, mock_get_client, mock_settings):
        """Pobiera i dekoduje tresc z MinIO."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b"# Hello World"
        mock_client.get_object.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = get_markdown("proj/page.md")

        assert result == "# Hello World"
        mock_client.get_object.assert_called_once_with("wiki-bucket", "proj/page.md")
        mock_response.close.assert_called_once()
        mock_response.release_conn.assert_called_once()

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_reads_unicode_content(self, mock_get_client, mock_settings):
        """Poprawnie dekoduje polskie znaki."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        content = "Żółta łódź ąćęłńóśźż"
        mock_response = MagicMock()
        mock_response.read.return_value = content.encode("utf-8")
        mock_client.get_object.return_value = mock_response
        mock_get_client.return_value = mock_client

        result = get_markdown("proj/page.md")

        assert result == content

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_closes_response_on_success(self, mock_get_client, mock_settings):
        """Zamyka polaczenie po udanym odczycie."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.read.return_value = b"content"
        mock_client.get_object.return_value = mock_response
        mock_get_client.return_value = mock_client

        get_markdown("path.md")

        mock_response.close.assert_called_once()
        mock_response.release_conn.assert_called_once()

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_closes_response_on_exception(self, mock_get_client, mock_settings):
        """Zamyka polaczenie nawet przy bledzie odczytu."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.read.side_effect = RuntimeError("read error")
        mock_client.get_object.return_value = mock_response
        mock_get_client.return_value = mock_client

        with pytest.raises(RuntimeError, match="read error"):
            get_markdown("path.md")

        mock_response.close.assert_called_once()
        mock_response.release_conn.assert_called_once()


@pytest.mark.unit
class TestDeleteObject:
    """Testy usuwania obiektow z MinIO."""

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_delete_success(self, mock_get_client, mock_settings):
        """Poprawne usuwanie obiektu."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        delete_object("proj/page.md")

        mock_client.remove_object.assert_called_once_with("wiki-bucket", "proj/page.md")

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_exception_does_not_raise(self, mock_get_client, mock_settings):
        """Wyjatek przy usuwaniu jest logowany, nie rzucony."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_client.remove_object.side_effect = Exception("Object not found")
        mock_get_client.return_value = mock_client

        # Nie powinno rzucic wyjatku
        delete_object("proj/nonexistent.md")

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_delete_attachment_path(self, mock_get_client, mock_settings):
        """Usuwanie zalacznika po sciezce."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        delete_object("proj/attachments/abc123.png")

        mock_client.remove_object.assert_called_once_with("wiki-bucket", "proj/attachments/abc123.png")


@pytest.mark.unit
class TestUploadAttachment:
    """Testy uploadu zalacznikow do MinIO."""

    @patch("monolynx.services.minio_client._date_prefix", return_value="2026/03/21")
    @patch("monolynx.services.minio_client.uuid.uuid4")
    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_generates_unique_filename(self, mock_get_client, mock_settings, mock_uuid, _mock_date):
        """Generuje unikalna nazwe pliku z UUID i data w sciezce."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_uuid.return_value = MagicMock(hex="abc123def456")

        result = upload_attachment("proj", "photo.jpg", b"image-data", "image/jpeg")

        assert result == "proj/attachments/2026/03/21/abc123def456.jpg"

    @patch("monolynx.services.minio_client._date_prefix", return_value="2026/03/21")
    @patch("monolynx.services.minio_client.uuid.uuid4")
    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_filename_without_extension(self, mock_get_client, mock_settings, mock_uuid, _mock_date):
        """Plik bez rozszerzenia uzywa 'bin'."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_uuid.return_value = MagicMock(hex="abc123def456")

        result = upload_attachment("proj", "noext", b"data", "application/octet-stream")

        assert result == "proj/attachments/2026/03/21/abc123def456.bin"

    @patch("monolynx.services.minio_client._date_prefix", return_value="2026/03/21")
    @patch("monolynx.services.minio_client.uuid.uuid4")
    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_calls_put_object_with_correct_params(self, mock_get_client, mock_settings, mock_uuid, _mock_date):
        """Wywoluje put_object z poprawnymi parametrami i sciezka YYYY/MM/DD."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_uuid.return_value = MagicMock(hex="abc123")
        data = b"PNG image data here"

        upload_attachment("proj", "photo.png", data, "image/png")

        mock_client.put_object.assert_called_once()
        call_args = mock_client.put_object.call_args
        assert call_args[0][0] == "wiki-bucket"
        assert call_args[0][1] == "proj/attachments/2026/03/21/abc123.png"
        data_arg = call_args[0][2]
        assert isinstance(data_arg, io.BytesIO)
        assert data_arg.read() == data
        assert call_args.kwargs["length"] == len(data)
        assert call_args.kwargs["content_type"] == "image/png"

    @patch("monolynx.services.minio_client._date_prefix", return_value="2026/03/21")
    @patch("monolynx.services.minio_client.uuid.uuid4")
    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_double_extension_uses_last(self, mock_get_client, mock_settings, mock_uuid, _mock_date):
        """Plik z podwojna kropka -- bierze ostatnie rozszerzenie."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()
        mock_get_client.return_value = mock_client
        mock_uuid.return_value = MagicMock(hex="aaa111")

        result = upload_attachment("proj", "archive.tar.gz", b"data", "application/gzip")

        assert result == "proj/attachments/2026/03/21/aaa111.gz"


@pytest.mark.unit
class TestGetAttachment:
    """Testy pobierania zalacznikow z MinIO."""

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_returns_data_and_content_type(self, mock_get_client, mock_settings):
        """Zwraca krotke (dane, content_type)."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()

        mock_stat = MagicMock()
        mock_stat.content_type = "image/png"
        mock_client.stat_object.return_value = mock_stat

        mock_response = MagicMock()
        mock_response.read.return_value = b"PNG-DATA"
        mock_client.get_object.return_value = mock_response

        mock_get_client.return_value = mock_client

        data, content_type = get_attachment("proj/attachments/img.png")

        assert data == b"PNG-DATA"
        assert content_type == "image/png"
        mock_response.close.assert_called_once()
        mock_response.release_conn.assert_called_once()

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_none_content_type_defaults_to_octet_stream(self, mock_get_client, mock_settings):
        """Gdy content_type jest None, zwraca application/octet-stream."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()

        mock_stat = MagicMock()
        mock_stat.content_type = None
        mock_client.stat_object.return_value = mock_stat

        mock_response = MagicMock()
        mock_response.read.return_value = b"binary-data"
        mock_client.get_object.return_value = mock_response

        mock_get_client.return_value = mock_client

        data, content_type = get_attachment("proj/attachments/file.bin")

        assert data == b"binary-data"
        assert content_type == "application/octet-stream"

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_closes_response_after_read(self, mock_get_client, mock_settings):
        """Zamyka polaczenie po odczytaniu danych."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()

        mock_stat = MagicMock()
        mock_stat.content_type = "text/plain"
        mock_client.stat_object.return_value = mock_stat

        mock_response = MagicMock()
        mock_response.read.return_value = b"data"
        mock_client.get_object.return_value = mock_response

        mock_get_client.return_value = mock_client

        get_attachment("path")

        mock_response.close.assert_called_once()
        mock_response.release_conn.assert_called_once()

    @patch("monolynx.services.minio_client.settings")
    @patch("monolynx.services.minio_client.get_minio_client")
    def test_stat_and_get_use_same_bucket_and_path(self, mock_get_client, mock_settings):
        """stat_object i get_object uzywaja tego samego bucketu i sciezki."""
        mock_settings.MINIO_BUCKET = "wiki-bucket"
        mock_client = MagicMock()

        mock_stat = MagicMock()
        mock_stat.content_type = "image/jpeg"
        mock_client.stat_object.return_value = mock_stat

        mock_response = MagicMock()
        mock_response.read.return_value = b"data"
        mock_client.get_object.return_value = mock_response

        mock_get_client.return_value = mock_client

        get_attachment("proj/attachments/photo.jpg")

        mock_client.stat_object.assert_called_once_with("wiki-bucket", "proj/attachments/photo.jpg")
        mock_client.get_object.assert_called_once_with("wiki-bucket", "proj/attachments/photo.jpg")
