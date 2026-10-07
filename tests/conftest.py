"""Glowne fixtures testowe -- db session, client, factories."""

import os
import secrets

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
)

from monolynx import blog_ai
from monolynx.cli import TEST_DB_PREFIX, validate_test_db_name
from monolynx.config import settings
from monolynx.database import get_db
from monolynx.main import app
from monolynx.models import Base
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.services.auth import hash_password

# Nazwa unikalna per przebieg: rownolegle worktree na jednym kontenerze db nie dziela bazy,
# wiec drop na koncu jednego przebiegu nie kasuje tabel drugiemu (MON-250).
TEST_DATABASE_NAME = validate_test_db_name(os.environ.get("TEST_DATABASE_NAME") or TEST_DB_PREFIX + secrets.token_hex(4))
TEST_DATABASE_URL = make_url(settings.DATABASE_URL).set(database=TEST_DATABASE_NAME)
ADMIN_DATABASE_URL = make_url(settings.DATABASE_URL).set(database="postgres")


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def engine():
    admin_engine = create_async_engine(ADMIN_DATABASE_URL, isolation_level="AUTOCOMMIT")
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    created = False
    try:
        async with admin_engine.connect() as conn:
            # Bez FORCE: pozostalosc po przerwanym przebiegu o recznej nazwie znika,
            # a trwajacy przebieg o tej samej nazwie daje blad zamiast cichego zabicia.
            await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}"'))
            await conn.execute(text(f'CREATE DATABASE "{TEST_DATABASE_NAME}"'))
            created = True
        async with engine.begin() as conn:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            await conn.run_sync(Base.metadata.create_all)
        yield engine
    finally:
        await engine.dispose()
        # Usuwamy tylko baze utworzona tu: gdy DROP/CREATE padl, nazwa moze nalezec do trwajacego przebiegu.
        if created:
            async with admin_engine.connect() as conn:
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DATABASE_NAME}" WITH (FORCE)'))
        await admin_engine.dispose()


@pytest.fixture(autouse=True)
def _clear_llms_full_cache():
    """Cache `/llms-full.txt` (odcisk metadanych, MON-271) żyje w pamięci procesu, więc bez czyszczenia dokument jednego
    testu wyciekłby do następnego. `getattr`, bo funkcja powstaje razem z cache'em."""
    clear = getattr(blog_ai, "clear_llms_full_cache", None)
    if clear is not None:
        clear()
    yield


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
        code="TST",
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


async def login_session(client, db_session, email="test@example.com", is_superuser: bool = True):
    """Tworzy uzytkownika i loguje go, zwraca client z sesja.

    Domyslnie tworzy superusera (is_superuser=True) zeby omijac sprawdzenie RBAC
    w endpointach ktore wywoluja require_permission. Ustaw is_superuser=False
    jezeli test ma weryfikowac zachowanie dla zwyklego usera.
    """
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=is_superuser,
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
