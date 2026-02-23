"""Pydantic schemas dla Issues API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel


class IssueListItem(BaseModel):
    id: uuid.UUID
    title: str
    culprit: str | None
    level: str
    status: str
    event_count: int
    first_seen: datetime
    last_seen: datetime

    model_config = {"from_attributes": True}


class EventDetail(BaseModel):
    id: uuid.UUID
    timestamp: datetime
    exception: dict[str, Any]
    request_data: dict[str, Any] | None
    environment: dict[str, Any] | None

    model_config = {"from_attributes": True}


class IssueDetail(BaseModel):
    id: uuid.UUID
    title: str
    culprit: str | None
    level: str
    status: str
    event_count: int
    first_seen: datetime
    last_seen: datetime
    events: list[EventDetail]

    model_config = {"from_attributes": True}


class StatusUpdate(BaseModel):
    status: str
