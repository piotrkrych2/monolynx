"""MON-270: uprawnienie `blog:write` na koncie użytkownika.

Trzy części, wszystkie bez SDK MCP po stronie klienta:
- helpery `has_blog_permission` / `ensure_blog_permission` z `services/permissions.py` na realnej bazie
  (K5/K6: aktywny superuser albo aktywne konto z `can_edit_blog`, nieistniejące i nieaktywne konto odmowa),
- czysta funkcja `_hide_blog_write_tools` i stała `BLOG_WRITE_TOOLS` z `mcp_server.py` (K3),
- zachowanie `_MCPBearerAuthMiddleware` na fałszywej aplikacji ASGI: lista tooli przefiltrowana tylko dla
  konta bez uprawnienia, `content-length` poprawiony, cudze odpowiedzi nietknięte. Middleware decyduje
  na podstawie obiektu `User` zwróconego przez `_verify_token` (patchowanego jak w `test_mcp_transport_auth.py`).
"""

from __future__ import annotations

import json
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from monolynx.models.user import User
from monolynx.services.permissions import ensure_blog_permission, has_blog_permission

pytestmark = pytest.mark.unit

BLOG_WRITE_NAMES = ("set_blog_post_meta", "publish_blog_post", "unpublish_blog_post")
BLOG_READ_NAMES = ("list_blog_posts", "get_blog_post", "lint_blog_post")


async def _make_user(db, tag: str, *, can_edit_blog: bool = False, is_superuser: bool = False, is_active: bool = True) -> User:
    user = User(
        email=f"blogperm-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        is_superuser=is_superuser,
        is_active=is_active,
        can_edit_blog=can_edit_blog,
    )
    db.add(user)
    await db.flush()
    return user


class TestUserModelDefault:
    async def test_can_edit_blog_defaults_to_false_after_flush(self, db_session):
        user = User(email=f"blogperm-default-{uuid.uuid4().hex[:8]}@example.com")
        db_session.add(user)
        await db_session.flush()
        await db_session.refresh(user)

        assert user.can_edit_blog is False


class TestHasBlogPermission:
    async def test_plain_account_has_no_permission(self, db_session):
        user = await _make_user(db_session, "plain")

        assert await has_blog_permission(db_session, user.id) is False

    async def test_account_with_flag_has_permission(self, db_session):
        user = await _make_user(db_session, "flag", can_edit_blog=True)

        assert await has_blog_permission(db_session, user.id) is True

    async def test_superuser_without_flag_has_permission(self, db_session):
        user = await _make_user(db_session, "super", is_superuser=True)

        assert await has_blog_permission(db_session, user.id) is True

    async def test_unknown_user_has_no_permission(self, db_session):
        assert await has_blog_permission(db_session, uuid.uuid4()) is False

    async def test_inactive_account_with_flag_has_no_permission(self, db_session):
        user = await _make_user(db_session, "inactive-flag", can_edit_blog=True, is_active=False)

        assert await has_blog_permission(db_session, user.id) is False

    async def test_inactive_superuser_has_no_permission(self, db_session):
        user = await _make_user(db_session, "inactive-super", is_superuser=True, is_active=False)

        assert await has_blog_permission(db_session, user.id) is False

    async def test_revoking_the_flag_takes_effect_on_next_check(self, db_session):
        user = await _make_user(db_session, "revoke", can_edit_blog=True)
        assert await has_blog_permission(db_session, user.id) is True

        user.can_edit_blog = False
        await db_session.flush()

        assert await has_blog_permission(db_session, user.id) is False


class TestEnsureBlogPermission:
    async def test_plain_account_gets_exact_message(self, db_session):
        user = await _make_user(db_session, "ens-plain")

        with pytest.raises(ValueError) as exc_info:
            await ensure_blog_permission(db_session, user.id)

        assert str(exc_info.value) == "Brak uprawnienia blog:write"

    async def test_unknown_user_gets_exact_message(self, db_session):
        with pytest.raises(ValueError) as exc_info:
            await ensure_blog_permission(db_session, uuid.uuid4())

        assert str(exc_info.value) == "Brak uprawnienia blog:write"

    async def test_inactive_account_gets_exact_message(self, db_session):
        user = await _make_user(db_session, "ens-inactive", can_edit_blog=True, is_active=False)

        with pytest.raises(ValueError) as exc_info:
            await ensure_blog_permission(db_session, user.id)

        assert str(exc_info.value) == "Brak uprawnienia blog:write"

    async def test_account_with_flag_passes(self, db_session):
        user = await _make_user(db_session, "ens-flag", can_edit_blog=True)

        assert await ensure_blog_permission(db_session, user.id) is None

    async def test_superuser_without_flag_passes(self, db_session):
        user = await _make_user(db_session, "ens-super", is_superuser=True)

        assert await ensure_blog_permission(db_session, user.id) is None


def _tool(name: str) -> dict:
    return {"name": name, "description": f"Opis {name}", "inputSchema": {"type": "object", "properties": {}}}


def _tools_list_body(names: tuple[str, ...] | list[str], *, request_id: int = 7) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "result": {"tools": [_tool(n) for n in names]}}).encode()


_ALL_NAMES = ("list_projects", *BLOG_READ_NAMES, *BLOG_WRITE_NAMES, "create_ticket", "create_wiki_page")


class TestBlogWriteToolsConstant:
    def test_is_exactly_the_three_write_tools(self):
        from monolynx.mcp_server import BLOG_WRITE_TOOLS

        assert frozenset(BLOG_WRITE_NAMES) == BLOG_WRITE_TOOLS
        assert isinstance(BLOG_WRITE_TOOLS, frozenset)

    async def test_registry_still_holds_every_blog_tool(self):
        from monolynx.mcp_server import BLOG_WRITE_TOOLS, mcp

        registered = {tool.name for tool in await mcp.list_tools()}

        assert registered >= BLOG_WRITE_TOOLS
        assert set(BLOG_READ_NAMES) <= registered


class TestHideBlogWriteTools:
    def test_removes_exactly_the_three_write_tools_and_keeps_order_of_the_rest(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        result = json.loads(_hide_blog_write_tools(_tools_list_body(_ALL_NAMES)))

        assert [t["name"] for t in result["result"]["tools"]] == ["list_projects", *BLOG_READ_NAMES, "create_ticket", "create_wiki_page"]

    def test_keeps_envelope_and_tool_payloads_intact(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        result = json.loads(_hide_blog_write_tools(_tools_list_body(_ALL_NAMES, request_id=42)))

        assert result["jsonrpc"] == "2.0"
        assert result["id"] == 42
        assert result["result"]["tools"][0] == _tool("list_projects")

    @pytest.mark.parametrize("name", BLOG_WRITE_NAMES)
    def test_each_write_tool_is_removed_on_its_own(self, name):
        from monolynx.mcp_server import _hide_blog_write_tools

        result = json.loads(_hide_blog_write_tools(_tools_list_body(["list_projects", name])))

        assert [t["name"] for t in result["result"]["tools"]] == ["list_projects"]

    def test_read_tools_and_wiki_tools_stay_visible(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        names = [*BLOG_READ_NAMES, "create_wiki_page", "update_wiki_page"]
        body = _tools_list_body(names)

        result = json.loads(_hide_blog_write_tools(body))

        assert [t["name"] for t in result["result"]["tools"]] == names

    def test_empty_tools_list_stays_empty(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        result = json.loads(_hide_blog_write_tools(_tools_list_body([])))

        assert result["result"]["tools"] == []

    @pytest.mark.parametrize(
        "body",
        [
            b"",
            b"not json at all",
            b"\xff\xfe\x00",
            b"null",
            b'"tools"',
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26", "capabilities": {}}}).encode(),
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"content": [{"type": "text", "text": "publish_blog_post"}]}}).encode(),
            json.dumps({"jsonrpc": "2.0", "id": 1, "error": {"code": -32600, "message": "publish_blog_post"}}).encode(),
            json.dumps({"error": "unauthorized"}).encode(),
            json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": "publish_blog_post"}}).encode(),
        ],
        ids=[
            "empty",
            "text",
            "binary",
            "json-null",
            "json-string",
            "initialize-result",
            "tools-call-result",
            "jsonrpc-error",
            "plain-error-object",
            "tools-not-a-list",
        ],
    )
    def test_any_other_body_is_returned_unchanged(self, body):
        from monolynx.mcp_server import _hide_blog_write_tools

        assert _hide_blog_write_tools(body) == body


def _scope(token: str = "tok") -> dict:
    return {
        "type": "http",
        "method": "POST",
        "path": "/mcp",
        "headers": [(b"authorization", f"Bearer {token}".encode()), (b"content-type", b"application/json")],
    }


def _fake_app(body_chunks: list[bytes], *, status: int = 200, extra_headers: list[tuple[bytes, bytes]] | None = None):
    total = sum(len(c) for c in body_chunks)

    async def app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(total).encode()), *(extra_headers or [])],
            }
        )
        for index, chunk in enumerate(body_chunks):
            await send({"type": "http.response.body", "body": chunk, "more_body": index < len(body_chunks) - 1})

    return app


def _rpc(method: str, *, request_id: int = 1) -> bytes:
    return json.dumps({"jsonrpc": "2.0", "id": request_id, "method": method}).encode()


def _receive_from(chunks: list[bytes]):
    """`receive` ASGI oddające ciało żądania po kawałku (`more_body`), a potem `http.disconnect`."""
    messages = [{"type": "http.request", "body": chunk, "more_body": index < len(chunks) - 1} for index, chunk in enumerate(chunks)]
    pending = iter(messages or [{"type": "http.request", "body": b"", "more_body": False}])

    async def receive():
        try:
            return next(pending)
        except StopIteration:
            return {"type": "http.disconnect"}

    return receive


async def _drive_raw(middleware_app, user, request_chunks: list[bytes] | None = None, scope=None) -> list[dict]:
    """Przepuść żądanie przez middleware i zwróć surowe komunikaty `send`, w kolejności wysłania.

    Domyślne żądanie to `tools/list`: tylko takie odpowiedź middleware filtruje (MON-271).
    """
    from monolynx.mcp_server import _MCPBearerAuthMiddleware

    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    middleware = _MCPBearerAuthMiddleware(middleware_app)
    receive = _receive_from([_rpc("tools/list")] if request_chunks is None else request_chunks)
    with patch("monolynx.mcp_server._verify_token", AsyncMock(return_value=user)):
        await middleware(scope or _scope(), receive, send)
    return sent


async def _drive(middleware_app, user, scope=None, request_chunks: list[bytes] | None = None):
    sent = await _drive_raw(middleware_app, user, request_chunks, scope)
    start = next(m for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return start, dict((k.lower(), v) for k, v in start["headers"]), body


def _plain_user() -> User:
    return User(id=uuid.uuid4(), email="plain@example.com", is_superuser=False, can_edit_blog=False, is_active=True)


def _flag_user() -> User:
    return User(id=uuid.uuid4(), email="flag@example.com", is_superuser=False, can_edit_blog=True, is_active=True)


def _super_user() -> User:
    return User(id=uuid.uuid4(), email="super@example.com", is_superuser=True, can_edit_blog=False, is_active=True)


class TestMiddlewareToolListFilter:
    async def test_account_without_permission_gets_list_without_blog_write_tools(self):
        _, _, body = await _drive(_fake_app([_tools_list_body(_ALL_NAMES)]), _plain_user())

        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        assert names == ["list_projects", *BLOG_READ_NAMES, "create_ticket", "create_wiki_page"]

    async def test_content_length_matches_the_filtered_body(self):
        original = _tools_list_body(_ALL_NAMES)

        _, headers, body = await _drive(_fake_app([original]), _plain_user())

        assert len(body) < len(original)
        assert headers[b"content-length"] == str(len(body)).encode()

    async def test_account_with_flag_gets_full_list_and_untouched_length(self):
        original = _tools_list_body(_ALL_NAMES)

        _, headers, body = await _drive(_fake_app([original]), _flag_user())

        assert body == original
        assert headers[b"content-length"] == str(len(original)).encode()

    async def test_superuser_without_flag_gets_full_list(self):
        original = _tools_list_body(_ALL_NAMES)

        _, _, body = await _drive(_fake_app([original]), _super_user())

        assert body == original

    async def test_filter_applies_to_a_body_split_into_chunks(self):
        original = _tools_list_body(_ALL_NAMES)
        middle = len(original) // 2

        _, headers, body = await _drive(_fake_app([original[:middle], original[middle:]]), _plain_user())

        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        assert not set(BLOG_WRITE_NAMES) & set(names)
        assert "list_projects" in names
        assert headers[b"content-length"] == str(len(body)).encode()

    async def test_status_and_other_headers_are_preserved(self):
        app = _fake_app([_tools_list_body(_ALL_NAMES)], status=200, extra_headers=[(b"mcp-session-id", b"abc")])

        start, headers, _ = await _drive(app, _plain_user())

        assert start["status"] == 200
        assert headers[b"mcp-session-id"] == b"abc"
        assert headers[b"content-type"] == b"application/json"

    async def test_tool_call_response_of_account_without_permission_is_not_altered(self):
        call_body = json.dumps({"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "Brak uprawnienia blog:write"}]}}).encode()

        _, headers, body = await _drive(_fake_app([call_body]), _plain_user(), request_chunks=[_rpc("tools/call")])

        assert body == call_body
        assert headers[b"content-length"] == str(len(call_body)).encode()

    async def test_error_status_response_is_not_altered(self):
        error_body = json.dumps({"error": "boom"}).encode()

        start, _, body = await _drive(_fake_app([error_body], status=500), _plain_user())

        assert start["status"] == 500
        assert body == error_body

    async def test_missing_token_still_gets_401_before_any_filtering(self):
        from monolynx.mcp_server import _MCPBearerAuthMiddleware

        sent: list[dict] = []
        reached = AsyncMock()

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}

        await _MCPBearerAuthMiddleware(reached)(scope, receive, send)

        assert sent[0]["status"] == 401
        reached.assert_not_awaited()


class TestHideBlogWriteToolsBodyShape:
    def test_list_without_blog_write_tools_is_returned_byte_identical(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"tools": [_tool("list_projects"), _tool("get_blog_post")]}}, indent=2).encode()

        assert _hide_blog_write_tools(body) == body

    def test_non_dict_entries_are_kept_while_blog_write_tools_are_removed(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"tools": ["surowy-wpis", _tool("publish_blog_post"), _tool("list_projects")]}}
        ).encode()

        tools = json.loads(_hide_blog_write_tools(body))["result"]["tools"]

        assert tools == ["surowy-wpis", _tool("list_projects")]


class TestMiddlewareStreamsEventStream:
    async def test_event_stream_of_account_without_permission_passes_chunk_by_chunk_unchanged(self):
        from monolynx.mcp_server import _MCPBearerAuthMiddleware

        events: list[tuple] = []
        first = b'event: message\ndata: {"result": {"tools": [{"name": "publish_blog_post"}]}}\n\n'
        second = b"event: message\ndata: koniec\n\n"

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"content-type", b"text/event-stream")]})
            events.append(("inner", "after-start"))
            await send({"type": "http.response.body", "body": first, "more_body": True})
            events.append(("inner", "after-first"))
            await send({"type": "http.response.body", "body": second, "more_body": False})

        receive = _receive_from([_rpc("tools/list")])

        async def send(message):
            if message["type"] == "http.response.start":
                events.append(("sent", "start", message["status"], dict(message["headers"]).get(b"content-length")))
            else:
                events.append(("sent", "body", message["body"], message.get("more_body", False)))

        with patch("monolynx.mcp_server._verify_token", AsyncMock(return_value=_plain_user())):
            await _MCPBearerAuthMiddleware(app)(_scope(), receive, send)

        assert events == [
            ("sent", "start", 200, None),
            ("inner", "after-start"),
            ("sent", "body", first, True),
            ("inner", "after-first"),
            ("sent", "body", second, False),
        ]

    async def test_content_type_header_name_is_matched_case_insensitively(self):
        original = _tools_list_body(_ALL_NAMES)

        async def app(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": [(b"Content-Type", b"application/json")]})
            await send({"type": "http.response.body", "body": original, "more_body": False})

        _, _, body = await _drive(app, _plain_user())

        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        assert not set(BLOG_WRITE_NAMES) & set(names)


def _app_reading_request(response_chunks: list[bytes], received: list[dict]):
    """Aplikacja ASGI, która czyta całe ciało żądania przez `receive` (jak SDK MCP), zapisuje komunikaty i odpowiada JSON-em."""
    inner = _fake_app(response_chunks)

    async def app(scope, receive, send):
        while True:
            message = await receive()
            received.append(message)
            if message["type"] != "http.request" or not message.get("more_body", False):
                break
        await inner(scope, receive, send)

    return app


def _request_padded_to(size: int, method: str) -> bytes:
    prefix = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": {"pad": ""}}).encode()
    body = prefix.replace(b'"pad": ""', b'"pad": "' + b"x" * (size - len(prefix)) + b'"')
    assert len(body) == size
    return body


class TestMiddlewareFiltersOnlyToolsListRequests:
    """MON-271: odpowiedź `tools/call` konta bez uprawnienia nie jest buforowana ani przepisywana."""

    async def test_tools_call_response_is_forwarded_message_by_message_without_buffering(self):
        first = b'{"jsonrpc": "2.0", "id": 3, '
        second = b'"result": {"content": [{"type": "text", "text": "Brak uprawnienia blog:write"}]}}'
        app = _fake_app([first, second], extra_headers=[(b"mcp-session-id", b"abc")])
        expected = await _drive_raw(app, _super_user(), [_rpc("tools/call", request_id=3)])

        sent = await _drive_raw(app, _plain_user(), [_rpc("tools/call", request_id=3)])

        assert sent == expected
        assert [m["body"] for m in sent if m["type"] == "http.response.body"] == [first, second]

    async def test_tools_call_response_shaped_like_a_tool_list_keeps_its_bytes_and_content_length(self):
        original = _tools_list_body(_ALL_NAMES)

        _, headers, body = await _drive(_fake_app([original]), _plain_user(), request_chunks=[_rpc("tools/call")])

        assert body == original
        assert headers[b"content-length"] == str(len(original)).encode()

    async def test_other_methods_are_not_buffered_either(self):
        original = _tools_list_body(_ALL_NAMES)

        for method in ("initialize", "ping", "resources/list", "tools/list_changed"):
            _, _, body = await _drive(_fake_app([original]), _plain_user(), request_chunks=[_rpc(method)])
            assert body == original, method

    async def test_tools_list_request_is_still_filtered(self):
        original = _tools_list_body(_ALL_NAMES)

        _, headers, body = await _drive(_fake_app([original]), _plain_user(), request_chunks=[_rpc("tools/list")])

        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        assert names == ["list_projects", *BLOG_READ_NAMES, "create_ticket", "create_wiki_page"]
        assert headers[b"content-length"] == str(len(body)).encode()

    async def test_account_with_flag_gets_full_list_for_tools_list_request(self):
        original = _tools_list_body(_ALL_NAMES)

        _, _, body = await _drive(_fake_app([original]), _flag_user(), request_chunks=[_rpc("tools/list")])

        assert body == original

    async def test_batch_request_with_tools_list_gets_every_response_element_filtered(self):
        batch_request = json.dumps([json.loads(_rpc("initialize", request_id=1)), json.loads(_rpc("tools/list", request_id=2))]).encode()
        batch_response = json.dumps(
            [
                {"jsonrpc": "2.0", "id": 1, "result": {"protocolVersion": "2025-03-26", "capabilities": {}}},
                json.loads(_tools_list_body(_ALL_NAMES, request_id=2)),
                json.loads(_tools_list_body(["publish_blog_post", "list_projects"], request_id=3)),
            ]
        ).encode()

        _, headers, body = await _drive(_fake_app([batch_response]), _plain_user(), request_chunks=[batch_request])

        elements = json.loads(body)
        assert elements[0]["result"]["protocolVersion"] == "2025-03-26"
        assert [t["name"] for t in elements[1]["result"]["tools"]] == ["list_projects", *BLOG_READ_NAMES, "create_ticket", "create_wiki_page"]
        assert [t["name"] for t in elements[2]["result"]["tools"]] == ["list_projects"]
        assert headers[b"content-length"] == str(len(body)).encode()

    async def test_batch_request_without_tools_list_is_forwarded_without_buffering(self):
        batch_request = json.dumps([json.loads(_rpc("tools/call", request_id=1)), json.loads(_rpc("ping", request_id=2))]).encode()
        first = b"[" + _tools_list_body(_ALL_NAMES, request_id=1) + b","
        second = _tools_list_body(_ALL_NAMES, request_id=2) + b"]"
        app = _fake_app([first, second])
        expected = await _drive_raw(app, _super_user(), [batch_request])

        sent = await _drive_raw(app, _plain_user(), [batch_request])

        assert sent == expected

    async def test_request_exactly_at_the_limit_is_read_and_decides(self):
        original = _tools_list_body(_ALL_NAMES)
        request = _request_padded_to(64 * 1024, "tools/call")

        _, _, body = await _drive(_fake_app([original]), _plain_user(), request_chunks=[request])

        assert body == original

    async def test_request_over_the_limit_falls_back_to_filtering(self):
        original = _tools_list_body(_ALL_NAMES)
        request = _request_padded_to(64 * 1024 + 1, "tools/call")

        _, headers, body = await _drive(_fake_app([original]), _plain_user(), request_chunks=[request])

        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        assert not set(BLOG_WRITE_NAMES) & set(names)
        assert headers[b"content-length"] == str(len(body)).encode()

    @pytest.mark.parametrize(
        "request_chunks",
        [[b"to nie jest json"], [b""], [], [b"null"], [b"5"], [b'"tools/list"']],
        ids=["invalid-json", "empty-body", "no-body", "json-null", "json-number", "json-string"],
    )
    async def test_request_with_undeterminable_method_falls_back_to_filtering(self, request_chunks):
        original = _tools_list_body(_ALL_NAMES)

        _, headers, body = await _drive(_fake_app([original]), _plain_user(), request_chunks=request_chunks)

        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        assert not set(BLOG_WRITE_NAMES) & set(names)
        assert headers[b"content-length"] == str(len(body)).encode()

    async def test_batch_request_with_non_object_element_and_tools_list_is_filtered(self):
        original = _tools_list_body(_ALL_NAMES)
        batch_request = json.dumps([1, json.loads(_rpc("tools/list", request_id=2))]).encode()

        _, headers, body = await _drive(_fake_app([original]), _plain_user(), request_chunks=[batch_request])

        names = [t["name"] for t in json.loads(body)["result"]["tools"]]
        assert not set(BLOG_WRITE_NAMES) & set(names)
        assert headers[b"content-length"] == str(len(body)).encode()

    async def test_batch_request_with_non_object_elements_and_no_tools_list_is_forwarded_without_buffering(self):
        batch_request = json.dumps([1, "x", json.loads(_rpc("tools/call", request_id=3))]).encode()
        first = b"[" + _tools_list_body(_ALL_NAMES, request_id=3) + b","
        second = _tools_list_body(_ALL_NAMES, request_id=4) + b"]"
        app = _fake_app([first, second])
        expected = await _drive_raw(app, _super_user(), [batch_request])

        sent = await _drive_raw(app, _plain_user(), [batch_request])

        assert sent == expected
        assert [m["body"] for m in sent if m["type"] == "http.response.body"] == [first, second]


class TestMiddlewareReplaysRequestBody:
    async def test_single_message_body_reaches_the_app_byte_for_byte(self):
        request = _rpc("tools/call", request_id=11)
        received: list[dict] = []

        await _drive_raw(_app_reading_request([b"{}"], received), _plain_user(), [request])

        assert b"".join(m["body"] for m in received if m["type"] == "http.request") == request
        assert received[-1]["more_body"] is False

    async def test_multi_part_body_reaches_the_app_byte_for_byte(self):
        parts = [b'{"jsonrpc": "2.0", ', b'"id": 12, "meth', b'od": "tools/call"}']
        received: list[dict] = []

        await _drive_raw(_app_reading_request([b"{}"], received), _plain_user(), parts)

        assert b"".join(m["body"] for m in received if m["type"] == "http.request") == b"".join(parts)
        assert received[-1]["more_body"] is False
        assert all(m["more_body"] for m in received[:-1])

    async def test_multi_part_tools_list_body_is_replayed_and_still_filtered(self):
        parts = [b'{"jsonrpc": "2.0", "id": 13, ', b'"method": "tools/list"}']
        received: list[dict] = []
        original = _tools_list_body(_ALL_NAMES)

        _, _, body = await _drive(_app_reading_request([original], received), _plain_user(), request_chunks=parts)

        assert b"".join(m["body"] for m in received if m["type"] == "http.request") == b"".join(parts)
        assert not set(BLOG_WRITE_NAMES) & {t["name"] for t in json.loads(body)["result"]["tools"]}

    async def test_body_over_the_limit_is_replayed_in_full(self):
        request = _request_padded_to(70 * 1024, "tools/call")
        parts = [request[i : i + 8192] for i in range(0, len(request), 8192)]
        received: list[dict] = []

        await _drive_raw(_app_reading_request([b"{}"], received), _plain_user(), parts)

        assert b"".join(m["body"] for m in received if m["type"] == "http.request") == request
        assert received[-1]["more_body"] is False

    async def test_invalid_json_body_is_replayed_unchanged(self):
        received: list[dict] = []

        await _drive_raw(_app_reading_request([b"{}"], received), _plain_user(), [b"to nie jest ", b"json"])

        assert b"".join(m["body"] for m in received if m["type"] == "http.request") == b"to nie jest json"

    async def test_account_with_permission_gets_the_original_receive(self):
        request = _rpc("tools/call", request_id=14)
        received: list[dict] = []

        await _drive_raw(_app_reading_request([b"{}"], received), _flag_user(), [request])

        assert received == [{"type": "http.request", "body": request, "more_body": False}]


class TestHideBlogWriteToolsBatchResponse:
    def test_every_element_of_a_batch_is_filtered(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        body = json.dumps(
            [
                json.loads(_tools_list_body(_ALL_NAMES, request_id=1)),
                {"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-03-26"}},
                json.loads(_tools_list_body(["unpublish_blog_post", "get_blog_post"], request_id=3)),
            ]
        ).encode()

        result = json.loads(_hide_blog_write_tools(body))

        assert [t["name"] for t in result[0]["result"]["tools"]] == ["list_projects", *BLOG_READ_NAMES, "create_ticket", "create_wiki_page"]
        assert result[1] == {"jsonrpc": "2.0", "id": 2, "result": {"protocolVersion": "2025-03-26"}}
        assert [t["name"] for t in result[2]["result"]["tools"]] == ["get_blog_post"]
        assert [e["id"] for e in result] == [1, 2, 3]

    def test_single_element_batch_is_filtered(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        body = json.dumps([json.loads(_tools_list_body(["publish_blog_post", "list_projects"]))]).encode()

        result = json.loads(_hide_blog_write_tools(body))

        assert [t["name"] for t in result[0]["result"]["tools"]] == ["list_projects"]

    def test_non_dict_elements_of_a_batch_are_kept(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        body = json.dumps(["surowy", 5, None, json.loads(_tools_list_body(["publish_blog_post", "list_projects"]))]).encode()

        result = json.loads(_hide_blog_write_tools(body))

        assert result[:3] == ["surowy", 5, None]
        assert [t["name"] for t in result[3]["result"]["tools"]] == ["list_projects"]

    def test_batch_without_blog_write_tools_is_returned_byte_identical(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        body = json.dumps([json.loads(_tools_list_body(["list_projects"])), {"jsonrpc": "2.0", "id": 2, "result": {}}], indent=2).encode()

        assert _hide_blog_write_tools(body) == body

    def test_empty_batch_is_returned_byte_identical(self):
        from monolynx.mcp_server import _hide_blog_write_tools

        assert _hide_blog_write_tools(b"[]") == b"[]"
