"""Schematy Pydantic dla modulu time trackingu."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from monolynx.constants import TIME_TRACKING_STATUSES


class TimeTrackingEntryCreate(BaseModel):
    """Schemat tworzenia nowego wpisu czasu pracy."""

    ticket_id: UUID = Field(..., description="UUID ticketu")
    duration_minutes: int = Field(..., gt=0, description="Czas trwania w minutach (> 0)")
    date_logged: date = Field(..., description="Data wykonania pracy (YYYY-MM-DD)")
    description: str | None = Field(None, description="Opcjonalny opis pracy", max_length=1000)


class TimeTrackingEntryUpdate(BaseModel):
    """Schemat aktualizacji wpisu (tylko zmiana statusu)."""

    status: str = Field(..., description="Nowy status: draft, submitted, approved, rejected")

    def validate_status(self) -> bool:
        return self.status in TIME_TRACKING_STATUSES


class TimeTrackingEntryResponse(BaseModel):
    """Schemat odpowiedzi API z wpisem czasu pracy."""

    id: UUID
    ticket_id: UUID
    user_id: UUID
    sprint_id: UUID | None
    project_id: UUID
    duration_minutes: int
    date_logged: date
    description: str | None
    status: str
    created_via_ai: bool = False
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class TimeTrackingFilter(BaseModel):
    """Schemat filtrow raportu pracy."""

    project_ids: list[UUID] | None = Field(None, description="Filtruj po projektach (lista UUID)")
    user_ids: list[UUID] | None = Field(None, description="Filtruj po uzytkownikach (lista UUID)")
    sprint_ids: list[UUID] | None = Field(None, description="Filtruj po sprintach (lista UUID)")
    date_from: date | None = Field(None, description="Poczatek zakresu dat (wlacznie)")
    date_to: date | None = Field(None, description="Koniec zakresu dat (wlacznie)")
    status: str | None = Field(None, description="Filtruj po statusie")
    created_via_ai: bool | None = Field(None, description="Filtruj po zrodle: True=AI, False=reczne, None=wszystkie")
    page: int = Field(1, ge=1, description="Numer strony")
    per_page: int = Field(20, ge=1, le=100, description="Wierszy na strone")

    def validate_status(self) -> bool:
        return self.status is None or self.status in TIME_TRACKING_STATUSES


class WorkReportResult(BaseModel):
    """Schemat zagregowanych wynikow raportu pracy."""

    entries: list[TimeTrackingEntryResponse] = Field(..., description="Lista przefiltrowanych wpisow")
    total_hours: float = Field(..., description="Suma godzin w przefiltrowanych wpisach")
    entry_count: int = Field(..., description="Liczba wpisow")
    hours_by_user: dict[str, float] = Field(..., description="Godziny wg uzytkownika (UUID jako string)")
    hours_by_sprint: dict[str, float] = Field(..., description="Godziny wg sprintu (UUID jako string)")
    hours_by_project: dict[str, float] = Field(default_factory=dict, description="Godziny wg projektu (UUID jako string)")
    date_range: tuple[date, date] | None = Field(None, description="Zakres dat wpisow")
    page: int = Field(..., description="Aktualny numer strony")
    total_pages: int = Field(..., description="Calkowita liczba stron")
