"""Schematy Pydantic dla modułu wiki."""

from __future__ import annotations

import uuid

from pydantic import BaseModel


class WikiBacklinkResponse(BaseModel):
    """Odpowiedź z danymi backlinku wiki."""

    source_page_id: uuid.UUID
    target_page_id: uuid.UUID
    anchor_text: str | None = None
