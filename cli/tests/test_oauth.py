"""Testy modulu monolynx_cli.oauth (MON-143): logowanie OAuth 2.1 z loopback redirect i odswiezanie tokenu.

Specyfikacja TDD dla oauth.py. Serwer autoryzacji to `FakeAuthServer` (httpx.MockTransport)
podpiety pod `oauth.http_client`; zachowuje sie jak backend: DCR nadaje client_id z
zapamietanym redirect_uri, /token przy `authorization_code` weryfikuje client_id, dokladny
redirect_uri i PKCE (sha256(code_verifier) == code_challenge z URL-a autoryzacji), a przy
`refresh_token` honoruje wylacznie NIEZUZYTE refresh tokeny (rotacja). Dzieki temu mutanty
"stary client_id", "zly PKCE" i "zapis starego refresh tokenu" koncza sie bledem serwera.

Przegladarke symuluje `make_browser`: parsuje URL autoryzacji i w osobnym watku robi
prawdziwe GET na serwer loopback z modulu pod testem. Fallback bez przegladarki (URL na
stderr przez typer.echo) symuluje `EchoSpy`, ktory po wypisaniu URL-a uruchamia ten sam
callback. Helpery (`FakeAuthServer`, `install_server`, `make_browser`, `EchoSpy`) importuje
tez test_auth_commands.py.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest
import typer
from monolynx_cli import config as config_mod
from monolynx_cli import oauth
from monolynx_cli.client import MonolynxAuthError, MonolynxError, MonolynxNetworkError
from monolynx_cli.config import Settings

pytestmark = pytest.mark.unit

BASE = "https://mon.test"
DISCOVERY_PATH = "/.well-known/oauth-authorization-server"
LOGIN_EXPIRES_IN = 3600
REFRESH_EXPIRES_IN = 7200
CODE = "code-" + "X" * 30
OLD_ACCESS = "old-access-" + "o" * 24
OLD_REFRESH = "old-refresh-" + "r" * 24
EXPIRED_AT = "2020-01-01T00:00:00+00:00"
# Nieprawidlowy port: httpx rzuca InvalidURL PRZED wyslaniem zadania ("http://[invalid" przechodzi i daje 404 z transportu).
INVALID_URL = "http://example.com:abc/x"
SESSION_LOST_MESSAGE = "Sesja wygasła i nie udało się jej odnowić. Zaloguj się ponownie: monolynx auth login"
_URL_PATTERN = re.compile(r"https?://\S+/authorize\?\S+")


class FakeAuthServer:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.transport: httpx.MockTransport | None = None
        self.register_bodies: list[dict[str, Any]] = []
        self.token_forms: list[dict[str, str]] = []
        self.clients: dict[str, str] = {}
        self.auth_params: dict[str, dict[str, str]] = {}
        self.opened_urls: list[str] = []
        self.callback_results: list[tuple[str, int | None]] = []
        self.callback_bodies: list[tuple[str, str]] = []
        self.thread_errors: list[str] = []
        self.threads: list[threading.Thread] = []
        self.timeouts: list[float] = []
        self.valid_refresh: set[str] = set()
        self.valid_access: set[str] = set()
        self.issued_access: list[str] = []
        self.issued_refresh: list[str] = []
        self.register_status = 201
        self.me_status = 200
        self.me_email = "oauth-user@example.com"
        self.discovery_error: Exception | None = None
        self.discovery_document: dict[str, Any] | None = None
        self.token_override: Callable[[dict[str, str]], httpx.Response] | None = None
        self._counter = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == DISCOVERY_PATH:
            if self.discovery_error is not None:
                raise self.discovery_error
            document = self.discovery_document
            if document is None:
                document = {
                    "issuer": BASE,
                    "authorization_endpoint": f"{BASE}/authorize",
                    "token_endpoint": f"{BASE}/token",
                    "registration_endpoint": f"{BASE}/register",
                    "code_challenge_methods_supported": ["S256"],
                }
            return httpx.Response(200, json=document)
        if path == "/register" and request.method == "POST":
            body = json.loads(request.content)
            self.register_bodies.append(body)
            if self.register_status != 201:
                return httpx.Response(self.register_status, json={"error": "server_error"})
            self._counter += 1
            client_id = f"mlx_client_{self._counter}_" + "c" * 20
            self.clients[client_id] = body["redirect_uris"][0]
            return httpx.Response(
                201,
                json={
                    "client_id": client_id,
                    "client_name": body["client_name"],
                    "redirect_uris": body["redirect_uris"],
                    "grant_types": body["grant_types"],
                },
            )
        if path == "/token" and request.method == "POST":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            self.token_forms.append(form)
            if self.token_override is not None:
                return self.token_override(form)
            return self._token(form)
        if path == "/api/v2/me":
            authorization = request.headers.get("authorization", "")
            if self.me_status == 200 and authorization.removeprefix("Bearer ") in self.valid_access:
                return httpx.Response(200, json={"id": "11111111-1111-4111-8111-111111111111", "email": self.me_email})
            status = 401 if self.me_status == 200 else self.me_status
            return httpx.Response(
                status,
                json={"title": "Brak dostepu", "detail": f"Szczegoly bledu {status}"},
                headers={"content-type": "application/problem+json"},
            )
        return httpx.Response(404)

    def _token(self, form: dict[str, str]) -> httpx.Response:
        invalid = httpx.Response(400, json={"error": "invalid_grant"})
        grant_type = form.get("grant_type")
        if grant_type == "authorization_code":
            params = self.auth_params.get(form.get("code", ""))
            client_id = form.get("client_id", "")
            if params is None or client_id != params.get("client_id") or client_id not in self.clients:
                return invalid
            if form.get("redirect_uri") != self.clients[client_id] or form.get("redirect_uri") != params.get("redirect_uri"):
                return invalid
            digest = hashlib.sha256(form.get("code_verifier", "").encode("ascii")).digest()
            expected = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
            if params.get("code_challenge_method") != "S256" or params.get("code_challenge") != expected:
                return invalid
            return self.issue(LOGIN_EXPIRES_IN)
        if grant_type == "refresh_token":
            refresh = form.get("refresh_token", "")
            if refresh not in self.valid_refresh:
                return invalid
            self.valid_refresh.discard(refresh)
            return self.issue(REFRESH_EXPIRES_IN)
        return httpx.Response(400, json={"error": "unsupported_grant_type"})

    def issue(self, expires_in: int) -> httpx.Response:
        self._counter += 1
        access = f"access-{self._counter}-" + "A" * 24
        refresh = f"refresh-{self._counter}-" + "R" * 24
        self.valid_access.add(access)
        self.valid_refresh.add(refresh)
        self.issued_access.append(access)
        self.issued_refresh.append(refresh)
        return httpx.Response(200, json={"access_token": access, "token_type": "Bearer", "expires_in": expires_in, "refresh_token": refresh})

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]

    def join(self) -> None:
        for thread in self.threads:
            thread.join(timeout=10)

    def _get(self, url: str) -> None:
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                self._record(url, response.status, response.read().decode("utf-8", "replace"))
        except urllib.error.HTTPError as exc:
            self._record(url, exc.code, exc.read().decode("utf-8", "replace"))
        except OSError as exc:
            self.thread_errors.append(f"{url}: {exc}")

    def _record(self, url: str, status: int, body: str) -> None:
        self.callback_results.append((urllib.parse.urlparse(url).path, status))
        self.callback_bodies.append((urllib.parse.urlparse(url).path, body))

    def dispatch_callback(self, authorize_url: str, *, code: str = CODE, mode: str = "ok") -> threading.Thread:
        """Rejestruje parametry z URL-a autoryzacji i w osobnym watku uderza w loopback redirect_uri."""
        self.opened_urls.append(authorize_url)
        params = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(authorize_url).query))
        self.auth_params[code] = params
        redirect = urllib.parse.urlparse(params["redirect_uri"])
        origin = f"{redirect.scheme}://{redirect.netloc}"
        state = params["state"]
        queries: dict[str, dict[str, str]] = {
            "ok": {"code": code, "state": state},
            "favicon_first": {"code": code, "state": state},
            "wrong_state": {"code": code, "state": "wrong-" + state},
            "missing_state": {"code": code},
            "denied": {"error": "access_denied", "state": state},
            "server_error": {"error": "server_error", "state": state},
        }

        def run() -> None:
            if mode == "favicon_first":
                self._get(f"{origin}/favicon.ico")
            self._get(f"{origin}{redirect.path}?{urllib.parse.urlencode(queries[mode])}")

        thread = threading.Thread(target=run, daemon=True)
        self.threads.append(thread)
        thread.start()
        return thread


def install_server(monkeypatch, mock_transport) -> FakeAuthServer:
    server = FakeAuthServer()
    transport, requests = mock_transport(server.handler)
    server.transport = transport
    server.requests = requests

    def http_client(timeout: float) -> httpx.Client:
        server.timeouts.append(timeout)
        return httpx.Client(transport=transport, timeout=timeout)

    monkeypatch.setattr(oauth, "http_client", http_client)
    return server


def make_browser(server: FakeAuthServer, *, code: str = CODE, mode: str = "ok") -> Callable[..., bool]:
    def _open(url: str, *args: Any, **kwargs: Any) -> bool:
        server.dispatch_callback(url, code=code, mode=mode)
        return True

    return _open


class EchoSpy:
    """Podglad typer.echo: zapamietuje wywolania i po wypisaniu URL-a autoryzacji odpala callback (fallback bez przegladarki)."""

    def __init__(self, server: FakeAuthServer, *, code: str = CODE, mode: str = "ok", respond: bool = True) -> None:
        self._real = typer.echo
        self._server = server
        self._code = code
        self._mode = mode
        self._respond = respond
        self.calls: list[tuple[str, bool]] = []

    def __call__(self, message: Any = None, **kwargs: Any) -> None:
        text = str(message)
        self.calls.append((text, bool(kwargs.get("err", False))))
        match = _URL_PATTERN.search(text)
        if match and self._respond:
            self._server.dispatch_callback(match.group(0), code=self._code, mode=self._mode)
        self._real(message, **kwargs)

    def stderr_text(self) -> str:
        return "\n".join(text for text, err in self.calls if err)

    def stdout_text(self) -> str:
        return "\n".join(text for text, err in self.calls if not err)


@pytest.fixture
def auth_server(monkeypatch, mock_transport):
    server = install_server(monkeypatch, mock_transport)
    yield server
    server.join()


def _parse_expires(text: str) -> datetime:
    parsed = datetime.fromisoformat(text)
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    return parsed


def _assert_expires_in(text: str, seconds: int) -> None:
    expected = datetime.now(timezone.utc) + timedelta(seconds=seconds)  # noqa: UP017 -- Python 3.10 nie ma datetime.UTC
    assert abs((_parse_expires(text) - expected).total_seconds()) < 30


def _assert_port_closed(redirect_uri: str) -> None:
    # Odmowa polaczenia, nie odpowiedz HTTP: dzialajacy serwer odpowiada 4xx (HTTPError to podklasa URLError),
    # wiec lapanie URLError przepuszczalo mutanta bez zamykania serwera.
    port = urllib.parse.urlparse(redirect_uri).port
    assert port
    with pytest.raises(ConnectionRefusedError):
        socket.create_connection(("127.0.0.1", port), timeout=2)


def _login(**kwargs: Any) -> oauth.TokenSet:
    return oauth.run_login(BASE, timeout=5.0, **kwargs)


# ---------------------------------------------------------------------------
# generate_pkce
# ---------------------------------------------------------------------------


def test_generate_pkce_verifier_has_rfc7636_length_and_alphabet():
    verifier, _ = oauth.generate_pkce()

    assert 43 <= len(verifier) <= 128
    assert re.fullmatch(r"[A-Za-z0-9\-._~]+", verifier)


def test_generate_pkce_challenge_is_unpadded_base64url_sha256_of_verifier():
    verifier, challenge = oauth.generate_pkce()

    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    assert challenge == base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert "=" not in challenge


def test_generate_pkce_returns_different_pair_on_each_call():
    first = oauth.generate_pkce()
    second = oauth.generate_pkce()

    assert first[0] != second[0]
    assert first[1] != second[1]


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


def test_discover_returns_document_and_requests_well_known_without_double_slash(auth_server):
    document = oauth.discover(BASE + "/", timeout=5.0)

    assert document["token_endpoint"] == f"{BASE}/token"
    assert [str(request.url) for request in auth_server.requests] == [f"{BASE}{DISCOVERY_PATH}"]
    assert auth_server.requests[0].method == "GET"


def test_discover_passes_timeout_to_http_client(auth_server):
    oauth.discover(BASE, timeout=7.5)

    assert auth_server.timeouts == [7.5]


@pytest.mark.parametrize("missing", ["authorization_endpoint", "token_endpoint", "registration_endpoint"])
def test_discover_missing_required_endpoint_raises_auth_error(auth_server, missing):
    document = {
        "authorization_endpoint": f"{BASE}/authorize",
        "token_endpoint": f"{BASE}/token",
        "registration_endpoint": f"{BASE}/register",
    }
    del document[missing]
    auth_server.discovery_document = document

    with pytest.raises(MonolynxAuthError):
        oauth.discover(BASE, timeout=5.0)


def test_discover_non_json_body_raises_auth_error(monkeypatch, mock_transport):
    transport, _ = mock_transport(lambda request: httpx.Response(200, content=b"<html>nie json</html>", headers={"content-type": "text/html"}))
    monkeypatch.setattr(oauth, "http_client", lambda timeout: httpx.Client(transport=transport, timeout=timeout))

    with pytest.raises(MonolynxAuthError):
        oauth.discover(BASE, timeout=5.0)


def test_discover_network_error_raises_network_error(auth_server):
    auth_server.discovery_error = httpx.ConnectError("polaczenie odrzucone")

    with pytest.raises(MonolynxNetworkError):
        oauth.discover(BASE, timeout=5.0)


# ---------------------------------------------------------------------------
# run_login: sciezka szczesliwa
# ---------------------------------------------------------------------------


def test_run_login_happy_path_returns_token_set_from_token_endpoint(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    tokens = _login()

    client_id = next(iter(auth_server.clients))
    assert tokens.client_id == client_id
    assert tokens.access_token == auth_server.issued_access[0]
    assert tokens.refresh_token == auth_server.issued_refresh[0]
    _assert_expires_in(tokens.expires_at, LOGIN_EXPIRES_IN)


def test_run_login_registers_client_with_loopback_redirect_and_expected_body(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()

    assert len(auth_server.register_bodies) == 1
    body = auth_server.register_bodies[0]
    redirect_uri = body["redirect_uris"][0]
    assert body == {
        "client_name": "Monolynx CLI",
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
    }
    parsed = urllib.parse.urlparse(redirect_uri)
    assert redirect_uri.startswith("http://127.0.0.1:")
    assert parsed.port and parsed.port > 0
    assert parsed.path == "/callback"


def test_run_login_binds_only_loopback_on_ephemeral_port(monkeypatch, auth_server):
    binds: list[Any] = []
    original_bind = socket.socket.bind

    def spy_bind(self, address):
        binds.append(address)
        return original_bind(self, address)

    monkeypatch.setattr(socket.socket, "bind", spy_bind)
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()

    assert binds == [("127.0.0.1", 0)]


def test_run_login_authorize_url_carries_pkce_state_and_registered_client(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()

    (url,) = auth_server.opened_urls
    assert url.startswith(f"{BASE}/authorize?")
    params = auth_server.auth_params[CODE]
    client_id = next(iter(auth_server.clients))
    assert params["response_type"] == "code"
    assert params["client_id"] == client_id
    assert params["redirect_uri"] == auth_server.register_bodies[0]["redirect_uris"][0]
    assert params["code_challenge_method"] == "S256"
    assert params["code_challenge"]
    assert len(params["state"]) >= 32


def test_run_login_exchanges_code_with_form_encoded_post_and_matching_pkce(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()

    (form,) = auth_server.token_forms
    client_id = next(iter(auth_server.clients))
    assert form == {
        "grant_type": "authorization_code",
        "code": CODE,
        "code_verifier": form["code_verifier"],
        "client_id": client_id,
        "redirect_uri": auth_server.clients[client_id],
    }
    assert 43 <= len(form["code_verifier"]) <= 128
    assert form["code_verifier"] != auth_server.auth_params[CODE]["code_challenge"]
    token_request = next(request for request in auth_server.requests if request.url.path == "/token")
    assert token_request.headers["content-type"].startswith("application/x-www-form-urlencoded")


def test_run_login_success_page_is_html_with_status_200(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()
    auth_server.join()

    assert auth_server.thread_errors == []
    assert auth_server.callback_results == [("/callback", 200)]
    assert "<" in auth_server.callback_bodies[0][1]


def test_run_login_ignores_non_callback_path_and_keeps_waiting(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server, mode="favicon_first"))

    tokens = _login()
    auth_server.join()

    assert tokens.access_token == auth_server.issued_access[0]
    assert auth_server.callback_results == [("/favicon.ico", 404), ("/callback", 200)]


def test_run_login_does_not_write_config(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()

    assert config_mod.config_path().exists() is False


def test_run_login_passes_timeout_to_http_client(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    oauth.run_login(BASE, timeout=11.0)

    assert auth_server.timeouts
    assert set(auth_server.timeouts) == {11.0}


def test_run_login_does_not_print_secrets(monkeypatch, auth_server, capsys):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()

    captured = capsys.readouterr()
    (form,) = auth_server.token_forms
    for secret in (CODE, form["code_verifier"], auth_server.issued_access[0], auth_server.issued_refresh[0]):
        assert secret not in captured.out
        assert secret not in captured.err


# ---------------------------------------------------------------------------
# run_login: DCR przy kazdym logowaniu
# ---------------------------------------------------------------------------


def test_run_login_registers_new_client_on_every_call_and_uses_the_latest(monkeypatch, auth_server):
    first_browser = make_browser(auth_server, code="code-first-" + "1" * 20)
    second_browser = make_browser(auth_server, code="code-second-" + "2" * 20)

    monkeypatch.setattr(oauth.webbrowser, "open", first_browser)
    first = _login()
    monkeypatch.setattr(oauth.webbrowser, "open", second_browser)
    second = _login()

    assert len(auth_server.register_bodies) == 2
    assert first.client_id != second.client_id
    assert list(auth_server.clients) == [first.client_id, second.client_id]
    assert [form["client_id"] for form in auth_server.token_forms] == [first.client_id, second.client_id]
    assert second.access_token == auth_server.issued_access[1]


# ---------------------------------------------------------------------------
# run_login: przegladarka i fallback z URL-em na stderr
# ---------------------------------------------------------------------------


def test_run_login_no_browser_does_not_open_browser_and_prints_url_to_stderr(monkeypatch, auth_server):
    opened: list[str] = []
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: opened.append("x") or True)
    spy = EchoSpy(auth_server)
    monkeypatch.setattr(typer, "echo", spy)

    tokens = _login(no_browser=True)

    assert opened == []
    assert tokens.access_token == auth_server.issued_access[0]
    (url,) = auth_server.opened_urls
    assert url in spy.stderr_text()
    assert url not in spy.stdout_text()
    assert url.startswith(f"{BASE}/authorize?")
    assert "code_challenge_method=S256" in url


@pytest.mark.parametrize("failure", ["returns_false", "raises"])
def test_run_login_falls_back_to_stderr_url_when_browser_cannot_open(monkeypatch, auth_server, failure):
    def failing_open(*args: Any, **kwargs: Any) -> bool:
        if failure == "raises":
            raise RuntimeError("brak przegladarki")
        return False

    monkeypatch.setattr(oauth.webbrowser, "open", failing_open)
    spy = EchoSpy(auth_server)
    monkeypatch.setattr(typer, "echo", spy)

    tokens = _login()

    assert tokens.access_token == auth_server.issued_access[0]
    (url,) = auth_server.opened_urls
    assert url in spy.stderr_text()
    assert url not in spy.stdout_text()


def test_run_login_prints_url_to_stderr_even_when_quiet(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: False)
    spy = EchoSpy(auth_server)
    monkeypatch.setattr(typer, "echo", spy)

    _login(quiet=True)

    (url,) = auth_server.opened_urls
    assert url in spy.stderr_text()


# ---------------------------------------------------------------------------
# run_login: bledy przeplywu (bez zapisu, bez wymiany kodu, serwer zamkniety)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["wrong_state", "missing_state", "denied", "server_error"])
def test_run_login_rejected_callback_raises_auth_error_without_token_exchange(monkeypatch, auth_server, mode):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server, mode=mode))

    with pytest.raises(MonolynxAuthError) as excinfo:
        _login()

    assert auth_server.token_forms == []
    assert CODE not in str(excinfo.value)
    _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])


def test_run_login_wrong_state_error_page_is_returned_to_browser(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server, mode="wrong_state"))

    with pytest.raises(MonolynxAuthError):
        _login()
    auth_server.join()

    assert auth_server.thread_errors == []
    assert len(auth_server.callback_results) == 1
    assert auth_server.callback_bodies[0][1]


def test_run_login_timeout_raises_readable_auth_error_and_closes_server(monkeypatch, auth_server):
    monkeypatch.setattr(oauth, "LOGIN_TIMEOUT", 0.5)
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: True)

    started = time.monotonic()
    with pytest.raises(MonolynxAuthError) as excinfo:
        _login()
    elapsed = time.monotonic() - started

    assert elapsed < 10
    assert str(excinfo.value).strip()
    assert auth_server.token_forms == []
    _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])


def test_run_login_closes_server_after_success(monkeypatch, auth_server):
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    _login()

    _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])


def test_run_login_registration_failure_raises_and_closes_server(monkeypatch, auth_server):
    auth_server.register_status = 500
    monkeypatch.setattr(oauth.webbrowser, "open", lambda *args, **kwargs: pytest.fail("przegladarka nie moze sie otworzyc bez client_id"))

    with pytest.raises(MonolynxError):
        _login()

    _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])


def test_run_login_token_endpoint_network_error_raises_network_error_and_closes_server(monkeypatch, auth_server):
    def boom(form: dict[str, str]) -> httpx.Response:
        raise httpx.ConnectError("polaczenie odrzucone")

    auth_server.token_override = boom
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    with pytest.raises(MonolynxNetworkError) as excinfo:
        _login()

    (form,) = auth_server.token_forms
    assert form["code_verifier"] not in str(excinfo.value)
    assert CODE not in str(excinfo.value)
    _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])


@pytest.mark.parametrize("dropped", ["access_token", "refresh_token", "expires_in"])
def test_run_login_token_response_missing_field_raises_auth_error(monkeypatch, auth_server, dropped):
    def incomplete(form: dict[str, str]) -> httpx.Response:
        payload = {"access_token": "acc-" + "a" * 20, "token_type": "Bearer", "expires_in": 3600, "refresh_token": "ref-" + "r" * 20}
        del payload[dropped]
        return httpx.Response(200, json=payload)

    auth_server.token_override = incomplete
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    with pytest.raises(MonolynxAuthError) as excinfo:
        _login()

    assert "a" * 20 not in str(excinfo.value)
    _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])


def test_run_login_idle_preconnect_does_not_block_callback_beyond_read_timeout(monkeypatch, auth_server):
    # Przegladarki otwieraja spekulatywne polaczenia TCP i nic w nich nie wysylaja; bez timeoutu odczytu
    # jednowatkowy serwer wisialby na takim polaczeniu i nie obsluzyl prawdziwego callbacku.
    monkeypatch.setattr(oauth, "CALLBACK_READ_TIMEOUT", 0.5)
    idle: list[socket.socket] = []
    real_browser = make_browser(auth_server)

    def open_with_preconnect(url: str, *args: Any, **kwargs: Any) -> bool:
        port = urllib.parse.urlparse(dict(urllib.parse.parse_qsl(urllib.parse.urlparse(url).query))["redirect_uri"]).port
        idle.append(socket.create_connection(("127.0.0.1", port), timeout=2))
        return real_browser(url)

    monkeypatch.setattr(oauth.webbrowser, "open", open_with_preconnect)

    started = time.monotonic()
    try:
        tokens = _login()
    finally:
        for sock in idle:
            sock.close()
    elapsed = time.monotonic() - started

    assert idle
    assert elapsed < 5
    assert tokens.access_token == auth_server.issued_access[0]
    _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])


@pytest.mark.parametrize("field", ["token_endpoint", "registration_endpoint"])
def test_run_login_invalid_endpoint_url_from_discovery_raises_auth_error(monkeypatch, auth_server, field):
    document = {
        "authorization_endpoint": f"{BASE}/authorize",
        "token_endpoint": f"{BASE}/token",
        "registration_endpoint": f"{BASE}/register",
    }
    document[field] = INVALID_URL
    auth_server.discovery_document = document
    monkeypatch.setattr(oauth.webbrowser, "open", make_browser(auth_server))

    with pytest.raises(MonolynxAuthError):
        _login()

    auth_server.join()
    assert "/x" not in auth_server.paths()
    if field == "token_endpoint":
        _assert_port_closed(auth_server.register_bodies[0]["redirect_uris"][0])
    else:
        assert auth_server.register_bodies == []


# ---------------------------------------------------------------------------
# save_tokens
# ---------------------------------------------------------------------------


def _token_set() -> oauth.TokenSet:
    return oauth.TokenSet(
        access_token="new-access-" + "a" * 20,
        refresh_token="new-refresh-" + "r" * 20,
        expires_at="2026-10-29T10:00:00+00:00",
        client_id="mlx_saved_client",
    )


def test_save_tokens_writes_oauth_fields_and_keeps_other_profile_fields(monkeypatch):
    config_mod.save_config(
        {
            "general": {"active_profile": "keep-me"},
            "profile": {
                "p": {"endpoint": "https://stary.test", "project": "moj-projekt", "auth_type": "token", "token": "legacy-token-1234"},
                "other": {"endpoint": "https://other.test", "token": "other-token"},
            },
        }
    )
    tokens = _token_set()

    oauth.save_tokens("p", "https://nowy.test", tokens)

    data = config_mod.load_config()
    assert data["profile"]["p"] == {
        "endpoint": "https://nowy.test",
        "project": "moj-projekt",
        "auth_type": "oauth",
        "access_token": tokens.access_token,
        "refresh_token": tokens.refresh_token,
        "expires_at": tokens.expires_at,
        "client_id": tokens.client_id,
    }
    assert data["profile"]["other"] == {"endpoint": "https://other.test", "token": "other-token"}
    assert data["general"]["active_profile"] == "keep-me"


def test_save_tokens_creates_profile_and_sets_default_active_profile_when_missing():
    tokens = _token_set()

    oauth.save_tokens("fresh", "https://x.test", tokens)

    data = config_mod.load_config()
    assert data["general"]["active_profile"] == "default"
    assert data["profile"]["fresh"]["auth_type"] == "oauth"
    assert data["profile"]["fresh"]["access_token"] == tokens.access_token


# ---------------------------------------------------------------------------
# refresh_tokens
# ---------------------------------------------------------------------------


def _seed_oauth_profile(server: FakeAuthServer, **overrides: Any) -> None:
    server.valid_refresh.add(OLD_REFRESH)
    profile = {
        "endpoint": BASE,
        "project": "keep-proj",
        "auth_type": "oauth",
        "access_token": OLD_ACCESS,
        "refresh_token": OLD_REFRESH,
        "expires_at": EXPIRED_AT,
        "client_id": "mlx_seeded_client",
    }
    profile.update(overrides)
    profile = {key: value for key, value in profile.items() if value is not None}
    config_mod.save_config(
        {
            "general": {"active_profile": "p"},
            "profile": {"p": profile, "other": {"endpoint": "https://other.test", "token": "other-token"}},
        }
    )


def test_refresh_tokens_posts_refresh_grant_and_saves_rotated_tokens(auth_server):
    _seed_oauth_profile(auth_server)

    new_access = oauth.refresh_tokens("p")

    assert new_access == auth_server.issued_access[0]
    assert auth_server.paths() == [DISCOVERY_PATH, "/token"]
    assert auth_server.token_forms == [{"grant_type": "refresh_token", "refresh_token": OLD_REFRESH, "client_id": "mlx_seeded_client"}]
    profile = config_mod.load_config()["profile"]["p"]
    assert profile["access_token"] == new_access
    assert profile["refresh_token"] == auth_server.issued_refresh[0]
    assert profile["refresh_token"] != OLD_REFRESH
    _assert_expires_in(profile["expires_at"], REFRESH_EXPIRES_IN)
    assert profile["client_id"] == "mlx_seeded_client"
    assert profile["project"] == "keep-proj"
    assert profile["auth_type"] == "oauth"
    assert profile["endpoint"] == BASE
    assert config_mod.load_config()["profile"]["other"] == {"endpoint": "https://other.test", "token": "other-token"}


def test_refresh_tokens_rotation_second_refresh_uses_the_new_refresh_token(auth_server):
    _seed_oauth_profile(auth_server)

    first = oauth.refresh_tokens("p")
    second = oauth.refresh_tokens("p")

    assert first == auth_server.issued_access[0]
    assert second == auth_server.issued_access[1]
    assert [form["refresh_token"] for form in auth_server.token_forms] == [OLD_REFRESH, auth_server.issued_refresh[0]]
    assert config_mod.load_config()["profile"]["p"]["refresh_token"] == auth_server.issued_refresh[1]


def test_refresh_tokens_passes_timeout_to_http_client(auth_server):
    _seed_oauth_profile(auth_server)

    oauth.refresh_tokens("p", timeout=7.0)

    assert auth_server.timeouts
    assert set(auth_server.timeouts) == {7.0}


def test_refresh_tokens_does_not_print_secrets(auth_server, capsys):
    _seed_oauth_profile(auth_server)

    oauth.refresh_tokens("p")

    captured = capsys.readouterr()
    for secret in (OLD_ACCESS, OLD_REFRESH, auth_server.issued_access[0], auth_server.issued_refresh[0]):
        assert secret not in captured.out
        assert secret not in captured.err


@pytest.mark.parametrize("missing", ["refresh_token", "client_id", "endpoint"])
def test_refresh_tokens_returns_none_without_request_when_profile_lacks_field(auth_server, missing):
    _seed_oauth_profile(auth_server, **{missing: None})

    assert oauth.refresh_tokens("p") is None
    assert auth_server.requests == []


def test_refresh_tokens_returns_none_for_unknown_profile(auth_server):
    _seed_oauth_profile(auth_server)

    assert oauth.refresh_tokens("nieznany") is None
    assert auth_server.requests == []


def test_refresh_tokens_invalid_grant_returns_none_and_leaves_config_untouched(auth_server):
    _seed_oauth_profile(auth_server)
    auth_server.valid_refresh.clear()
    before = config_mod.config_path().read_bytes()

    assert oauth.refresh_tokens("p") is None

    assert auth_server.paths() == [DISCOVERY_PATH, "/token"]
    assert config_mod.config_path().read_bytes() == before


def test_refresh_tokens_invalid_token_endpoint_url_returns_none_and_leaves_config_untouched(auth_server):
    _seed_oauth_profile(auth_server)
    auth_server.discovery_document = {
        "authorization_endpoint": f"{BASE}/authorize",
        "token_endpoint": INVALID_URL,
        "registration_endpoint": f"{BASE}/register",
    }
    before = config_mod.config_path().read_bytes()

    assert oauth.refresh_tokens("p") is None

    assert auth_server.paths() == [DISCOVERY_PATH]
    assert config_mod.config_path().read_bytes() == before


@pytest.mark.parametrize("failure", ["server_error", "not_json", "network"])
def test_refresh_tokens_other_failures_return_none_and_leave_config_untouched(auth_server, failure):
    _seed_oauth_profile(auth_server)

    def broken(form: dict[str, str]) -> httpx.Response:
        if failure == "server_error":
            return httpx.Response(500, json={"error": "server_error"})
        if failure == "not_json":
            return httpx.Response(200, content=b"to nie jest json", headers={"content-type": "text/plain"})
        raise httpx.ConnectError("polaczenie odrzucone")

    auth_server.token_override = broken
    before = config_mod.config_path().read_bytes()

    assert oauth.refresh_tokens("p") is None

    assert config_mod.config_path().read_bytes() == before


# ---------------------------------------------------------------------------
# make_refresh_hook
# ---------------------------------------------------------------------------


def _settings(auth_type: str | None, *, profile: str = "p") -> Settings:
    return Settings(endpoint=BASE, project=None, profile=profile, auth_type=auth_type, bearer=OLD_ACCESS)


@pytest.mark.parametrize("auth_type", ["token", None])
def test_make_refresh_hook_returns_none_for_non_oauth_settings(auth_server, auth_type):
    _seed_oauth_profile(auth_server)

    assert oauth.make_refresh_hook(_settings(auth_type)) is None


def test_make_refresh_hook_success_returns_new_access_token_and_prints_nothing(auth_server, capsys):
    _seed_oauth_profile(auth_server)

    hook = oauth.make_refresh_hook(_settings("oauth"))

    assert hook is not None
    assert hook() == auth_server.issued_access[0]
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_make_refresh_hook_refreshes_the_profile_from_settings_only(auth_server):
    _seed_oauth_profile(auth_server)
    config = config_mod.load_config()
    config["profile"]["second"] = dict(config["profile"]["p"], refresh_token="second-refresh-" + "s" * 20)
    config_mod.save_config(config)
    auth_server.valid_refresh.add("second-refresh-" + "s" * 20)

    hook = oauth.make_refresh_hook(_settings("oauth", profile="second"))
    assert hook is not None
    hook()

    data = config_mod.load_config()["profile"]
    assert data["second"]["access_token"] == auth_server.issued_access[0]
    assert data["p"]["access_token"] == OLD_ACCESS
    assert data["p"]["refresh_token"] == OLD_REFRESH


def test_make_refresh_hook_failure_prints_relogin_hint_to_stderr_and_returns_none(auth_server, capsys):
    _seed_oauth_profile(auth_server)
    auth_server.valid_refresh.clear()

    hook = oauth.make_refresh_hook(_settings("oauth"))

    assert hook is not None
    assert hook() is None
    captured = capsys.readouterr()
    assert SESSION_LOST_MESSAGE in captured.err
    assert SESSION_LOST_MESSAGE not in captured.out


def test_make_refresh_hook_passes_timeout_to_refresh(auth_server):
    _seed_oauth_profile(auth_server)

    hook = oauth.make_refresh_hook(_settings("oauth"), timeout=9.0)
    assert hook is not None
    hook()

    assert set(auth_server.timeouts) == {9.0}


# ---------------------------------------------------------------------------
# MON-228 C1/C2: odświeżanie tokenu w komendach zasobowych (proaktywne i reaktywne)
#
# Testy idą przez root app i dowodzą zachowania (żądania HTTP, zawartość configu), nie nazw
# funkcji wewnętrznych. `install_api_server` podmienia transport KAŻDEGO httpx.Client, więc
# nie zależy od tego, w którym module komenda tworzy klienta.
# ---------------------------------------------------------------------------

API_SLUG = "testproj"
API_PAGE_ID = "22222222-2222-4222-8222-222222222222"
_API_BODY = {"items": [], "page": 1, "per_page": 100, "total": 0, "total_pages": 1, "id": "x", "slug": API_SLUG, "spec_page_id": None}


def install_api_server(monkeypatch, mock_transport) -> FakeAuthServer:
    """FakeAuthServer rozszerzony o /api/v2/projects*: 200 dla ważnego access tokenu, 401 dla reszty."""
    server = FakeAuthServer()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/api/v2/projects"):
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in server.valid_access:
                return httpx.Response(
                    401,
                    json={"title": "Brak dostepu", "detail": "Token niewazny"},
                    headers={"content-type": "application/problem+json"},
                )
            return httpx.Response(200, json=_API_BODY)
        return server.handler(request)

    transport, requests = mock_transport(handler)
    server.transport = transport
    server.requests = requests
    real_init = httpx.Client.__init__

    def init(self: httpx.Client, *args: Any, **kwargs: Any) -> None:
        kwargs["transport"] = transport
        real_init(self, *args, **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", init)
    return server


@pytest.fixture
def api_server(monkeypatch, mock_transport):
    server = install_api_server(monkeypatch, mock_transport)
    yield server
    server.join()


def _at(delta: timedelta, *, z_suffix: bool = False) -> str:
    text = (datetime.now(timezone.utc) + delta).replace(microsecond=0).isoformat()  # noqa: UP017
    return text.replace("+00:00", "Z") if z_suffix else text


def _api_paths(server: FakeAuthServer) -> list[str]:
    return [request.url.path for request in server.requests]


def _run_api(runner, args: list[str]):
    from monolynx_cli.main import app

    return runner.invoke(app, ["-o", "json", *args])


_API_PROJECT = f"/api/v2/projects/{API_SLUG}"
_API_COMMANDS = [
    pytest.param(["project", "get", API_SLUG], _API_PROJECT, id="project"),
    pytest.param(["member", "list"], f"{_API_PROJECT}/members", id="member"),
    pytest.param(["wiki", "get", API_PAGE_ID], f"{_API_PROJECT}/wiki/pages/{API_PAGE_ID}", id="wiki"),
    pytest.param(["ticket", "get", "MON-1"], f"{_API_PROJECT}/tickets/MON-1", id="ticket"),
    pytest.param(["sprint", "get", "s1"], f"{_API_PROJECT}/sprints/s1", id="sprint"),
]


@pytest.mark.parametrize(("args", "api_path"), _API_COMMANDS)
def test_group_command_401_with_oauth_profile_refreshes_and_retries_with_new_token(runner, api_server, args, api_path):
    # C2: każda grupa komend (nie tylko `auth whoami`) odświeża sesję po 401.
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(timedelta(days=10)))

    result = _run_api(runner, args)

    assert result.exit_code == 0, result.output
    assert _api_paths(api_server) == [api_path, DISCOVERY_PATH, "/token", api_path]
    new_access = api_server.issued_access[0]
    assert api_server.requests[0].headers["authorization"] == f"Bearer {OLD_ACCESS}"
    assert api_server.requests[3].headers["authorization"] == f"Bearer {new_access}"
    assert api_server.token_forms == [{"grant_type": "refresh_token", "refresh_token": OLD_REFRESH, "client_id": "mlx_seeded_client"}]
    profile = config_mod.load_config()["profile"]["p"]
    assert profile["access_token"] == new_access
    assert profile["refresh_token"] == api_server.issued_refresh[0]
    assert OLD_ACCESS not in result.output
    assert new_access not in result.output


@pytest.mark.parametrize(("args", "api_path"), _API_COMMANDS)
def test_group_command_401_with_failed_refresh_exits_3_without_second_api_request(runner, api_server, args, api_path):
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(timedelta(days=10)))
    api_server.valid_refresh.clear()

    result = _run_api(runner, args)

    assert result.exit_code == 3
    assert _api_paths(api_server) == [api_path, DISCOVERY_PATH, "/token"]
    assert "monolynx auth login" in result.stderr


@pytest.mark.parametrize(("args", "api_path"), _API_COMMANDS)
def test_group_command_with_static_token_401_does_not_call_token_endpoint(runner, monkeypatch, api_server, args, api_path):
    monkeypatch.setenv("MONOLYNX_TOKEN", "statyczny-" + "s" * 20)
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(timedelta(days=10)))

    result = _run_api(runner, args)

    assert result.exit_code == 3
    assert _api_paths(api_server) == [api_path]
    assert api_server.token_forms == []


# C1: proaktywny refresh, gdy do końca ważności access tokenu zostało mniej niż 24 h


@pytest.mark.parametrize(
    "delta_and_format",
    [
        pytest.param((timedelta(hours=2), False), id="in-2h"),
        pytest.param((timedelta(hours=23), False), id="in-23h"),
        pytest.param((timedelta(hours=2), True), id="in-2h-z-suffix"),
    ],
)
@pytest.mark.parametrize(("args", "api_path"), _API_COMMANDS)
def test_expiring_access_token_is_refreshed_before_first_api_request(runner, api_server, args, api_path, delta_and_format):
    delta, z_suffix = delta_and_format
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(delta, z_suffix=z_suffix))
    api_server.valid_access.add(OLD_ACCESS)

    result = _run_api(runner, args)

    assert result.exit_code == 0, result.output
    assert _api_paths(api_server) == [DISCOVERY_PATH, "/token", api_path]
    new_access = api_server.issued_access[0]
    assert api_server.requests[2].headers["authorization"] == f"Bearer {new_access}"
    assert api_server.token_forms == [{"grant_type": "refresh_token", "refresh_token": OLD_REFRESH, "client_id": "mlx_seeded_client"}]
    profile = config_mod.load_config()["profile"]["p"]
    assert profile["access_token"] == new_access
    assert profile["refresh_token"] == api_server.issued_refresh[0]
    _assert_expires_in(profile["expires_at"], REFRESH_EXPIRES_IN)


def test_already_expired_access_token_is_refreshed_before_first_api_request(runner, api_server):
    # Stary access token nieważny: bez proaktywnego refreshu pierwsze żądanie dostałoby 401.
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=EXPIRED_AT)

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert _api_paths(api_server) == [DISCOVERY_PATH, "/token", f"{_API_PROJECT}/tickets/MON-1"]
    assert api_server.requests[2].headers["authorization"] == f"Bearer {api_server.issued_access[0]}"


@pytest.mark.parametrize(
    "expires_at",
    [
        pytest.param(_at(timedelta(days=10)), id="in-10-days"),
        pytest.param(_at(timedelta(hours=25)), id="in-25h"),
        pytest.param(None, id="no-expires-at"),
    ],
)
def test_access_token_valid_for_more_than_24h_or_without_expiry_is_not_refreshed(runner, api_server, expires_at):
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=expires_at)
    api_server.valid_access.add(OLD_ACCESS)
    before = config_mod.config_path().read_bytes()

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert _api_paths(api_server) == [f"{_API_PROJECT}/tickets/MON-1"]
    assert api_server.requests[0].headers["authorization"] == f"Bearer {OLD_ACCESS}"
    assert api_server.token_forms == []
    assert config_mod.config_path().read_bytes() == before


def test_proactive_refresh_failure_sends_request_with_old_token_and_keeps_config(runner, api_server):
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(timedelta(hours=2)))
    api_server.valid_access.add(OLD_ACCESS)
    api_server.valid_refresh.clear()
    before = config_mod.config_path().read_bytes()

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert _api_paths(api_server) == [DISCOVERY_PATH, "/token", f"{_API_PROJECT}/tickets/MON-1"]
    assert api_server.requests[2].headers["authorization"] == f"Bearer {OLD_ACCESS}"
    assert config_mod.config_path().read_bytes() == before
    assert json.loads(result.stdout) == _API_BODY


def test_proactive_refresh_network_failure_sends_request_with_old_token(runner, api_server):
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(timedelta(hours=2)))
    api_server.valid_access.add(OLD_ACCESS)
    api_server.discovery_error = httpx.ConnectError("brak sieci")

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert api_server.token_forms == []
    assert api_server.requests[-1].url.path == f"{_API_PROJECT}/tickets/MON-1"
    assert api_server.requests[-1].headers["authorization"] == f"Bearer {OLD_ACCESS}"


def test_token_profile_with_near_expires_at_is_never_refreshed(runner, api_server):
    config_mod.save_config(
        {
            "general": {"active_profile": "p"},
            "profile": {
                "p": {
                    "endpoint": BASE,
                    "project": API_SLUG,
                    "auth_type": "token",
                    "token": OLD_ACCESS,
                    "expires_at": _at(timedelta(hours=2)),
                    "refresh_token": OLD_REFRESH,
                    "client_id": "mlx_seeded_client",
                }
            },
        }
    )
    api_server.valid_access.add(OLD_ACCESS)
    api_server.valid_refresh.add(OLD_REFRESH)

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert _api_paths(api_server) == [f"{_API_PROJECT}/tickets/MON-1"]
    assert api_server.token_forms == []


def test_env_token_over_expiring_oauth_profile_is_never_refreshed(runner, monkeypatch, api_server):
    env_token = "env-token-" + "e" * 20
    monkeypatch.setenv("MONOLYNX_TOKEN", env_token)
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(timedelta(hours=2)))
    api_server.valid_access.add(env_token)
    before = config_mod.config_path().read_bytes()

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert _api_paths(api_server) == [f"{_API_PROJECT}/tickets/MON-1"]
    assert api_server.requests[0].headers["authorization"] == f"Bearer {env_token}"
    assert api_server.token_forms == []
    assert config_mod.config_path().read_bytes() == before


def test_unparsable_expires_at_does_not_crash_the_command(runner, api_server):
    # expires_at pochodzi z ręcznie edytowalnego pliku: śmieci nie mogą kończyć się tracebackiem.
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at="nie-data")
    api_server.valid_access.add(OLD_ACCESS)

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert api_server.requests[-1].url.path == f"{_API_PROJECT}/tickets/MON-1"


def test_naive_expires_at_is_treated_as_utc_and_refreshed(runner, api_server):
    naive = (datetime.now(timezone.utc) + timedelta(hours=2)).replace(tzinfo=None, microsecond=0).isoformat()  # noqa: UP017
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=naive)
    api_server.valid_access.add(OLD_ACCESS)

    result = _run_api(runner, ["ticket", "get", "MON-1"])

    assert result.exit_code == 0, result.output
    assert result.exception is None or isinstance(result.exception, SystemExit)
    assert _api_paths(api_server) == [DISCOVERY_PATH, "/token", f"{_API_PROJECT}/tickets/MON-1"]


@pytest.mark.parametrize("args", [["project", "get", ".."], ["member", "list", "--project", ".."]], ids=["project-get", "member-list"])
def test_dot_segment_slug_exits_2_before_any_request_including_token_refresh(runner, api_server, args):
    _seed_oauth_profile(api_server, project=API_SLUG, expires_at=_at(timedelta(hours=2)))
    api_server.valid_access.add(OLD_ACCESS)

    result = _run_api(runner, args)

    assert result.exit_code == 2
    assert api_server.requests == []
