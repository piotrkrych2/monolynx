---
paths:
  - "src/monolynx/mcp_server.py"
  - "src/monolynx/api/v2/**"
  - "src/monolynx/services/**"
  - "cli/src/monolynx_cli/**"
---

# Nowy tool MCP → endpoint API v2 i komenda CLI w tym samym tickecie

Monolynx ma jedną warstwę logiki (`src/monolynx/services/`) i trzy wejścia do niej: narzędzia MCP (`src/monolynx/mcp_server.py`), REST API v2 (`src/monolynx/api/v2/`) oraz CLI `monolynx` / `mnx` (`cli/src/monolynx_cli/`), które rozmawia z serwerem wyłącznie przez API v2. Obowiązek parytetu wyzwala dodanie nowego narzędzia MCP (`@mcp.tool` w `mcp_server.py`) albo zmiana sygnatury istniejącego: nowy parametr, zmiana nazwy, typu lub znaczenia pola, zmiana zwracanych danych.

## Rule

1. **Trzy warstwy albo jawny wyjątek.** W tym samym tickecie powstają: narzędzie MCP, endpoint API v2 (router w `src/monolynx/api/v2/`, schematy w `src/monolynx/schemas/api_v2/`, wpis w `TOOL_TO_ENDPOINT`) i komenda CLI (moduł w `cli/src/monolynx_cli/commands/`, opis w `cli/README.md`). Jeśli narzędzie świadomie nie ma odpowiednika w v2, trafia do `INTENTIONALLY_UNMAPPED` w `tests/integration/tool_endpoint_map.py` z niepustym powodem. Dzisiejsze powody to moduły poza zakresem API v2 (500ki, Monitoring, Heartbeat, zapis grafu Połączeń (robi go wyłącznie synchronizacja `cicd/sync_graph.py` przez MCP), Rozliczenia, Plan pracy, Pipelines, Role i uprawnienia), metoda LLM Wiki z załącznikami i plikami wiki, załączniki ticketów, czas pracy, raporty i dziennik aktywności oraz `install_monolynx_skills` (dystrybucja skilli wyłącznie przez MCP). Nowy powód piszesz tak samo konkretnie: co jest poza zakresem i dlaczego.
2. **Logika w `services/`, frontendy cienkie.** Walidacja, uprawnienia, zapis i `await db.commit()` żyją w funkcji serwisu (konwencja `(db, user, project, ...)`, wynik jako model ORM albo `@dataclass`, błąd biznesowy jako `ValueError`). Narzędzie MCP i router v2 tylko parsują wejście, wołają serwis i formatują wynik; router zamienia `ValueError` na problem+json przez `service_error()` z `api/v2/common.py`. Komenda CLI woła endpoint przez `MonolynxClient` i przekazuje dane do `emit(ctx, dane)`. Logika skopiowana do dwóch frontendów to rozjazd czekający na pierwszą zmianę.
3. **Różnice kontraktu są jawne.** Pole obecne tylko w jednym kanale albo inna nazwa parametru opisujesz w `ToolEndpointSpec` (`field_map`, `known_differences`), a nie zostawiasz testowi parytetu do odkrycia. Zmiana `is_write` wymaga zgodnej zmiany `_WRITE_TOOLS` w `tests/integration/test_api_v2_parity.py`.
4. **Test kompletności mapy (MON-131) egzekwuje część tej reguły.** `TestMapCompleteness` w `tests/integration/test_api_v2_parity.py` pada, gdy narzędzie zarejestrowane w MCP nie ma wpisu w żadnym słowniku, występuje w obu, wpis wskazuje nieistniejące narzędzie, powód w `INTENTIONALLY_UNMAPPED` jest pusty albo zmapowana trasa nie istnieje w `/api/v2/openapi.json`. Asercje liczebności obu słowników w `test_every_mcp_tool_has_exactly_one_entry` aktualizujesz razem z mapą. `TestToolEndpointParity` wymaga scenariusza dla każdego zmapowanego narzędzia i porównuje wynik obu kanałów. Test NIE łapie braku komendy CLI ani braku jej opisu w `cli/README.md`: tę część sprawdzasz sam.

## Definition of Done

Ticket dodający albo zmieniający narzędzie MCP jest "done" dopiero gdy:
- [ ] logika jest w `src/monolynx/services/`, a narzędzie MCP i router v2 ją wołają,
- [ ] narzędzie ma wpis w `TOOL_TO_ENDPOINT` (z endpointem, schematem w `schemas/api_v2/` i scenariuszem parytetu) albo w `INTENTIONALLY_UNMAPPED` z powodem,
- [ ] zmapowany endpoint ma komendę CLI w `cli/src/monolynx_cli/commands/` z testami w `cli/tests/`,
- [ ] komenda jest opisana w `cli/README.md`, a opis zgadza się z jej `--help` (`.claude/rules/docs-commands-verified.md`),
- [ ] landing page jest zaktualizowany zgodnie z `.claude/rules/landing-page-features.md`.

## Pitfall

Rozjazd między narzędziami MCP a komendami CLI jest niewidoczny, dopóki ktoś ręcznie nie porówna listy narzędzi z `monolynx --help`. Test kompletności mapy pilnuje tylko granicy MCP <-> v2, więc łatwo dodać narzędzie, dopisać je do mapy, zobaczyć zielone CI i zapomnieć o komendzie CLI i o `cli/README.md`. Druga pułapka: zmiana pola w serwisie przechodzi przez MCP (który zwraca to, co dostał), a schemat v2 w `schemas/api_v2/` dalej go nie ma i klient CLI nigdy go nie zobaczy.

## What to check

- Nowy `@mcp.tool` w `mcp_server.py` bez nowego wpisu w `tests/integration/tool_endpoint_map.py` -> dopisz wpis do `TOOL_TO_ENDPOINT` albo `INTENTIONALLY_UNMAPPED` z powodem.
- Nowy wpis w `TOOL_TO_ENDPOINT` bez scenariusza w `test_api_v2_parity.py` albo bez zmiany liczebności w `TestMapCompleteness` -> uzupełnij test.
- Zmiana pola lub parametru w funkcji serwisu bez zmiany schematu w `src/monolynx/schemas/api_v2/` i routera w `src/monolynx/api/v2/` -> dopasuj v2 albo opisz różnicę w `known_differences`.
- Logika biznesowa (zapytanie, walidacja, commit) dopisana w `mcp_server.py` albo w routerze v2 zamiast w `services/` -> przenieś do serwisu.
- Nowy endpoint v2 bez komendy w `cli/src/monolynx_cli/commands/` -> dodaj komendę albo zapisz w tickecie, który ticket ją dodaje.
- Nowa lub zmieniona komenda CLI bez zmiany `cli/README.md` -> zaktualizuj opis i porównaj z `--help`.
- Narzędzie przeniesione z `INTENTIONALLY_UNMAPPED` do `TOOL_TO_ENDPOINT` (moduł dostał API v2) -> odpowiednia grupa CLI przestaje być pustym placeholderem i dostaje komendy.
