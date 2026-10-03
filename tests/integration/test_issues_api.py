"""Testy integracyjne -- Issues API (P2)."""

import secrets
import uuid

import pytest

from monolynx.models.project import Project


async def _project_key(db_session) -> str:
    project = Project(
        name="Issues API project",
        slug=f"issues-api-{secrets.token_hex(3)}",
        code="I" + secrets.token_hex(4).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project.api_key


@pytest.mark.integration
class TestUpdateIssueStatus:
    async def test_nonexistent_issue_returns_404(self, client, db_session):
        key = await _project_key(db_session)
        fake_id = uuid.uuid4()
        response = await client.patch(
            f"/api/v1/issues/{fake_id}/status",
            json={"status": "resolved"},
            headers={"X-Monolynx-Key": key},
        )
        assert response.status_code == 404

    async def test_invalid_status_returns_422(self, client, db_session):
        key = await _project_key(db_session)
        fake_id = uuid.uuid4()
        response = await client.patch(
            f"/api/v1/issues/{fake_id}/status",
            json={"status": "invalid_status"},
            headers={"X-Monolynx-Key": key},
        )
        # Walidacja statusu poprzedza lookup issue, wiec nieistniejacy issue i tak daje 422.
        assert response.status_code == 422
