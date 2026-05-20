"""Test E2E -- modul Plan pracy (cross-project).

Scenariusz: jeden uzytkownik, dwa projekty, schedule/list/filtr/patch/delete.
MON-71.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import date
from unittest.mock import MagicMock

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import async_sessionmaker

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.models.user_api_token import UserApiToken
from monolynx.models.work_plan import WorkPlanEntry
from monolynx.services.mcp_auth import generate_api_token
from tests.conftest import login_session

# Stala data uzywana we wszystkich krokach testu
_DATE_A = "2026-05-20"
_DATE_B = "2026-05-21"


def _mcp_ctx(token: str) -> MagicMock:
    """Mock MCP Context z Bearer token (realny token z bazy)."""
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


@pytest.mark.integration
async def test_e2e_work_plan_cross_project(client, db_session):
    """Scenariusz cross-project:
    1. Setup: user, 2 projekty, 1 ticket per projekt, membership owner.
    2. Schedule ticket_a na DATE_A.
    3. Schedule ticket_b na DATE_A.
    4. GET api/data -> 2 entries.
    5. Filtr project_ids=proj_a -> 1 entry.
    6. PATCH ticket_a entry -> DATE_B.
    7. GET DATE_A -> tylko ticket_b.
    8. DELETE ticket_b entry.
    9. GET DATE_A -> pusta lista.
    """
    # --- Arrange -------------------------------------------------------
    uid = uuid.uuid4().hex[:8]

    proj_a = Project(
        name=f"E2E Proj A {uid}",
        slug=f"e2e-proj-a-{uid}",
        code=f"EPA{uid[:3].upper()}",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    proj_b = Project(
        name=f"E2E Proj B {uid}",
        slug=f"e2e-proj-b-{uid}",
        code=f"EPB{uid[:3].upper()}",
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(proj_a)
    db_session.add(proj_b)
    await db_session.flush()

    # login_session tworzy usera i loguje (zwraca client z sesja)
    email = f"wp-e2e-{uid}@test.com"
    await login_session(client, db_session, email=email, is_superuser=False)

    # Pobierz user_id z sesji przez zapytanie do bazy
    from sqlalchemy import select

    from monolynx.models.user import User

    result = await db_session.execute(select(User).where(User.email == email))
    user = result.scalar_one()

    member_a = ProjectMember(project_id=proj_a.id, user_id=user.id, role="owner")
    member_b = ProjectMember(project_id=proj_b.id, user_id=user.id, role="owner")
    db_session.add(member_a)
    db_session.add(member_b)
    await db_session.flush()

    ticket_a = Ticket(
        project_id=proj_a.id,
        number=1,
        title="Ticket A E2E",
        status="backlog",
        priority="medium",
    )
    ticket_b = Ticket(
        project_id=proj_b.id,
        number=1,
        title="Ticket B E2E",
        status="backlog",
        priority="medium",
    )
    db_session.add(ticket_a)
    db_session.add(ticket_b)
    await db_session.flush()

    # --- Act: Schedule ticket_a na DATE_A ----------------------------
    r = await client.post(
        "/dashboard/plan/entries",
        json={"ticket_id": str(ticket_a.id), "scheduled_date": _DATE_A},
    )
    assert r.status_code == 200, f"Oczekiwano 200, dostano {r.status_code}: {r.text}"
    entry_a_id = r.json()["id"]
    assert r.json()["ticket_id"] == str(ticket_a.id)

    # --- Act: Schedule ticket_b na DATE_A ----------------------------
    r = await client.post(
        "/dashboard/plan/entries",
        json={"ticket_id": str(ticket_b.id), "scheduled_date": _DATE_A},
    )
    assert r.status_code == 200, f"Oczekiwano 200, dostano {r.status_code}: {r.text}"
    entry_b_id = r.json()["id"]
    assert r.json()["ticket_id"] == str(ticket_b.id)

    # --- Assert: GET api/data -> 2 entries ---------------------------
    r = await client.get(f"/dashboard/plan/api/data?start={_DATE_A}&end={_DATE_A}")
    assert r.status_code == 200, r.text
    data = r.json()
    assert len(data) == 2, f"Oczekiwano 2 entries, dostano {len(data)}: {data}"

    project_ids_in_data = {entry["project_id"] for entry in data}
    assert str(proj_a.id) in project_ids_in_data, "Brak proj_a w wynikach"
    assert str(proj_b.id) in project_ids_in_data, "Brak proj_b w wynikach"

    # --- Assert: Filtr project_ids=proj_a -> 1 entry -----------------
    r = await client.get(f"/dashboard/plan/api/data?start={_DATE_A}&end={_DATE_A}&project_ids={proj_a.id}")
    assert r.status_code == 200, r.text
    data_filtered = r.json()
    assert len(data_filtered) == 1, f"Oczekiwano 1 entry po filtrze, dostano {len(data_filtered)}"
    assert data_filtered[0]["ticket_id"] == str(ticket_a.id)
    assert data_filtered[0]["project_id"] == str(proj_a.id)

    # --- Act: PATCH ticket_a entry -> DATE_B -------------------------
    r = await client.patch(
        f"/dashboard/plan/entries/{entry_a_id}",
        json={"scheduled_date": _DATE_B},
    )
    assert r.status_code == 200, f"PATCH zwrocil {r.status_code}: {r.text}"
    assert r.json()["scheduled_date"] == _DATE_B

    # --- Assert: GET DATE_A -> tylko ticket_b ------------------------
    r = await client.get(f"/dashboard/plan/api/data?start={_DATE_A}&end={_DATE_A}")
    assert r.status_code == 200, r.text
    data_after_patch = r.json()
    assert len(data_after_patch) == 1, f"Po PATCH oczekiwano 1 entry na DATE_A, dostano {len(data_after_patch)}"
    assert data_after_patch[0]["ticket_id"] == str(ticket_b.id)

    # --- Act: DELETE ticket_b entry ----------------------------------
    r = await client.delete(f"/dashboard/plan/entries/{entry_b_id}")
    assert r.status_code in (200, 204), f"DELETE zwrocil {r.status_code}: {r.text}"

    # --- Assert: GET DATE_A -> pusta lista ---------------------------
    r = await client.get(f"/dashboard/plan/api/data?start={_DATE_A}&end={_DATE_A}")
    assert r.status_code == 200, r.text
    data_empty = r.json()
    assert len(data_empty) == 0, f"Po DELETE oczekiwano pustej listy, dostano {len(data_empty)}"


@pytest.mark.integration
async def test_mcp_schedule_ticket_persists_to_separate_session(engine, monkeypatch):
    """Regression MON-71: schedule_ticket (MCP) commituje, wiec list_work_plan
    w OSOBNEJ sesji widzi wpis. Lapie brak db.commit() w warstwie serwisu.

    W odroznieniu od test_mcp_work_plan.py (mockuje serwis i sesje) ten test uzywa
    realnego async_session_factory (podmienionego na test-engine). Tylko osobne sesje
    na realnej bazie wykryja brakujacy commit: bez commitu zapis schedule_ticket
    rollbackuje przy zamknieciu sesji, a list_work_plan zwraca pusto.
    """
    from monolynx import mcp_server

    # Podmien fabryke sesji MCP na zwiazana z testowa baza (osobne, realne sesje + commit)
    test_factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(mcp_server, "async_session_factory", test_factory)

    uid = uuid.uuid4().hex[:8]
    raw_token, token_hash = generate_api_token()
    day = date.today()

    # --- Setup: utrwal realne wiersze w osobnej sesji ----------------
    async with test_factory() as setup_db:
        user = User(email=f"wp-mcp-{uid}@test.com", is_superuser=False)
        setup_db.add(user)
        await setup_db.flush()
        setup_db.add(
            UserApiToken(
                user_id=user.id,
                token_hash=token_hash,
                token_prefix=raw_token[:8],
                name="e2e-mcp",
            )
        )
        project = Project(
            name=f"WP MCP {uid}",
            slug=f"wp-mcp-{uid}",
            code=f"WM{uid[:3].upper()}",
            api_key=secrets.token_urlsafe(32),
            is_active=True,
        )
        setup_db.add(project)
        await setup_db.flush()
        setup_db.add(ProjectMember(project_id=project.id, user_id=user.id, role="owner"))
        ticket = Ticket(
            project_id=project.id,
            number=1,
            title="MCP Persist Ticket",
            status="backlog",
            priority="medium",
        )
        setup_db.add(ticket)
        await setup_db.commit()
        user_id = user.id
        project_id = project.id
        ticket_id = ticket.id

    ctx = _mcp_ctx(raw_token)

    try:
        # --- Act: schedule przez realny MCP tool (wlasna sesja + commit) ---
        created = await mcp_server.schedule_ticket(ctx, ticket_id=str(ticket_id), scheduled_date=day.isoformat())
        assert created["ticket_id"] == str(ticket_id)

        # --- Assert: list w OSOBNEJ sesji widzi wpis (lapie brak commit) ---
        listed = await mcp_server.list_work_plan(ctx, start_date=day.isoformat(), end_date=day.isoformat())
        assert len(listed) == 1, f"Wpis nieutrwalony (brak commit w schedule?): {listed}"
        assert listed[0]["ticket_id"] == str(ticket_id)

        # --- Assert: get_ticket_schedule tez widzi utrwalony wpis ---
        scheduled = await mcp_server.get_ticket_schedule(ctx, ticket_id=str(ticket_id))
        assert len(scheduled) == 1
        assert scheduled[0]["scheduled_date"] == day.isoformat()
    finally:
        # --- Cleanup: utrwalone wiersze nie sa rollbackowane przez conftest ---
        async with test_factory() as cleanup_db:
            await cleanup_db.execute(delete(WorkPlanEntry).where(WorkPlanEntry.user_id == user_id))
            await cleanup_db.execute(delete(Ticket).where(Ticket.project_id == project_id))
            await cleanup_db.execute(delete(ProjectMember).where(ProjectMember.project_id == project_id))
            await cleanup_db.execute(delete(UserApiToken).where(UserApiToken.user_id == user_id))
            await cleanup_db.execute(delete(Project).where(Project.id == project_id))
            await cleanup_db.execute(delete(User).where(User.id == user_id))
            await cleanup_db.commit()
