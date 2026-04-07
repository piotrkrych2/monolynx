"""Schematy Pydantic dla modelu Role (RBAC)."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, field_validator

from monolynx.constants import PERMISSION_ACTIONS, PERMISSION_MODULES


class RoleCreate(BaseModel):
    """Tworzenie nowej roli."""

    name: str
    permissions: dict[str, list[str]]
    project_id: uuid.UUID | None = None
    is_system: bool = False

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Nazwa roli nie może być pusta.")
        if len(v) > 50:
            raise ValueError("Nazwa roli nie może przekraczać 50 znaków.")
        return v

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: dict[str, list[str]]) -> dict[str, list[str]]:
        for module, actions in v.items():
            if module not in PERMISSION_MODULES:
                raise ValueError(f"Nieznany moduł: '{module}'. Dozwolone: {PERMISSION_MODULES}")
            for action in actions:
                if action not in PERMISSION_ACTIONS:
                    raise ValueError(f"Nieznana akcja: '{action}'. Dozwolone: {PERMISSION_ACTIONS}")
        return v


class RoleUpdate(BaseModel):
    """Edycja roli (wszystkie pola opcjonalne)."""

    name: str | None = None
    permissions: dict[str, list[str]] | None = None

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if len(v) > 50:
            raise ValueError("Nazwa roli nie może przekraczać 50 znaków.")
        return v

    @field_validator("permissions")
    @classmethod
    def validate_permissions(cls, v: dict[str, list[str]] | None) -> dict[str, list[str]] | None:
        if v is None:
            return v
        for module, actions in v.items():
            if module not in PERMISSION_MODULES:
                raise ValueError(f"Nieznany moduł: '{module}'. Dozwolone: {PERMISSION_MODULES}")
            for action in actions:
                if action not in PERMISSION_ACTIONS:
                    raise ValueError(f"Nieznana akcja: '{action}'. Dozwolone: {PERMISSION_ACTIONS}")
        return v


class RoleResponse(BaseModel):
    """Odpowiedź API z danymi roli."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    project_id: uuid.UUID | None
    permissions: dict[str, list[str]]
    is_system: bool
    created_at: datetime
    updated_at: datetime | None
