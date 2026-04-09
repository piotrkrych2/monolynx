"""Testy integracyjne -- MON-64: globalny widok /dashboard/rozliczenia + MCP tools rozliczen."""

from __future__ import annotations

import base64
import secrets
import uuid
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

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
        slug = f"glb-{secrets.token_hex(4)}"
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


async def _create_settlement(
    db_session,
    project: Project,
    creator: User,
    status: str = "draft",
    name: str | None = None,
    period_from: date = date(2026, 1, 1),
    period_to: date = date(2026, 1, 31),
) -> Settlement:
    result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    next_number = int(result.scalar_one()) + 1

    settlement = Settlement(
        number=next_number,
        name=name or f"Rozliczenie GLB {next_number}",
        period_from=period_from,
        period_to=period_to,
        status=status,
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()

    sp = SettlementProject(settlement_id=settlement.id, project_id=project.id)
    db_session.add(sp)
    await db_session.flush()

    return settlement


async def _create_ticket(db_session, project: Project, number: int | None = None) -> Ticket:
    if number is None:
        import random

        number = random.randint(10000, 99999)
    ticket = Ticket(
        project_id=project.id,
        number=number,
        title=f"Ticket GLB #{number}",
        status="backlog",
        priority="medium",
    )
    db_session.add(ticket)
    await db_session.flush()
    return ticket


async def _create_user_with_role(
    db_session,
    email: str,
    project: Project,
    role: str = "member",
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


# MCP helpers


def _make_ctx(token: str = "test-token") -> MagicMock:
    ctx = MagicMock()
    ctx.request_context = MagicMock()
    ctx.request_context.request = MagicMock()
    ctx.request_context.request.headers = {"authorization": f"Bearer {token}"}
    return ctx


def _make_mock_factory(db_session):
    """Zastepuje async_session_factory dla MCP tools — commit() -> flush()."""
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

    return _factory


# ---------------------------------------------------------------------------
# Testy globalnego widoku: GET /dashboard/rozliczenia
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestGlobalSettlementsView:
    """MON-64 -- globalny widok /dashboard/rozliczenia."""

    async def test_unauthenticated_redirects_to_login(self, client):
        """Bez sesji -> 302 /auth/login."""
        response = await client.get("/dashboard/rozliczenia", follow_redirects=False)
        assert response.status_code in (302, 303)
        assert "/auth/login" in response.headers["location"]

    async def test_user_with_read_in_one_project_sees_only_that_project_settlements(self, client, db_session):
        """User owner projektu A (rozliczenia:read) + member projektu B (brak read).
        Settlement w A, settlement w B.
        GET /dashboard/rozliczenia -> widzi tylko settlement z A.
        """
        suffix = secrets.token_hex(4)
        project_a = await _create_project(db_session, f"Project A {suffix}")
        project_b = await _create_project(db_session, f"Project B {suffix}")

        email = f"global_read_filter_{suffix}@example.com"
        # owner w A (ma rozliczenia:read), member w B (brak rozliczenia:read)
        user = await _create_user_with_role(db_session, email, project_a, role="owner")
        # Dodaj membership w B jako member (brak rozliczenia:read)
        member_b = ProjectMember(project_id=project_b.id, user_id=user.id, role="member")
        db_session.add(member_b)
        await db_session.flush()

        s_a = await _create_settlement(db_session, project_a, user, name=f"Rozl A {suffix}")
        _s_b = await _create_settlement(db_session, project_b, user, name=f"Rozl B {suffix}")

        await _login_existing_user(client, email)
        response = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert response.status_code == 200
        text = response.text
        assert s_a.name in text
        assert f"Rozl B {suffix}" not in text

    async def test_superuser_sees_all_active_settlements(self, client, db_session):
        """Superuser widzi rozliczenia ze wszystkich aktywnych projektow."""
        suffix = secrets.token_hex(4)
        project_x = await _create_project(db_session, f"Super X {suffix}")
        project_y = await _create_project(db_session, f"Super Y {suffix}")

        email = f"superuser_global_{suffix}@example.com"
        superuser = User(
            email=email,
            password_hash=hash_password("testpass123"),
            is_superuser=True,
        )
        db_session.add(superuser)
        await db_session.flush()

        s_x = await _create_settlement(db_session, project_x, superuser, name=f"Rozl X {suffix}")
        s_y = await _create_settlement(db_session, project_y, superuser, name=f"Rozl Y {suffix}")

        await _login_existing_user(client, email)
        response = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert response.status_code == 200
        text = response.text
        assert s_x.name in text
        assert s_y.name in text

    async def test_user_without_any_rozliczenia_read_sees_empty_list(self, client, db_session):
        """User bez uprawnien rozliczenia:read w zadnym projekcie -> GET 200 z empty state, NIE 403."""
        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"NoRead {suffix}")

        email = f"no_read_{suffix}@example.com"
        # member role: rozliczenia: []
        await _create_user_with_role(db_session, email, project, role="member")

        await _login_existing_user(client, email)
        response = await client.get("/dashboard/rozliczenia", follow_redirects=False)

        assert response.status_code == 200
        # Nie ma 403, strona sie laduje
        assert "403" not in response.text

    async def test_filter_by_project_ids_multi(self, client, db_session):
        """Multi-select project_ids -- filtruje do zaznaczonych projektow w allowed_set."""
        suffix = secrets.token_hex(4)
        project_a = await _create_project(db_session, f"Filter A {suffix}")
        project_b = await _create_project(db_session, f"Filter B {suffix}")

        email = f"filter_multi_{suffix}@example.com"
        user = User(
            email=email,
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()

        # owner w obu projektach
        for proj in (project_a, project_b):
            db_session.add(ProjectMember(project_id=proj.id, user_id=user.id, role="owner"))
        await db_session.flush()

        s_a = await _create_settlement(db_session, project_a, user, name=f"Sett A {suffix}")
        s_b = await _create_settlement(db_session, project_b, user, name=f"Sett B {suffix}")

        await _login_existing_user(client, email)
        # Filtruj tylko projekt A
        response = await client.get(
            f"/dashboard/rozliczenia?project_id={project_a.id}",
            follow_redirects=False,
        )

        assert response.status_code == 200
        text = response.text
        assert s_a.name in text
        assert s_b.name not in text

    async def test_filter_project_id_not_in_allowed_ignored(self, client, db_session):
        """User podaje project_id do ktorego nie ma dostepu -> nie widzi settlements z tego projektu."""
        suffix = secrets.token_hex(4)
        project_mine = await _create_project(db_session, f"Mine {suffix}")
        project_other = await _create_project(db_session, f"Other {suffix}")

        email = f"security_filter_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project_mine, role="owner")

        # creator dla drugiego projektu (inny user)
        other_email = f"other_owner_{suffix}@example.com"
        other_user = await _create_user_with_role(db_session, other_email, project_other, role="owner")

        s_mine = await _create_settlement(db_session, project_mine, user, name=f"Mine Sett {suffix}")
        _s_other = await _create_settlement(db_session, project_other, other_user, name=f"Other Sett {suffix}")

        await _login_existing_user(client, email)
        # Probuje podac project_id do ktorego nie ma dostepu
        response = await client.get(
            f"/dashboard/rozliczenia?project_id={project_other.id}",
            follow_redirects=False,
        )

        assert response.status_code == 200
        text = response.text
        # Moje settlements nadal widoczne (fallback do allowed_project_ids bo filter_project_ids puste po walidacji)
        assert s_mine.name in text
        # Obce niewidoczne
        assert f"Other Sett {suffix}" not in text

    async def test_filter_by_status(self, client, db_session):
        """status=draft -> tylko draft; status=sent -> tylko sent."""
        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"Status Filter {suffix}")

        email = f"status_filter_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s_draft = await _create_settlement(db_session, project, user, status="draft", name=f"Draft {suffix}")
        s_sent = await _create_settlement(db_session, project, user, status="sent", name=f"Sent {suffix}")

        await _login_existing_user(client, email)

        resp_draft = await client.get("/dashboard/rozliczenia?status=draft", follow_redirects=False)
        assert resp_draft.status_code == 200
        assert s_draft.name in resp_draft.text
        assert s_sent.name not in resp_draft.text

        resp_sent = await client.get("/dashboard/rozliczenia?status=sent", follow_redirects=False)
        assert resp_sent.status_code == 200
        assert s_sent.name in resp_sent.text
        assert s_draft.name not in resp_sent.text

    async def test_filter_by_date_range(self, client, db_session):
        """date_from/date_to filtruje po period_from/period_to."""
        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"Date Filter {suffix}")

        email = f"date_filter_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s_jan = await _create_settlement(
            db_session,
            project,
            user,
            name=f"Jan {suffix}",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
        )
        s_mar = await _create_settlement(
            db_session,
            project,
            user,
            name=f"Mar {suffix}",
            period_from=date(2026, 3, 1),
            period_to=date(2026, 3, 31),
        )

        await _login_existing_user(client, email)

        # Filtr tylko styczen
        resp = await client.get(
            "/dashboard/rozliczenia?date_from=2026-01-01&date_to=2026-01-31",
            follow_redirects=False,
        )
        assert resp.status_code == 200
        assert s_jan.name in resp.text
        assert s_mar.name not in resp.text

    async def test_pagination(self, client, db_session):
        """Tworzy >20 settlements, page 1 zwraca 20, page 2 zwraca resztę."""
        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"Paginate {suffix}")

        email = f"pagination_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        # Tworzenie 25 settlements
        for i in range(25):
            await _create_settlement(db_session, project, user, name=f"Rozl Pag {suffix} #{i:02d}")

        await _login_existing_user(client, email)

        resp1 = await client.get("/dashboard/rozliczenia?page=1", follow_redirects=False)
        assert resp1.status_code == 200
        # Strona 1 powinna byc dostepna
        resp2 = await client.get("/dashboard/rozliczenia?page=2", follow_redirects=False)
        assert resp2.status_code == 200
        # Druga strona zawiera inne settlements
        assert resp1.text != resp2.text


# ---------------------------------------------------------------------------
# Testy MCP tools -- list_settlements
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpListSettlements:
    """MCP tool: list_settlements."""

    async def test_requires_rozliczenia_read(self, db_session):
        """User bez rozliczenia:read -> ValueError z list_settlements."""
        from monolynx.mcp_server import list_settlements

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP NoRead {suffix}")
        email = f"mcp_noread_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="member")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Brak uprawnienia rozliczenia:read"),
        ):
            await list_settlements(ctx, project.slug)

    async def test_returns_settlements_with_permission(self, db_session):
        """User z rozliczenia:read (owner) -> lista settlements projektu."""
        from monolynx.mcp_server import list_settlements

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Read {suffix}")
        email = f"mcp_read_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s = await _create_settlement(db_session, project, user, name=f"MCP Sett {suffix}")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_settlements(ctx, project.slug)

        assert "settlements" in result
        ids = [s2["settlement_id"] for s2 in result["settlements"]]
        assert str(s.id) in ids

    async def test_status_filter(self, db_session):
        """status=draft filtruje tylko draft."""
        from monolynx.mcp_server import list_settlements

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Status {suffix}")
        email = f"mcp_status_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s_draft = await _create_settlement(db_session, project, user, status="draft", name=f"Draft {suffix}")
        _s_sent = await _create_settlement(db_session, project, user, status="sent", name=f"Sent {suffix}")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await list_settlements(ctx, project.slug, status="draft")

        ids = [s2["settlement_id"] for s2 in result["settlements"]]
        assert str(s_draft.id) in ids
        assert all(s2["status"] == "draft" for s2 in result["settlements"] if s2["settlement_id"] in ids)

    async def test_pagination(self, db_session):
        """page=2 zwraca inna strone niz page=1."""
        from monolynx.mcp_server import list_settlements

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Paginate {suffix}")
        email = f"mcp_pag_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        for i in range(25):
            await _create_settlement(db_session, project, user, name=f"MCP Pag {suffix} #{i:02d}")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result1 = await list_settlements(ctx, project.slug, page=1)
            result2 = await list_settlements(ctx, project.slug, page=2)

        assert result1["total"] == 25
        assert result1["total_pages"] == 2
        assert len(result1["settlements"]) == 20
        assert len(result2["settlements"]) == 5

        ids1 = {s["settlement_id"] for s in result1["settlements"]}
        ids2 = {s["settlement_id"] for s in result2["settlements"]}
        assert ids1.isdisjoint(ids2)


# ---------------------------------------------------------------------------
# Testy MCP tools -- get_settlement
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpGetSettlement:
    """MCP tool: get_settlement."""

    async def test_returns_full_detail(self, db_session):
        """Zawiera projects, tickets, attachments (metadane)."""
        from monolynx.mcp_server import get_settlement

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Detail {suffix}")
        email = f"mcp_detail_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s = await _create_settlement(db_session, project, user, name=f"Detail {suffix}")
        ticket = await _create_ticket(db_session, project)
        # Link ticket
        s_loaded = (await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == s.id))).scalar_one()
        s_loaded.tickets.append(ticket)
        await db_session.flush()

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await get_settlement(ctx, project.slug, str(s.id))

        assert result["settlement_id"] == str(s.id)
        assert "projects" in result
        assert "tickets" in result
        assert "attachments" in result
        assert len(result["projects"]) >= 1
        assert any(t["ticket_id"] == str(ticket.id) for t in result["tickets"])

    async def test_raises_for_nonexistent_settlement(self, db_session):
        """Nieistniejace settlement_id -> ValueError."""
        from monolynx.mcp_server import get_settlement

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP 404 {suffix}")
        email = f"mcp_404_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Rozliczenie nie istnieje"),
        ):
            await get_settlement(ctx, project.slug, str(uuid.uuid4()))


# ---------------------------------------------------------------------------
# Testy MCP tools -- create_settlement
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpCreateSettlement:
    """MCP tool: create_settlement."""

    async def test_creates_with_single_project(self, db_session):
        """Happy path -- create settlement w jednym projekcie."""
        from monolynx.mcp_server import create_settlement

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Create {suffix}")
        email = f"mcp_create_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_settlement(
                ctx,
                project_slug=project.slug,
                name=f"New Settlement {suffix}",
                period_from="2026-02-01",
                period_to="2026-02-28",
            )

        assert "settlement_id" in result
        assert result["status"] == "draft"
        assert result["name"] == f"New Settlement {suffix}"

    async def test_creates_with_additional_project_slugs(self, db_session):
        """Multi-project settlement (current + additional)."""
        from monolynx.mcp_server import create_settlement

        suffix = secrets.token_hex(4)
        project_a = await _create_project(db_session, f"MCP Multi A {suffix}")
        project_b = await _create_project(db_session, f"MCP Multi B {suffix}")
        email = f"mcp_multi_{suffix}@example.com"
        user = User(
            email=email,
            password_hash=hash_password("testpass123"),
            is_superuser=False,
        )
        db_session.add(user)
        await db_session.flush()
        # owner w obu projektach
        for proj in (project_a, project_b):
            db_session.add(ProjectMember(project_id=proj.id, user_id=user.id, role="owner"))
        await db_session.flush()

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await create_settlement(
                ctx,
                project_slug=project_a.slug,
                name=f"Multi {suffix}",
                period_from="2026-03-01",
                period_to="2026-03-31",
                additional_project_slugs=[project_b.slug],
            )

        assert len(result["projects"]) == 2
        project_slugs_in_result = {p["slug"] for p in result["projects"]}
        assert project_a.slug in project_slugs_in_result
        assert project_b.slug in project_slugs_in_result

    async def test_rejects_when_no_write_in_additional_project(self, db_session):
        """User ma rozliczenia:write w A ale nie w B; additional_project_slugs=['B'] -> ValueError."""
        from monolynx.mcp_server import create_settlement

        suffix = secrets.token_hex(4)
        project_a = await _create_project(db_session, f"MCP Write A {suffix}")
        project_b = await _create_project(db_session, f"MCP NoWrite B {suffix}")
        email = f"mcp_nowrite_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project_a, role="owner")
        # member w B - brak rozliczenia:write
        db_session.add(ProjectMember(project_id=project_b.id, user_id=user.id, role="member"))
        await db_session.flush()

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Brak uprawnienia rozliczenia:write"),
        ):
            await create_settlement(
                ctx,
                project_slug=project_a.slug,
                name=f"Blocked {suffix}",
                period_from="2026-04-01",
                period_to="2026-04-30",
                additional_project_slugs=[project_b.slug],
            )


# ---------------------------------------------------------------------------
# Testy MCP tools -- change_settlement_status
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpChangeSettlementStatus:
    """MCP tool: change_settlement_status."""

    async def test_valid_transition_draft_to_sent(self, db_session):
        """Happy path draft -> sent z timestampem."""
        from monolynx.mcp_server import change_settlement_status

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Status Trans {suffix}")
        email = f"mcp_trans_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")
        s = await _create_settlement(db_session, project, user, status="draft")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await change_settlement_status(ctx, project.slug, str(s.id), "sent")

        assert result["status"] == "sent"
        assert result["sent_at"] is not None

    async def test_invalid_transition_draft_to_paid(self, db_session):
        """draft -> paid -> ValueError."""
        from monolynx.mcp_server import change_settlement_status

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Bad Trans {suffix}")
        email = f"mcp_bad_trans_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")
        s = await _create_settlement(db_session, project, user, status="draft")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowe przejscie statusu"),
        ):
            await change_settlement_status(ctx, project.slug, str(s.id), "paid")

    async def test_requires_write_permission(self, db_session):
        """User bez rozliczenia:write -> blad (ValueError lub HTTPException)."""
        from monolynx.mcp_server import change_settlement_status

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP NoWrite Trans {suffix}")
        email_owner = f"mcp_owner_trans_{suffix}@example.com"
        email_member = f"mcp_member_trans_{suffix}@example.com"
        owner = await _create_user_with_role(db_session, email_owner, project, role="owner")
        member = await _create_user_with_role(db_session, email_member, project, role="member")

        s = await _create_settlement(db_session, project, owner, status="draft")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=member)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises((ValueError, Exception), match=r"Brak uprawnienia rozliczenia:write|403"),
        ):
            await change_settlement_status(ctx, project.slug, str(s.id), "sent")


# ---------------------------------------------------------------------------
# Testy MCP tools -- link/unlink ticket
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpLinkUnlinkTicket:
    """MCP tools: link_ticket_to_settlement / unlink_ticket_from_settlement."""

    async def test_link_ticket_happy_path(self, db_session):
        """Draft settlement + ticket z projektu settlement.projects -> linked."""
        from monolynx.mcp_server import link_ticket_to_settlement

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Link {suffix}")
        email = f"mcp_link_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s = await _create_settlement(db_session, project, user, status="draft")
        ticket = await _create_ticket(db_session, project)

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await link_ticket_to_settlement(ctx, project.slug, str(s.id), str(ticket.id))

        assert result["linked"] is True

    async def test_link_ticket_cross_project_rejected(self, db_session):
        """Settlement obejmuje projekt A. Ticket z projektu B. Link -> ValueError.
        Kluczowy test AC 5aec3174.
        """
        from monolynx.mcp_server import link_ticket_to_settlement

        suffix = secrets.token_hex(4)
        project_a = await _create_project(db_session, f"MCP CrossA {suffix}")
        project_b = await _create_project(db_session, f"MCP CrossB {suffix}")
        email = f"mcp_cross_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project_a, role="owner")
        db_session.add(ProjectMember(project_id=project_b.id, user_id=user.id, role="owner"))
        await db_session.flush()

        # Settlement tylko dla projektu A
        s = await _create_settlement(db_session, project_a, user, status="draft")
        # Ticket z projektu B
        ticket_b = await _create_ticket(db_session, project_b)

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Ticket nie nalezy do zadnego projektu"),
        ):
            await link_ticket_to_settlement(ctx, project_a.slug, str(s.id), str(ticket_b.id))

    async def test_link_ticket_to_sent_settlement_rejected(self, db_session):
        """Settlement w status sent -> nie mozna podpiac ticketu."""
        from monolynx.mcp_server import link_ticket_to_settlement

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP SentLink {suffix}")
        email = f"mcp_sent_link_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s = await _create_settlement(db_session, project, user, status="sent")
        ticket = await _create_ticket(db_session, project)

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="statusie draft"),
        ):
            await link_ticket_to_settlement(ctx, project.slug, str(s.id), str(ticket.id))

    async def test_unlink_ticket(self, db_session):
        """Happy path unlink."""
        from monolynx.mcp_server import unlink_ticket_from_settlement

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Unlink {suffix}")
        email = f"mcp_unlink_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        s = await _create_settlement(db_session, project, user, status="draft")
        ticket = await _create_ticket(db_session, project)

        # Podepnij ticket bezposrednio
        s_loaded = (await db_session.execute(select(Settlement).options(selectinload(Settlement.tickets)).where(Settlement.id == s.id))).scalar_one()
        s_loaded.tickets.append(ticket)
        await db_session.flush()

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
        ):
            result = await unlink_ticket_from_settlement(ctx, project.slug, str(s.id), str(ticket.id))

        assert result["unlinked"] is True


# ---------------------------------------------------------------------------
# Testy MCP tools -- zalaczniki
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestMcpSettlementAttachments:
    """MCP tools: list/add/get/delete settlement attachments."""

    async def test_add_attachment_base64_roundtrip(self, db_session):
        """POST add_settlement_attachment z file_base64 -> zapisane.
        GET get_settlement_attachment -> base64 decode = identyczne bytes.
        AC ebcb816a + f5e60b11.
        """
        from monolynx.mcp_server import add_settlement_attachment, get_settlement_attachment

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP Attach {suffix}")
        email = f"mcp_attach_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")
        s = await _create_settlement(db_session, project, user, status="draft")

        original_bytes = b"PDF_CONTENT_" + suffix.encode()
        encoded = base64.b64encode(original_bytes).decode("utf-8")

        fake_storage: dict[str, bytes] = {}

        def _fake_upload(path: str, data: bytes, ct: str) -> None:
            fake_storage[path] = data

        def _fake_get(path: str) -> tuple[bytes, str]:
            return fake_storage[path], "application/pdf"

        # Krok 1: add attachment
        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.minio_client.upload_object", _fake_upload),
        ):
            add_result = await add_settlement_attachment(
                ctx,
                project_slug=project.slug,
                settlement_id=str(s.id),
                file_base64=encoded,
                filename=f"faktura_{suffix}.pdf",
                category="invoice",
                state="draft",
                mime_type="application/pdf",
            )

        assert "attachment_id" in add_result
        attachment_id = add_result["attachment_id"]
        assert add_result["filename"] == f"faktura_{suffix}.pdf"
        assert add_result["size"] == len(original_bytes)

        # Krok 2: pobierz attachment przez MCP -- wymaga odswiezenia relacji w sesji
        # Buforujemy wartosci przed expire aby uniknac lazy load poza greenlet
        project_slug = project.slug
        settlement_id_str = str(s.id)

        # Odswiezamy settlement zeby session zobaczyla nowy attachment
        await db_session.refresh(s)

        mock_factory2 = _make_mock_factory(db_session)
        mock_verify2 = AsyncMock(return_value=user)
        ctx2 = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory2),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify2),
            patch("monolynx.services.minio_client.get_attachment", _fake_get),
        ):
            get_result = await get_settlement_attachment(
                ctx2,
                project_slug=project_slug,
                settlement_id=settlement_id_str,
                attachment_id=attachment_id,
            )

        assert get_result["attachment_id"] == attachment_id
        assert get_result["file_base64"] is not None
        decoded = base64.b64decode(get_result["file_base64"])
        assert decoded == original_bytes

    async def test_add_attachment_invalid_base64_rejected(self, db_session):
        """Zle base64 -> ValueError."""
        from monolynx.mcp_server import add_settlement_attachment

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP BadB64 {suffix}")
        email = f"mcp_badb64_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")
        s = await _create_settlement(db_session, project, user, status="draft")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="Nieprawidlowy format base64"),
        ):
            await add_settlement_attachment(
                ctx,
                project_slug=project.slug,
                settlement_id=str(s.id),
                file_base64="!!NOT_VALID_BASE64!!",
                filename="faktura.pdf",
                category="invoice",
                state="draft",
            )

    async def test_add_attachment_size_limit(self, db_session):
        """Plik > 200MB -> ValueError."""
        from monolynx.mcp_server import add_settlement_attachment

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP BigFile {suffix}")
        email = f"mcp_big_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")
        s = await _create_settlement(db_session, project, user, status="draft")

        # Kodujemy dużą ilosc bajtow
        big_bytes = b"X" * (201 * 1024 * 1024)
        encoded = base64.b64encode(big_bytes).decode("utf-8")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises(ValueError, match="za duzy"),
        ):
            await add_settlement_attachment(
                ctx,
                project_slug=project.slug,
                settlement_id=str(s.id),
                file_base64=encoded,
                filename="bigfile.pdf",
                category="invoice",
                state="draft",
            )

    async def test_list_attachments_no_bytes(self, db_session):
        """Response metadane (id, filename, size, category, state) bez file_base64."""
        from monolynx.mcp_server import add_settlement_attachment, list_settlement_attachments

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP ListAttach {suffix}")
        email = f"mcp_list_attach_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")
        s = await _create_settlement(db_session, project, user, status="draft")

        content = b"SMALL_CONTENT"
        encoded = base64.b64encode(content).decode("utf-8")

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        fake_storage: dict[str, bytes] = {}

        def _fake_upload(path: str, data: bytes, ct: str) -> None:
            fake_storage[path] = data

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            patch("monolynx.services.minio_client.upload_object", _fake_upload),
        ):
            await add_settlement_attachment(
                ctx,
                project_slug=project.slug,
                settlement_id=str(s.id),
                file_base64=encoded,
                filename=f"raport_{suffix}.pdf",
                category="report",
                state="draft",
                mime_type="application/pdf",
            )

        # Odswiezamy settlement zeby session zobaczyla nowy attachment
        project_slug = project.slug
        settlement_id_str = str(s.id)
        await db_session.refresh(s)

        mock_factory2 = _make_mock_factory(db_session)
        mock_verify2 = AsyncMock(return_value=user)
        ctx2 = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory2),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify2),
        ):
            list_result = await list_settlement_attachments(ctx2, project_slug, settlement_id_str)

        assert len(list_result) >= 1
        item = list_result[0]
        assert "attachment_id" in item
        assert "filename" in item
        assert "size" in item
        assert "category" in item
        assert "state" in item
        # Nie powinno byc file_base64 w liscie
        assert "file_base64" not in item

    async def test_delete_attachment_draft_only(self, db_session):
        """Attachment w settlement sent -> delete rejected."""
        from monolynx.mcp_server import delete_settlement_attachment

        suffix = secrets.token_hex(4)
        project = await _create_project(db_session, f"MCP DelAttach {suffix}")
        email = f"mcp_del_attach_{suffix}@example.com"
        user = await _create_user_with_role(db_session, email, project, role="owner")

        # Settlement juz w statusie sent
        s = await _create_settlement(db_session, project, user, status="sent")

        # Tworzymy attachment bezposrednio w DB (omijajac upload do MinIO)
        attachment = SettlementAttachment(
            settlement_id=s.id,
            category="faktura",
            state="sent",
            filename="test.pdf",
            storage_path=f"settlements/{s.id}/test.pdf",
            mime_type="application/pdf",
            size=100,
            uploaded_by_id=user.id,
        )
        db_session.add(attachment)
        await db_session.flush()

        mock_factory = _make_mock_factory(db_session)
        mock_verify = AsyncMock(return_value=user)
        ctx = _make_ctx()

        with (
            patch("monolynx.mcp_server.async_session_factory", mock_factory),
            patch("monolynx.mcp_server.verify_mcp_token", mock_verify),
            pytest.raises((ValueError, Exception), match=r"sent|draft"),
        ):
            await delete_settlement_attachment(ctx, project.slug, str(s.id), str(attachment.id))


# ---------------------------------------------------------------------------
# Weryfikacja rejestracji nowych MCP tools
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestMcpSettlementToolsRegistered:
    """Sprawdza ze 13 nowych settlement tools jest zarejestrowanych w MCP."""

    async def test_all_settlement_tools_registered(self):
        """13 nowych narzedzi settlements widocznych w list_tools()."""
        from monolynx.mcp_server import mcp

        expected_settlement_tools = [
            "list_settlements",
            "get_settlement",
            "create_settlement",
            "update_settlement",
            "delete_settlement",
            "change_settlement_status",
            "link_ticket_to_settlement",
            "unlink_ticket_from_settlement",
            "list_settlement_tickets",
            "list_settlement_attachments",
            "add_settlement_attachment",
            "get_settlement_attachment",
            "delete_settlement_attachment",
        ]

        tools = await mcp.list_tools()
        tool_names = [t.name for t in tools]

        for name in expected_settlement_tools:
            assert name in tool_names, f"Brak narzedzia MCP: {name}"

    async def test_total_tool_count_updated(self):
        """Laczna liczba narzedzi to poprzednia (77) + 13 nowych = 90.
        (Poprzednie 77 bylo przed MON-64; po dodaniu settlements: 90.)
        """
        from monolynx.mcp_server import mcp

        tools = await mcp.list_tools()
        # Jesli ta asercja padnie -- zaktualizuj EXPECTED_TOOLS w test_mcp_server.py
        assert len(tools) >= 90, (
            f"Oczekiwano >= 90 narzedzi MCP, znaleziono {len(tools)}. Zaktualizuj EXPECTED_TOOLS w tests/unit/test_mcp_server.py."
        )
