"""Test E2E -- modul Plan pracy (cross-project).

Scenariusz: jeden uzytkownik, dwa projekty, schedule/list/filtr/patch/delete.
MON-71.
"""

from __future__ import annotations

import secrets
import uuid

import pytest

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from tests.conftest import login_session

# Stala data uzywana we wszystkich krokach testu
_DATE_A = "2026-05-20"
_DATE_B = "2026-05-21"


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
