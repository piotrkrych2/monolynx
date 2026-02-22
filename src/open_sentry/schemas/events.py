"""Pydantic schemas dla Event Ingest API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StackFrame(BaseModel):
    filename: str = ""
    function: str = "?"
    lineno: int | None = None
    context_line: str | None = None
    pre_context: list[str] = Field(default_factory=list)
    post_context: list[str] = Field(default_factory=list)


class Stacktrace(BaseModel):
    frames: list[StackFrame] = Field(default_factory=list)


class ExceptionData(BaseModel):
    type: str = "UnknownError"
    value: str = ""
    module: str | None = None
    stacktrace: Stacktrace = Field(default_factory=Stacktrace)


class RequestData(BaseModel):
    url: str = ""
    method: str = ""
    headers: dict[str, str] = Field(default_factory=dict)
    query_string: str = ""
    data: dict[str, Any] | None = None
    body: str | None = None
    client_ip: str | None = None


class UserData(BaseModel):
    id: str | None = None
    username: str | None = None
    email: str | None = None
    ip_address: str | None = None


class ServerData(BaseModel):
    hostname: str | None = None
    os: str | None = None
    python_version: str | None = None
    django_version: str | None = None


class EventPayload(BaseModel):
    event_id: str | None = None
    timestamp: datetime | None = None
    platform: str = "python"
    level: str = "error"
    environment: str | None = None
    release: str | None = None
    exception: ExceptionData
    request: RequestData | None = None
    user: UserData | None = None
    server: ServerData | None = None
    fingerprint: str | None = None
    sdk: dict[str, str] | None = None
    message: str | None = None


class EventResponse(BaseModel):
    id: str
