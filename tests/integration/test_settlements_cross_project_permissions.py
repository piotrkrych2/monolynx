"""Testy integracyjne MON-240: uprawnienia rozliczenia wieloprojektowego.

Kontrakt (nie tylko projekt ze sluga URL):
- odczyty (detal, zalacznik, lista globalna, narzedzia MCP) wymagaja rozliczenia:read
  we wszystkich projektach rozliczenia; lista globalna i list_settlements pomijaja takie rozliczenia,
- edycja (formularz i zapis) wymaga rozliczenia:write we wszystkich projektach,
- link/unlink wymaga read we wszystkich projektach (pierwsza warstwa) oraz write w projekcie
  ticketu (druga warstwa); kazda warstwa ma test z aktorem, ktorego zatrzymuje tylko ona,
- wyszukiwarka ticketow zwraca tylko tickety z projektow, w ktorych user ma write.

Scenariusz bazowy: rozliczenie A+B (draft), ticket w A i ticket w B. Aktorzy:
- owner_ab: owner w A i B (kontrola pozytywna),
- only_a: owner tylko w A (brak czlonkostwa w B),
- rw: rola niestandardowa - read+write w A, tylko read w B,
- su: superuser (czlonek tylko A, bypass uprawnien).

Przy odmowie asercje dotycza stanu bazy (tabela settlement_tickets), komunikatu warstwy
albo braku danych projektu B w odpowiedzi.
"""

from __future__ import annotations

import secrets
import uuid
from contextlib import contextmanager
from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.role import Role
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_attachment import SettlementAttachment
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.settlement_ticket import SettlementTicket
from monolynx.models.ticket import Ticket
from monolynx.models.user import User
from monolynx.services.auth import hash_password

_MINIO_GET = "monolynx.services.settlements.minio_client.get_attachment"
_ATTACHMENT_BYTES = b"SEKRETNY-ZALACZNIK-B"
# Dwie warstwy odmowy link/unlink: read/write we wszystkich projektach rozliczenia oraz write w projekcie ticketu.
# Nieprawidlowe project_ids: handler re-renderuje edit.html (gałąź walidacji przed serwisem).
_INVALID_EDIT_FORM = {"name": "Zmiana", "period_from": "2026-01-01", "period_to": "2026-01-31", "project_ids": "to-nie-uuid"}
_READ_ALL_DENIAL = "rozliczenia:read we wszystkich projektach rozliczenia"
_TICKET_WRITE_DENIAL = "rozliczenia:write w projekcie ticketu"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(db_session, name: str, slug: str) -> Project:
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


async def _create_user(db_session, email: str, is_superuser: bool = False) -> User:
    user = User(email=email, password_hash=hash_password("testpass123"), is_superuser=is_superuser)
    db_session.add(user)
    await db_session.flush()
    return user


async def _add_member(db_session, project: Project, user: User, role: str = "owner", role_obj: Role | None = None) -> None:
    db_session.add(
        ProjectMember(
            project_id=project.id,
            user_id=user.id,
            role=role,
            role_id=role_obj.id if role_obj else None,
        )
    )
    await db_session.flush()


async def _create_role(db_session, project: Project, name: str, actions: list[str]) -> Role:
    role = Role(name=name, project_id=project.id, permissions={"rozliczenia": actions}, is_system=False)
    db_session.add(role)
    await db_session.flush()
    return role


async def _login_existing_user(client, email: str) -> None:
    response = await client.post(
        "/auth/login",
        data={"email": email, "password": "testpass123"},
        follow_redirects=False,
    )
    assert response.status_code == 303


async def _make_settlement(db_session, creator: User, projects: list[Project], name: str, notes: str | None = None) -> Settlement:
    result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    settlement = Settlement(
        number=int(result.scalar_one()) + 1,
        name=name,
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        status="draft",
        notes=notes,
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()
    for project in projects:
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project.id))
    await db_session.flush()
    return settlement


async def _make_ticket(db_session, project: Project, title: str) -> Ticket:
    ticket = Ticket(
        project_id=project.id,
        number=int(secrets.token_hex(3), 16) % 90000 + 10000,
        title=title,
        status="backlog",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _link(db_session, settlement: Settlement, ticket: Ticket) -> None:
    result = await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == settlement.id))
    result.scalar_one().tickets.append(ticket)
    await db_session.flush()


async def _linked_ids(db_session, settlement: Settlement) -> set[uuid.UUID]:
    """Stan tabeli asocjacyjnej wprost z bazy, bez relacji ORM z identity map."""
    result = await db_session.execute(select(SettlementTicket.ticket_id).where(SettlementTicket.settlement_id == settlement.id))
    return set(result.scalars().all())


async def _build_scenario(db_session) -> SimpleNamespace:
    sfx = secrets.token_hex(4)
    project_a = await _create_project(db_session, f"ProjA-{sfx}", f"xp-a-{sfx}")
    project_b = await _create_project(db_session, f"ProjB-{sfx}", f"xp-b-{sfx}")

    owner_ab = await _create_user(db_session, f"xp_owner_ab_{sfx}@example.com")
    await _add_member(db_session, project_a, owner_ab)
    await _add_member(db_session, project_b, owner_ab)

    only_a = await _create_user(db_session, f"xp_only_a_{sfx}@example.com")
    await _add_member(db_session, project_a, only_a)

    rw = await _create_user(db_session, f"xp_rw_{sfx}@example.com")
    role_a = await _create_role(db_session, project_a, f"rw-a-{sfx}", ["read", "write"])
    role_b = await _create_role(db_session, project_b, f"ro-b-{sfx}", ["read"])
    await _add_member(db_session, project_a, rw, role=f"rw-a-{sfx}"[:20], role_obj=role_a)
    await _add_member(db_session, project_b, rw, role=f"ro-b-{sfx}"[:20], role_obj=role_b)

    su = await _create_user(db_session, f"xp_su_{sfx}@example.com", is_superuser=True)
    await _add_member(db_session, project_a, su, role="member")

    ab = await _make_settlement(db_session, owner_ab, [project_a, project_b], f"Rozl-AB-{sfx}", notes=f"Notatki-AB-{sfx}")
    only_a_settlement = await _make_settlement(db_session, owner_ab, [project_a], f"Rozl-tylkoA-{sfx}")
    ticket_a = await _make_ticket(db_session, project_a, f"TytulA-{sfx}")
    ticket_b = await _make_ticket(db_session, project_b, f"TytulB-{sfx}")

    attachment = SettlementAttachment(
        settlement_id=ab.id,
        category="invoice",
        state="draft",
        filename=f"faktura-{sfx}.pdf",
        storage_path=f"settlements/{ab.id}/2026/01/{uuid.uuid4().hex}.pdf",
        mime_type="application/pdf",
        size=len(_ATTACHMENT_BYTES),
        uploaded_by_id=owner_ab.id,
    )
    db_session.add(attachment)
    await db_session.flush()

    return SimpleNamespace(
        sfx=sfx,
        a=project_a,
        b=project_b,
        owner_ab=owner_ab,
        only_a=only_a,
        rw=rw,
        su=su,
        ab=ab,
        only_a_settlement=only_a_settlement,
        ticket_a=ticket_a,
        ticket_b=ticket_b,
        attachment=attachment,
        key_a=f"{project_a.code}-{ticket_a.number}",
        key_b=f"{project_b.code}-{ticket_b.number}",
    )


def _base(sc: SimpleNamespace) -> str:
    return f"/dashboard/{sc.a.slug}/rozliczenia/{sc.ab.id}"


def _assert_no_project_b_data(text: str, sc: SimpleNamespace) -> None:
    assert sc.ticket_b.title not in text
    assert sc.key_b not in text
    assert sc.b.name not in text


# ---------------------------------------------------------------------------
# Dashboard: szczegoly
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDashboardDetailCrossProject:
    async def test_detail_denied_when_no_access_to_project_b(self, client, db_session):
        """Rozliczenie A+B, user tylko w A: 403 i zadnych danych B w odpowiedzi."""
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.get(_base(sc), follow_redirects=False)

        assert resp.status_code == 403
        _assert_no_project_b_data(resp.text, sc)

    async def test_detail_shows_project_b_ticket_with_access_in_both(self, client, db_session):
        """Para pozytywna: owner A+B widzi ticket B i nazwe projektu B."""
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)
        await _login_existing_user(client, sc.owner_ab.email)

        resp = await client.get(_base(sc), follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ticket_b.title in resp.text
        assert sc.b.name in resp.text

    async def test_detail_allowed_with_read_only_in_project_b(self, client, db_session):
        """Para pozytywna: read w obu projektach wystarcza do odczytu (write tylko w A)."""
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.get(_base(sc), follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ticket_b.title in resp.text

    async def test_detail_allowed_for_superuser_without_membership_in_b(self, client, db_session):
        """Para pozytywna: superuser omija sprawdzenie w projekcie B."""
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)
        await _login_existing_user(client, sc.su.email)

        resp = await client.get(_base(sc), follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ticket_b.title in resp.text

    async def test_detail_of_single_project_settlement_still_allowed_for_only_a(self, client, db_session):
        """Para pozytywna: rozliczenie tylko-A dalej dostepne dla usera tylko w A."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.get(f"/dashboard/{sc.a.slug}/rozliczenia/{sc.only_a_settlement.id}", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.only_a_settlement.name in resp.text


# ---------------------------------------------------------------------------
# Dashboard: formularz edycji
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDashboardEditFormCrossProject:
    async def test_edit_form_denied_when_no_access_to_project_b(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.get(f"{_base(sc)}/edit", follow_redirects=False)

        assert resp.status_code == 403
        assert f"Notatki-AB-{sc.sfx}" not in resp.text
        assert sc.b.name not in resp.text

    async def test_edit_form_denied_with_write_only_in_a(self, client, db_session):
        """Edycja zmienia rozliczenie w calosci, wiec write wymagane w kazdym jego projekcie."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.get(f"{_base(sc)}/edit", follow_redirects=False)

        assert resp.status_code == 403
        assert f"Notatki-AB-{sc.sfx}" not in resp.text

    async def test_edit_submit_with_invalid_data_denied_when_no_access_to_project_b(self, client, db_session):
        """Bledne dane re-renderuja formularz z danymi rozliczenia, wiec odmowa musi je poprzedzac.

        Poprawne dane nie zabilyby mutanta: serwis update_settlement odmawia tak samo.
        """
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.post(f"{_base(sc)}/edit", data=_INVALID_EDIT_FORM, follow_redirects=False)

        assert resp.status_code == 403
        assert sc.ab.name not in resp.text
        assert sc.b.name not in resp.text

    async def test_edit_submit_with_invalid_data_denied_with_write_only_in_a(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.post(f"{_base(sc)}/edit", data=_INVALID_EDIT_FORM, follow_redirects=False)

        assert resp.status_code == 403
        assert sc.ab.name not in resp.text

    async def test_edit_submit_with_invalid_data_rerenders_form_with_write_in_both(self, client, db_session):
        """Para pozytywna: te same bledne dane u owner A+B daja formularz z nazwa rozliczenia."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.owner_ab.email)

        resp = await client.post(f"{_base(sc)}/edit", data=_INVALID_EDIT_FORM, follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ab.name in resp.text

    async def test_edit_form_shows_notes_with_write_in_both(self, client, db_session):
        """Para pozytywna."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.owner_ab.email)

        resp = await client.get(f"{_base(sc)}/edit", follow_redirects=False)

        assert resp.status_code == 200
        assert f"Notatki-AB-{sc.sfx}" in resp.text


# ---------------------------------------------------------------------------
# Dashboard: zalacznik
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDashboardAttachmentCrossProject:
    async def test_download_denied_when_no_access_to_project_b(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        with patch(_MINIO_GET, return_value=(_ATTACHMENT_BYTES, "application/pdf")):
            resp = await client.get(f"{_base(sc)}/attachments/{sc.attachment.id}", follow_redirects=False)

        assert resp.status_code == 403
        assert _ATTACHMENT_BYTES not in resp.content

    async def test_download_allowed_with_access_in_both(self, client, db_session):
        """Para pozytywna: owner A+B pobiera zalacznik."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.owner_ab.email)

        with patch(_MINIO_GET, return_value=(_ATTACHMENT_BYTES, "application/pdf")):
            resp = await client.get(f"{_base(sc)}/attachments/{sc.attachment.id}", follow_redirects=False)

        assert resp.status_code == 200
        assert resp.content == _ATTACHMENT_BYTES

    async def test_download_allowed_for_superuser(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.su.email)

        with patch(_MINIO_GET, return_value=(_ATTACHMENT_BYTES, "application/pdf")):
            resp = await client.get(f"{_base(sc)}/attachments/{sc.attachment.id}", follow_redirects=False)

        assert resp.status_code == 200
        assert resp.content == _ATTACHMENT_BYTES


# ---------------------------------------------------------------------------
# Dashboard: wyszukiwarka ticketow
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDashboardSearchCrossProject:
    async def test_search_does_not_leak_project_b_ticket_for_only_a(self, client, db_session):
        """Dopuszczalna odmowa (403) albo pusty wynik - kluczowe: brak ticketu B w tresci."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.get(f"{_base(sc)}/tickets/search", follow_redirects=False)

        assert sc.ticket_b.title not in resp.text
        assert sc.key_b not in resp.text

    async def test_search_returns_only_projects_with_write(self, client, db_session):
        """read A+B, write tylko A: wynik zawiera ticket A, nie zawiera ticketu B."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.get(f"{_base(sc)}/tickets/search", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ticket_a.title in resp.text
        assert sc.ticket_b.title not in resp.text
        assert sc.key_b not in resp.text

    async def test_search_by_text_does_not_find_ticket_b_with_write_only_in_a(self, client, db_session):
        """Filtr tekstowy (wspolny sufiks tytulow A i B) nie omija filtra projektow z write.

        Zapytanie to sam sufiks, bo szablon odbija zapytanie przy pustym wyniku.
        """
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.get(f"{_base(sc)}/tickets/search", params={"q": sc.sfx}, follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ticket_a.title in resp.text
        assert sc.ticket_b.title not in resp.text

    async def test_search_returns_both_projects_with_write_in_both(self, client, db_session):
        """Para pozytywna: owner A+B znajduje tickety obu projektow."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.owner_ab.email)

        resp = await client.get(f"{_base(sc)}/tickets/search", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ticket_a.title in resp.text
        assert sc.ticket_b.title in resp.text

    async def test_search_returns_both_projects_for_superuser(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.su.email)

        resp = await client.get(f"{_base(sc)}/tickets/search", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ticket_a.title in resp.text
        assert sc.ticket_b.title in resp.text


# ---------------------------------------------------------------------------
# Dashboard: link / unlink
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDashboardLinkUnlinkCrossProject:
    async def test_link_ticket_b_denied_for_only_a(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.post(f"{_base(sc)}/tickets/link", data={"ticket_id": str(sc.ticket_b.id)}, follow_redirects=False)

        assert resp.status_code == 403
        assert _READ_ALL_DENIAL in resp.text
        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_unlink_ticket_b_denied_for_only_a(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.post(f"{_base(sc)}/tickets/{sc.ticket_b.id}/unlink", follow_redirects=False)

        assert resp.status_code == 403
        assert _READ_ALL_DENIAL in resp.text
        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_b.id}

    async def test_link_ticket_a_denied_by_read_all_check_alone(self, client, db_session):
        """only_a ma write w A i ticket jest z A: zatrzymuje go wylacznie read we wszystkich projektach."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.post(f"{_base(sc)}/tickets/link", data={"ticket_id": str(sc.ticket_a.id)}, follow_redirects=False)

        assert resp.status_code == 403
        assert _READ_ALL_DENIAL in resp.text
        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_unlink_ticket_a_denied_by_read_all_check_alone(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_a)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.post(f"{_base(sc)}/tickets/{sc.ticket_a.id}/unlink", follow_redirects=False)

        assert resp.status_code == 403
        assert _READ_ALL_DENIAL in resp.text
        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_a.id}

    async def test_link_ticket_b_denied_with_write_only_in_a(self, client, db_session):
        """read A+B, write tylko A: ticket B nie zostaje podpiety."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.post(f"{_base(sc)}/tickets/link", data={"ticket_id": str(sc.ticket_b.id)}, follow_redirects=False)

        assert resp.status_code == 403
        assert _TICKET_WRITE_DENIAL in resp.text
        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_unlink_ticket_b_denied_with_write_only_in_a(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.post(f"{_base(sc)}/tickets/{sc.ticket_b.id}/unlink", follow_redirects=False)

        assert resp.status_code == 403
        assert _TICKET_WRITE_DENIAL in resp.text
        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_b.id}

    async def test_link_ticket_a_works_with_write_only_in_a(self, client, db_session):
        """Para pozytywna: ticket z projektu z write dalej da sie podpiac."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.post(f"{_base(sc)}/tickets/link", data={"ticket_id": str(sc.ticket_a.id)}, follow_redirects=False)

        assert resp.status_code == 303
        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_a.id}

    async def test_unlink_ticket_a_works_with_write_only_in_a(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_a)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.post(f"{_base(sc)}/tickets/{sc.ticket_a.id}/unlink", follow_redirects=False)

        assert resp.status_code == 303
        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_link_and_unlink_ticket_b_work_with_write_in_both(self, client, db_session):
        """Para pozytywna: owner A+B podpina i odpina ticket B."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.owner_ab.email)

        await client.post(f"{_base(sc)}/tickets/link", data={"ticket_id": str(sc.ticket_b.id)}, follow_redirects=False)
        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_b.id}

        await client.post(f"{_base(sc)}/tickets/{sc.ticket_b.id}/unlink", follow_redirects=False)
        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_link_ticket_b_works_for_superuser(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.su.email)

        await client.post(f"{_base(sc)}/tickets/link", data={"ticket_id": str(sc.ticket_b.id)}, follow_redirects=False)

        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_b.id}


# ---------------------------------------------------------------------------
# Dashboard: lista globalna
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGlobalListCrossProject:
    async def test_global_list_hides_settlement_with_inaccessible_project(self, client, db_session):
        """Rozliczenie A+B nie jest listowane dla usera tylko w A; tylko-A jest."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.only_a.email)

        resp = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.only_a_settlement.name in resp.text
        assert sc.ab.name not in resp.text
        assert sc.b.name not in resp.text

    async def test_global_list_shows_cross_project_settlement_with_access_in_both(self, client, db_session):
        """Para pozytywna."""
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.owner_ab.email)

        resp = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ab.name in resp.text
        assert sc.only_a_settlement.name in resp.text

    async def test_global_list_shows_cross_project_settlement_with_read_in_both(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.rw.email)

        resp = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ab.name in resp.text

    async def test_global_list_shows_cross_project_settlement_for_superuser(self, client, db_session):
        sc = await _build_scenario(db_session)
        await _login_existing_user(client, sc.su.email)

        resp = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert resp.status_code == 200
        assert sc.ab.name in resp.text


# ---------------------------------------------------------------------------
# MCP
# ---------------------------------------------------------------------------


def _make_ctx() -> MagicMock:
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": "Bearer test-token"}
    return ctx


@contextmanager
def _mcp_as(db_session, user: User):
    """Podstawia sesje testowa (commit -> flush) i uwierzytelnionego usera."""
    from contextlib import asynccontextmanager

    original_commit = db_session.commit

    async def _flush_instead():
        await db_session.flush()

    @asynccontextmanager
    async def _factory():
        db_session.commit = _flush_instead
        try:
            yield db_session
        finally:
            db_session.commit = original_commit

    with (
        patch("monolynx.mcp_server.async_session_factory", _factory),
        patch("monolynx.mcp_server.verify_mcp_token", AsyncMock(return_value=user)),
        patch(_MINIO_GET, return_value=(_ATTACHMENT_BYTES, "application/pdf")),
    ):
        yield


async def _call_single_settlement_tool(tool: str, sc: SimpleNamespace):
    import monolynx.mcp_server as mcp_server

    ctx = _make_ctx()
    fn = getattr(mcp_server, tool)
    if tool == "get_settlement_attachment":
        return await fn(ctx, sc.a.slug, str(sc.ab.id), str(sc.attachment.id))
    return await fn(ctx, sc.a.slug, str(sc.ab.id))


_SINGLE_SETTLEMENT_READ_TOOLS = [
    "get_settlement",
    "list_settlement_tickets",
    "list_settlement_attachments",
    "get_settlement_attachment",
]


@pytest.mark.integration
class TestMcpReadCrossProject:
    @pytest.mark.parametrize("tool", _SINGLE_SETTLEMENT_READ_TOOLS)
    async def test_read_tool_denied_when_no_access_to_project_b(self, db_session, tool):
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)

        with _mcp_as(db_session, sc.only_a), pytest.raises(ValueError, match="Brak uprawnienia"):
            await _call_single_settlement_tool(tool, sc)

    @pytest.mark.parametrize("tool", _SINGLE_SETTLEMENT_READ_TOOLS)
    @pytest.mark.parametrize("actor", ["owner_ab", "rw", "su"])
    async def test_read_tool_allowed_with_access_in_both(self, db_session, tool, actor):
        """Para pozytywna: owner A+B, read A+B (write tylko A) i superuser."""
        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)

        with _mcp_as(db_session, getattr(sc, actor)):
            result = await _call_single_settlement_tool(tool, sc)

        if tool == "get_settlement":
            assert result["settlement_id"] == str(sc.ab.id)
            assert [t["title"] for t in result["tickets"]] == [sc.ticket_b.title]
        elif tool == "list_settlement_tickets":
            assert [t["ticket_id"] for t in result] == [str(sc.ticket_b.id)]
        elif tool == "list_settlement_attachments":
            assert [a["attachment_id"] for a in result] == [str(sc.attachment.id)]
        else:
            assert result["attachment_id"] == str(sc.attachment.id)

    async def test_list_settlements_excludes_settlement_with_inaccessible_project(self, db_session):
        from monolynx.mcp_server import list_settlements

        sc = await _build_scenario(db_session)

        with _mcp_as(db_session, sc.only_a):
            result = await list_settlements(_make_ctx(), sc.a.slug)

        ids = {s["settlement_id"] for s in result["settlements"]}
        assert str(sc.only_a_settlement.id) in ids
        assert str(sc.ab.id) not in ids
        assert result["total"] == 1

    @pytest.mark.parametrize("actor", ["owner_ab", "rw", "su"])
    async def test_list_settlements_includes_cross_project_settlement_with_access_in_both(self, db_session, actor):
        from monolynx.mcp_server import list_settlements

        sc = await _build_scenario(db_session)

        with _mcp_as(db_session, getattr(sc, actor)):
            result = await list_settlements(_make_ctx(), sc.a.slug)

        ids = {s["settlement_id"] for s in result["settlements"]}
        assert {str(sc.ab.id), str(sc.only_a_settlement.id)} <= ids


@pytest.mark.integration
class TestMcpLinkUnlinkCrossProject:
    async def test_link_ticket_b_denied_for_only_a(self, db_session):
        from monolynx.mcp_server import link_ticket_to_settlement

        sc = await _build_scenario(db_session)

        with _mcp_as(db_session, sc.only_a), pytest.raises(ValueError, match=_READ_ALL_DENIAL):
            await link_ticket_to_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_b.id))

        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_link_ticket_a_denied_by_read_all_check_alone(self, db_session):
        """only_a ma write w A i ticket jest z A: zatrzymuje go wylacznie read we wszystkich projektach."""
        from monolynx.mcp_server import link_ticket_to_settlement

        sc = await _build_scenario(db_session)

        with _mcp_as(db_session, sc.only_a), pytest.raises(ValueError, match=_READ_ALL_DENIAL):
            await link_ticket_to_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_a.id))

        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_unlink_ticket_a_denied_by_read_all_check_alone(self, db_session):
        from monolynx.mcp_server import unlink_ticket_from_settlement

        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_a)

        with _mcp_as(db_session, sc.only_a), pytest.raises(ValueError, match=_READ_ALL_DENIAL):
            await unlink_ticket_from_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_a.id))

        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_a.id}

    async def test_unlink_ticket_b_denied_for_only_a(self, db_session):
        from monolynx.mcp_server import unlink_ticket_from_settlement

        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)

        with _mcp_as(db_session, sc.only_a), pytest.raises(ValueError, match=_READ_ALL_DENIAL):
            await unlink_ticket_from_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_b.id))

        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_b.id}

    async def test_link_ticket_b_denied_with_write_only_in_a(self, db_session):
        from monolynx.mcp_server import link_ticket_to_settlement

        sc = await _build_scenario(db_session)

        with _mcp_as(db_session, sc.rw), pytest.raises(ValueError, match=_TICKET_WRITE_DENIAL):
            await link_ticket_to_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_b.id))

        assert await _linked_ids(db_session, sc.ab) == set()

    async def test_unlink_ticket_b_denied_with_write_only_in_a(self, db_session):
        from monolynx.mcp_server import unlink_ticket_from_settlement

        sc = await _build_scenario(db_session)
        await _link(db_session, sc.ab, sc.ticket_b)

        with _mcp_as(db_session, sc.rw), pytest.raises(ValueError, match=_TICKET_WRITE_DENIAL):
            await unlink_ticket_from_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_b.id))

        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_b.id}

    async def test_link_and_unlink_ticket_a_work_with_write_only_in_a(self, db_session):
        """Para pozytywna: ticket z projektu z write dalej da sie podpiac i odpiac."""
        from monolynx.mcp_server import link_ticket_to_settlement, unlink_ticket_from_settlement

        sc = await _build_scenario(db_session)

        with _mcp_as(db_session, sc.rw):
            linked = await link_ticket_to_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_a.id))
        assert linked["linked"] is True
        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_a.id}

        with _mcp_as(db_session, sc.rw):
            unlinked = await unlink_ticket_from_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_a.id))
        assert unlinked["unlinked"] is True
        assert await _linked_ids(db_session, sc.ab) == set()

    @pytest.mark.parametrize("actor", ["owner_ab", "su"])
    async def test_link_and_unlink_ticket_b_work_with_write_in_both(self, db_session, actor):
        from monolynx.mcp_server import link_ticket_to_settlement, unlink_ticket_from_settlement

        sc = await _build_scenario(db_session)

        with _mcp_as(db_session, getattr(sc, actor)):
            await link_ticket_to_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_b.id))
        assert await _linked_ids(db_session, sc.ab) == {sc.ticket_b.id}

        with _mcp_as(db_session, getattr(sc, actor)):
            await unlink_ticket_from_settlement(_make_ctx(), sc.a.slug, str(sc.ab.id), str(sc.ticket_b.id))
        assert await _linked_ids(db_session, sc.ab) == set()
