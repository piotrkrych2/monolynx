"""Schematy Pydantic API v2 dla zasobu Tickets (MON-142): request/response bodies,
budowane z ORM Ticket/TicketComment/TicketAttachment/TicketAcceptanceCriterion
zwracanych przez services/tickets.py (MON-137) -- pola nazwane jak parametry
odpowiadajacego tool MCP w mcp_server.py (parytet MON-131)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from monolynx.schemas.api_v2.sprint import SprintResponse


class TicketCreateRequest(BaseModel):
    title: str
    description: str | None = None
    priority: str = "medium"
    story_points: int | None = None
    sprint_id: str | None = None
    assignee_email: str | None = None
    due_date: str | None = None
    label_ids: list[str] | None = None
    acceptance_criteria: list[str] | None = None
    spec_page_id: str | None = None
    blocked_by_ids: list[str] | None = None


class TicketUpdateRequest(BaseModel):
    """Semantyka PATCH: router odczytuje tylko pola jawnie obecne w body przez
    `model_dump(exclude_unset=True)`, nie po `is None` -- pusty string w
    assignee_email/due_date/sprint_id musi dotrzec do serwisu jako "" (czysci
    pole), nie zostac odfiltrowany jak brak pola."""

    title: str | None = None
    description: str | None = None
    status: str | None = None
    priority: str | None = None
    story_points: int | None = None
    sprint_id: str | None = None
    assignee_email: str | None = None
    due_date: str | None = None
    label_ids: list[str] | None = None
    blocked_by_ids: list[str] | None = None


class BulkUpdateRequest(BaseModel):
    ticket_ids: list[str] = Field(min_length=1, max_length=100)
    status: str | None = None
    priority: str | None = None
    assignee_email: str | None = None
    sprint_id: str | None = None
    due_date: str | None = None


class BulkUpdateFailedItem(BaseModel):
    id: str
    reason: str


class BulkUpdateResponse(BaseModel):
    updated: int
    failed: list[BulkUpdateFailedItem]


class TicketRelationItem(BaseModel):
    """Wpis w blocked_by/blocks -- klucz, tytul, status blokera/blokowanego (bez
    pelnych szczegolow, jak sekcja '## Blokery (relacja)' w tool MCP get_ticket)."""

    id: str
    key: str
    title: str
    status: str


class TicketListItem(BaseModel):
    id: str
    key: str
    title: str
    description: str | None
    status: str
    priority: str
    story_points: int | None
    sprint_id: str | None
    assignee_email: str | None
    due_date: str | None
    label_ids: list[str]
    blocked_by: list[TicketRelationItem]
    created_via_ai: bool
    created_at: str
    updated_at: str


class TicketListItemCompact(BaseModel):
    """Element listy ticketow w trybie `?compact=true` (MON-247): pola `TicketListItem` w tej samej
    kolejnosci, bez `description`. Osobny model, a nie `response_model_exclude`, zeby OpenAPI
    opisywal faktyczny kształt odpowiedzi; `TicketListItem` zostaje bazą `TicketDetailResponse`."""

    id: str
    key: str
    title: str
    status: str
    priority: str
    story_points: int | None
    sprint_id: str | None
    assignee_email: str | None
    due_date: str | None
    label_ids: list[str]
    blocked_by: list[TicketRelationItem]
    created_via_ai: bool
    created_at: str
    updated_at: str


class TicketCommentResponse(BaseModel):
    id: str
    content: str
    author: str
    created_via_ai: bool
    created_at: str


class TicketAttachmentResponse(BaseModel):
    id: str
    filename: str
    mime_type: str | None
    size: int
    created_at: str


class TicketAcceptanceCriterionResponse(BaseModel):
    id: str
    description: str
    is_completed: bool
    created_via_ai: bool
    position: int
    created_at: str


class TicketDetailResponse(TicketListItem):
    comments: list[TicketCommentResponse]
    attachments: list[TicketAttachmentResponse]
    acceptance_criteria: list[TicketAcceptanceCriterionResponse]
    spec_page_id: str | None
    blocks: list[TicketRelationItem]


class TicketSearchItem(BaseModel):
    """Odpowiada wierszowi z services/tickets.py::search_tickets (_ticket_row) --
    'settlements' jest obecne w wejsciowym dict tylko gdy wywolujacy ma
    rozliczenia:read, wiec pole ma domyslnie None."""

    id: str
    key: str
    title: str
    status: str
    priority: str
    story_points: int | None
    assignee: str | None
    due_date: str | None
    settlements: list[dict[str, Any]] | None = None


class CommentCreateRequest(BaseModel):
    content: str


class AcceptanceCriterionCreateRequest(BaseModel):
    description: str


class AcceptanceCriterionUpdateRequest(BaseModel):
    description: str | None = None
    is_completed: bool | None = None


class BoardTicketItem(BaseModel):
    """Odpowiada wierszowi z services/tickets.py::get_board (kolumny tablicy)."""

    key: str
    title: str
    priority: str
    story_points: int | None
    assignee: str | None
    labels: list[str]


class BoardResponse(BaseModel):
    sprint: SprintResponse | None
    columns: dict[str, list[BoardTicketItem]]
