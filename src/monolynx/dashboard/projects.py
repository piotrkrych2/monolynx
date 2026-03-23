"""Dashboard -- zarzadzanie projektami (lista, tworzenie, setup guide)."""

from __future__ import annotations

import re
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.services.auth import get_current_user
from monolynx.services.project_stats import get_bulk_project_stats

from .helpers import SLUG_PATTERN, _get_user_id, templates

CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

_SORT_OPTIONS = {
    "name_asc",
    "name_desc",
    "activity_desc",
    "activity_asc",
}


@router.get("/", response_class=HTMLResponse, response_model=None)
async def project_list(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    # Query params
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except (ValueError, TypeError):
        page = 1
    search = request.query_params.get("search", "").strip()
    sort = request.query_params.get("sort", "activity_desc")
    if sort not in _SORT_OPTIONS:
        sort = "activity_desc"

    per_page = 10

    user = await get_current_user(request, db)
    if user and user.is_superuser:
        base_query = select(Project).where(Project.is_active.is_(True))
    else:
        base_query = (
            select(Project)
            .join(ProjectMember, ProjectMember.project_id == Project.id)
            .where(
                Project.is_active.is_(True),
                ProjectMember.user_id == user_id,
            )
        )

    # Filtr wyszukiwania
    if search:
        base_query = base_query.where(
            or_(
                Project.name.ilike(f"%{search}%"),
                Project.slug.ilike(f"%{search}%"),
            )
        )

    # Subquery last_activity z ticketów (potrzebna do sortowania i statystyk)
    last_activity_sq = select(Ticket.project_id, func.max(Ticket.updated_at).label("last_activity")).group_by(Ticket.project_id).subquery()

    # Sortowanie
    if sort == "name_asc":
        order_clause = Project.name.asc()
        sort_query = base_query.order_by(order_clause)
    elif sort == "name_desc":
        order_clause = Project.name.desc()
        sort_query = base_query.order_by(order_clause)
    elif sort == "activity_asc":
        sort_query = base_query.outerjoin(last_activity_sq, Project.id == last_activity_sq.c.project_id).order_by(
            last_activity_sq.c.last_activity.asc().nulls_last()
        )
    else:  # activity_desc (domyślne)
        sort_query = base_query.outerjoin(last_activity_sq, Project.id == last_activity_sq.c.project_id).order_by(
            last_activity_sq.c.last_activity.desc().nulls_last()
        )

    # Count total (na base_query bez sortowania)
    count_query = select(func.count()).select_from(base_query.subquery())
    total_count = (await db.execute(count_query)).scalar() or 0

    # Paginacja
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)

    result = await db.execute(sort_query.limit(per_page).offset((page - 1) * per_page))
    projects = result.scalars().all()

    # Bulk statystyki (max 5 queries)
    stats = await get_bulk_project_stats([p.id for p in projects], db)

    return templates.TemplateResponse(
        request,
        "dashboard/projects.html",
        {
            "projects": projects,
            "stats": stats,
            "page": page,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1,
            "search": search,
            "sort": sort,
        },
    )


@router.get("/create-project", response_class=HTMLResponse, response_model=None)
async def create_project_form(
    request: Request,
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    ctx = {"error": None, "name": "", "slug": "", "code": ""}
    return templates.TemplateResponse(request, "dashboard/create_project.html", ctx)


@router.post("/create-project", response_class=HTMLResponse, response_model=None)
async def create_project(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user_id = _get_user_id(request)
    if user_id is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    slug = str(form.get("slug", "")).strip()
    code = str(form.get("code", "")).strip().upper()

    if not name or not slug or not code:
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {"error": "Nazwa, slug i kod sa wymagane", "name": name, "slug": slug, "code": code},
        )

    if not SLUG_PATTERN.match(slug):
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {
                "error": "Slug moze zawierac tylko male litery, cyfry i myslniki",
                "name": name,
                "slug": slug,
                "code": code,
            },
        )

    if not CODE_PATTERN.match(code):
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {
                "error": "Kod musi miec 2-10 znakow: wielkie litery i cyfry, zaczynac sie od litery (np. PIM, PROJ2)",
                "name": name,
                "slug": slug,
                "code": code,
            },
        )

    project = Project(
        name=name,
        slug=slug,
        code=code,
        api_key=secrets.token_urlsafe(32),
    )
    db.add(project)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {
                "error": "Projekt z takim slugiem lub kodem juz istnieje",
                "name": name,
                "slug": slug,
                "code": code,
            },
        )

    # Dodaj twórce jako właściciela projektu
    member = ProjectMember(
        project_id=project.id,
        user_id=user_id,
        role="owner",
    )
    db.add(member)

    await db.commit()
    return RedirectResponse(url="/dashboard/", status_code=303)
