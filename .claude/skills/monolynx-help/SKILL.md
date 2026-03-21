---
name: monolynx-help
description: "Wyswietl instrukcje uzycia skilli Monolynx — flow pracy z ticketami oraz skille dodatkowe. Uzyj gdy chcesz wiedziec jak pracowac z Monolynx w Claude Code."
user-invocable: true
argument-hint: ""
allowed-tools: ""
---

# Monolynx Skills — Przewodnik

Wyswietl ponizszy przewodnik uzytkownikowi i zakoncz. Nie wykonuj zadnych dodatkowych akcji.

---

## Flow pracy z ticketem

Skille Monolynx tworza kompletny przepis pracy — od pomyslu do realizacji:

### 1. `/monolynx-ticket-create [opis zadania]`

Tworzysz nowy ticket. Skill zbiera kontekst z wiki, kodu i grafu zaleznosci, a nastepnie generuje pelny opis ticketu (cel, kontekst, zakres, kryteria akceptacji, zaleznosci). Ticket trafia do sprintu lub backlogu.

### 2. `/monolynx-ticket-review [ticket-id lub klucz np. MNX-12]`

Recenzujesz ticket przed podjieciem pracy. Skill sprawdza forme ticketu, weryfikuje zalozenia z wiki i kodem, i generuje raport z ocena. Mozesz uruchomic review kilka razy — po kazdej poprawce ticketu, az opis bedzie kompletny i jednoznaczny.

**Wskazowka**: Powtarzaj cykl *review → poprawka → review* az raport pokaze same "OK" w formie i "ZGODNE" w zalozeniach. Dobrze zrecenzowany ticket = szybsza realizacja.

### 3. `/monolynx-work [ticket-id lub klucz np. MNX-12]`

Podejmujesz ticket do realizacji. Skill waliduje branch, uruchamia Researchera, dobiera zespol agentow i prowadzi rownolegle prace z obowiazkowym krytykiem. Na koniec loguje czas pracy.

---

## Skille dodatkowe

Te skille dzialaja niezaleznie od powyzszego flow:

### `/monolynx-search [pytanie]`

Wyszukiwanie semantyczne (RAG) w wiki projektu. Uzyj gdy potrzebujesz informacji z dokumentacji — o architekturze, API, integracjach, standardach kodu. Aktywuje sie tez automatycznie gdy pytasz o dokumentacje projektu.

### `/monolynx-create-graph-ci-script`

Generuje skrypt CI (`cicd/sync_graph.py`) i stage w `.gitlab-ci.yml`, ktory automatycznie synchronizuje graf zaleznosci kodu z platforma Monolynx. Dzieki temu modul Polaczenia zawsze odzwierciedla aktualna strukture kodu. Uzyj raz w kazdym projekcie Python — potem CI robi reszte.
