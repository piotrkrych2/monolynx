"""Testy integracyjne -- uwierzytelnienie PATCH /api/v1/issues/{id}/status (MON-236).

Poswiadczenia: naglowek X-Monolynx-Key (issue musi nalezec do projektu klucza)
albo sesja dashboardu z uprawnieniem 500ki:write w projekcie issue. Kazdy test
odmowy sprawdza stan w bazie, nie tylko kod HTTP.
"""

from __future__ import annotations

import secrets
import uuid

import pytest
from sqlalchemy import select

from monolynx.models.issue import Issue
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.user import User
from monolynx.services import auth as auth_service
from tests.conftest import login_session

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clear_api_key_cache():
    # Cache kluczy jest modulowy (TTL 60 s); bez czyszczenia testy zalezalyby od kolejnosci.
    auth_service._api_key_cache.clear()
    yield
    auth_service._api_key_cache.clear()


async def _make_project(db_session, label: str) -> Project:
    project = Project(
        name=f"Project {label}",
        slug=f"mon236-{label}-{secrets.token_hex(3)}",
        code="M" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _make_issue(db_session, project: Project, status: str = "unresolved") -> Issue:
    issue = Issue(
        project_id=project.id,
        fingerprint=secrets.token_hex(8),
        title="ValueError: mon236",
        status=status,
    )
    db_session.add(issue)
    await db_session.flush()
    return issue


async def _login_member(client, db_session, email: str, project: Project | None, role: str | None) -> User:
    """Loguje zwyklego usera; z role=None nie dodaje czlonkostwa."""
    await login_session(client, db_session, email=email, is_superuser=False)
    user = (await db_session.execute(select(User).where(User.email == email))).scalar_one()
    if project is not None and role is not None:
        db_session.add(ProjectMember(project_id=project.id, user_id=user.id, role=role))
        await db_session.flush()
    return user


async def _patch(client, issue_id, status: str = "resolved", key: str | None = None):
    headers = {"X-Monolynx-Key": key} if key is not None else {}
    return await client.patch(
        f"/api/v1/issues/{issue_id}/status",
        json={"status": status},
        headers=headers,
    )


async def _assert_status(db_session, issue: Issue, expected: str = "unresolved") -> None:
    await db_session.refresh(issue)
    assert issue.status == expected


class TestNoCredentials:
    """Przypadek 1: brak poswiadczen -> 401 zawsze, bez wyroczni istnienia."""

    async def test_no_credentials_existing_issue_returns_401_and_keeps_status(self, client, db_session):
        project = await _make_project(db_session, "nocred")
        issue = await _make_issue(db_session, project)

        resp = await _patch(client, issue.id, "resolved")

        assert resp.status_code == 401
        assert resp.json() == {"detail": "Authentication required"}
        await _assert_status(db_session, issue)

    async def test_no_credentials_nonexistent_issue_returns_401_not_404(self, client):
        resp = await _patch(client, uuid.uuid4(), "resolved")

        assert resp.status_code == 401
        assert resp.json() == {"detail": "Authentication required"}

    async def test_no_credentials_invalid_status_returns_401_not_422(self, client, db_session):
        project = await _make_project(db_session, "nocredbad")
        issue = await _make_issue(db_session, project)

        resp = await _patch(client, issue.id, "closed")

        assert resp.status_code == 401
        assert resp.json() == {"detail": "Authentication required"}
        await _assert_status(db_session, issue)


class TestApiKeyCredential:
    """Przypadki 2-4: poswiadczenie A (X-Monolynx-Key)."""

    async def test_unknown_key_returns_401_and_keeps_status(self, client, db_session):
        project = await _make_project(db_session, "badkey")
        issue = await _make_issue(db_session, project)

        resp = await _patch(client, issue.id, "resolved", key="nieistniejacy-klucz-mon236")

        assert resp.status_code == 401
        assert resp.json() == {"detail": "Invalid API key"}
        await _assert_status(db_session, issue)

    async def test_key_of_other_project_returns_404_and_keeps_status(self, client, db_session):
        project_a = await _make_project(db_session, "keya")
        project_b = await _make_project(db_session, "issueb")
        issue_b = await _make_issue(db_session, project_b)

        resp = await _patch(client, issue_b.id, "resolved", key=project_a.api_key)

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Issue not found"}
        await _assert_status(db_session, issue_b)

    async def test_foreign_issue_response_identical_to_nonexistent_issue(self, client, db_session):
        project_a = await _make_project(db_session, "oraclea")
        project_b = await _make_project(db_session, "oracleb")
        issue_b = await _make_issue(db_session, project_b)

        foreign = await _patch(client, issue_b.id, "resolved", key=project_a.api_key)
        missing = await _patch(client, uuid.uuid4(), "resolved", key=project_a.api_key)

        assert foreign.status_code == missing.status_code == 404
        assert foreign.json() == missing.json() == {"detail": "Issue not found"}

    async def test_key_of_issue_project_returns_200_and_updates_db(self, client, db_session):
        project = await _make_project(db_session, "keyok")
        issue = await _make_issue(db_session, project)

        resp = await _patch(client, issue.id, "resolved", key=project.api_key)

        assert resp.status_code == 200
        assert resp.json() == {"status": "resolved"}
        await _assert_status(db_session, issue, expected="resolved")

    async def test_key_with_invalid_status_returns_422_and_keeps_status(self, client, db_session):
        project = await _make_project(db_session, "keybad")
        issue = await _make_issue(db_session, project)

        resp = await _patch(client, issue.id, "closed", key=project.api_key)

        assert resp.status_code == 422
        assert "Invalid status" in resp.json()["detail"]
        await _assert_status(db_session, issue)


class TestSessionCredential:
    """Przypadki 5-7: poswiadczenie B (sesja z 500ki:write w projekcie issue)."""

    async def test_superuser_session_returns_200_and_updates_db(self, client, db_session):
        project = await _make_project(db_session, "sess-su")
        issue = await _make_issue(db_session, project)
        await login_session(client, db_session, email="mon236_superuser@example.com", is_superuser=True)

        resp = await _patch(client, issue.id, "ignored")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ignored"}
        await _assert_status(db_session, issue, expected="ignored")

    @pytest.mark.parametrize("role", ["owner", "admin"])
    async def test_member_with_write_role_returns_200_and_updates_db(self, client, db_session, role):
        project = await _make_project(db_session, f"sess-{role}")
        issue = await _make_issue(db_session, project)
        await _login_member(client, db_session, f"mon236_{role}_write@example.com", project, role)

        resp = await _patch(client, issue.id, "resolved")

        assert resp.status_code == 200
        assert resp.json() == {"status": "resolved"}
        await _assert_status(db_session, issue, expected="resolved")

    async def test_member_with_read_only_role_returns_403_and_keeps_status(self, client, db_session):
        project = await _make_project(db_session, "sess-readonly")
        issue = await _make_issue(db_session, project)
        await _login_member(client, db_session, "mon236_member_readonly@example.com", project, "member")

        resp = await _patch(client, issue.id, "resolved")

        assert resp.status_code == 403
        await _assert_status(db_session, issue)

    async def test_non_member_returns_404_and_keeps_status(self, client, db_session):
        project = await _make_project(db_session, "sess-outsider")
        issue = await _make_issue(db_session, project)
        await _login_member(client, db_session, "mon236_outsider@example.com", None, None)

        resp = await _patch(client, issue.id, "resolved")

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Issue not found"}
        await _assert_status(db_session, issue)

    async def test_member_of_other_project_only_returns_404_and_keeps_status(self, client, db_session):
        # Uprawnienie write w innym projekcie nie daje dostepu do issue tego projektu.
        own_project = await _make_project(db_session, "sess-own")
        foreign_project = await _make_project(db_session, "sess-foreign")
        issue = await _make_issue(db_session, foreign_project)
        await _login_member(client, db_session, "mon236_other_project_owner@example.com", own_project, "owner")

        resp = await _patch(client, issue.id, "resolved")

        assert resp.status_code == 404
        assert resp.json() == {"detail": "Issue not found"}
        await _assert_status(db_session, issue)

    async def test_session_with_write_and_invalid_status_returns_422(self, client, db_session):
        project = await _make_project(db_session, "sess-invalid")
        issue = await _make_issue(db_session, project)
        await _login_member(client, db_session, "mon236_owner_invalid@example.com", project, "owner")

        resp = await _patch(client, issue.id, "closed")

        assert resp.status_code == 422
        assert "Invalid status" in resp.json()["detail"]
        await _assert_status(db_session, issue)


class TestCredentialPrecedence:
    """Przypadek 9: klucz ma pierwszenstwo przed sesja."""

    async def test_valid_key_wins_over_session_without_access(self, client, db_session):
        project = await _make_project(db_session, "prec-keywins")
        issue = await _make_issue(db_session, project)
        await _login_member(client, db_session, "mon236_prec_outsider@example.com", None, None)

        resp = await _patch(client, issue.id, "resolved", key=project.api_key)

        assert resp.status_code == 200
        await _assert_status(db_session, issue, expected="resolved")

    async def test_unknown_key_is_not_rescued_by_authorized_session(self, client, db_session):
        # Zly klucz = 401 bez fallbacku na sesje, nawet gdy sesja ma 500ki:write w projekcie issue.
        project = await _make_project(db_session, "prec-badkey")
        issue = await _make_issue(db_session, project)
        await _login_member(client, db_session, "mon236_prec_owner@example.com", project, "owner")

        resp = await _patch(client, issue.id, "resolved", key="nieistniejacy-klucz-mon236")

        assert resp.status_code == 401
        assert resp.json() == {"detail": "Invalid API key"}
        await _assert_status(db_session, issue)


class TestSoftDeletedProjectWarmCache:
    """Przypadek 10: ciepły cache klucza nie omija soft delete projektu."""

    async def test_patch_with_warm_cache_after_soft_delete_returns_401(self, client, db_session):
        project = await _make_project(db_session, "softdel-patch")
        issue = await _make_issue(db_session, project)
        warm = await _patch(client, issue.id, "resolved", key=project.api_key)
        assert warm.status_code == 200
        assert project.api_key in auth_service._api_key_cache
        issue.status = "unresolved"
        project.is_active = False
        await db_session.flush()

        resp = await _patch(client, issue.id, "resolved", key=project.api_key)

        assert resp.status_code == 401
        await _assert_status(db_session, issue)

    async def test_events_with_warm_cache_after_soft_delete_returns_401(self, client, db_session, sample_event_payload):
        project = await _make_project(db_session, "softdel-events")
        headers = {"X-Monolynx-Key": project.api_key}
        warm = await client.post("/api/v1/events", json=sample_event_payload, headers=headers)
        assert warm.status_code == 202
        assert project.api_key in auth_service._api_key_cache
        project.is_active = False
        await db_session.flush()

        resp = await client.post("/api/v1/events", json=sample_event_payload, headers=headers)

        assert resp.status_code == 401


class TestEventsIngestRegression:
    """Przypadek 11: kontrakt ingestu zdarzen bez zmian."""

    async def test_events_without_header_still_returns_422(self, client, sample_event_payload):
        resp = await client.post("/api/v1/events", json=sample_event_payload)

        assert resp.status_code == 422

    async def test_events_with_unknown_key_still_returns_401(self, client, sample_event_payload):
        resp = await client.post(
            "/api/v1/events",
            json=sample_event_payload,
            headers={"X-Monolynx-Key": "nieistniejacy-klucz-mon236"},
        )

        assert resp.status_code == 401
