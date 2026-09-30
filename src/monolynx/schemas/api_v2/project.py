"""Schematy request/response API v2 dla zasobu Projects (MON-142).

Response modele budowane z dataclass/ORM zwracanych przez `services/projects.py`
(MON-137) -- `model_validate(..., from_attributes=True)` tam, gdzie ksztalt polami
sie pokrywa 1:1, `from_domain()` tam, gdzie serwis zwraca zlozenie dwoch obiektow
(np. `ProjectListItem` = `Project` + `role`)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from monolynx.services.projects import LabelSummary, MemberResult, ProjectDetailResult, ProjectListItem


class ProjectResponse(BaseModel):
    """Ksztalt `Project` ORM -- odpowiedz create_project / update_project."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    code: str
    description: str | None
    created_at: datetime


class ProjectListItemResponse(BaseModel):
    """Element listy projektow -- `ProjectListItem` (projekt + rola wywolujacego)."""

    id: uuid.UUID
    name: str
    slug: str
    code: str
    description: str | None
    role: str
    created_at: datetime

    @classmethod
    def from_domain(cls, item: ProjectListItem) -> ProjectListItemResponse:
        return cls(
            id=item.project.id,
            name=item.project.name,
            slug=item.project.slug,
            code=item.project.code,
            description=item.project.description,
            role=item.role,
            created_at=item.project.created_at,
        )


class ProjectDetailResponse(BaseModel):
    """GET /projects/{project_slug} -- `ProjectDetailResult` (projekt + statystyki)."""

    id: uuid.UUID
    name: str
    slug: str
    code: str
    description: str | None
    created_at: datetime
    role: str | None
    members_count: int
    tickets_count: int
    active_sprint: dict[str, Any] | None
    monitors_count: int
    heartbeats_count: int

    @classmethod
    def from_domain(cls, result: ProjectDetailResult) -> ProjectDetailResponse:
        return cls(
            id=result.project.id,
            name=result.project.name,
            slug=result.project.slug,
            code=result.project.code,
            description=result.project.description,
            created_at=result.project.created_at,
            role=result.role,
            members_count=result.members_count,
            tickets_count=result.tickets_count,
            active_sprint=result.active_sprint,
            monitors_count=result.monitors_count,
            heartbeats_count=result.heartbeats_count,
        )


class ProjectCreateRequest(BaseModel):
    name: str
    slug: str | None = None
    description: str | None = None
    code: str | None = None


class ProjectUpdateRequest(BaseModel):
    """Semantyka PATCH: pole nieobecne = bez zmian. `slug` mapowany na `new_slug` serwisu."""

    name: str | None = None
    description: str | None = None
    slug: str | None = None


class InviteMemberRequest(BaseModel):
    email: str
    role: str = "member"


class InviteMemberResponse(BaseModel):
    """`InviteMemberResult` -- `invitation_token`/`expires_at` obecne tylko dla nowego konta."""

    model_config = ConfigDict(from_attributes=True)

    email: str
    role: str
    is_new_user: bool
    invitation_token: uuid.UUID | None = None
    expires_at: datetime | None = None


class MemberResponse(BaseModel):
    """`MemberResult` -- `user_id` to samo pole, ktore DELETE .../members/{member_id} przyjmuje w sciezce."""

    model_config = ConfigDict(from_attributes=True)

    user_id: uuid.UUID
    name: str
    email: str
    role: str
    joined_at: datetime

    @classmethod
    def from_domain(cls, member: MemberResult) -> MemberResponse:
        return cls(user_id=member.user_id, name=member.name, email=member.email, role=member.role, joined_at=member.joined_at)


class ProjectSummaryResponse(BaseModel):
    """`ProjectSummaryResult` -- zagregowane statystyki projektu."""

    model_config = ConfigDict(from_attributes=True)

    issues_unresolved: int
    monitors_failing: int
    uptime_24h: float | None
    active_sprint: dict[str, Any] | None
    backlog_count: int


class LabelCreateRequest(BaseModel):
    name: str
    color: str | None = None


class LabelResponse(BaseModel):
    """Odpowiedz POST /labels -- serwis zwraca `Label` ORM bez `tickets_count`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    color: str


class LabelSummaryResponse(LabelResponse):
    """Element listy etykiet -- `LabelSummary` z liczba powiazanych ticketow."""

    tickets_count: int

    @classmethod
    def from_domain(cls, label: LabelSummary) -> LabelSummaryResponse:
        return cls(id=label.id, name=label.name, color=label.color, tickets_count=label.tickets_count)
