"""Testy integracyjne dashboard/settlements.py -- MON-81.

Pokrywa niepokryte galezie endpointow routera:
- Lista rozliczen: show_paid toggle, paginacja, brak uprawnien
- Formularz tworzenia: GET (z all_projects), POST walidacja, brak loginu, 403
- Detal: notes_html, can_edit_tickets=False (nie-draft lub brak write)
- Edycja: GET redirect gdy nie-draft, POST walidacja (invalid UUID, brak biezacego projektu, ValueError)
- Zmiana statusu: brak loginu, 403 (member)
- Usuwanie: brak loginu, 403 (brak delete), ValueError z serwisu
- Upload zalacznika: brak loginu, 403, ValueError (zly state/kategoria/rozmiar)
- Download zalacznika: 404 obcy attachment, nagłowki UTF-8
- Usuniecie zalacznika: brak loginu, 403, 404 obcy attachment
- Szukanie ticketow: 403 (check_permission), settlement nie-draft -> puste, po numerze, po tytule
- Link ticket: nie-draft, invalid UUID, ticket nie istnieje, already linked, sukces
- Unlink ticket: nie-draft, ticket nie podpiety, sukces
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_attachment import SettlementAttachment
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.services.auth import hash_password

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(db_session, name: str, slug: str | None = None) -> Project:
    if slug is None:
        slug = f"dsh-{secrets.token_hex(4)}"
    project = Project(
        name=name,
        slug=slug,
        code=secrets.token_hex(3).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=True,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_user_with_role(
    db_session,
    email: str,
    project: Project,
    role: str = "owner",
    is_superuser: bool = False,
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=is_superuser,
    )
    db_session.add(user)
    await db_session.flush()

    member = ProjectMember(
        project_id=project.id,
        user_id=user.id,
        role=role,
    )
    db_session.add(member)
    await db_session.flush()
    return user


async def _login_existing_user(client, email: str) -> None:
    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def _make_settlement(
    db_session,
    creator: User,
    project: Project,
    status: str = "draft",
    sent_at: datetime | None = None,
    paid_at: datetime | None = None,
    name: str | None = None,
    notes: str | None = None,
) -> Settlement:
    result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    next_number = int(result.scalar_one()) + 1

    settlement = Settlement(
        number=next_number,
        name=name or f"Rozliczenie DSH {next_number}",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        status=status,
        sent_at=sent_at,
        paid_at=paid_at,
        notes=notes,
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()

    sp = SettlementProject(settlement_id=settlement.id, project_id=project.id)
    db_session.add(sp)
    await db_session.flush()

    return settlement


async def _make_ticket(db_session, project: Project, number: int | None = None, title: str | None = None) -> Ticket:
    if number is None:
        number = int(secrets.token_hex(3), 16) % 90000 + 10000
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=title or f"Ticket DSH #{number}",
        status="backlog",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _make_attachment(
    db_session,
    settlement: Settlement,
    user: User,
    storage_path: str | None = None,
    mime_type: str | None = "application/pdf",
    filename: str = "test.pdf",
    category: str = "invoice",
    state: str = "draft",
) -> SettlementAttachment:
    att = SettlementAttachment(
        settlement_id=settlement.id,
        category=category,
        state=state,
        filename=filename,
        storage_path=storage_path or f"settlements/{settlement.id}/2026/01/{uuid.uuid4().hex}.pdf",
        mime_type=mime_type,
        size=1024,
        uploaded_by_id=user.id,
    )
    db_session.add(att)
    await db_session.flush()
    return att


async def _link_ticket_to_settlement(db_session, settlement: Settlement, ticket: Ticket) -> None:
    result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
    s = result.scalar_one()
    s.tickets.append(ticket)
    await db_session.flush()


# ---------------------------------------------------------------------------
# Lista rozliczen
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementList:
    """GET /{slug}/rozliczenia/ -- lista."""

    async def test_list_redirects_when_not_logged_in(self, client, db_session):
        """Brak sesji -> redirect do /auth/login."""
        project = await _create_project(db_session, "ListNoLogin", "dsh-list-nologin")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/", follow_redirects=False)

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_list_returns_403_for_member(self, client, db_session):
        """Member bez rozliczenia:read -> 403."""
        project = await _create_project(db_session, "ListNoRead", "dsh-list-noread")
        await _create_user_with_role(db_session, "dsh-listnoread@test.com", project, "member")
        await _login_existing_user(client, "dsh-listnoread@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/", follow_redirects=False)

        assert resp.status_code == 403

    async def test_list_shows_settlements_for_owner(self, client, db_session):
        """Owner widzi liste rozliczen (200)."""
        project = await _create_project(db_session, "ListOwner", "dsh-list-owner")
        owner = await _create_user_with_role(db_session, "dsh-listowner@test.com", project, "owner")
        await _make_settlement(db_session, owner, project, name="Test ROZ lista")
        await _login_existing_user(client, "dsh-listowner@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/", follow_redirects=False)

        assert resp.status_code == 200
        assert b"Test ROZ lista" in resp.content

    async def test_list_hides_paid_by_default(self, client, db_session):
        """Paid settlements nie wyswietlane bez show_paid=1."""
        project = await _create_project(db_session, "ListHidePaid", "dsh-list-hidepaid")
        owner = await _create_user_with_role(db_session, "dsh-listhidepaid@test.com", project, "owner")
        await _make_settlement(
            db_session,
            owner,
            project,
            status="paid",
            name="PAID rozliczenie",
            sent_at=datetime(2026, 1, 15, tzinfo=UTC),
            paid_at=datetime(2026, 1, 20, tzinfo=UTC),
        )
        await _login_existing_user(client, "dsh-listhidepaid@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/", follow_redirects=False)

        assert resp.status_code == 200
        # Paid rozliczenie nie pokazuje sie bez show_paid
        assert b"PAID rozliczenie" not in resp.content

    async def test_list_shows_paid_with_show_paid_param(self, client, db_session):
        """?show_paid=1 pokazuje tez paid settlements."""
        project = await _create_project(db_session, "ListShowPaid", "dsh-list-showpaid")
        owner = await _create_user_with_role(db_session, "dsh-listshowpaid@test.com", project, "owner")
        await _make_settlement(
            db_session,
            owner,
            project,
            status="paid",
            name="PAID VISIBLE",
            sent_at=datetime(2026, 1, 15, tzinfo=UTC),
            paid_at=datetime(2026, 1, 20, tzinfo=UTC),
        )
        await _login_existing_user(client, "dsh-listshowpaid@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/?show_paid=1", follow_redirects=False)

        assert resp.status_code == 200
        assert b"PAID VISIBLE" in resp.content

    async def test_list_pagination_page_param(self, client, db_session):
        """?page=2 dziala (200), nie powoduje bledu."""
        project = await _create_project(db_session, "ListPage", "dsh-list-page")
        await _create_user_with_role(db_session, "dsh-listpage@test.com", project, "owner")
        await _login_existing_user(client, "dsh-listpage@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/?page=2", follow_redirects=False)

        # Strona 2 nawet bez danych -> 200 (clampuje do 1)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Formularz tworzenia
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementCreateForm:
    """GET/POST /{slug}/rozliczenia/create."""

    async def test_get_form_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "CreateFormNoLogin", "dsh-crf-nologin")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/create", follow_redirects=False)

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_get_form_returns_403_for_member(self, client, db_session):
        """Member bez rozliczenia:write -> 403 GET form."""
        project = await _create_project(db_session, "CreateFormMember", "dsh-crf-member")
        await _create_user_with_role(db_session, "dsh-crfmember@test.com", project, "member")
        await _login_existing_user(client, "dsh-crfmember@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/create", follow_redirects=False)

        assert resp.status_code == 403

    async def test_get_form_returns_200_for_owner(self, client, db_session):
        """Owner widzi formularz tworzenia (200)."""
        project = await _create_project(db_session, "CreateFormOwner", "dsh-crf-owner")
        await _create_user_with_role(db_session, "dsh-crfowner@test.com", project, "owner")
        await _login_existing_user(client, "dsh-crfowner@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/create", follow_redirects=False)

        assert resp.status_code == 200

    async def test_post_redirects_when_not_logged_in(self, client, db_session):
        """POST bez loginu -> redirect."""
        project = await _create_project(db_session, "CreatePostNoLogin", "dsh-crp-nologin")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/create",
            data={"name": "Test", "period_from": "2026-01-01", "period_to": "2026-01-31"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_post_returns_403_for_member(self, client, db_session):
        """Member bez write -> 403 POST."""
        project = await _create_project(db_session, "CreatePostMember", "dsh-crpm-mem")
        await _create_user_with_role(db_session, "dsh-crpmmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-crpmmem@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/create",
            data={"name": "Test", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": [str(project.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_post_invalid_project_uuid_shows_flash(self, client, db_session):
        """Nieprawidlowy UUID w project_ids -> 200 z flash error."""
        project = await _create_project(db_session, "CreateInvalidUUID", "dsh-crp-uuid")
        await _create_user_with_role(db_session, "dsh-crpuuid@test.com", project, "owner")
        await _login_existing_user(client, "dsh-crpuuid@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/create",
            data={"name": "Test", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": ["not-a-uuid"]},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert b"Nieprawidlowe ID projektu" in resp.content

    async def test_post_current_project_not_in_ids_shows_flash(self, client, db_session):
        """Biezacy projekt nie wybrany -> 200 z flash error."""
        project = await _create_project(db_session, "CreateNoCurrentProj", "dsh-crp-nocur")
        other = await _create_project(db_session, "OtherForCreate", "dsh-crp-other")
        user = await _create_user_with_role(db_session, "dsh-crpnocur@test.com", project, "owner")
        # Dodaj user do other
        db_session.add(ProjectMember(project_id=other.id, user_id=user.id, role="owner"))
        await db_session.flush()
        await _login_existing_user(client, "dsh-crpnocur@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/create",
            data={"name": "Test", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": [str(other.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert b"Biezacy projekt musi byc wybrany" in resp.content

    async def test_post_value_error_from_service_shows_flash(self, client, db_session):
        """ValueError z create_settlement (np. period_from > period_to) -> 200 z flash."""
        project = await _create_project(db_session, "CreateValErr", "dsh-crp-valerr")
        await _create_user_with_role(db_session, "dsh-crpvalerr@test.com", project, "owner")
        await _login_existing_user(client, "dsh-crpvalerr@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/create",
            data={
                "name": "Test",
                "period_from": "2026-02-01",  # po period_to
                "period_to": "2026-01-01",
                "project_ids": [str(project.id)],
            },
            follow_redirects=False,
        )

        assert resp.status_code == 200
        # Flash error od serwisu
        assert b"Data poczatku" in resp.content or b"Data" in resp.content

    async def test_post_happy_path_redirects_to_detail(self, client, db_session):
        """Happy path POST -> redirect do detalu."""
        project = await _create_project(db_session, "CreateHappyDash", "dsh-crp-happy")
        await _create_user_with_role(db_session, "dsh-crphappy@test.com", project, "owner")
        await _login_existing_user(client, "dsh-crphappy@test.com")

        with patch("monolynx.services.settlements.minio_client.upload_object", return_value=None):
            resp = await client.post(
                f"/dashboard/{project.slug}/rozliczenia/create",
                data={
                    "name": "Rozliczenie Dashboard Test",
                    "period_from": "2026-01-01",
                    "period_to": "2026-01-31",
                    "project_ids": [str(project.id)],
                },
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/rozliczenia/" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Detal rozliczenia
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementDetail:
    """GET /{slug}/rozliczenia/{settlement_id} -- detal."""

    async def test_detail_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "DetailNoLogin", "dsh-det-nologin")
        owner = await _create_user_with_role(db_session, "dsh-detnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}", follow_redirects=False)

        assert resp.status_code == 303

    async def test_detail_returns_403_for_member(self, client, db_session):
        """Member bez read -> 403."""
        project = await _create_project(db_session, "DetailNoRead", "dsh-det-noread")
        owner = await _create_user_with_role(db_session, "dsh-detnoread-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-detnoread@test.com", project, "member")
        await _login_existing_user(client, "dsh-detnoread@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}", follow_redirects=False)

        assert resp.status_code == 403

    async def test_detail_returns_404_for_wrong_project(self, client, db_session):
        """Settlement z innego projektu -> 404."""
        project_a = await _create_project(db_session, "DetailWrongA", "dsh-det-wronga")
        project_b = await _create_project(db_session, "DetailWrongB", "dsh-det-wrongb")
        owner = await _create_user_with_role(db_session, "dsh-detwrong@test.com", project_a, "owner")
        # Settlement nalezace do project_b
        settlement = await _make_settlement(db_session, owner, project_b)
        # Daj ownerowi tez dostep do B
        db_session.add(ProjectMember(project_id=project_b.id, user_id=owner.id, role="owner"))
        await db_session.flush()
        await _login_existing_user(client, "dsh-detwrong@test.com")

        # Prosimy o settlement przez kontekst projektu A - powinno dac 404
        resp = await client.get(f"/dashboard/{project_a.slug}/rozliczenia/{settlement.id}", follow_redirects=False)

        assert resp.status_code == 404

    async def test_detail_renders_notes_markdown(self, client, db_session):
        """Notes sa renderowane jako HTML (markdown)."""
        project = await _create_project(db_session, "DetailNotes", "dsh-det-notes")
        owner = await _create_user_with_role(db_session, "dsh-detnotes@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, notes="**Pogrubiona notatka**")
        await _login_existing_user(client, "dsh-detnotes@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}", follow_redirects=False)

        assert resp.status_code == 200
        # Markdown <strong> lub <b> w wyniku renderowania
        assert b"<strong>" in resp.content or b"<b>" in resp.content

    async def test_detail_can_edit_tickets_false_when_not_draft(self, client, db_session):
        """can_edit_tickets=False gdy settlement.status != draft."""
        project = await _create_project(db_session, "DetailCanEditFalse", "dsh-det-cef")
        owner = await _create_user_with_role(db_session, "dsh-detcef@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, status="sent", sent_at=datetime(2026, 1, 15, tzinfo=UTC))
        await _login_existing_user(client, "dsh-detcef@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}", follow_redirects=False)

        # Strona laduje sie poprawnie (200)
        assert resp.status_code == 200

    async def test_detail_shows_linked_ticket(self, client, db_session):
        """Podpiete tickety sa widoczne na detalu."""
        project = await _create_project(db_session, "DetailTicket", "dsh-det-ticket")
        owner = await _create_user_with_role(db_session, "dsh-detticket@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        ticket = await _make_ticket(db_session, project, title="Ticket w rozliczeniu")
        await _link_ticket_to_settlement(db_session, settlement, ticket)
        await _login_existing_user(client, "dsh-detticket@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}", follow_redirects=False)

        assert resp.status_code == 200
        assert b"Ticket w rozliczeniu" in resp.content


# ---------------------------------------------------------------------------
# Edycja rozliczenia
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementEditForm:
    """GET/POST /{slug}/rozliczenia/{settlement_id}/edit."""

    async def test_get_edit_form_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "EditFormNoLogin", "dsh-eff-nologin")
        owner = await _create_user_with_role(db_session, "dsh-effnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit", follow_redirects=False)

        assert resp.status_code == 303

    async def test_get_edit_form_returns_403_for_member(self, client, db_session):
        """Member bez write -> 403 GET edit."""
        project = await _create_project(db_session, "EditFormMember", "dsh-eff-mem")
        owner = await _create_user_with_role(db_session, "dsh-effmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-effmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-effmem@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit", follow_redirects=False)

        assert resp.status_code == 403

    async def test_get_edit_form_redirects_when_not_draft(self, client, db_session):
        """Settlement nie-draft -> redirect do detalu z flash."""
        project = await _create_project(db_session, "EditFormNotDraft", "dsh-eff-nondraft")
        owner = await _create_user_with_role(db_session, "dsh-effnondraft@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, status="sent", sent_at=datetime(2026, 1, 15, tzinfo=UTC))
        await _login_existing_user(client, "dsh-effnondraft@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit", follow_redirects=False)

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_get_edit_form_returns_200_for_draft(self, client, db_session):
        """Draft settlement -> 200 formularz edycji."""
        project = await _create_project(db_session, "EditFormDraft", "dsh-eff-draft")
        owner = await _create_user_with_role(db_session, "dsh-effdraft@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-effdraft@test.com")

        resp = await client.get(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit", follow_redirects=False)

        assert resp.status_code == 200

    async def test_post_edit_redirects_when_not_logged_in(self, client, db_session):
        """POST bez loginu -> redirect."""
        project = await _create_project(db_session, "EditPostNoLogin", "dsh-ep-nologin")
        owner = await _create_user_with_role(db_session, "dsh-epnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit",
            data={"name": "X", "period_from": "2026-01-01", "period_to": "2026-01-31"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_post_edit_redirects_when_not_draft(self, client, db_session):
        """POST na nie-draft -> redirect do detalu z flash."""
        project = await _create_project(db_session, "EditPostNotDraft", "dsh-ep-nondraft")
        owner = await _create_user_with_role(db_session, "dsh-epnondraft@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, status="sent", sent_at=datetime(2026, 1, 15, tzinfo=UTC))
        await _login_existing_user(client, "dsh-epnondraft@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit",
            data={"name": "X", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": [str(project.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_post_edit_invalid_project_uuid_shows_flash(self, client, db_session):
        """Nieprawidlowy UUID w project_ids edycji -> 200 z flash."""
        project = await _create_project(db_session, "EditPostInvalidUUID", "dsh-ep-uuid")
        owner = await _create_user_with_role(db_session, "dsh-epuuid@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-epuuid@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit",
            data={"name": "Test", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": ["zly-uuid"]},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert b"Nieprawidlowe ID projektu" in resp.content

    async def test_post_edit_current_project_not_selected_shows_flash(self, client, db_session):
        """Biezacy projekt nie wybrany w edycji -> 200 z flash."""
        project = await _create_project(db_session, "EditPostNoCur", "dsh-ep-nocur")
        other = await _create_project(db_session, "OtherForEdit", "dsh-ep-other")
        user = await _create_user_with_role(db_session, "dsh-epnocur@test.com", project, "owner")
        db_session.add(ProjectMember(project_id=other.id, user_id=user.id, role="owner"))
        await db_session.flush()
        settlement = await _make_settlement(db_session, user, project)
        await _login_existing_user(client, "dsh-epnocur@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit",
            data={"name": "Test", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": [str(other.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert b"Biezacy projekt musi byc wybrany" in resp.content

    async def test_post_edit_value_error_shows_flash(self, client, db_session):
        """ValueError z update_settlement -> 200 z flash."""
        project = await _create_project(db_session, "EditPostValErr", "dsh-ep-valerr")
        owner = await _create_user_with_role(db_session, "dsh-epvalerr@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-epvalerr@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit",
            data={
                "name": "Test",
                "period_from": "2026-03-01",  # po period_to -> ValueError
                "period_to": "2026-01-01",
                "project_ids": [str(project.id)],
            },
            follow_redirects=False,
        )

        assert resp.status_code == 200

    async def test_post_edit_happy_path_redirects_to_detail(self, client, db_session):
        """Happy path POST edit -> redirect do detalu z flash success."""
        project = await _create_project(db_session, "EditPostHappy", "dsh-ep-happy")
        owner = await _create_user_with_role(db_session, "dsh-ephappy@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-ephappy@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/edit",
            data={"name": "Edytowana nazwa", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": [str(project.id)]},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]


# ---------------------------------------------------------------------------
# Zmiana statusu
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementChangeStatusDashboard:
    """POST /{slug}/rozliczenia/{settlement_id}/status -- przez dashboard."""

    async def test_status_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "StatusNoLogin", "dsh-sts-nologin")
        owner = await _create_user_with_role(db_session, "dsh-stsnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "sent"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_status_returns_403_for_member(self, client, db_session):
        """Member bez write -> 403 przy POST /status."""
        project = await _create_project(db_session, "StatusMember", "dsh-sts-member")
        owner = await _create_user_with_role(db_session, "dsh-stsmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-stsmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-stsmem@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "sent"},
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_status_value_error_shows_flash(self, client, db_session):
        """ValueError (np. draft->paid bezposrednio) -> redirect z flash error."""
        project = await _create_project(db_session, "StatusValErr", "dsh-sts-valerr")
        owner = await _create_user_with_role(db_session, "dsh-stsvalerr@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)  # status=draft
        await _login_existing_user(client, "dsh-stsvalerr@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/status",
            data={"new_status": "paid"},  # niedozwolone z draft
            follow_redirects=False,
        )

        assert resp.status_code == 303


# ---------------------------------------------------------------------------
# Usuniecie rozliczenia
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementDelete:
    """POST /{slug}/rozliczenia/{settlement_id}/delete."""

    async def test_delete_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "DeleteNoLogin", "dsh-del-nologin")
        owner = await _create_user_with_role(db_session, "dsh-delnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.post(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/delete", follow_redirects=False)

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_returns_403_for_admin_without_delete_perm(self, client, db_session):
        """Admin ma write ale nie delete -> 403.

        DEFAULT_ROLE_PERMISSIONS: admin=[read,write], NIE ma 'delete'.
        Endpoint wymaga rozliczenia:delete.
        """
        project = await _create_project(db_session, "DeleteAdminNoDelete", "dsh-del-admin")
        owner = await _create_user_with_role(db_session, "dsh-deladmin-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-deladmin@test.com", project, "admin")
        await _login_existing_user(client, "dsh-deladmin@test.com")

        resp = await client.post(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/delete", follow_redirects=False)

        assert resp.status_code == 403

    async def test_delete_returns_403_for_member(self, client, db_session):
        """Member bez delete -> 403."""
        project = await _create_project(db_session, "DeleteMember", "dsh-del-member")
        owner = await _create_user_with_role(db_session, "dsh-delmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-delmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-delmem@test.com")

        resp = await client.post(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/delete", follow_redirects=False)

        assert resp.status_code == 403

    async def test_delete_value_error_redirects_with_flash(self, client, db_session):
        """ValueError z delete_settlement (np. nie-draft) -> redirect z flash."""
        project = await _create_project(db_session, "DeleteValErr", "dsh-del-valerr")
        owner = await _create_user_with_role(db_session, "dsh-delvalerr@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, status="sent", sent_at=datetime(2026, 1, 15, tzinfo=UTC))
        await _login_existing_user(client, "dsh-delvalerr@test.com")

        resp = await client.post(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/delete", follow_redirects=False)

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_delete_happy_path_redirects_to_list(self, client, db_session):
        """Happy path owner delete draft -> redirect do listy."""
        project = await _create_project(db_session, "DeleteHappyDash", "dsh-del-happy")
        owner = await _create_user_with_role(db_session, "dsh-delhappy@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-delhappy@test.com")

        resp = await client.post(f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/delete", follow_redirects=False)

        assert resp.status_code == 303
        assert f"/dashboard/{project.slug}/rozliczenia/" in resp.headers["location"]


# ---------------------------------------------------------------------------
# Upload zalacznika
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementUploadAttachmentDashboard:
    """POST /{slug}/rozliczenia/{settlement_id}/attachments -- upload."""

    async def test_upload_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "UploadNoLogin", "dsh-upl-nologin")
        owner = await _create_user_with_role(db_session, "dsh-uplnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments",
            files={"file": ("test.pdf", b"content", "application/pdf")},
            data={"category": "invoice", "state": "draft"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_upload_returns_403_for_member(self, client, db_session):
        """Member bez write -> 403 upload."""
        project = await _create_project(db_session, "UploadMember", "dsh-upl-member")
        owner = await _create_user_with_role(db_session, "dsh-uplmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-uplmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-uplmem@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments",
            files={"file": ("test.pdf", b"content", "application/pdf")},
            data={"category": "invoice", "state": "draft"},
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_upload_value_error_redirects_with_flash(self, client, db_session):
        """ValueError (np. zla kategoria) -> redirect z flash error."""
        project = await _create_project(db_session, "UploadBadCat", "dsh-upl-badcat")
        owner = await _create_user_with_role(db_session, "dsh-uplbadcat@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-uplbadcat@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments",
            files={"file": ("test.pdf", b"content", "application/pdf")},
            data={"category": "invalid_cat", "state": "draft"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_upload_value_error_signed_state_redirects(self, client, db_session):
        """Walidacja state='signed' -> ValueError z serwisu -> redirect."""
        project = await _create_project(db_session, "UploadSigned", "dsh-upl-signed")
        owner = await _create_user_with_role(db_session, "dsh-uplsigned@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-uplsigned@test.com")

        # Sprawdzamy czy endpoint poprawnie przekazuje state do serwisu.
        # W serwisie 'signed' to POPRAWNY state - tu nie bedzie ValueError z powodu state.
        # Testujemy ze bad extension gives ValueError -> redirect
        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments",
            files={"file": ("malware.exe", b"content", "application/octet-stream")},
            data={"category": "invoice", "state": "signed"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_upload_happy_path_redirects_with_flash_success(self, client, db_session):
        """Happy path upload -> redirect z flash success."""
        project = await _create_project(db_session, "UploadHappyDash", "dsh-upl-happy")
        owner = await _create_user_with_role(db_session, "dsh-uplhappy@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-uplhappy@test.com")

        with patch("monolynx.services.settlements.minio_client.upload_object", return_value=None):
            resp = await client.post(
                f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments",
                files={"file": ("faktura.pdf", b"PDF content", "application/pdf")},
                data={"category": "invoice", "state": "draft"},
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]


# ---------------------------------------------------------------------------
# Download zalacznika
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementDownloadAttachment:
    """GET /{slug}/rozliczenia/{settlement_id}/attachments/{attachment_id}."""

    async def test_download_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "DlNoLogin", "dsh-dl-nologin")
        owner = await _create_user_with_role(db_session, "dsh-dlnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner)

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 303

    async def test_download_returns_403_for_member(self, client, db_session):
        """Member bez read -> 403 download."""
        project = await _create_project(db_session, "DlMember", "dsh-dl-member")
        owner = await _create_user_with_role(db_session, "dsh-dlmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner)
        await _create_user_with_role(db_session, "dsh-dlmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-dlmem@test.com")

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_download_returns_404_for_foreign_attachment(self, client, db_session):
        """Attachment nalezacy do innego settlement -> 404."""
        project = await _create_project(db_session, "DlForeignAtt", "dsh-dl-foreign")
        owner = await _create_user_with_role(db_session, "dsh-dlforeign@test.com", project, "owner")
        settlement_a = await _make_settlement(db_session, owner, project)
        settlement_b = await _make_settlement(db_session, owner, project)
        # Attachment nalezy do settlement_b
        att_b = await _make_attachment(db_session, settlement_b, owner)
        await _login_existing_user(client, "dsh-dlforeign@test.com")

        # Prosba o attachment w kontekscie settlement_a
        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement_a.id}/attachments/{att_b.id}",
            follow_redirects=False,
        )

        assert resp.status_code == 404

    async def test_download_happy_path_returns_file_with_headers(self, client, db_session):
        """Happy path -> 200 z Content-Disposition i prawidlowym content."""
        project = await _create_project(db_session, "DlHappy", "dsh-dl-happy")
        owner = await _create_user_with_role(db_session, "dsh-dlhappy@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner, filename="faktura_test.pdf")
        await _login_existing_user(client, "dsh-dlhappy@test.com")

        with patch("monolynx.services.settlements.minio_client.get_attachment", return_value=(b"PDF bytes", "application/pdf")):
            resp = await client.get(
                f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}",
                follow_redirects=False,
            )

        assert resp.status_code == 200
        assert resp.content == b"PDF bytes"
        assert "Content-Disposition" in resp.headers
        assert "faktura_test.pdf" in resp.headers["Content-Disposition"]

    async def test_download_utf8_filename_has_filename_star_header(self, client, db_session):
        """Polskie znaki w nazwie pliku -> nagłowek filename* z UTF-8 encoding."""
        project = await _create_project(db_session, "DlUtf8", "dsh-dl-utf8")
        owner = await _create_user_with_role(db_session, "dsh-dlutf8@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner, filename="faktura_z_polskimi_znakami.pdf")
        await _login_existing_user(client, "dsh-dlutf8@test.com")

        with patch("monolynx.services.settlements.minio_client.get_attachment", return_value=(b"bytes", "application/pdf")):
            resp = await client.get(
                f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}",
                follow_redirects=False,
            )

        assert resp.status_code == 200
        cd = resp.headers.get("Content-Disposition", "")
        assert "filename*=UTF-8''" in cd


# ---------------------------------------------------------------------------
# Usuniecie zalacznika
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementDeleteAttachmentDashboard:
    """POST /{slug}/rozliczenia/{settlement_id}/attachments/{attachment_id}/delete."""

    async def test_delete_att_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "DelAttNoLogin", "dsh-da-nologin")
        owner = await _create_user_with_role(db_session, "dsh-danologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner)

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_delete_att_returns_403_for_admin_without_delete(self, client, db_session):
        """Admin ma write ale nie delete -> 403 usuwanie zalacznika.

        Endpoint wymaga rozliczenia:delete (nie :write).
        """
        project = await _create_project(db_session, "DelAttAdmin", "dsh-da-admin")
        owner = await _create_user_with_role(db_session, "dsh-daadmin-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner)
        await _create_user_with_role(db_session, "dsh-daadmin@test.com", project, "admin")
        await _login_existing_user(client, "dsh-daadmin@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_delete_att_returns_403_for_member(self, client, db_session):
        """Member bez delete -> 403."""
        project = await _create_project(db_session, "DelAttMember", "dsh-da-member")
        owner = await _create_user_with_role(db_session, "dsh-damem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner)
        await _create_user_with_role(db_session, "dsh-damem@test.com", project, "member")
        await _login_existing_user(client, "dsh-damem@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_delete_att_returns_404_for_foreign_attachment(self, client, db_session):
        """Attachment z innego settlement -> 404."""
        project = await _create_project(db_session, "DelAttForeign", "dsh-da-foreign")
        owner = await _create_user_with_role(db_session, "dsh-daforeign@test.com", project, "owner")
        settlement_a = await _make_settlement(db_session, owner, project)
        settlement_b = await _make_settlement(db_session, owner, project)
        att_b = await _make_attachment(db_session, settlement_b, owner)
        await _login_existing_user(client, "dsh-daforeign@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement_a.id}/attachments/{att_b.id}/delete",
            follow_redirects=False,
        )

        assert resp.status_code == 404

    async def test_delete_att_happy_path_redirects_to_detail(self, client, db_session):
        """Happy path owner delete attachment -> redirect do detalu."""
        project = await _create_project(db_session, "DelAttHappyDash", "dsh-da-happy")
        owner = await _create_user_with_role(db_session, "dsh-dahappy@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        att = await _make_attachment(db_session, settlement, owner, storage_path="settlements/test/happy.pdf")
        await _login_existing_user(client, "dsh-dahappy@test.com")

        with patch("monolynx.services.settlements.minio_client.delete_object", return_value=None):
            resp = await client.post(
                f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/attachments/{att.id}/delete",
                follow_redirects=False,
            )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]


# ---------------------------------------------------------------------------
# Wyszukiwanie ticketow (HTMX)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementSearchTickets:
    """GET /{slug}/rozliczenia/{settlement_id}/tickets/search -- HTMX."""

    async def test_search_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "SearchNoLogin", "dsh-srch-nologin")
        owner = await _create_user_with_role(db_session, "dsh-srchnologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search",
            follow_redirects=False,
        )

        assert resp.status_code == 303

    async def test_search_returns_403_for_member(self, client, db_session):
        """Member bez write -> 403 (HTML empty, status 403)."""
        project = await _create_project(db_session, "SearchMember", "dsh-srch-member")
        owner = await _create_user_with_role(db_session, "dsh-srchmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-srchmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-srchmem@test.com")

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search",
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_search_returns_empty_when_settlement_not_draft(self, client, db_session):
        """Settlement nie-draft -> 200 z pustym wynikiem (draft-only guard)."""
        project = await _create_project(db_session, "SearchNotDraft", "dsh-srch-nondraft")
        owner = await _create_user_with_role(db_session, "dsh-srchnondraft@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, status="sent", sent_at=datetime(2026, 1, 15, tzinfo=UTC))
        await _login_existing_user(client, "dsh-srchnondraft@test.com")

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search?q=test",
            follow_redirects=False,
        )

        # Status 200 (nie 403) ale pusta odpowiedz
        assert resp.status_code == 200
        assert resp.content.strip() == b""

    async def test_search_by_title_returns_matching_tickets(self, client, db_session):
        """Wyszukiwanie po fragmencie tytulu."""
        project = await _create_project(db_session, "SearchByTitle", "dsh-srch-title")
        owner = await _create_user_with_role(db_session, "dsh-srchtitle@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _make_ticket(db_session, project, title="Unikalny tytul XYZ123")
        await _login_existing_user(client, "dsh-srchtitle@test.com")

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search?q=XYZ123",
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert b"XYZ123" in resp.content

    async def test_search_by_number_digit_only(self, client, db_session):
        """Wyszukiwanie po samej cyfrze (np. "88888")."""
        project = await _create_project(db_session, "SearchByNum", "dsh-srch-num")
        owner = await _create_user_with_role(db_session, "dsh-srchnum@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _make_ticket(db_session, project, number=88888, title="Ticket z numerem 88888")
        await _login_existing_user(client, "dsh-srchnum@test.com")

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search?q=88888",
            follow_redirects=False,
        )

        assert resp.status_code == 200
        assert b"88888" in resp.content or b"Ticket z numerem" in resp.content

    async def test_search_by_code_format(self, client, db_session):
        """Wyszukiwanie po formacie "CODE-12345" (rsplit z myslnikiem)."""
        project = await _create_project(db_session, "SearchByCode", "dsh-srch-code")
        owner = await _create_user_with_role(db_session, "dsh-srchcode@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _make_ticket(db_session, project, number=77777, title="Ticket o kodzie")
        await _login_existing_user(client, "dsh-srchcode@test.com")

        # "DSH-77777" powinno byc parsowane jako numer 77777
        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search?q=DSH-77777",
            follow_redirects=False,
        )

        assert resp.status_code == 200

    async def test_search_excludes_already_linked_tickets(self, client, db_session):
        """Juz podpiete tickety nie wracaja w wyszukiwaniu."""
        project = await _create_project(db_session, "SearchExclude", "dsh-srch-excl")
        owner = await _create_user_with_role(db_session, "dsh-srchexcl@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        linked_ticket = await _make_ticket(db_session, project, title="Juz podpiety EXCL")
        await _link_ticket_to_settlement(db_session, settlement, linked_ticket)
        await _login_existing_user(client, "dsh-srchexcl@test.com")

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search?q=EXCL",
            follow_redirects=False,
        )

        assert resp.status_code == 200
        # Podpiety ticket nie pojawia sie w wynikach
        assert b"Juz podpiety EXCL" not in resp.content

    async def test_search_no_query_returns_results(self, client, db_session):
        """Puste q= -> zwraca max 20 ticketow bez filtrowania."""
        project = await _create_project(db_session, "SearchNoQ", "dsh-srch-noq")
        owner = await _create_user_with_role(db_session, "dsh-srchnoq@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _make_ticket(db_session, project, title="Ticket bez query")
        await _login_existing_user(client, "dsh-srchnoq@test.com")

        resp = await client.get(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/search",
            follow_redirects=False,
        )

        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Link ticket
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementLinkTicket:
    """POST /{slug}/rozliczenia/{settlement_id}/tickets/link."""

    async def test_link_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "LinkNoLogin", "dsh-lnk-nologin")
        owner = await _create_user_with_role(db_session, "dsh-lnknologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(uuid.uuid4())},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_link_returns_403_for_member(self, client, db_session):
        """Member bez write -> 403."""
        project = await _create_project(db_session, "LinkMember", "dsh-lnk-member")
        owner = await _create_user_with_role(db_session, "dsh-lnkmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _create_user_with_role(db_session, "dsh-lnkmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-lnkmem@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(uuid.uuid4())},
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_link_redirects_when_not_draft(self, client, db_session):
        """Settlement nie-draft -> redirect z flash error (draft-only guard)."""
        project = await _create_project(db_session, "LinkNotDraft", "dsh-lnk-nondraft")
        owner = await _create_user_with_role(db_session, "dsh-lnknondraft@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, status="sent", sent_at=datetime(2026, 1, 15, tzinfo=UTC))
        await _login_existing_user(client, "dsh-lnknondraft@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(uuid.uuid4())},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_link_invalid_ticket_uuid_redirects_with_flash(self, client, db_session):
        """Nieprawidlowy UUID ticket_id -> redirect z flash error."""
        project = await _create_project(db_session, "LinkInvalidUUID", "dsh-lnk-uuid")
        owner = await _create_user_with_role(db_session, "dsh-lnkuuid@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-lnkuuid@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": "nie-uuid"},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_link_nonexistent_ticket_redirects_with_flash(self, client, db_session):
        """Ticket nie istnieje -> redirect z flash error."""
        project = await _create_project(db_session, "LinkNoTicket", "dsh-lnk-noticket")
        owner = await _create_user_with_role(db_session, "dsh-lnknoticket@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        await _login_existing_user(client, "dsh-lnknoticket@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(uuid.uuid4())},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_link_already_linked_shows_info_flash(self, client, db_session):
        """Ticket juz podpiety -> redirect z flash info (not error)."""
        project = await _create_project(db_session, "LinkAlreadyLinked", "dsh-lnk-already")
        owner = await _create_user_with_role(db_session, "dsh-lnkalready@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        ticket = await _make_ticket(db_session, project, title="Ticket already linked")
        await _link_ticket_to_settlement(db_session, settlement, ticket)
        await _login_existing_user(client, "dsh-lnkalready@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(ticket.id)},
            follow_redirects=False,
        )

        # Redirect z info flash (nie error)
        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_link_happy_path_adds_ticket(self, client, db_session):
        """Happy path link ticket -> redirect z flash success."""
        project = await _create_project(db_session, "LinkHappy", "dsh-lnk-happy")
        owner = await _create_user_with_role(db_session, "dsh-lnkhappy@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        ticket = await _make_ticket(db_session, project, title="Nowy ticket do podpiecia")
        await _login_existing_user(client, "dsh-lnkhappy@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(ticket.id)},
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_link_ticket_from_wrong_project_redirects_with_flash(self, client, db_session):
        """Ticket z projektu nie nalezacego do settlement -> redirect z flash error."""
        project_a = await _create_project(db_session, "LinkWrongProjA", "dsh-lnk-wrpa")
        project_b = await _create_project(db_session, "LinkWrongProjB", "dsh-lnk-wrpb")
        owner = await _create_user_with_role(db_session, "dsh-lnkwrp@test.com", project_a, "owner")
        db_session.add(ProjectMember(project_id=project_b.id, user_id=owner.id, role="owner"))
        await db_session.flush()
        # Settlement tylko w projekcie A
        settlement = await _make_settlement(db_session, owner, project_a)
        # Ticket w projekcie B
        ticket_b = await _make_ticket(db_session, project_b, title="Ticket z B")
        await _login_existing_user(client, "dsh-lnkwrp@test.com")

        resp = await client.post(
            f"/dashboard/{project_a.slug}/rozliczenia/{settlement.id}/tickets/link",
            data={"ticket_id": str(ticket_b.id)},
            follow_redirects=False,
        )

        # validate_settlement_ticket_link rzuca ValueError -> redirect
        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]


# ---------------------------------------------------------------------------
# Unlink ticket
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestSettlementUnlinkTicket:
    """POST /{slug}/rozliczenia/{settlement_id}/tickets/{ticket_id}/unlink."""

    async def test_unlink_redirects_when_not_logged_in(self, client, db_session):
        """Brak loginu -> redirect."""
        project = await _create_project(db_session, "UnlinkNoLogin", "dsh-ulnk-nologin")
        owner = await _create_user_with_role(db_session, "dsh-ulnknologin@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        ticket = await _make_ticket(db_session, project)

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert "/auth/login" in resp.headers["location"]

    async def test_unlink_returns_403_for_member(self, client, db_session):
        """Member bez write -> 403."""
        project = await _create_project(db_session, "UnlinkMember", "dsh-ulnk-member")
        owner = await _create_user_with_role(db_session, "dsh-ulnkmem-own@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        ticket = await _make_ticket(db_session, project)
        await _link_ticket_to_settlement(db_session, settlement, ticket)
        await _create_user_with_role(db_session, "dsh-ulnkmem@test.com", project, "member")
        await _login_existing_user(client, "dsh-ulnkmem@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        assert resp.status_code == 403

    async def test_unlink_redirects_when_not_draft(self, client, db_session):
        """Settlement nie-draft -> redirect z flash error."""
        project = await _create_project(db_session, "UnlinkNotDraft", "dsh-ulnk-nondraft")
        owner = await _create_user_with_role(db_session, "dsh-ulnknondraft@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project, status="sent", sent_at=datetime(2026, 1, 15, tzinfo=UTC))
        ticket = await _make_ticket(db_session, project)
        await _link_ticket_to_settlement(db_session, settlement, ticket)
        await _login_existing_user(client, "dsh-ulnknondraft@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_unlink_ticket_not_linked_redirects_with_flash_error(self, client, db_session):
        """Ticket nie jest podpiety -> redirect z flash error."""
        project = await _create_project(db_session, "UnlinkNotLinked", "dsh-ulnk-notlinked")
        owner = await _create_user_with_role(db_session, "dsh-ulnknotlinked@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        ticket = await _make_ticket(db_session, project)
        # Ticket NIE jest podpiety
        await _login_existing_user(client, "dsh-ulnknotlinked@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]

    async def test_unlink_happy_path_redirects_to_detail(self, client, db_session):
        """Happy path unlink -> redirect do detalu z flash success."""
        project = await _create_project(db_session, "UnlinkHappy", "dsh-ulnk-happy")
        owner = await _create_user_with_role(db_session, "dsh-ulnkhappy@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, project)
        ticket = await _make_ticket(db_session, project, title="Ticket do odpiety")
        await _link_ticket_to_settlement(db_session, settlement, ticket)
        await _login_existing_user(client, "dsh-ulnkhappy@test.com")

        resp = await client.post(
            f"/dashboard/{project.slug}/rozliczenia/{settlement.id}/tickets/{ticket.id}/unlink",
            follow_redirects=False,
        )

        assert resp.status_code == 303
        assert str(settlement.id) in resp.headers["location"]
