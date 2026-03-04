"""Modele OAuth 2.1 -- klienty, kody autoryzacji, tokeny dostepu i odswiezania."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import Boolean, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSON, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.user import User


class OAuthClient(Base):
    """Zarejestrowany klient OAuth (Dynamic Client Registration)."""

    __tablename__ = "oauth_clients"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    client_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    client_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    redirect_uris: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    grant_types: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    client_secret: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class OAuthAuthorizationCode(Base):
    """Kod autoryzacji OAuth (jednorazowy, krotki TTL)."""

    __tablename__ = "oauth_authorization_codes"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    redirect_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    code_challenge: Mapped[str] = mapped_column(String(255), nullable=False)
    code_challenge_method: Mapped[str] = mapped_column(String(10), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()


class OAuthAccessToken(Base):
    """Token dostepu OAuth (SHA256 hash w DB)."""

    __tablename__ = "oauth_access_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    scope: Mapped[str | None] = mapped_column(String(255), nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped[User] = relationship()


class OAuthRefreshToken(Base):
    """Token odswiezania OAuth (powiazany z access token)."""

    __tablename__ = "oauth_refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    token_hash: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    access_token_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("oauth_access_tokens.id"), nullable=False)
    client_id: Mapped[str] = mapped_column(String(255), nullable=False)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_revoked: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    access_token: Mapped[OAuthAccessToken] = relationship()
    user: Mapped[User] = relationship()
