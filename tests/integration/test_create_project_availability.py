"""Testy integracyjne -- dostepnosc sluga i kodu przy tworzeniu projektu (MON-244).

Dotyczy `GET /dashboard/create-project/check` oraz kolizji w `POST /dashboard/create-project`.
"""

import secrets
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import func, select

from monolynx.models.project import Project
from tests.conftest import login_session


async def _make_project(db_session, slug: str, code: str, *, is_active: bool = True) -> Project:
    project = Project(
        name="Zajmujacy projekt",
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=is_active,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _count_projects(db_session, **filters) -> int:
    stmt = select(func.count(Project.id))
    for column, value in filters.items():
        stmt = stmt.where(getattr(Project, column) == value)
    return (await db_session.execute(stmt)).scalar_one()


@pytest.mark.integration
class TestCheckEndpointAuth:
    async def test_check_without_session_returns_401_json(self, client):
        response = await client.get("/dashboard/create-project/check", params={"slug": "x-244"})

        assert response.status_code == 401
        assert response.json() == {"error": "Unauthorized"}


@pytest.mark.integration
class TestCheckEndpointFormat:
    async def test_invalid_slug_format_returns_valid_false_without_query(self, client, db_session):
        client = await login_session(client, db_session, email="chk_fmt_slug@example.com")

        with patch("monolynx.dashboard.projects.suggest_available_slug", new_callable=AsyncMock) as mock_slug:
            response = await client.get("/dashboard/create-project/check", params={"slug": "Zly Slug!"})

        assert response.status_code == 200
        assert response.json() == {"slug": {"value": "zly slug!", "valid": False, "available": False, "suggestion": None}}
        mock_slug.assert_not_awaited()

    async def test_invalid_code_format_returns_valid_false_without_query(self, client, db_session):
        client = await login_session(client, db_session, email="chk_fmt_code@example.com")

        with patch("monolynx.dashboard.projects.suggest_available_code", new_callable=AsyncMock) as mock_code:
            response = await client.get("/dashboard/create-project/check", params={"code": "1ABC"})

        assert response.status_code == 200
        assert response.json() == {"code": {"value": "1ABC", "valid": False, "available": False, "suggestion": None}}
        mock_code.assert_not_awaited()

    async def test_code_longer_than_ten_chars_is_invalid(self, client, db_session):
        client = await login_session(client, db_session, email="chk_fmt_long_code@example.com")

        response = await client.get("/dashboard/create-project/check", params={"code": "ABCDEFGHIJK"})

        assert response.json()["code"] == {"value": "ABCDEFGHIJK", "valid": False, "available": False, "suggestion": None}

    async def test_slug_longer_than_255_chars_is_invalid(self, client, db_session):
        client = await login_session(client, db_session, email="chk_fmt_long_slug@example.com")
        slug = "a" * 256

        response = await client.get("/dashboard/create-project/check", params={"slug": slug})

        assert response.json()["slug"] == {"value": slug, "valid": False, "available": False, "suggestion": None}

    async def test_slug_with_exactly_255_chars_is_valid(self, client, db_session):
        client = await login_session(client, db_session, email="chk_fmt_255_slug@example.com")
        slug = "a" * 255

        response = await client.get("/dashboard/create-project/check", params={"slug": slug})

        assert response.json()["slug"] == {"value": slug, "valid": True, "available": True, "suggestion": slug}


@pytest.mark.integration
class TestCheckEndpointAvailability:
    async def test_free_slug_and_code_return_available_with_same_suggestion(self, client, db_session):
        client = await login_session(client, db_session, email="chk_free@example.com")

        response = await client.get("/dashboard/create-project/check", params={"slug": "wolny-244", "code": "WOLNY244"})

        assert response.status_code == 200
        assert response.json() == {
            "slug": {"value": "wolny-244", "valid": True, "available": True, "suggestion": "wolny-244"},
            "code": {"value": "WOLNY244", "valid": True, "available": True, "suggestion": "WOLNY244"},
        }

    async def test_taken_slug_returns_unavailable_with_suffix_suggestion(self, client, db_session):
        client = await login_session(client, db_session, email="chk_taken_slug@example.com")
        await _make_project(db_session, "chk-zajety-244", "CHKZ1")

        response = await client.get("/dashboard/create-project/check", params={"slug": "chk-zajety-244"})

        assert response.json() == {"slug": {"value": "chk-zajety-244", "valid": True, "available": False, "suggestion": "chk-zajety-244-2"}}

    async def test_taken_code_returns_unavailable_with_suffix_suggestion(self, client, db_session):
        client = await login_session(client, db_session, email="chk_taken_code@example.com")
        await _make_project(db_session, "chk-kod-244", "CHKK")

        response = await client.get("/dashboard/create-project/check", params={"code": "CHKK"})

        assert response.json() == {"code": {"value": "CHKK", "valid": True, "available": False, "suggestion": "CHKK2"}}

    async def test_soft_deleted_project_still_occupies_slug_and_code(self, client, db_session):
        client = await login_session(client, db_session, email="chk_soft@example.com")
        await _make_project(db_session, "chk-soft-244", "CHKS", is_active=False)

        response = await client.get("/dashboard/create-project/check", params={"slug": "chk-soft-244", "code": "CHKS"})

        assert response.json() == {
            "slug": {"value": "chk-soft-244", "valid": True, "available": False, "suggestion": "chk-soft-244-2"},
            "code": {"value": "CHKS", "valid": True, "available": False, "suggestion": "CHKS2"},
        }

    async def test_slug_and_code_checked_independently(self, client, db_session):
        client = await login_session(client, db_session, email="chk_indep@example.com")
        await _make_project(db_session, "chk-indep-244", "CHKI")

        response = await client.get("/dashboard/create-project/check", params={"slug": "chk-indep-244", "code": "CHKI9"})

        body = response.json()
        assert body["slug"]["available"] is False
        assert body["code"] == {"value": "CHKI9", "valid": True, "available": True, "suggestion": "CHKI9"}


@pytest.mark.integration
class TestCheckEndpointNormalization:
    async def test_slug_is_stripped_and_lowercased(self, client, db_session):
        client = await login_session(client, db_session, email="chk_norm_slug@example.com")
        await _make_project(db_session, "chk-norm-244", "CHKN1")

        response = await client.get("/dashboard/create-project/check", params={"slug": "  Chk-Norm-244 "})

        assert response.json() == {"slug": {"value": "chk-norm-244", "valid": True, "available": False, "suggestion": "chk-norm-244-2"}}

    async def test_code_is_stripped_and_uppercased(self, client, db_session):
        client = await login_session(client, db_session, email="chk_norm_code@example.com")
        await _make_project(db_session, "chk-norm-code-244", "NORM244")

        response = await client.get("/dashboard/create-project/check", params={"code": " norm244 "})

        assert response.json() == {"code": {"value": "NORM244", "valid": True, "available": False, "suggestion": "NORM2442"}}


@pytest.mark.integration
class TestCheckEndpointParameters:
    async def test_only_slug_returns_only_slug_key(self, client, db_session):
        client = await login_session(client, db_session, email="chk_only_slug@example.com")

        response = await client.get("/dashboard/create-project/check", params={"slug": "tylko-slug-244"})

        assert list(response.json()) == ["slug"]

    async def test_only_code_returns_only_code_key(self, client, db_session):
        client = await login_session(client, db_session, email="chk_only_code@example.com")

        response = await client.get("/dashboard/create-project/check", params={"code": "TYLKOK"})

        assert list(response.json()) == ["code"]

    async def test_empty_slug_param_omits_slug_key(self, client, db_session):
        client = await login_session(client, db_session, email="chk_empty_slug@example.com")

        response = await client.get("/dashboard/create-project/check", params={"slug": "", "code": "PUSTYS"})

        assert list(response.json()) == ["code"]

    async def test_whitespace_only_param_omits_key(self, client, db_session):
        client = await login_session(client, db_session, email="chk_ws_code@example.com")

        response = await client.get("/dashboard/create-project/check", params={"slug": "bialy-244", "code": "   "})

        assert list(response.json()) == ["slug"]

    async def test_no_params_returns_empty_object(self, client, db_session):
        client = await login_session(client, db_session, email="chk_none@example.com")

        response = await client.get("/dashboard/create-project/check")

        assert response.status_code == 200
        assert response.json() == {}


@pytest.mark.integration
class TestCreateProjectCollision:
    async def test_taken_slug_shows_suggestion_and_does_not_create_project(self, client, db_session):
        client = await login_session(client, db_session, email="col_slug@example.com")
        await _make_project(db_session, "kolizja-244", "KOLS1")

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Nowy Projekt", "slug": "kolizja-244", "code": "KOLN1"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert "Slug „kolizja-244” jest zajęty, proponujemy „kolizja-244-2”." in response.text
        assert 'name="slug" value="kolizja-244-2"' in response.text
        assert 'name="code" value="KOLN1"' in response.text
        assert 'name="name" value="Nowy Projekt"' in response.text
        assert await _count_projects(db_session, slug="kolizja-244-2") == 0
        assert await _count_projects(db_session, code="KOLN1") == 0
        assert await _count_projects(db_session, slug="kolizja-244") == 1

    async def test_taken_code_shows_suggestion_and_does_not_create_project(self, client, db_session):
        client = await login_session(client, db_session, email="col_code@example.com")
        await _make_project(db_session, "kolizja-kod-244", "KODX")

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Inny Projekt", "slug": "wolny-kod-244", "code": "KODX"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert "Kod „KODX” jest zajęty, proponujemy „KODX2”." in response.text
        assert "Slug „wolny-kod-244”" not in response.text
        assert 'name="code" value="KODX2"' in response.text
        assert 'name="slug" value="wolny-kod-244"' in response.text
        assert await _count_projects(db_session, slug="wolny-kod-244") == 0
        assert await _count_projects(db_session, code="KODX2") == 0

    async def test_both_taken_shows_both_messages_slug_first(self, client, db_session):
        client = await login_session(client, db_session, email="col_both@example.com")
        await _make_project(db_session, "oba-244", "OBAX")

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Oba", "slug": "oba-244", "code": "OBAX"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert "Slug „oba-244” jest zajęty, proponujemy „oba-244-2”. Kod „OBAX” jest zajęty, proponujemy „OBAX2”." in response.text
        assert 'name="slug" value="oba-244-2"' in response.text
        assert 'name="code" value="OBAX2"' in response.text
        assert await _count_projects(db_session, name="Oba") == 0

    async def test_soft_deleted_project_blocks_slug_in_post(self, client, db_session):
        client = await login_session(client, db_session, email="col_soft@example.com")
        await _make_project(db_session, "kolizja-soft-244", "KSOFT", is_active=False)

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Soft", "slug": "kolizja-soft-244", "code": "KSOFN"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert "Slug „kolizja-soft-244” jest zajęty, proponujemy „kolizja-soft-244-2”." in response.text
        assert await _count_projects(db_session, code="KSOFN") == 0

    async def test_taken_slug_without_free_variant_shows_message_without_suggestion(self, client, db_session):
        client = await login_session(client, db_session, email="col_nosug@example.com")
        slug = "a" * 254
        await _make_project(db_session, slug, "KNOSG")

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Bez propozycji", "slug": slug, "code": "KNOSN"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert f"Slug „{slug}” jest zajęty." in response.text
        assert f"Slug „{slug}” jest zajęty, proponujemy" not in response.text
        assert f'name="slug" value="{slug}"' in response.text
        assert await _count_projects(db_session, code="KNOSN") == 0

    async def test_taken_code_without_free_variant_keeps_typed_code_and_no_suggestion(self, client, db_session):
        client = await login_session(client, db_session, email="col_nocode@example.com")
        for i, code in enumerate(["PE"] + [f"PE{n}" for n in range(2, 100)]):
            await _make_project(db_session, f"kolizja-pe-{i}-244", code)

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Bez wariantu kodu", "slug": "kolizja-bez-kodu-244", "code": "PE"},
            follow_redirects=False,
        )

        assert response.status_code == 200
        assert "Kod „PE” jest zajęty." in response.text
        assert "Kod „PE” jest zajęty, proponujemy" not in response.text
        assert 'name="code" value="PE"' in response.text
        assert 'name="slug" value="kolizja-bez-kodu-244"' in response.text
        assert "Slug „kolizja-bez-kodu-244”" not in response.text
        assert await _count_projects(db_session, slug="kolizja-bez-kodu-244") == 0

    async def test_free_values_still_create_project(self, client, db_session):
        client = await login_session(client, db_session, email="col_ok@example.com")

        response = await client.post(
            "/dashboard/create-project",
            data={"name": "Wolny Projekt", "slug": "kolizja-wolny-244", "code": "KWOL"},
            follow_redirects=False,
        )

        assert response.status_code == 303
        assert response.headers["location"] == "/dashboard/"
        assert await _count_projects(db_session, slug="kolizja-wolny-244", code="KWOL") == 1
