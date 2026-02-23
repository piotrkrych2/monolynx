"""Testy serwisu monitoringu -- check_url i _check_url_sync."""

from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from monolynx.services.monitoring import _check_url_sync, check_url


@pytest.mark.unit
class TestCheckUrlSync:
    """Testy synchronicznej funkcji _check_url_sync."""

    @patch("monolynx.services.monitoring._opener")
    def test_success_200(self, mock_opener):
        """Odpowiedz 200 -> is_success=True, status_code=200."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener.open.return_value = mock_resp

        result = _check_url_sync("https://example.com", timeout=10)

        assert result["status_code"] == 200
        assert result["is_success"] is True
        assert result["error_message"] is None
        assert isinstance(result["response_time_ms"], int)
        assert result["response_time_ms"] >= 0

    @patch("monolynx.services.monitoring._opener")
    def test_success_301_is_still_success(self, mock_opener):
        """Odpowiedz 301 -> is_success=True (200 <= code < 400)."""
        mock_resp = MagicMock()
        mock_resp.status = 301
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener.open.return_value = mock_resp

        result = _check_url_sync("https://example.com", timeout=10)

        assert result["status_code"] == 301
        assert result["is_success"] is True
        assert result["error_message"] is None

    @patch("monolynx.services.monitoring._opener")
    def test_http_error_500(self, mock_opener):
        """HTTPError 500 -> is_success=False, status_code=500."""
        mock_opener.open.side_effect = HTTPError(
            url="https://example.com",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),
            fp=None,
        )

        result = _check_url_sync("https://example.com", timeout=10)

        assert result["status_code"] == 500
        assert result["is_success"] is False
        assert result["error_message"] == "Internal Server Error"
        assert isinstance(result["response_time_ms"], int)

    @patch("monolynx.services.monitoring._opener")
    def test_http_error_404(self, mock_opener):
        """HTTPError 404 -> is_success=False, status_code=404."""
        mock_opener.open.side_effect = HTTPError(
            url="https://example.com",
            code=404,
            msg="Not Found",
            hdrs=MagicMock(),
            fp=None,
        )

        result = _check_url_sync("https://example.com", timeout=10)

        assert result["status_code"] == 404
        assert result["is_success"] is False
        assert result["error_message"] == "Not Found"

    @patch("monolynx.services.monitoring._opener")
    def test_url_error_dns_failure(self, mock_opener):
        """URLError (np. DNS) -> status_code=None, is_success=False."""
        mock_opener.open.side_effect = URLError(reason="Name or service not known")

        result = _check_url_sync("https://nonexistent.invalid", timeout=10)

        assert result["status_code"] is None
        assert result["is_success"] is False
        assert "Name or service not known" in result["error_message"]
        assert isinstance(result["response_time_ms"], int)

    @patch("monolynx.services.monitoring._opener")
    def test_url_error_connection_refused(self, mock_opener):
        """URLError (connection refused) -> status_code=None."""
        mock_opener.open.side_effect = URLError(reason="[Errno 111] Connection refused")

        result = _check_url_sync("https://localhost:9999", timeout=10)

        assert result["status_code"] is None
        assert result["is_success"] is False
        assert "Connection refused" in result["error_message"]

    @patch("monolynx.services.monitoring._opener")
    def test_generic_exception(self, mock_opener):
        """Nieoczekiwany wyjatek -> status_code=None, is_success=False."""
        mock_opener.open.side_effect = RuntimeError("unexpected crash")

        result = _check_url_sync("https://example.com", timeout=10)

        assert result["status_code"] is None
        assert result["is_success"] is False
        assert result["error_message"] == "unexpected crash"
        assert isinstance(result["response_time_ms"], int)

    @patch("monolynx.services.monitoring._opener")
    def test_error_message_truncated_to_1024(self, mock_opener):
        """Dlugi komunikat bledu jest obcinany do 1024 znakow."""
        long_reason = "x" * 2000
        mock_opener.open.side_effect = URLError(reason=long_reason)

        result = _check_url_sync("https://example.com", timeout=10)

        assert len(result["error_message"]) == 1024

    @patch("monolynx.services.monitoring._opener")
    def test_http_error_message_truncated_to_1024(self, mock_opener):
        """Dlugi reason HTTPError jest obcinany do 1024 znakow."""
        long_reason = "y" * 2000
        mock_opener.open.side_effect = HTTPError(
            url="https://example.com",
            code=503,
            msg=long_reason,
            hdrs=MagicMock(),
            fp=None,
        )

        result = _check_url_sync("https://example.com", timeout=10)

        assert len(result["error_message"]) == 1024

    @patch("monolynx.services.monitoring._opener")
    def test_result_keys(self, mock_opener):
        """Wynik zawsze zawiera 4 wymagane klucze."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener.open.return_value = mock_resp

        result = _check_url_sync("https://example.com", timeout=5)

        assert set(result.keys()) == {
            "status_code",
            "response_time_ms",
            "is_success",
            "error_message",
        }

    @patch("monolynx.services.monitoring._opener")
    def test_user_agent_header_set(self, mock_opener):
        """Request zawiera naglowek User-Agent: Monolynx-Monitor/1.0."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_opener.open.return_value = mock_resp

        _check_url_sync("https://example.com", timeout=10)

        call_args = mock_opener.open.call_args
        req = call_args[0][0]
        assert req.get_header("User-agent") == "Monolynx-Monitor/1.0"


@pytest.mark.unit
class TestCheckUrlAsync:
    """Testy asynchronicznego wrappera check_url."""

    @patch("monolynx.services.monitoring._check_url_sync")
    async def test_async_wrapper_calls_sync(self, mock_sync):
        """check_url deleguje do _check_url_sync przez executor."""
        mock_sync.return_value = {
            "status_code": 200,
            "response_time_ms": 42,
            "is_success": True,
            "error_message": None,
        }

        result = await check_url("https://example.com", timeout=5)

        assert result["status_code"] == 200
        assert result["is_success"] is True
        assert result["response_time_ms"] == 42
        assert result["error_message"] is None

    @patch("monolynx.services.monitoring._check_url_sync")
    async def test_async_wrapper_passes_default_timeout(self, mock_sync):
        """check_url uzywa domyslnego timeout=10."""
        mock_sync.return_value = {
            "status_code": 200,
            "response_time_ms": 10,
            "is_success": True,
            "error_message": None,
        }

        await check_url("https://example.com")

        mock_sync.assert_called_once_with("https://example.com", 10)

    @patch("monolynx.services.monitoring._check_url_sync")
    async def test_async_wrapper_passes_custom_timeout(self, mock_sync):
        """check_url przekazuje niestandardowy timeout."""
        mock_sync.return_value = {
            "status_code": 200,
            "response_time_ms": 10,
            "is_success": True,
            "error_message": None,
        }

        await check_url("https://example.com", timeout=30)

        mock_sync.assert_called_once_with("https://example.com", 30)

    @patch("monolynx.services.monitoring._check_url_sync")
    async def test_async_wrapper_returns_error_result(self, mock_sync):
        """check_url zwraca wynik bledu z _check_url_sync."""
        mock_sync.return_value = {
            "status_code": None,
            "response_time_ms": 5,
            "is_success": False,
            "error_message": "Connection refused",
        }

        result = await check_url("https://localhost:9999")

        assert result["is_success"] is False
        assert result["error_message"] == "Connection refused"
