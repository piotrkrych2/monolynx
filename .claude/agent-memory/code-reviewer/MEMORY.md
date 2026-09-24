# Code Reviewer Memory

## Topic files
- [review_method.md](review_method.md) — jak dowodzic znalezisk: sam odpalaj gate'y spojnosci, mutant zamiast deklaracji autora, guard Bash, coverage crash, adres `team-lead`.
- [codebase_patterns.md](codebase_patterns.md) — wzorce Monolynx: serwisy, MCP (auth, formaty, transport), tokeny, testy, powracajace wady, konwencje plugin/skills.
- [review_history.md](review_history.md) — historia ocen ticketow MON-* (score/iteracje/znaleziska per ticket).
- [plugin_packaging.md](plugin_packaging.md) — plugin Claude Code (MON-72): struktura, `claude plugin validate`, em-dash, `${user_config.X}` w bashu.
- [migration_head_verify.md](migration_head_verify.md) — Alembic: sam licz head + sprawdz unikalnosc revision ID, nie wierz ticketowi (MON-73).
- [wiki_llm_method.md](wiki_llm_method.md) — metoda LLM Wiki (MON-73): sygnatury, marker sprzecznosci, RESERVED_SLUGS, patch MinIO gotcha.
- [bash_guard.md](bash_guard.md) — hook pluginu (MON-144/147): jak probowac hooka, luka DESTRUCTIVE na flagi dlugie, granice `is_tmp_cleanup`.
- [graphify_sync.md](graphify_sync.md) — graf graphify->Monolynx (MON-115/116/118): kontrakt `replace_graph`, taksonomia, wzorzec transportu MCP.
- [selfref_selectin_join_depth.md](selfref_selectin_join_depth.md) — self-referential `lazy="selectin"` bez `join_depth` = martwe, test z jawnym selectinload to maskuje (MON-198).

## Trzy rzeczy, ktore ratowaly review najczesciej
1. **Odpal gate'y sam.** Sygnal "wszystko zielone" od TM bywa sprzed ostatniego zapisu developera (MON-197: `sync_skills.py --check` czerwony w chwili review). `ls -lT` zrodla i kopii rozstrzyga w sekunde.
2. **Dowodz mutantem, nie deklaracja.** Test moze istniec i nie wykonywac ocenianego kodu (grep po literale w pliku zrodlowym, stub narzedzia nieznajacy nowych argumentow, fixture z literalem z echa promptu).
3. **Zrob swiezy `git diff` per plik.** Snapshot statusu z promptu potrafi przemilczec zmiany unstaged (MON-79); przy stackowanych branchach grepuj symbol w bazie (MON-117).
