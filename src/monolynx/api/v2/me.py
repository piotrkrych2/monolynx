"""API v2: GET /api/v2/me -- tozsamosc zalogowanego uzytkownika (MON-125).

Chroniony przez `get_api_user` (naglowek Authorization: Bearer, wspoldzielony
z MCP -- OAuth access token i legacy osk_*). Zwraca id i email uzytkownika,
do ktorego nalezy token; brak naglowka albo nieprawidlowy/nieaktywny token
konczy sie 401 problem+json z `get_api_user` (patrz `api/v2/deps.py`), zanim
handler tego modulu w ogole sie wykona.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from monolynx.api.v2.deps import get_api_user
from monolynx.models.user import User

router = APIRouter()


class MeResponse(BaseModel):
    id: uuid.UUID
    email: str


@router.get("/me", response_model=MeResponse)
async def get_me(user: User = Depends(get_api_user)) -> MeResponse:
    return MeResponse(id=user.id, email=user.email)
