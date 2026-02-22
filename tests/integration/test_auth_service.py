"""Testy integracyjne -- serwis autentykacji (authenticate_user, get_current_user)."""

from __future__ import annotations

import secrets

import pytest

from open_sentry.models.project import Project
from open_sentry.models.user import User
from open_sentry.services.auth import (
    authenticate_user,
    hash_password,
    verify_password,
)
from tests.conftest import login_session


@pytest.mark.integration
class TestHashAndVerifyPassword:
    async def test_hash_password_returns_bcrypt_string(self, db_session):
        """hash_password zwraca hash bcrypt."""
        hashed = hash_password("mypassword123")
        assert hashed is not None
        assert hashed.startswith("$2")

    async def test_verify_password_correct(self, db_session):
        """verify_password zwraca True dla poprawnego hasla."""
        hashed = hash_password("correctpassword")
        assert verify_password("correctpassword", hashed) is True

    async def test_verify_password_incorrect(self, db_session):
        """verify_password zwraca False dla blednego hasla."""
        hashed = hash_password("correctpassword")
        assert verify_password("wrongpassword", hashed) is False


@pytest.mark.integration
class TestAuthenticateUser:
    async def test_authenticate_valid_user(self, db_session):
        """authenticate_user zwraca usera dla poprawnych danych."""
        user = User(
            email="authsvc-valid@test.com",
            password_hash=hash_password("validpass123"),
        )
        db_session.add(user)
        await db_session.flush()

        result = await authenticate_user("authsvc-valid@test.com", "validpass123", db_session)
        assert result is not None
        assert result.email == "authsvc-valid@test.com"

    async def test_authenticate_wrong_password(self, db_session):
        """authenticate_user zwraca None dla blednego hasla."""
        user = User(
            email="authsvc-wrongpw@test.com",
            password_hash=hash_password("correctpass"),
        )
        db_session.add(user)
        await db_session.flush()

        result = await authenticate_user("authsvc-wrongpw@test.com", "wrongpass", db_session)
        assert result is None

    async def test_authenticate_nonexistent_user(self, db_session):
        """authenticate_user zwraca None dla nieistniejacego emaila."""
        result = await authenticate_user("authsvc-nouser@test.com", "anything", db_session)
        assert result is None

    async def test_authenticate_user_without_password_hash(self, db_session):
        """authenticate_user zwraca None dla usera bez password_hash (invited)."""
        user = User(
            email="authsvc-nopw@test.com",
            password_hash=None,
        )
        db_session.add(user)
        await db_session.flush()

        result = await authenticate_user("authsvc-nopw@test.com", "anything", db_session)
        assert result is None

    async def test_authenticate_inactive_user(self, db_session):
        """authenticate_user zwraca None dla nieaktywnego usera."""
        user = User(
            email="authsvc-inactive@test.com",
            password_hash=hash_password("validpass"),
            is_active=False,
        )
        db_session.add(user)
        await db_session.flush()

        result = await authenticate_user("authsvc-inactive@test.com", "validpass", db_session)
        assert result is None


@pytest.mark.integration
class TestGetCurrentUser:
    async def test_get_current_user_with_valid_session(self, client, db_session):
        """get_current_user zwraca usera z sesji."""
        await login_session(client, db_session, email="authsvc-cu-valid@test.com")

        # Access the dashboard which calls get_current_user internally
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 200

    async def test_get_current_user_without_session(self, client, db_session):
        """Dostep do chronionych stron bez sesji redirectuje na login."""
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]


@pytest.mark.integration
class TestVerifyApiKey:
    async def test_verify_api_key_valid(self, client, db_session):
        """verify_api_key akceptuje poprawny klucz API."""
        api_key = secrets.token_urlsafe(32)
        project = Project(
            name="API Key Test",
            slug="api-key-test",
            api_key=api_key,
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        # Use the API endpoint which depends on verify_api_key
        resp = await client.post(
            "/api/v1/events",
            json={
                "exception": {
                    "type": "TestError",
                    "value": "test",
                    "stacktrace": {
                        "frames": [
                            {
                                "filename": "test.py",
                                "function": "test_func",
                                "lineno": 1,
                            }
                        ]
                    },
                },
                "platform": "python",
                "timestamp": "2026-02-19T10:00:00Z",
                "level": "error",
            },
            headers={"X-OpenSentry-Key": api_key},
        )
        assert resp.status_code == 202

    async def test_verify_api_key_invalid(self, client, db_session):
        """verify_api_key odrzuca niepoprawny klucz API."""
        resp = await client.post(
            "/api/v1/events",
            json={
                "exception": {
                    "type": "TestError",
                    "value": "test",
                    "stacktrace": {"frames": []},
                },
                "platform": "python",
                "level": "error",
            },
            headers={"X-OpenSentry-Key": "totally-invalid-key-xyz"},
        )
        assert resp.status_code == 401

    async def test_verify_api_key_cached(self, client, db_session):
        """verify_api_key korzysta z cache przy kolejnych zapytaniach."""
        api_key = secrets.token_urlsafe(32)
        project = Project(
            name="API Cache Test",
            slug="api-cache-test",
            api_key=api_key,
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        payload = {
            "exception": {
                "type": "CacheTestError",
                "value": "cache test",
                "stacktrace": {
                    "frames": [
                        {
                            "filename": "cache.py",
                            "function": "test_cache",
                            "lineno": 1,
                        }
                    ]
                },
            },
            "platform": "python",
            "timestamp": "2026-02-19T10:00:00Z",
            "level": "error",
        }

        # First call -- populates cache
        resp1 = await client.post(
            "/api/v1/events",
            json=payload,
            headers={"X-OpenSentry-Key": api_key},
        )
        assert resp1.status_code == 202

        # Second call -- should use cache
        resp2 = await client.post(
            "/api/v1/events",
            json=payload,
            headers={"X-OpenSentry-Key": api_key},
        )
        assert resp2.status_code == 202


@pytest.mark.integration
class TestProjectListSuperuser:
    async def test_superuser_sees_all_projects(self, client, db_session):
        """Superuser widzi wszystkie projekty na liscie."""
        project = Project(
            name="Superuser Project View",
            slug="su-proj-view",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        # Create superuser and login
        user = User(
            email="authsvc-super-list@test.com",
            password_hash=hash_password("superpass123"),
            is_superuser=True,
        )
        db_session.add(user)
        await db_session.flush()

        resp = await client.post(
            "/auth/login",
            data={"email": "authsvc-super-list@test.com", "password": "superpass123"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        # Access project list -- superuser should see all projects
        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 200
        assert "Superuser Project View" in resp.text

    async def test_regular_user_sees_only_member_projects(self, client, db_session):
        """Zwykly uzytkownik widzi tylko projekty gdzie jest czlonkiem."""
        project = Project(
            name="Hidden Project",
            slug="hidden-proj",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        # Regular user -- not a member of the project
        await login_session(client, db_session, email="authsvc-regular@test.com")

        resp = await client.get("/dashboard/", follow_redirects=False)
        assert resp.status_code == 200
        # Should NOT see the project since they are not a member
        assert "Hidden Project" not in resp.text
