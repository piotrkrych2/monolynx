# Monolynx Skills dla Claude Code

## Instalacja

1. Rozpakuj archiwum w katalogu glownym projektu — pliki trafia do `.claude/skills/`
2. We wszystkich plikach SKILL.md zamien `<SLUG_PROJEKTU>` na slug Twojego projektu w Monolynx
   (np. `my-app`, `ecommerce` — ten sam slug, ktory widzisz w URL dashboardu)

## Zawarte skille

### monolynx-work
Koordynator pracy zespolu agentow nad ticketem ze Scruma.
Wywolanie: `/monolynx-work [ticket-id]`

### monolynx-search
Wyszukiwanie semantyczne w wiki projektu.
Trigger automatyczny na slowa "wiki", "szukaj w wiki", "sprawdz w monolynx".

### monolynx-create-graph-ci-script
Generator skryptu CI synchronizujacego graf zaleznosci kodu z Monolynx.
Wywolanie: `/monolynx-create-graph-ci-script [monolynx-url]`

## Wymagania

- Claude Code z podlaczonym serwerem MCP Monolynx (patrz: How to MCP)
- Token API Monolynx skonfigurowany w `.mcp.json`
