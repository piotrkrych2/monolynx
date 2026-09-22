---
name: codebase-patterns
description: Wzorce kodu Monolynx potwierdzone przy review - serwisy, MCP, tokeny, testy, powracajace wady
metadata:
  type: project
---

# Wzorce kodu Monolynx (potwierdzone przy review)

## Serwisy i dashboard
- Module-level `logger = logging.getLogger("monolynx.<module>")`.
- `monitoring.py` to czysty utility (bez CRUD i DB); serwisy CRUD (`wiki.py`, `sprint.py`) operuja na DB bezposrednio.
- Dashboard NIE sprawdza `ProjectMember` - kazdy zalogowany user wchodzi na projekt po slugu (wzorzec zastany).
- Walidacja formularzy uzywa jednej zmiennej `error`, ktora bywa nadpisywana (wzorzec zastany, wadliwy).
- Przy bledzie walidacji uzywany jest `templates.TemplateResponse` zamiast `render_project_page` (bez badge'ow sidebaru) - przyjete.

## Tokeny
- MCP: prefiks `osk_` + SHA256 w DB. Heartbeat: prefiks `hb_` + plain text (przyjety tradeoff, zakres = tylko ping).
- Generowanie: `secrets.token_urlsafe(16)` = 22 znaki, pasuje do `^hb_[A-Za-z0-9_-]{20,30}$`.

## MCP server
- `_auth(ctx)` dla tooli projektowych (list/create), `_get_user_and_project(ctx, slug)` dla project-scoped; `_get_user_member_and_project` zwraca tez `ProjectMember`.
- `_get_user_and_project` odrzuca role -> redundantne zapytanie w kazdym toolu, ktory jej potrzebuje.
- ORM odczytywany poza blokiem sesji dziala dla kolumn skalarnych (brak lazy loads) - wzorzec zastany.
- `delete_project` owner-only, `delete_monitor` owner/admin, `delete_heartbeat` bez roli - niespojnosc.
- Format odpowiedzi: `list_tickets` -> `list[dict]` z `_meta` na koncu (stary), `search_tickets` -> `dict` z `results/total/page/total_pages` (nowy, preferowany).
- Braki modelowe: `Project` i `Sprint` bez `updated_at`; `Ticket.updated_at` ma `onupdate`, wiec nie nadaje sie do sledzenia zmian statusu (burndown to aproksymuje).
- period/grace: API w minutach, DB w sekundach (`*60` / `//60`).

## MCP transport auth (MON-103)
- `_MCPBearerAuthMiddleware` (plain ASGI) owija `mcp.streamable_http_app()` przez `build_mcp_http_app()`; `main.py` mountuje TEN SAM obiekt pod `/mcp` i `/`.
- Bramka reuzywa `_verify_token` (OAuth-first, fallback `osk_`); per-tool `_auth(ctx)` zostaje jako defense-in-depth. Router OAuth ma priorytet nad mountem `/`.
- PULAPKA TESTOWA: pod `ASGITransport` BEZ lifespan MCP session manager nie jest zainicjalizowany -> `/mcp` daje 404 zanim dojdzie do middleware, wiec testy 401 sa order-dependent. Weryfikuj pod `TestClient` (lifespan ON), trace'ujac `__call__` middleware i sprawdzajac `/mcp` ORAZ `/mcp/`.

## Ticket ID lookup (SPROSTOWANE MON-196)
- `get_ticket`, `update_ticket`, `add_comment`, `get_ticket_schedule` przyjmuja UUID **albo** klucz `MON-20`.
- PULAPKA: `_resolve_ticket_uuid` (`mcp_server.py:799-823`) dopasowuje wylacznie NUMER, prefiks IGNORUJE - `JIRA-45` w projekcie `MON` trafi w MON-45. Kod biorący klucz z danych zewnetrznych (nazwa brancha, tytul MR) musi sam porownac prefiks; pierwsza linia `get_ticket` to `<KOD>-<numer> | tytul`.

## Wzorce testowe
- Fixture: transakcja na poziomie connection z rollbackiem, `expire_on_commit=False` - serwisowe `db.commit()` dziala na savepointach.
- Neo4j: `result_mock.__aiter__ = lambda self: AsyncIterMock(records)`; driver przez `__aenter__`/`__aexit__` na `driver.session.return_value`.
- Tool MCP mozna w tescie zaimportowac i `await` bezposrednio - `@mcp.tool()` zachowuje callable.

## Powracajace wady do wypatrzenia
- Blokujace wywolania MinIO w async endpointach (`wiki.py:532`, `scrum.py:658`) - bez `run_in_executor`; tool MCP robi to poprawnie przez ThreadPoolExecutor.
- Brak serwerowej walidacji MIME przy uploadzie (tylko FilePond po stronie klienta) - ryzyko stored XSS przez `text/html`.
- Sanityzacja nazwy pliku: `scrum.py` ma (basename + regex + podmiana `chr(34)`/`chr(92)`), wiki NIE ma.
- Stale definiowane w `mcp_server.py` zamiast `constants.py` (LABEL_COLOR_PALETTE, ACTIVITY_ENTITY_TYPES).
- Brak limitow zasobow per projekt (monitory 20, heartbeaty 50, zalaczniki bez limitu).
- `scalar_one()` tam, gdzie powinno byc `scalar_one_or_none()`; brak obslugi `IntegrityError` przy `UniqueConstraint`.
- Anti-pattern `scalar() or default`: `0 or -1` zwraca `-1`, bo `0` jest falsy - nie dokladaj Pythonowego `or` do COALESCE (MON-53).
- Nowa funkcja dodana do czesci tooli, a nie do wszystkich (labels w `list_tickets`/`get_ticket`, brak w `search_tickets`/`create`/`update`).

## Konwencje plugin/skills
- ToolSearch per krok NIE jest wymagany, gdy krok uzywa tooli zaladowanych wczesniej (`work-simple` laduje hurtem, `sprint-end` nie ma go wcale). Regula `skill-mcp-calls.md` dotyczy krokow, ktore ToolSearch faktycznie wolaja.
- `landing-page-features.md` wyzwala sie na toolu MCP / module dashboardu / widocznym feature. Nowy SKILL to ani tool, ani modul - nie odejmuj -25 za brak wpisu w `features.py`.
- `src/monolynx/static/skills/<nazwa>/SKILL.md` rozni sie od zrodla o transformacje `sync_skills.py` (dodane `name:` we frontmatterze, `<PROJECT_SLUG>` -> `<PROJECT-SLUG>`) - to NIE jest drift.
- `plugin/scripts/`: bit wykonywalnosci jest MIESZANY (755: sprint_run_state.py, mutation_baseline.py, sprint_run.sh; 644: mr_queue_state.py, mutation_report.py). Nie punktuj braku bitu jako naruszenia konwencji; ruff tego nie lapie (EXE001 wylaczone).
- `${CLAUDE_PLUGIN_ROOT}` i `${user_config.X}` istnieja tylko w Claude Code - w komendzie dla terminala uzytkownika rozwina sie do pustego stringu. W dokumentacji: najpierw `echo` rozwijajacy sciezke, potem komenda z literalem.
- Dwa identyfikatory sesji `claude agents --json --all`: `id` (8 znakow, tylko background) i `sessionId` (UUID). `claude logs|attach|stop|rm` przyjmuja WYLACZNIE `id`; pomylka jest cicha (log nigdy nie znaleziony).
- Granica systemu w skryptach CLI: waliduj nie tylko top-level ksztalt JSON, ale i elementy - `row["key"]` na zlym elemencie daje traceback i exit 1 zamiast zadeklarowanego exit 2.

## Landing page (MON-170)
Teksty marketingowe zyja w `TRANSLATIONS` w `main.py`, nie tylko w szablonie - asercja `"<fraza>" in response.text` bywa ZIELONA na baseline. Test elementu UI musi celowac w markup (`">/monolynx:sprint-run</code>"`). Licznik skilli tez siedzi w `main.py`. Chipy komend: KOLEJNOSC z `mcp_guide.html`, ale KLASY od sasiadow w tym samym pliku.
- Instrukcja w SKILL.md typu "obowiazuje przy KAZDYM X" musi lezec PRZED pierwszym X w pliku - Team Manager wykonuje dokument liniowo, wiec regula dopisana pozniej nie zostanie zastosowana do wczesniejszych wywolan. Sprawdzaj numery linii wystapien, nie tresc deklaracji (MON-187).
- Formula odejmujaca okno subagenta od czasu TM jest poprawna tylko dla subagentow majacych wlasny `log_time`. W `work-simple` maja go dev i krytyk; Explore z FAZY 1 NIE ma (`references/researcher.md` bez `log_time`, brak odpowiednika KROK 3d z `work`), wiec jego czas zostaje w czasie TM (MON-187).

## Pobieranie zalacznikow: Content-Disposition (MON-203)
Wszystkie trzy handlery pobierania (`blog.py:188`, `dashboard/wiki.py:778`, `dashboard/settlements.py:665`) wstawiaja nazwe pliku do naglowka jako `filename="{safe_name}"` z surowej wartosci z bazy. Starlette koduje naglowki latin-1, wiec nazwa z polskim znakiem konczy sie `UnicodeEncodeError` -> 500 (w publicznym endpointcie: dla anonimowego czytelnika i bez naglowka `nosniff`). Sanitizer uploadu `re.sub(r"[^\w\s\-.]", "_", ...)` NIE usuwa diakrytykow - `\w` w Pythonie jest unicode-aware, wiec taka nazwa realnie trafia do bazy. `settlements.py` doklada `filename*=UTF-8''{quote(...)}`, ale zostawia surowa nazwe w `filename="..."`, wiec wywroci sie tak samo - NIE wskazuj go jako wzorca. Stan na 2026-09-21: znane, osobny ticket. Nowy handler kopiujacy ten wzorzec zglaszaj jako rekomendacje, nie odjecie (ticket MON-203 sam kazal kopiowac `wiki_page_attachment_serve()`).

## Wiki: is_public i partial unique index
`services/wiki.py:483` (`create_wiki_page`) i `:542` (`update_wiki_page`) robia `await db.commit()` wewnatrz `try` z mapowaniem `IntegrityError -> db.rollback() -> ValueError` (globalny partial unique index `uq_wiki_public_slug`, MON-201). Tool MCP i router NIE dodaja wlasnego commita - bylby podwojny. Weryfikuj to przy kazdym tickecie dotykajacym wiki, bo na tym jednym fakcie wisi caly wiersz -30. Konsekwencja dla routera: po `ValueError` sesja jest po rollbacku, wiec kazdy obiekt ORM trafiajacy do Jinja2 musi byc przeladowany (`MissingGreenlet` w sync renderze).
