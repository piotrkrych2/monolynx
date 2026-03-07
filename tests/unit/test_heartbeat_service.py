"""Testy serwisu heartbeat -- get_heartbeat_status, CRUD."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from monolynx.models.heartbeat import Heartbeat
from monolynx.models.project import Project
from monolynx.services.heartbeat import (
    create_heartbeat,
    delete_heartbeat,
    get_heartbeat_by_token,
    get_heartbeat_status,
    update_heartbeat,
)


def _make_heartbeat(
    *,
    last_ping_at: datetime | None = None,
    period: int = 300,
    grace: int = 60,
) -> MagicMock:
    hb = MagicMock(spec=Heartbeat)
    hb.last_ping_at = last_ping_at
    hb.period = period
    hb.grace = grace
    return hb


@pytest.mark.unit
class TestGetHeartbeatStatus:
    def test_pending_when_no_last_ping(self):
        """Brak last_ping_at -> status 'pending'."""
        hb = _make_heartbeat(last_ping_at=None)
        assert get_heartbeat_status(hb) == "pending"

    def test_up_when_within_deadline(self):
        """Ostatni ping w oknie tolerancji -> status 'up'."""
        hb = _make_heartbeat(
            last_ping_at=datetime.now(UTC) - timedelta(seconds=10),
            period=300,
            grace=60,
        )
        assert get_heartbeat_status(hb) == "up"

    def test_down_when_past_deadline(self):
        """Ostatni ping przekroczyl period+grace -> status 'down'."""
        hb = _make_heartbeat(
            last_ping_at=datetime.now(UTC) - timedelta(seconds=500),
            period=300,
            grace=60,
        )
        assert get_heartbeat_status(hb) == "down"

    def test_up_at_exact_deadline(self):
        """Ping dokladnie na granicy deadline -> 'up'."""
        now = datetime.now(UTC)
        hb = _make_heartbeat(
            last_ping_at=now - timedelta(seconds=359),
            period=300,
            grace=60,
        )
        assert get_heartbeat_status(hb) == "up"

    def test_naive_datetime_treated_as_utc(self):
        """Naive datetime (bez tzinfo) jest konwertowane na UTC."""
        hb = _make_heartbeat(
            last_ping_at=datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=10),
            period=300,
            grace=60,
        )
        # Should not raise, should return "up"
        assert get_heartbeat_status(hb) == "up"


def _make_project(slug: str, code: str) -> Project:
    return Project(
        name=f"HB Svc {slug}",
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


@pytest.mark.integration
class TestCreateHeartbeat:
    async def test_creates_with_correct_fields(self, db_session):
        project = _make_project("hbsvc-create", "HSC")
        db_session.add(project)
        await db_session.flush()

        hb = await create_heartbeat(db_session, project.id, {"name": "My Cron", "period": 600, "grace": 120})

        assert hb.name == "My Cron"
        assert hb.period == 600
        assert hb.grace == 120
        assert hb.project_id == project.id
        assert hb.token.startswith("hb_")

    async def test_default_grace(self, db_session):
        project = _make_project("hbsvc-defgr", "HSD")
        db_session.add(project)
        await db_session.flush()

        hb = await create_heartbeat(db_session, project.id, {"name": "No Grace", "period": 300})

        assert hb.grace == 60


@pytest.mark.integration
class TestUpdateHeartbeat:
    async def test_updates_name(self, db_session):
        project = _make_project("hbsvc-upd", "HSU")
        db_session.add(project)
        await db_session.flush()

        hb = await create_heartbeat(db_session, project.id, {"name": "Old", "period": 300, "grace": 60})

        updated = await update_heartbeat(db_session, project.id, hb.id, {"name": "New"})

        assert updated.name == "New"
        assert updated.period == 300  # unchanged

    async def test_updates_period_and_grace(self, db_session):
        project = _make_project("hbsvc-updpg", "HSP")
        db_session.add(project)
        await db_session.flush()

        hb = await create_heartbeat(db_session, project.id, {"name": "PG", "period": 300, "grace": 60})

        updated = await update_heartbeat(db_session, project.id, hb.id, {"period": 600, "grace": 120})

        assert updated.period == 600
        assert updated.grace == 120


@pytest.mark.integration
class TestDeleteHeartbeat:
    async def test_deletes_heartbeat(self, db_session):
        project = _make_project("hbsvc-del", "HDE")
        db_session.add(project)
        await db_session.flush()

        hb = await create_heartbeat(db_session, project.id, {"name": "Del", "period": 300, "grace": 60})
        hb_id = hb.id

        await delete_heartbeat(db_session, project.id, hb_id)

        result = await get_heartbeat_by_token(db_session, hb.token)
        assert result is None


@pytest.mark.integration
class TestGetHeartbeatByToken:
    async def test_returns_heartbeat(self, db_session):
        project = _make_project("hbsvc-tok", "HTK")
        db_session.add(project)
        await db_session.flush()

        hb = await create_heartbeat(db_session, project.id, {"name": "Token", "period": 300, "grace": 60})

        found = await get_heartbeat_by_token(db_session, hb.token)
        assert found is not None
        assert found.id == hb.id

    async def test_returns_none_for_unknown_token(self, db_session):
        result = await get_heartbeat_by_token(db_session, "hb_nonexistent_token_1234")
        assert result is None
