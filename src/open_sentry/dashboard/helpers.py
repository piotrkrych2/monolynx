"""Wspolne helpery dla modulu dashboard."""

from __future__ import annotations

import logging
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from open_sentry.services.sidebar import get_sidebar_badges

logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=str(Path(__file__).parent.parent / "templates"))

SLUG_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


def _get_user_id(request: Request) -> uuid.UUID | None:
    user_id = request.session.get("user_id")
    if user_id:
        return uuid.UUID(user_id)
    return None


def flash(request: Request, message: str, type: str = "success") -> None:
    """Add a flash message to the session for display on next page load."""
    request.session.setdefault("_flash_messages", []).append(
        {"message": message, "type": type}
    )


async def render_project_page(
    request: Request,
    template_name: str,
    context: dict[str, Any],
    db: AsyncSession,
) -> HTMLResponse:
    """Renderuj strone projektowa z badge'ami w sidebarze."""
    project = context.get("project")
    if project is not None:
        try:
            badges = await get_sidebar_badges(project.id, db)
            context["sidebar_badges"] = badges
        except Exception:
            logger.exception("Blad pobierania badge'ow sidebara")
    return templates.TemplateResponse(request, template_name, context)
