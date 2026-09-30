---
paths:
  - "plugin/scripts/**"
  - "plugin/hooks/**"
  - "cicd/**"
  - "src/monolynx/mcp_server.py"
  - "src/monolynx/api/**"
  - "src/monolynx/dashboard/**"
  - "src/monolynx/blog.py"
  - "src/monolynx/main.py"
  - "cli/src/**"
---

# Granica systemu: waliduj każdy element wejścia, nie tylko korzeń

Skrypt czytający JSON/XML/tekst z pliku, stdin albo z wyjścia innego narzędzia sprawdza kształt **każdego elementu kolekcji**, zanim go użyje. Sprawdzenie samego korzenia (`isinstance(data, list)`) nie jest walidacją: pierwszy zły element wewnątrz daje `KeyError`/`AttributeError`/`TypeError` i traceback zamiast obiecanego kodu wyjścia.

## Rule

1. Dla każdej kolekcji z wejścia (lista ticketów, `agents`, `mutants`, `outcomes`, wiersze tekstu) pętla walidacyjna sprawdza: element jest słownikiem (albo oczekiwanym typem), wymagane klucze istnieją, ich wartości mają oczekiwany typ i nie są puste, gdy pustka jest błędem.
2. Zły element kończy się komunikatem po polsku na stderr z indeksem i powodem (`tickets-file[3]: brak pola 'key'`) i kodem 2 (błąd wejścia). Wyjątek nigdy nie wychodzi z `main()`.
3. Walidacja stoi **przed** przetwarzaniem, nie w środku pętli roboczej: żaden efekt uboczny (zapis, komentarz, start sesji) nie zachodzi na podstawie częściowo zwalidowanych danych.
4. Element opcjonalny, którego brak da się bezpiecznie pominąć (np. wpis `agents` bez `id`), jest pomijany jawnie z komentarzem WHY, a nie przez łapanie wyjątku.
5. Test pokrywa co najmniej: element nie-dict, element bez wymaganego klucza, element z wartością złego typu.
6. Handler HTTP, który czyta z MinIO/S3 albo innego zewnętrznego serwisu, ma to wywołanie w `try/except` mapowanym na kod z kontraktu endpointu, z `logger.exception`. Publiczny endpoint (np. `/blog/`) odpowiada 404, nigdy 500. Lista agregująca wiele elementów degraduje pojedynczy element (np. puste streszczenie), zamiast wywracać całą odpowiedź.
7. Test pokrywa gałąź błędu storage (mock rzucający wyjątek) i asercję na kodzie odpowiedzi.
8. Klient HTTP (CLI `monolynx`) sprawdza kształt odpowiedzi API i pliku konfiguracyjnego edytowanego przez człowieka przed użyciem. Brak wymaganego pola albo zły typ kończy się komunikatem na stderr i kodem wyjścia z kontraktu CLI, nigdy tracebackiem ani cichym fallbackiem (pusty string, `.get(..., "")`) z kodem 0.

## Pitfall

Docstring obiecuje "kod 2 przy błędnym wejściu", a implementacja robi `data["key"]` w pętli. Ręcznie pisane fixture w testach mają zawsze poprawny kształt, więc gałąź nigdy nie jest ćwiczona; plik generowany przez LLM albo inne narzędzie w realnym przebiegu ma czasem brakujące pole.

## Regression

- MON-167: `build_report` czytał `ticket["key"]`/`ticket["status"]` bez walidacji; element bez klucza dawał traceback i kod 1 zamiast 2 (-10).
- MON-149: `parse_report()` dla `mte-json` walidował `files` jako dict, ale nie elementy `mutants`; zdeformowany wpis wypuszczał `AttributeError` (rekomendacja krytyka).
- MON-201: odczyt treści strony z MinIO na publicznym `/blog/{slug}` bez mapowania błędu dawał 500 zamiast 404 (-10). Retro sprintu "Publiczna wiki i blog" rozszerzyło `paths` o dashboard, `blog.py` i `main.py`.
- MON-190: błąd subprocessu niezmapowany na kod wyjścia skryptu.
- MON-125: odpowiedź HTTP i `config.toml` w CLI używane bez sprawdzenia kształtu (reguła roli powtórzona po MON-167 i MON-190).
- MON-133: `monolynx wiki get --raw` na odpowiedzi bez `content` wypisywał pusty string z exit 0 (-25, powtórka MON-125). Retro sprintu „CLI monolynx - fundament + REST API v2” dopisało `cli/src/**` do `paths` i punkt 8.

## What to check

- `isinstance(x, list)` / `isinstance(x, dict)` na korzeniu bez pętli po elementach -> brak walidacji elementów.
- Indeksowanie `elem["pole"]` na danych z zewnątrz bez wcześniejszego sprawdzenia -> traceback na granicy.
- `try/except Exception` wokół całego parsowania zamiast walidacji -> ukrywa, który element jest zły.
- Test błędów wejścia obejmuje tylko "plik nie istnieje" i "zły JSON" -> brakuje przypadków na poziomie elementu.
- Wywołanie `minio_client`/`get_page_content` w handlerze HTTP bez `try/except` -> 500 na błędzie storage.
- `data.get("pole", "")` albo `or ""` na odpowiedzi API w CLI, po którym komenda kończy się kodem 0 -> cichy fallback zamiast błędu kontraktu.
