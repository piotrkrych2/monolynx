"""Dashboard -- zarzadzanie projektami (lista, tworzenie ze sprawdzaniem dostepnosci sluga i kodu, setup guide)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.database import get_db
from monolynx.models.project import Project
from monolynx.models.project_member import ProjectMember
from monolynx.models.ticket import Ticket
from monolynx.services.project_stats import get_bulk_project_stats
from monolynx.services.projects import suggest_available_code, suggest_available_slug

from .helpers import CODE_PATTERN, SLUG_PATTERN, get_session_user, templates

__all__ = ["CODE_PATTERN", "router"]

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
    user = await get_session_user(request, db)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=303)
    user_id = user.id

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

    if user.is_superuser:
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
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    if await get_session_user(request, db) is None:
        return RedirectResponse(url="/auth/login", status_code=303)

    ctx = {"error": None, "name": "", "slug": "", "code": ""}
    return templates.TemplateResponse(request, "dashboard/create_project.html", ctx)


@router.get("/create-project/check", response_model=None)
async def check_project_availability(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> JSONResponse:
    if await get_session_user(request, db) is None:
        return JSONResponse({"error": "Unauthorized"}, status_code=401)

    result: dict[str, dict[str, object]] = {}

    slug = request.query_params.get("slug", "").strip().lower()
    if slug:
        if SLUG_PATTERN.match(slug) and len(slug) <= 255:
            available, suggestion = await suggest_available_slug(db, slug)
            result["slug"] = {"value": slug, "valid": True, "available": available, "suggestion": suggestion}
        else:
            result["slug"] = {"value": slug, "valid": False, "available": False, "suggestion": None}

    code = request.query_params.get("code", "").strip().upper()
    if code:
        if CODE_PATTERN.match(code):
            available, suggestion = await suggest_available_code(db, code)
            result["code"] = {"value": code, "valid": True, "available": available, "suggestion": suggestion}
        else:
            result["code"] = {"value": code, "valid": False, "available": False, "suggestion": None}

    return JSONResponse(result)


@router.post("/create-project", response_class=HTMLResponse, response_model=None)
async def create_project(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> HTMLResponse | RedirectResponse:
    user = await get_session_user(request, db)
    if user is None:
        return RedirectResponse(url="/auth/login", status_code=303)
    user_id = user.id

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

    slug_free, slug_suggestion = await suggest_available_slug(db, slug)
    code_free, code_suggestion = await suggest_available_code(db, code)
    if not slug_free or not code_free:
        messages: list[str] = []
        if not slug_free:
            messages.append(f"Slug „{slug}” jest zajęty, proponujemy „{slug_suggestion}”." if slug_suggestion else f"Slug „{slug}” jest zajęty.")
            slug = slug_suggestion or slug
        if not code_free:
            messages.append(f"Kod „{code}” jest zajęty, proponujemy „{code_suggestion}”." if code_suggestion else f"Kod „{code}” jest zajęty.")
            code = code_suggestion or code
        return templates.TemplateResponse(
            request,
            "dashboard/create_project.html",
            {"error": " ".join(messages), "name": name, "slug": slug, "code": code},
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
