---
paths:
  - "plugin/scripts/**"
  - "plugin/hooks/**"
  - "cicd/**"
  - "src/monolynx/mcp_server.py"
  - "src/monolynx/api/**"
---

# Granica systemu: waliduj każdy element wejścia, nie tylko korzeń

Skrypt czytający JSON/XML/tekst z pliku, stdin albo z wyjścia innego narzędzia sprawdza kształt **każdego elementu kolekcji**, zanim go użyje. Sprawdzenie samego korzenia (`isinstance(data, list)`) nie jest walidacją: pierwszy zły element wewnątrz daje `KeyError`/`AttributeError`/`TypeError` i traceback zamiast obiecanego kodu wyjścia.

## Rule

1. Dla każdej kolekcji z wejścia (lista ticketów, `agents`, `mutants`, `outcomes`, wiersze tekstu) pętla walidacyjna sprawdza: element jest słownikiem (albo oczekiwanym typem), wymagane klucze istnieją, ich wartości mają oczekiwany typ i nie są puste, gdy pustka jest błędem.
2. Zły element kończy się komunikatem po polsku na stderr z indeksem i powodem (`tickets-file[3]: brak pola 'key'`) i kodem 2 (błąd wejścia). Wyjątek nigdy nie wychodzi z `main()`.
3. Walidacja stoi **przed** przetwarzaniem, nie w środku pętli roboczej: żaden efekt uboczny (zapis, komentarz, start sesji) nie zachodzi na podstawie częściowo zwalidowanych danych.
4. Element opcjonalny, którego brak da się bezpiecznie pominąć (np. wpis `agents` bez `id`), jest pomijany jawnie z komentarzem WHY, a nie przez łapanie wyjątku.
5. Test pokrywa co najmniej: element nie-dict, element bez wymaganego klucza, element z wartością złego typu.

## Pitfall

Docstring obiecuje "kod 2 przy błędnym wejściu", a implementacja robi `data["key"]` w pętli. Ręcznie pisane fixture w testach mają zawsze poprawny kształt, więc gałąź nigdy nie jest ćwiczona; plik generowany przez LLM albo inne narzędzie w realnym przebiegu ma czasem brakujące pole.

## Regression

- MON-167: `build_report` czytał `ticket["key"]`/`ticket["status"]` bez walidacji; element bez klucza dawał traceback i kod 1 zamiast 2 (-10).
- MON-149: `parse_report()` dla `mte-json` walidował `files` jako dict, ale nie elementy `mutants`; zdeformowany wpis wypuszczał `AttributeError` (rekomendacja krytyka).

## What to check

- `isinstance(x, list)` / `isinstance(x, dict)` na korzeniu bez pętli po elementach -> brak walidacji elementów.
- Indeksowanie `elem["pole"]` na danych z zewnątrz bez wcześniejszego sprawdzenia -> traceback na granicy.
- `try/except Exception` wokół całego parsowania zamiast walidacji -> ukrywa, który element jest zły.
- Test błędów wejścia obejmuje tylko "plik nie istnieje" i "zły JSON" -> brakuje przypadków na poziomie elementu.
