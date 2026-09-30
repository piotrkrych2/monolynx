"""Testy monolynx_cli.client: MonolynxClient (retry, refresh 401, mapowanie bledow RFC 7807, --debug).

Specyfikacja dla MON-126 -- modul monolynx_cli.client jeszcze nie istnieje w chwili
pisania tych testow (TDD, red phase). Kontrakt: interface.md ticketu MON-126 (sekcja A).
Transport to httpx.MockTransport z fixture `mock_transport`; czekanie miedzy probami
idzie WYLACZNIE przez wstrzykniete `sleep`, wiec zaden test nie spi naprawde i kazdy moze
sprawdzic dokladna liste opoznien.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from monolynx_cli.client import (
    BACKOFF_DELAYS,
    DEFAULT_TIMEOUT,
    MAX_RETRY_AFTER,
    MonolynxAPIError,
    MonolynxAuthError,
    MonolynxClient,
    MonolynxError,
    MonolynxNetworkError,
)
from monolynx_cli.config import mask_token
from monolynx_cli.version import __version__

pytestmark = pytest.mark.unit

ENDPOINT = "https://x.test"
TOKEN = "osk_" + "a" * 40
# secrets.token_urlsafe(48) daje 64 znaki bez prefiksu osk_.
OAUTH_TOKEN = "Zk3-" + "q" * 60

PROBLEM_CT = {"content-type": "application/problem+json"}


def _make(mock_transport, handler, *, token: str | None = TOKEN, **kwargs: Any):
    transport, requests = mock_transport(handler)
    sleeps: list[float] = []
    client = MonolynxClient(ENDPOINT, token, transport=transport, sleep=sleeps.append, **kwargs)
    return client, requests, sleeps


def _sequence(responses: list[httpx.Response]) -> Callable[[httpx.Request], httpx.Response]:
    """Kolejne wywolania dostaja kolejne odpowiedzi; po wyczerpaniu powtarza ostatnia."""
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        index = min(state["i"], len(responses) - 1)
        state["i"] += 1
        return responses[index]

    return handler


def _call(client: MonolynxClient, method: str) -> Any:
    if method == "get":
        return client.get("/api/v2/p")
    if method == "delete":
        return client.delete("/api/v2/p")
    if method == "post":
        return client.post("/api/v2/p", json={"a": 1})
    return client.patch("/api/v2/p", json={"a": 1})


def _always(status: int, **kwargs: Any) -> Callable[[httpx.Request], httpx.Response]:
    return lambda request: httpx.Response(status, **kwargs)


# ---------------------------------------------------------------------------
# Wyjatki i stale
# ---------------------------------------------------------------------------


def test_exit_codes_per_exception_class():
    assert MonolynxAPIError.exit_code == 1
    assert MonolynxAuthError.exit_code == 3
    assert MonolynxNetworkError.exit_code == 4


def test_exception_hierarchy():
    assert issubclass(MonolynxAPIError, MonolynxError)
    assert issubclass(MonolynxAuthError, MonolynxAPIError)
    assert issubclass(MonolynxNetworkError, MonolynxError)
    assert not issubclass(MonolynxNetworkError, MonolynxAPIError)
    assert issubclass(MonolynxError, Exception)


def test_module_constants_match_contract():
    assert BACKOFF_DELAYS == (0.5, 1.0, 2.0)
    assert MAX_RETRY_AFTER == 60.0
    assert DEFAULT_TIMEOUT == 30.0


def test_api_error_exposes_attributes_and_default_type():
    exc = MonolynxAPIError(404, "Not Found", "brak zasobu")

    assert exc.status == 404
    assert exc.title == "Not Found"
    assert exc.detail == "brak zasobu"
    assert exc.type_ == "about:blank"


def test_api_error_str_contains_status_title_and_detail():
    text = str(MonolynxAPIError(409, "Konflikt", "ticket juz istnieje", "urn:x:conflict"))

    assert "409" in text
    assert "Konflikt" in text
    assert "ticket juz istnieje" in text


def test_api_error_str_omits_none_status_and_none_detail():
    text = str(MonolynxAPIError(None, "Brak tokenu"))

    assert "Brak tokenu" in text
    assert "None" not in text


# ---------------------------------------------------------------------------
# Naglowki, URL, parametry, cialo
# ---------------------------------------------------------------------------


def test_request_carries_exact_bearer_user_agent_and_accept_headers(mock_transport):
    client, requests, _sleeps = _make(mock_transport, _always(200, json={"ok": True}))

    client.get("/api/v2/me")

    assert len(requests) == 1
    headers = requests[0].headers
    assert headers["authorization"] == f"Bearer {TOKEN}"
    assert headers["user-agent"] == f"monolynx-cli/{__version__}"
    assert headers["accept"] == "application/json"


@pytest.mark.parametrize("method", ["get", "delete", "post", "patch"])
def test_every_method_sends_headers_and_uses_matching_http_verb(mock_transport, method):
    client, requests, _sleeps = _make(mock_transport, _always(200, json={}))

    _call(client, method)

    assert requests[0].method == method.upper()
    assert str(requests[0].url) == f"{ENDPOINT}/api/v2/p"
    assert requests[0].headers["authorization"] == f"Bearer {TOKEN}"
    assert requests[0].headers["user-agent"] == f"monolynx-cli/{__version__}"
    assert requests[0].headers["accept"] == "application/json"


def test_get_and_delete_pass_query_params(mock_transport):
    client, requests, _sleeps = _make(mock_transport, _always(200, json={}))

    client.get("/api/v2/tickets", params={"page": 2, "status": "todo"})
    client.delete("/api/v2/tickets/1", params={"force": "1"})

    assert requests[0].url.params["page"] == "2"
    assert requests[0].url.params["status"] == "todo"
    assert requests[1].url.params["force"] == "1"


@pytest.mark.parametrize("method", ["post", "patch"])
def test_post_and_patch_send_json_body_and_params(mock_transport, method):
    client, requests, _sleeps = _make(mock_transport, _always(200, json={}))

    getattr(client, method)("/api/v2/tickets", json={"title": "Nowy", "sp": 3}, params={"dry": "1"})

    assert json.loads(requests[0].content) == {"title": "Nowy", "sp": 3}
    assert requests[0].url.params["dry"] == "1"


def test_returns_parsed_json_dict_and_list(mock_transport):
    client, _requests, _sleeps = _make(
        mock_transport,
        _sequence([httpx.Response(200, json={"a": 1}), httpx.Response(200, json=[1, 2, 3])]),
    )

    assert client.get("/api/v2/a") == {"a": 1}
    assert client.get("/api/v2/b") == [1, 2, 3]


def test_201_json_is_returned(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(201, json={"id": "x"}))

    assert client.post("/api/v2/tickets", json={}) == {"id": "x"}


def test_204_returns_none(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(204))

    assert client.delete("/api/v2/tickets/1") is None


def test_2xx_with_empty_body_returns_none(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(200, content=b""))

    assert client.get("/api/v2/x") is None


def test_2xx_with_non_json_body_raises_api_error_with_status_and_body(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(200, content=b"to nie jest json", headers={"content-type": "text/plain"}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.get("/api/v2/x")

    assert excinfo.value.status == 200
    assert excinfo.value.detail is not None
    assert "to nie jest json" in excinfo.value.detail
    assert excinfo.value.exit_code == 1
    assert not isinstance(excinfo.value, MonolynxAuthError)


# ---------------------------------------------------------------------------
# Timeout i cykl zycia
# ---------------------------------------------------------------------------


def test_timeout_is_passed_to_httpx(mock_transport):
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.extensions["timeout"]))
        return httpx.Response(200, json={})

    client, _requests, _sleeps = _make(mock_transport, handler, timeout=7.5)

    client.get("/api/v2/x")

    assert seen == [{"connect": 7.5, "read": 7.5, "write": 7.5, "pool": 7.5}]


def test_default_timeout_is_30_seconds(mock_transport):
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.extensions["timeout"]))
        return httpx.Response(200, json={})

    client, _requests, _sleeps = _make(mock_transport, handler)

    client.get("/api/v2/x")

    assert seen == [{"connect": 30.0, "read": 30.0, "write": 30.0, "pool": 30.0}]


def test_context_manager_returns_client_and_closes_it_on_exit(mock_transport):
    transport, _requests = mock_transport(_always(200, json={}))

    with MonolynxClient(ENDPOINT, TOKEN, transport=transport, sleep=lambda s: None) as client:
        assert isinstance(client, MonolynxClient)
        client.get("/api/v2/x")

    with pytest.raises(RuntimeError):
        client.get("/api/v2/x")


def test_close_closes_underlying_client(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(200, json={}))

    client.close()

    with pytest.raises(RuntimeError):
        client.get("/api/v2/x")


# ---------------------------------------------------------------------------
# Retry: liczba prob i opoznienia
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "delete"])
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_idempotent_methods_retry_5xx_with_exact_attempts_and_backoff(mock_transport, method, status):
    client, requests, sleeps = _make(mock_transport, _always(status, json={"x": 1}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        _call(client, method)

    assert len(requests) == 4
    assert sleeps == [0.5, 1.0, 2.0]
    assert excinfo.value.status == status
    assert excinfo.value.exit_code == 1


@pytest.mark.parametrize("method", ["get", "delete"])
def test_idempotent_methods_retry_429_with_exact_attempts_and_backoff(mock_transport, method):
    client, requests, sleeps = _make(mock_transport, _always(429, json={}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        _call(client, method)

    assert len(requests) == 4
    assert sleeps == [0.5, 1.0, 2.0]
    assert excinfo.value.status == 429


@pytest.mark.parametrize("method", ["post", "patch"])
@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_post_and_patch_do_not_retry_5xx(mock_transport, method, status):
    client, requests, sleeps = _make(mock_transport, _always(status, json={}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        _call(client, method)

    assert len(requests) == 1
    assert sleeps == []
    assert excinfo.value.status == status


@pytest.mark.parametrize("method", ["post", "patch"])
def test_post_and_patch_retry_429_with_exact_attempts_and_backoff(mock_transport, method):
    client, requests, sleeps = _make(mock_transport, _always(429, json={}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        _call(client, method)

    assert len(requests) == 4
    assert sleeps == [0.5, 1.0, 2.0]
    assert excinfo.value.status == 429


@pytest.mark.parametrize("method", ["post", "patch"])
def test_post_and_patch_429_then_success_returns_json_and_resends_body(mock_transport, method):
    client, requests, sleeps = _make(mock_transport, _sequence([httpx.Response(429, json={}), httpx.Response(200, json={"ok": True})]))

    assert _call(client, method) == {"ok": True}

    assert len(requests) == 2
    assert sleeps == [0.5]
    assert [json.loads(r.content) for r in requests] == [{"a": 1}, {"a": 1}]


def test_get_recovers_after_two_5xx_and_sleeps_only_twice(mock_transport):
    client, requests, sleeps = _make(
        mock_transport,
        _sequence([httpx.Response(502), httpx.Response(503), httpx.Response(200, json={"ok": True})]),
    )

    assert client.get("/api/v2/x") == {"ok": True}

    assert len(requests) == 3
    assert sleeps == [0.5, 1.0]


def test_get_recovers_on_last_allowed_attempt(mock_transport):
    client, requests, sleeps = _make(
        mock_transport,
        _sequence([httpx.Response(500)] * 3 + [httpx.Response(200, json={"ok": 1})]),
    )

    assert client.get("/api/v2/x") == {"ok": 1}

    assert len(requests) == 4
    assert sleeps == [0.5, 1.0, 2.0]


@pytest.mark.parametrize("status", [400, 404, 409, 422])
@pytest.mark.parametrize("method", ["get", "delete", "post", "patch"])
def test_4xx_other_than_401_403_429_is_not_retried(mock_transport, method, status):
    client, requests, sleeps = _make(mock_transport, _always(status, json={}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        _call(client, method)

    assert len(requests) == 1
    assert sleeps == []
    assert excinfo.value.status == status
    assert not isinstance(excinfo.value, MonolynxAuthError)


def test_last_5xx_response_defines_final_error(mock_transport):
    client, _requests, _sleeps = _make(
        mock_transport,
        _sequence(
            [
                httpx.Response(500),
                httpx.Response(502),
                httpx.Response(503),
                httpx.Response(504, json={"title": "ignorowany"}, headers=PROBLEM_CT),
            ]
        ),
    )

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.get("/api/v2/x")

    assert excinfo.value.status == 504
    assert excinfo.value.title == "ignorowany"


# ---------------------------------------------------------------------------
# Retry-After
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        ("3", 3.0),
        ("1.5", 1.5),
        ("0", 0.0),
        ("60", 60.0),
        ("61", 60.0),
        ("3600", 60.0),
    ],
)
def test_retry_after_seconds_wins_over_backoff_and_is_capped(mock_transport, header, expected):
    client, requests, sleeps = _make(
        mock_transport,
        _sequence([httpx.Response(429, headers={"Retry-After": header}), httpx.Response(200, json={"ok": 1})]),
    )

    assert client.get("/api/v2/x") == {"ok": 1}

    assert len(requests) == 2
    assert sleeps == [expected]


@pytest.mark.parametrize("header", ["abc", "-5", "-0.1", "", "Wed, 21 Oct 2099 07:28:00 GMT"])
def test_invalid_retry_after_falls_back_to_backoff(mock_transport, header):
    client, _requests, sleeps = _make(
        mock_transport,
        _sequence([httpx.Response(429, headers={"Retry-After": header}), httpx.Response(200, json={"ok": 1})]),
    )

    client.get("/api/v2/x")

    assert sleeps == [0.5]


def test_retry_after_is_used_per_attempt_and_backoff_index_follows_attempt_number(mock_transport):
    # Druga proba (n=1) bez Retry-After musi dostac BACKOFF_DELAYS[1], nie [0].
    client, _requests, sleeps = _make(
        mock_transport,
        _sequence(
            [
                httpx.Response(429, headers={"Retry-After": "3"}),
                httpx.Response(429),
                httpx.Response(429, headers={"Retry-After": "7"}),
                httpx.Response(200, json={"ok": 1}),
            ]
        ),
    )

    client.get("/api/v2/x")

    assert sleeps == [3.0, 1.0, 7.0]


def test_retry_after_on_post_429_wins_over_backoff(mock_transport):
    client, _requests, sleeps = _make(
        mock_transport,
        _sequence([httpx.Response(429, headers={"Retry-After": "4"}), httpx.Response(200, json={"ok": 1})]),
    )

    client.post("/api/v2/x", json={})

    assert sleeps == [4.0]


# ---------------------------------------------------------------------------
# Bledy sieciowe
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("method", ["get", "delete", "post", "patch"])
@pytest.mark.parametrize(
    "error_factory",
    [
        lambda request: httpx.ConnectError("polaczenie odrzucone", request=request),
        lambda request: httpx.ReadTimeout("timeout odczytu", request=request),
        lambda request: httpx.ConnectTimeout("timeout polaczenia", request=request),
    ],
    ids=["connect", "read-timeout", "connect-timeout"],
)
def test_transport_error_maps_to_network_error_without_retry(mock_transport, method, error_factory):
    def handler(request: httpx.Request) -> httpx.Response:
        raise error_factory(request)

    client, requests, sleeps = _make(mock_transport, handler)

    with pytest.raises(MonolynxNetworkError) as excinfo:
        _call(client, method)

    assert len(requests) == 1
    assert sleeps == []
    assert excinfo.value.exit_code == 4
    assert str(excinfo.value).strip()
    assert not isinstance(excinfo.value, MonolynxAPIError)


def test_network_error_after_retryable_response_is_not_retried_further(mock_transport):
    state = {"i": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        state["i"] += 1
        if state["i"] == 1:
            return httpx.Response(503)
        raise httpx.ConnectError("polaczenie odrzucone", request=request)

    client, requests, sleeps = _make(mock_transport, handler)

    with pytest.raises(MonolynxNetworkError):
        client.get("/api/v2/x")

    assert len(requests) == 2
    assert sleeps == [0.5]


# ---------------------------------------------------------------------------
# 401 / 403 / refresh
# ---------------------------------------------------------------------------


def test_401_without_refresh_raises_auth_error_immediately(mock_transport):
    client, requests, sleeps = _make(mock_transport, _always(401, json={"detail": "x"}))

    with pytest.raises(MonolynxAuthError) as excinfo:
        client.get("/api/v2/me")

    assert excinfo.value.exit_code == 3
    assert excinfo.value.status == 401
    assert len(requests) == 1
    assert sleeps == []


@pytest.mark.parametrize("method", ["get", "delete", "post", "patch"])
def test_401_raises_auth_error_for_every_method(mock_transport, method):
    client, requests, _sleeps = _make(mock_transport, _always(401))

    with pytest.raises(MonolynxAuthError):
        _call(client, method)

    assert len(requests) == 1


def test_403_raises_auth_error_and_does_not_call_refresh(mock_transport):
    calls: list[int] = []

    def refresh() -> str | None:
        calls.append(1)
        return "nowy"

    client, requests, sleeps = _make(mock_transport, _always(403, json={}), refresh=refresh)

    with pytest.raises(MonolynxAuthError) as excinfo:
        client.get("/api/v2/x")

    assert excinfo.value.status == 403
    assert excinfo.value.exit_code == 3
    assert calls == []
    assert len(requests) == 1
    assert sleeps == []


def test_auth_error_carries_problem_json_title_and_detail(mock_transport):
    client, _requests, _sleeps = _make(
        mock_transport,
        _always(
            401,
            json={"title": "Unauthorized", "detail": "Token wygasl", "type": "urn:monolynx:auth"},
            headers=PROBLEM_CT,
        ),
    )

    with pytest.raises(MonolynxAuthError) as excinfo:
        client.get("/api/v2/me")

    assert excinfo.value.title == "Unauthorized"
    assert excinfo.value.detail == "Token wygasl"
    assert excinfo.value.type_ == "urn:monolynx:auth"


def test_401_with_refresh_calls_it_once_swaps_token_and_repeats_request(mock_transport):
    calls: list[int] = []

    def refresh() -> str | None:
        calls.append(1)
        return "nowy-token-123456"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    client, requests, sleeps = _make(mock_transport, handler, refresh=refresh)

    assert client.get("/api/v2/x") == {"ok": True}

    assert calls == [1]
    assert [r.headers["authorization"] for r in requests] == [f"Bearer {TOKEN}", "Bearer nowy-token-123456"]
    assert sleeps == []


def test_refresh_repeats_post_with_same_body(mock_transport):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(201, json={"id": "1"})

    client, requests, _sleeps = _make(mock_transport, handler, refresh=lambda: "nowy-token-123456")

    assert client.post("/api/v2/tickets", json={"title": "T"}) == {"id": "1"}

    assert [r.method for r in requests] == ["POST", "POST"]
    assert [json.loads(r.content) for r in requests] == [{"title": "T"}, {"title": "T"}]


def test_refreshed_token_is_used_by_later_calls_without_second_refresh(mock_transport):
    calls: list[int] = []

    def refresh() -> str | None:
        calls.append(1)
        return "nowy-token-123456"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == f"Bearer {TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    client, requests, _sleeps = _make(mock_transport, handler, refresh=refresh)

    client.get("/api/v2/a")
    client.get("/api/v2/b")

    assert calls == [1]
    assert [r.headers["authorization"] for r in requests] == [
        f"Bearer {TOKEN}",
        "Bearer nowy-token-123456",
        "Bearer nowy-token-123456",
    ]


def test_refresh_returning_none_raises_auth_error_and_is_not_called_twice(mock_transport):
    calls: list[int] = []

    def refresh() -> str | None:
        calls.append(1)
        return None

    client, requests, _sleeps = _make(mock_transport, _always(401), refresh=refresh)

    with pytest.raises(MonolynxAuthError) as excinfo:
        client.get("/api/v2/x")

    assert excinfo.value.exit_code == 3
    assert calls == [1]
    assert len(requests) == 1


def test_second_401_after_successful_refresh_raises_auth_error_without_second_refresh(mock_transport):
    calls: list[int] = []

    def refresh() -> str | None:
        calls.append(1)
        return "nowy-token-123456"

    client, requests, _sleeps = _make(mock_transport, _always(401), refresh=refresh)

    with pytest.raises(MonolynxAuthError) as excinfo:
        client.get("/api/v2/x")

    assert excinfo.value.status == 401
    assert calls == [1]
    assert len(requests) == 2


def test_refresh_budget_is_per_method_call(mock_transport):
    # "jeszcze nie uzyty w tym wywolaniu metody": kolejne wywolanie moze odswiezyc ponownie.
    calls: list[int] = []
    tokens = iter(["drugi-token-123456", "trzeci-token-123456"])

    def refresh() -> str | None:
        calls.append(1)
        return next(tokens)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] in {f"Bearer {TOKEN}", "Bearer drugi-token-123456"}:
            return httpx.Response(401)
        return httpx.Response(200, json={"ok": True})

    client, _requests, _sleeps = _make(mock_transport, handler, refresh=refresh)

    # Pierwsze wywolanie: TOKEN -> 401 -> refresh (drugi) -> 401 -> AuthError.
    with pytest.raises(MonolynxAuthError):
        client.get("/api/v2/a")
    # Drugie wywolanie startuje z tokenem "drugi" -> 401 -> nowy refresh (trzeci) -> 200.
    assert client.get("/api/v2/b") == {"ok": True}

    assert calls == [1, 1]


def test_401_does_not_consume_retry_budget(mock_transport):
    # 3 ponowienia 5xx (4 proby) + 401 + powtorzenie po refreshu = 5 requestow, sukces.
    def handler_factory():
        state = {"i": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            state["i"] += 1
            if state["i"] <= 3:
                return httpx.Response(503)
            if request.headers["authorization"] == f"Bearer {TOKEN}":
                return httpx.Response(401)
            return httpx.Response(200, json={"ok": True})

        return handler

    client, requests, sleeps = _make(mock_transport, handler_factory(), refresh=lambda: "nowy-token-123456")

    assert client.get("/api/v2/x") == {"ok": True}

    assert len(requests) == 5
    assert sleeps == [0.5, 1.0, 2.0]


@pytest.mark.parametrize("token", [None, ""], ids=["none", "pusty"])
def test_missing_or_empty_token_raises_auth_error_without_any_request(mock_transport, token):
    client, requests, sleeps = _make(mock_transport, _always(200, json={}), token=token)

    with pytest.raises(MonolynxAuthError) as excinfo:
        client.get("/api/v2/me")

    assert excinfo.value.status is None
    assert excinfo.value.exit_code == 3
    assert requests == []
    assert sleeps == []


@pytest.mark.parametrize("token", [None, ""], ids=["none", "pusty"])
@pytest.mark.parametrize("method", ["get", "delete", "post", "patch"])
def test_missing_or_empty_token_raises_auth_error_for_every_method_without_request(mock_transport, method, token):
    client, requests, _sleeps = _make(mock_transport, _always(200, json={}), token=token)

    with pytest.raises(MonolynxAuthError):
        _call(client, method)

    assert requests == []


# ---------------------------------------------------------------------------
# Mapowanie bledow (problem+json vs reszta)
# ---------------------------------------------------------------------------


def test_problem_json_maps_to_api_error_fields(mock_transport):
    client, _requests, _sleeps = _make(
        mock_transport,
        _always(
            404,
            json={"title": "Not Found", "detail": "Ticket MON-1 nie istnieje", "type": "urn:monolynx:not-found"},
            headers=PROBLEM_CT,
        ),
    )

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.get("/api/v2/tickets/1")

    exc = excinfo.value
    assert exc.status == 404
    assert exc.title == "Not Found"
    assert exc.detail == "Ticket MON-1 nie istnieje"
    assert exc.type_ == "urn:monolynx:not-found"
    assert exc.exit_code == 1
    assert not isinstance(exc, MonolynxAuthError)


def test_problem_json_with_charset_parameter_is_parsed(mock_transport):
    client, _requests, _sleeps = _make(
        mock_transport,
        _always(
            422,
            content=json.dumps({"title": "Walidacja", "detail": "pole title wymagane"}).encode(),
            headers={"content-type": "application/problem+json; charset=utf-8"},
        ),
    )

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.post("/api/v2/tickets", json={})

    assert excinfo.value.title == "Walidacja"
    assert excinfo.value.detail == "pole title wymagane"


def test_problem_json_without_type_defaults_to_about_blank(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(409, json={"title": "Konflikt", "detail": "d"}, headers=PROBLEM_CT))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.post("/api/v2/x", json={})

    assert excinfo.value.type_ == "about:blank"


def test_problem_json_status_comes_from_http_response_not_body(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(422, json={"title": "T", "status": 999}, headers=PROBLEM_CT))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.post("/api/v2/x", json={})

    assert excinfo.value.status == 422


def test_plain_application_json_error_is_not_treated_as_problem_json(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(404, json={"title": "Wlasny tytul", "detail": "wlasny detail"}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.get("/api/v2/x")

    assert excinfo.value.status == 404
    assert excinfo.value.title == "Not Found"
    assert excinfo.value.title != "Wlasny tytul"


def test_html_502_maps_to_api_error_with_truncated_body_and_no_parse_exception(mock_transport):
    body = "<html>" + "x" * 5000 + "</html>"
    client, requests, _sleeps = _make(mock_transport, _always(502, content=body.encode(), headers={"content-type": "text/html"}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.post("/api/v2/x", json={})

    assert len(requests) == 1
    assert excinfo.value.status == 502
    assert excinfo.value.title == "Bad Gateway"
    assert excinfo.value.detail is not None
    assert len(excinfo.value.detail) == 200
    assert excinfo.value.detail == body[:200]


def test_html_502_on_get_is_retried_then_maps_to_api_error(mock_transport):
    client, requests, _sleeps = _make(mock_transport, _always(502, content=b"<html>Bad Gateway</html>", headers={"content-type": "text/html"}))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.get("/api/v2/x")

    assert len(requests) == 4
    assert excinfo.value.status == 502
    assert excinfo.value.detail == "<html>Bad Gateway</html>"


def test_empty_error_body_gives_none_detail_and_reason_phrase_title(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(500, content=b""))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.post("/api/v2/x", json={})

    assert excinfo.value.status == 500
    assert excinfo.value.title == "Internal Server Error"
    assert excinfo.value.detail is None


def test_problem_json_content_type_with_invalid_json_maps_to_api_error(mock_transport):
    client, _requests, _sleeps = _make(mock_transport, _always(400, content=b"{zepsute", headers=PROBLEM_CT))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.post("/api/v2/x", json={})

    assert excinfo.value.status == 400
    assert excinfo.value.title == "Bad Request"
    assert excinfo.value.detail == "{zepsute"


@pytest.mark.parametrize("payload", [[1, 2], "tekst", 42, None])
def test_problem_json_content_type_with_non_dict_json_maps_to_api_error(mock_transport, payload):
    client, _requests, _sleeps = _make(mock_transport, _always(400, content=json.dumps(payload).encode(), headers=PROBLEM_CT))

    with pytest.raises(MonolynxAPIError) as excinfo:
        client.post("/api/v2/x", json={})

    assert excinfo.value.status == 400
    assert excinfo.value.title == "Bad Request"
    assert excinfo.value.detail == json.dumps(payload)


def test_client_module_does_not_print_to_stdout_on_errors(mock_transport, capsys):
    client, _requests, _sleeps = _make(mock_transport, _always(404, json={"title": "T"}, headers=PROBLEM_CT))

    with pytest.raises(MonolynxAPIError):
        client.get("/api/v2/x")

    assert capsys.readouterr().out == ""


# ---------------------------------------------------------------------------
# --debug: request/response na stderr, token zawsze zamaskowany
# ---------------------------------------------------------------------------


@pytest.fixture
def wide_console(monkeypatch):
    # Rich zawija linie poza TTY do 80 kolumn; dlugie body/tokeny rozbilyby substring-match.
    monkeypatch.setenv("COLUMNS", "2000")


@pytest.mark.parametrize("token", [TOKEN, OAUTH_TOKEN], ids=["osk", "oauth-bez-prefiksu"])
def test_debug_logs_request_and_response_on_stderr_with_masked_token(mock_transport, capsys, wide_console, token):
    client, _requests, _sleeps = _make(mock_transport, _always(200, json={"email": "d@example.com"}), token=token, debug=True)

    client.get("/api/v2/me")

    captured = capsys.readouterr()
    err = captured.err
    assert "> GET https://x.test/api/v2/me" in err
    assert f"> Authorization: Bearer {mask_token(token)}" in err
    assert f"> User-Agent: monolynx-cli/{__version__}" in err
    assert "> Accept: application/json" in err
    assert "< 200" in err
    assert "d@example.com" in err
    assert token not in captured.out
    assert token not in captured.err.replace("\n", "")
    assert captured.out == ""


def test_debug_masks_short_token_completely(mock_transport, capsys, wide_console):
    short = "krotki"
    client, _requests, _sleeps = _make(mock_transport, _always(200, json={}), token=short, debug=True)

    client.get("/api/v2/me")

    err = capsys.readouterr().err
    assert f"Authorization: Bearer {mask_token(short)}" in err
    assert f"Bearer {short}" not in err


@pytest.mark.parametrize("token", [TOKEN, OAUTH_TOKEN], ids=["osk", "oauth-bez-prefiksu"])
def test_debug_error_response_is_logged_and_token_not_leaked(mock_transport, capsys, wide_console, token):
    client, _requests, _sleeps = _make(
        mock_transport,
        _always(401, json={"title": "Unauthorized", "detail": "zly token"}, headers=PROBLEM_CT),
        token=token,
        debug=True,
    )

    with pytest.raises(MonolynxAuthError):
        client.get("/api/v2/me")

    captured = capsys.readouterr()
    assert "< 401" in captured.err
    assert "zly token" in captured.err
    assert token not in captured.out
    assert token not in captured.err.replace("\n", "")


def test_debug_does_not_leak_old_or_refreshed_token(mock_transport, capsys, wide_console):
    new_token = "N3w-" + "r" * 60

    def handler(request: httpx.Request) -> httpx.Response:
        if request.headers["authorization"] == f"Bearer {OAUTH_TOKEN}":
            return httpx.Response(401)
        return httpx.Response(200, json={})

    client, _requests, _sleeps = _make(mock_transport, handler, token=OAUTH_TOKEN, debug=True, refresh=lambda: new_token)

    client.get("/api/v2/me")

    captured = capsys.readouterr()
    flat_err = captured.err.replace("\n", "")
    assert OAUTH_TOKEN not in flat_err
    assert new_token not in flat_err
    assert f"Bearer {mask_token(OAUTH_TOKEN)}" in flat_err
    assert f"Bearer {mask_token(new_token)}" in flat_err
    assert OAUTH_TOKEN not in captured.out
    assert new_token not in captured.out


def test_debug_truncates_response_body_to_500_characters(mock_transport, capsys, wide_console):
    client, _requests, _sleeps = _make(mock_transport, _always(200, content=b"Q" * 2000, headers={"content-type": "application/json"}), debug=True)

    with pytest.raises(MonolynxAPIError):
        client.get("/api/v2/x")

    err = capsys.readouterr().err.replace("\n", "")
    assert "Q" * 500 in err
    assert "Q" * 501 not in err


def test_debug_does_not_interpret_rich_markup_in_body(mock_transport, capsys, wide_console):
    client, _requests, _sleeps = _make(
        mock_transport, _always(400, content=b"[bold]x[/bold] a[/b]c", headers={"content-type": "text/plain"}), debug=True
    )

    with pytest.raises(MonolynxAPIError):
        client.post("/api/v2/x", json={})

    assert "[bold]x[/bold] a[/b]c" in capsys.readouterr().err


def test_debug_disabled_writes_nothing_to_stdout_or_stderr(mock_transport, capsys):
    client, _requests, _sleeps = _make(mock_transport, _always(200, json={"a": 1}))

    client.get("/api/v2/me")

    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""
