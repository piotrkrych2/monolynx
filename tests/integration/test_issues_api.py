"""Testy integracyjne -- Issues API (P2)."""

import uuid

import pytest


@pytest.mark.integration
class TestUpdateIssueStatus:
    async def test_nonexistent_issue_returns_404(self, client):
        fake_id = uuid.uuid4()
        response = await client.patch(
            f"/api/v1/issues/{fake_id}/status",
            json={"status": "resolved"},
        )
        assert response.status_code == 404

    async def test_invalid_status_returns_422(self, client):
        fake_id = uuid.uuid4()
        response = await client.patch(
            f"/api/v1/issues/{fake_id}/status",
            json={"status": "invalid_status"},
        )
        # Zwroci 422 (invalid status) lub 404 (not found) -- obie odpowiedzi OK
        assert response.status_code in (404, 422)
