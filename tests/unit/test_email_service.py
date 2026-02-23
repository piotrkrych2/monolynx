"""Testy serwisu email -- wysylanie zaproszen, obsluga bledow SMTP."""

import uuid
from unittest.mock import MagicMock, patch

import pytest

from monolynx.services.email import (
    _send_email_sync,
    send_email,
    send_invitation_email,
)


@pytest.mark.unit
class TestSendEmailSync:
    """Testy synchronicznej funkcji _send_email_sync."""

    @patch("monolynx.services.email.settings")
    def test_smtp_not_configured_does_not_crash(self, mock_settings):
        """Gdy SMTP_HOST jest pusty, funkcja wraca bez bledu."""
        mock_settings.SMTP_HOST = ""

        # Nie powinno rzucic wyjatku
        _send_email_sync("user@example.com", "Temat", "<p>Tresc</p>")

    @patch("monolynx.services.email.settings")
    def test_smtp_not_configured_logs_warning(self, mock_settings, caplog):
        """Gdy SMTP nie jest skonfigurowany, loguje ostrzezenie."""
        mock_settings.SMTP_HOST = ""

        import logging

        with caplog.at_level(logging.WARNING):
            _send_email_sync("user@example.com", "Temat", "<p>Tresc</p>")

        assert "SMTP nie skonfigurowany" in caplog.text
        assert "user@example.com" in caplog.text

    @patch("monolynx.services.email.smtplib.SMTP")
    @patch("monolynx.services.email.settings")
    def test_sends_email_with_tls(self, mock_settings, mock_smtp_cls):
        """Gdy SMTP_USE_TLS=True, uzywa starttls()."""
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USE_TLS = True
        mock_settings.SMTP_USER = "user"
        mock_settings.SMTP_PASSWORD = "pass"
        mock_settings.SMTP_FROM_EMAIL = "noreply@example.com"

        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        _send_email_sync("to@example.com", "Temat", "<p>Tresc</p>")

        mock_smtp_cls.assert_called_once_with("smtp.example.com", 587)
        mock_server.starttls.assert_called_once()
        mock_server.login.assert_called_once_with("user", "pass")
        mock_server.sendmail.assert_called_once()
        # Sprawdz ze nadawca i odbiorca sa poprawni
        call_args = mock_server.sendmail.call_args
        assert call_args[0][0] == "noreply@example.com"
        assert call_args[0][1] == "to@example.com"
        mock_server.quit.assert_called_once()

    @patch("monolynx.services.email.smtplib.SMTP")
    @patch("monolynx.services.email.settings")
    def test_sends_email_without_tls(self, mock_settings, mock_smtp_cls):
        """Gdy SMTP_USE_TLS=False, nie wywoluje starttls()."""
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 25
        mock_settings.SMTP_USE_TLS = False
        mock_settings.SMTP_USER = ""
        mock_settings.SMTP_PASSWORD = ""
        mock_settings.SMTP_FROM_EMAIL = "noreply@example.com"

        mock_server = MagicMock()
        mock_smtp_cls.return_value = mock_server

        _send_email_sync("to@example.com", "Temat", "<p>Tresc</p>")

        mock_smtp_cls.assert_called_once_with("smtp.example.com", 25)
        mock_server.starttls.assert_not_called()
        mock_server.login.assert_not_called()
        mock_server.sendmail.assert_called_once()
        mock_server.quit.assert_called_once()

    @patch("monolynx.services.email.smtplib.SMTP")
    @patch("monolynx.services.email.settings")
    def test_smtp_exception_does_not_crash(self, mock_settings, mock_smtp_cls):
        """Blad SMTP jest lapany -- funkcja nie crashuje."""
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USE_TLS = False
        mock_settings.SMTP_USER = ""
        mock_settings.SMTP_PASSWORD = ""
        mock_settings.SMTP_FROM_EMAIL = "noreply@example.com"

        mock_smtp_cls.side_effect = ConnectionRefusedError("Connection refused")

        # Nie powinno rzucic wyjatku
        _send_email_sync("to@example.com", "Temat", "<p>Tresc</p>")

    @patch("monolynx.services.email.smtplib.SMTP")
    @patch("monolynx.services.email.settings")
    def test_smtp_exception_logs_error(self, mock_settings, mock_smtp_cls, caplog):
        """Blad SMTP jest logowany."""
        mock_settings.SMTP_HOST = "smtp.example.com"
        mock_settings.SMTP_PORT = 587
        mock_settings.SMTP_USE_TLS = False
        mock_settings.SMTP_USER = ""
        mock_settings.SMTP_PASSWORD = ""
        mock_settings.SMTP_FROM_EMAIL = "noreply@example.com"

        mock_smtp_cls.side_effect = OSError("Network unreachable")

        import logging

        with caplog.at_level(logging.ERROR):
            _send_email_sync("to@example.com", "Temat", "<p>Tresc</p>")

        assert "Blad wysylania emaila" in caplog.text
        assert "to@example.com" in caplog.text


@pytest.mark.unit
class TestSendEmail:
    """Testy funkcji send_email (wrapper z executorem)."""

    @patch("monolynx.services.email._executor")
    def test_submits_to_executor(self, mock_executor):
        """send_email zleca zadanie do executora."""
        send_email("to@example.com", "Temat", "<p>Tresc</p>")

        mock_executor.submit.assert_called_once_with(_send_email_sync, "to@example.com", "Temat", "<p>Tresc</p>")

    @patch("monolynx.services.email._executor")
    def test_executor_exception_does_not_crash(self, mock_executor):
        """Blad executora jest lapany -- send_email nie crashuje."""
        mock_executor.submit.side_effect = RuntimeError("executor broken")

        # Nie powinno rzucic wyjatku
        send_email("to@example.com", "Temat", "<p>Tresc</p>")

    @patch("monolynx.services.email._executor")
    def test_executor_exception_logs_error(self, mock_executor, caplog):
        """Blad executora jest logowany."""
        mock_executor.submit.side_effect = RuntimeError("executor broken")

        import logging

        with caplog.at_level(logging.ERROR):
            send_email("to@example.com", "Temat", "<p>Tresc</p>")

        assert "Blad zlecania wysylki emaila" in caplog.text


@pytest.mark.unit
class TestSendInvitationEmail:
    """Testy funkcji send_invitation_email."""

    @patch("monolynx.services.email.send_email")
    @patch("monolynx.services.email.settings")
    def test_builds_correct_link(self, mock_settings, mock_send_email):
        """Link w zaproszeniu zawiera token i APP_URL."""
        mock_settings.APP_URL = "https://sentry.example.com"
        token = uuid.UUID("12345678-1234-5678-1234-567812345678")

        send_invitation_email("new@example.com", "Jan", token)

        mock_send_email.assert_called_once()
        call_args = mock_send_email.call_args
        body_html = call_args[0][2]
        expected_link = "https://sentry.example.com/auth/accept-invite/12345678-1234-5678-1234-567812345678"
        assert expected_link in body_html

    @patch("monolynx.services.email.send_email")
    @patch("monolynx.services.email.settings")
    def test_strips_trailing_slash_from_app_url(self, mock_settings, mock_send_email):
        """APP_URL z trailing slash nie powoduje podwojnego slasha."""
        mock_settings.APP_URL = "https://sentry.example.com/"
        token = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")

        send_invitation_email("new@example.com", "Anna", token)

        call_args = mock_send_email.call_args
        body_html = call_args[0][2]
        assert "//auth" not in body_html
        expected_link = "https://sentry.example.com/auth/accept-invite/aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
        assert expected_link in body_html

    @patch("monolynx.services.email.send_email")
    @patch("monolynx.services.email.settings")
    def test_subject_is_zaproszenie(self, mock_settings, mock_send_email):
        """Temat emaila to 'Zaproszenie do Monolynx'."""
        mock_settings.APP_URL = "http://localhost:8000"
        token = uuid.uuid4()

        send_invitation_email("new@example.com", "Jan", token)

        call_args = mock_send_email.call_args
        subject = call_args[0][1]
        assert subject == "Zaproszenie do Monolynx"

    @patch("monolynx.services.email.send_email")
    @patch("monolynx.services.email.settings")
    def test_greeting_includes_first_name(self, mock_settings, mock_send_email):
        """Powitanie zawiera imie uzytkownika."""
        mock_settings.APP_URL = "http://localhost:8000"
        token = uuid.uuid4()

        send_invitation_email("new@example.com", "Katarzyna", token)

        call_args = mock_send_email.call_args
        body_html = call_args[0][2]
        assert "Witaj Katarzyna!" in body_html

    @patch("monolynx.services.email.send_email")
    @patch("monolynx.services.email.settings")
    def test_greeting_without_first_name(self, mock_settings, mock_send_email):
        """Gdy brak imienia, powitanie bez dodatkowej spacji."""
        mock_settings.APP_URL = "http://localhost:8000"
        token = uuid.uuid4()

        send_invitation_email("new@example.com", "", token)

        call_args = mock_send_email.call_args
        body_html = call_args[0][2]
        assert "Witaj!" in body_html
        # Nie powinno byc "Witaj !" z dodatkowa spacja
        assert "Witaj !" not in body_html

    @patch("monolynx.services.email.send_email")
    @patch("monolynx.services.email.settings")
    def test_sends_to_correct_recipient(self, mock_settings, mock_send_email):
        """Email jest wysylany na podany adres."""
        mock_settings.APP_URL = "http://localhost:8000"
        token = uuid.uuid4()

        send_invitation_email("recipient@company.com", "Piotr", token)

        call_args = mock_send_email.call_args
        to = call_args[0][0]
        assert to == "recipient@company.com"
