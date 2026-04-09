"""Testy pokrycia serwisu settlements.py -- MON-64.

Pokrywa brakujace linie:
- change_settlement_status: empty projects (99), inactive project skip (102), paid->sent branch (120-124)
- create_settlement: walidacje (164,166,170,174,184,190), retry-on-IntegrityError (218-222)
- update_settlement: cala funkcja (248-303)
- delete_settlement: cala funkcja (320-336)
- upload_settlement_attachment: walidacje (363,367,371,378,384,388,397)
- delete_settlement_attachment: cala funkcja (446-468)
- get_settlement_attachment_bytes: linia 481 (fallback mime_type)
"""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from monolynx.constants import (
    MAX_ATTACHMENTS_PER_SETTLEMENT,
    SETTLEMENT_ATTACHMENT_MAX_SIZE,
)
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.settlement import Settlement
from monolynx.models.settlement_attachment import SettlementAttachment
from monolynx.models.settlement_project import SettlementProject
from monolynx.models.user import User
from monolynx.services.auth import hash_password
from monolynx.services.settlements import (
    change_settlement_status,
    create_settlement,
    delete_settlement,
    delete_settlement_attachment,
    get_settlement_attachment_bytes,
    update_settlement,
    upload_settlement_attachment,
    validate_settlement_ticket_link,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _create_project(db_session, name: str, slug: str | None = None) -> Project:
    if slug is None:
        slug = f"cov-{secrets.token_hex(4)}"
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


async def _create_inactive_project(db_session, name: str) -> Project:
    project = Project(
        name=name,
        slug=f"cov-inactive-{secrets.token_hex(4)}",
        code=secrets.token_hex(3).upper(),
        api_key=secrets.token_urlsafe(32),
        is_active=False,
    )
    db_session.add(project)
    await db_session.flush()
    return project


async def _create_user_with_role(
    db_session,
    email: str,
    project: Project,
    role: str = "owner",
) -> User:
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=False,
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


async def _create_user_no_project(db_session, email: str) -> User:
    """Tworzy uzytkownika bez przypisania do projektu."""
    user = User(
        email=email,
        password_hash=hash_password("testpass123"),
        is_superuser=False,
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _make_settlement(
    db_session,
    creator: User,
    projects: list[Project],
    status: str = "draft",
    sent_at: datetime | None = None,
    paid_at: datetime | None = None,
    name: str | None = None,
) -> Settlement:
    """Tworzy settlement z podlinkowanymi projektami."""
    result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
    next_number = int(result.scalar_one()) + 1

    settlement = Settlement(
        number=next_number,
        name=name or f"Rozliczenie COV {next_number}",
        period_from=date(2026, 1, 1),
        period_to=date(2026, 1, 31),
        status=status,
        sent_at=sent_at,
        paid_at=paid_at,
        created_by_id=creator.id,
    )
    db_session.add(settlement)
    await db_session.flush()

    for project in projects:
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=project.id))
    await db_session.flush()

    return settlement


async def _get_settlement_with_all(db_session, settlement_id: uuid.UUID) -> Settlement:
    result = await db_session.execute(
        select(Settlement)
        .options(
            selectinload(Settlement.projects),
            selectinload(Settlement.attachments),
            selectinload(Settlement.tickets),
        )
        .where(Settlement.id == settlement_id)
    )
    return result.scalar_one()


async def _make_attachment(
    db_session,
    settlement: Settlement,
    user: User,
    storage_path: str | None = None,
    mime_type: str | None = None,
) -> SettlementAttachment:
    att = SettlementAttachment(
        settlement_id=settlement.id,
        category="invoice",
        state="draft",
        filename="faktura.pdf",
        storage_path=storage_path or f"settlements/{settlement.id}/2026/01/01/{uuid.uuid4().hex}.pdf",
        mime_type=mime_type,
        size=1024,
        uploaded_by_id=user.id,
    )
    db_session.add(att)
    await db_session.flush()
    return att


# ---------------------------------------------------------------------------
# change_settlement_status -- brakujace linie
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestChangeSettlementStatusMissingBranches:
    """Pokrywa linie 99, 102, 120-124."""

    async def test_empty_projects_raises_403(self, db_session):
        """Linia 99: settlement bez projektow -> HTTPException 403."""
        # Arrange: settlement BEZ projektow (pomijamy _make_settlement)
        project = await _create_project(db_session, "EmptyProj", "covcs-empty-01")
        creator = await _create_user_with_role(db_session, "covcs-empty01@test.com", project, "owner")

        result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
        next_number = int(result.scalar_one()) + 1
        settlement = Settlement(
            number=next_number,
            name="Settlement bez projektow",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            status="draft",
            created_by_id=creator.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        # NIE dodajemy SettlementProject -- brak projektow

        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Act & Assert
        with pytest.raises(HTTPException) as exc_info:
            await change_settlement_status(db_session, fresh, creator.id, "sent")

        assert exc_info.value.status_code == 403
        assert "aktywnych projektow" in exc_info.value.detail

    async def test_inactive_project_skipped_in_permission_check(self, db_session):
        """Linia 102: nieaktywny projekt w settlement jest pomijany przy sprawdzaniu uprawnien.

        Settlement ma dwa projekty: aktywny (owner ma write) i nieaktywny.
        Zmiana statusu powinna przejsc pomimo nieaktywnego projektu.
        """
        # Arrange
        active_proj = await _create_project(db_session, "ActiveProj", "covcs-active-01")
        inactive_proj = await _create_inactive_project(db_session, "InactiveProj")

        owner = await _create_user_with_role(db_session, "covcs-inactive01@test.com", active_proj, "owner")

        # settlement z dwoma projektami: aktywnym i nieaktywnym
        result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
        next_number = int(result.scalar_one()) + 1
        settlement = Settlement(
            number=next_number,
            name="Settlement z inactive projektem",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            status="draft",
            created_by_id=owner.id,
        )
        db_session.add(settlement)
        await db_session.flush()

        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=active_proj.id))
        db_session.add(SettlementProject(settlement_id=settlement.id, project_id=inactive_proj.id))
        await db_session.flush()

        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Act: zmiana powinna przejsc -- inactive projekt jest pomijany (continue)
        updated = await change_settlement_status(db_session, fresh, owner.id, "sent")

        # Assert
        assert updated.status == "sent"

    async def test_paid_to_sent_clears_paid_at_branch(self, db_session):
        """Linie 120-124: paid -> sent: paid_at = None, sent_at zachowane."""
        # Arrange
        project = await _create_project(db_session, "PaidSentBranch", "covcs-paidsent-01")
        owner = await _create_user_with_role(db_session, "covcs-paidsent01@test.com", project, "owner")

        known_sent = datetime(2026, 2, 1, 8, 0, 0, tzinfo=UTC)
        known_paid = datetime(2026, 2, 20, 10, 0, 0, tzinfo=UTC)
        settlement = await _make_settlement(
            db_session,
            owner,
            [project],
            status="paid",
            sent_at=known_sent,
            paid_at=known_paid,
        )
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Act
        updated = await change_settlement_status(db_session, fresh, owner.id, "sent")

        # Assert: paid_at wyczyszczone, sent_at zachowane
        assert updated.status == "sent"
        assert updated.paid_at is None
        assert updated.sent_at == known_sent


# ---------------------------------------------------------------------------
# create_settlement -- brakujace linie walidacji
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCreateSettlementValidation:
    """Pokrywa linie 164, 166, 170, 174, 184, 190."""

    async def test_empty_name_raises_value_error(self, db_session):
        """Linia 164: name = whitespace -> ValueError."""
        project = await _create_project(db_session, "CreateValEmpty", "covcr-empty-01")
        owner = await _create_user_with_role(db_session, "covcr-emptyname01@test.com", project, "owner")

        with pytest.raises(ValueError, match="Nazwa rozliczenia nie moze byc pusta"):
            await create_settlement(
                db_session,
                owner.id,
                name="   ",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project.id],
            )

    async def test_name_too_long_raises_value_error(self, db_session):
        """Linia 166: name > 200 znakow -> ValueError."""
        project = await _create_project(db_session, "CreateValLong", "covcr-long-01")
        owner = await _create_user_with_role(db_session, "covcr-longname01@test.com", project, "owner")

        long_name = "A" * 201

        with pytest.raises(ValueError, match="Nazwa rozliczenia nie moze przekraczac 200 znakow"):
            await create_settlement(
                db_session,
                owner.id,
                name=long_name,
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project.id],
            )

    async def test_period_from_after_period_to_raises_value_error(self, db_session):
        """Linia 170: period_from > period_to -> ValueError."""
        project = await _create_project(db_session, "CreateValDates", "covcr-dates-01")
        owner = await _create_user_with_role(db_session, "covcr-dates01@test.com", project, "owner")

        with pytest.raises(ValueError, match="Data poczatku okresu nie moze byc pozniejsza niz data konca"):
            await create_settlement(
                db_session,
                owner.id,
                name="Test Period",
                period_from=date(2026, 2, 1),
                period_to=date(2026, 1, 1),
                project_ids=[project.id],
            )

    async def test_empty_project_ids_raises_value_error(self, db_session):
        """Linia 174: project_ids = [] -> ValueError."""
        project = await _create_project(db_session, "CreateValNoProj", "covcr-noproj-01")
        owner = await _create_user_with_role(db_session, "covcr-noproj01@test.com", project, "owner")

        with pytest.raises(ValueError, match="Rozliczenie musi byc przypisane do co najmniej jednego projektu"):
            await create_settlement(
                db_session,
                owner.id,
                name="Test No Projects",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[],
            )

    async def test_nonexistent_project_id_raises_value_error(self, db_session):
        """Linia 184: projekt nie istnieje -> ValueError z missing set."""
        project = await _create_project(db_session, "CreateValMissing", "covcr-missing-01")
        owner = await _create_user_with_role(db_session, "covcr-missing01@test.com", project, "owner")

        fake_id = uuid.uuid4()

        with pytest.raises(ValueError, match="Projekty nie istnieja lub sa nieaktywne"):
            await create_settlement(
                db_session,
                owner.id,
                name="Test Missing Project",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[fake_id],
            )

    async def test_inactive_project_raises_value_error(self, db_session):
        """Linia 184: nieaktywny projekt -> ValueError."""
        inactive = await _create_inactive_project(db_session, "InactiveForCreate")
        active = await _create_project(db_session, "ActiveForCreate", "covcr-inactive-01")
        owner = await _create_user_with_role(db_session, "covcr-inactive01@test.com", active, "owner")

        with pytest.raises(ValueError, match="Projekty nie istnieja lub sa nieaktywne"):
            await create_settlement(
                db_session,
                owner.id,
                name="Test Inactive Project",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[inactive.id],
            )

    async def test_no_write_permission_raises_403(self, db_session):
        """Linia 190: user bez rozliczenia:write -> HTTPException 403."""
        project = await _create_project(db_session, "CreateValNoWrite", "covcr-nowrite-01")
        # member nie ma rozliczenia:write
        member = await _create_user_with_role(db_session, "covcr-nowrite01@test.com", project, "member")

        with pytest.raises(HTTPException) as exc_info:
            await create_settlement(
                db_session,
                member.id,
                name="Test No Write",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project.id],
            )

        assert exc_info.value.status_code == 403
        assert "rozliczenia:write" in exc_info.value.detail

    async def test_happy_path_creates_settlement(self, db_session):
        """Happy path create_settlement -- zwraca Settlement z prawidlowymi danymi."""
        project = await _create_project(db_session, "CreateHappy", "covcr-happy-01")
        owner = await _create_user_with_role(db_session, "covcr-happy01@test.com", project, "owner")

        result = await create_settlement(
            db_session,
            owner.id,
            name="Rozliczenie Happy",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            project_ids=[project.id],
            notes="Notatka testowa",
        )

        assert result.id is not None
        assert result.name == "Rozliczenie Happy"
        assert result.status == "draft"
        assert result.created_by_id == owner.id


# ---------------------------------------------------------------------------
# create_settlement -- retry-on-IntegrityError (linie 218-222)
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCreateSettlementRetryOnIntegrityError:
    """Pokrywa linie 218-222: retry loop przy race condition na unikalnym numerze.

    Testy sa oznaczone @pytest.mark.unit bo wymagaja pelnego mockowania sesji --
    prawdziwy db.rollback() w architekturze outer-transaction niszczy dane testowe.
    """

    async def test_retries_on_integrity_error_then_succeeds(self):
        """Retry loop: pierwszy attempt rzuca IntegrityError, drugi sukces -- linie 218-222."""
        import uuid as _uuid

        # Tworzymy w pełni zamockowana sesje
        mock_db = AsyncMock()
        user_id = _uuid.uuid4()
        project_id = _uuid.uuid4()

        # Mockujemy check_permission: zawsze True
        get_next_call = 0

        async def mock_get_next(db):
            nonlocal get_next_call
            get_next_call += 1
            return 42 if get_next_call == 1 else 99  # raz kolizja (42), potem wolny (99)

        # flush: pierwsza proba rzuca IntegrityError, nastepne OK
        flush_call_count = 0

        async def mock_flush(*args, **kwargs):
            nonlocal flush_call_count
            flush_call_count += 1
            if flush_call_count == 1:
                raise IntegrityError(
                    statement="INSERT INTO settlements ...",
                    params={},
                    orig=Exception("duplicate key value violates unique constraint"),
                )
            # inne flushe OK

        mock_db.flush = mock_flush
        mock_db.add = MagicMock()
        mock_db.rollback = AsyncMock()
        mock_db.commit = AsyncMock()
        mock_db.refresh = AsyncMock()

        # Mock dla select(Project).where(...) -- zwraca project

        fake_project = MagicMock()
        fake_project.id = project_id

        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [fake_project]
        mock_db.execute = AsyncMock(return_value=mock_result)

        with (
            patch("monolynx.services.settlements.get_next_settlement_number", side_effect=mock_get_next),
            patch("monolynx.services.settlements.check_permission", return_value=True),
        ):
            await create_settlement(
                mock_db,
                user_id,
                name="Retry Settlement",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project_id],
            )

        # Rollback zostal wywolany raz (po pierwszej IntegrityError)
        mock_db.rollback.assert_called_once()
        # get_next wywolany 2 razy
        assert get_next_call == 2
        # Commit zostal wywolany (sukces na drugiej probie)
        mock_db.commit.assert_called_once()

    async def test_raises_after_three_integrity_errors(self):
        """Trzy IntegrityError z rzedu -> IntegrityError propaguje po attempt==2 -- linia 220-221."""
        import uuid as _uuid

        mock_db = AsyncMock()
        user_id = _uuid.uuid4()
        project_id = _uuid.uuid4()

        fake_project = MagicMock()
        fake_project.id = project_id
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = [fake_project]
        mock_db.execute = AsyncMock(return_value=mock_result)

        async def always_integrity_error(*args, **kwargs):
            raise IntegrityError(
                statement="INSERT INTO settlements ...",
                params={},
                orig=Exception("duplicate key value violates unique constraint"),
            )

        mock_db.flush = always_integrity_error
        mock_db.add = MagicMock()
        mock_db.rollback = AsyncMock()

        async def mock_get_next_always(db):
            return 42  # zawsze ten sam numer

        with (
            patch("monolynx.services.settlements.get_next_settlement_number", side_effect=mock_get_next_always),
            patch("monolynx.services.settlements.check_permission", return_value=True),
            pytest.raises(IntegrityError),
        ):
            await create_settlement(
                mock_db,
                user_id,
                name="Always Collision",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project_id],
            )

        # Rollback wywolany 3 razy (po kazdej probie), ale przy attempt==2 IntegrityError propaguje
        assert mock_db.rollback.call_count == 3


# ---------------------------------------------------------------------------
# update_settlement -- cala funkcja (linie 248-303)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUpdateSettlement:
    """Pokrywa linie 248-303: update_settlement."""

    async def test_happy_path_updates_all_fields(self, db_session):
        """Happy path: aktualizuje name/period/notes i zastepuje M2M projects."""
        project_a = await _create_project(db_session, "UpdateHappyA", "covupd-hpa-01")
        project_b = await _create_project(db_session, "UpdateHappyB", "covupd-hpb-01")
        owner = await _create_user_with_role(db_session, "covupd-happy01@test.com", project_a, "owner")
        await _create_user_with_role(db_session, "covupd-happy01-b@test.com", project_b, "owner")

        # Dodaj owner do projektu B
        member_b = ProjectMember(project_id=project_b.id, user_id=owner.id, role="owner")
        db_session.add(member_b)
        await db_session.flush()

        settlement = await _make_settlement(db_session, owner, [project_a], status="draft")
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Act: zmien na projekt_b + nowa nazwa
        updated = await update_settlement(
            db_session,
            fresh,
            owner.id,
            name="  Zaktualizowana nazwa  ",
            period_from=date(2026, 2, 1),
            period_to=date(2026, 2, 28),
            project_ids=[project_b.id],
            notes="Nowe notatki",
        )

        assert updated.name == "Zaktualizowana nazwa"  # strip() zastosowany
        assert updated.period_from == date(2026, 2, 1)
        assert updated.period_to == date(2026, 2, 28)
        assert updated.notes == "Nowe notatki"
        assert updated.status == "draft"

    async def test_non_draft_status_raises_value_error(self, db_session):
        """Linia 248-249: status != draft -> ValueError."""
        project = await _create_project(db_session, "UpdateNonDraft", "covupd-nondraft-01")
        owner = await _create_user_with_role(db_session, "covupd-nondraft01@test.com", project, "owner")
        settlement = await _make_settlement(
            db_session,
            owner,
            [project],
            status="sent",
            sent_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Rozliczenie mozna edytowac tylko w statusie draft"):
            await update_settlement(
                db_session,
                fresh,
                owner.id,
                name="New Name",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project.id],
            )

    async def test_empty_name_raises_value_error(self, db_session):
        """Walidacja name w update."""
        project = await _create_project(db_session, "UpdateEmptyName", "covupd-emname-01")
        owner = await _create_user_with_role(db_session, "covupd-emname01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Nazwa rozliczenia nie moze byc pusta"):
            await update_settlement(
                db_session,
                fresh,
                owner.id,
                name="",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project.id],
            )

    async def test_name_too_long_raises_value_error(self, db_session):
        """Walidacja name > 200 w update."""
        project = await _create_project(db_session, "UpdateLongName", "covupd-longname-01")
        owner = await _create_user_with_role(db_session, "covupd-longname01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Nazwa rozliczenia nie moze przekraczac 200 znakow"):
            await update_settlement(
                db_session,
                fresh,
                owner.id,
                name="B" * 201,
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project.id],
            )

    async def test_invalid_period_raises_value_error(self, db_session):
        """Walidacja dat w update."""
        project = await _create_project(db_session, "UpdateBadDates", "covupd-baddates-01")
        owner = await _create_user_with_role(db_session, "covupd-baddates01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Data poczatku okresu nie moze byc pozniejsza niz data konca"):
            await update_settlement(
                db_session,
                fresh,
                owner.id,
                name="Test",
                period_from=date(2026, 3, 1),
                period_to=date(2026, 2, 1),
                project_ids=[project.id],
            )

    async def test_empty_project_ids_raises_value_error(self, db_session):
        """Walidacja project_ids = [] w update."""
        project = await _create_project(db_session, "UpdateNoProj", "covupd-noproj-01")
        owner = await _create_user_with_role(db_session, "covupd-noproj01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Rozliczenie musi byc przypisane do co najmniej jednego projektu"):
            await update_settlement(
                db_session,
                fresh,
                owner.id,
                name="Test",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[],
            )

    async def test_nonexistent_project_raises_value_error(self, db_session):
        """Walidacja nieistniejacego project_id w update."""
        project = await _create_project(db_session, "UpdateMissing", "covupd-missing-01")
        owner = await _create_user_with_role(db_session, "covupd-missing01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Projekty nie istnieja lub sa nieaktywne"):
            await update_settlement(
                db_session,
                fresh,
                owner.id,
                name="Test",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[uuid.uuid4()],
            )

    async def test_no_write_permission_raises_403(self, db_session):
        """Brak rozliczenia:write w nowym projekcie -> HTTPException 403."""
        project_a = await _create_project(db_session, "UpdateNoWriteA", "covupd-nowrite-01")
        project_b = await _create_project(db_session, "UpdateNoWriteB", "covupd-nowrite-02")
        owner = await _create_user_with_role(db_session, "covupd-nowrite01@test.com", project_a, "owner")
        # owner NIE ma w projekcie B
        settlement = await _make_settlement(db_session, owner, [project_a])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(HTTPException) as exc_info:
            await update_settlement(
                db_session,
                fresh,
                owner.id,
                name="Test",
                period_from=date(2026, 1, 1),
                period_to=date(2026, 1, 31),
                project_ids=[project_b.id],
            )

        assert exc_info.value.status_code == 403

    async def test_replaces_m2m_projects(self, db_session):
        """update_settlement zastepuje M2M -- stary projekt usuniety, nowy dodany."""
        project_old = await _create_project(db_session, "UpdateM2MOld", "covupd-m2m-old-01")
        project_new = await _create_project(db_session, "UpdateM2MNew", "covupd-m2m-new-01")
        owner = await _create_user_with_role(db_session, "covupd-m2m01@test.com", project_old, "owner")

        # Dodaj owner do project_new
        member_new = ProjectMember(project_id=project_new.id, user_id=owner.id, role="owner")
        db_session.add(member_new)
        await db_session.flush()

        settlement = await _make_settlement(db_session, owner, [project_old])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        updated = await update_settlement(
            db_session,
            fresh,
            owner.id,
            name="Updated M2M",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            project_ids=[project_new.id],
        )

        fresh_after = await _get_settlement_with_all(db_session, updated.id)
        project_ids_after = {p.id for p in fresh_after.projects}
        assert project_new.id in project_ids_after
        assert project_old.id not in project_ids_after


# ---------------------------------------------------------------------------
# delete_settlement -- cala funkcja (linie 320-336)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteSettlement:
    """Pokrywa linie 320-336: delete_settlement."""

    async def test_happy_path_soft_deletes(self, db_session):
        """Happy path: draft settlement -> is_active = False."""
        project = await _create_project(db_session, "DeleteHappy", "covdel-happy-01")
        owner = await _create_user_with_role(db_session, "covdel-happy01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Act
        await delete_settlement(db_session, fresh, owner.id)

        # Assert: is_active = False po commicie
        result = await db_session.execute(select(Settlement).where(Settlement.id == settlement.id))
        deleted = result.scalar_one()
        assert deleted.is_active is False

    async def test_non_draft_raises_value_error(self, db_session):
        """Linia 320-321: status != draft -> ValueError."""
        project = await _create_project(db_session, "DeleteNonDraft", "covdel-nondraft-01")
        owner = await _create_user_with_role(db_session, "covdel-nondraft01@test.com", project, "owner")
        settlement = await _make_settlement(
            db_session,
            owner,
            [project],
            status="sent",
            sent_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Rozliczenie mozna usunac tylko w statusie draft"):
            await delete_settlement(db_session, fresh, owner.id)

    async def test_empty_projects_raises_403(self, db_session):
        """Linia 324-325: settlement bez projektow -> HTTPException 403."""
        project = await _create_project(db_session, "DeleteNoProj", "covdel-noproj-01")
        creator = await _create_user_with_role(db_session, "covdel-noproj01@test.com", project, "owner")

        result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
        next_number = int(result.scalar_one()) + 1
        settlement = Settlement(
            number=next_number,
            name="Settlement bez projektow",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            status="draft",
            created_by_id=creator.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        # Bez SettlementProject

        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(HTTPException) as exc_info:
            await delete_settlement(db_session, fresh, creator.id)

        assert exc_info.value.status_code == 403

    async def test_no_delete_permission_raises_403(self, db_session):
        """Linia 327-332: brak rozliczenia:delete -> HTTPException 403."""
        project = await _create_project(db_session, "DeleteNoPerm", "covdel-noperm-01")
        # member nie ma rozliczenia:delete
        member = await _create_user_with_role(db_session, "covdel-noperm01@test.com", project, "member")
        owner = await _create_user_with_role(db_session, "covdel-noperm01-owner@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(HTTPException) as exc_info:
            await delete_settlement(db_session, fresh, member.id)

        assert exc_info.value.status_code == 403
        assert "rozliczenia:delete" in exc_info.value.detail


# ---------------------------------------------------------------------------
# upload_settlement_attachment -- walidacje (linie 363, 367, 371, 378, 384, 388, 397)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUploadSettlementAttachment:
    """Pokrywa linie 363-397: upload_settlement_attachment."""

    async def test_non_draft_raises_value_error(self, db_session):
        """Linia 363: status != draft -> ValueError."""
        project = await _create_project(db_session, "UploadNonDraft", "covatt-nondraft-01")
        owner = await _create_user_with_role(db_session, "covatt-nondraft01@test.com", project, "owner")
        settlement = await _make_settlement(
            db_session,
            owner,
            [project],
            status="sent",
            sent_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Nie mozna dodawac zalacznikow do rozliczenia w statusie"):
            await upload_settlement_attachment(
                db_session,
                fresh,
                owner.id,
                file_bytes=b"test",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="draft",
            )

    async def test_empty_projects_raises_403(self, db_session):
        """Linia 367: settlement bez projektow -> HTTPException 403."""
        project = await _create_project(db_session, "UploadNoProj", "covatt-noproj-01")
        creator = await _create_user_with_role(db_session, "covatt-noproj01@test.com", project, "owner")

        result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
        next_number = int(result.scalar_one()) + 1
        settlement = Settlement(
            number=next_number,
            name="Upload bez projektow",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            status="draft",
            created_by_id=creator.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        # Bez SettlementProject

        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(HTTPException) as exc_info:
            await upload_settlement_attachment(
                db_session,
                fresh,
                creator.id,
                file_bytes=b"test",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="draft",
            )

        assert exc_info.value.status_code == 403

    async def test_no_write_permission_raises_403(self, db_session):
        """Linia 371: brak rozliczenia:write -> HTTPException 403."""
        project = await _create_project(db_session, "UploadNoWrite", "covatt-nowrite-01")
        member = await _create_user_with_role(db_session, "covatt-nowrite01@test.com", project, "member")
        owner = await _create_user_with_role(db_session, "covatt-nowrite01-owner@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(HTTPException) as exc_info:
            await upload_settlement_attachment(
                db_session,
                fresh,
                member.id,
                file_bytes=b"test",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="draft",
            )

        assert exc_info.value.status_code == 403

    async def test_invalid_category_raises_value_error(self, db_session):
        """Linia 378: category not in SETTLEMENT_CATEGORIES -> ValueError."""
        project = await _create_project(db_session, "UploadBadCat", "covatt-badcat-01")
        owner = await _create_user_with_role(db_session, "covatt-badcat01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Nieprawidlowa kategoria"):
            await upload_settlement_attachment(
                db_session,
                fresh,
                owner.id,
                file_bytes=b"test",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invalid_category",
                state="draft",
            )

    async def test_file_too_large_raises_value_error(self, db_session):
        """Linia 384: file > SETTLEMENT_ATTACHMENT_MAX_SIZE -> ValueError."""
        project = await _create_project(db_session, "UploadTooBig", "covatt-toobig-01")
        owner = await _create_user_with_role(db_session, "covatt-toobig01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        big_file = b"x" * (SETTLEMENT_ATTACHMENT_MAX_SIZE + 1)

        with pytest.raises(ValueError, match="Plik za duzy"):
            await upload_settlement_attachment(
                db_session,
                fresh,
                owner.id,
                file_bytes=big_file,
                filename="big.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="draft",
            )

    async def test_attachment_limit_exceeded_raises_value_error(self, db_session):
        """Linia 388: attachments >= MAX_ATTACHMENTS_PER_SETTLEMENT -> ValueError."""
        project = await _create_project(db_session, "UploadLimit", "covatt-limit-01")
        owner = await _create_user_with_role(db_session, "covatt-limit01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])

        # Dodaj MAX_ATTACHMENTS_PER_SETTLEMENT zalacznikow bezposrednio
        for i in range(MAX_ATTACHMENTS_PER_SETTLEMENT):
            att = SettlementAttachment(
                settlement_id=settlement.id,
                category="invoice",
                state="draft",
                filename=f"att_{i}.pdf",
                storage_path=f"settlements/{settlement.id}/2026/01/01/att_{i}.pdf",
                mime_type="application/pdf",
                size=100,
                uploaded_by_id=owner.id,
            )
            db_session.add(att)
        await db_session.flush()

        fresh = await _get_settlement_with_all(db_session, settlement.id)
        assert len(fresh.attachments) == MAX_ATTACHMENTS_PER_SETTLEMENT

        with pytest.raises(ValueError, match="Osiagnieto limit"):
            await upload_settlement_attachment(
                db_session,
                fresh,
                owner.id,
                file_bytes=b"test",
                filename="extra.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="draft",
            )

    async def test_invalid_extension_raises_value_error(self, db_session):
        """Linia 397: ext not in SETTLEMENT_ALLOWED_EXT -> ValueError."""
        project = await _create_project(db_session, "UploadBadExt", "covatt-badext-01")
        owner = await _create_user_with_role(db_session, "covatt-badext01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Niedozwolone rozszerzenie pliku"):
            await upload_settlement_attachment(
                db_session,
                fresh,
                owner.id,
                file_bytes=b"test",
                filename="malware.exe",
                mime_type="application/octet-stream",
                category="invoice",
                state="draft",
            )

    async def test_happy_path_uploads_and_saves(self, db_session):
        """Happy path: upload przechodzi, zwraca SettlementAttachment z DB."""
        project = await _create_project(db_session, "UploadHappy", "covatt-happy-01")
        owner = await _create_user_with_role(db_session, "covatt-happy01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with patch("monolynx.services.settlements.minio_client.upload_object") as mock_upload:
            mock_upload.return_value = None

            result = await upload_settlement_attachment(
                db_session,
                fresh,
                owner.id,
                file_bytes=b"PDF content",
                filename="faktura.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="draft",
            )

        assert result.id is not None
        assert result.filename == "faktura.pdf"
        assert result.category == "invoice"
        assert result.size == len(b"PDF content")
        assert result.uploaded_by_id == owner.id
        mock_upload.assert_called_once()


# ---------------------------------------------------------------------------
# delete_settlement_attachment -- cala funkcja (linie 446-468)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDeleteSettlementAttachment:
    """Pokrywa linie 446-468: delete_settlement_attachment."""

    async def _get_attachment_with_settlement(self, db_session, attachment_id: uuid.UUID) -> SettlementAttachment:
        result = await db_session.execute(
            select(SettlementAttachment)
            .options(
                selectinload(SettlementAttachment.settlement).selectinload(Settlement.projects),
            )
            .where(SettlementAttachment.id == attachment_id)
        )
        return result.scalar_one()

    async def test_non_draft_settlement_raises_value_error(self, db_session):
        """Linia 442-443: settlement.status != draft -> ValueError (juz pokryte przez testy existujace? -- sprawdzamy)."""
        project = await _create_project(db_session, "DelAttNonDraft", "covdelatt-nondraft-01")
        owner = await _create_user_with_role(db_session, "covdelatt-nondraft01@test.com", project, "owner")
        settlement = await _make_settlement(
            db_session,
            owner,
            [project],
            status="sent",
            sent_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        att = await _make_attachment(db_session, settlement, owner)

        att_fresh = await self._get_attachment_with_settlement(db_session, att.id)

        with pytest.raises(ValueError, match="Nie mozna usuwac zalacznikow z rozliczenia w statusie"):
            await delete_settlement_attachment(db_session, att_fresh, owner.id)

    async def test_empty_projects_raises_403(self, db_session):
        """Linia 446-447: settlement bez projektow -> HTTPException 403."""
        project = await _create_project(db_session, "DelAttNoProj", "covdelatt-noproj-01")
        creator = await _create_user_with_role(db_session, "covdelatt-noproj01@test.com", project, "owner")

        result = await db_session.execute(select(func.coalesce(func.max(Settlement.number), 0)))
        next_number = int(result.scalar_one()) + 1
        settlement = Settlement(
            number=next_number,
            name="DelAtt bez projektow",
            period_from=date(2026, 1, 1),
            period_to=date(2026, 1, 31),
            status="draft",
            created_by_id=creator.id,
        )
        db_session.add(settlement)
        await db_session.flush()
        # Bez SettlementProject

        att = SettlementAttachment(
            settlement_id=settlement.id,
            category="invoice",
            state="draft",
            filename="test.pdf",
            storage_path=f"settlements/{settlement.id}/2026/01/01/test.pdf",
            mime_type=None,
            size=100,
            uploaded_by_id=creator.id,
        )
        db_session.add(att)
        await db_session.flush()

        att_fresh = await self._get_attachment_with_settlement(db_session, att.id)

        with pytest.raises(HTTPException) as exc_info:
            await delete_settlement_attachment(db_session, att_fresh, creator.id)

        assert exc_info.value.status_code == 403

    async def test_no_delete_permission_raises_403(self, db_session):
        """Linia 449-454: brak rozliczenia:delete -> HTTPException 403."""
        project = await _create_project(db_session, "DelAttNoPerm", "covdelatt-noperm-01")
        member = await _create_user_with_role(db_session, "covdelatt-noperm01@test.com", project, "member")
        owner = await _create_user_with_role(db_session, "covdelatt-noperm01-owner@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        att = await _make_attachment(db_session, settlement, owner)

        att_fresh = await self._get_attachment_with_settlement(db_session, att.id)

        with pytest.raises(HTTPException) as exc_info:
            await delete_settlement_attachment(db_session, att_fresh, member.id)

        assert exc_info.value.status_code == 403

    async def test_happy_path_deletes_from_minio_and_db(self, db_session):
        """Linie 456-468: happy path -- MinIO delete + db.delete + flush."""
        project = await _create_project(db_session, "DelAttHappy", "covdelatt-happy-01")
        owner = await _create_user_with_role(db_session, "covdelatt-happy01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        att = await _make_attachment(db_session, settlement, owner, storage_path="settlements/test/happy.pdf")
        att_id = att.id

        att_fresh = await self._get_attachment_with_settlement(db_session, att_id)

        with patch("monolynx.services.settlements.minio_client.delete_object") as mock_delete:
            mock_delete.return_value = None

            await delete_settlement_attachment(db_session, att_fresh, owner.id)

        # Verify: MinIO delete wywolany
        mock_delete.assert_called_once_with("settlements/test/happy.pdf")

        # Verify: rekord usuniety z DB (flush)
        result = await db_session.execute(select(SettlementAttachment).where(SettlementAttachment.id == att_id))
        assert result.scalar_one_or_none() is None

    async def test_minio_exception_logs_warning_and_continues(self, db_session):
        """Linia 463-464: MinIO rzuca wyjatek -- logger.warning + kontynuacja (db.delete)."""
        project = await _create_project(db_session, "DelAttMinioFail", "covdelatt-minio-01")
        owner = await _create_user_with_role(db_session, "covdelatt-minio01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        att = await _make_attachment(db_session, settlement, owner, storage_path="settlements/test/minio-fail.pdf")
        att_id = att.id

        att_fresh = await self._get_attachment_with_settlement(db_session, att_id)

        with (
            patch("monolynx.services.settlements.minio_client.delete_object", side_effect=Exception("MinIO down")) as mock_delete,
            patch("monolynx.services.settlements.logger") as mock_logger,
        ):
            # Funkcja NIE powinna rzucac wyjatku
            await delete_settlement_attachment(db_session, att_fresh, owner.id)

        # MinIO delete zostal wywolany (rzucil wyjatek)
        mock_delete.assert_called_once()

        # Logger.warning zostal wywolany
        mock_logger.warning.assert_called_once()

        # Rekord usuniety z DB mimo blednego MinIO
        result = await db_session.execute(select(SettlementAttachment).where(SettlementAttachment.id == att_id))
        assert result.scalar_one_or_none() is None


# ---------------------------------------------------------------------------
# get_settlement_attachment_bytes -- linia 481
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSettlementAttachmentBytes:
    """Pokrywa linie 478-482: get_settlement_attachment_bytes."""

    def test_uses_attachment_mime_type_when_minio_returns_generic(self):
        """Linia 481: content_type == 'application/octet-stream' AND mime_type is set -> uzywamy mime_type."""
        # Arrange
        attachment = MagicMock(spec=SettlementAttachment)
        attachment.storage_path = "settlements/test/file.pdf"
        attachment.mime_type = "application/pdf"  # prawdziwy mime_type

        with patch(
            "monolynx.services.settlements.minio_client.get_attachment",
            return_value=(b"PDF bytes", "application/octet-stream"),
        ):
            file_bytes, content_type = get_settlement_attachment_bytes(attachment)

        # Powinno zamienic generyczny typ na attachment.mime_type
        assert content_type == "application/pdf"
        assert file_bytes == b"PDF bytes"

    def test_keeps_specific_content_type_from_minio(self):
        """MinIO zwraca konkretny content_type (nie generic) -- nie zamieniamy."""
        attachment = MagicMock(spec=SettlementAttachment)
        attachment.storage_path = "settlements/test/image.png"
        attachment.mime_type = "image/png"

        with patch(
            "monolynx.services.settlements.minio_client.get_attachment",
            return_value=(b"PNG bytes", "image/png"),
        ):
            file_bytes, content_type = get_settlement_attachment_bytes(attachment)

        assert content_type == "image/png"
        assert file_bytes == b"PNG bytes"

    def test_keeps_generic_when_no_attachment_mime_type(self):
        """MinIO generic + attachment.mime_type = None -> zwracamy generic."""
        attachment = MagicMock(spec=SettlementAttachment)
        attachment.storage_path = "settlements/test/unknown"
        attachment.mime_type = None  # brak zapisanego mime_type

        with patch(
            "monolynx.services.settlements.minio_client.get_attachment",
            return_value=(b"raw bytes", "application/octet-stream"),
        ):
            file_bytes, content_type = get_settlement_attachment_bytes(attachment)

        # mime_type jest None -> warunek False -> zwracamy oryginalny
        assert content_type == "application/octet-stream"
        assert file_bytes == b"raw bytes"


# ---------------------------------------------------------------------------
# validate_settlement_ticket_link -- linie 62-67
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestValidateSettlementTicketLink:
    """Pokrywa linie 62-67: validate_settlement_ticket_link."""

    async def test_non_draft_settlement_raises_value_error(self, db_session):
        """Linia 62-63: settlement.status != draft -> ValueError."""
        project = await _create_project(db_session, "ValTickNonDraft", "covvtl-nondraft-01")
        owner = await _create_user_with_role(db_session, "covvtl-nondraft01@test.com", project, "owner")
        settlement = await _make_settlement(
            db_session,
            owner,
            [project],
            status="sent",
            sent_at=datetime(2026, 1, 15, tzinfo=UTC),
        )
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Tworzymy mock ticket
        mock_ticket = MagicMock()
        mock_ticket.project_id = project.id

        with pytest.raises(ValueError, match="Mozna powiazac ticket tylko z rozliczeniem w statusie draft"):
            await validate_settlement_ticket_link(db_session, fresh, mock_ticket)

    async def test_ticket_not_in_settlement_projects_raises_value_error(self, db_session):
        """Linia 66-67: ticket.project_id nie nalezy do settlement.projects -> ValueError."""
        project = await _create_project(db_session, "ValTickWrongProj", "covvtl-wrongproj-01")
        other_project = await _create_project(db_session, "ValTickOtherProj", "covvtl-otherproj-01")
        owner = await _create_user_with_role(db_session, "covvtl-wrongproj01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])  # tylko project, nie other_project
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Ticket z innego projektu
        mock_ticket = MagicMock()
        mock_ticket.project_id = other_project.id

        with pytest.raises(ValueError, match="Ticket nie nalezy do zadnego projektu powiazanego z tym rozliczeniem"):
            await validate_settlement_ticket_link(db_session, fresh, mock_ticket)

    async def test_valid_ticket_link_passes(self, db_session):
        """Happy path: settlement draft + ticket.project_id w settlement.projects -> brak wyjatku."""
        project = await _create_project(db_session, "ValTickHappy", "covvtl-happy-01")
        owner = await _create_user_with_role(db_session, "covvtl-happy01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        mock_ticket = MagicMock()
        mock_ticket.project_id = project.id

        # Nie powinno rzucic wyjatku
        await validate_settlement_ticket_link(db_session, fresh, mock_ticket)


# ---------------------------------------------------------------------------
# upload_settlement_attachment -- brakujaca walidacja state (linia 380)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestUploadSettlementAttachmentStateValidation:
    """Pokrywa linie 379-380: walidacja state not in SETTLEMENT_ATTACHMENT_STATES."""

    async def test_invalid_state_raises_value_error(self, db_session):
        """Linia 380: state nie nalezy do SETTLEMENT_ATTACHMENT_STATES -> ValueError."""
        project = await _create_project(db_session, "UploadBadState", "covatt-badstate-01")
        owner = await _create_user_with_role(db_session, "covatt-badstate01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project])
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        with pytest.raises(ValueError, match="Nieprawidlowy stan"):
            await upload_settlement_attachment(
                db_session,
                fresh,
                owner.id,
                file_bytes=b"test",
                filename="test.pdf",
                mime_type="application/pdf",
                category="invoice",
                state="invalid_state",
            )


# ---------------------------------------------------------------------------
# change_settlement_status -- branch 120->124 (elif False path)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestChangeSettlementStatusBranchCoverage:
    """Wymuszamy branch coverage dla elif na linii 120 (False path -> 124).

    Linia 120: elif current == 'paid' and new_status == 'sent':
    Branch False (120->124) odpala gdy ALL poprzednie elif sa False -- tj. ogolny 'else'.
    W praktyce jest to impossible bo ALLOWED_SETTLEMENT_TRANSITIONS zapobiega zlym tranzycjom.
    Jedynym sposobem na osiagniecie tego brancha jest symulacja przez BEZPOSREDNIE ustawienie
    statusu settlement na wartosc ktora nie pasuje do zadnego elif.

    Obejscie: nie patchujemy -- ten branch jest martwym kodem przez walidacje tranzycji.
    Zamiast tego pokrywamy go przez modyfikacje settlement.status na niestandardowy.
    """

    async def test_unknown_transition_skips_all_elif_branches(self, db_session):
        """Wymuszamy branch 120->124 (elif False) przez bezposrednie ustawienie statusu.

        Tworzymy settlement z status='draft' ale patchujemy ALLOWED_SETTLEMENT_TRANSITIONS
        zeby dopuscic tranzycje 'draft' -> 'paid' -- ktora nie pasuje do zadnego elif.
        """
        import monolynx.services.settlements as svc

        project = await _create_project(db_session, "BranchCovProj", "covbranch-cs-01")
        owner = await _create_user_with_role(db_session, "covbranch-cs01@test.com", project, "owner")
        settlement = await _make_settlement(db_session, owner, [project], status="draft")
        fresh = await _get_settlement_with_all(db_session, settlement.id)

        # Patchujemy ALLOWED_SETTLEMENT_TRANSITIONS zeby dopuscic 'draft' -> 'paid'
        # Ta tranzycja nie pasuje do zadnego elif (nie jest draft->sent, sent->paid, sent->draft, paid->sent)
        patched_transitions = {
            "draft": frozenset({"sent", "paid"}),  # dodajemy 'paid' jako dozwolone
            "sent": frozenset({"paid", "draft"}),
            "paid": frozenset({"sent"}),
        }

        with patch.object(svc, "ALLOWED_SETTLEMENT_TRANSITIONS", patched_transitions):
            updated = await change_settlement_status(db_session, fresh, owner.id, "paid")

        # Status zmieniony, ale zadna gałaz timestamp-ow nie zostala wykonana
        assert updated.status == "paid"
        assert updated.sent_at is None
        assert updated.paid_at is None
