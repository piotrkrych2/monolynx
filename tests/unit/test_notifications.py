"""Testy serwisu powiadomien -- send_monitor_alert, _build_alert_message, _is_webhook_url_safe."""

from __future__ import annotations

import socket
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from monolynx.services.notifications import (
    ALERT_DEBOUNCE_MINUTES,
    _build_alert_message,
    _is_webhook_url_safe,
    _send_slack_webhook_sync,
    send_monitor_alert,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_monitor(notification_config: dict | None, last_alert_sent_at=None) -> MagicMock:
    """Pomocnik tworzacy mock Monitor."""
    monitor = MagicMock()
    monitor.id = uuid.uuid4()
    monitor.name = "Test Monitor"
    monitor.url = "https://example.com"
    monitor.notification_config = notification_config
    monitor.last_alert_sent_at = last_alert_sent_at
    return monitor


def _make_check(status_code: int | None = 500, error_message: str | None = "Internal Server Error") -> MagicMock:
    """Pomocnik tworzacy mock MonitorCheck."""
    check = MagicMock()
    check.status_code = status_code
    check.error_message = error_message
    return check


# ---------------------------------------------------------------------------
# Testy _build_alert_message
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBuildAlertMessage:
    """Testy czystej funkcji _build_alert_message."""

    def test_contains_monitor_name(self):
        """Wiadomosc zawiera nazwe monitora."""
        msg = _build_alert_message("Produkcja API", "https://api.example.com", 500, "error")
        assert "Produkcja API" in msg

    def test_contains_monitor_url(self):
        """Wiadomosc zawiera URL monitora."""
        msg = _build_alert_message("Monitor", "https://api.example.com", 500, "error")
        assert "https://api.example.com" in msg

    def test_contains_status_code(self):
        """Wiadomosc zawiera kod statusu HTTP."""
        msg = _build_alert_message("Monitor", "https://example.com", 503, "unavailable")
        assert "503" in msg

    def test_contains_error_message(self):
        """Wiadomosc zawiera komunikat bledu."""
        msg = _build_alert_message("Monitor", "https://example.com", None, "Connection refused")
        assert "Connection refused" in msg

    def test_handles_none_status_code(self):
        """Brak kodu statusu (None) nie crashuje funkcji."""
        msg = _build_alert_message("Monitor", "https://example.com", None, None)
        assert msg  # jakis komunikat

    def test_handles_none_error_message(self):
        """Brak komunikatu bledu (None) nie crashuje funkcji."""
        msg = _build_alert_message("Monitor", "https://example.com", 200, None)
        assert msg


# ---------------------------------------------------------------------------
# Testy _is_webhook_url_safe (SSRF protection)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsWebhookUrlSafe:
    """Testy ochrony SSRF dla webhook URL."""

    def test_localhost_is_blocked(self):
        """localhost jest blokowany."""
        assert _is_webhook_url_safe("http://localhost/hook") is False

    def test_127_0_0_1_is_blocked(self):
        """127.0.0.1 jest blokowany."""
        assert _is_webhook_url_safe("http://127.0.0.1/hook") is False

    def test_file_scheme_is_blocked(self):
        """Schemat file:// jest blokowany."""
        assert _is_webhook_url_safe("file:///etc/passwd") is False

    def test_ftp_scheme_is_blocked(self):
        """Schemat ftp:// jest blokowany."""
        assert _is_webhook_url_safe("ftp://example.com/hook") is False

    def test_empty_string_is_blocked(self):
        """Pusty string jest blokowany."""
        assert _is_webhook_url_safe("") is False

    def test_url_without_hostname_is_blocked(self):
        """URL bez nazwy hosta jest blokowany."""
        assert _is_webhook_url_safe("https:///no-host") is False

    def test_malformed_url_raises_value_error_is_caught(self):
        """Blad parsowania URL (ValueError) -> traktowany jako niebezpieczny."""
        with patch("urllib.parse.urlparse", side_effect=ValueError("malformed URL")):
            assert _is_webhook_url_safe("http://example.com/hook") is False

    def test_dns_failure_is_blocked(self):
        """Blad rozwiazania DNS (socket.gaierror) -> niebezpieczny."""
        with patch(
            "monolynx.services.notifications.socket.getaddrinfo",
            side_effect=socket.gaierror("Name or service not known"),
        ):
            assert _is_webhook_url_safe("https://nonexistent.invalid/hook") is False

    def test_private_ip_is_blocked(self):
        """Domena rozwiazujaca sie do adresu prywatnego jest blokowana."""
        with patch(
            "monolynx.services.notifications.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.5", 0))],
        ):
            assert _is_webhook_url_safe("https://internal.example.com/hook") is False

    def test_loopback_ip_is_blocked(self):
        """Domena rozwiazujaca sie do adresu loopback jest blokowana."""
        with patch(
            "monolynx.services.notifications.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.5", 0))],
        ):
            assert _is_webhook_url_safe("https://sneaky.example.com/hook") is False

    def test_link_local_ip_is_blocked(self):
        """Domena rozwiazujaca sie do adresu link-local jest blokowana."""
        with patch(
            "monolynx.services.notifications.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("169.254.1.1", 0))],
        ):
            assert _is_webhook_url_safe("https://link-local.example.com/hook") is False

    def test_reserved_ip_is_blocked(self):
        """Domena rozwiazujaca sie do adresu zarezerwowanego jest blokowana."""
        with patch(
            "monolynx.services.notifications.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("240.0.0.1", 0))],
        ):
            assert _is_webhook_url_safe("https://reserved.example.com/hook") is False

    def test_public_ip_is_safe(self):
        """Domena rozwiazujaca sie do publicznego adresu -> bezpieczny."""
        with patch(
            "monolynx.services.notifications.socket.getaddrinfo",
            return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))],
        ):
            assert _is_webhook_url_safe("https://hooks.slack.com/services/TEST") is True

    def test_invalid_ip_in_addrinfo_is_skipped(self):
        """Niepoprawny adres IP w wyniku getaddrinfo jest pomijany, nie crashuje."""
        with patch(
            "monolynx.services.notifications.socket.getaddrinfo",
            return_value=[(socket.AF_UNIX, socket.SOCK_STREAM, 6, "", ("not-an-ip",))],
        ):
            assert _is_webhook_url_safe("https://weird.example.com/hook") is True


# ---------------------------------------------------------------------------
# Testy send_monitor_alert -- brak konfiguracji
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMonitorAlertNoConfig:
    """Brak konfiguracji powiadomien -- nic nie jest wysylane."""

    async def test_empty_dict_config_does_nothing(self):
        """Pusta konfiguracja -> zadne powiadomienie nie jest wyslane."""
        monitor = _make_monitor({})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email, patch("monolynx.services.sms_client.send_sms") as mock_sms:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_not_called()
        mock_sms.assert_not_called()

    async def test_empty_config_does_not_commit(self):
        """Pusta konfiguracja -> brak commit do bazy danych."""
        monitor = _make_monitor({})
        check = _make_check()
        db = AsyncMock()

        await send_monitor_alert(monitor, check, db)

        db.commit.assert_not_awaited()

    async def test_none_config_does_nothing(self):
        """Konfiguracja None -> funkcja nie crashuje i nie wysyla."""
        monitor = _make_monitor(None)
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_not_called()


# ---------------------------------------------------------------------------
# Testy debouncing
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMonitorAlertDebouncing:
    """Debouncing -- nie wysyla jesli < ALERT_DEBOUNCE_MINUTES od ostatniego alertu."""

    async def test_does_not_send_when_last_alert_2_minutes_ago(self):
        """Alert wysylany 2 minuty temu -> debouncing, nie wysyla."""
        monitor = _make_monitor(
            {"email_enabled": True, "email_recipients": ["ops@example.com"]},
            last_alert_sent_at=datetime.now(UTC) - timedelta(minutes=2),
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_not_called()

    async def test_does_not_commit_when_debounced(self):
        """Debouncing -> brak commit."""
        monitor = _make_monitor(
            {"email_enabled": True, "email_recipients": ["ops@example.com"]},
            last_alert_sent_at=datetime.now(UTC) - timedelta(minutes=1),
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email"):
            await send_monitor_alert(monitor, check, db)

        db.commit.assert_not_awaited()

    async def test_sends_when_last_alert_10_minutes_ago(self):
        """Alert wysylany 10 minut temu -> po debaouncing, wysyla."""
        monitor = _make_monitor(
            {"email_enabled": True, "email_recipients": ["ops@example.com"]},
            last_alert_sent_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_called_once()

    async def test_sends_when_no_previous_alert(self):
        """Brak poprzedniego alertu (None) -> wysyla bez ograniczen."""
        monitor = _make_monitor(
            {"email_enabled": True, "email_recipients": ["ops@example.com"]},
            last_alert_sent_at=None,
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_called_once()

    async def test_sends_when_exactly_over_debounce_threshold(self):
        """Uplynelo > ALERT_DEBOUNCE_MINUTES minut -> wysyla."""
        monitor = _make_monitor(
            {"email_enabled": True, "email_recipients": ["ops@example.com"]},
            last_alert_sent_at=datetime.now(UTC) - timedelta(minutes=ALERT_DEBOUNCE_MINUTES, seconds=1),
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_called_once()

    async def test_debounce_constant_is_5_minutes(self):
        """Stalej ALERT_DEBOUNCE_MINUTES wynosi 5 minut."""
        assert ALERT_DEBOUNCE_MINUTES == 5


# ---------------------------------------------------------------------------
# Testy email
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMonitorAlertEmail:
    """Wysylanie alertu emailem."""

    async def test_sends_email_to_configured_recipient(self):
        """Email jest wysylany na adres z email_recipients."""
        monitor = _make_monitor({"email_enabled": True, "email_recipients": ["devops@firma.pl"]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_called_once()
        to_addr = mock_email.call_args[0][0]
        assert to_addr == "devops@firma.pl"

    async def test_sends_email_to_all_recipients(self):
        """Email jest wysylany do kazdego adresu na liscie."""
        monitor = _make_monitor(
            {
                "email_enabled": True,
                "email_recipients": ["alpha@example.com", "beta@example.com"],
            }
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        assert mock_email.call_count == 2
        sent_to = {c[0][0] for c in mock_email.call_args_list}
        assert sent_to == {"alpha@example.com", "beta@example.com"}

    async def test_does_not_send_email_when_disabled(self):
        """email_enabled=False -> email nie jest wysylany."""
        monitor = _make_monitor({"email_enabled": False, "email_recipients": ["ops@example.com"]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_not_called()

    async def test_does_not_send_email_when_recipients_empty(self):
        """email_enabled=True ale pusta lista -> email nie jest wysylany."""
        monitor = _make_monitor({"email_enabled": True, "email_recipients": []})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_not_called()

    async def test_skips_blank_recipients(self):
        """Puste stringi w liscie odbiorcow sa pomijane."""
        monitor = _make_monitor({"email_enabled": True, "email_recipients": ["", "   "]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_not_called()


# ---------------------------------------------------------------------------
# Testy SMS
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMonitorAlertSms:
    """Wysylanie alertu SMS."""

    async def test_sends_sms_to_configured_phone(self):
        """SMS jest wysylany na numer z sms_recipients."""
        monitor = _make_monitor({"sms_enabled": True, "sms_recipients": ["+48500111222"]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.sms_client.send_sms") as mock_sms:
            await send_monitor_alert(monitor, check, db)

        mock_sms.assert_called_once()
        phone = mock_sms.call_args[0][0]
        assert phone == "+48500111222"

    async def test_sends_sms_to_all_recipients(self):
        """SMS jest wysylany na kazdy numer z listy."""
        monitor = _make_monitor(
            {
                "sms_enabled": True,
                "sms_recipients": ["+48500111222", "+48600333444"],
            }
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.sms_client.send_sms") as mock_sms:
            await send_monitor_alert(monitor, check, db)

        assert mock_sms.call_count == 2

    async def test_does_not_send_sms_when_disabled(self):
        """sms_enabled=False -> SMS nie jest wysylany."""
        monitor = _make_monitor({"sms_enabled": False, "sms_recipients": ["+48500111222"]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.sms_client.send_sms") as mock_sms:
            await send_monitor_alert(monitor, check, db)

        mock_sms.assert_not_called()

    async def test_does_not_send_sms_when_recipients_empty(self):
        """sms_enabled=True ale pusta lista -> SMS nie jest wysylany."""
        monitor = _make_monitor({"sms_enabled": True, "sms_recipients": []})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.sms_client.send_sms") as mock_sms:
            await send_monitor_alert(monitor, check, db)

        mock_sms.assert_not_called()


# ---------------------------------------------------------------------------
# Testy Slack
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMonitorAlertSlack:
    """Wysylanie alertu przez Slack webhook."""

    async def test_sends_slack_when_enabled(self):
        """slack_enabled=True -> _send_slack_webhook_sync jest wywolywany."""
        monitor = _make_monitor(
            {
                "slack_enabled": True,
                "slack_channels": ["https://hooks.slack.com/services/TEST/ABC"],
            }
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.notifications._send_slack_webhook_sync") as mock_slack:
            await send_monitor_alert(monitor, check, db)

        mock_slack.assert_called_once()

    async def test_sends_slack_to_all_channels(self):
        """Slack webhook jest wysylany na kazdy kanal z listy."""
        monitor = _make_monitor(
            {
                "slack_enabled": True,
                "slack_channels": [
                    "https://hooks.slack.com/services/A",
                    "https://hooks.slack.com/services/B",
                ],
            }
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.notifications._send_slack_webhook_sync") as mock_slack:
            await send_monitor_alert(monitor, check, db)

        assert mock_slack.call_count == 2

    async def test_does_not_send_slack_when_disabled(self):
        """slack_enabled=False -> webhook nie jest wywolywany."""
        monitor = _make_monitor(
            {
                "slack_enabled": False,
                "slack_channels": ["https://hooks.slack.com/services/TEST"],
            }
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.notifications._send_slack_webhook_sync") as mock_slack:
            await send_monitor_alert(monitor, check, db)

        mock_slack.assert_not_called()

    async def test_does_not_send_slack_when_channels_empty(self):
        """slack_enabled=True ale pusta lista -> webhook nie jest wywolywany."""
        monitor = _make_monitor({"slack_enabled": True, "slack_channels": []})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.notifications._send_slack_webhook_sync") as mock_slack:
            await send_monitor_alert(monitor, check, db)

        mock_slack.assert_not_called()

    async def test_slack_error_does_not_crash(self):
        """Blad _send_slack_webhook_sync jest lapany -- funkcja nie crashuje."""
        monitor = _make_monitor(
            {
                "slack_enabled": True,
                "slack_channels": ["https://hooks.slack.com/services/TEST"],
            }
        )
        check = _make_check()
        db = AsyncMock()

        with patch(
            "monolynx.services.notifications._send_slack_webhook_sync",
            side_effect=RuntimeError("webhook failed"),
        ):
            await send_monitor_alert(monitor, check, db)  # nie powinno rzucac


# ---------------------------------------------------------------------------
# Testy _send_slack_webhook_sync (funkcja synchroniczna wywolywana w executorze)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendSlackWebhookSync:
    """Testy niskopoziomowej funkcji wysylajacej webhook Slack."""

    def test_unsafe_url_does_not_send(self):
        """Niebezpieczny URL webhooka -> wczesny return, brak wysylki."""
        with (
            patch("monolynx.services.notifications._is_webhook_url_safe", return_value=False) as mock_safe,
            patch("monolynx.services.notifications.urllib.request.urlopen") as mock_urlopen,
        ):
            _send_slack_webhook_sync("http://localhost/hook", "message")

        mock_safe.assert_called_once_with("http://localhost/hook")
        mock_urlopen.assert_not_called()

    def test_urlopen_success_logs_status(self):
        """Udane wyslanie -- urlopen wywolane z poprawnym requestem."""
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False

        with (
            patch("monolynx.services.notifications._is_webhook_url_safe", return_value=True),
            patch("monolynx.services.notifications.urllib.request.urlopen", return_value=mock_resp) as mock_urlopen,
        ):
            _send_slack_webhook_sync("https://hooks.slack.com/services/TEST", "message")

        mock_urlopen.assert_called_once()

    def test_urlopen_exception_is_caught(self):
        """Wyjatek podczas wysylania webhooka jest lapany (logger.exception), nie propaguje."""
        with (
            patch("monolynx.services.notifications._is_webhook_url_safe", return_value=True),
            patch(
                "monolynx.services.notifications.urllib.request.urlopen",
                side_effect=RuntimeError("connection failed"),
            ),
        ):
            _send_slack_webhook_sync("https://hooks.slack.com/services/TEST", "message")  # nie powinno rzucac


# ---------------------------------------------------------------------------
# Testy debouncingu z naiwnym (bez tzinfo) last_alert_sent_at
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMonitorAlertNaiveDatetime:
    """last_alert_sent_at bez tzinfo jest traktowany jako UTC."""

    async def test_naive_datetime_within_debounce_blocks_send(self):
        """Naiwny datetime sprzed 1 minuty -> debounce blokuje wysylke."""
        monitor = _make_monitor(
            {"email_enabled": True, "email_recipients": ["ops@example.com"]},
            last_alert_sent_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=1),
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_not_called()

    async def test_naive_datetime_past_debounce_allows_send(self):
        """Naiwny datetime sprzed 10 minut -> po debounce, wysyla."""
        monitor = _make_monitor(
            {"email_enabled": True, "email_recipients": ["ops@example.com"]},
            last_alert_sent_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(minutes=10),
        )
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email") as mock_email:
            await send_monitor_alert(monitor, check, db)

        mock_email.assert_called_once()


# ---------------------------------------------------------------------------
# Testy aktualizacji znacznika czasu i commitu
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSendMonitorAlertUpdatesTimestamp:
    """Po wyslaniu aktualizuje last_alert_sent_at i commituje sesje."""

    async def test_updates_last_alert_sent_at_after_email(self):
        """Po wyslaniu emaila last_alert_sent_at jest ustawiony."""
        monitor = _make_monitor({"email_enabled": True, "email_recipients": ["ops@example.com"]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email"):
            await send_monitor_alert(monitor, check, db)

        assert monitor.last_alert_sent_at is not None

    async def test_updates_last_alert_sent_at_after_sms(self):
        """Po wyslaniu SMS last_alert_sent_at jest ustawiony."""
        monitor = _make_monitor({"sms_enabled": True, "sms_recipients": ["+48500000000"]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.sms_client.send_sms"):
            await send_monitor_alert(monitor, check, db)

        assert monitor.last_alert_sent_at is not None

    async def test_commits_db_after_sending(self):
        """Po wyslaniu powiadomienia sesja jest commitowana."""
        monitor = _make_monitor({"email_enabled": True, "email_recipients": ["ops@example.com"]})
        check = _make_check()
        db = AsyncMock()

        with patch("monolynx.services.email.send_email"):
            await send_monitor_alert(monitor, check, db)

        db.commit.assert_awaited_once()

    async def test_does_not_update_timestamp_when_nothing_sent(self):
        """Gdy nic nie wyslano, last_alert_sent_at nie jest zmieniany."""
        monitor = _make_monitor({})
        monitor.last_alert_sent_at = None
        check = _make_check()
        db = AsyncMock()

        await send_monitor_alert(monitor, check, db)

        assert monitor.last_alert_sent_at is None

    async def test_does_not_commit_when_nothing_sent(self):
        """Gdy nic nie wyslano, sesja nie jest commitowana."""
        monitor = _make_monitor({})
        check = _make_check()
        db = AsyncMock()

        await send_monitor_alert(monitor, check, db)

        db.commit.assert_not_awaited()
