"""Testy integracyjne -- EventProcessor (process_event)."""

import secrets

import pytest

from open_sentry.models.project import Project
from open_sentry.schemas.events import EventPayload
from open_sentry.services.event_processor import process_event


async def _create_project(db_session, slug=None):
    if slug is None:
        slug = f"ep-{secrets.token_hex(4)}"
    project = Project(
        name=f"Event Proc {slug}",
        slug=slug,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


def _make_payload(**overrides):
    """Tworzy prawidlowy EventPayload."""
    data = {
        "exception": {
            "type": "ValueError",
            "value": "test error",
            "stacktrace": {
                "frames": [
                    {
                        "filename": "app/views.py",
                        "function": "handle",
                        "lineno": 42,
                    }
                ]
            },
        },
        "platform": "python",
        "level": "error",
    }
    data.update(overrides)
    return EventPayload(**data)


@pytest.mark.integration
class TestProcessEvent:
    async def test_creates_new_issue_for_first_event(self, db_session):
        """Pierwszy event tworzy nowy Issue."""
        project = await _create_project(db_session, slug="ep-first")
        payload = _make_payload()

        event_id = await process_event(payload, project, db_session)
        assert event_id is not None

    async def test_groups_duplicate_events(self, db_session):
        """Dwa identyczne eventy trafiaja do tego samego Issue."""
        project = await _create_project(db_session, slug="ep-dup")
        payload = _make_payload()

        event_id_1 = await process_event(payload, project, db_session)
        event_id_2 = await process_event(payload, project, db_session)

        assert event_id_1 != event_id_2

        # Sprawdz ze Issue ma event_count=2
        from sqlalchemy import select

        from open_sentry.models.issue import Issue

        result = await db_session.execute(
            select(Issue).where(Issue.project_id == project.id)
        )
        issues = result.scalars().all()
        assert len(issues) == 1
        assert issues[0].event_count == 2

    async def test_creates_issue_with_correct_title(self, db_session):
        """Issue tytul to 'ExceptionType: value'."""
        project = await _create_project(db_session, slug="ep-title")
        payload = _make_payload(
            exception={
                "type": "RuntimeError",
                "value": "something broke",
                "stacktrace": {
                    "frames": [
                        {
                            "filename": "main.py",
                            "function": "run",
                            "lineno": 10,
                        }
                    ]
                },
            }
        )

        await process_event(payload, project, db_session)

        from sqlalchemy import select

        from open_sentry.models.issue import Issue

        result = await db_session.execute(
            select(Issue).where(Issue.project_id == project.id)
        )
        issue = result.scalar_one()
        assert issue.title == "RuntimeError: something broke"
        assert issue.culprit == "main.py in run"

    async def test_creates_issue_with_no_frames(self, db_session):
        """Issue bez stacktrace frames ma culprit=None."""
        project = await _create_project(db_session, slug="ep-noframes")
        payload = _make_payload(
            exception={
                "type": "ImportError",
                "value": "No module named foo",
                "stacktrace": {"frames": []},
            }
        )

        await process_event(payload, project, db_session)

        from sqlalchemy import select

        from open_sentry.models.issue import Issue

        result = await db_session.execute(
            select(Issue).where(Issue.project_id == project.id)
        )
        issue = result.scalar_one()
        assert issue.culprit is None

    async def test_event_with_request_data(self, db_session):
        """Event z danymi request zapisuje je."""
        project = await _create_project(db_session, slug="ep-req")
        payload = _make_payload(
            request={
                "url": "https://example.com/api/test",
                "method": "POST",
            }
        )

        event_id = await process_event(payload, project, db_session)
        assert event_id is not None

    async def test_event_with_custom_fingerprint(self, db_session):
        """Event z wlasnym fingerprintem uzywa go zamiast wyliczonego."""
        project = await _create_project(db_session, slug="ep-fp")
        payload = _make_payload(fingerprint="custom-fingerprint-123")

        await process_event(payload, project, db_session)

        from sqlalchemy import select

        from open_sentry.models.issue import Issue

        result = await db_session.execute(
            select(Issue).where(Issue.project_id == project.id)
        )
        issue = result.scalar_one()
        assert issue.fingerprint == "custom-fingerprint-123"

    async def test_event_with_timestamp(self, db_session):
        """Event z podanym timestampem uzywa go."""
        project = await _create_project(db_session, slug="ep-ts")
        payload = _make_payload(timestamp="2026-02-20T15:30:00Z")

        event_id = await process_event(payload, project, db_session)
        assert event_id is not None
