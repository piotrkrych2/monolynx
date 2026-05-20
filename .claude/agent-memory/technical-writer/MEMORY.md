# Technical Writer — pamięć projektu Monolynx

## Konwencje pisania
- Dokumenty PL: pełne diakrytyki (ą ć ę ł ń ó ś ź ż), ZERO em-dash `—` (sprawdź `grep -c "—" <plik>` = 0). Zamiana: dwukropek/przecinek/kropka.
- CLAUDE.md jest w angielskim i używa strzałki `→` jako separatora sekwencji (już występuje w pliku, spójne).
- Wszystkie komendy Pythona przez `docker compose exec app`, nigdy lokalnie.

## Architektura / dystrybucja
- [Plugin vs install_monolynx_skills](project_plugin_vs_install_skills.md) — dwie równoległe ścieżki dystrybucji skilli, obie utrzymywane; mcp_server.py bez zmian.

## Raportowanie do ticketów (obowiązkowe)
- Po każdej sesji: komentarz PL (`add_comment`) + log czasu (`log_time`, min 1 min) na tickecie. MCP: `mcp__claude_ai_Monolynx__*`.
