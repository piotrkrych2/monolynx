"""Fixtures wspólne dla testów API v2 (MON-142): użytkownik + token Bearer,
projekt z członkostwem, helper nagłówków Authorization.

`client` i `db_session` są dziedziczone z `tests/conftest.py` (nadrzędny
conftest, wspólny dla całego katalogu `tests/`) -- ten plik ich nie
definiuje ponownie. Zamiast fixture'ów per-scenariusz, wystawia zwykłe
async helpery (wzorzec z `tests/integration/test_wiki_service_extraction.py`),
bo każdy test potrzebuje własnej, unikalnej kombinacji użytkowników/ról/
projektów -- fixture o stałym kształcie by to ograniczał.
"""

from __future__ import annotations

import secrets
import string
import uuid
from dataclasses import dataclass

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.services.auth import hash_password
from monolynx.services.mcp_auth import generate_api_token


@dataclass
class ApiV2Actor:
    """Użytkownik testowy API v2: encja `User` + gotowe nagłówki `Authorization: Bearer`."""

    user: User
    headers: dict[str, str]


async def make_actor(db_session, tag: str, *, is_superuser: bool = False, can_edit_blog: bool = False) -> ApiV2Actor:
    """Utwórz użytkownika z tokenem `osk_*` (jak `UserApiToken` z profilu) i zwróć
    nagłówki Bearer gotowe do requestów `/api/v2/*`.

    `can_edit_blog` (MON-270): uprawnienie `blog:write` na koncie; domyślnie `False`, jak w produkcji."""
    user = User(
        email=f"v2-{tag}-{uuid.uuid4().hex[:8]}@example.com",
        password_hash=hash_password("testpass123"),
        is_superuser=is_superuser,
        can_edit_blog=can_edit_blog,
    )
    db_session.add(user)
    await db_session.flush()

    raw_token, token_hash = generate_api_token()
    db_session.add(
        UserApiToken(
            user_id=user.id,
            token_hash=token_hash,
            token_prefix=raw_token[:8],
            name=f"api-v2-test-{tag}",
        )
    )
    await db_session.flush()

    return ApiV2Actor(user=user, headers={"Authorization": f"Bearer {raw_token}"})


async def make_project(db_session, tag: str) -> Project:
    """Utwórz aktywny projekt testowy ze slugiem/kodem unikalnym per wywołanie.

    Kod TYLKO z liter -- `resolve_ticket_uuid()` w services/tickets.py rozpoznaje
    klucz ticketu (np. "A-1") regexem `^([A-Za-z]{1,10})-(\\d+)$`, bez cyfr w
    prefiksie; `secrets.token_hex()` dawałby hex (0-9a-f), co psułoby testy
    kluczy ticketów w test_tickets.py (identyczna poprawka jak w
    tests/integration/test_tickets_service.py::_make_project).
    """
    unique = uuid.uuid4().hex[:6]
    letters_suffix = "".join(secrets.choice(string.ascii_uppercase) for _ in range(4))
    project = Project(
        name=f"API v2 {tag}",
        slug=f"apiv2-{tag}-{unique}",
        code=f"A{letters_suffix}",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def add_member(db_session, project: Project, user: User, role: str = "owner") -> ProjectMember:
    """Dołącz użytkownika do projektu z podaną rolą legacy (DEFAULT_ROLE_PERMISSIONS)."""
    member = ProjectMember(project_id=project.id, user_id=user.id, role=role)
    db_session.add(member)
    await db_session.flush()
    return member


async def make_owner_project(db_session, tag: str, *, can_edit_blog: bool = False) -> tuple[ApiV2Actor, Project]:
    """Skrót: nowy użytkownik + nowy projekt + członkostwo 'owner' (pełne uprawnienia w projekcie).

    Konto nie ma `blog:write`, dopóki test nie poda `can_edit_blog=True` (MON-270)."""
    actor = await make_actor(db_session, tag, can_edit_blog=can_edit_blog)
    project = await make_project(db_session, tag)
    await add_member(db_session, project, actor.user, role="owner")
    return actor, project
