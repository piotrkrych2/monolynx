"""Testy jednostkowe -- CLI (main, createsuperuser, backfill-backlinks, backfill-embeddings)."""

from __future__ import annotations

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError

from monolynx.cli import (
    COMMANDS,
    MIN_PASSWORD_LENGTH,
    backfill_backlinks_cmd,
    backfill_embeddings_cmd,
    createsuperuser,
    main,
)


@pytest.mark.unit
class TestCliMain:
    """Testy funkcji main() -- parsowanie argumentow CLI."""

    def test_no_args_exits_with_code_1(self):
        """Brak argumentow -> SystemExit(1)."""
        with patch("sys.argv", ["cli"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_invalid_command_exits_with_code_1(self):
        """Nieznana komenda -> SystemExit(1)."""
        with patch("sys.argv", ["cli", "invalid_command"]):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1

    def test_invalid_command_prints_usage(self, capsys):
        """Nieznana komenda wyswietla dostepne komendy."""
        with patch("sys.argv", ["cli", "nonexistent"]), pytest.raises(SystemExit):
            main()
        captured = capsys.readouterr()
        assert "createsuperuser" in captured.out
        assert "Dostepne komendy" in captured.out

    def test_no_args_prints_usage(self, capsys):
        """Brak argumentow wyswietla dostepne komendy."""
        with patch("sys.argv", ["cli"]), pytest.raises(SystemExit):
            main()
        captured = capsys.readouterr()
        assert "Uzycie:" in captured.out
        assert "createsuperuser" in captured.out

    @patch("monolynx.cli.asyncio.run")
    def test_valid_command_calls_asyncio_run(self, mock_run):
        """Poprawna komenda wywoluje asyncio.run z odpowiednia coroutine."""
        with patch("sys.argv", ["cli", "createsuperuser"]):
            main()
        mock_run.assert_called_once()


@pytest.mark.unit
class TestCliCommands:
    """Testy slownika COMMANDS."""

    def test_commands_contains_createsuperuser(self):
        """COMMANDS zawiera klucz 'createsuperuser'."""
        assert "createsuperuser" in COMMANDS

    def test_createsuperuser_is_callable(self):
        """Wartosc 'createsuperuser' w COMMANDS jest callable."""
        assert callable(COMMANDS["createsuperuser"])

    def test_commands_createsuperuser_points_to_function(self):
        """COMMANDS['createsuperuser'] wskazuje na funkcje createsuperuser."""
        assert COMMANDS["createsuperuser"] is createsuperuser


@pytest.mark.unit
class TestMinPasswordLength:
    """Testy stalej MIN_PASSWORD_LENGTH."""

    def test_min_password_length_is_8(self):
        assert MIN_PASSWORD_LENGTH == 8


@pytest.mark.unit
class TestCreateSuperuser:
    """Testy funkcji createsuperuser -- tworzenie administratora."""

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_empty_email_exits(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Pusty email -> SystemExit(1)."""
        mock_input.return_value = ""

        with pytest.raises(SystemExit) as exc_info:
            await createsuperuser()
        assert exc_info.value.code == 1

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_whitespace_only_email_exits(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Email ze samych bialych znakow -> SystemExit(1)."""
        mock_input.return_value = "   "

        with pytest.raises(SystemExit) as exc_info:
            await createsuperuser()
        assert exc_info.value.code == 1

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_password_mismatch_exits(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Rozne hasla -> SystemExit(1)."""
        mock_input.side_effect = ["admin@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["password123", "different456"]

        with pytest.raises(SystemExit) as exc_info:
            await createsuperuser()
        assert exc_info.value.code == 1

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_password_mismatch_prints_error(self, mock_input, mock_getpass, mock_hash, mock_factory, capsys):
        """Rozne hasla -> komunikat o niezgodnosci."""
        mock_input.side_effect = ["admin@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["password123", "different456"]

        with pytest.raises(SystemExit):
            await createsuperuser()

        captured = capsys.readouterr()
        assert "hasla nie sa zgodne" in captured.out

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_short_password_exits(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Haslo krotsze niz MIN_PASSWORD_LENGTH -> SystemExit(1)."""
        mock_input.side_effect = ["admin@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["short", "short"]

        with pytest.raises(SystemExit) as exc_info:
            await createsuperuser()
        assert exc_info.value.code == 1

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_short_password_prints_error(self, mock_input, mock_getpass, mock_hash, mock_factory, capsys):
        """Haslo krotsze niz MIN_PASSWORD_LENGTH -> komunikat."""
        mock_input.side_effect = ["admin@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["short", "short"]

        with pytest.raises(SystemExit):
            await createsuperuser()

        captured = capsys.readouterr()
        assert f"minimum {MIN_PASSWORD_LENGTH}" in captured.out

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_password_exactly_min_length_succeeds(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Haslo dokladnie MIN_PASSWORD_LENGTH znakow -> sukces."""
        mock_input.side_effect = ["admin@example.com", "Jan", "Kowalski"]
        exact_pw = "a" * MIN_PASSWORD_LENGTH
        mock_getpass.side_effect = [exact_pw, exact_pw]
        mock_hash.return_value = "hashed"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        await createsuperuser()

        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_successful_creation(self, mock_input, mock_getpass, mock_hash, mock_factory, capsys):
        """Poprawne dane -> tworzenie superusera i komunikat sukcesu."""
        mock_input.side_effect = ["admin@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["securepassword123", "securepassword123"]
        mock_hash.return_value = "hashed_password"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        await createsuperuser()

        mock_hash.assert_called_once_with("securepassword123")
        mock_session.add.assert_called_once()
        mock_session.commit.assert_awaited_once()

        captured = capsys.readouterr()
        assert "admin@example.com" in captured.out
        assert "Utworzono superusera" in captured.out

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_user_model_created_with_correct_fields(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """User jest tworzony z poprawnymi polami (email, first_name, last_name, is_superuser)."""
        mock_input.side_effect = ["admin@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["securepass123", "securepass123"]
        mock_hash.return_value = "hashed"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        with patch("monolynx.cli.User") as mock_user_cls:
            mock_user = MagicMock()
            mock_user_cls.return_value = mock_user

            await createsuperuser()

            mock_user_cls.assert_called_once_with(
                email="admin@example.com",
                password_hash="hashed",
                first_name="Jan",
                last_name="Kowalski",
                is_superuser=True,
            )
            mock_session.add.assert_called_once_with(mock_user)

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_optional_names_can_be_empty(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Imie i nazwisko sa opcjonalne -- moga byc puste."""
        mock_input.side_effect = ["admin@example.com", "", ""]
        mock_getpass.side_effect = ["securepass123", "securepass123"]
        mock_hash.return_value = "hashed"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        with patch("monolynx.cli.User") as mock_user_cls:
            mock_user = MagicMock()
            mock_user_cls.return_value = mock_user

            await createsuperuser()

            mock_user_cls.assert_called_once_with(
                email="admin@example.com",
                password_hash="hashed",
                first_name="",
                last_name="",
                is_superuser=True,
            )

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_duplicate_email_exits(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Duplikat emaila (IntegrityError) -> SystemExit(1)."""
        mock_input.side_effect = ["existing@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["securepass123", "securepass123"]
        mock_hash.return_value = "hashed"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit.side_effect = IntegrityError(
            statement="INSERT INTO users ...",
            params={},
            orig=Exception("duplicate key"),
        )
        mock_factory.return_value = mock_session

        with pytest.raises(SystemExit) as exc_info:
            await createsuperuser()
        assert exc_info.value.code == 1

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_duplicate_email_prints_error(self, mock_input, mock_getpass, mock_hash, mock_factory, capsys):
        """Duplikat emaila -> komunikat o istniejacym uzytkowniku."""
        mock_input.side_effect = ["existing@example.com", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["securepass123", "securepass123"]
        mock_hash.return_value = "hashed"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.commit.side_effect = IntegrityError(
            statement="INSERT INTO users ...",
            params={},
            orig=Exception("duplicate key"),
        )
        mock_factory.return_value = mock_session

        with pytest.raises(SystemExit):
            await createsuperuser()

        captured = capsys.readouterr()
        assert "existing@example.com" in captured.out
        assert "juz istnieje" in captured.out

    @patch("monolynx.cli.async_session_factory")
    @patch("monolynx.cli.hash_password")
    @patch("getpass.getpass")
    @patch("builtins.input")
    async def test_email_is_stripped(self, mock_input, mock_getpass, mock_hash, mock_factory):
        """Email jest stripowany z bialych znakow."""
        mock_input.side_effect = ["  admin@example.com  ", "Jan", "Kowalski"]
        mock_getpass.side_effect = ["securepass123", "securepass123"]
        mock_hash.return_value = "hashed"

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        with patch("monolynx.cli.User") as mock_user_cls:
            mock_user = MagicMock()
            mock_user_cls.return_value = mock_user

            await createsuperuser()

            call_kwargs = mock_user_cls.call_args[1]
            assert call_kwargs["email"] == "admin@example.com"


@pytest.mark.unit
class TestBackfillBacklinksCmd:
    """Testy komendy backfill-backlinks -- odtworzenie backlinkow wiki."""

    @patch("monolynx.cli.async_session_factory")
    async def test_prints_count_of_processed_pages(self, mock_factory, capsys):
        """Wywoluje backfill_backlinks i wypisuje liczbe przetworzonych stron."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        with patch("monolynx.services.wiki.backfill_backlinks", new=AsyncMock(return_value=7)) as mock_backfill:
            await backfill_backlinks_cmd()

        mock_backfill.assert_awaited_once_with(mock_session)
        captured = capsys.readouterr()
        assert "Przetworzono stron: 7" in captured.out

    @patch("monolynx.cli.async_session_factory")
    async def test_zero_pages_processed(self, mock_factory, capsys):
        """Zero stron do przetworzenia -- nadal wypisuje komunikat sukcesu."""
        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_factory.return_value = mock_session

        with patch("monolynx.services.wiki.backfill_backlinks", new=AsyncMock(return_value=0)):
            await backfill_backlinks_cmd()

        captured = capsys.readouterr()
        assert "Przetworzono stron: 0" in captured.out


@pytest.mark.unit
class TestBackfillEmbeddingsCmd:
    """Testy komendy backfill-embeddings -- generowanie embeddingow dla stron wiki."""

    @patch("monolynx.cli.async_session_factory")
    async def test_no_pages_skips_update_and_prints_count(self, mock_factory, capsys):
        """Brak stron -- update_page_embeddings nie jest wywolywane, liczba embeddingow wypisana."""
        result_pages = MagicMock()
        result_pages.scalars.return_value.all.return_value = []
        result_count = MagicMock()
        result_count.scalar.return_value = 0

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=[result_pages, result_count])
        mock_factory.return_value = mock_session

        with patch("monolynx.services.embeddings.update_page_embeddings", new=AsyncMock()) as mock_update:
            await backfill_embeddings_cmd()

        mock_update.assert_not_awaited()
        captured = capsys.readouterr()
        assert "Gotowe! Laczna liczba embeddingow: 0" in captured.out

    @patch("monolynx.cli.async_session_factory")
    async def test_generates_embeddings_for_each_page(self, mock_factory, capsys):
        """Kazda strona wiki -> jedno wywolanie update_page_embeddings, wypisuje tytul."""
        page1 = MagicMock(id="id-1", title="Strona pierwsza")
        page2 = MagicMock(id="id-2", title="Strona druga")

        result_pages = MagicMock()
        result_pages.scalars.return_value.all.return_value = [page1, page2]
        result_count = MagicMock()
        result_count.scalar.return_value = 42

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)
        mock_session.execute = AsyncMock(side_effect=[result_pages, result_count])
        mock_factory.return_value = mock_session

        with (
            patch("monolynx.services.embeddings.update_page_embeddings", new=AsyncMock()) as mock_update,
            patch("monolynx.services.wiki.get_page_content", side_effect=["tresc 1", "tresc 2"]) as mock_content,
        ):
            await backfill_embeddings_cmd()

        assert mock_update.await_count == 2
        assert mock_content.call_count == 2
        captured = capsys.readouterr()
        assert "Strona pierwsza" in captured.out
        assert "Strona druga" in captured.out
        assert "Gotowe! Laczna liczba embeddingow: 42" in captured.out


@pytest.mark.unit
class TestCliBackfillCommandsRegression:
    """Regresja: komendy backfill musza pozostac bezargumentowe i zarejestrowane w COMMANDS.

    Patrz .claude/rules/makefile-cli.md -- Makefile nie moze zawierac logiki,
    komendy CLI musza byc `async def` bez argumentow zeby dzialaly przez
    `python -m monolynx.cli <command>`.
    """

    def test_commands_contains_backfill_backlinks(self):
        assert "backfill-backlinks" in COMMANDS

    def test_commands_contains_backfill_embeddings(self):
        assert "backfill-embeddings" in COMMANDS

    def test_commands_backfill_backlinks_points_to_function(self):
        assert COMMANDS["backfill-backlinks"] is backfill_backlinks_cmd

    def test_commands_backfill_embeddings_points_to_function(self):
        assert COMMANDS["backfill-embeddings"] is backfill_embeddings_cmd

    def test_backfill_backlinks_cmd_takes_no_arguments(self):
        """Funkcja musi byc wywolywalna bez argumentow (asyncio.run(COMMANDS[cmd]()))."""
        sig = inspect.signature(backfill_backlinks_cmd)
        assert len(sig.parameters) == 0

    def test_backfill_embeddings_cmd_takes_no_arguments(self):
        """Funkcja musi byc wywolywalna bez argumentow (asyncio.run(COMMANDS[cmd]()))."""
        sig = inspect.signature(backfill_embeddings_cmd)
        assert len(sig.parameters) == 0

    def test_backfill_backlinks_cmd_is_coroutine_function(self):
        assert inspect.iscoroutinefunction(backfill_backlinks_cmd)

    def test_backfill_embeddings_cmd_is_coroutine_function(self):
        assert inspect.iscoroutinefunction(backfill_embeddings_cmd)


@pytest.mark.unit
class TestCliModuleBlock:
    """Testy bloku __name__ == '__main__'."""

    def test_module_has_main_guard(self):
        """Modul cli zawiera blok if __name__ == '__main__'."""
        import inspect

        import monolynx.cli

        source = inspect.getsource(monolynx.cli)
        assert 'if __name__ == "__main__"' in source or "if __name__ == '__main__'" in source
