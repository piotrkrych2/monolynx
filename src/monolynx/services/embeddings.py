"""Serwis embeddingow -- chunking, generowanie wektorow, wyszukiwanie semantyczne."""

from __future__ import annotations

import asyncio
import logging
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import tiktoken
from openai import OpenAI
from sqlalchemy import delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from monolynx.config import settings
from monolynx.models.wiki_embedding import WikiEmbedding

logger = logging.getLogger("monolynx.embeddings")

_executor = ThreadPoolExecutor(max_workers=2)
_openai_client: OpenAI | None = None


def _get_openai_client() -> OpenAI | None:
    """Zwroc singleton klienta OpenAI. None jesli klucz nie skonfigurowany."""
    global _openai_client
    if not settings.OPENAI_API_KEY:
        return None
    if _openai_client is None:
        _openai_client = OpenAI(api_key=settings.OPENAI_API_KEY)
    return _openai_client


def is_enabled() -> bool:
    """Czy embeddingi sa wlaczone (klucz OpenAI skonfigurowany)."""
    return bool(settings.OPENAI_API_KEY)


def chunk_text(
    raw: str,
    chunk_size: int | None = None,
    overlap: int | None = None,
) -> list[tuple[str, int]]:
    """Podziel tekst na chunki tokenowe. Zwraca list[(chunk_text, token_count)]."""
    if chunk_size is None:
        chunk_size = settings.EMBEDDING_CHUNK_SIZE
    if overlap is None:
        overlap = settings.EMBEDDING_CHUNK_OVERLAP

    enc = tiktoken.encoding_for_model("gpt-4o")
    tokens = enc.encode(raw)

    if not tokens:
        return []

    chunks: list[tuple[str, int]] = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunk_str = enc.decode(chunk_tokens)
        chunks.append((chunk_str, len(chunk_tokens)))
        if end >= len(tokens):
            break
        start += chunk_size - overlap

    return chunks


def _generate_embeddings_sync(texts: list[str]) -> list[list[float]] | None:
    """Generuj embeddingi synchronicznie (wywolywane w thread pool)."""
    client = _get_openai_client()
    if client is None:
        return None

    try:
        response = client.embeddings.create(
            model=settings.EMBEDDING_MODEL,
            input=texts,
            dimensions=settings.EMBEDDING_DIMENSIONS,
        )
        return [item.embedding for item in response.data]
    except Exception:
        logger.warning("Blad generowania embeddingow z OpenAI", exc_info=True)
        return None


async def generate_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Generuj embeddingi asynchronicznie -- deleguje do thread pool."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, _generate_embeddings_sync, texts)


async def update_page_embeddings(
    page_id: uuid.UUID,
    content: str,
    db: AsyncSession,
) -> None:
    """Usun stare embeddingi strony i wygeneruj nowe."""
    if not is_enabled():
        return

    # Usun stare
    await db.execute(delete(WikiEmbedding).where(WikiEmbedding.wiki_page_id == page_id))

    chunks = chunk_text(content)
    if not chunks:
        await db.commit()
        return

    chunk_texts = [c[0] for c in chunks]
    vectors = await generate_embeddings(chunk_texts)
    if vectors is None:
        await db.commit()
        return

    for i, ((chunk_str, token_count), vector) in enumerate(zip(chunks, vectors, strict=True)):
        embedding = WikiEmbedding(
            wiki_page_id=page_id,
            chunk_index=i,
            chunk_text=chunk_str,
            embedding=vector,
            token_count=token_count,
        )
        db.add(embedding)

    await db.commit()
    logger.info("Wygenerowano %d embeddingow dla strony %s", len(chunks), page_id)


async def search_wiki_pages(
    project_id: uuid.UUID,
    query: str,
    db: AsyncSession,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Wyszukaj strony wiki semantycznie -- zwraca posortowane wyniki."""
    if not is_enabled():
        return []

    query_vectors = await generate_embeddings([query])
    if query_vectors is None or not query_vectors:
        return []

    query_vector = query_vectors[0]
    vector_str = "[" + ",".join(str(v) for v in query_vector) + "]"

    sql = text("""
        SELECT DISTINCT ON (wp.id)
            wp.id, wp.title, wp.slug, we.chunk_text,
            1 - (we.embedding <=> CAST(:query_embedding AS vector)) AS similarity
        FROM wiki_embeddings we
        JOIN wiki_pages wp ON wp.id = we.wiki_page_id
        WHERE wp.project_id = :project_id
        ORDER BY wp.id, we.embedding <=> CAST(:query_embedding AS vector)
    """)

    result = await db.execute(sql, {"project_id": project_id, "query_embedding": vector_str})
    rows = result.fetchall()

    # Sortuj po similarity desc i ogranicz
    rows = sorted(rows, key=lambda r: r[4], reverse=True)[:limit]

    return [
        {
            "id": str(row[0]),
            "title": row[1],
            "slug": row[2],
            "snippet": row[3][:300],
            "similarity": round(float(row[4]), 4),
        }
        for row in rows
        if row[4] > 0.3  # filtruj slabe trafienia
    ]
