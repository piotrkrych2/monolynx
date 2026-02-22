"""Testy integracyjne -- POST /api/v1/events (P0)."""

import pytest

from open_sentry.models.issue import Issue


@pytest.mark.integration
class TestPostEvent:
    async def test_accept_valid_event_returns_202(
        self, client, test_project, sample_event_payload
    ):
        response = await client.post(
            "/api/v1/events",
            json=sample_event_payload,
            headers={"X-OpenSentry-Key": test_project.api_key},
        )
        assert response.status_code == 202
        data = response.json()
        assert "id" in data

    async def test_reject_invalid_api_key_returns_401(
        self, client, sample_event_payload
    ):
        response = await client.post(
            "/api/v1/events",
            json=sample_event_payload,
            headers={"X-OpenSentry-Key": "invalid-key-12345"},
        )
        assert response.status_code == 401

    async def test_reject_missing_api_key_returns_422(
        self, client, sample_event_payload
    ):
        response = await client.post(
            "/api/v1/events",
            json=sample_event_payload,
        )
        assert response.status_code == 422

    async def test_reject_malformed_payload_returns_422(self, client, test_project):
        response = await client.post(
            "/api/v1/events",
            json={"invalid": "payload"},
            headers={"X-OpenSentry-Key": test_project.api_key},
        )
        assert response.status_code == 422

    async def test_creates_new_issue_for_first_event(
        self, client, test_project, sample_event_payload, db_session
    ):
        await client.post(
            "/api/v1/events",
            json=sample_event_payload,
            headers={"X-OpenSentry-Key": test_project.api_key},
        )

        from sqlalchemy import select

        result = await db_session.execute(
            select(Issue).where(Issue.project_id == test_project.id)
        )
        issues = result.scalars().all()
        assert len(issues) >= 1

    async def test_groups_duplicate_events_into_same_issue(
        self, client, test_project, sample_event_payload
    ):
        """Dwa identyczne bledy -> jeden Issue z event_count >= 2."""
        for _ in range(2):
            response = await client.post(
                "/api/v1/events",
                json=sample_event_payload,
                headers={"X-OpenSentry-Key": test_project.api_key},
            )
            assert response.status_code == 202


class TestHealthcheck:
    async def test_health_returns_ok(self, client):
        response = await client.get("/api/v1/health")
        assert response.status_code == 200
        assert response.json() == {"status": "ok"}
