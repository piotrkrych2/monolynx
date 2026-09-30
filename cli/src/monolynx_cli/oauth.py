"""Logowanie OAuth 2.1 (Authorization Code + PKCE S256, loopback redirect) i odświeżanie tokenu.

Backend porównuje `redirect_uri` dokładnie, razem z portem, więc każde logowanie robi
Dynamic Client Registration od nowa z portem wylosowanym przez system (port 0) -- klient
z poprzedniego logowania nie pasowałby do nowego redirectu. Serwer callbacku słucha
wyłącznie na 127.0.0.1 i jest zamykany zawsze, także po błędzie i po timeoucie.

Żądania do /register i /token idą przez `http_client`, a nie przez MonolynxClient: jego
tryb --debug drukuje treść odpowiedzi, a tu leżą kody, access i refresh token. Access/refresh
token, code i code_verifier nie trafiają na stdout/stderr ani do komunikatów wyjątków.
`state` jest tylko w URL-u autoryzacji, który przy --no-browser lub nieudanym otwarciu
przeglądarki wypisujemy na stderr (użytkownik musi go zobaczyć); poza tym nie trafia do
komunikatów wyjątków ani logów. Serwer callbacku nie loguje linii żądań (zawierają query
z code i state), a bezczynne połączenie wstępne przeglądarki porzuca po CALLBACK_READ_TIMEOUT,
żeby nie zablokować jednowątkowego serwera.

Refresh token jest rotowany: nowa para jest zapisywana do configu zanim access token
wróci do wołającego, bo stary refresh token po użyciu jest już nieważny.

Odświeżanie jest proaktywne (`refresh_if_expiring`: access token wygasa za mniej niż
PROACTIVE_REFRESH_WINDOW albo już wygasł, raz przed pierwszym żądaniem komendy) i reaktywne
(`make_refresh_hook`: po 401). Dotyczy tylko profilu oauth, nigdy tokenu z --token ani MONOLYNX_TOKEN.
"""

from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import re
import secrets
import socketserver
import threading
import urllib.parse
import webbrowser
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx
import typer

from monolynx_cli.client import MonolynxAPIError, MonolynxAuthError, MonolynxError, MonolynxNetworkError
from monolynx_cli.config import DEFAULT_PROFILE, ConfigError, Settings, get_profile, load_config, save_config
from monolynx_cli.output import echo_stderr
from monolynx_cli.version import __version__

LOGIN_TIMEOUT: float = 300.0
CALLBACK_READ_TIMEOUT: float = 10.0
CALLBACK_PATH = "/callback"
CLIENT_NAME = "Monolynx CLI"
LOOPBACK_HOST = "127.0.0.1"
# Refresh token ma pełny TTL przy każdej rotacji, więc odświeżenie z wyprzedzeniem przedłuża sesję
# aktywnego użytkownika bez zmiany TTL po stronie serwera.
PROACTIVE_REFRESH_WINDOW = timedelta(hours=24)

SESSION_LOST_MESSAGE = "Sesja wygasła i nie udało się jej odnowić. Zaloguj się ponownie: monolynx auth login"

_DISCOVERY_PATH = "/.well-known/oauth-authorization-server"
_REQUIRED_ENDPOINTS = ("authorization_endpoint", "token_endpoint", "registration_endpoint")
_ERROR_CODE = re.compile(r"[a-z_]{1,40}")

_PAGE = (
    '<!doctype html><html lang="pl"><head><meta charset="utf-8"><title>Monolynx CLI</title></head><body><h1>{title}</h1><p>{text}</p></body></html>'
)
_SUCCESS_PAGE = _PAGE.format(title="Zalogowano", text="Możesz zamknąć tę kartę i wrócić do terminala.")
_FAILURE_PAGE = _PAGE.format(title="Logowanie nie powiodło się", text="Wróć do terminala, aby zobaczyć szczegóły.")
_NOT_FOUND_PAGE = _PAGE.format(title="Nie znaleziono", text="Ta ścieżka nie obsługuje logowania.")


@dataclasses.dataclass(frozen=True)
class TokenSet:
    access_token: str
    refresh_token: str
    expires_at: str
    client_id: str


def http_client(timeout: float) -> httpx.Client:
    return httpx.Client(timeout=timeout, headers={"User-Agent": f"monolynx-cli/{__version__}", "Accept": "application/json"})


def generate_pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return verifier, base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def _network_error(exc: httpx.HTTPError, what: str) -> MonolynxNetworkError:
    # Tylko nazwa typu: str(exc) mógłby zawierać adres z parametrami zapytania.
    return MonolynxNetworkError(f"Błąd połączenia podczas {what} ({type(exc).__name__})")


def _invalid_endpoint(title: str) -> MonolynxAuthError:
    return MonolynxAuthError(None, title, "Nieprawidłowy adres w konfiguracji OAuth serwera.")


def _json_object(response: httpx.Response, title: str) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise MonolynxAuthError(response.status_code, title, "Odpowiedź serwera nie jest poprawnym JSON.") from exc
    if not isinstance(payload, dict):
        raise MonolynxAuthError(response.status_code, title, "Odpowiedź serwera ma nieoczekiwany kształt.")
    return payload


def discover(endpoint: str, *, timeout: float) -> dict[str, Any]:
    url = f"{endpoint.rstrip('/')}{_DISCOVERY_PATH}"
    try:
        with http_client(timeout) as client:
            response = client.get(url)
    except httpx.HTTPError as exc:
        raise _network_error(exc, "pobierania konfiguracji OAuth") from exc
    except httpx.InvalidURL as exc:
        raise MonolynxNetworkError(f"Nieprawidłowy adres API: {endpoint}") from exc

    title = "Nie udało się pobrać konfiguracji OAuth"
    if response.status_code != 200:
        raise MonolynxAuthError(response.status_code, title)
    document = _json_object(response, title)
    missing = [key for key in _REQUIRED_ENDPOINTS if not isinstance(document.get(key), str) or not document[key]]
    if missing:
        raise MonolynxAuthError(response.status_code, title, f"Brak w dokumencie: {', '.join(missing)}.")
    return document


class _CallbackServer(HTTPServer):
    expected_state: str
    outcome: tuple[str | None, str | None] | None
    done: threading.Event

    def server_bind(self) -> None:
        # HTTPServer.server_bind woła socket.getfqdn(), co potrafi blokować na DNS.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = port


class _CallbackHandler(BaseHTTPRequestHandler):
    server: _CallbackServer

    def setup(self) -> None:
        # Serwer jest jednowątkowy: preconnect przeglądarki bez żądania zablokowałby handle_request
        # i server.shutdown(). Timeout czytany przy obsłudze połączenia, nie przy imporcie; ustawiany
        # na gnieździe, bo StreamRequestHandler.timeout to zmienna klasowa (przy None setup() go nie rusza).
        self.request.settimeout(CALLBACK_READ_TIMEOUT)
        super().setup()

    def log_message(self, format: str, *args: Any) -> None:
        # Domyślna linia logu zawiera query z code i state.
        return

    def _respond(self, status: int, page: str) -> None:
        body = page.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != CALLBACK_PATH:
            self._respond(404, _NOT_FOUND_PAGE)
            return
        if self.server.outcome is not None:
            self._respond(409, _FAILURE_PAGE)
            return

        params = {key: values[0] for key, values in urllib.parse.parse_qs(parsed.query).items()}
        code, error = self._evaluate(params)
        self.server.outcome = (code, error)
        self._respond(200 if code else 400, _SUCCESS_PAGE if code else _FAILURE_PAGE)
        self.server.done.set()

    def _evaluate(self, params: dict[str, str]) -> tuple[str | None, str | None]:
        state = params.get("state")
        if state is None or not hmac.compare_digest(state.encode("utf-8"), self.server.expected_state.encode("utf-8")):
            return None, "Nieprawidłowy parametr state - odpowiedź nie pochodzi z tego logowania."
        error = params.get("error")
        if error is not None:
            if error == "access_denied":
                return None, "Odmówiono dostępu w przeglądarce."
            detail = error if _ERROR_CODE.fullmatch(error) else "nieznany błąd"
            return None, f"Serwer autoryzacji zwrócił błąd: {detail}."
        code = params.get("code")
        if not code:
            return None, "Odpowiedź serwera autoryzacji nie zawiera kodu."
        return code, None


def _register_client(registration_endpoint: str, redirect_uri: str, timeout: float) -> str:
    body = {"client_name": CLIENT_NAME, "redirect_uris": [redirect_uri], "grant_types": ["authorization_code", "refresh_token"]}
    title = "Rejestracja klienta OAuth nie powiodła się"
    try:
        with http_client(timeout) as client:
            response = client.post(registration_endpoint, json=body)
    except httpx.HTTPError as exc:
        raise _network_error(exc, "rejestracji klienta OAuth") from exc
    except httpx.InvalidURL as exc:
        raise _invalid_endpoint(title) from exc
    if response.status_code not in (200, 201):
        raise MonolynxAPIError(response.status_code, title)
    client_id = _json_object(response, title).get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise MonolynxAuthError(response.status_code, title, "Odpowiedź nie zawiera client_id.")
    return client_id


def _deliver_url(url: str, *, no_browser: bool, quiet: bool, no_color: bool) -> None:
    opened = False
    if not no_browser:
        echo_stderr("Otwieram przeglądarkę, aby dokończyć logowanie...", quiet=quiet, no_color=no_color)
        try:
            opened = bool(webbrowser.open(url))
        except Exception:
            opened = False
    if not opened:
        # typer.echo, a nie echo_stderr: bez tego adresu użytkownik nie ma jak się zalogować, więc --quiet go nie wycisza.
        typer.echo(f"Otwórz ten adres w przeglądarce, aby się zalogować:\n{url}", err=True)


def _await_code(server: _CallbackServer) -> str:
    if not server.done.wait(LOGIN_TIMEOUT):
        raise MonolynxAuthError(
            None,
            "Przekroczono czas oczekiwania",
            f"Nie dokończono logowania w przeglądarce w ciągu {LOGIN_TIMEOUT:g} s. Spróbuj ponownie: monolynx auth login",
        )
    code, error = server.outcome or (None, "Brak odpowiedzi z przeglądarki.")
    if code is None:
        raise MonolynxAuthError(None, "Logowanie nie powiodło się", error)
    return code


def _stop_server(server: _CallbackServer, thread: threading.Thread) -> None:
    try:
        if thread.ident is not None:
            server.shutdown()
    finally:
        server.server_close()
        thread.join(timeout=5)


def _expires_at(expires_in: float) -> str:
    # cli wspiera Python 3.10, gdzie datetime.UTC nie istnieje.
    return (datetime.now(timezone.utc) + timedelta(seconds=expires_in)).replace(microsecond=0).isoformat()  # noqa: UP017


def _parse_token_response(payload: dict[str, Any], client_id: str) -> TokenSet:
    access = payload.get("access_token")
    refresh = payload.get("refresh_token")
    expires_in = payload.get("expires_in")
    if not isinstance(access, str) or not access or not isinstance(refresh, str) or not refresh:
        raise _incomplete_token_response()
    if isinstance(expires_in, bool) or not isinstance(expires_in, (int, float)) or expires_in <= 0:
        raise _incomplete_token_response()
    return TokenSet(access_token=access, refresh_token=refresh, expires_at=_expires_at(expires_in), client_id=client_id)


def _incomplete_token_response() -> MonolynxAuthError:
    return MonolynxAuthError(
        None, "Nieprawidłowa odpowiedź serwera", "Odpowiedź /token nie zawiera kompletu: access_token, refresh_token, expires_in."
    )


def _exchange_code(token_endpoint: str, form: dict[str, str], client_id: str, timeout: float) -> TokenSet:
    title = "Wymiana kodu na token nie powiodła się"
    try:
        with http_client(timeout) as client:
            response = client.post(token_endpoint, data=form)
    except httpx.HTTPError as exc:
        raise _network_error(exc, "wymiany kodu na token") from exc
    except httpx.InvalidURL as exc:
        raise _invalid_endpoint(title) from exc
    if response.status_code != 200:
        raise MonolynxAuthError(response.status_code, title)
    return _parse_token_response(_json_object(response, title), client_id)


def run_login(endpoint: str, *, no_browser: bool = False, timeout: float = 30.0, quiet: bool = False, no_color: bool = False) -> TokenSet:
    document = discover(endpoint, timeout=timeout)

    state = secrets.token_urlsafe(32)
    verifier, challenge = generate_pkce()
    try:
        server = _CallbackServer((LOOPBACK_HOST, 0), _CallbackHandler)
    except OSError as exc:
        raise MonolynxError(f"Nie udało się uruchomić lokalnego serwera logowania: {exc}") from exc
    server.expected_state = state
    server.outcome = None
    server.done = threading.Event()
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)

    try:
        thread.start()
        redirect_uri = f"http://{LOOPBACK_HOST}:{server.server_port}{CALLBACK_PATH}"
        client_id = _register_client(document["registration_endpoint"], redirect_uri, timeout)
        query = urllib.parse.urlencode(
            {
                "response_type": "code",
                "client_id": client_id,
                "redirect_uri": redirect_uri,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": state,
            }
        )
        _deliver_url(f"{document['authorization_endpoint']}?{query}", no_browser=no_browser, quiet=quiet, no_color=no_color)
        echo_stderr("Czekam na zakończenie logowania w przeglądarce...", quiet=quiet, no_color=no_color)
        code = _await_code(server)
    finally:
        _stop_server(server, thread)

    form = {"grant_type": "authorization_code", "code": code, "code_verifier": verifier, "client_id": client_id, "redirect_uri": redirect_uri}
    return _exchange_code(document["token_endpoint"], form, client_id, timeout)


def save_tokens(profile: str, endpoint: str, tokens: TokenSet) -> None:
    data = load_config()
    profiles = data.setdefault("profile", {})
    target = dict(profiles.get(profile, {}))
    target.update(
        endpoint=endpoint,
        auth_type="oauth",
        access_token=tokens.access_token,
        refresh_token=tokens.refresh_token,
        expires_at=tokens.expires_at,
        client_id=tokens.client_id,
    )
    target.pop("token", None)
    profiles[profile] = target
    data.setdefault("general", {}).setdefault("active_profile", DEFAULT_PROFILE)
    save_config(data)


def refresh_tokens(profile: str, *, timeout: float = 30.0) -> str | None:
    """Odnów access token; każdy błąd (sieć, invalid_grant, zły JSON) to None, config bez zmian."""
    try:
        data = load_config()
    except ConfigError:
        return None
    stored = get_profile(profile, data)
    refresh_token, client_id, endpoint = stored.get("refresh_token"), stored.get("client_id"), stored.get("endpoint")
    if not (isinstance(refresh_token, str) and refresh_token and isinstance(client_id, str) and client_id and isinstance(endpoint, str) and endpoint):
        return None

    try:
        document = discover(endpoint, timeout=timeout)
        form = {"grant_type": "refresh_token", "refresh_token": refresh_token, "client_id": client_id}
        tokens = _exchange_code(document["token_endpoint"], form, client_id, timeout)
    except MonolynxError:
        return None

    stored.update(access_token=tokens.access_token, refresh_token=tokens.refresh_token, expires_at=tokens.expires_at)
    data["profile"][profile] = stored
    try:
        save_config(data)
    except OSError:
        return None
    return tokens.access_token


def _parse_expires_at(value: Any) -> datetime | None:
    # expires_at leży w ręcznie edytowalnym pliku; śmieci to brak daty, nie wyjątek.
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text[-1:] in ("Z", "z"):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)  # noqa: UP017


def refresh_if_expiring(settings: Settings, *, timeout: float = 30.0) -> str | None:
    """Odśwież profil oauth, gdy access token wygasa w ciągu PROACTIVE_REFRESH_WINDOW albo już wygasł.

    Zwraca nowy access token, a None gdy odświeżanie nie było potrzebne albo się nie udało:
    wtedy komenda idzie dalej ze starym tokenem, a po 401 działa hook z make_refresh_hook.
    """
    if settings.auth_type != "oauth":
        return None
    try:
        stored = get_profile(settings.profile)
    except ConfigError:
        return None
    expires_at = _parse_expires_at(stored.get("expires_at"))
    if expires_at is None or expires_at - datetime.now(timezone.utc) >= PROACTIVE_REFRESH_WINDOW:  # noqa: UP017
        return None
    return refresh_tokens(settings.profile, timeout=timeout)


def make_refresh_hook(settings: Settings, *, timeout: float = 30.0) -> Callable[[], str | None] | None:
    if settings.auth_type != "oauth":
        return None

    def hook() -> str | None:
        access = refresh_tokens(settings.profile, timeout=timeout)
        if access is None:
            typer.echo(SESSION_LOST_MESSAGE, err=True)
        return access

    return hook
