"""API v2: szkielet routera REST z autoryzacja Bearer wspoldzielona z MCP.

Fundament pod endpointy domenowe Projects/Scrum/Wiki (MON-142). `router`
(prefix /api/v2) niesie placeholder korzenia (z i bez koncowego slasha),
wlasna dokumentacje OpenAPI/Swagger, dolaczony router `GET /me` (MON-125,
`api/v2/me.py`), routery domenowe (w tym odczyt grafu Polaczen, MON-248,
`api/v2/graph.py`) i catch-all 404; `register_api_v2(app)` rejestruje
middleware X-Request-Id oraz handlery bledow problem+json (RFC 7807) na
GLOWNEJ aplikacji FastAPI, ograniczone do sciezek /api/v2 (granica
sprawdzana przez `_is_v2_path`, nie goly `str.startswith`, zeby
"/api/v2foo" nie zostal pomylony z v2) -- dashboard i API v1 zachowuja swoj
obecny (FastAPI-owy) format bledow.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import NoReturn

from fastapi import APIRouter, FastAPI, Request
from fastapi.exception_handlers import http_exception_handler as _default_http_exception_handler
from fastapi.exception_handlers import request_validation_exception_handler as _default_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.openapi.utils import get_openapi
from pydantic import BaseModel
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from monolynx.api.v2.errors import ApiV2Error
from monolynx.api.v2.graph import router as graph_router
from monolynx.api.v2.me import router as me_router
from monolynx.api.v2.projects import router as projects_router
from monolynx.api.v2.sprints import router as sprints_router
from monolynx.api.v2.tickets import router as tickets_router
from monolynx.api.v2.wiki import router as wiki_router

_V2_PREFIX = "/api/v2"

router = APIRouter(prefix=_V2_PREFIX)


def _is_v2_path(path: str) -> bool:
    """True tylko gdy `path` JEST prefiksem v2 albo jego podsciezka.

    "/api/v2foo" zaczyna sie od "/api/v2" jako goly string, ale jest inna
    trasa (inny router z prefiksem "/api/v2foo") -- goly str.startswith
    dopisalby jej naglowek X-Request-Id i format problem+json, mimo ze
    nie jest to sciezka v2 (MON-140 iteracja 2).
    """
    return path == _V2_PREFIX or path.startswith(_V2_PREFIX + "/")


class _RootResponse(BaseModel):
    api: str


@router.get("/", response_model=_RootResponse)
@router.get("", include_in_schema=False, response_model=_RootResponse)
async def api_v2_root() -> _RootResponse:
    """Placeholder korzenia -- bez autoryzacji, dostepny z i bez koncowego
    slasha (bez sluga "" GET /api/v2 spadalby do mountu MCP na "/" i wracal
    401, bo catch-all "/{full_path:path}" wymaga segmentu po "/api/v2/")."""
    return _RootResponse(api="v2")


@router.get("/openapi.json", include_in_schema=False)
async def api_v2_openapi() -> dict[str, object]:
    """Schema OpenAPI zbudowana WYLACZNIE z tras `router` (bez dashboardu/v1)."""
    return get_openapi(title="Monolynx API v2", version="2.0.0", routes=router.routes)


@router.get("/docs", include_in_schema=False, response_class=HTMLResponse)
async def api_v2_docs() -> HTMLResponse:
    """Swagger UI wskazujacy na /api/v2/openapi.json."""
    return get_swagger_ui_html(openapi_url=f"{_V2_PREFIX}/openapi.json", title="Monolynx API v2 - Swagger UI")


# Dolaczone PRZED catch-allem (kolejnosc rejestracji tras Starlette): bez
# tego "/{full_path:path}" ponizej dopasowalby /api/v2/me i trasy domenowe
# pierwszy i zwrocil 404 zamiast wywolac wlasciwy handler.
router.include_router(me_router)
router.include_router(projects_router)
router.include_router(tickets_router)
router.include_router(sprints_router)
router.include_router(wiki_router)
router.include_router(graph_router)


# Catch-all MUSI byc rejestrowany jako OSTATNI w tym pliku: Router Starlette
# dopasowuje trasy w kolejnosci rejestracji, a "/{full_path:path}" dopasowuje
# kazda sciezke, w tym /openapi.json i /docs zdefiniowane wyzej.
#
# Bez tego routu nieznaleziona sciezka /api/v2/* nie wygenerowalaby 404 -- MCP
# jest zamontowany na "/" (empty-prefix Mount dopasowuje WSZYSTKO), wiec
# request przepadalby w mouncie MCP i wracal jako 401 (Bearer wymagany)
# zamiast 404 problem+json. HEAD i OPTIONS musza byc w methods -- bez nich
# Starlette widzi PARTIAL match (sciezka zgadza sie, metoda nie) i rzuca 405
# zamiast 404 problem+json (MON-140 iteracja 2).
@router.api_route(
    "/{full_path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
    include_in_schema=False,
    response_model=None,
)
async def api_v2_catch_all(full_path: str) -> NoReturn:
    raise ApiV2Error(status=404, title="Not Found", detail=f"Nieznana sciezka: {_V2_PREFIX}/{full_path}")


def _problem_response(
    request: Request,
    *,
    status: int,
    title: str,
    detail: str | None,
    type_: str = "about:blank",
    headers: Mapping[str, str] | None = None,
) -> JSONResponse:
    body = {"type": type_, "title": title, "status": status, "detail": detail, "instance": request.url.path}
    response = JSONResponse(content=body, status_code=status, media_type="application/problem+json")
    if headers:
        for key, value in headers.items():
            response.headers[key] = value
    return response


class _RequestIdMiddleware:
    """Middleware ASGI czysty (bez BaseHTTPMiddleware/@app.middleware("http")),
    bo MCP jest zamontowany na "/" i serwuje streamable HTTP/SSE -
    BaseHTTPMiddleware buforuje/owija CALY ruch (takze przez ten mount),
    co psuje streaming. Dziala tylko dla sciezek /api/v2 (przez
    `_is_v2_path`); naglowek dopisywany bezposrednio do wiadomosci ASGI
    `http.response.start`, wiec trafia takze do 401/404/422 z handlerow
    bledow (MON-140 iteracja 2).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not _is_v2_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        request_id = _extract_request_id(scope) or str(uuid.uuid4())

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message).append("X-Request-Id", request_id)
            await send(message)

        await self.app(scope, receive, send_wrapper)


def _extract_request_id(scope: Scope) -> str | None:
    headers: list[tuple[bytes, bytes]] = scope.get("headers", [])
    for key, value in headers:
        if key.lower() == b"x-request-id":
            return value.decode("latin-1")
    return None


def register_api_v2(app: FastAPI) -> None:
    """Zarejestruj middleware X-Request-Id i handlery problem+json dla /api/v2/*.

    Wywolywana raz na aplikacje (main.py::_register_routers oraz testy na malej
    aplikacji FastAPI() z wlasnym routerem) -- niezalezna od `router` zdefiniowanego
    w tym module, zeby dzialala bez rzeczywistych tras v2.
    """
    app.add_middleware(_RequestIdMiddleware)

    @app.exception_handler(ApiV2Error)
    async def _api_v2_error_handler(request: Request, exc: ApiV2Error) -> JSONResponse:
        return _problem_response(request, status=exc.status, title=exc.title, detail=exc.detail, type_=exc.type, headers=exc.headers)

    @app.exception_handler(StarletteHTTPException)
    async def _http_exception_handler(request: Request, exc: StarletteHTTPException) -> Response:
        if not _is_v2_path(request.url.path):
            return await _default_http_exception_handler(request, exc)
        title = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return _problem_response(request, status=exc.status_code, title=title, detail=title, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exception_handler(request: Request, exc: RequestValidationError) -> Response:
        if not _is_v2_path(request.url.path):
            return await _default_validation_exception_handler(request, exc)
        detail = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors())
        return _problem_response(request, status=422, title="Validation Error", detail=detail)
