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


def _validate(name: str) -> str:
    from monolynx import cli as cli_module

    return cli_module.validate_test_db_name(name)


@pytest.mark.unit
class TestValidateTestDbName:
    """Walidacja nazwy bazy testowej -- ostatnia bariera przed usunieciem bazy."""

    @pytest.mark.parametrize("name", ["open_sentry_test_ab12cd34", "open_sentry_test_MON-250", "open_sentry_test_a_b-C9"])
    def test_valid_name_returned_unchanged(self, name):
        assert _validate(name) == name

    @pytest.mark.parametrize(
        "name",
        [
            "open_sentry",
            "open_sentry_test",
            "open_sentry_test_",
            "open_sentry_test_a;b",
            'open_sentry_test_a"b',
            "open_sentry_test_a'b",
            "open_sentry_test_a b",
            "x_open_sentry_test_a",
            "",
        ],
    )
    def test_invalid_name_raises_value_error(self, name):
        with pytest.raises(ValueError):
            _validate(name)

    def test_name_with_trailing_newline_raises(self):
        """`$` bez `\\Z` przepuszcza koncowy newline -- nazwa musi byc odrzucona."""
        with pytest.raises(ValueError):
            _validate("open_sentry_test_abc\n")

    def test_name_of_63_chars_accepted(self):
        name = "open_sentry_test_" + "a" * (63 - len("open_sentry_test_"))
        assert len(name) == 63
        assert _validate(name) == name

    def test_name_of_64_chars_rejected(self):
        name = "open_sentry_test_" + "a" * (64 - len("open_sentry_test_"))
        assert len(name) == 64
        with pytest.raises(ValueError):
            _validate(name)

    def test_test_db_prefix_constant(self):
        from monolynx import cli as cli_module

        assert cli_module.TEST_DB_PREFIX == "open_sentry_test_"


def _make_prune_engine(rows, failing_names=()):
    """Engine z mockowanym polaczeniem: 1. execute zwraca wiersze pg_stat_database, kolejne to usuwanie baz."""
    from sqlalchemy.exc import OperationalError

    select_result = MagicMock()
    select_result.all.return_value = rows
    conn = AsyncMock()

    async def execute(stmt, *args, **kwargs):
        if conn.execute.await_count == 1:
            return select_result
        sql = str(stmt)
        if any(f'"{name}"' in sql for name in failing_names):
            raise OperationalError(sql, {}, Exception("database is being accessed by other users"))
        return MagicMock()

    conn.execute = AsyncMock(side_effect=execute)
    connect_cm = MagicMock()
    connect_cm.__aenter__ = AsyncMock(return_value=conn)
    connect_cm.__aexit__ = AsyncMock(return_value=False)
    engine = MagicMock()
    engine.connect.return_value = connect_cm
    engine.dispose = AsyncMock()
    return engine, conn


_PRUNE_SETTINGS_URL = "postgresql+asyncpg://user:pw@dbhost:5432/open_sentry_test_run1"


async def _run_prune(rows, failing_names=()):
    from types import SimpleNamespace

    from monolynx import cli as cli_module

    engine, conn = _make_prune_engine(rows, failing_names)
    settings = SimpleNamespace(DATABASE_URL=_PRUNE_SETTINGS_URL)
    with (
        patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=engine) as mock_create,
        patch("monolynx.config.settings", settings),
    ):
        await cli_module.prune_test_dbs_cmd()
    return engine, conn, mock_create


def _drop_sqls(conn):
    return [str(c.args[0]) for c in conn.execute.await_args_list[1:] if "DROP DATABASE" in str(c.args[0])]


@pytest.mark.unit
class TestPruneTestDbsRegistration:
    """prune-test-dbs musi byc bezargumentowe i zarejestrowane (.claude/rules/makefile-cli.md)."""

    def test_commands_contains_prune_test_dbs(self):
        assert "prune-test-dbs" in COMMANDS

    def test_commands_prune_points_to_function(self):
        from monolynx import cli as cli_module

        assert COMMANDS["prune-test-dbs"] is cli_module.prune_test_dbs_cmd

    def test_prune_cmd_takes_no_arguments(self):
        from monolynx import cli as cli_module

        assert len(inspect.signature(cli_module.prune_test_dbs_cmd).parameters) == 0

    def test_prune_cmd_is_coroutine_function(self):
        from monolynx import cli as cli_module

        assert inspect.iscoroutinefunction(cli_module.prune_test_dbs_cmd)


@pytest.mark.unit
class TestPruneTestDbsCmd:
    """prune-test-dbs: sprzata porzucone bazy open_sentry_test_* bez aktywnych polaczen."""

    async def test_drops_only_databases_without_connections(self):
        rows = [("open_sentry_test_idle1", 0), ("open_sentry_test_busy", 2), ("open_sentry_test_idle2", 0)]

        _, conn, _ = await _run_prune(rows)

        drops = _drop_sqls(conn)
        assert len(drops) == 2
        assert any('"open_sentry_test_idle1"' in sql for sql in drops)
        assert any('"open_sentry_test_idle2"' in sql for sql in drops)
        assert not any("open_sentry_test_busy" in sql for sql in drops)

    async def test_database_with_single_connection_not_dropped(self):
        """Granica numbackends: 1 polaczenie to juz baza aktywna."""
        _, conn, _ = await _run_prune([("open_sentry_test_one", 1)])

        assert _drop_sqls(conn) == []

    async def test_drop_statement_quotes_identifier_and_has_no_force(self):
        _, conn, _ = await _run_prune([("open_sentry_test_x", 0)])

        drops = _drop_sqls(conn)
        assert drops == ['DROP DATABASE "open_sentry_test_x"']
        assert "FORCE" not in drops[0].upper()

    async def test_connects_to_postgres_database_in_autocommit(self):
        _, _, mock_create = await _run_prune([])

        mock_create.assert_called_once()
        url = mock_create.call_args.args[0]
        assert url.database == "postgres"
        assert url.host == "dbhost"
        assert mock_create.call_args.kwargs["isolation_level"] == "AUTOCOMMIT"

    async def test_first_statement_selects_from_pg_stat_database(self):
        _, conn, _ = await _run_prune([])

        first_sql = str(conn.execute.await_args_list[0].args[0])
        assert "pg_stat_database" in first_sql
        assert "DROP DATABASE" not in first_sql.upper()

    async def test_invalid_name_from_database_not_dropped(self):
        rows = [('open_sentry_test_a"; DROP DATABASE open_sentry; --', 0), ("open_sentry", 0), ("open_sentry_test_ok", 0)]

        _, conn, _ = await _run_prune(rows)

        assert _drop_sqls(conn) == ['DROP DATABASE "open_sentry_test_ok"']

    async def test_drop_error_does_not_block_remaining_databases(self, capsys):
        rows = [("open_sentry_test_first", 0), ("open_sentry_test_second", 0)]

        engine, conn, _ = await _run_prune(rows, failing_names=["open_sentry_test_first"])

        drops = _drop_sqls(conn)
        assert len(drops) == 2
        assert '"open_sentry_test_second"' in drops[1]
        engine.dispose.assert_awaited_once()

        lines = capsys.readouterr().out.strip().splitlines()
        failed = [line for line in lines if "open_sentry_test_first" in line]
        ok = [line for line in lines if "open_sentry_test_second" in line]
        assert len(failed) == 1
        assert "BLAD" in failed[0]
        assert "usunieto" not in failed[0]
        assert len(ok) == 1
        assert "usunieto" in ok[0]
        assert "1" in lines[-1]
        assert "2" not in lines[-1]

    async def test_engine_disposed_after_successful_run(self):
        engine, _, _ = await _run_prune([("open_sentry_test_a", 0)])

        engine.dispose.assert_awaited_once()

    async def test_engine_disposed_when_select_fails(self):
        from types import SimpleNamespace

        from monolynx import cli as cli_module

        engine, conn = _make_prune_engine([])
        conn.execute = AsyncMock(side_effect=RuntimeError("connection refused"))
        settings = SimpleNamespace(DATABASE_URL=_PRUNE_SETTINGS_URL)
        with (
            patch("sqlalchemy.ext.asyncio.create_async_engine", return_value=engine),
            patch("monolynx.config.settings", settings),
            pytest.raises(RuntimeError),
        ):
            await cli_module.prune_test_dbs_cmd()

        engine.dispose.assert_awaited_once()

    async def test_output_reports_dropped_and_skipped_with_names(self, capsys):
        rows = [("open_sentry_test_gone", 0), ("open_sentry_test_busy", 3)]

        await _run_prune(rows)

        lines = capsys.readouterr().out.splitlines()
        gone = [line for line in lines if "open_sentry_test_gone" in line]
        busy = [line for line in lines if "open_sentry_test_busy" in line]
        assert len(gone) == 1
        assert "usunieto" in gone[0]
        assert len(busy) == 1
        assert "pominieto" in busy[0]
        assert "usunieto" not in busy[0]

    async def test_summary_counts_only_dropped(self, capsys):
        rows = [("open_sentry_test_a", 0), ("open_sentry_test_b", 0), ("open_sentry_test_c", 5)]

        await _run_prune(rows)

        last_line = capsys.readouterr().out.strip().splitlines()[-1]
        assert "2" in last_line
        assert "3" not in last_line
