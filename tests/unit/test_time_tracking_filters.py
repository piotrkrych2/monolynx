"""Testy filtrow raportu pracy -- pusta lista to zero wierszy, None to brak filtra (MON-234)."""

import secrets
import uuid
from datetime import date

import pytest
from sqlalchemy import select

from monolynx.models.project import Project
from monolynx.models.sprint import Sprint
from monolynx.models.ticket import Ticket
from monolynx.models.time_tracking_entry import TimeTrackingEntry
from monolynx.models.user import User
from monolynx.schemas.time_tracking import TimeTrackingFilter
from monolynx.services.auth import hash_password
from monolynx.services.time_tracking import (
    _apply_filters,
    aggregate_hours_per_project,
    aggregate_hours_per_sprint,
    aggregate_hours_per_user,
    get_work_report,
)


async def _seed_project_entry(db_session, *, tag, minutes, with_sprint=True):
    """Tworzy projekt, autora, opcjonalny sprint, ticket i jeden wpis czasu."""
    project = Project(
        name=f"Filtr {tag}",
        slug=f"flt-{tag}-{uuid.uuid4().hex[:6]}",
        code="F" + secrets.token_hex(3).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    user = User(email=f"flt-{tag}-{uuid.uuid4().hex[:6]}@test.com", password_hash=hash_password("testpass123"))
    db_session.add_all([project, user])
    await db_session.flush()

    sprint = None
    if with_sprint:
        sprint = Sprint(project_id=project.id, name=f"Sprint {tag}", start_date=date(2026, 1, 1))
        db_session.add(sprint)
        await db_session.flush()

    ticket = Ticket(project_id=project.id, number=1, title=f"Ticket {tag}", sprint_id=sprint.id if sprint else None)
    db_session.add(ticket)
    await db_session.flush()

    entry = TimeTrackingEntry(
        ticket_id=ticket.id,
        user_id=user.id,
        project_id=project.id,
        sprint_id=sprint.id if sprint else None,
        duration_minutes=minutes,
        date_logged=date(2026, 1, 5),
        description=f"Wpis {tag}",
    )
    db_session.add(entry)
    await db_session.flush()
    return {"project": project, "user": user, "sprint": sprint, "entry": entry}


@pytest.fixture
async def two_projects(db_session):
    """Dwa projekty z jednym wpisem kazdy (A = 60 min, B = 120 min)."""
    a = await _seed_project_entry(db_session, tag="a", minutes=60)
    b = await _seed_project_entry(db_session, tag="b", minutes=120)
    return a, b


async def _entry_ids(db_session, filters):
    result = await db_session.execute(_apply_filters(select(TimeTrackingEntry.id), filters))
    return set(result.scalars().all())


@pytest.mark.unit
class TestApplyFiltersProjectIds:
    async def test_none_means_no_restriction(self, db_session, two_projects):
        a, b = two_projects

        ids = await _entry_ids(db_session, TimeTrackingFilter(project_ids=None))

        assert {a["entry"].id, b["entry"].id} <= ids

    async def test_empty_list_returns_zero_rows(self, db_session, two_projects):
        ids = await _entry_ids(db_session, TimeTrackingFilter(project_ids=[]))

        assert ids == set()

    async def test_single_project_returns_only_its_rows(self, db_session, two_projects):
        a, b = two_projects

        ids = await _entry_ids(db_session, TimeTrackingFilter(project_ids=[a["project"].id]))

        assert ids == {a["entry"].id}
        assert b["entry"].id not in ids

    async def test_empty_list_wins_over_other_matching_filters(self, db_session, two_projects):
        a, _b = two_projects

        ids = await _entry_ids(
            db_session,
            TimeTrackingFilter(project_ids=[], user_ids=[a["user"].id], sprint_ids=[a["sprint"].id]),
        )

        assert ids == set()


@pytest.mark.unit
class TestApplyFiltersUserIds:
    async def test_none_means_no_restriction(self, db_session, two_projects):
        a, b = two_projects

        ids = await _entry_ids(db_session, TimeTrackingFilter(user_ids=None))

        assert {a["entry"].id, b["entry"].id} <= ids

    async def test_empty_list_returns_zero_rows(self, db_session, two_projects):
        ids = await _entry_ids(db_session, TimeTrackingFilter(user_ids=[]))

        assert ids == set()

    async def test_single_user_returns_only_its_rows(self, db_session, two_projects):
        a, b = two_projects

        ids = await _entry_ids(db_session, TimeTrackingFilter(user_ids=[b["user"].id]))

        assert ids == {b["entry"].id}
        assert a["entry"].id not in ids


@pytest.mark.unit
class TestApplyFiltersSprintIds:
    async def test_none_means_no_restriction(self, db_session, two_projects):
        a, b = two_projects

        ids = await _entry_ids(db_session, TimeTrackingFilter(sprint_ids=None))

        assert {a["entry"].id, b["entry"].id} <= ids

    async def test_empty_list_returns_zero_rows(self, db_session, two_projects):
        ids = await _entry_ids(db_session, TimeTrackingFilter(sprint_ids=[]))

        assert ids == set()

    async def test_single_sprint_returns_only_its_rows(self, db_session, two_projects):
        a, b = two_projects

        ids = await _entry_ids(db_session, TimeTrackingFilter(sprint_ids=[a["sprint"].id]))

        assert ids == {a["entry"].id}
        assert b["entry"].id not in ids


@pytest.mark.unit
class TestWorkReportEmptyProjectIds:
    async def test_get_work_report_returns_empty_report(self, db_session, two_projects):
        report = await get_work_report(TimeTrackingFilter(project_ids=[]), db_session)

        assert report.entries == []
        assert report.entry_count == 0
        assert report.total_hours == 0
        assert report.hours_by_user == {}
        assert report.hours_by_sprint == {}
        assert report.hours_by_project == {}
        assert report.date_range is None

    async def test_get_work_report_unpaginated_returns_empty_report(self, db_session, two_projects):
        report = await get_work_report(TimeTrackingFilter(project_ids=[]), db_session, paginate=False)

        assert report.entries == []
        assert report.entry_count == 0

    async def test_get_work_report_single_project_counts_only_that_project(self, db_session, two_projects):
        a, b = two_projects

        report = await get_work_report(TimeTrackingFilter(project_ids=[a["project"].id]), db_session)

        assert [e.id for e in report.entries] == [a["entry"].id]
        assert report.entry_count == 1
        assert report.total_hours == 1.0
        assert report.hours_by_project == {str(a["project"].id): 1.0}
        assert str(b["project"].id) not in report.hours_by_project
        assert report.hours_by_user == {str(a["user"].id): 1.0}
        assert report.hours_by_sprint == {str(a["sprint"].id): 1.0}

    @pytest.mark.parametrize(
        "aggregate",
        [aggregate_hours_per_user, aggregate_hours_per_sprint, aggregate_hours_per_project],
    )
    async def test_aggregation_with_empty_project_ids_is_empty(self, db_session, two_projects, aggregate):
        result = await aggregate(TimeTrackingFilter(project_ids=[]), db_session)

        assert result == {}

    @pytest.mark.parametrize(
        "aggregate",
        [aggregate_hours_per_user, aggregate_hours_per_sprint, aggregate_hours_per_project],
    )
    async def test_aggregation_with_none_project_ids_includes_all_projects(self, db_session, two_projects, aggregate):
        a, b = two_projects

        result = await aggregate(TimeTrackingFilter(project_ids=None), db_session)

        keys = {
            aggregate_hours_per_user: (a["user"].id, b["user"].id),
            aggregate_hours_per_sprint: (a["sprint"].id, b["sprint"].id),
            aggregate_hours_per_project: (a["project"].id, b["project"].id),
        }[aggregate]
        assert result[keys[0]] == 1.0
        assert result[keys[1]] == 2.0

    async def test_user_ids_empty_gives_empty_report(self, db_session, two_projects):
        report = await get_work_report(TimeTrackingFilter(user_ids=[]), db_session)

        assert report.entry_count == 0
        assert report.hours_by_project == {}

    async def test_sprint_ids_empty_gives_empty_report(self, db_session, two_projects):
        report = await get_work_report(TimeTrackingFilter(sprint_ids=[]), db_session)

        assert report.entry_count == 0
        assert report.hours_by_sprint == {}
