"""Klient MinIO -- operacje na plikach wiki i zalacznikach."""

from __future__ import annotations

import io
import logging
import uuid

from minio import Minio

from monolynx.config import settings

logger = logging.getLogger("monolynx.minio")

_client: Minio | None = None


def get_minio_client() -> Minio:
    """Zwroc singleton klienta MinIO."""
    global _client
    if _client is None:
        _client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_USE_SSL,
        )
    return _client


def ensure_bucket() -> None:
    """Utworz bucket jesli nie istnieje. Wywolywane przy starcie aplikacji."""
    client = get_minio_client()
    bucket = settings.MINIO_BUCKET
    try:
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)
            logger.info("Utworzono bucket MinIO: %s", bucket)
        else:
            logger.debug("Bucket MinIO istnieje: %s", bucket)
    except Exception:
        logger.exception("Blad tworzenia bucketu MinIO: %s", bucket)


def upload_markdown(project_slug: str, page_id: uuid.UUID, content: str) -> str:
    """Upload treści markdown do MinIO. Zwraca sciezke obiektu."""
    client = get_minio_client()
    object_path = f"{project_slug}/{page_id}.md"
    data = content.encode("utf-8")
    client.put_object(
        settings.MINIO_BUCKET,
        object_path,
        io.BytesIO(data),
        length=len(data),
        content_type="text/markdown; charset=utf-8",
    )
    return object_path


def get_markdown(minio_path: str) -> str:
    """Pobierz tresc markdown z MinIO."""
    client = get_minio_client()
    response = client.get_object(settings.MINIO_BUCKET, minio_path)
    try:
        return response.read().decode("utf-8")
    finally:
        response.close()
        response.release_conn()


def delete_object(minio_path: str) -> None:
    """Usun obiekt z MinIO."""
    client = get_minio_client()
    try:
        client.remove_object(settings.MINIO_BUCKET, minio_path)
    except Exception:
        logger.exception("Blad usuwania obiektu MinIO: %s", minio_path)


def upload_attachment(project_slug: str, filename: str, data: bytes, content_type: str) -> str:
    """Upload zalacznika (obrazek) do MinIO. Zwraca sciezke obiektu."""
    client = get_minio_client()
    ext = filename.rsplit(".", 1)[-1] if "." in filename else "bin"
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    object_path = f"{project_slug}/attachments/{unique_name}"
    client.put_object(
        settings.MINIO_BUCKET,
        object_path,
        io.BytesIO(data),
        length=len(data),
        content_type=content_type,
    )
    return object_path


def get_attachment(minio_path: str) -> tuple[bytes, str]:
    """Pobierz zalacznik z MinIO. Zwraca (dane, content_type)."""
    client = get_minio_client()
    stat = client.stat_object(settings.MINIO_BUCKET, minio_path)
    response = client.get_object(settings.MINIO_BUCKET, minio_path)
    try:
        data = response.read()
    finally:
        response.close()
        response.release_conn()
    return data, stat.content_type or "application/octet-stream"
