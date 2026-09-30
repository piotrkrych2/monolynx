"""Testy jednostkowe serwisu mcp_auth.

MON-140: dodatkowo verify_bearer_token (OAuth + fallback osk_*, wspolna sesja
`db`, rollback + log DEBUG przy wyjatku OAuth) oraz test delegacji
monolynx.mcp_server._verify_token do verify_bearer_token.
"""

import logging
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from monolynx.services.mcp_auth import generate_api_token, hash_token, verify_bearer_token


@pytest.mark.unit
class TestGenerateApiToken:
    def test_returns_tuple(self):
        raw, hashed = generate_api_token()
        assert isinstance(raw, str)
        assert isinstance(hashed, str)

    def test_prefix(self):
        raw, _ = generate_api_token()
        assert raw.startswith("osk_")

    def test_length(self):
        raw, _ = generate_api_token()
        assert len(raw) > 20

    def test_uniqueness(self):
        tokens = {generate_api_token()[0] for _ in range(10)}
        assert len(tokens) == 10

    def test_hash_matches(self):
        raw, hashed = generate_api_token()
        assert hash_token(raw) == hashed


@pytest.mark.unit
class TestHashToken:
    def test_deterministic(self):
        assert hash_token("test123") == hash_token("test123")

    def test_different_inputs(self):
        assert hash_token("abc") != hash_token("def")

    def test_returns_hex_string(self):
        result = hash_token("test")
        assert len(result) == 64  # SHA256 hex
        assert all(c in "0123456789abcdef" for c in result)


@pytest.mark.unit
class TestVerifyBearerToken:
    """MON-140: verify_bearer_token -- OAuth first, fallback na legacy osk_*.

    Kontrakt z backend-developerem: verify_bearer_token importuje
    verify_oauth_access_token LOKALNIE z monolynx.services.oauth (patchujemy
    tam), a verify_mcp_token wywoluje jako nazwe modulowa tego samego modulu
    (patchujemy monolynx.services.mcp_auth.verify_mcp_token). Wyjatek na
    sciezce OAuth -> db.rollback() PRZED fallbackiem. Ta sama sesja `db`
    idzie do obu funkcji -- verify_bearer_token nie otwiera wlasnej.
    """

    async def test_oauth_success_does_not_call_fallback(self):
        oauth_user = object()
        mock_oauth = AsyncMock(return_value=oauth_user)
        mock_osk = AsyncMock()
        db = AsyncMock()

        with (
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_oauth),
            patch("monolynx.services.mcp_auth.verify_mcp_token", mock_osk),
        ):
            result = await verify_bearer_token("sometoken", db)

        assert result is oauth_user
        mock_oauth.assert_awaited_once_with("sometoken", db)
        mock_osk.assert_not_awaited()

    async def test_falls_back_to_osk_when_oauth_returns_none(self):
        osk_user = object()
        mock_oauth = AsyncMock(return_value=None)
        mock_osk = AsyncMock(return_value=osk_user)
        db = AsyncMock()

        with (
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_oauth),
            patch("monolynx.services.mcp_auth.verify_mcp_token", mock_osk),
        ):
            result = await verify_bearer_token("osk_xyz", db)

        assert result is osk_user
        mock_oauth.assert_awaited_once_with("osk_xyz", db)
        mock_osk.assert_awaited_once_with("osk_xyz", db)

    async def test_returns_none_when_both_return_none(self):
        mock_oauth = AsyncMock(return_value=None)
        mock_osk = AsyncMock(return_value=None)
        db = AsyncMock()

        with (
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_oauth),
            patch("monolynx.services.mcp_auth.verify_mcp_token", mock_osk),
        ):
            result = await verify_bearer_token("nic-nie-pasuje", db)

        assert result is None

    async def test_oauth_exception_rolls_back_before_fallback(self):
        """Wyjatek na sciezce OAuth -> db.rollback() PRZED wywolaniem fallbacku.

        Kolejnosc zapisujemy do wspolnej listy przez side_effect kazdego
        mocka -- to zabija mutanta odwracajacego kolejnosc albo usuwajacego
        rollback (test_kills_mutant: rollback musi byc PRZED fallbackiem).
        """
        call_order: list[str] = []
        fallback_user = object()

        async def _oauth_side_effect(*_args: object, **_kwargs: object) -> None:
            raise RuntimeError("oauth table missing")

        async def _rollback_side_effect(*_args: object, **_kwargs: object) -> None:
            call_order.append("rollback")

        async def _osk_side_effect(*_args: object, **_kwargs: object) -> object:
            call_order.append("verify_mcp_token")
            return fallback_user

        mock_oauth = AsyncMock(side_effect=_oauth_side_effect)
        mock_osk = AsyncMock(side_effect=_osk_side_effect)
        db = AsyncMock()
        db.rollback = AsyncMock(side_effect=_rollback_side_effect)

        with (
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_oauth),
            patch("monolynx.services.mcp_auth.verify_mcp_token", mock_osk),
        ):
            result = await verify_bearer_token("token-z-wyjatkiem", db)

        assert result is fallback_user
        assert call_order == ["rollback", "verify_mcp_token"]
        db.rollback.assert_awaited_once()

    async def test_oauth_exception_logs_debug_message(self, caplog):
        """MON-140 iteracja 2: wyjatek na sciezce OAuth loguje DEBUG z fraza
        'OAuth verification skipped' (logger monolynx.services.mcp_auth) --
        zabija mutanta usuwajacego logger.debug() z blocku except."""
        mock_oauth = AsyncMock(side_effect=RuntimeError("oauth table missing"))
        mock_osk = AsyncMock(return_value=object())
        db = AsyncMock()

        with (
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_oauth),
            patch("monolynx.services.mcp_auth.verify_mcp_token", mock_osk),
            caplog.at_level(logging.DEBUG, logger="monolynx.services.mcp_auth"),
        ):
            await verify_bearer_token("token-z-wyjatkiem", db)

        assert any("OAuth verification skipped" in record.getMessage() for record in caplog.records if record.name == "monolynx.services.mcp_auth")

    async def test_does_not_open_own_session(self):
        """verify_bearer_token przekazuje TA SAMA sesje `db` dalej, nie async_session_factory().

        Gdyby otwieral wlasna sesje i przekazywal ja do verify_oauth_access_token
        / verify_mcp_token zamiast naszego sentinela `db`, ponizsze
        assert_awaited_once_with(..., db) by padly.
        """
        mock_oauth = AsyncMock(return_value=None)
        mock_osk = AsyncMock(return_value=None)
        db = AsyncMock()

        with (
            patch("monolynx.services.oauth.verify_oauth_access_token", mock_oauth),
            patch("monolynx.services.mcp_auth.verify_mcp_token", mock_osk),
        ):
            await verify_bearer_token("token", db)

        mock_oauth.assert_awaited_once_with("token", db)
        mock_osk.assert_awaited_once_with("token", db)


@pytest.mark.unit
class TestMcpServerVerifyTokenDelegation:
    """MON-140: mcp_server._verify_token staje sie wrapperem nad verify_bearer_token.

    Kontrakt: mcp_server importuje verify_bearer_token NA POZIOMIE MODULU
    (`from monolynx.services.mcp_auth import verify_bearer_token`), wiec
    patchujemy monolynx.mcp_server.verify_bearer_token, nie
    monolynx.services.mcp_auth.verify_bearer_token.
    """

    async def test_delegates_to_verify_bearer_token_and_returns_user(self):
        from monolynx.mcp_server import _verify_token

        fake_user = object()
        mock_bearer = AsyncMock(return_value=fake_user)

        @asynccontextmanager
        async def _fake_session_factory():
            yield object()

        with (
            patch("monolynx.mcp_server.verify_bearer_token", mock_bearer),
            patch("monolynx.mcp_server.async_session_factory", _fake_session_factory),
        ):
            user = await _verify_token("sometoken")

        assert user is fake_user
        mock_bearer.assert_awaited_once()
        assert mock_bearer.await_args.args[0] == "sometoken"

    async def test_raises_valueerror_when_verify_bearer_token_returns_none(self):
        from monolynx.mcp_server import _verify_token

        mock_bearer = AsyncMock(return_value=None)

        @asynccontextmanager
        async def _fake_session_factory():
            yield object()

        with (
            patch("monolynx.mcp_server.verify_bearer_token", mock_bearer),
            patch("monolynx.mcp_server.async_session_factory", _fake_session_factory),
            pytest.raises(ValueError),
        ):
            await _verify_token("nieprawidlowy")
