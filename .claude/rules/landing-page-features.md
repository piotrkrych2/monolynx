---
paths:
  - "src/monolynx/mcp_server.py"
  - "src/monolynx/dashboard/**"
  - "src/monolynx/models/**"
  - "src/monolynx/services/**"
  - "src/monolynx/features.py"
---

# Nowy tool / feature / moduł → update landing page

Każda nowa funkcjonalność widoczna dla użytkownika lub agenta AI musi zostać odzwierciedlona na landing page. Landing nie aktualizuje się sam - to ręczny krok, który należy wykonać w tym samym tickecie co implementacja.

## Co wyzwala obowiązek

- Nowe narzędzie MCP (`@mcp.tool()` w `src/monolynx/mcp_server.py`).
- Nowy moduł dashboardu (`dashboard/<module>.py`) lub istotna funkcja modułu.
- Nowy widoczny feature (UI, endpoint API, integracja, zachowanie produktu).

Czysto wewnętrzne zmiany (refactor, migracja bez nowej funkcji, fix) NIE wymagają update'u landing.

## Gdzie zaktualizować

Źródłem treści landing page jest `src/monolynx/features.py` (NIE template - template tylko renderuje). Każdy moduł ma builder `_feature_<modul>(lang)` zwracający dict z polami `features[]`, `steps[]`, `mcp_tools[]`, `tech_details[]`.

1. **Nowy tool MCP** → dopisz wpis `{"name": ..., "desc": ...}` do `mcp_tools[]` właściwego modułu. Jeśli `ai_intro` zawiera licznik narzędzi ("N narzędzi MCP" / "N MCP tools") - zwiększ go.
2. **Nowy feature modułu** → dopisz wpis do `features[]` (i ewentualnie `steps[]`).
3. **Nowy moduł** → nowy builder `_feature_<modul>`, rejestracja w `_FEATURES`, wpis w `_other_modules()`, kafel w `templates/landing.html`.

**ZAWSZE aktualizuj OBA języki** - każdy builder ma osobną gałąź `if lang == "pl"` i `return` dla EN. Pominięcie jednej gałęzi to bug.

## Definition of Done (rozszerzenie)

Ticket dodający tool/feature/moduł jest "done" dopiero gdy:
- [ ] kod + testy gotowe,
- [ ] `features.py` zaktualizowany (PL + EN),
- [ ] licznik narzędzi w `ai_intro` zgadza się z faktyczną liczbą wpisów w `mcp_tools[]`.

## Pitfall

Landing to plik Pythona renderowany do statycznych stron - rozjazd między faktycznym zestawem narzędzi MCP a listą na `/features/<slug>` jest niewidoczny dopóki ktoś ręcznie nie porówna. Łatwo dodać tool w `mcp_server.py` i zapomnieć o `features.py`. Traktuj update landing jako część implementacji, nie osobne zadanie "na później".
