---
name: monolynx-help
description: "Wyswietl instrukcje uzycia skilli Monolynx - flow pracy z ticketami oraz skille dodatkowe. Uzyj gdy chcesz wiedziec jak pracowac z Monolynx w Claude Code."
user-invocable: true
argument-hint: ""
allowed-tools: ""
---

# Monolynx Skills - Przewodnik

Wyswietl ponizszy przewodnik uzytkownikowi i zakoncz. Nie wykonuj zadnych dodatkowych akcji.

---

## Flow pracy z ticketem

Skille Monolynx tworza kompletny przepis pracy - od pomyslu do realizacji:

### 1. `/monolynx:ticket-create [opis zadania]`

Tworzysz nowy ticket. Skill zbiera kontekst z wiki, kodu i grafu zaleznosci, a nastepnie generuje pelny opis ticketu (cel, kontekst, zakres, kryteria akceptacji, zaleznosci). Ticket trafia do sprintu lub backlogu.

### 2. `/monolynx:ticket-review [ticket-id lub klucz np. MNX-12]`

Recenzujesz ticket przed podjieciem pracy. Skill sprawdza forme ticketu, weryfikuje zalozenia z wiki i kodem, i generuje raport z ocena. Mozesz uruchomic review kilka razy - po kazdej poprawce ticketu, az opis bedzie kompletny i jednoznaczny.

**Wskazowka**: Powtarzaj cykl *review → poprawka → review* az raport pokaze same "OK" w formie i "ZGODNE" w zalozeniach. Dobrze zrecenzowany ticket = szybsza realizacja.

### 3. `/monolynx:work [ticket-id lub klucz np. MNX-12]`

Podejmujesz ticket do realizacji. Skill waliduje branch, uruchamia Researchera, dobiera zespol agentow i prowadzi rownolegle prace z obowiazkowym krytykiem. Na koniec loguje czas pracy.

Przebieg pracy jest raportowany do **modulu Pipelines** (obserwowalnosc, wzorowana na GitLab CI/CD): tworzy sie pipeline `ticket_work` ze stepami research → coding → wrap-up, a kazdy agent dostaje swoj job. Raport kazdego agenta (co zrobil, decyzje, pliki) zapisywany jest jako strona wiki podpieta pod job. Status, czas trwania i logi widac na zywo w zakladce "Pipelines" projektu. Raportowanie jest best-effort - jesli serwer MCP nie ma modulu Pipelines, skill dziala jak dotychczas. `/monolynx:work-simple` raportuje analogicznie, w uproszczonej formie (dev + krytyk).

### 4. `/monolynx:sprint-end [nazwa sprintu (opcjonalnie)]`

Zamykasz sprint. Skill orkiestruje zamkniecie jako pipeline `sprint_close` ze stepami wiki-update → wrap-up: integruje logi pracy ze sprintu z wiki (INGEST), audytuje wiki (LINT), czysci strony logow pipeline sprintu i na koniec realnie zamyka sprint (`complete_sprint` - niedokonczone tickety wracaja do backlogu, dlatego skill prosi o potwierdzenie). Bez argumentu bierze aktywny sprint. Raportowanie do Pipelines jest best-effort.

---

## Skille dodatkowe

Te skille dzialaja niezaleznie od powyzszego flow:

### `/monolynx:search [pytanie]`

Wyszukiwanie semantyczne (RAG) w wiki projektu. Uzyj gdy potrzebujesz informacji z dokumentacji - o architekturze, API, integracjach, standardach kodu. Aktywuje sie tez automatycznie gdy pytasz o dokumentacje projektu.

### `/monolynx:create-graph-ci-script`

Konfiguruje [graphify](https://github.com/Graphify-Labs/graphify) (zewnetrzny ekstraktor AST, 36 jezykow, offline) jako zrodlo grafu zaleznosci kodu: wykrywa system CI (GitLab/GitHub/Bitbucket/Jenkins), generuje `.graphifyignore` i cienki `cicd/sync_graph.py` (graph.json -> `replace_graph`), dodaje non-blocking step CI. WYMOG: graphify musi byc zainstalowane na runnerze CI (lub lokalnie) przez wlasciciela projektu - step CI go tylko uzywa, nigdy nie instaluje; brak graphify nie psuje builda. Uzyj raz w dowolnym projekcie - potem CI robi reszte.

### `/monolynx:graph-sync`

Lokalna synchronizacja grafu zaleznosci za reke - komplement do CI-owego `create-graph-ci-script`. Wykrywa graphify (przy braku prowadzi przez instalacje na macOS/Linux/Windows), generuje `.graphifyignore`, uruchamia offline ekstrakcje AST (`graphify update .`) i wypycha graf przez `cicd/sync_graph.py` (tool `replace_graph`). Uzyj przy pierwszym zasileniu grafu projektu albo do odswiezenia ad hoc bez CI.

---

## Konfiguracja projektu

Skille odczytuja slug projektu Monolynx w kolejnosci:
1. Zmienna srodowiskowa `MONOLYNX_PROJECT_SLUG` (lub `.env` w katalogu projektu)
2. Konfiguracja pluginu (`user_config.project_slug`)
3. Domyslny fallback: `monolynx`

Aby ustawic projekt na stale, dodaj do `.env`:
```
MONOLYNX_PROJECT_SLUG=twoj-projekt
```
