"""Testy integracyjne szkieletu API v2 (MON-140).

Zakres: placeholder GET /api/v2/ i GET /api/v2 (bez slasha), catch-all 404
problem+json pod /api/v2/* dla GET/POST/PUT/PATCH/DELETE/OPTIONS/HEAD (zeby
mount MCP na "/" nie przechwytywal nieznalezionych sciezek v2),
/api/v2/openapi.json + /api/v2/docs, middleware X-Request-Id ograniczony do
/api/v2 Z GRANICA prefiksu (nie goly str.startswith -- "/api/v2foo" nie jest
v2), dependency get_api_user (401 z WWW-Authenticate: Bearer) /
get_api_user_and_project, ApiV2Error i zwykly HTTPException pod v2 (oba z
zachowaniem wlasnych naglowkow, np. Allow/Retry-After), i PaginatedResponse[T].
Regresja: format bledow poza /api/v2 (dashboard, API v1, inny prefiks jak
/api/v2foo) MUSI zostac FastAPI-owy ({"detail": ...}, application/json) --
nie problem+json.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from pydantic import BaseModel, ValidationError

from monolynx.api.v2 import register_api_v2
from monolynx.api.v2.deps import get_api_user, get_api_user_and_project
from monolynx.api.v2.errors import ApiV2Error
from monolynx.database import get_db
from monolynx.models.oauth import OAuthAccessToken
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.schemas.api_v2.pagination import PaginatedResponse
from monolynx.services.auth import hash_password
from monolynx.services.mcp_auth import generate_api_token
from tests.conftest import login_session

pytestmark = pytest.mark.integration


def _is_uuid4(value: str) -> bool:
    return uuid.UUID(value).version == 4


# ---------------------------------------------------------------------------
# Placeholder GET /api/v2/, catch-all 404, openapi/docs, X-Request-Id na
# glownej aplikacji (fixture `client`)
# ---------------------------------------------------------------------------


class TestPlaceholderRoot:
    async def test_returns_200_without_authorization(self, client):
        response = await client.get("/api/v2/")

        assert response.status_code == 200
        assert response.json() == {"api": "v2"}

    async def test_x_request_id_echoed_when_provided(self, client):
        response = await client.get("/api/v2/", headers={"X-Request-Id": "moj-request-id"})

        assert response.headers["x-request-id"] == "moj-request-id"

    async def test_x_request_id_generated_as_uuid4_when_absent(self, client):
        response = await client.get("/api/v2/")

        assert _is_uuid4(response.headers["x-request-id"])

    async def test_x_request_id_absent_outside_v2(self, client):
        response = await client.get("/api/v1/health")

        assert "x-request-id" not in response.headers

    async def test_no_trailing_slash_returns_200(self, client):
        """MON-140 iteracja 2: GET /api/v2 (bez koncowego slasha) -> 200 wprost,
        nie redirect. Dzisiejsza dziura: router ma tylko "/" (pelna sciezka
        /api/v2/), a "/api/v2" bez segmentu nie trafia w catch-all
        "/api/v2/{full_path:path}" (wymaga slasha) -- request spada do mountu
        MCP na "/" i wraca 401 Bearer required."""
        response = await client.get("/api/v2")

        assert response.status_code == 200
        assert response.json() == {"api": "v2"}


class TestCatchAll404:
    async def test_unknown_v2_path_returns_problem_json_404(self, client):
        response = await client.get("/api/v2/nieistniejaca")

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["status"] == 404
        assert body["instance"] == "/api/v2/nieistniejaca"
        assert "type" in body
        assert "title" in body
        assert "detail" in body

    async def test_unknown_v2_path_carries_x_request_id(self, client):
        response = await client.get("/api/v2/nieistniejaca", headers={"X-Request-Id": "catch-all-rid"})

        assert response.headers["x-request-id"] == "catch-all-rid"

    async def test_unknown_v2_path_is_not_401_from_mcp_mount(self, client):
        """Regresja: bez catch-all, / mount MCP przechwycilby nieznaleziona
        sciezke v2 i oddal 401 (Bearer wymagany), zamiast 404 problem+json."""
        response = await client.get("/api/v2/nieistniejaca")

        assert response.status_code != 401

    async def test_unknown_v2_path_options_returns_404_problem_json(self, client):
        """MON-140 iteracja 2: OPTIONS musi byc w metodach catch-all, inaczej
        Starlette widzi PARTIAL match (sciezka zgadza sie, metoda nie) i
        rzuca 405 zamiast 404."""
        response = await client.options("/api/v2/nieistniejaca")

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_unknown_v2_path_head_returns_404_without_body(self, client):
        """HEAD nie ma body w odpowiedzi (httpx ASGITransport ucina body dla
        HEAD niezaleznie od tego co wyslala apka -- sprawdzamy tylko ze
        status to wciaz 404, nie 405/401)."""
        response = await client.head("/api/v2/nieistniejaca")

        assert response.status_code == 404
        assert response.content == b""


class TestOpenapiAndDocs:
    async def test_v2_openapi_contains_only_v2_paths(self, client):
        response = await client.get("/api/v2/openapi.json")

        assert response.status_code == 200
        data = response.json()
        assert "/api/v2/" in data["paths"]
        for path in data["paths"]:
            assert path.startswith("/api/v2"), f"nieoczekiwana sciezka poza v2 w openapi v2: {path}"

    async def test_v2_docs_returns_html_pointing_to_v2_openapi(self, client):
        response = await client.get("/api/v2/docs")

        assert response.status_code == 200
        assert "text/html" in response.headers["content-type"]
        assert "/api/v2/openapi.json" in response.text

    async def test_main_openapi_still_works(self, client):
        response = await client.get("/openapi.json")

        assert response.status_code == 200
        assert "paths" in response.json()


class TestPrefixBoundaryOnMainApp:
    async def test_similar_prefix_has_no_x_request_id(self, client):
        """MON-140 iteracja 2: middleware sprawdza "/api/v2" jako PREFIKS ZE
        GRANICA, nie naiwny str.startswith("/api/v2") -- "/api/v2foo" zaczyna
        sie od "/api/v2" jako string, ale nie jest sciezka v2. Naiwny mutant
        (goly startswith) dopisalby tu naglowek X-Request-Id."""
        response = await client.get("/api/v2foo")

        assert "x-request-id" not in response.headers


# ---------------------------------------------------------------------------
# Regresja: format bledow POZA /api/v2 zostaje niezmieniony
# ---------------------------------------------------------------------------


class TestErrorFormatOutsideV2Unchanged:
    async def test_api_v1_404_stays_fastapi_json(self, client):
        random_issue_id = uuid.uuid4()
        response = await client.patch(
            f"/api/v1/issues/{random_issue_id}/status",
            json={"status": "resolved"},
        )

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body == {"detail": "Issue not found"}
        assert "title" not in body
        assert "type" not in body

    async def test_api_v1_path_param_validation_error_stays_fastapi_json(self, client):
        response = await client.patch(
            "/api/v1/issues/not-a-uuid/status",
            json={"status": "resolved"},
        )

        assert response.status_code == 422
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert isinstance(body["detail"], list)
        assert "title" not in body

    async def test_dashboard_404_stays_fastapi_json(self, client, db_session):
        await login_session(client, db_session, email="mon140-dashboard-404@example.com")

        response = await client.get(
            f"/dashboard/nieistniejacy-projekt-mon140/scrum/tickets/{uuid.uuid4()}/work-plan",
        )

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert "title" not in body
        assert "type" not in body


# ---------------------------------------------------------------------------
# Dependency get_api_user / get_api_user_and_project + ApiV2Error -- mala
# aplikacja testowa zeby nie zaleziec od realnych sciezek v2 (ktore MON-140
# jeszcze nie definiuje poza placeholderem).
# ---------------------------------------------------------------------------


def _build_test_app() -> FastAPI:
    from fastapi import APIRouter

    app = FastAPI()
    register_api_v2(app)

    test_router = APIRouter(prefix="/api/v2")

    @test_router.get("/_t/me")
    async def _me(user: User = Depends(get_api_user)) -> dict[str, str]:
        return {"email": user.email}

    @test_router.get("/_t/projects/{project_slug}")
    async def _project(
        user_and_project: tuple[User, Project] = Depends(get_api_user_and_project),
    ) -> dict[str, str]:
        _user, project = user_and_project
        return {"slug": project.slug}

    @test_router.get("/_t/query")
    async def _query(n: int) -> dict[str, int]:
        return {"n": n}

    @test_router.get("/_t/conflict")
    async def _conflict() -> None:
        raise ApiV2Error(status=409, title="Conflict", detail="x")

    @test_router.get("/_t/http-exc")
    async def _http_exc() -> None:
        raise HTTPException(status_code=404, detail="x")

    @test_router.get("/_t/http-exc-with-headers")
    async def _http_exc_with_headers() -> None:
        raise HTTPException(status_code=405, detail="x", headers={"Allow": "GET"})

    @test_router.get("/_t/api-v2-error-with-headers")
    async def _api_v2_error_with_headers() -> None:
        raise ApiV2Error(status=429, title="Too Many", headers={"Retry-After": "5"})

    app.include_router(test_router)

    # Router z prefiksem, ktory zaczyna sie jak /api/v2 ale NIE jest v2 --
    # dowod ze handler HTTPException sprawdza granice prefiksu, nie goly
    # str.startswith("/api/v2").
    other_prefix_router = APIRouter(prefix="/api/v2foo")

    @other_prefix_router.get("/_t")
    async def _other_prefix_http_exc() -> None:
        raise HTTPException(status_code=404, detail="x")

    app.include_router(other_prefix_router)

    return app


@pytest.fixture
async def v2_client(db_session):
    app = _build_test_app()

    async def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def _create_user(db_session, email: str) -> User:
    user = User(email=email, password_hash=hash_password("testpass123"))
    db_session.add(user)
    await db_session.flush()
    return user


class TestGetApiUserMissingOrBadAuth:
    async def test_missing_authorization_header_returns_401_problem_json(self, v2_client):
        response = await v2_client.get("/api/v2/_t/me")

        assert response.status_code == 401
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_missing_authorization_carries_x_request_id(self, v2_client):
        response = await v2_client.get("/api/v2/_t/me", headers={"X-Request-Id": "auth-missing"})

        assert response.headers["x-request-id"] == "auth-missing"

    async def test_wrong_scheme_returns_401(self, v2_client):
        response = await v2_client.get("/api/v2/_t/me", headers={"Authorization": "Basic xyz"})

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_invalid_osk_token_returns_401(self, v2_client):
        response = await v2_client.get("/api/v2/_t/me", headers={"Authorization": "Bearer osk_zly"})

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    async def test_empty_bearer_token_returns_401(self, v2_client):
        response = await v2_client.get("/api/v2/_t/me", headers={"Authorization": "Bearer "})

        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"


class TestGetApiUserValidTokens:
    async def test_valid_osk_token_returns_user(self, v2_client, db_session):
        user = await _create_user(db_session, "mon140-osk@example.com")
        raw_token, token_hash = generate_api_token()
        db_session.add(
            UserApiToken(
                user_id=user.id,
                token_hash=token_hash,
                token_prefix=raw_token[:8],
                name="MON-140 osk token",
            )
        )
        await db_session.flush()

        response = await v2_client.get("/api/v2/_t/me", headers={"Authorization": f"Bearer {raw_token}"})

        assert response.status_code == 200
        assert response.json() == {"email": user.email}

    async def test_valid_oauth_access_token_returns_user(self, db_session, v2_client):
        import hashlib

        user = await _create_user(db_session, "mon140-oauth@example.com")
        raw_access = "oauth-raw-" + uuid.uuid4().hex
        db_session.add(
            OAuthAccessToken(
                token_hash=hashlib.sha256(raw_access.encode()).hexdigest(),
                client_id="mlx_test_client",
                user_id=user.id,
                scope=None,
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        await db_session.flush()

        response = await v2_client.get("/api/v2/_t/me", headers={"Authorization": f"Bearer {raw_access}"})

        assert response.status_code == 200
        assert response.json() == {"email": user.email}


class TestGetApiUserAndProject:
    async def _auth_headers(self, db_session, email: str) -> tuple[dict[str, str], User]:
        user = await _create_user(db_session, email)
        raw_token, token_hash = generate_api_token()
        db_session.add(
            UserApiToken(
                user_id=user.id,
                token_hash=token_hash,
                token_prefix=raw_token[:8],
                name="MON-140 project token",
            )
        )
        await db_session.flush()
        return {"Authorization": f"Bearer {raw_token}"}, user

    async def test_nonexistent_project_returns_404(self, v2_client, db_session):
        headers, _user = await self._auth_headers(db_session, "mon140-proj-404@example.com")

        response = await v2_client.get("/api/v2/_t/projects/nieistniejacy-projekt", headers=headers)

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"

    async def test_inactive_project_returns_404(self, v2_client, db_session):
        headers, user = await self._auth_headers(db_session, "mon140-proj-inactive@example.com")
        project = Project(
            name="Inactive MON-140",
            slug="mon140-inactive-proj",
            code="M140I",
            api_key="key-" + uuid.uuid4().hex,
            is_active=False,
        )
        db_session.add(project)
        await db_session.flush()
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        await db_session.flush()

        response = await v2_client.get(f"/api/v2/_t/projects/{project.slug}", headers=headers)

        assert response.status_code == 404

    async def test_project_without_membership_returns_403(self, v2_client, db_session):
        headers, _user = await self._auth_headers(db_session, "mon140-proj-403@example.com")
        project = Project(
            name="No membership MON-140",
            slug="mon140-no-member-proj",
            code="M140N",
            api_key="key-" + uuid.uuid4().hex,
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()

        response = await v2_client.get(f"/api/v2/_t/projects/{project.slug}", headers=headers)

        assert response.status_code == 403
        assert response.headers["content-type"] == "application/problem+json"

    async def test_member_gets_200(self, v2_client, db_session):
        headers, user = await self._auth_headers(db_session, "mon140-proj-ok@example.com")
        project = Project(
            name="Member MON-140",
            slug="mon140-member-proj",
            code="M140M",
            api_key="key-" + uuid.uuid4().hex,
            is_active=True,
        )
        db_session.add(project)
        await db_session.flush()
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        await db_session.flush()

        response = await v2_client.get(f"/api/v2/_t/projects/{project.slug}", headers=headers)

        assert response.status_code == 200
        assert response.json() == {"slug": project.slug}


class TestValidationErrorAndApiV2Error:
    async def test_query_validation_error_returns_422_problem_json(self, v2_client):
        response = await v2_client.get("/api/v2/_t/query", params={"n": "not-an-int"})

        assert response.status_code == 422
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["status"] == 422

    async def test_query_validation_error_carries_x_request_id(self, v2_client):
        response = await v2_client.get(
            "/api/v2/_t/query",
            params={"n": "not-an-int"},
            headers={"X-Request-Id": "validation-rid"},
        )

        assert response.headers["x-request-id"] == "validation-rid"

    async def test_api_v2_error_returns_declared_status_and_fields(self, v2_client):
        response = await v2_client.get("/api/v2/_t/conflict")

        assert response.status_code == 409
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["status"] == 409
        assert body["title"] == "Conflict"
        assert body["detail"] == "x"


class TestHttpExceptionUnderV2:
    """MON-140 iteracja 2 (fix po krytyku, -20): pod /api/v2 kazdy HTTPException
    (nie tylko ApiV2Error) MUSI wpadac w problem+json. Trasa /_t/http-exc
    faktycznie rzuca HTTPException -- zabija mutanta 'zawsze
    return await _default_http_exception_handler(request, exc)'."""

    async def test_http_exception_becomes_problem_json(self, v2_client):
        response = await v2_client.get("/api/v2/_t/http-exc")

        assert response.status_code == 404
        assert response.headers["content-type"] == "application/problem+json"
        body = response.json()
        assert body["status"] == 404
        assert body["instance"] == "/api/v2/_t/http-exc"
        assert "title" in body

    async def test_http_exception_carries_x_request_id(self, v2_client):
        response = await v2_client.get("/api/v2/_t/http-exc", headers={"X-Request-Id": "http-exc-rid"})

        assert response.headers["x-request-id"] == "http-exc-rid"

    async def test_http_exception_preserves_custom_headers(self, v2_client):
        """exc.headers (np. Allow) nie moze zgubic sie przy przepisywaniu
        na problem+json -- to jest to co niesie oryginalny HTTPException."""
        response = await v2_client.get("/api/v2/_t/http-exc-with-headers")

        assert response.status_code == 405
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["allow"] == "GET"
        assert "x-request-id" in response.headers

    async def test_api_v2_error_preserves_custom_headers(self, v2_client):
        response = await v2_client.get("/api/v2/_t/api-v2-error-with-headers")

        assert response.status_code == 429
        assert response.headers["content-type"] == "application/problem+json"
        assert response.headers["retry-after"] == "5"


class TestPrefixBoundaryOnSmallApp:
    async def test_similar_prefix_http_exception_stays_fastapi_json(self, v2_client):
        """/api/v2foo/_t zaczyna sie od "/api/v2" jako string, ale nie jest
        pod v2 -- handler musi to rozpoznac po granicy prefiksu, nie po
        gołym str.startswith."""
        response = await v2_client.get("/api/v2foo/_t")

        assert response.status_code == 404
        assert response.headers["content-type"].startswith("application/json")
        body = response.json()
        assert body == {"detail": "x"}
        assert "title" not in body
        assert "type" not in body

    async def test_similar_prefix_has_no_x_request_id(self, v2_client):
        response = await v2_client.get("/api/v2foo/_t")

        assert "x-request-id" not in response.headers


# ---------------------------------------------------------------------------
# PaginatedResponse[T]
# ---------------------------------------------------------------------------


class _Item(BaseModel):
    name: str


@pytest.mark.unit
class TestPaginatedResponse:
    def test_serializes_expected_keys_only(self):
        resp = PaginatedResponse[_Item](
            items=[_Item(name="a"), _Item(name="b")],
            page=1,
            per_page=10,
            total=25,
            total_pages=3,
        )

        data = resp.model_dump()

        assert set(data.keys()) == {"items", "page", "per_page", "total", "total_pages"}
        assert data["items"] == [{"name": "a"}, {"name": "b"}]
        assert data["page"] == 1
        assert data["per_page"] == 10
        assert data["total"] == 25
        assert data["total_pages"] == 3

    def test_item_type_is_validated(self):
        with pytest.raises(ValidationError):
            PaginatedResponse[_Item](
                items=[{"nazwa_nie_name": "a"}],
                page=1,
                per_page=10,
                total=1,
                total_pages=1,
            )
