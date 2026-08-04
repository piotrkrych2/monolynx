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
from monolynx.constants import PERMISSION_ACTIONS, PERMISSION_MODULES
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


def _get_user_id(request: Request) -> uuid.UUID | None:
    user_id = request.session.get("user_id")
    if user_id:
        return uuid.UUID(user_id)
    return None


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

        # Pobierz uprawnienia uzytkownika dla tego projektu
        # Format: {module: {action: True}} -- uzyty w szablonach przez permissions.get(module, {}).get(action)
        try:
            user_id_str = request.session.get("user_id")
            is_superuser = request.session.get("is_superuser", False)
            if user_id_str:
                if is_superuser:
                    raw_perms: dict[str, list[str]] = {m: list(PERMISSION_ACTIONS) for m in PERMISSION_MODULES}
                else:
                    from monolynx.services.permissions import get_user_permissions

                    user_id = uuid.UUID(user_id_str)
                    raw_perms = await get_user_permissions(db, user_id, project.id)
                # Konwertuj {module: [actions]} -> {module: {action: True}}
                context["permissions"] = {module: {action: True for action in actions} for module, actions in raw_perms.items()}
            else:
                context["permissions"] = {}
        except Exception:
            logger.exception("Blad pobierania uprawnien uzytkownika")
            context["permissions"] = {}

    return templates.TemplateResponse(request, template_name, context)
