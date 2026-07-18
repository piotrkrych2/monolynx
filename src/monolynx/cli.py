"""CLI do zarzadzania Monolynx -- uruchamiany przez python -m monolynx.cli."""

from __future__ import annotations

import asyncio
import getpass
import sys

from sqlalchemy.exc import IntegrityError

from monolynx.database import async_session_factory
from monolynx.models.user import User
from monolynx.services.auth import hash_password

MIN_PASSWORD_LENGTH = 8


async def createsuperuser() -> None:
    email = input("Email: ").strip()
    if not email:
        print("Blad: email nie moze byc pusty.")
        sys.exit(1)

    first_name = input("Imie (opcjonalne): ").strip()
    last_name = input("Nazwisko (opcjonalne): ").strip()

    password = getpass.getpass("Haslo: ")
    password_confirm = getpass.getpass("Potwierdz haslo: ")

    if password != password_confirm:
        print("Blad: hasla nie sa zgodne.")
        sys.exit(1)

    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"Blad: haslo musi miec minimum {MIN_PASSWORD_LENGTH} znakow.")
        sys.exit(1)

    async with async_session_factory() as session:
        user = User(
            email=email,
            password_hash=hash_password(password),
            first_name=first_name,
            last_name=last_name,
            is_superuser=True,
        )
        session.add(user)
        try:
            await session.commit()
        except IntegrityError:
            print(f"Blad: uzytkownik z emailem {email} juz istnieje.")
            sys.exit(1)

    print(f"Utworzono superusera: {email}")


async def backfill_backlinks_cmd() -> None:
    from monolynx.services.wiki import backfill_backlinks

    async with async_session_factory() as session:
        count = await backfill_backlinks(session)
    print(f"Gotowe! Przetworzono stron: {count}")


async def backfill_embeddings_cmd() -> None:
    from sqlalchemy import select, text

    from monolynx.models.wiki_page import WikiPage
    from monolynx.services.embeddings import update_page_embeddings
    from monolynx.services.wiki import get_page_content

    async with async_session_factory() as session:
        pages = list((await session.execute(select(WikiPage))).scalars().all())
        for page in pages:
            print(f"Generuje embeddingi: {page.title}")
            await update_page_embeddings(page.id, get_page_content(page), session)
        count = (await session.execute(text("SELECT count(*) FROM wiki_embeddings"))).scalar()
    print(f"Gotowe! Laczna liczba embeddingow: {count}")


async def clear_all_graphs_cmd() -> None:
    """Zaoraj grafy Neo4j WSZYSTKICH projektow (rollout graphify, MON-121)."""
    from sqlalchemy import select

    from monolynx.models.project import Project
    from monolynx.services import graph

    confirm = input("Ta operacja NIEODWRACALNIE usunie grafy WSZYSTKICH projektow. Wpisz TAK aby kontynuowac: ").strip()
    if confirm != "TAK":
        print("Przerwano - nic nie usunieto.")
        return

    await graph.init_driver()
    if not graph.is_enabled() or graph._driver is None:
        print("Graf Neo4j niedostepny (ENABLE_GRAPH_DB=false lub brak polaczenia) - nic do zrobienia.")
        return
    try:
        async with async_session_factory() as session:
            # celowo bez filtra is_active -- pelne zaoranie lapie tez osierocone
            # wezly projektow nieaktywnych
            projects = list((await session.execute(select(Project))).scalars().all())

        total_nodes = 0
        total_edges = 0
        for project in projects:
            result = await graph.replace_graph(project.id, [], [], clear_first=True)
            deleted_nodes = result["deleted_nodes"]
            deleted_edges = result["deleted_edges"]
            total_nodes += deleted_nodes
            total_edges += deleted_edges
            marker = "" if project.is_active else " (nieaktywny)"
            print(f"{project.slug}{marker}: usunieto {deleted_nodes} node'ow / {deleted_edges} krawedzi")

        print(f"Gotowe! Lacznie usunieto {total_nodes} node'ow i {total_edges} krawedzi z {len(projects)} projektow.")
    finally:
        await graph.close_driver()


COMMANDS = {
    "createsuperuser": createsuperuser,
    "backfill-backlinks": backfill_backlinks_cmd,
    "backfill-embeddings": backfill_embeddings_cmd,
    "clear-all-graphs": clear_all_graphs_cmd,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        available = ", ".join(COMMANDS)
        print(f"Uzycie: python -m monolynx.cli <komenda>\nDostepne komendy: {available}")
        sys.exit(1)

    asyncio.run(COMMANDS[sys.argv[1]]())


if __name__ == "__main__":
    main()
