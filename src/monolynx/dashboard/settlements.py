"""Dashboard -- modul rozliczen (lista, detal, tworzenie, edycja, usuwanie, zalaczniki)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.dashboard.helpers import _get_user_id, flash, render_project_page, templates
from monolynx.database import get_db
from monolynx.models import Project, ProjectMember, Settlement, SettlementProject, Ticket
from monolynx.services.permissions import check_permission, require_permission
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
from monolynx.services.wiki import render_markdown_html

router = APIRouter(prefix="/dashboard", tags=["settlements"])


async def _get_projects_with_write(
    user_id: uuid.UUID,
    db: AsyncSession,
    is_superuser: bool,
) -> list[Project]:
    """Zwraca liste aktywnych projektow gdzie user ma rozliczenia:write."""
    if is_superuser:
        result = await db.execute(select(Project).where(Project.is_active.is_(True)).order_by(Project.name))
        return list(result.scalars().all())

    # Pobierz projekty, w ktorych user jest czlonkiem
    result = await db.execute(
        select(Project)
        .join(ProjectMember, ProjectMember.project_id == Project.id)
        .where(
            ProjectMember.user_id == user_id,
            Project.is_active.is_(True),
        )
        .order_by(Project.name)
    )
    all_member_projects = list(result.scalars().unique().all())

    # Odfiltruj te, w ktorych user ma write
    projects_with_write: list[Project] = []
    for project in all_member_projects:
        if await check_permission(db, user_id, project.id, "rozliczenia", "write"):
            projects_with_write.append(project)

    return projects_with_write


async def _get_project(slug: str, db: AsyncSession) -> Project:
    """Pobierz aktywny projekt po slug, 404 gdy nie istnieje."""
    result = await db.execute(select(Project).where(Project.slug == slug, Project.is_active.is_(True)))
    project = result.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=404, detail="Projekt nie istnieje")
    return project


async def _get_settlement(settlement_id: uuid.UUID, project_id: uuid.UUID, db: AsyncSession) -> Settlement:
    """Pobierz settlement z eager load + walidacja ownership przez M2M.

    Zwraca 404 gdy:
    - settlement nie istnieje
    - settlement.is_active == False
    - projekt nie jest w settlement.projects (M2M)
    """
    result = await db.execute(
        select(Settlement)
        .join(SettlementProject, SettlementProject.settlement_id == Settlement.id)
        .where(
            Settlement.id == settlement_id,
            Settlement.is_active.is_(True),
            SettlementProject.project_id == project_id,
        )
        .options(
            selectinload(Settlement.projects.and_(Project.is_active.is_(True))),
            selectinload(Settlement.tickets),
            selectinload(Settlement.attachments),
            selectinload(Settlement.created_by),
        )
    )
    settlement = result.scalar_one_or_none()
    if settlement is None:
        raise HTTPException(status_code=404, detail="Rozliczenie nie istnieje")
    return settlement


@router.get("/{slug}/rozliczenia/", response_class=HTMLResponse, response_model=None)
async def settlement_list(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "read")

    # Query params
    page = max(1, int(request.query_params.get("page", "1")))
    per_page = 20
    show_paid = request.query_params.get("show_paid") == "1"

    # Base conditions
    conditions = [
        SettlementProject.project_id == project.id,
        Settlement.is_active.is_(True),
    ]
    if not show_paid:
        conditions.append(Settlement.status != "paid")

    # Count
    count_result = await db.execute(
        select(func.count(Settlement.id.distinct())).join(SettlementProject, SettlementProject.settlement_id == Settlement.id).where(*conditions)
    )
    total_count = count_result.scalar() or 0
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    # Main query with eager load
    result = await db.execute(
        select(Settlement)
        .join(SettlementProject, SettlementProject.settlement_id == Settlement.id)
        .where(*conditions)
        .options(
            selectinload(Settlement.projects.and_(Project.is_active.is_(True))),
            selectinload(Settlement.tickets),
            selectinload(Settlement.attachments),
            selectinload(Settlement.created_by),
        )
        .order_by(Settlement.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    settlements = list(result.scalars().unique().all())

    return await render_project_page(
        request,
        "dashboard/settlements/list.html",
        {
            "project": project,
            "settlements": settlements,
            "page": page,
            "per_page": per_page,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "show_paid": show_paid,
            "active_module": "rozliczenia",
        },
        db,
    )


# UWAGA: Endpoint /create musi byc przed /{settlement_id} aby uniknac konfliktu
# (FastAPI i tak rozwiaze to poprawnie przez walidacje UUID, ale kolejnosc jest tu zachowana)


@router.get("/{slug}/rozliczenia/create", response_class=HTMLResponse, response_model=None)
async def settlement_create_form(
    request: Request,
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    is_superuser = bool(request.session.get("is_superuser", False))
    all_projects = await _get_projects_with_write(user_id, db, is_superuser)

    return await render_project_page(
        request,
        "dashboard/settlements/create.html",
        {
            "project": project,
            "all_projects": all_projects,
            "current_project_id": project.id,
            "form_data": {},
            "active_module": "rozliczenia",
        },
        db,
    )


@router.post("/{slug}/rozliczenia/create", response_class=HTMLResponse, response_model=None)
async def settlement_create(
    request: Request,
    slug: str,
    name: Annotated[str, Form()],
    period_from: Annotated[date, Form()],
    period_to: Annotated[date, Form()],
    project_ids_raw: Annotated[list[str], Form(alias="project_ids")] = [],  # noqa: B006
    notes: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    is_superuser = bool(request.session.get("is_superuser", False))
    all_projects = await _get_projects_with_write(user_id, db, is_superuser)

    # Parsuj project_ids z listy stringow na UUID
    project_ids: list[uuid.UUID] = []
    for raw in project_ids_raw:
        try:
            project_ids.append(uuid.UUID(raw))
        except ValueError:
            flash(request, f"Nieprawidlowe ID projektu: {raw}", "error")
            return await render_project_page(
                request,
                "dashboard/settlements/create.html",
                {
                    "project": project,
                    "all_projects": all_projects,
                    "current_project_id": project.id,
                    "form_data": {"name": name, "period_from": period_from, "period_to": period_to, "notes": notes},
                    "active_module": "rozliczenia",
                },
                db,
            )

    # Biezacy projekt musi byc w project_ids
    if project.id not in project_ids:
        flash(request, "Biezacy projekt musi byc wybrany na liscie projektow", "error")
        return await render_project_page(
            request,
            "dashboard/settlements/create.html",
            {
                "project": project,
                "all_projects": all_projects,
                "current_project_id": project.id,
                "form_data": {
                    "name": name,
                    "period_from": period_from,
                    "period_to": period_to,
                    "notes": notes,
                    "project_ids": project_ids,
                },
                "active_module": "rozliczenia",
            },
            db,
        )

    try:
        settlement = await create_settlement(
            db=db,
            user_id=user_id,
            name=name,
            period_from=period_from,
            period_to=period_to,
            project_ids=project_ids,
            notes=notes or None,
        )
    except ValueError as e:
        flash(request, str(e), "error")
        return await render_project_page(
            request,
            "dashboard/settlements/create.html",
            {
                "project": project,
                "all_projects": all_projects,
                "current_project_id": project.id,
                "form_data": {
                    "name": name,
                    "period_from": period_from,
                    "period_to": period_to,
                    "notes": notes,
                    "project_ids": project_ids,
                },
                "active_module": "rozliczenia",
            },
            db,
        )
    except HTTPException:
        raise

    flash(request, f"Utworzono ROZ-{settlement.number}", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement.id}", status_code=303)


@router.get("/{slug}/rozliczenia/{settlement_id}", response_class=HTMLResponse, response_model=None)
async def settlement_detail(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "read")

    settlement = await _get_settlement(settlement_id, project.id, db)

    # Render markdown dla notatek
    notes_html = render_markdown_html(settlement.notes) if settlement.notes else ""

    # Tickets z eager load .project -- tickety moga byc z roznych projektow
    ticket_ids = [t.id for t in settlement.tickets]
    tickets_with_projects: list[Ticket] = []
    if ticket_ids:
        tickets_result = await db.execute(select(Ticket).where(Ticket.id.in_(ticket_ids)).options(selectinload(Ticket.project)))
        tickets_with_projects = list(tickets_result.scalars().all())

    # Dostepne tickety do podpiecia -- wyszukiwanie ladowane asynchronicznie przez HTMX
    can_edit_tickets = settlement.status == "draft" and await check_permission(db, user_id, project.id, "rozliczenia", "write")

    return await render_project_page(
        request,
        "dashboard/settlements/detail.html",
        {
            "project": project,
            "settlement": settlement,
            "notes_html": notes_html,
            "tickets_with_projects": tickets_with_projects,
            "can_edit_tickets": can_edit_tickets,
            "active_module": "rozliczenia",
        },
        db,
    )


@router.get("/{slug}/rozliczenia/{settlement_id}/edit", response_class=HTMLResponse, response_model=None)
async def settlement_edit_form(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    settlement = await _get_settlement(settlement_id, project.id, db)

    if settlement.status != "draft":
        flash(request, "Rozliczenie mozna edytowac tylko w statusie draft", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    is_superuser = bool(request.session.get("is_superuser", False))
    all_projects = await _get_projects_with_write(user_id, db, is_superuser)

    # Pre-populate form_data z settlement
    selected_project_ids = [p.id for p in settlement.projects]
    form_data = {
        "name": settlement.name,
        "period_from": settlement.period_from,
        "period_to": settlement.period_to,
        "notes": settlement.notes or "",
        "project_ids": selected_project_ids,
    }

    return await render_project_page(
        request,
        "dashboard/settlements/edit.html",
        {
            "project": project,
            "settlement": settlement,
            "all_projects": all_projects,
            "current_project_id": project.id,
            "form_data": form_data,
            "active_module": "rozliczenia",
        },
        db,
    )


@router.post("/{slug}/rozliczenia/{settlement_id}/edit", response_class=HTMLResponse, response_model=None)
async def settlement_edit(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    name: Annotated[str, Form()],
    period_from: Annotated[date, Form()],
    period_to: Annotated[date, Form()],
    project_ids_raw: Annotated[list[str], Form(alias="project_ids")] = [],  # noqa: B006
    notes: Annotated[str, Form()] = "",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    settlement = await _get_settlement(settlement_id, project.id, db)

    if settlement.status != "draft":
        flash(request, "Rozliczenie mozna edytowac tylko w statusie draft", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    is_superuser = bool(request.session.get("is_superuser", False))
    all_projects = await _get_projects_with_write(user_id, db, is_superuser)

    # Parsuj project_ids
    project_ids: list[uuid.UUID] = []
    for raw in project_ids_raw:
        try:
            project_ids.append(uuid.UUID(raw))
        except ValueError:
            flash(request, f"Nieprawidlowe ID projektu: {raw}", "error")
            return await render_project_page(
                request,
                "dashboard/settlements/edit.html",
                {
                    "project": project,
                    "settlement": settlement,
                    "all_projects": all_projects,
                    "current_project_id": project.id,
                    "form_data": {"name": name, "period_from": period_from, "period_to": period_to, "notes": notes},
                    "active_module": "rozliczenia",
                },
                db,
            )

    # Biezacy projekt musi byc w project_ids
    if project.id not in project_ids:
        flash(request, "Biezacy projekt musi byc wybrany na liscie projektow", "error")
        return await render_project_page(
            request,
            "dashboard/settlements/edit.html",
            {
                "project": project,
                "settlement": settlement,
                "all_projects": all_projects,
                "current_project_id": project.id,
                "form_data": {
                    "name": name,
                    "period_from": period_from,
                    "period_to": period_to,
                    "notes": notes,
                    "project_ids": project_ids,
                },
                "active_module": "rozliczenia",
            },
            db,
        )

    try:
        await update_settlement(
            db=db,
            settlement=settlement,
            user_id=user_id,
            name=name,
            period_from=period_from,
            period_to=period_to,
            project_ids=project_ids,
            notes=notes or None,
        )
    except ValueError as e:
        flash(request, str(e), "error")
        # Po potencjalnym rollbacku re-query settlement
        settlement = await _get_settlement(settlement_id, project.id, db)
        return await render_project_page(
            request,
            "dashboard/settlements/edit.html",
            {
                "project": project,
                "settlement": settlement,
                "all_projects": all_projects,
                "current_project_id": project.id,
                "form_data": {
                    "name": name,
                    "period_from": period_from,
                    "period_to": period_to,
                    "notes": notes,
                    "project_ids": project_ids,
                },
                "active_module": "rozliczenia",
            },
            db,
        )
    except HTTPException:
        raise

    flash(request, "Zapisano zmiany", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)


@router.post("/{slug}/rozliczenia/{settlement_id}/status", response_model=None)
async def settlement_change_status(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Zmien status rozliczenia (draft<->sent<->paid)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    settlement = await _get_settlement(settlement_id, project.id, db)

    form = await request.form()
    new_status = str(form.get("new_status", "")).strip()

    try:
        await change_settlement_status(db, settlement, user_id, new_status)
        flash(request, f"Status rozliczenia zmieniony na: {new_status}", "success")
    except ValueError as e:
        flash(request, str(e), "error")
    except HTTPException:
        raise

    return RedirectResponse(
        url=f"/dashboard/{slug}/rozliczenia/{settlement_id}",
        status_code=303,
    )


@router.post("/{slug}/rozliczenia/{settlement_id}/delete", response_class=HTMLResponse, response_model=None)
async def settlement_delete(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "delete")

    settlement = await _get_settlement(settlement_id, project.id, db)

    try:
        await delete_settlement(db=db, settlement=settlement, user_id=user_id)
    except ValueError as e:
        flash(request, str(e), "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)
    except HTTPException:
        raise

    flash(request, "Rozliczenie usuniete", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/", status_code=303)


@router.post("/{slug}/rozliczenia/{settlement_id}/attachments", response_model=None)
async def settlement_upload_attachment(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    file: UploadFile = File(...),
    category: str = Form(...),
    state: str = Form("draft"),
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    settlement = await _get_settlement(settlement_id, project.id, db)

    file_bytes = await file.read()

    try:
        attachment = await upload_settlement_attachment(
            db=db,
            settlement=settlement,
            user_id=user_id,
            file_bytes=file_bytes,
            filename=file.filename or "file",
            mime_type=file.content_type,
            category=category,
            state=state,
        )
        await db.commit()
    except ValueError as e:
        await db.rollback()
        flash(request, str(e), "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)
    except HTTPException:
        raise

    flash(request, f"Dodano zalacznik: {attachment.filename}", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)


@router.get("/{slug}/rozliczenia/{settlement_id}/attachments/{attachment_id}", response_model=None)
async def settlement_download_attachment(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> Response | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "read")

    settlement = await _get_settlement(settlement_id, project.id, db)

    matching = [a for a in settlement.attachments if a.id == attachment_id]
    if not matching:
        raise HTTPException(status_code=404, detail="Zalacznik nie istnieje")
    attachment = matching[0]

    file_bytes, content_type = get_settlement_attachment_bytes(attachment)

    safe_filename = attachment.filename.replace('"', "_").replace("\\", "_")
    encoded = quote(safe_filename)
    headers = {
        "Content-Disposition": f"attachment; filename=\"{safe_filename}\"; filename*=UTF-8''{encoded}",
        "Content-Length": str(len(file_bytes)),
        "X-Content-Type-Options": "nosniff",
    }
    return Response(
        content=file_bytes,
        media_type=content_type or attachment.mime_type or "application/octet-stream",
        headers=headers,
    )


@router.post("/{slug}/rozliczenia/{settlement_id}/attachments/{attachment_id}/delete", response_model=None)
async def settlement_delete_attachment(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    attachment_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "delete")

    settlement = await _get_settlement(settlement_id, project.id, db)

    matching = [a for a in settlement.attachments if a.id == attachment_id]
    if not matching:
        raise HTTPException(status_code=404, detail="Zalacznik nie istnieje")
    attachment = matching[0]

    try:
        await delete_settlement_attachment(db=db, attachment=attachment, user_id=user_id)
        await db.commit()
    except ValueError as e:
        await db.rollback()
        flash(request, str(e), "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)
    except HTTPException:
        raise

    flash(request, "Zalacznik usuniety", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)


@router.get(
    "/{slug}/rozliczenia/{settlement_id}/tickets/search",
    response_class=HTMLResponse,
    response_model=None,
)
async def settlement_search_tickets(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    q: str = "",
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    """HTMX endpoint -- wyszukiwanie ticketow do podpiecia (max 20).

    Filtruje po numerze (np. "12" lub "MNX-12") i po fragmencie tytulu (ILIKE).
    Ogranicza do projektow powiazanych z rozliczeniem i wyklucza juz podpiete tickety.
    """
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    if not await check_permission(db, user_id, project.id, "rozliczenia", "write"):
        return HTMLResponse("", status_code=403)

    settlement = await _get_settlement(settlement_id, project.id, db)

    # Brak dostepu do dodawania gdy settlement nie jest w draft
    if settlement.status != "draft":
        return HTMLResponse("", status_code=200)

    settlement_project_ids = [p.id for p in settlement.projects]
    linked_ticket_ids = {t.id for t in settlement.tickets}

    results: list[Ticket] = []
    if settlement_project_ids:
        query = select(Ticket).options(selectinload(Ticket.project)).where(Ticket.project_id.in_(settlement_project_ids))
        if linked_ticket_ids:
            query = query.where(Ticket.id.not_in(linked_ticket_ids))

        q_trim = q.strip()
        if q_trim:
            # Obsluga wyszukiwania po numerze: "12" lub "MNX-12"
            candidate_num: int | None = None
            if q_trim.isdigit():
                candidate_num = int(q_trim)
            elif "-" in q_trim:
                parts = q_trim.rsplit("-", 1)
                if len(parts) == 2 and parts[1].isdigit():
                    candidate_num = int(parts[1])
            if candidate_num is not None:
                query = query.where(or_(Ticket.title.ilike(f"%{q_trim}%"), Ticket.number == candidate_num))
            else:
                query = query.where(Ticket.title.ilike(f"%{q_trim}%"))

        query = query.order_by(Ticket.project_id, Ticket.number).limit(20)
        result = await db.execute(query)
        results = list(result.scalars().all())

    return templates.TemplateResponse(
        request,
        "dashboard/settlements/_ticket_search_results.html",
        {
            "results": results,
            "slug": slug,
            "settlement_id": settlement_id,
            "query": q.strip(),
        },
    )


@router.post("/{slug}/rozliczenia/{settlement_id}/tickets/link", response_model=None)
async def settlement_link_ticket(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Podepnij ticket do rozliczenia (tylko draft, tylko projekty M2M, wymaga write)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    settlement = await _get_settlement(settlement_id, project.id, db)

    if settlement.status != "draft":
        flash(request, "Mozna podpiac ticket tylko do rozliczenia w statusie draft", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    form = await request.form()
    ticket_id_raw = str(form.get("ticket_id", "")).strip()
    try:
        ticket_uuid = uuid.UUID(ticket_id_raw)
    except ValueError:
        flash(request, "Nieprawidlowe ID ticketu", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    ticket_result = await db.execute(select(Ticket).options(selectinload(Ticket.project)).where(Ticket.id == ticket_uuid))
    ticket = ticket_result.scalar_one_or_none()
    if ticket is None:
        flash(request, "Ticket nie istnieje", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    try:
        await validate_settlement_ticket_link(db, settlement, ticket)
    except ValueError as e:
        flash(request, str(e), "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    # Dodaj tylko jesli jeszcze nie jest podpiete
    linked_ids = {t.id for t in settlement.tickets}
    if ticket.id not in linked_ids:
        settlement.tickets.append(ticket)
        await db.commit()
        flash(request, "Ticket zostal podpiety do rozliczenia", "success")
    else:
        flash(request, "Ticket jest juz podpiety do tego rozliczenia", "info")

    return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)


@router.post("/{slug}/rozliczenia/{settlement_id}/tickets/{ticket_id}/unlink", response_model=None)
async def settlement_unlink_ticket(
    request: Request,
    slug: str,
    settlement_id: uuid.UUID,
    ticket_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> RedirectResponse:
    """Odepnij ticket od rozliczenia (tylko draft, wymaga write)."""
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    project = await _get_project(slug, db)
    await require_permission(db, user_id, project.id, "rozliczenia", "write")

    settlement = await _get_settlement(settlement_id, project.id, db)

    if settlement.status != "draft":
        flash(request, "Mozna odpiac ticket tylko od rozliczenia w statusie draft", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    ticket_to_remove = next((t for t in settlement.tickets if t.id == ticket_id), None)
    if ticket_to_remove is None:
        flash(request, "Ticket nie jest podpiety do tego rozliczenia", "error")
        return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)

    settlement.tickets.remove(ticket_to_remove)
    await db.commit()
    flash(request, "Ticket zostal odpiety od rozliczenia", "success")
    return RedirectResponse(url=f"/dashboard/{slug}/rozliczenia/{settlement_id}", status_code=303)
