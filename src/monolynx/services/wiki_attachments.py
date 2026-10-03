"""Serwis zalacznikow wiki: zalaczniki stron oraz globalne pliki repozytorium wiki projektu."""

from __future__ import annotations

import asyncio
import base64
import os
import re
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.models.project import Project
from monolynx.models.user import User
from monolynx.models.wiki_attachment import WikiAttachment
from monolynx.models.wiki_file import WikiFile
from monolynx.models.wiki_page import WikiPage
from monolynx.services.minio_client import get_attachment, upload_object
from monolynx.services.permissions import ensure_permission

MAX_WIKI_UPLOAD_SIZE = 200 * 1024 * 1024

# Sygnatury funkcji MinIO wstrzykiwanych z mcp_server.py -- wrapper przekazuje jawnie swoje
# wlasne minio_upload_object/minio_get_attachment, bo tests/unit/test_mcp_server.py patchuje
# je pod nazwami monolynx.mcp_server.minio_*.
ObjectUploader = Callable[[str, bytes, str], object]
ObjectGetter = Callable[[str], tuple[bytes, str]]


def _resolve_uploader(injected: ObjectUploader | None) -> ObjectUploader:
    return injected if injected is not None else upload_object


def _resolve_getter(injected: ObjectGetter | None) -> ObjectGetter:
    return injected if injected is not None else get_attachment


async def _run_blocking(fn: Callable[[], Any]) -> Any:
    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as executor:
        return await loop.run_in_executor(executor, fn)


def _validate_filename(filename: str) -> str:
    if not filename or not filename.strip():
        raise ValueError("Nazwa pliku nie moze byc pusta")
    name = os.path.basename(filename.strip())
    name = re.sub(r'[\x00-\x1f"\\]', "_", name)
    if not name or name in (".", ".."):
        raise ValueError("Nieprawidlowa nazwa pliku")
    if len(name) > 255:
        raise ValueError("Nazwa pliku nie moze przekraczac 255 znakow")
    return name


def _decode_base64(file_base64: str) -> bytes:
    if not file_base64 or not file_base64.strip():
        raise ValueError("Zawartosc pliku (file_base64) nie moze byc pusta")
    try:
        data = base64.b64decode(file_base64, validate=True)
    except Exception as exc:
        raise ValueError("Nieprawidlowy format base64") from exc
    if len(data) > MAX_WIKI_UPLOAD_SIZE:
        raise ValueError(f"Plik za duzy: max 200MB, otrzymano {len(data) / 1024 / 1024:.1f}MB")
    return data


def _validate_mime_type(mime_type: str | None) -> str:
    if mime_type is None:
        return "application/octet-stream"
    if not re.match(r"^[a-z]+/[a-z0-9.+\-]+$", mime_type):
        raise ValueError("Nieprawidlowy format mime_type")
    return mime_type


def _build_storage_path(prefix: str, filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    now = datetime.now(UTC)
    date_prefix = f"{now.year}/{now.month:02d}/{now.day:02d}"
    return f"{prefix}/{date_prefix}/{uuid.uuid4().hex}.{ext}"


def _encode_content(data: bytes, content_type: str) -> str:
    encoded = base64.b64encode(data).decode("ascii")
    if content_type.startswith("image/"):
        return f"data:{content_type};base64,{encoded}"
    return encoded


# --- Zalaczniki do stron wiki ---


@dataclass
class WikiAttachmentContent:
    attachment: WikiAttachment
    content_base64: str


async def get_wiki_attachment(
    db: AsyncSession,
    user: User,
    project: Project,
    page_id: str,
    attachment_filename: str,
    *,
    get_object: ObjectGetter | None = None,
) -> WikiAttachmentContent:
    """Zawartosc najnowszego (created_at desc) zalacznika o danej nazwie na stronie projektu. Wymaga wiki:read."""
    await ensure_permission(db, user.id, project.id, "wiki", "read")
    result = await db.execute(
        select(WikiAttachment)
        .join(WikiPage, WikiAttachment.wiki_page_id == WikiPage.id)
        .where(
            WikiAttachment.filename == attachment_filename,
            WikiPage.id == uuid.UUID(page_id),
            WikiPage.project_id == project.id,
        )
        .order_by(WikiAttachment.created_at.desc())
        .limit(1)
    )
    attachment = result.scalar_one_or_none()
    if attachment is None:
        raise ValueError("Zalacznik nie istnieje")

    fn = _resolve_getter(get_object)
    data, content_type = await _run_blocking(lambda: fn(attachment.storage_path))

    return WikiAttachmentContent(attachment=attachment, content_base64=_encode_content(data, content_type))


async def add_wiki_page_attachment(
    db: AsyncSession,
    user: User,
    project: Project,
    page_id: str,
    file_base64: str,
    filename: str,
    mime_type: str | None = None,
    *,
    upload_object: ObjectUploader | None = None,
) -> WikiAttachment:
    """Dodaj zalacznik do strony wiki. Wymaga wiki:write. Plik zakodowany w base64, limit MAX_WIKI_UPLOAD_SIZE.

    Walidacje (nazwa, base64, rozmiar, mime_type) wspolne z add_wiki_file -- patrz prywatne
    helpery modulu powyzej.
    """
    await ensure_permission(db, user.id, project.id, "wiki", "write")
    filename = _validate_filename(filename)
    file_bytes = _decode_base64(file_base64)
    mime_type = _validate_mime_type(mime_type)

    page_result = await db.execute(select(WikiPage).where(WikiPage.id == uuid.UUID(page_id), WikiPage.project_id == project.id))
    if page_result.scalar_one_or_none() is None:
        raise ValueError("Strona wiki nie istnieje")

    storage_path = _build_storage_path(f"{project.slug}/wiki-attachments/{page_id}", filename)
    fn = _resolve_uploader(upload_object)
    await _run_blocking(lambda: fn(storage_path, file_bytes, mime_type))

    attachment = WikiAttachment(
        wiki_page_id=uuid.UUID(page_id),
        filename=filename,
        storage_path=storage_path,
        mime_type=mime_type,
        size=len(file_bytes),
        created_via_ai=True,
    )
    db.add(attachment)
    await db.commit()
    await db.refresh(attachment)
    return attachment


async def list_wiki_page_attachments(db: AsyncSession, user: User, project: Project, page_id: str) -> list[WikiAttachment]:
    """Lista zalacznikow strony wiki (bez tresci), rosnaco wg daty utworzenia. Wymaga wiki:read."""
    await ensure_permission(db, user.id, project.id, "wiki", "read")
    page_result = await db.execute(select(WikiPage).where(WikiPage.id == uuid.UUID(page_id), WikiPage.project_id == project.id))
    if page_result.scalar_one_or_none() is None:
        raise ValueError("Strona wiki nie istnieje")

    result = await db.execute(select(WikiAttachment).where(WikiAttachment.wiki_page_id == uuid.UUID(page_id)).order_by(WikiAttachment.created_at))
    return list(result.scalars().all())


# --- Globalne pliki wiki ---


@dataclass
class WikiFileContent:
    wiki_file: WikiFile
    content_base64: str


async def add_wiki_file(
    db: AsyncSession,
    user: User,
    project: Project,
    file_base64: str,
    filename: str,
    mime_type: str | None = None,
    description: str | None = None,
    *,
    upload_object: ObjectUploader | None = None,
) -> WikiFile:
    """Dodaj globalny plik do repozytorium wiki projektu. Wymaga wiki:write. Walidacje wspolne z add_wiki_page_attachment."""
    await ensure_permission(db, user.id, project.id, "wiki", "write")
    filename = _validate_filename(filename)
    file_bytes = _decode_base64(file_base64)
    mime_type = _validate_mime_type(mime_type)

    storage_path = _build_storage_path(f"{project.slug}/wiki-files", filename)
    fn = _resolve_uploader(upload_object)
    await _run_blocking(lambda: fn(storage_path, file_bytes, mime_type))

    wiki_file = WikiFile(
        project_id=project.id,
        filename=filename,
        storage_path=storage_path,
        mime_type=mime_type,
        size=len(file_bytes),
        description=description,
        created_via_ai=True,
    )
    db.add(wiki_file)
    await db.commit()
    await db.refresh(wiki_file)
    return wiki_file


async def get_wiki_file(
    db: AsyncSession,
    user: User,
    project: Project,
    file_id: str,
    *,
    get_object: ObjectGetter | None = None,
) -> WikiFileContent:
    """Zawartosc globalnego pliku wiki (base64). Wymaga wiki:read."""
    await ensure_permission(db, user.id, project.id, "wiki", "read")
    result = await db.execute(select(WikiFile).where(WikiFile.id == uuid.UUID(file_id), WikiFile.project_id == project.id))
    wiki_file = result.scalar_one_or_none()
    if wiki_file is None:
        raise ValueError("Plik nie istnieje")

    fn = _resolve_getter(get_object)
    data, content_type = await _run_blocking(lambda: fn(wiki_file.storage_path))

    return WikiFileContent(wiki_file=wiki_file, content_base64=_encode_content(data, content_type))


async def update_wiki_file(
    db: AsyncSession,
    user: User,
    project: Project,
    file_id: str,
    description: str | None = None,
    filename: str | None = None,
) -> WikiFile:
    """Aktualizuj opis lub nazwe globalnego pliku wiki. Wymaga wiki:write. Podaj tylko pola do zmiany."""
    await ensure_permission(db, user.id, project.id, "wiki", "write")
    result = await db.execute(select(WikiFile).where(WikiFile.id == uuid.UUID(file_id), WikiFile.project_id == project.id))
    wiki_file = result.scalar_one_or_none()
    if wiki_file is None:
        raise ValueError("Plik nie istnieje")

    if description is not None:
        wiki_file.description = description or None
    if filename is not None:
        safe = re.sub(r"[^\w.\-]", "_", filename.strip())[:255]
        if safe:
            wiki_file.filename = safe

    await db.commit()
    await db.refresh(wiki_file)
    return wiki_file


async def list_wiki_files(db: AsyncSession, user: User, project: Project) -> list[WikiFile]:
    """Lista globalnych plikow projektu, malejaco wg daty utworzenia. Wymaga wiki:read."""
    await ensure_permission(db, user.id, project.id, "wiki", "read")
    result = await db.execute(select(WikiFile).where(WikiFile.project_id == project.id).order_by(WikiFile.created_at.desc()))
    return list(result.scalars().all())
