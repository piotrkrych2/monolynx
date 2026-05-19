"""Testy integracyjne MCP tools -- plan pracy (MON-70).

Pokrywa:
1. Happy path: schedule_ticket -> list_work_plan -> get_today_tasks
   -> update_work_plan_entry -> delete_work_plan_entry
2. ticket_id przez klucz (np. TST-1) oraz UUID
3. Konflikt unikalnosci (ten sam ticket+date+user dwukrotnie)
4. Autoryzacja czlonkostwa (user spoza projektu)
5. get_today_tasks bez i z project_slug (filtrowanie)
6. list_work_plan z zakresem > 90 dni (ValueError)
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(token: str = "test-token") -> MagicMock:
    """Mock MCP Context z Bearer token w naglowku."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _mock_user() -> MagicMock:
    """Tworzy mock User."""
    user = MagicMock()
    user.id = uuid.uuid4()
    user.email = f"wp-{uuid.uuid4().hex[:8]}@test.com"
    return user


def _make_entry(
    entry_id: uuid.UUID | None = None,
    user_id: uuid.UUID | None = None,
    ticket_id: uuid.UUID | None = None,
    scheduled_date: date | None = None,
    notes: str | None = None,
    position: int = 0,
    ticket_title: str = "Testowy Ticket",
    project_slug: str = "test-proj",
    project_code: str = "TST",
    ticket_number: int = 1,
) -> MagicMock:
    """Tworzy mock WorkPlanEntry z relacjami ticket i project."""
    entry = MagicMock()
    entry.id = entry_id or uuid.uuid4()
    entry.user_id = user_id or uuid.uuid4()
    entry.ticket_id = ticket_id or uuid.uuid4()
    entry.scheduled_date = scheduled_date or date.today()
    entry.notes = notes
    entry.position = position

    ticket = MagicMock()
    ticket.id = entry.ticket_id
    ticket.title = ticket_title
    ticket.number = ticket_number

    project = MagicMock()
    project.slug = project_slug
    project.code = project_code

    ticket.project = project
    entry.ticket = ticket

    return entry


def _make_entry_response(entry: MagicMock) -> dict[str, Any]:
    """Symuluje WorkPlanEntryResponse.from_entry(entry).model_dump()."""
    return {
        "id": str(entry.id),
        "entry_id": str(entry.id),
        "user_id": str(entry.user_id),
        "ticket_id": str(entry.ticket_id),
        "ticket_title": entry.ticket.title,
        "ticket_key": f"{entry.ticket.project.code}-{entry.ticket.number}",
        "scheduled_date": str(entry.scheduled_date),
        "position": entry.position,
        "notes": entry.notes,
        "project_slug": entry.ticket.project.slug,
    }


@asynccontextmanager
async def _mock_db_session():
    """Context manager udajacy async_session_factory()."""
    session = AsyncMock()
    yield session


# ---------------------------------------------------------------------------
# 1. Happy path
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestWorkPlanHappyPath:
    """Flow: schedule_ticket -> list_work_plan -> get_today_tasks
    -> update_work_plan_entry -> delete_work_plan_entry."""

    async def test_schedule_ticket_returns_entry(self):
        """schedule_ticket zwraca dict z entry_id i ticket_id."""
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_id = str(uuid.uuid4())
        entry = _make_entry(user_id=mock_user.id, ticket_id=uuid.UUID(ticket_id))
        expected_response = _make_entry_response(entry)

        mock_response = MagicMock()
        mock_response.model_dump.return_value = expected_response

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=uuid.UUID(ticket_id))),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.schedule = AsyncMock(return_value=entry)
            mock_resp_cls.from_entry.return_value = mock_response

            result = await schedule_ticket(
                _make_ctx(),
                ticket_id=ticket_id,
                scheduled_date=str(date.today()),
            )

        assert isinstance(result, dict)
        assert "entry_id" in result or "id" in result

    async def test_list_work_plan_returns_entries(self):
        """list_work_plan zwraca liste wpisow w zakresie dat."""
        from monolynx.mcp_server import list_work_plan

        mock_user = _mock_user()
        today = date.today()
        entries = [_make_entry(user_id=mock_user.id, scheduled_date=today, ticket_number=i) for i in range(1, 4)]

        mock_responses = []
        for e in entries:
            mock_resp = MagicMock()
            mock_resp.model_dump.return_value = _make_entry_response(e)
            mock_responses.append(mock_resp)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.list_for_user_range = AsyncMock(return_value=entries)
            mock_resp_cls.from_entry.side_effect = mock_responses

            result = await list_work_plan(
                _make_ctx(),
                start_date=str(today),
                end_date=str(today + timedelta(days=7)),
            )

        assert isinstance(result, list)
        assert len(result) == 3

    async def test_get_today_tasks_returns_today_entries(self):
        """get_today_tasks zwraca wpisy na dzisiaj."""
        from monolynx.mcp_server import get_today_tasks

        mock_user = _mock_user()
        today = date.today()
        entry = _make_entry(user_id=mock_user.id, scheduled_date=today)

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = _make_entry_response(entry)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.today_for_user = AsyncMock(return_value=[entry])
            mock_resp_cls.from_entry.return_value = mock_resp

            result = await get_today_tasks(_make_ctx())

        assert isinstance(result, list)
        assert len(result) == 1

    async def test_update_work_plan_entry_changes_fields(self):
        """update_work_plan_entry zwraca zaktualizowany entry."""
        from monolynx.mcp_server import update_work_plan_entry

        mock_user = _mock_user()
        entry_id = uuid.uuid4()
        updated_entry = _make_entry(
            entry_id=entry_id,
            user_id=mock_user.id,
            notes="zaktualizowana notatka",
            position=5,
        )
        expected_response = _make_entry_response(updated_entry)

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = expected_response

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.update = AsyncMock(return_value=updated_entry)
            mock_resp_cls.from_entry.return_value = mock_resp

            result = await update_work_plan_entry(
                _make_ctx(),
                entry_id=str(entry_id),
                notes="zaktualizowana notatka",
                position=5,
            )

        assert isinstance(result, dict)

    async def test_delete_work_plan_entry_returns_confirmation(self):
        """delete_work_plan_entry zwraca potwierdzenie usuniecia."""
        from monolynx.mcp_server import delete_work_plan_entry

        mock_user = _mock_user()
        entry_id = uuid.uuid4()

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
        ):
            mock_svc.unschedule = AsyncMock(return_value=None)

            result = await delete_work_plan_entry(
                _make_ctx(),
                entry_id=str(entry_id),
            )

        assert isinstance(result, dict)
        assert result.get("success") is True or "message" in result


# ---------------------------------------------------------------------------
# 2. ticket_id przez klucz vs UUID
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTicketIdResolution:
    """schedule_ticket i get_ticket_schedule akceptuja UUID i klucz (np. TST-1)."""

    async def test_schedule_ticket_with_uuid(self):
        """schedule_ticket z ticket_id jako UUID -- sukces."""
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_uuid = uuid.uuid4()
        entry = _make_entry(user_id=mock_user.id, ticket_id=ticket_uuid)

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = _make_entry_response(entry)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=ticket_uuid)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.schedule = AsyncMock(return_value=entry)
            mock_resp_cls.from_entry.return_value = mock_resp

            result = await schedule_ticket(
                _make_ctx(),
                ticket_id=str(ticket_uuid),
                scheduled_date=str(date.today()),
            )

        assert isinstance(result, dict)

    async def test_schedule_ticket_with_key(self):
        """schedule_ticket z ticket_id jako klucz (TST-1) -- sukces.

        _resolve_ticket_globally zamienia klucz na UUID zanim dotrze do serwisu.
        """
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_uuid = uuid.uuid4()
        entry = _make_entry(user_id=mock_user.id, ticket_id=ticket_uuid, ticket_number=1)

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = _make_entry_response(entry)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=ticket_uuid)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.schedule = AsyncMock(return_value=entry)
            mock_resp_cls.from_entry.return_value = mock_resp

            result = await schedule_ticket(
                _make_ctx(),
                ticket_id="TST-1",
                scheduled_date=str(date.today()),
            )

        assert isinstance(result, dict)

    async def test_resolve_ticket_globally_called_for_key(self):
        """Wywolanie schedule_ticket z kluczem przekazuje klucz do _resolve_ticket_globally."""
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_uuid = uuid.uuid4()
        entry = _make_entry(user_id=mock_user.id, ticket_id=ticket_uuid, ticket_number=5)

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = _make_entry_response(entry)
        mock_resolve = AsyncMock(return_value=ticket_uuid)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", mock_resolve),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.schedule = AsyncMock(return_value=entry)
            mock_resp_cls.from_entry.return_value = mock_resp

            await schedule_ticket(_make_ctx(), ticket_id="TST-5", scheduled_date=str(date.today()))

        mock_resolve.assert_called_once_with("TST-5")

    async def test_get_ticket_schedule_with_uuid(self):
        """get_ticket_schedule z ticket_id jako UUID -- zwraca liste."""
        from monolynx.mcp_server import get_ticket_schedule

        mock_user = _mock_user()
        ticket_uuid = uuid.uuid4()
        entries = [_make_entry(user_id=mock_user.id, ticket_id=ticket_uuid)]

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = _make_entry_response(entries[0])

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=ticket_uuid)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.schedule_for_ticket = AsyncMock(return_value=entries)
            mock_resp_cls.from_entry.return_value = mock_resp

            result = await get_ticket_schedule(_make_ctx(), ticket_id=str(ticket_uuid))

        assert isinstance(result, list)
        assert len(result) == 1

    async def test_get_ticket_schedule_with_key(self):
        """get_ticket_schedule z ticket_id jako klucz (TST-2) -- zwraca liste."""
        from monolynx.mcp_server import get_ticket_schedule

        mock_user = _mock_user()
        ticket_uuid = uuid.uuid4()
        entries = [_make_entry(user_id=mock_user.id, ticket_id=ticket_uuid, ticket_number=2)]

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = _make_entry_response(entries[0])
        mock_resolve = AsyncMock(return_value=ticket_uuid)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", mock_resolve),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.schedule_for_ticket = AsyncMock(return_value=entries)
            mock_resp_cls.from_entry.return_value = mock_resp

            result = await get_ticket_schedule(_make_ctx(), ticket_id="TST-2")

        assert isinstance(result, list)
        mock_resolve.assert_called_once_with("TST-2")


# ---------------------------------------------------------------------------
# 3. Konflikt unique
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestScheduleConflict:
    """Dwa schedule_ticket na ten sam ticket+date+user -> czytelny komunikat z entry_id."""

    async def test_second_schedule_raises_value_error_with_entry_id(self):
        """Drugi schedule_ticket dla tego samego (user, ticket, date) rzuca ValueError z entry_id.

        Serwis zwraca str 'Ticket juz zaplanowany na ten dzien'. Tool pobiera istniejacy
        entry z DB i rzuca ValueError z jego id.
        """
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_uuid = uuid.uuid4()
        today = date.today()
        conflict_msg = "Ticket juz zaplanowany na ten dzien"

        # Istniejacy entry w DB (pobierany przez drugie wywolanie async_session_factory)
        existing_entry = _make_entry(user_id=mock_user.id, ticket_id=ticket_uuid, scheduled_date=today)

        call_count = 0

        @asynccontextmanager
        async def _db_session_with_existing():
            nonlocal call_count
            session = AsyncMock()
            if call_count > 0:
                # Drugie otwarcie -- zwroc istniejacy entry (dla konfliktu)
                mock_result = AsyncMock()
                mock_result.scalar_one_or_none = MagicMock(return_value=existing_entry)
                session.execute = AsyncMock(return_value=mock_result)
            call_count += 1
            yield session

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=ticket_uuid)),
            patch("monolynx.mcp_server.async_session_factory", _db_session_with_existing),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
        ):
            mock_svc.schedule = AsyncMock(return_value=conflict_msg)

            with pytest.raises(ValueError) as exc_info:
                await schedule_ticket(
                    _make_ctx(),
                    ticket_id=str(ticket_uuid),
                    scheduled_date=str(today),
                )

        error_text = str(exc_info.value).lower()
        assert any(kw in error_text for kw in ("zaplanowany", "entry_id", "juz"))

    async def test_conflict_message_contains_entry_id(self):
        """Komunikat bledu konfliktu zawiera entry_id istniejacego wpisu."""
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_uuid = uuid.uuid4()
        today = date.today()
        conflict_msg = "Ticket juz zaplanowany na ten dzien"

        existing_entry = _make_entry(user_id=mock_user.id, ticket_id=ticket_uuid, scheduled_date=today)
        existing_entry_id = existing_entry.id

        call_count = 0

        @asynccontextmanager
        async def _db_with_conflict():
            nonlocal call_count
            session = AsyncMock()
            if call_count > 0:
                mock_result = AsyncMock()
                mock_result.scalar_one_or_none = MagicMock(return_value=existing_entry)
                session.execute = AsyncMock(return_value=mock_result)
            call_count += 1
            yield session

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=ticket_uuid)),
            patch("monolynx.mcp_server.async_session_factory", _db_with_conflict),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
        ):
            mock_svc.schedule = AsyncMock(return_value=conflict_msg)

            with pytest.raises(ValueError) as exc_info:
                await schedule_ticket(
                    _make_ctx(),
                    ticket_id=str(ticket_uuid),
                    scheduled_date=str(today),
                )

        assert str(existing_entry_id) in str(exc_info.value)


# ---------------------------------------------------------------------------
# 4. Autoryzacja czlonkostwa
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMembershipAuthorization:
    """User nie bedacy czlonkiem projektu nie moze zaplanowac ticketu."""

    async def test_non_member_schedule_ticket_returns_error(self):
        """schedule_ticket dla usera spoza projektu rzuca ValueError z czytelnym komunikatem."""
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_id = str(uuid.uuid4())
        error_msg = "Uzytkownik nie jest czlonkiem projektu"

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=uuid.UUID(ticket_id))),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
        ):
            mock_svc.schedule = AsyncMock(return_value=error_msg)

            with pytest.raises(ValueError) as exc_info:
                await schedule_ticket(
                    _make_ctx(),
                    ticket_id=ticket_id,
                    scheduled_date=str(date.today()),
                )

        error_text = str(exc_info.value).lower()
        assert any(kw in error_text for kw in ("czlonek", "member", "dostep", "nie jest"))

    async def test_non_member_error_is_not_empty(self):
        """Komunikat bledu czlonkostwa nie jest pusty."""
        from monolynx.mcp_server import schedule_ticket

        mock_user = _mock_user()
        ticket_id = str(uuid.uuid4())
        error_msg = "Uzytkownik nie jest czlonkiem projektu"

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server._resolve_ticket_globally", AsyncMock(return_value=uuid.UUID(ticket_id))),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
        ):
            mock_svc.schedule = AsyncMock(return_value=error_msg)

            with pytest.raises(ValueError) as exc_info:
                await schedule_ticket(
                    _make_ctx(),
                    ticket_id=ticket_id,
                    scheduled_date=str(date.today()),
                )

        assert len(str(exc_info.value)) > 5


# ---------------------------------------------------------------------------
# 5. get_today_tasks bez vs z project_slug
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGetTodayTasksFiltering:
    """get_today_tasks bez project_slug zwraca wszystkie, z project_slug filtruje do jednego projektu."""

    async def test_today_without_project_slug_returns_all(self):
        """get_today_tasks() bez project_slug zwraca wpisy ze wszystkich projektow."""
        from monolynx.mcp_server import get_today_tasks

        mock_user = _mock_user()
        today = date.today()

        entry_a = _make_entry(user_id=mock_user.id, scheduled_date=today, project_slug="proj-a", ticket_number=1)
        entry_b = _make_entry(user_id=mock_user.id, scheduled_date=today, project_slug="proj-b", ticket_number=2)

        mock_resp_a = MagicMock()
        mock_resp_a.model_dump.return_value = _make_entry_response(entry_a)
        mock_resp_b = MagicMock()
        mock_resp_b.model_dump.return_value = _make_entry_response(entry_b)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.today_for_user = AsyncMock(return_value=[entry_a, entry_b])
            mock_resp_cls.from_entry.side_effect = [mock_resp_a, mock_resp_b]

            # Brak project_slug --> wszystkie projekty
            result = await get_today_tasks(_make_ctx())

        assert isinstance(result, list)
        assert len(result) == 2
        # Upewnij sie ze today_for_user (bez filtru) zostal wywolany
        mock_svc.today_for_user.assert_called_once()

    async def test_today_with_project_slug_uses_project_filter(self):
        """get_today_tasks(project_slug=...) wywoluje today_for_user_in_project."""
        from monolynx.mcp_server import get_today_tasks

        mock_user = _mock_user()
        today = date.today()
        project_id = uuid.uuid4()

        entry_a = _make_entry(user_id=mock_user.id, scheduled_date=today, project_slug="proj-a", ticket_number=1)

        mock_resp = MagicMock()
        mock_resp.model_dump.return_value = _make_entry_response(entry_a)

        @asynccontextmanager
        async def _db_with_project():
            session = AsyncMock()
            proj_result = AsyncMock()
            proj_result.scalar_one_or_none = MagicMock(return_value=project_id)
            session.execute = AsyncMock(return_value=proj_result)
            yield session

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _db_with_project),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.today_for_user_in_project = AsyncMock(return_value=[entry_a])
            mock_resp_cls.from_entry.return_value = mock_resp

            result = await get_today_tasks(_make_ctx(), project_slug="proj-a")

        assert isinstance(result, list)
        assert len(result) == 1
        mock_svc.today_for_user_in_project.assert_called_once()

    async def test_today_two_projects_filtered_returns_one(self):
        """Dwa projekty -- get_today_tasks(project_slug='proj-a') zwraca 1, bez filtra 2."""
        from monolynx.mcp_server import get_today_tasks

        mock_user = _mock_user()
        today = date.today()
        project_id = uuid.uuid4()

        entry_a = _make_entry(user_id=mock_user.id, scheduled_date=today, project_slug="proj-a", ticket_number=1)
        entry_b = _make_entry(user_id=mock_user.id, scheduled_date=today, project_slug="proj-b", ticket_number=2)

        mock_resp_a = MagicMock()
        mock_resp_a.model_dump.return_value = _make_entry_response(entry_a)
        mock_resp_b = MagicMock()
        mock_resp_b.model_dump.return_value = _make_entry_response(entry_b)

        @asynccontextmanager
        async def _db_with_project():
            session = AsyncMock()
            proj_result = AsyncMock()
            proj_result.scalar_one_or_none = MagicMock(return_value=project_id)
            session.execute = AsyncMock(return_value=proj_result)
            yield session

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _db_with_project),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            # Z project_slug -- serwis zwraca tylko 1 wpis (przefiltrowany)
            mock_svc.today_for_user_in_project = AsyncMock(return_value=[entry_a])
            mock_resp_cls.from_entry.return_value = mock_resp_a

            result_filtered = await get_today_tasks(_make_ctx(), project_slug="proj-a")

        assert len(result_filtered) == 1

        # Bez project_slug -- serwis zwraca 2 wpisy
        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse") as mock_resp_cls,
        ):
            mock_svc.today_for_user = AsyncMock(return_value=[entry_a, entry_b])
            mock_resp_cls.from_entry.side_effect = [mock_resp_a, mock_resp_b]

            result_all = await get_today_tasks(_make_ctx())

        assert len(result_all) == 2


# ---------------------------------------------------------------------------
# 6. Range > 90 dni -- ValueError
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestListWorkPlanRangeValidation:
    """list_work_plan z zakresem > 90 dni zwraca czytelny ValueError."""

    async def test_range_91_days_raises_error(self):
        """list_work_plan(start, end) gdzie end-start = 91 dni -> ValueError."""
        from monolynx.mcp_server import list_work_plan

        mock_user = _mock_user()
        start = date.today()
        end = start + timedelta(days=91)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            pytest.raises(ValueError) as exc_info,
        ):
            await list_work_plan(
                _make_ctx(),
                start_date=str(start),
                end_date=str(end),
            )

        error_text = str(exc_info.value).lower()
        assert any(kw in error_text for kw in ("90", "zakres", "max", "limit", "zbyt", "dni", "przekrac"))

    async def test_range_exactly_90_days_is_allowed(self):
        """list_work_plan z zakresem dokladnie 90 dni nie rzuca bledu."""
        from monolynx.mcp_server import list_work_plan

        mock_user = _mock_user()
        start = date.today()
        end = start + timedelta(days=90)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            patch("monolynx.mcp_server.work_plan_service") as mock_svc,
            patch("monolynx.mcp_server.WorkPlanEntryResponse"),
        ):
            mock_svc.list_for_user_range = AsyncMock(return_value=[])

            result = await list_work_plan(
                _make_ctx(),
                start_date=str(start),
                end_date=str(end),
            )

        assert isinstance(result, list)

    async def test_range_200_days_raises_descriptive_error(self):
        """list_work_plan z zakresem 200 dni rzuca czytelny komunikat."""
        from monolynx.mcp_server import list_work_plan

        mock_user = _mock_user()
        start = date.today()
        end = start + timedelta(days=200)

        with (
            patch("monolynx.mcp_server._auth", AsyncMock(return_value=mock_user)),
            patch("monolynx.mcp_server.async_session_factory", _mock_db_session),
            pytest.raises(ValueError) as exc_info,
        ):
            await list_work_plan(
                _make_ctx(),
                start_date=str(start),
                end_date=str(end),
            )

        assert len(str(exc_info.value)) > 5
