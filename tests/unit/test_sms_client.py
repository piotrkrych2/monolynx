"""Testy serwisu SMS -- _send_sms_sync, send_sms (lepszesmsy.pl)."""

from __future__ import annotations

import json
import uuid
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

from monolynx.services.sms_client import _send_sms_sync, send_sms


@pytest.mark.unit
class TestSendSmsSyncNoLicenseKey:
    """Graceful degradation gdy LEPSZESMSY_LICENSE_KEY jest pusty."""

    @patch("monolynx.services.sms_client.settings")
    def test_no_license_key_does_not_crash(self, mock_settings):
        """Brak klucza licencji -> funkcja wraca bez bledu."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = ""
        _send_sms_sync("+48500000000", "Test message")

    @patch("monolynx.services.sms_client.settings")
    def test_no_license_key_logs_warning(self, mock_settings, caplog):
        """Brak klucza licencji -> loguje ostrzezenie z numerem telefonu."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = ""

        import logging

        with caplog.at_level(logging.WARNING):
            _send_sms_sync("+48500000001", "Test")

        assert caplog.text
        assert "+48500000001" in caplog.text

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_no_license_key_does_not_send_http_request(self, mock_settings, mock_urlopen):
        """Brak klucza licencji -> zadne zadanie HTTP nie jest wyslane."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = ""
        _send_sms_sync("+48500000000", "Test")
        mock_urlopen.assert_not_called()


@pytest.mark.unit
class TestSendSmsSyncHttpRequest:
    """Testy wysylania SMS przez HTTP do lepszesmsy.pl."""

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_sends_post_to_lepszesmsy_url(self, mock_settings, mock_urlopen):
        """Request trafia na URL lepszesmsy.pl jako metoda POST."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "test-key-123"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _send_sms_sync("+48500000000", "Alert: serwis jest offline")

        mock_urlopen.assert_called_once()
        req = mock_urlopen.call_args[0][0]
        assert req.full_url == "https://lepszesmsy.pl/api/messages/"
        assert req.method == "POST"

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_body_contains_license_key(self, mock_settings, mock_urlopen):
        """Body zawiera license__key z konfiguracji."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "moj-tajny-klucz"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _send_sms_sync("+48600000000", "wiadomosc")

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode())
        assert body["license__key"] == "moj-tajny-klucz"

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_body_contains_correct_phone_and_message(self, mock_settings, mock_urlopen):
        """Body zawiera numer telefonu i tresc wiadomosci."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _send_sms_sync("+48700000000", "Awaria serwisu ABC")

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode())
        assert body["phone"] == "+48700000000"
        assert body["message"] == "Awaria serwisu ABC"

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_body_contains_valid_uuid_external_id(self, mock_settings, mock_urlopen):
        """Body zawiera external_id jako poprawny UUID (string)."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _send_sms_sync("+48700000000", "test")

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode())
        assert "external_id" in body
        parsed = uuid.UUID(body["external_id"])  # rzuca ValueError gdy niepoprawny UUID
        assert isinstance(parsed, uuid.UUID)

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_body_contains_priority_field(self, mock_settings, mock_urlopen):
        """Body zawiera pole priority."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _send_sms_sync("+48700000000", "test")

        req = mock_urlopen.call_args[0][0]
        body = json.loads(req.data.decode())
        assert "priority" in body

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_content_type_header_is_application_json(self, mock_settings, mock_urlopen):
        """Request zawiera naglowek Content-Type: application/json."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = MagicMock(return_value=mock_resp)
        mock_resp.__exit__ = MagicMock(return_value=False)
        mock_urlopen.return_value = mock_resp

        _send_sms_sync("+48700000000", "test")

        req = mock_urlopen.call_args[0][0]
        assert req.get_header("Content-type") == "application/json"


@pytest.mark.unit
class TestSendSmsSyncErrorHandling:
    """Bledy HTTP nie crashuja aplikacji."""

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_http_error_500_does_not_crash(self, mock_settings, mock_urlopen):
        """HTTPError 500 jest lapany -- funkcja nie crashuje."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_urlopen.side_effect = HTTPError(
            url="https://lepszesmsy.pl/api/messages/",
            code=500,
            msg="Internal Server Error",
            hdrs=MagicMock(),
            fp=None,
        )
        _send_sms_sync("+48700000000", "test")

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_url_error_does_not_crash(self, mock_settings, mock_urlopen):
        """URLError (np. brak sieci) jest lapany -- funkcja nie crashuje."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_urlopen.side_effect = URLError(reason="Connection refused")
        _send_sms_sync("+48700000000", "test")

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_generic_exception_does_not_crash(self, mock_settings, mock_urlopen):
        """Nieoczekiwany wyjatek jest lapany -- funkcja nie crashuje."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_urlopen.side_effect = RuntimeError("unexpected error")
        _send_sms_sync("+48700000000", "test")

    @patch("monolynx.services.sms_client.urllib.request.urlopen")
    @patch("monolynx.services.sms_client.settings")
    def test_error_is_logged(self, mock_settings, mock_urlopen, caplog):
        """Blad wysylania SMS jest logowany z numerem telefonu."""
        mock_settings.LEPSZESMSY_LICENSE_KEY = "klucz"
        mock_urlopen.side_effect = OSError("Network unreachable")

        import logging

        with caplog.at_level(logging.ERROR):
            _send_sms_sync("+48700111222", "test")

        assert caplog.text
        assert "+48700111222" in caplog.text


@pytest.mark.unit
class TestSendSmsExecutor:
    """Testy wrappera send_sms (asynchroniczny executor)."""

    @patch("monolynx.services.sms_client._executor")
    def test_submits_to_executor(self, mock_executor):
        """send_sms zleca zadanie do executora."""
        send_sms("+48700000000", "test message")
        mock_executor.submit.assert_called_once_with(_send_sms_sync, "+48700000000", "test message")

    @patch("monolynx.services.sms_client._executor")
    def test_executor_exception_does_not_crash(self, mock_executor):
        """Blad executora jest lapany -- send_sms nie crashuje."""
        mock_executor.submit.side_effect = RuntimeError("executor broken")
        send_sms("+48700000000", "test")

    @patch("monolynx.services.sms_client._executor")
    def test_executor_exception_is_logged(self, mock_executor, caplog):
        """Blad executora jest logowany."""
        mock_executor.submit.side_effect = RuntimeError("executor broken")

        import logging

        with caplog.at_level(logging.ERROR):
            send_sms("+48700000000", "test")

        assert caplog.text
