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


COMMANDS = {
    "createsuperuser": createsuperuser,
}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        available = ", ".join(COMMANDS)
        print(f"Uzycie: python -m monolynx.cli <komenda>\nDostepne komendy: {available}")
        sys.exit(1)

    asyncio.run(COMMANDS[sys.argv[1]]())


if __name__ == "__main__":
    main()
