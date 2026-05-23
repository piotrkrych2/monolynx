"""Serwis wiki -- CRUD stron, drzewo, rendering markdown."""

from __future__ import annotations

import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import markdown
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from monolynx.models.project import Project
from monolynx.models.wiki_backlink import WikiBacklink
from monolynx.models.wiki_page import WikiPage
from monolynx.services.minio_client import delete_object, get_markdown, upload_markdown

logger = logging.getLogger("monolynx.wiki")

# Slugi zarezerwowane dla stron systemowych wiki - nie mogą być tworzone przez użytkowników ani MCP
RESERVED_SLUGS: frozenset[str] = frozenset({"wiki-index", "wiki-log", "wiki-schema"})


def generate_slug(title: str) -> str:
    """Generuj slug z tytulu strony."""
    slug = title.lower().strip()
    slug = re.sub(r"[ąà]", "a", slug)
    slug = re.sub(r"[ćč]", "c", slug)
    slug = re.sub(r"[ęè]", "e", slug)
    slug = re.sub(r"[łl]", "l", slug)
    slug = re.sub(r"[ńñ]", "n", slug)
    slug = re.sub(r"[óò]", "o", slug)
    slug = re.sub(r"[śš]", "s", slug)
    slug = re.sub(r"[źżž]", "z", slug)
    slug = re.sub(r"[^a-z0-9\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    slug = slug.strip("-")
    return slug or "strona"


async def _ensure_unique_slug(project_id: uuid.UUID, slug: str, db: AsyncSession, exclude_id: uuid.UUID | None = None) -> str:
    """Upewnij sie, ze slug jest unikalny w projekcie. Dodaje suffix jesli trzeba."""
    base_slug = slug
    counter = 1
    while True:
        conditions: list[Any] = [WikiPage.project_id == project_id, WikiPage.slug == slug]
        if exclude_id:
            conditions.append(WikiPage.id != exclude_id)
        result = await db.execute(select(WikiPage.id).where(*conditions).limit(1))
        if result.scalar_one_or_none() is None:
            return slug
        slug = f"{base_slug}-{counter}"
        counter += 1


def render_markdown_html(raw: str) -> str:
    """Renderuj markdown do HTML."""
    result: str = markdown.markdown(
        raw,
        extensions=["fenced_code", "tables", "toc", "nl2br", "sane_lists"],
    )
    return result


# Wzorzec wikilink [[slug]] lub [[uuid]]
_WIKILINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
# Wzorzec markdown [text](target) - wyciągamy ostatni segment ścieżki gdy target wygląda jak slug lub URL wiki
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
# Segment wyglądający jak slug lub UUID (bez http/https/mailto)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def extract_wiki_links(content: str) -> dict[str, str | None]:
    """Wyodrębnij referencje do innych stron wiki z treści markdown.

    Zwraca słownik ref -> anchor_text. Przy duplikacie referencji wygrywa
    pierwszy napotkany wpis (wikilink przed linkiem markdown).

    Obsługuje dwa formaty:
    - [[slug]] lub [[uuid]] - format wikilink (kanoniczny); anchor = slug/uuid
    - [tekst](target) gdzie target wskazuje stronę wiki - anchor = tekst linku
    """
    ref_to_anchor: dict[str, str | None] = {}

    for match in _WIKILINK_RE.finditer(content):
        ref = match.group(1).strip()
        if ref and ref not in ref_to_anchor:
            ref_to_anchor[ref] = ref  # anchor dla [[slug]] to sam slug

    for match in _MD_LINK_RE.finditer(content):
        target = match.group(2).strip()
        # Pomiń linki zewnętrzne i zakotwiczenia
        if target.startswith(("http://", "https://", "mailto:", "#")):
            continue
        # Wyciągnij ostatni segment ścieżki
        segment = target.rstrip("/").rsplit("/", 1)[-1]
        # Akceptuj tylko gdy wygląda jak slug albo UUID
        if segment and (_SLUG_RE.match(segment) or _UUID_RE.match(segment)) and segment not in ref_to_anchor:
            anchor = match.group(1).strip() or None
            ref_to_anchor[segment] = anchor

    return ref_to_anchor


async def sync_backlinks(*, page: WikiPage, content: str, db: AsyncSession) -> None:
    """Zsynchronizuj backlinki dla strony wiki.

    Resolwuje referencje do istniejących WikiPage w obrębie page.project_id
    (po slug LUB po id), usuwa stare backlinki źródłowe, tworzy nowe.
    Nierozwiązane referencje (dead linki) są ignorowane.
    """
    ref_to_anchor = extract_wiki_links(content)

    if not ref_to_anchor:
        await db.execute(delete(WikiBacklink).where(WikiBacklink.source_page_id == page.id))
        await db.commit()
        return

    # Rozdziel referencje na UUID i slug - jedno zapytanie do bazy dla obu
    uuid_refs: list[uuid.UUID] = []
    slug_refs: list[str] = []
    for ref in ref_to_anchor:
        if _UUID_RE.match(ref):
            try:
                uuid_refs.append(uuid.UUID(ref))
            except ValueError:
                slug_refs.append(ref)
        else:
            slug_refs.append(ref)

    resolved_targets: dict[uuid.UUID, str | None] = {}  # target_id -> anchor_text

    conditions = []
    if uuid_refs:
        conditions.append(WikiPage.id.in_(uuid_refs))
    if slug_refs:
        conditions.append(WikiPage.slug.in_(slug_refs))

    if conditions:
        result = await db.execute(
            select(WikiPage.id, WikiPage.slug).where(
                WikiPage.project_id == page.project_id,
                or_(*conditions),
            )
        )
        for row in result.all():
            target_id: uuid.UUID = row.id
            target_slug: str = row.slug
            if target_id == page.id:
                # Pomiń self-link
                continue
            # Anchor: sprawdź po UUID (jako string) i po slug
            anchor = ref_to_anchor.get(str(target_id)) or ref_to_anchor.get(target_slug)
            if target_id not in resolved_targets:
                resolved_targets[target_id] = anchor

    # Usuń stare backlinki tej strony jako źródła
    await db.execute(delete(WikiBacklink).where(WikiBacklink.source_page_id == page.id))

    # Utwórz nowe
    for target_id, anchor in resolved_targets.items():
        db.add(
            WikiBacklink(
                source_page_id=page.id,
                target_page_id=target_id,
                anchor_text=anchor,
            )
        )

    await db.commit()


def is_wiki_llm_enabled(project: Project) -> bool:
    """Sprawdź czy LLM Wiki jest włączone dla projektu."""
    return bool(project.wiki_llm_enabled)


async def ensure_system_page(
    *,
    project: Project,
    slug: str,
    title: str,
    default_content: str,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> WikiPage:
    """Pobierz lub utwórz stronę systemową wiki (pomija walidację RESERVED_SLUGS).

    Strony systemowe (wiki-index, wiki-log, wiki-schema) są zarządzane wewnętrznie,
    dlatego ten helper omija walidację slugów zarezerwowanych w create_wiki_page.
    """
    result = await db.execute(select(WikiPage).where(WikiPage.project_id == project.id, WikiPage.slug == slug))
    existing = result.scalar_one_or_none()
    if existing is not None:
        return existing

    page_id = uuid.uuid4()
    minio_path = upload_markdown(project.slug, page_id, default_content)

    page = WikiPage(
        id=page_id,
        project_id=project.id,
        parent_id=None,
        title=title,
        slug=slug,
        position=0,
        minio_path=minio_path,
        is_ai_touched=True,
        created_by_id=user_id,
        last_edited_by_id=user_id,
    )
    db.add(page)
    await db.commit()
    await db.refresh(page)
    return page


async def get_backlinks(page_id: uuid.UUID, db: AsyncSession) -> list[WikiBacklink]:
    """Backlinki przychodzące - strony wskazujące na tę stronę (target_page_id == page_id)."""
    result = await db.execute(select(WikiBacklink).options(selectinload(WikiBacklink.source_page)).where(WikiBacklink.target_page_id == page_id))
    return list(result.scalars().all())


async def get_outlinks(page_id: uuid.UUID, db: AsyncSession) -> list[WikiBacklink]:
    """Outlinki wychodzące - strony na które wskazuje ta strona (source_page_id == page_id)."""
    result = await db.execute(select(WikiBacklink).options(selectinload(WikiBacklink.target_page)).where(WikiBacklink.source_page_id == page_id))
    return list(result.scalars().all())


async def regenerate_index(*, project: Project, user_id: uuid.UUID, db: AsyncSession) -> tuple[WikiPage, int]:
    """Przebuduj stronę wiki-index - katalog wszystkich stron projektu.

    Wyklucza strony systemowe (RESERVED_SLUGS) z katalogu.
    Dla każdej strony zawiera: tytuł (link), 1-zdaniowe summary, liczba backlinków.
    Format linku: [[slug]] - obsługiwany przez extract_wiki_links jako wewnętrzny.

    Zwraca (index_page, liczba_skatalogowanych_stron) - caller nie musi robić osobnego COUNT.
    """
    result = await db.execute(
        select(WikiPage, func.count(WikiBacklink.id).label("backlink_count"))
        .outerjoin(WikiBacklink, WikiBacklink.target_page_id == WikiPage.id)
        .where(
            WikiPage.project_id == project.id,
            WikiPage.slug.notin_(RESERVED_SLUGS),
        )
        .group_by(WikiPage.id)
        .order_by(WikiPage.title)
    )
    rows = result.all()
    page_count = len(rows)

    lines: list[str] = [
        "# Indeks stron wiki",
        "",
        f"*Wygenerowano automatycznie: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M')} UTC*",
        "",
        "| Strona | Opis | Backlinki |",
        "|--------|------|-----------|",
    ]

    for page, backlink_count in rows:
        # 1-zdaniowe summary: pierwsza niepusta linia niebędąca nagłówkiem
        try:
            raw_content = get_markdown(page.minio_path)
            summary = _extract_summary(raw_content)
        except Exception:
            summary = ""

        # Link w formacie [[slug]] - rozpoznawany przez extract_wiki_links
        link = f"[[{page.slug}]]"
        summary_cell = summary.replace("|", "&#124;")
        lines.append(f"| {link} | {summary_cell} | {backlink_count} |")

    content = "\n".join(lines)
    index_page = await ensure_system_page(
        project=project,
        slug="wiki-index",
        title="Indeks Wiki",
        default_content=content,
        user_id=user_id,
        db=db,
    )
    await svc_update_system_page(page=index_page, project=project, content=content, user_id=user_id, db=db)
    return index_page, page_count


def _extract_summary(content: str) -> str:
    """Wyciągnij pierwszą niepustą linię niebędącą nagłówkiem markdown."""
    for line in content.splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            # Ogranicz do 200 znaków żeby tabela była czytelna
            return stripped[:200]
    return ""


async def svc_update_system_page(
    *,
    page: WikiPage,
    project: Project,
    content: str,
    user_id: uuid.UUID,
    db: AsyncSession,
) -> WikiPage:
    """Wewnętrzny helper: zaktualizuj treść strony systemowej (MinIO + DB commit).

    Używany przez regenerate_index i append_log - omija pełną logikę update_wiki_page
    (slug rename, embeddings) dla wydajności przy stronach systemowych.
    """
    upload_markdown(project.slug, page.id, content)
    page.last_edited_by_id = user_id
    page.is_ai_touched = True
    await db.commit()
    await db.refresh(page)
    return page


async def append_log(*, project: Project, entry: str, user_id: uuid.UUID, db: AsyncSession) -> WikiPage:
    """Dopisz wpis do dziennika wiki-log (append-only).

    Format wpisu: - [YYYY-MM-DD HH:MM] <entry>
    Nie nadpisuje istniejących wpisów.
    """
    log_page = await ensure_system_page(
        project=project,
        slug="wiki-log",
        title="Dziennik Wiki",
        default_content="# Dziennik Wiki\n\n",
        user_id=user_id,
        db=db,
    )

    try:
        current_content = get_markdown(log_page.minio_path)
    except Exception:
        current_content = "# Dziennik Wiki\n\n"

    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M")
    new_line = f"- [{timestamp}] {entry}"
    updated_content = current_content.rstrip("\n") + "\n" + new_line + "\n"

    await svc_update_system_page(page=log_page, project=project, content=updated_content, user_id=user_id, db=db)
    return log_page


async def create_wiki_page(
    *,
    project_id: uuid.UUID,
    project_slug: str,
    title: str,
    content: str,
    user_id: uuid.UUID,
    parent_id: uuid.UUID | None = None,
    position: int = 0,
    is_ai: bool = False,
    db: AsyncSession,
) -> WikiPage:
    """Utworz strone wiki -- zapis metadanych w DB, tresc w MinIO."""
    slug = generate_slug(title)

    # Blokada slugów systemowych - użytkownicy/MCP nie mogą tworzyć stron o zarezerwowanych slugach
    if slug in RESERVED_SLUGS:
        raise ValueError(f"Slug '{slug}' jest zarezerwowany dla systemu wiki")

    slug = await _ensure_unique_slug(project_id, slug, db)

    page_id = uuid.uuid4()
    minio_path = upload_markdown(project_slug, page_id, content)

    page = WikiPage(
        id=page_id,
        project_id=project_id,
        parent_id=parent_id,
        title=title.strip(),
        slug=slug,
        position=position,
        minio_path=minio_path,
        is_ai_touched=is_ai,
        created_by_id=user_id,
        last_edited_by_id=user_id,
    )
    db.add(page)
    await db.commit()
    await db.refresh(page)

    # Best-effort embedding generation
    try:
        from monolynx.services.embeddings import update_page_embeddings

        await update_page_embeddings(page.id, content, db)
    except Exception:
        logger.warning("Nie udalo sie wygenerowac embeddingow dla strony %s", page.id)

    # Indeks backlinków - best-effort (nie blokuje zapisu strony)
    try:
        await sync_backlinks(page=page, content=content, db=db)
    except Exception:
        logger.warning("Nie udalo sie zsynchronizowac backlinkow dla strony %s", page.id)

    return page


async def update_wiki_page(
    *,
    page: WikiPage,
    project_slug: str,
    title: str | None = None,
    content: str | None = None,
    position: int | None = None,
    user_id: uuid.UUID,
    is_ai: bool = False,
    db: AsyncSession,
) -> WikiPage:
    """Aktualizuj strone wiki."""
    if title is not None and title.strip() != page.title:
        page.title = title.strip()
        new_slug = generate_slug(title)
        page.slug = await _ensure_unique_slug(page.project_id, new_slug, db, exclude_id=page.id)

    if content is not None:
        upload_markdown(project_slug, page.id, content)

    if position is not None:
        page.position = position

    page.last_edited_by_id = user_id
    if is_ai:
        page.is_ai_touched = True

    await db.commit()
    await db.refresh(page)

    # Best-effort embedding update
    if content is not None:
        try:
            from monolynx.services.embeddings import update_page_embeddings

            await update_page_embeddings(page.id, content, db)
        except Exception:
            logger.warning("Nie udalo sie zaktualizowac embeddingow dla strony %s", page.id)

        # Indeks backlinków - best-effort, tylko gdy content się zmienił
        try:
            await sync_backlinks(page=page, content=content, db=db)
        except Exception:
            logger.warning("Nie udalo sie zsynchronizowac backlinkow dla strony %s", page.id)

    return page


async def delete_wiki_page(page: WikiPage, db: AsyncSession) -> None:
    """Usun strone wiki wraz z potomkami. Usuwa pliki z MinIO."""
    descendants = await _collect_descendants(page.id, db)
    all_pages = [page, *descendants]

    for p in all_pages:
        delete_object(p.minio_path)

    await db.delete(page)
    await db.commit()


async def _collect_descendants(page_id: uuid.UUID, db: AsyncSession) -> list[WikiPage]:
    """Zbierz wszystkie strony potomne (rekurencyjnie)."""
    result = await db.execute(select(WikiPage).where(WikiPage.parent_id == page_id))
    children = list(result.scalars().all())
    descendants = list(children)
    for child in children:
        descendants.extend(await _collect_descendants(child.id, db))
    return descendants


async def get_page_tree(project_id: uuid.UUID, db: AsyncSession) -> list[dict[str, Any]]:
    """Pobierz drzewo stron wiki jako zagniezdzona liste."""
    result = await db.execute(
        select(WikiPage)
        .options(selectinload(WikiPage.created_by), selectinload(WikiPage.last_edited_by))
        .where(WikiPage.project_id == project_id)
        .order_by(WikiPage.position, WikiPage.title)
    )
    all_pages = list(result.scalars().all())

    pages_by_parent: dict[uuid.UUID | None, list[WikiPage]] = {}
    for p in all_pages:
        pages_by_parent.setdefault(p.parent_id, []).append(p)

    def _build_tree(parent_id: uuid.UUID | None) -> list[dict[str, Any]]:
        children = pages_by_parent.get(parent_id, [])
        return [
            {
                "page": page,
                "children": _build_tree(page.id),
            }
            for page in children
        ]

    return _build_tree(None)


async def get_breadcrumbs(page: WikiPage, db: AsyncSession) -> list[WikiPage]:
    """Zbuduj breadcrumbs od roota do aktualnej strony."""
    crumbs: list[WikiPage] = [page]
    current = page
    while current.parent_id is not None:
        result = await db.execute(select(WikiPage).where(WikiPage.id == current.parent_id))
        parent = result.scalar_one_or_none()
        if parent is None:
            break
        crumbs.insert(0, parent)
        current = parent
    return crumbs


def get_page_content(page: WikiPage) -> str:
    """Pobierz tresc markdown strony z MinIO."""
    return get_markdown(page.minio_path)


async def backfill_backlinks(db: AsyncSession) -> int:
    """Wygeneruj backlinki dla wszystkich istniejących stron wiki.

    Zwraca liczbę przetworzonych stron.
    """
    result = await db.execute(select(WikiPage))
    pages = list(result.scalars().all())
    for page in pages:
        content = get_markdown(page.minio_path)
        await sync_backlinks(page=page, content=content, db=db)
    return len(pages)
