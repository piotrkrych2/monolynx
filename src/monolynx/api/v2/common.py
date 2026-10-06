"""Wspolne helpery routerow domenowych API v2 (Projects, Scrum, Wiki).

Serwisy z MON-137/MON-139 sygnalizuja kazdy blad jednym typem - `ValueError` -
bo MCP zwraca go jako tekstowy blad toola bez kodu statusu. REST potrzebuje
rozroznienia 404/403/422, a sygnatur serwisow nie wolno zmieniac (wola je
takze MCP i dashboard), wiec mapowanie odbywa sie tutaj, po zamknietej liscie
komunikatow "zasob nie istnieje" wskazanego w SCIEZCE requestu.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.api.v2.errors import ApiV2Error
from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.schemas.api_v2.pagination import PaginatedResponse
from monolynx.services.permissions import check_permission

DEFAULT_PER_PAGE = 100
MAX_PER_PAGE = 100

# Komunikaty serwisow oznaczajace brak zasobu ze sciezki (ticket, sprint,
# strona wiki, kryterium, aktywny sprint dla burndown). Dopasowanie pelne,
# nie po fragmencie: "Wskazany bloker ... nie istnieje" dotyczy pola body
# (blocked_by_ids) i ma zostac 422, nie 404.
_NOT_FOUND_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"Ticket nie istnieje"),
    re.compile(r"Ticket '.*' nie istnieje w projekcie"),
    re.compile(r"Nieprawidlowy identyfikator ticketa: .*"),
    re.compile(r"Sprint nie istnieje"),
    re.compile(r"Sprint nie istnieje lub nie należy do projektu"),
    re.compile(r"Brak aktywnego sprintu dla projektu"),
    re.compile(r"Strona wiki nie istnieje"),
    re.compile(r"Wpis bloga nie istnieje"),
    re.compile(r"Kryterium akceptacji nie istnieje w tym tickecie"),
    re.compile(r"Nieprawidlowy format criterion_id \(oczekiwany UUID\)"),
)

_FORBIDDEN_PREFIX = "Brak uprawnienia"


def not_found(detail: str) -> ApiV2Error:
    return ApiV2Error(status=404, title="Not Found", detail=detail)


def service_error(exc: ValueError) -> ApiV2Error:
    """Zamien `ValueError` z serwisu na problem+json: 404 / 403 / 422.

    Uzycie: `except ValueError as exc: raise service_error(exc) from exc`.
    """
    message = str(exc)
    if any(pattern.fullmatch(message) for pattern in _NOT_FOUND_PATTERNS):
        return not_found(message)
    if message.startswith(_FORBIDDEN_PREFIX):
        return ApiV2Error(status=403, title="Forbidden", detail=message)
    return ApiV2Error(status=422, title="Unprocessable Entity", detail=message)


async def require_permission(db: AsyncSession, user: User, project: Project, module: str, action: str) -> None:
    """403 problem+json, gdy uzytkownik nie ma `module:action` w projekcie.

    Wolane w routerze dla operacji, ktorych funkcja serwisu nie sprawdza sama
    (brak usera w serwisie: start/complete sprintu, burndown, strony wiki,
    ustawienia, czlonkowie) oraz jako sprawdzenie scrum:read przed zapisem,
    gdy odpowiedz czyta zasob po commit. Pozostale uprawnienia sprawdzaja
    serwisy (`ensure_permission`), a ich ValueError wraca przez `service_error`.
    Tekst `detail` jest identyczny jak w `ensure_permission`.
    """
    if not await check_permission(db, user.id, project.id, module, action):
        raise ApiV2Error(status=403, title="Forbidden", detail=f"Brak uprawnienia {module}:{action}")


def paginate[T](items: Sequence[T], page: int, per_page: int) -> PaginatedResponse[T]:
    """Stronicuj liste zwrocona w calosci przez serwis (toole MCP bez paginacji).

    `total_pages` >= 1 jak w `services/tickets.py::list_tickets`, zeby CLI
    iterowalo po stronach tym samym warunkiem dla wszystkich list.
    """
    total = len(items)
    start = (page - 1) * per_page
    return PaginatedResponse[T](
        items=list(items[start : start + per_page]),
        page=page,
        per_page=per_page,
        total=total,
        total_pages=max(1, math.ceil(total / per_page)),
    )
