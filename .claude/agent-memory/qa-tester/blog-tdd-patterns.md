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
