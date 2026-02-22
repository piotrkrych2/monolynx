"""Testy jednostkowe serwisu mcp_auth."""

import pytest

from open_sentry.services.mcp_auth import generate_api_token, hash_token


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
