"""Modele Pipeline, PipelineStep, PipelineJob -- modul Pipelines."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from monolynx.models.base import Base

if TYPE_CHECKING:
    from monolynx.models.project import Project
    from monolynx.models.sprint import Sprint
    from monolynx.models.ticket import Ticket
    from monolynx.models.user import User
    from monolynx.models.wiki_page import WikiPage


class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("projects.id"), nullable=False, index=True)
    pipeline_type: Mapped[str] = mapped_column(String(32), nullable=False)
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tickets.id"), nullable=True)
    sprint_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sprints.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created", index=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    triggered_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    meta: Mapped[dict[str, Any]] = mapped_column("meta", JSONB, server_default=text("'{}'::jsonb"), nullable=False)

    project: Mapped[Project] = relationship()
    ticket: Mapped[Ticket | None] = relationship(foreign_keys=[ticket_id])
    sprint: Mapped[Sprint | None] = relationship(foreign_keys=[sprint_id])
    triggered_by_user: Mapped[User | None] = relationship(foreign_keys=[triggered_by])
    steps: Mapped[list[PipelineStep]] = relationship(
        back_populates="pipeline",
        cascade="all, delete-orphan",
        order_by="PipelineStep.position",
    )


class PipelineStep(Base):
    __tablename__ = "pipeline_steps"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    pipeline_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="pending")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    pipeline: Mapped[Pipeline] = relationship(back_populates="steps")
    jobs: Mapped[list[PipelineJob]] = relationship(
        back_populates="step",
        cascade="all, delete-orphan",
        order_by="PipelineJob.created_at",
    )


class PipelineJob(Base):
    __tablename__ = "pipeline_jobs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    step_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pipeline_steps.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    agent_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="created")
    attempt: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    wiki_page_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("wiki_pages.id", ondelete="SET NULL"), nullable=True)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    step: Mapped[PipelineStep] = relationship(back_populates="jobs")
    wiki_page: Mapped[WikiPage | None] = relationship(foreign_keys=[wiki_page_id])
