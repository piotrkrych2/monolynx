"""Testy jednostkowe schematow time trackingu."""

from datetime import date
from uuid import uuid4

import pytest
from pydantic import ValidationError

from monolynx.schemas.time_tracking import (
    TimeTrackingEntryCreate,
    TimeTrackingEntryUpdate,
    TimeTrackingFilter,
)


@pytest.mark.unit
class TestTimeTrackingEntryCreate:
    def test_valid_entry(self):
        """Poprawny wpis przechodzi walidacje."""
        entry = TimeTrackingEntryCreate(
            ticket_id=uuid4(),
            duration_minutes=90,
            date_logged=date(2026, 2, 25),
            description="Code review",
        )
        assert entry.duration_minutes == 90
        assert entry.description == "Code review"

    def test_rejects_zero_duration(self):
        """duration_minutes musi byc > 0."""
        with pytest.raises(ValidationError):
            TimeTrackingEntryCreate(
                ticket_id=uuid4(),
                duration_minutes=0,
                date_logged=date(2026, 2, 25),
            )

    def test_rejects_negative_duration(self):
        """Ujemny czas jest odrzucany."""
        with pytest.raises(ValidationError):
            TimeTrackingEntryCreate(
                ticket_id=uuid4(),
                duration_minutes=-30,
                date_logged=date(2026, 2, 25),
            )

    def test_description_optional(self):
        """Opis jest opcjonalny."""
        entry = TimeTrackingEntryCreate(
            ticket_id=uuid4(),
            duration_minutes=60,
            date_logged=date(2026, 2, 25),
        )
        assert entry.description is None

    def test_description_max_length(self):
        """Opis nie moze przekraczac 1000 znakow."""
        with pytest.raises(ValidationError):
            TimeTrackingEntryCreate(
                ticket_id=uuid4(),
                duration_minutes=60,
                date_logged=date(2026, 2, 25),
                description="x" * 1001,
            )

    def test_description_at_max_length(self):
        """Opis z dokladnie 1000 znakow przechodzi walidacje."""
        entry = TimeTrackingEntryCreate(
            ticket_id=uuid4(),
            duration_minutes=60,
            date_logged=date(2026, 2, 25),
            description="x" * 1000,
        )
        assert len(entry.description) == 1000

    def test_requires_ticket_id(self):
        """ticket_id jest wymagany."""
        with pytest.raises(ValidationError):
            TimeTrackingEntryCreate(
                duration_minutes=60,
                date_logged=date(2026, 2, 25),
            )

    def test_requires_date_logged(self):
        """date_logged jest wymagany."""
        with pytest.raises(ValidationError):
            TimeTrackingEntryCreate(
                ticket_id=uuid4(),
                duration_minutes=60,
            )

    def test_requires_duration_minutes(self):
        """duration_minutes jest wymagany."""
        with pytest.raises(ValidationError):
            TimeTrackingEntryCreate(
                ticket_id=uuid4(),
                date_logged=date(2026, 2, 25),
            )

    def test_duration_one_minute(self):
        """Minimalny czas 1 minuta jest poprawny."""
        entry = TimeTrackingEntryCreate(
            ticket_id=uuid4(),
            duration_minutes=1,
            date_logged=date(2026, 2, 25),
        )
        assert entry.duration_minutes == 1


@pytest.mark.unit
class TestTimeTrackingEntryUpdate:
    def test_valid_status(self):
        """Poprawny status przechodzi walidacje."""
        update = TimeTrackingEntryUpdate(status="submitted")
        assert update.validate_status() is True

    def test_invalid_status(self):
        """Niepoprawny status nie przechodzi walidacji metody."""
        update = TimeTrackingEntryUpdate(status="invalid")
        assert update.validate_status() is False

    def test_all_statuses_valid(self):
        """Wszystkie zdefiniowane statusy sa poprawne."""
        for status in ("draft", "submitted", "approved", "rejected"):
            update = TimeTrackingEntryUpdate(status=status)
            assert update.validate_status() is True

    def test_empty_string_status_invalid(self):
        """Pusty string nie jest poprawnym statusem."""
        update = TimeTrackingEntryUpdate(status="")
        assert update.validate_status() is False

    def test_status_is_required(self):
        """Status jest polem wymaganym."""
        with pytest.raises(ValidationError):
            TimeTrackingEntryUpdate()


@pytest.mark.unit
class TestTimeTrackingFilter:
    def test_defaults(self):
        """Domyslne wartosci filtrow."""
        f = TimeTrackingFilter()
        assert f.project_ids is None
        assert f.user_ids is None
        assert f.sprint_ids is None
        assert f.date_from is None
        assert f.date_to is None
        assert f.status is None
        assert f.created_via_ai is None
        assert f.page == 1
        assert f.per_page == 20

    def test_page_must_be_positive(self):
        """Numer strony musi byc >= 1."""
        with pytest.raises(ValidationError):
            TimeTrackingFilter(page=0)

    def test_page_negative_rejected(self):
        """Ujemny numer strony jest odrzucany."""
        with pytest.raises(ValidationError):
            TimeTrackingFilter(page=-1)

    def test_per_page_max(self):
        """per_page nie moze przekraczac 100."""
        with pytest.raises(ValidationError):
            TimeTrackingFilter(per_page=101)

    def test_per_page_at_max(self):
        """per_page rowne 100 jest poprawne."""
        f = TimeTrackingFilter(per_page=100)
        assert f.per_page == 100

    def test_per_page_min(self):
        """per_page musi byc >= 1."""
        with pytest.raises(ValidationError):
            TimeTrackingFilter(per_page=0)

    def test_validate_status_none(self):
        """None status jest akceptowany."""
        f = TimeTrackingFilter()
        assert f.validate_status() is True

    def test_validate_status_valid(self):
        """Poprawny status przechodzi walidacje."""
        f = TimeTrackingFilter(status="draft")
        assert f.validate_status() is True

    def test_validate_status_invalid(self):
        """Niepoprawny status nie przechodzi."""
        f = TimeTrackingFilter(status="nonexistent")
        assert f.validate_status() is False

    def test_all_filters_set(self):
        """Wszystkie filtry ustawione jednoczesnie."""
        project = uuid4()
        user = uuid4()
        sprint = uuid4()
        f = TimeTrackingFilter(
            project_ids=[project],
            user_ids=[user],
            sprint_ids=[sprint],
            date_from=date(2026, 1, 1),
            date_to=date(2026, 12, 31),
            status="approved",
            created_via_ai=True,
            page=2,
            per_page=50,
        )
        assert f.project_ids == [project]
        assert f.user_ids == [user]
        assert f.sprint_ids == [sprint]
        assert f.date_from == date(2026, 1, 1)
        assert f.date_to == date(2026, 12, 31)
        assert f.status == "approved"
        assert f.created_via_ai is True
        assert f.page == 2
        assert f.per_page == 50

    def test_created_via_ai_filter_true(self):
        """Filtr AI=True."""
        f = TimeTrackingFilter(created_via_ai=True)
        assert f.created_via_ai is True

    def test_created_via_ai_filter_false(self):
        """Filtr AI=False (tylko reczne)."""
        f = TimeTrackingFilter(created_via_ai=False)
        assert f.created_via_ai is False
