"""Klient HTTP monolynx-cli: MonolynxClient, wyjątki i mapowanie błędów `/api/v2` (RFC 7807).

Jeden klient dla wszystkich komend: nagłówki (Authorization Bearer, User-Agent, Accept),
retry z backoffem, jednorazowy hook odświeżenia tokenu po 401 i tłumaczenie odpowiedzi
błędu na wyjątki z deterministycznym `exit_code`. Globalny handler w `monolynx_cli.main`
zamienia każdy `MonolynxError` na komunikat na stderr i kod wyjścia procesu:

    1  MonolynxAPIError      błąd API (4xx poza 401/403, 429/5xx po wyczerpaniu prób)
    3  MonolynxAuthError     401/403, nieudany refresh, brak tokenu wykryty przed wysłaniem
    4  MonolynxNetworkError  timeout, odmowa połączenia, DNS (bez retry)

Retry: maks. 4 próby. GET/DELETE ponawiane na 429 i 5xx, POST/PATCH tylko na 429 --
ponowienie po 5xx mogłoby zdublować utworzony zasób. Czekanie idzie wyłącznie przez
wstrzyknięte `sleep`, dzięki czemu testy nie śpią naprawdę.

Token nie ma stałego prefiksu (legacy `osk_`, access token OAuth to goły `token_urlsafe`),
więc do logów `--debug` trafia wyłącznie `config.mask_token()`.
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from typing import Any

import httpx
from rich.console import Console

from monolynx_cli.config import mask_token
from monolynx_cli.version import __version__

BACKOFF_DELAYS: tuple[float, ...] = (0.5, 1.0, 2.0)
MAX_RETRY_AFTER: float = 60.0
DEFAULT_TIMEOUT: float = 30.0

_ERROR_BODY_LIMIT = 200
_DEBUG_BODY_LIMIT = 500
_RETRY_AFTER_SECONDS = re.compile(r"\d+(?:\.\d+)?")
_NO_TOKEN_HINT = "Zaloguj się: monolynx auth login"


class MonolynxError(Exception):
    exit_code: int = 1


class MonolynxAPIError(MonolynxError):
    exit_code = 1

    def __init__(self, status: int | None, title: str, detail: str | None = None, type_: str = "about:blank") -> None:
        head = f"{status} {title}" if status is not None else title
        super().__init__(f"{head}: {detail}" if detail else head)
        self.status = status
        self.title = title
        self.detail = detail
        self.type_ = type_


class MonolynxAuthError(MonolynxAPIError):
    exit_code = 3


class MonolynxNetworkError(MonolynxError):
    exit_code = 4

    def __init__(self, message: str) -> None:
        super().__init__(message)


def _parse_retry_after(value: str | None) -> float | None:
    # Data HTTP, wartość ujemna i nienumeryczna celowo wracają do zwykłego backoffu.
    if value is None:
        return None
    text = value.strip()
    if not _RETRY_AFTER_SECONDS.fullmatch(text):
        return None
    return min(float(text), MAX_RETRY_AFTER)


def _build_error(response: httpx.Response) -> MonolynxAPIError:
    status = response.status_code
    body = response.text.strip()
    title = response.reason_phrase or "Błąd HTTP"
    detail: str | None = body[:_ERROR_BODY_LIMIT] or None
    type_ = "about:blank"

    content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type == "application/problem+json":
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if isinstance(payload, dict):
            problem_title = payload.get("title")
            problem_detail = payload.get("detail")
            problem_type = payload.get("type")
            title = problem_title if isinstance(problem_title, str) and problem_title else title
            detail = problem_detail if isinstance(problem_detail, str) and problem_detail else None
            type_ = problem_type if isinstance(problem_type, str) and problem_type else type_

    error_class = MonolynxAuthError if status in (401, 403) else MonolynxAPIError
    return error_class(status, title, detail, type_)


class MonolynxClient:
    def __init__(
        self,
        endpoint: str,
        token: str | None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        debug: bool = False,
        refresh: Callable[[], str | None] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token
        self._debug = debug
        self._refresh = refresh
        self._sleep = sleep
        try:
            self._client = httpx.Client(
                base_url=endpoint,
                timeout=timeout,
                transport=transport,
                headers={"User-Agent": f"monolynx-cli/{__version__}", "Accept": "application/json"},
            )
        except httpx.InvalidURL as exc:
            raise MonolynxNetworkError(f"Nieprawidłowy adres API: {endpoint} ({exc})") from exc

    def __enter__(self) -> MonolynxClient:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._request("GET", path, params=params)

    def delete(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        return self._request("DELETE", path, params=params)

    def post(self, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        return self._request("POST", path, json=json, params=params)

    def patch(self, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        return self._request("PATCH", path, json=json, params=params)

    def _request(self, method: str, path: str, *, json: Any = None, params: dict[str, Any] | None = None) -> Any:
        if not self._token:
            raise MonolynxAuthError(None, "Brak tokenu", _NO_TOKEN_HINT)

        retry_5xx = method in ("GET", "DELETE")
        refreshed = False
        retries = 0
        while True:
            response = self._send(method, path, json, params)
            status = response.status_code

            if status == 401 and self._refresh is not None and not refreshed:
                refreshed = True
                new_token = self._refresh()
                if new_token is None:
                    raise _build_error(response)
                self._token = new_token
                continue

            if status < 400:
                return self._parse_success(response)

            if retries < len(BACKOFF_DELAYS) and (status == 429 or (retry_5xx and status >= 500)):
                delay = BACKOFF_DELAYS[retries]
                if status == 429:
                    retry_after = _parse_retry_after(response.headers.get("retry-after"))
                    if retry_after is not None:
                        delay = retry_after
                self._sleep(delay)
                retries += 1
                continue

            raise _build_error(response)

    def _send(self, method: str, path: str, json: Any, params: dict[str, Any] | None) -> httpx.Response:
        token = self._token or ""
        try:
            request = self._client.build_request(method, path, json=json, params=params, headers={"Authorization": f"Bearer {token}"})
            self._log_request(request, token)
            response = self._client.send(request)
        except httpx.InvalidURL as exc:
            raise MonolynxNetworkError(f"Nieprawidłowy adres żądania: {path} ({exc})") from exc
        except httpx.TransportError as exc:
            raise MonolynxNetworkError(f"Błąd połączenia z {self._client.base_url} ({type(exc).__name__}): {exc}") from exc
        self._log_response(response)
        return response

    @staticmethod
    def _parse_success(response: httpx.Response) -> Any:
        if response.status_code == 204 or not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            body = response.text.strip()[:_ERROR_BODY_LIMIT] or None
            raise MonolynxAPIError(response.status_code, "Nieprawidłowa odpowiedź serwera", body) from exc

    def _log_request(self, request: httpx.Request, token: str) -> None:
        if not self._debug:
            return
        console = Console(stderr=True, markup=False, highlight=False, emoji=False)
        console.print(f"> {request.method} {request.url}", soft_wrap=True)
        console.print(f"> Authorization: Bearer {mask_token(token)}", soft_wrap=True)
        console.print(f"> User-Agent: monolynx-cli/{__version__}", soft_wrap=True)
        console.print("> Accept: application/json", soft_wrap=True)

    def _log_response(self, response: httpx.Response) -> None:
        if not self._debug:
            return
        console = Console(stderr=True, markup=False, highlight=False, emoji=False)
        console.print(f"< {response.status_code}", soft_wrap=True)
        body = response.text.strip()[:_DEBUG_BODY_LIMIT]
        if body:
            console.print(body, soft_wrap=True)
