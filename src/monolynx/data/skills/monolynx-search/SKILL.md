---
name: monolynx-search
description: Szukaj informacji w wiki projektu na platformie Monolynx. Użyj gdy użytkownik pyta o dokumentację projektu, architekturę, API, integracje, standardy kodu lub inne informacje zapisane w wiki Monolynx. Trigger na słowa "monolynx", "wiki", "szukaj w wiki", "sprawdź w monolynx", "co mamy w wiki", "jak działa" (w kontekście dokumentacji projektu).
allowed-tools: mcp__monolynx__search_wiki, mcp__monolynx__get_wiki_page, mcp__monolynx__list_wiki_pages, mcp__monolynx__list_projects, mcp__monolynx__log_time, AskUserQuestion
---

# Wyszukiwanie w Wiki Monolynx

## Kiedy użyć

Użyj tego Skill'a gdy użytkownik:
- Pyta "sprawdź w monolynx", "szukaj w wiki", "co mamy w wiki"
- Pyta o dokumentację projektu (architektura, API, integracje, standardy)
- Chce wiedzieć jak coś działa w projekcie i informacja może być w wiki
- Wspomina "monolynx", "wiki", "dokumentacja projektu"

## Proces

### Krok 1: Ustal projekt

Jeśli użytkownik podał slug projektu — użyj go.

Jeśli NIE podał projektu:
1. Użyj `mcp__monolynx__list_projects` aby wylistować dostępne projekty
2. Jeśli jest tylko 1 projekt — użyj go automatycznie
3. Jeśli jest więcej projektów — zapytaj użytkownika za pomocą `AskUserQuestion`:
   - "W którym projekcie Monolynx szukać?"
   - Opcje: lista dostępnych projektów (slug + nazwa)

### Krok 2: Wyszukaj w wiki

Użyj `mcp__monolynx__search_wiki` z:
- `project_slug`: slug projektu
- `query`: pytanie użytkownika (w naturalnym języku)
- `limit`: 5 (domyślnie)

### Krok 3: Pobierz szczegóły

Jeśli wyniki wyszukiwania semantycznego nie wystarczają do pełnej odpowiedzi:
1. Użyj `mcp__monolynx__get_wiki_page` aby pobrać pełną treść najlepiej dopasowanej strony
2. Jeśli potrzeba — pobierz dodatkowe strony

### Krok 4: Odpowiedz

Podaj:
- Bezpośrednią odpowiedź na pytanie użytkownika
- Kluczowe fragmenty z wiki (cytaty, tabele, diagramy)
- Nazwę strony wiki, z której pochodzi informacja
- Jeśli informacja nie została znaleziona — powiedz o tym jasno

## Wskazówki

- Wyszukiwanie semantyczne (`search_wiki`) jest najlepsze do szerokich pytań
- Do przeglądania struktury wiki użyj `list_wiki_pages`
- Odpowiadaj w języku, w którym pyta użytkownik
- Nie kopiuj całych stron — wyciągaj istotne fragmenty
- Jeśli wiki nie zawiera odpowiedzi, zaproponuj przeszukanie kodu źródłowego
