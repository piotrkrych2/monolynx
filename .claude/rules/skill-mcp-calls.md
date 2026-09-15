---
paths:
  - "plugin/skills/**/SKILL.md"
  - "src/monolynx/static/skills/**/SKILL.md"
---

# SKILL.md - wywołania MCP zgodne z rzeczywistym narzędziem

Każde wywołanie `mcp__monolynx__<tool>(...)` w pliku SKILL.md musi mieć nazwę
narzędzia i parametry zgodne z faktyczną sygnaturą w `src/monolynx/mcp_server.py`.
`ToolSearch` w danym kroku musi wymieniać termin dla KAŻDEGO narzędzia, którego
ten krok używa, zawsze z `max_results=10`.

## Rule

1. Przed wpisaniem wywołania sprawdź sygnaturę toola w `mcp_server.py`
   (`grep -n "async def <tool>" src/monolynx/mcp_server.py`). Parametr, którego
   tam nie ma, nie istnieje - nawet jeśli sąsiednie linie w SKILL.md go używają.
2. `ToolSearch(query="+monolynx ...", max_results=10)` - w query po jednym
   terminie na każde narzędzie wołane w kroku (np. krok woła `list_wiki_pages`,
   `get_wiki_page`, `create_wiki_page` -> query zawiera `list get create`).
   Domyślne `max_results=5` to loteria: narzędzia o równej punktacji wypychają się
   nawzajem i krok nie ma czym pracować.
3. `project_slug` zawsze jako `<PROJECT_SLUG>` - patrz `skill-md-project-slug.md`
   (ta reguła jest szczególnym przypadkiem niniejszej).

## Pitfall

SKILL.md nie ma kompilatora. Wywołanie z nieistniejącym parametrem albo brakujące
narzędzie w `ToolSearch` nie wywali walidatora pluginu ani testów - ugryzie
dopiero użytkownika w cudzym projekcie, gdy skill wykona się w połowie. Autorzy
skilli kopiują wywołania ze starszych linii tego samego pliku, powielając
błąd zamiast go łapać.

## Regression

- MON-77 - hardkod `project_slug='monolynx'` skopiowany z sąsiednich linii.
- MON-148 - `create_wiki_page(..., slug="toolchain")` z parametrem, którego tool
  nie przyjmuje (po jego usunięciu slug powstawał z tytułu i strona stała się
  niewidoczna dla `work`/`work-simple`); `ToolSearch` w KROK 1 bez terminu `get`
  i bez `max_results`, przez co `get_wiki_page` ładował się losowo. Ten sam brak
  `max_results` zgłoszony w `ticket-create` i `ticket-review`.

## What to check

- Nowe lub zmienione wywołanie `mcp__monolynx__*` w SKILL.md -> porównaj
  parametry z sygnaturą w `mcp_server.py`.
- Krok woła narzędzie, którego `ToolSearch` tego kroku nie ładuje -> dopisz
  termin do query.
- `ToolSearch` bez `max_results=10` -> dopisz.
- Kopia w `src/monolynx/static/skills/` jest generowana (`make sync-skills`),
  poprawiaj źródło w `plugin/skills/`.
