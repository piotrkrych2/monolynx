"""Testy integracyjne -- dashboard i API modulu heartbeat."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from monolynx.models.heartbeat import Heartbeat
from monolynx.models.project import Project
from monolynx.services.heartbeat import check_heartbeat_statuses
from tests.conftest import login_session


def _make_project(name: str, slug: str, code: str) -> Project:
    return Project(
        name=name,
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )


@pytest.mark.integration
class TestHeartbeatCreate:
    async def test_create_heartbeat_redirects_to_list(self, client, db_session):
        """POST /dashboard/{slug}/heartbeat/create z poprawnymi danymi -> redirect 303 do listy."""
        project = _make_project("HB Create", "hb-create", "HBC")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-create@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "Moj cron", "period": "5", "grace": "1"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/heartbeat/" in resp.headers["location"]

    async def test_create_heartbeat_requires_auth(self, client, db_session):
        """POST bez sesji -> redirect do /auth/login."""
        project = _make_project("HB Auth", "hb-auth-create", "HBA")
        db_session.add(project)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "cron", "period": "5", "grace": "1"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_heartbeat_empty_name_returns_form_with_error(self, client, db_session):
        """POST bez nazwy -> 200 z komunikatem o bledzie."""
        project = _make_project("HB EmptyName", "hb-emptyname", "HBE")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-emptyname@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "", "period": "5", "grace": "1"},
        )

        assert resp.status_code == 200
        assert "Nazwa jest wymagana" in resp.text

    async def test_create_heartbeat_nonexistent_project_returns_404(self, client, db_session):
        """POST na nieistniejacy projekt -> 404."""
        await login_session(client, db_session, email="hb-noproj@test.com")

        resp = await client.post(
            "/dashboard/nonexistent-hb-slug/heartbeat/create",
            data={"name": "cron", "period": "5", "grace": "1"},
        )

        assert resp.status_code == 404

    async def test_create_heartbeat_stores_period_as_seconds(self, client, db_session):
        """Formularz przyjmuje minuty, model przechowuje sekundy."""
        project = _make_project("HB Seconds", "hb-seconds", "HBS")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-seconds@test.com")

        await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "Cron sekundy", "period": "10", "grace": "2"},
            follow_redirects=False,
        )

        result = await db_session.execute(
            select(Heartbeat).where(
                Heartbeat.project_id == project.id,
                Heartbeat.name == "Cron sekundy",
            )
        )
        hb = result.scalar_one_or_none()
        assert hb is not None
        assert hb.period == 10 * 60  # 600 sekund
        assert hb.grace == 2 * 60  # 120 sekund


@pytest.mark.integration
class TestHeartbeatPingApi:
    async def test_ping_sets_status_up(self, client, db_session):
        """POST /hb/{token} -> {"status":"ok"}, status="up", last_ping_at ustawiony."""
        project = _make_project("HB Ping", "hb-ping", "HBP")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Ping Test",
            period=300,
            grace=60,
            status="pending",
        )
        db_session.add(heartbeat)
        await db_session.flush()

        token = heartbeat.token
        resp = await client.post(f"/hb/{token}")

        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

        await db_session.refresh(heartbeat)
        assert heartbeat.status == "up"
        assert heartbeat.last_ping_at is not None

    async def test_ping_get_also_sets_status_up(self, client, db_session):
        """GET /hb/{token} rowniez dziala (heartbeat akceptuje GET i POST)."""
        project = _make_project("HB PingGet", "hb-ping-get", "HBG")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Ping Get Test",
            period=300,
            grace=60,
            status="pending",
        )
        db_session.add(heartbeat)
        await db_session.flush()

        resp = await client.get(f"/hb/{heartbeat.token}")

        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

        await db_session.refresh(heartbeat)
        assert heartbeat.status == "up"

    async def test_ping_unknown_token_returns_404(self, client, db_session):
        """POST /hb/hb_AAAABBBBCCCCDDDDEEEEAA -> 404 (token spelnia regex, ale nie istnieje)."""
        resp = await client.post("/hb/hb_AAAABBBBCCCCDDDDEEEEAA")
        assert resp.status_code == 404

    async def test_ping_invalid_token_format_returns_422(self, client, db_session):
        """Token niezgodny z regex -> 422 walidacja FastAPI."""
        resp = await client.post("/hb/invalid-token")
        assert resp.status_code == 422


@pytest.mark.integration
class TestHeartbeatStatusDown:
    async def test_heartbeat_status_down_after_grace(self, client, db_session):
        """check_heartbeat_statuses zmienia status na 'down' gdy minal period+grace."""
        project = _make_project("HB Down", "hb-down", "HBD")
        db_session.add(project)
        await db_session.flush()

        period = 300  # 5 minut w sekundach
        grace = 60  # 1 minuta w sekundach

        # last_ping_at dawno temu -- przekroczono period+grace
        old_ping = datetime.now(UTC) - timedelta(seconds=period + grace + 100)

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Overdue Heartbeat",
            period=period,
            grace=grace,
            status="up",
            last_ping_at=old_ping,
        )
        db_session.add(heartbeat)
        await db_session.flush()

        await check_heartbeat_statuses(db_session)

        # Odpytaj ponownie po wywolaniu serwisu
        result = await db_session.execute(select(Heartbeat).where(Heartbeat.id == heartbeat.id))
        updated_hb = result.scalar_one()
        assert updated_hb.status == "down"

    async def test_heartbeat_status_stays_up_within_grace(self, client, db_session):
        """check_heartbeat_statuses NIE zmienia statusu gdy ostatni ping jest w oknie grace."""
        project = _make_project("HB StaysUp", "hb-stays-up", "HBU")
        db_session.add(project)
        await db_session.flush()

        period = 300
        grace = 60

        # last_ping_at przed chwila -- w oknie tolerancji
        recent_ping = datetime.now(UTC) - timedelta(seconds=10)

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Recent Heartbeat",
            period=period,
            grace=grace,
            status="up",
            last_ping_at=recent_ping,
        )
        db_session.add(heartbeat)
        await db_session.flush()

        await check_heartbeat_statuses(db_session)

        result = await db_session.execute(select(Heartbeat).where(Heartbeat.id == heartbeat.id))
        updated_hb = result.scalar_one()
        assert updated_hb.status == "up"

    async def test_heartbeat_pending_not_changed_by_checker(self, client, db_session):
        """check_heartbeat_statuses ignoruje heartbeaty ze statusem 'pending' (brak last_ping_at)."""
        project = _make_project("HB Pending", "hb-pending", "HBQ")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Pending Heartbeat",
            period=60,
            grace=60,
            status="pending",
            last_ping_at=None,
        )
        db_session.add(heartbeat)
        await db_session.flush()

        await check_heartbeat_statuses(db_session)

        result = await db_session.execute(select(Heartbeat).where(Heartbeat.id == heartbeat.id))
        updated_hb = result.scalar_one()
        assert updated_hb.status == "pending"


@pytest.mark.integration
class TestHeartbeatList:
    async def test_heartbeat_list_requires_auth(self, client, db_session):
        """GET /dashboard/{slug}/heartbeat/ bez sesji -> redirect 303 do /auth/login."""
        project = _make_project("HB ListAuth", "hb-list-auth", "HBL")
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/heartbeat/",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_heartbeat_list_shows_heartbeats(self, client, db_session):
        """GET /dashboard/{slug}/heartbeat/ -> 200 z lista heartbeatow."""
        project = _make_project("HB ListShow", "hb-list-show", "HBX")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Visible Heartbeat",
            period=300,
            grace=60,
        )
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-list-show@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/heartbeat/")

        assert resp.status_code == 200
        assert "Visible Heartbeat" in resp.text

    async def test_heartbeat_list_nonexistent_project_returns_404(self, client, db_session):
        """GET dla nieistniejacego projektu -> 404."""
        await login_session(client, db_session, email="hb-listnoproj@test.com")

        resp = await client.get("/dashboard/nonexistent-hb-proj/heartbeat/")

        assert resp.status_code == 404


@pytest.mark.integration
class TestHeartbeatDelete:
    async def test_delete_heartbeat_redirects_and_removes(self, client, db_session):
        """POST /{slug}/heartbeat/{id}/delete -> redirect 303, heartbeat usuniety z DB."""
        project = _make_project("HB Delete", "hb-delete", "HBZ")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Delete Me",
            period=300,
            grace=60,
        )
        db_session.add(heartbeat)
        await db_session.flush()
        heartbeat_id = heartbeat.id

        await login_session(client, db_session, email="hb-delete@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat_id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/heartbeat/" in resp.headers["location"]

        # Weryfikacja usuniecia
        result = await db_session.execute(select(Heartbeat).where(Heartbeat.id == heartbeat_id))
        assert result.scalar_one_or_none() is None

    async def test_delete_heartbeat_requires_auth(self, client, db_session):
        """DELETE bez sesji -> redirect do /auth/login."""
        project = _make_project("HB DelAuth", "hb-del-auth", "HBT")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Auth Delete",
            period=300,
            grace=60,
        )
        db_session.add(heartbeat)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_nonexistent_heartbeat_returns_404(self, client, db_session):
        """POST na nieistniejacy heartbeat -> 404."""
        project = _make_project("HB DelNF", "hb-del-nf", "HBN")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-del-nf@test.com")

        fake_id = uuid.uuid4()
        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{fake_id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 404

    async def test_delete_nonexistent_project_returns_404(self, client, db_session):
        """POST delete na nieistniejacy projekt -> 404."""
        await login_session(client, db_session, email="hb-del-noproj@test.com")

        resp = await client.post(
            f"/dashboard/no-such-proj/heartbeat/{uuid.uuid4()}/delete",
            follow_redirects=False,
        )
        assert resp.status_code == 404


@pytest.mark.integration
class TestHeartbeatDetail:
    async def test_detail_shows_heartbeat_info(self, client, db_session):
        """GET /dashboard/{slug}/heartbeat/{id} -> 200 ze szczegolami."""
        project = _make_project("HB Detail", "hb-detail", "HBD2")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(
            project_id=project.id,
            name="Detail Heartbeat",
            period=600,
            grace=120,
        )
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-detail@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}")
        assert resp.status_code == 200
        assert "Detail Heartbeat" in resp.text

    async def test_detail_requires_auth(self, client, db_session):
        """GET bez sesji -> redirect do /auth/login."""
        project = _make_project("HB DetAuth", "hb-det-auth", "HDA")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Auth Detail", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_detail_nonexistent_project_returns_404(self, client, db_session):
        """GET dla nieistniejacego projektu -> 404."""
        await login_session(client, db_session, email="hb-det-noproj@test.com")

        resp = await client.get(f"/dashboard/no-such-proj/heartbeat/{uuid.uuid4()}")
        assert resp.status_code == 404

    async def test_detail_nonexistent_heartbeat_returns_404(self, client, db_session):
        """GET dla nieistniejacego heartbeatu -> 404."""
        project = _make_project("HB DetNF", "hb-det-nf", "HDN")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-det-nf@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/heartbeat/{uuid.uuid4()}")
        assert resp.status_code == 404


@pytest.mark.integration
class TestHeartbeatCreateForm:
    async def test_create_form_renders(self, client, db_session):
        """GET /dashboard/{slug}/heartbeat/create -> 200 z formularzem."""
        project = _make_project("HB CrForm", "hb-cr-form", "HCF")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-cr-form@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/heartbeat/create")
        assert resp.status_code == 200

    async def test_create_form_requires_auth(self, client, db_session):
        """GET bez sesji -> redirect do /auth/login."""
        project = _make_project("HB CrFAuth", "hb-cr-fauth", "HCA")
        db_session.add(project)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/heartbeat/create",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_create_form_nonexistent_project_returns_404(self, client, db_session):
        """GET dla nieistniejacego projektu -> 404."""
        await login_session(client, db_session, email="hb-cr-fnoproj@test.com")

        resp = await client.get("/dashboard/no-such-proj/heartbeat/create")
        assert resp.status_code == 404


@pytest.mark.integration
class TestHeartbeatCreateValidation:
    async def test_name_too_long(self, client, db_session):
        """POST z nazwa > 255 znakow -> blad walidacji."""
        project = _make_project("HB LongName", "hb-longname", "HLN")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="hb-longname@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "x" * 256, "period": "5", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "255" in resp.text

    async def test_invalid_period_not_number(self, client, db_session):
        """POST z period niebedacym liczba -> blad walidacji."""
        project = _make_project("HB BadPer", "hb-badper", "HBP2")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="hb-badper@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "Test", "period": "abc", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "liczba" in resp.text

    async def test_invalid_grace_not_number(self, client, db_session):
        """POST z grace niebedacym liczba -> blad walidacji."""
        project = _make_project("HB BadGr", "hb-badgr", "HBG2")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="hb-badgr@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "Test", "period": "5", "grace": "abc"},
        )
        assert resp.status_code == 200
        assert "liczba" in resp.text

    async def test_period_zero_rejected(self, client, db_session):
        """POST z period=0 -> blad walidacji."""
        project = _make_project("HB ZeroPer", "hb-zeroper", "HZP")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="hb-zeroper@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "Test", "period": "0", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "wiekszy" in resp.text

    async def test_negative_grace_rejected(self, client, db_session):
        """POST z grace < 0 -> blad walidacji."""
        project = _make_project("HB NegGr", "hb-neggr", "HNG")
        db_session.add(project)
        await db_session.flush()
        await login_session(client, db_session, email="hb-neggr@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "Test", "period": "5", "grace": "-1"},
        )
        assert resp.status_code == 200
        assert "nieujemna" in resp.text

    async def test_duplicate_name_returns_error(self, client, db_session):
        """POST z duplikatem nazwy -> IntegrityError -> blad formularza."""
        project = _make_project("HB Dup", "hb-dup", "HDP")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Unique Name", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-dup@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "Unique Name", "period": "5", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "juz istnieje" in resp.text

    async def test_limit_reached_returns_error(self, client, db_session):
        """POST gdy osiagnieto limit heartbeatow -> blad."""
        project = _make_project("HB Limit", "hb-limit", "HLM")
        db_session.add(project)
        await db_session.flush()

        for i in range(50):
            db_session.add(Heartbeat(project_id=project.id, name=f"HB-{i}", period=300, grace=60))
        await db_session.flush()

        await login_session(client, db_session, email="hb-limit@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/create",
            data={"name": "One Too Many", "period": "5", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "limit" in resp.text.lower()


@pytest.mark.integration
class TestHeartbeatEditForm:
    async def test_edit_form_renders(self, client, db_session):
        """GET /dashboard/{slug}/heartbeat/{id}/edit -> 200."""
        project = _make_project("HB EdForm", "hb-edform", "HEF")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Edit Me", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-edform@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/edit")
        assert resp.status_code == 200
        assert "Edit Me" in resp.text

    async def test_edit_form_requires_auth(self, client, db_session):
        """GET bez sesji -> redirect do /auth/login."""
        project = _make_project("HB EdAuth", "hb-edauth", "HEA")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Auth Edit", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        resp = await client.get(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/edit",
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_edit_form_nonexistent_project_returns_404(self, client, db_session):
        """GET dla nieistniejacego projektu -> 404."""
        await login_session(client, db_session, email="hb-ed-noproj@test.com")

        resp = await client.get(f"/dashboard/no-such-proj/heartbeat/{uuid.uuid4()}/edit")
        assert resp.status_code == 404

    async def test_edit_form_nonexistent_heartbeat_returns_404(self, client, db_session):
        """GET dla nieistniejacego heartbeatu -> 404."""
        project = _make_project("HB EdNF", "hb-ednf", "HEN")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-ednf@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/heartbeat/{uuid.uuid4()}/edit")
        assert resp.status_code == 404


@pytest.mark.integration
class TestHeartbeatEdit:
    async def test_edit_updates_heartbeat(self, client, db_session):
        """POST /dashboard/{slug}/heartbeat/{id}/edit -> redirect 303."""
        project = _make_project("HB EditOk", "hb-editok", "HEO")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Before Edit", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()
        hb_id = heartbeat.id

        await login_session(client, db_session, email="hb-editok@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{hb_id}/edit",
            data={"name": "After Edit", "period": "10", "grace": "2"},
            follow_redirects=False,
        )
        assert resp.status_code == 303

        result = await db_session.execute(select(Heartbeat).where(Heartbeat.id == hb_id))
        hb = result.scalar_one()
        assert hb.name == "After Edit"
        assert hb.period == 600
        assert hb.grace == 120

    async def test_edit_requires_auth(self, client, db_session):
        """POST bez sesji -> redirect do /auth/login."""
        project = _make_project("HB EdPAuth", "hb-edpauth", "HPA")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="AuthEdit", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/edit",
            data={"name": "New Name", "period": "5", "grace": "1"},
            follow_redirects=False,
        )
        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_edit_nonexistent_project_returns_404(self, client, db_session):
        """POST na nieistniejacy projekt -> 404."""
        await login_session(client, db_session, email="hb-edp-noproj@test.com")

        resp = await client.post(
            f"/dashboard/no-such-proj/heartbeat/{uuid.uuid4()}/edit",
            data={"name": "X", "period": "5", "grace": "1"},
        )
        assert resp.status_code == 404

    async def test_edit_nonexistent_heartbeat_returns_404(self, client, db_session):
        """POST na nieistniejacy heartbeat -> 404."""
        project = _make_project("HB EdPNF", "hb-edpnf", "HPN")
        db_session.add(project)
        await db_session.flush()

        await login_session(client, db_session, email="hb-edpnf@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{uuid.uuid4()}/edit",
            data={"name": "X", "period": "5", "grace": "1"},
        )
        assert resp.status_code == 404

    async def test_edit_empty_name_returns_error(self, client, db_session):
        """POST z pusta nazwa -> blad walidacji."""
        project = _make_project("HB EdEmpty", "hb-edempty", "HEE")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Valid", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-edempty@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/edit",
            data={"name": "", "period": "5", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "wymagana" in resp.text

    async def test_edit_invalid_period_returns_error(self, client, db_session):
        """POST z period niebedacym liczba -> blad walidacji."""
        project = _make_project("HB EdBadPer", "hb-edbadper", "HEB")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Valid2", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-edbadper@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/edit",
            data={"name": "Valid2", "period": "abc", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "liczba" in resp.text

    async def test_edit_invalid_grace_returns_error(self, client, db_session):
        """POST z grace niebedacym liczba -> blad walidacji."""
        project = _make_project("HB EdBadGr", "hb-edbadgr", "HEG")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Valid3", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-edbadgr@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/edit",
            data={"name": "Valid3", "period": "5", "grace": "abc"},
        )
        assert resp.status_code == 200
        assert "liczba" in resp.text

    async def test_edit_name_too_long_returns_error(self, client, db_session):
        """POST z nazwa > 255 znakow -> blad walidacji."""
        project = _make_project("HB EdLong", "hb-edlong", "HEL")
        db_session.add(project)
        await db_session.flush()

        heartbeat = Heartbeat(project_id=project.id, name="Short", period=300, grace=60)
        db_session.add(heartbeat)
        await db_session.flush()

        await login_session(client, db_session, email="hb-edlong@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/heartbeat/{heartbeat.id}/edit",
            data={"name": "x" * 256, "period": "5", "grace": "1"},
        )
        assert resp.status_code == 200
        assert "255" in resp.text
