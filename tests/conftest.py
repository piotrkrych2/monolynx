"""Glowne fixtures testowe -- db session, client, factories."""

import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)

from open_sentry.config import settings
from open_sentry.database import get_db
from open_sentry.main import app
from open_sentry.models import Base
from open_sentry.models.project import Project
from open_sentry.models.user import User
from open_sentry.services.auth import hash_password

TEST_DATABASE_URL = settings.DATABASE_URL.replace("/open_sentry", "/open_sentry_test")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    # Upewniamy sie ze baza testowa istnieje
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine as _create

    admin_url = settings.DATABASE_URL.replace("/open_sentry", "/postgres")
    admin_engine = _create(admin_url, isolation_level="AUTOCOMMIT")
    async with admin_engine.connect() as conn:
        result = await conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'open_sentry_test'")
        )
        if not result.scalar():
            await conn.execute(text("CREATE DATABASE open_sentry_test"))
    await admin_engine.dispose()

    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()


@pytest.fixture
async def db_session(engine):
    async with engine.connect() as connection:
        transaction = await connection.begin()

        session = AsyncSession(bind=connection, expire_on_commit=False)

        yield session

        await session.close()
        await transaction.rollback()


@pytest.fixture
async def client(db_session):
    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture
async def test_project(db_session):
    """Tworzy testowy projekt z API key."""
    project = Project(
        name="Test Project",
        slug="test-project",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


@pytest.fixture
def sample_event_payload():
    return {
        "exception": {
            "type": "ValueError",
            "value": "invalid literal for int() with base 10: 'abc'",
            "module": "builtins",
            "stacktrace": {
                "frames": [
                    {
                        "filename": "app/views.py",
                        "function": "process_order",
                        "lineno": 42,
                        "context_line": "    quantity = int(request.POST['qty'])",
                    }
                ]
            },
        },
        "platform": "python",
        "timestamp": "2026-02-19T10:00:00Z",
        "level": "error",
    }


async def login_session(client, db_session, email="test@example.com"):
    """Tworzy uzytkownika i loguje go, zwraca client z sesja."""
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
    )
    db_session.add(user)
    await db_session.flush()

    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303
    return client
