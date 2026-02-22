"""Testy schematow Pydantic dla modulu Scrum."""

import uuid
from datetime import date

import pytest
from pydantic import ValidationError

from open_sentry.schemas.scrum import (
    MemberAdd,
    SprintCreate,
    TicketCreate,
    TicketStatusUpdate,
    TicketUpdate,
)


@pytest.mark.unit
class TestTicketCreate:
    def test_minimal(self):
        t = TicketCreate(title="Bug fix")
        assert t.title == "Bug fix"
        assert t.priority == "medium"
        assert t.story_points is None
        assert t.sprint_id is None

    def test_full(self):
        sid = uuid.uuid4()
        uid = uuid.uuid4()
        t = TicketCreate(
            title="Feature",
            description="Desc",
            priority="high",
            story_points=5,
            sprint_id=sid,
            assignee_id=uid,
        )
        assert t.sprint_id == sid
        assert t.assignee_id == uid
        assert t.story_points == 5

    def test_empty_title_rejected(self):
        with pytest.raises(ValidationError):
            TicketCreate(title="")

    def test_validate_priority_valid(self):
        t = TicketCreate(title="X", priority="critical")
        assert t.validate_priority() is True

    def test_validate_priority_invalid(self):
        t = TicketCreate(title="X", priority="unknown")
        assert t.validate_priority() is False


@pytest.mark.unit
class TestTicketUpdate:
    def test_all_none_defaults(self):
        t = TicketUpdate()
        assert t.title is None
        assert t.status is None

    def test_validate_status_valid(self):
        t = TicketUpdate(status="in_progress")
        assert t.validate_status() is True

    def test_validate_status_invalid(self):
        t = TicketUpdate(status="invalid")
        assert t.validate_status() is False

    def test_validate_status_none_is_valid(self):
        t = TicketUpdate(status=None)
        assert t.validate_status() is True


@pytest.mark.unit
class TestTicketStatusUpdate:
    def test_valid_status(self):
        t = TicketStatusUpdate(status="done")
        assert t.validate_status() is True

    def test_invalid_status(self):
        t = TicketStatusUpdate(status="nope")
        assert t.validate_status() is False


@pytest.mark.unit
class TestSprintCreate:
    def test_minimal(self):
        s = SprintCreate(name="Sprint 1", start_date=date(2026, 3, 1))
        assert s.name == "Sprint 1"
        assert s.end_date is None
        assert s.goal is None

    def test_full(self):
        s = SprintCreate(
            name="Sprint 2",
            goal="Deliver MVP",
            start_date=date(2026, 3, 1),
            end_date=date(2026, 3, 14),
        )
        assert s.end_date == date(2026, 3, 14)

    def test_empty_name_rejected(self):
        with pytest.raises(ValidationError):
            SprintCreate(name="", start_date=date(2026, 3, 1))


@pytest.mark.unit
class TestMemberAdd:
    def test_default_role(self):
        m = MemberAdd(email="test@example.com")
        assert m.role == "member"

    def test_custom_role(self):
        m = MemberAdd(email="test@example.com", role="admin")
        assert m.role == "admin"

    def test_empty_email_rejected(self):
        with pytest.raises(ValidationError):
            MemberAdd(email="")
