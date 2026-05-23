# Technical Writer — pamięć projektu Monolynx

## Konwencje pisania
- Dokumenty PL: pełne diakrytyki (ą ć ę ł ń ó ś ź ż), ZERO em-dash `—` (sprawdź `grep -c "—" <plik>` = 0). Zamiana em-dash na hyphen `-` (NIE kasuj myślnika, NIE zamieniaj na przecinek). Bulk: `LC_ALL=en_US.UTF-8 perl -CSD -i -pe 's/\x{2014}/-/g' <plik>`.
- CLAUDE.md jest w angielskim i używa strzałki `→` jako separatora sekwencji (już występuje w pliku, spójne). UWAGA: CLAUDE.md ma ~100 istniejących em-dash jako separator bulletów `nazwa.py — opis` (cudze, poza zakresem - NIE ruszać). Targetowane edycje: liczy się tylko czy MOJE dodane/nowe linie mają 0 em-dash. Check: `grep -c "—"` przed i po musi być równe (zero netto). Nowe bullety w sekcji Services pisz z hyphenem `-` jako separator, nie kopiuj em-dash z sąsiednich.
- Wszystkie komendy Pythona przez `docker compose exec app`, nigdy lokalnie.

## Architektura / dystrybucja
- [Plugin vs install_monolynx_skills](project_plugin_vs_install_skills.md) — dwie równoległe ścieżki dystrybucji skilli, obie utrzymywane; mcp_server.py bez zmian.
- [Metoda LLM Wiki](project_wiki_llm_method.md) — regulamin (DEFAULT_WIKI_SCHEMA), strony systemowe wiki-index/wiki-log/wiki-schema, narzędzia MCP z gatingiem, skille wiki-init/ingest/lint + QUERY w search.

## Raportowanie do ticketów (obowiązkowe)
- Po każdej sesji: komentarz PL (`add_comment`) + log czasu (`log_time`, min 1 min) na tickecie. MCP: `mcp__claude_ai_Monolynx__*`.
