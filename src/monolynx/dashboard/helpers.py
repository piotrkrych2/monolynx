"""Wspolne helpery dla modulu dashboard."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.models.user import User
from monolynx.services.auth import get_current_user
from monolynx.services.permissions import get_user_permissions
from monolynx.services.sidebar import get_sidebar_badges

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))
templates.env.globals["ga_measurement_id"] = settings.GA_MEASUREMENT_ID

_app_tz = ZoneInfo(settings.APP_TIMEZONE)


def _localtime(value: datetime | date | None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """Filtr Jinja2: konwertuje datetime UTC na lokalny czas i formatuje."""
    if value is None:
        return ""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.strftime(fmt)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(_app_tz).strftime(fmt)


templates.env.filters["localtime"] = _localtime

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


def _get_user_id(request: Request) -> uuid.UUID | None:
    user_id = request.session.get("user_id")
    if user_id:
        return uuid.UUID(user_id)
    return None


async def get_session_user(request: Request, db: AsyncSession) -> User | None:
    """Zwraca aktywnego uzytkownika z bazy dla biezacej sesji.

    Brak rekordu albo `is_active == False` czyszcza sesje, wiec cookie przestaje dzialac.
    """
    user = await get_current_user(request, db)
    if user is None and request.session.get("user_id"):
        request.session.clear()
    return user


def flash(request: Request, message: str, type: str = "success") -> None:
    """Add a flash message to the session for display on next page load."""
    request.session.setdefault("_flash_messages", []).append({"message": message, "type": type})


async def render_project_page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
    db: AsyncSession,
) -> HTMLResponse:
    """Renderuj strone projektowa z badge'ami w sidebarze i uprawnieniami."""
    project = context.get("project")
    if project is not None:
        try:
            badges = await get_sidebar_badges(project.id, db)
            context["sidebar_badges"] = badges
        except Exception:
            logger.exception("Blad pobierania badge'ow sidebara")

        # Pobierz uprawnienia uzytkownika dla tego projektu (superuser z bazy obsluguje get_user_permissions)
        # Format: {module: {action: True}} -- uzyty w szablonach przez permissions.get(module, {}).get(action)
        try:
            user_id_str = request.session.get("user_id")
            if user_id_str:
                raw_perms = await get_user_permissions(db, uuid.UUID(user_id_str), project.id)
                context["permissions"] = {module: {action: True for action in actions} for module, actions in raw_perms.items()}
            else:
                context["permissions"] = {}
        except Exception:
            logger.exception("Blad pobierania uprawnien uzytkownika")
            context["permissions"] = {}

    return templates.TemplateResponse(request, template_name, context)
