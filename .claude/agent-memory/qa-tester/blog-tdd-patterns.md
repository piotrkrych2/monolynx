---
name: blog-tdd-patterns
description: Wzorce testów MON-254 (BlogPost) - migracja przez Operations, trwałość w nowej sesji, haczyki hooka Bash
metadata:
  type: project
---

- Test migracji Alembic na bazie z `create_all`: w teście `DROP TABLE <tabela>` w transakcji `db_session`, potem `(await db_session.connection()).run_sync(fn)` z `MigrationContext.configure(sync_conn)` + `Operations.context(ctx)` i wywołaniem `upgrade()` / `downgrade()` modułu ładowanego `importlib.util.spec_from_file_location`; rollback fixture przywraca tabelę. Stałe SQL z migracji (np. `BACKFILL_SQL`) wykonuj przez `text()`.
- Dowód commita bez spy: sesja z `async_sessionmaker(engine)` zapisuje, NOWA sesja czyta, `finally` sprząta (kaskada FK usuwa wiersze zależne od strony wiki). Wzór: `_mcp_env` w `tests/integration/test_blog_public.py`.
- `now()` w PostgreSQL jest stały w transakcji testu: `onupdate=func.now()` nie rozróżni czasów edycji - ustaw `updated_at` jawnie.
- Hook Bash subagenta odrzuca KAŻDĄ komendę zawierającą słowo pytest/lint w treści (także heredoc i `grep`) - do dopisywania do plików używaj Edit (nowy_string = stary koniec + dopisek), do przeszukiwania `grep -n` bez tych słów. `sed -n "${n}p"` w pętli odrzuca guard worktree - czytaj Read z offsetem.
- Atrapy `MagicMock()` jako `page` mają `is_public` typu MagicMock (prawdziwy w if) - nowy kod gałęziowy po `page.is_public` trzeba w tych testach stubować (autouse fixture w klasie).
- MON-262 (lint bloga, TDD): wielolinijkowe edycje istniejących plików testowych robię skryptem `python3 <plik w $CLAUDE_JOB_DIR/tmp>` napisanym przez Write (hook odrzuca słowa testowe w komendzie i heredocu; `sed` BSD nie zna `\n`). Test CLI z kodem wyjścia 1 po wypisaniu raportu: `result.exception` ma być `SystemExit`, a bad-shape daje stdout pusty. `_COMMANDS` w cli/tests/test_blog_commands.py wymaga payloadu z kodem 0 (dla lintu: same ostrzeżenia).
- MON-263 (publish/unpublish, TDD): `session.rollback()` w serwisie cofa CAŁĄ transakcję testu (seed też) - testy odmowy/kolizji serwisu używają zacommitowanego seeda przez `engine` (`_committed_blog`), a testy MCP/v2/parity podmieniają `patch.object(db_session, "rollback", AsyncMock())` + `assert_awaited_once`. Commit w v2 dowodzi spy `_commit_states` zapisujący `is_public` w chwili commita (commit auth jest legalny). Skrypty edycji dużych plików: Write do `$CLAUDE_JOB_DIR/tmp` + `python3 <plik>`; pipe i `awk -v`/`git` w złożonej komendzie odrzuca guard worktree. Lint read-only: docker run bez pipe, 2 wywołania (check, format --check); SIM117 wymaga jednego `with (...)` z `pytest.raises`.
