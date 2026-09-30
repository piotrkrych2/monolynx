"""API v2 -- Tickets (MON-142): tickety, tablica Kanban, komentarze, kryteria
akceptacji. Endpointy 1:1 na toole MCP odpowiadajace, wolajace services/tickets.py
(MON-137) i (przez niego) services/ticket_dependencies.py::set_blockers dla
blocked_by_ids. Uprawnienia -- tylko czlonkostwo przez `get_api_user_and_project`,
bez dodatkowych check_permission (patrz sekcja 7 ticketu).

Trasy statyczne `/tickets/search` i `/tickets/bulk` sa rejestrowane PRZED
`/tickets/{ticket_id}` -- inaczej FastAPI dopasowalby "search"/"bulk" jako
ticket_id i trafilby do resolve_ticket_uuid (404) zamiast wlasciwego handlera."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from monolynx.api.v2.common import DEFAULT_PER_PAGE, MAX_PER_PAGE, paginate, service_error
from monolynx.api.v2.deps import get_api_user_and_project
from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.ticket import Ticket
from monolynx.models.ticket_acceptance_criterion import TicketAcceptanceCriterion
from monolynx.models.ticket_attachment import TicketAttachment
from monolynx.models.ticket_comment import TicketComment
from monolynx.models.user import User
from monolynx.schemas.api_v2.pagination import PaginatedResponse
from monolynx.schemas.api_v2.sprint import SprintResponse
from monolynx.schemas.api_v2.ticket import (
    AcceptanceCriterionCreateRequest,
    AcceptanceCriterionUpdateRequest,
    BoardResponse,
    BoardTicketItem,
    BulkUpdateFailedItem,
    BulkUpdateRequest,
    BulkUpdateResponse,
    CommentCreateRequest,
    TicketAcceptanceCriterionResponse,
    TicketAttachmentResponse,
    TicketCommentResponse,
    TicketCreateRequest,
    TicketDetailResponse,
    TicketListItem,
    TicketRelationItem,
    TicketSearchItem,
    TicketUpdateRequest,
)
from monolynx.services import tickets as tickets_service

router = APIRouter(tags=["tickets"])


def _ticket_list_item(ticket: Ticket, project_code: str) -> TicketListItem:
    return TicketListItem(
        id=str(ticket.id),
        key=f"{project_code}-{ticket.number}",
        title=ticket.title,
        description=ticket.description,
        status=ticket.status,
        priority=ticket.priority,
        story_points=ticket.story_points,
        sprint_id=str(ticket.sprint_id) if ticket.sprint_id else None,
        assignee_email=ticket.assignee.email if ticket.assignee else None,
        due_date=ticket.due_date.isoformat() if ticket.due_date else None,
        label_ids=[str(lb.id) for lb in ticket.labels],
        created_via_ai=ticket.created_via_ai,
        created_at=ticket.created_at.isoformat(),
        updated_at=ticket.updated_at.isoformat(),
    )


def _ticket_relation_item(other: Ticket, project_code: str) -> TicketRelationItem:
    return TicketRelationItem(id=str(other.id), key=f"{project_code}-{other.number}", title=other.title, status=other.status)


def _comment_response(comment: TicketComment, author_email: str) -> TicketCommentResponse:
    return TicketCommentResponse(
        id=str(comment.id),
        content=comment.content,
        author=author_email,
        created_via_ai=comment.created_via_ai,
        created_at=comment.created_at.isoformat(),
    )


def _attachment_response(attachment: TicketAttachment) -> TicketAttachmentResponse:
    return TicketAttachmentResponse(
        id=str(attachment.id),
        filename=attachment.filename,
        mime_type=attachment.mime_type,
        size=attachment.size,
        created_at=attachment.created_at.isoformat(),
    )


def _criterion_response(criterion: TicketAcceptanceCriterion) -> TicketAcceptanceCriterionResponse:
    return TicketAcceptanceCriterionResponse(
        id=str(criterion.id),
        description=criterion.description,
        is_completed=criterion.is_completed,
        created_via_ai=criterion.created_via_ai,
        position=criterion.position,
        created_at=criterion.created_at.isoformat(),
    )


def _ticket_detail(ticket: Ticket, project_code: str) -> TicketDetailResponse:
    base = _ticket_list_item(ticket, project_code)
    return TicketDetailResponse(
        **base.model_dump(),
        comments=[_comment_response(c, c.author.email) for c in ticket.comments],
        attachments=[_attachment_response(a) for a in ticket.attachments],
        acceptance_criteria=[_criterion_response(c) for c in ticket.acceptance_criteria],
        blocked_by=[_ticket_relation_item(t, project_code) for t in ticket.blocked_by],
        blocks=[_ticket_relation_item(t, project_code) for t in ticket.blocking],
    )


# ---------------------------------------------------------------------------
# GET /projects/{project_slug}/tickets
# ---------------------------------------------------------------------------


@router.get("/projects/{project_slug}/tickets", response_model=PaginatedResponse[TicketListItem])
async def list_tickets_endpoint(
    project_slug: str,
    status: str | None = None,
    priority: str | None = None,
    search: str | None = None,
    sprint_id: str | None = None,
    due_date_before: str | None = None,
    due_date_after: str | None = None,
    overdue: bool = False,
    label_id: str | None = None,
    page: int = Query(1, ge=1),
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TicketListItem]:
    user, project = user_project
    try:
        result = await tickets_service.list_tickets(
            db,
            user,
            project,
            status=status,
            priority=priority,
            search=search,
            sprint_id=sprint_id,
            due_date_before=due_date_before,
            due_date_after=due_date_after,
            overdue=overdue,
            label_id=label_id,
            page=page,
        )
    except ValueError as exc:
        raise service_error(exc) from exc

    items = [_ticket_list_item(t, project.code) for t in result.tickets]
    return PaginatedResponse[TicketListItem](items=items, page=result.page, per_page=20, total=result.total, total_pages=result.total_pages)


# ---------------------------------------------------------------------------
# GET /projects/{project_slug}/tickets/search -- MUSI byc przed {ticket_id}
# ---------------------------------------------------------------------------


@router.get("/projects/{project_slug}/tickets/search", response_model=PaginatedResponse[TicketSearchItem])
async def search_tickets_endpoint(
    project_slug: str,
    query: str | None = None,
    status: str | None = None,
    priority: str | None = None,
    assignee_email: str | None = None,
    sprint_id: str | None = None,
    due_before: str | None = None,
    due_after: str | None = None,
    page: int = Query(1, ge=1),
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TicketSearchItem]:
    user, project = user_project
    try:
        result = await tickets_service.search_tickets(
            db,
            user,
            project,
            query=query,
            status=status,
            priority=priority,
            assignee_email=assignee_email,
            sprint_id=sprint_id,
            due_before=due_before,
            due_after=due_after,
            page=page,
        )
    except ValueError as exc:
        raise service_error(exc) from exc

    items = [TicketSearchItem.model_validate(row) for row in result.results]
    return PaginatedResponse[TicketSearchItem](items=items, page=result.page, per_page=20, total=result.total, total_pages=result.total_pages)


# ---------------------------------------------------------------------------
# PATCH /projects/{project_slug}/tickets/bulk -- MUSI byc przed {ticket_id}
# ---------------------------------------------------------------------------


@router.patch("/projects/{project_slug}/tickets/bulk", response_model=BulkUpdateResponse)
async def bulk_update_tickets_endpoint(
    project_slug: str,
    body: BulkUpdateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> BulkUpdateResponse:
    user, project = user_project
    try:
        result = await tickets_service.bulk_update_tickets(
            db,
            user,
            project,
            body.ticket_ids,
            status=body.status,
            priority=body.priority,
            assignee_email=body.assignee_email,
            sprint_id=body.sprint_id,
            due_date=body.due_date,
        )
    except ValueError as exc:
        raise service_error(exc) from exc

    return BulkUpdateResponse(updated=result.updated, failed=[BulkUpdateFailedItem(**f) for f in result.failed])


# ---------------------------------------------------------------------------
# GET/POST/PATCH/DELETE /projects/{project_slug}/tickets/{ticket_id}
# ---------------------------------------------------------------------------


@router.get("/projects/{project_slug}/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def get_ticket_endpoint(
    project_slug: str,
    ticket_id: str,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> TicketDetailResponse:
    user, project = user_project
    try:
        detail = await tickets_service.get_ticket(db, user, project, ticket_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    return _ticket_detail(detail.ticket, project.code)


@router.post("/projects/{project_slug}/tickets", response_model=TicketDetailResponse, status_code=201)
async def create_ticket_endpoint(
    project_slug: str,
    body: TicketCreateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> TicketDetailResponse:
    user, project = user_project
    try:
        create_result = await tickets_service.create_ticket(
            db,
            user,
            project,
            body.title,
            description=body.description,
            priority=body.priority,
            story_points=body.story_points,
            sprint_id=body.sprint_id,
            assignee_email=body.assignee_email,
            due_date=body.due_date,
            label_ids=body.label_ids,
            acceptance_criteria=body.acceptance_criteria,
            spec_page_id=body.spec_page_id,
            blocked_by_ids=body.blocked_by_ids,
        )
        # blocked_by/blocking nie sa dotykane przez create_ticket (set_blockers pisze
        # bezposrednio do ticket_dependencies, nie przez atrybut relacji), ale expire
        # tu i tak jest tanie/bezpieczne i trzyma symetrie z update_ticket ponizej.
        db.expire(create_result.ticket, ["blocked_by", "blocking"])
        detail = await tickets_service.get_ticket(db, user, project, str(create_result.ticket.id))
    except ValueError as exc:
        raise service_error(exc) from exc
    return _ticket_detail(detail.ticket, project.code)


@router.patch("/projects/{project_slug}/tickets/{ticket_id}", response_model=TicketDetailResponse)
async def update_ticket_endpoint(
    project_slug: str,
    ticket_id: str,
    body: TicketUpdateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> TicketDetailResponse:
    user, project = user_project
    fields = body.model_dump(exclude_unset=True)
    try:
        ticket = await tickets_service.update_ticket(db, user, project, ticket_id, **fields)
        # set_blockers zmienia ticket_dependencies bezposrednio przez SQL, nie przez
        # atrybut relacji -- gdy blocked_by/blocking byly juz zaladowane na tym samym
        # obiekcie ORM (identity map) we wczesniejszym requescie w tej samej sesji,
        # selectinload w get_ticket() ich NIE nadpisze (domyslnie nie odswieza juz
        # zaladowanej kolekcji). Bez jawnego expire response nizej zwracalby stara
        # liste blokerow sprzed tego PATCH (test_blocked_by_ids_replace_all/_clears).
        db.expire(ticket, ["blocked_by", "blocking"])
        detail = await tickets_service.get_ticket(db, user, project, str(ticket.id))
    except ValueError as exc:
        raise service_error(exc) from exc
    return _ticket_detail(detail.ticket, project.code)


@router.delete("/projects/{project_slug}/tickets/{ticket_id}", status_code=204, response_model=None)
async def delete_ticket_endpoint(
    project_slug: str,
    ticket_id: str,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user, project = user_project
    try:
        await tickets_service.delete_ticket(db, user, project, ticket_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# GET /projects/{project_slug}/board
# ---------------------------------------------------------------------------


@router.get("/projects/{project_slug}/board", response_model=BoardResponse)
async def get_board_endpoint(
    project_slug: str,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> BoardResponse:
    user, project = user_project
    result = await tickets_service.get_board(db, user, project)
    sprint_response = SprintResponse.from_domain(result.sprint) if result.sprint else None
    columns = {col: [BoardTicketItem.model_validate(item) for item in items] for col, items in result.columns.items()}
    return BoardResponse(sprint=sprint_response, columns=columns)


# ---------------------------------------------------------------------------
# Komentarze
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_slug}/tickets/{ticket_id}/comments",
    response_model=PaginatedResponse[TicketCommentResponse],
)
async def list_comments_endpoint(
    project_slug: str,
    ticket_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TicketCommentResponse]:
    user, project = user_project
    try:
        comments = await tickets_service.list_comments(db, user, project, ticket_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    items = [_comment_response(c, c.author.email) for c in comments]
    return paginate(items, page, per_page)


@router.post(
    "/projects/{project_slug}/tickets/{ticket_id}/comments",
    response_model=TicketCommentResponse,
    status_code=201,
)
async def add_comment_endpoint(
    project_slug: str,
    ticket_id: str,
    body: CommentCreateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> TicketCommentResponse:
    user, project = user_project
    try:
        comment = await tickets_service.add_comment(db, user, project, ticket_id, body.content)
    except ValueError as exc:
        raise service_error(exc) from exc
    # comment.author nie jest tu zaladowany (add_comment nie robi selectinload) --
    # uzywamy user.email, bo add_comment ustawia user_id=user.id.
    return _comment_response(comment, user.email)


# ---------------------------------------------------------------------------
# Kryteria akceptacji
# ---------------------------------------------------------------------------


@router.get(
    "/projects/{project_slug}/tickets/{ticket_id}/acceptance-criteria",
    response_model=PaginatedResponse[TicketAcceptanceCriterionResponse],
)
async def list_acceptance_criteria_endpoint(
    project_slug: str,
    ticket_id: str,
    page: int = Query(1, ge=1),
    per_page: int = Query(DEFAULT_PER_PAGE, ge=1, le=MAX_PER_PAGE),
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[TicketAcceptanceCriterionResponse]:
    user, project = user_project
    try:
        criteria = await tickets_service.list_acceptance_criteria(db, user, project, ticket_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    items = [_criterion_response(c) for c in criteria]
    return paginate(items, page, per_page)


@router.post(
    "/projects/{project_slug}/tickets/{ticket_id}/acceptance-criteria",
    response_model=TicketAcceptanceCriterionResponse,
    status_code=201,
)
async def add_acceptance_criterion_endpoint(
    project_slug: str,
    ticket_id: str,
    body: AcceptanceCriterionCreateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> TicketAcceptanceCriterionResponse:
    user, project = user_project
    try:
        criterion = await tickets_service.add_acceptance_criterion(db, user, project, ticket_id, body.description)
    except ValueError as exc:
        raise service_error(exc) from exc
    return _criterion_response(criterion)


@router.patch(
    "/projects/{project_slug}/tickets/{ticket_id}/acceptance-criteria/{criterion_id}",
    response_model=TicketAcceptanceCriterionResponse,
)
async def update_acceptance_criterion_endpoint(
    project_slug: str,
    ticket_id: str,
    criterion_id: str,
    body: AcceptanceCriterionUpdateRequest,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> TicketAcceptanceCriterionResponse:
    user, project = user_project
    fields = body.model_dump(exclude_unset=True)
    try:
        criterion = await tickets_service.update_acceptance_criterion(db, user, project, ticket_id, criterion_id, **fields)
    except ValueError as exc:
        raise service_error(exc) from exc
    return _criterion_response(criterion)


@router.delete(
    "/projects/{project_slug}/tickets/{ticket_id}/acceptance-criteria/{criterion_id}",
    status_code=204,
    response_model=None,
)
async def delete_acceptance_criterion_endpoint(
    project_slug: str,
    ticket_id: str,
    criterion_id: str,
    user_project: tuple[User, Project] = Depends(get_api_user_and_project),
    db: AsyncSession = Depends(get_db),
) -> Response:
    user, project = user_project
    try:
        await tickets_service.delete_acceptance_criterion(db, user, project, ticket_id, criterion_id)
    except ValueError as exc:
        raise service_error(exc) from exc
    return Response(status_code=204)
