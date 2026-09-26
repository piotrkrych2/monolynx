---
paths:
  - "tests/**"
---

# Test musi paść na mutancie kodu, który chroni

Nowy test ma dowodzić zachowania, nie tylko przechodzić. Jeśli po usunięciu albo odwróceniu kodu, który test ma chronić (ORDER BY, filtr, wartość domyślna, gałąź warunku), test dalej jest zielony, to test nic nie chroni.

## Rule

1. Każda asercja nowego testu musi padać po usunięciu lub odwróceniu kodu, który chroni. Przed raportem wskaż w myślach mutanta (np. "usuwam `order_by`") i sprawdź, że asercja go zabija.
2. Seed danych do testu sortowania lub filtrowania układasz tak, żeby kolejność wstawiania różniła się od oczekiwanej kolejności wyniku. Seed w kolejności oczekiwanej przepuszcza usunięcie sortowania.
3. Status odpowiedzi sprawdzasz jedną oczekiwaną wartością (`== 303`), nie zbiorem (`in (200, 303)`).
4. Wartość domyślną parametru (np. `is_public=False` w narzędziu MCP) testujesz jawnie wywołaniem bez tego parametru i asercją na stanie po zapisie.
5. Asercja na tekście, który test sam podał na wejściu (echo promptu, echo formularza), albo grep literału w pliku źródłowym nie jest testem zachowania.
6. Test regexu z granicą słowa `\b` używa wejścia, na którym wariant bez `\b` daje inny wynik: `1MON-5` (brak wiodącego `\b`) i `MON-5a` (brak końcowego `\b`). `XMON-5` nie zabija mutanta, bo `re.search` i tak znajdzie `MON-5` dalej w napisie. Przed oddaniem sprawdź pełny wzorzec i oba warianty bez `\b` przez `re.findall` na docelowym wejściu.
7. Test commita działa w modelu transakcji fixture: `conftest.py` wiąże sesję z zewnętrzną transakcją, więc commit nie wychodzi poza nią i odczyt na innym połączeniu nic nie dowodzi. Sprawdź, że test pada na mutancie „usuń `db.commit()`” (np. spy na `db.commit()`), zanim go oddasz.
8. Test walidacji, której broni więcej niż jedna warstwa (filtr w handlerze i walidacja w serwisie), asertuje efekt konkretnej warstwy (np. `location` odpowiedzi, treść komunikatu), inaczej przechodzi także po usunięciu tej warstwy.

## Pitfall

Test pisany w TDD przed implementacją przechodzi po implementacji, więc wygląda na skuteczny. Nikt nie sprawdza, czy przeszedłby także bez niej. Krytyk wykrywa to dopiero, gdy ręcznie usunie chroniony fragment.

## Regression

- MON-202: seed wpisów bloga w kolejności oczekiwanej, test listy przechodził po usunięciu `ORDER BY` (-25, "korekta wraca").
- MON-197: test weryfikował obecność literału w pliku zamiast zachowania skilla.
- MON-191: asercja łapała echo promptu, nie wynik.
- MON-203: mutant `is_public=True` jako wartość domyślna przeżywał, dopiero iteracja dodała jawny test domyślnej wartości i asercję flasha.
- MON-198: pierwszy test relacji `blocked_by` używał jawnego `selectinload`, więc nie łapał braku `join_depth` (martwe `lazy="selectin"`); dopiero test bez `.options()` zabił mutanta.
- MON-199: test „bloker z innego projektu” w edycji przeszedłby bez filtra kandydatów, bo serwis i tak rzucał `ValueError` - brak asercji `location`.
- MON-123: asercja na `slugs` w `test_flagged_page_with_backlink_not_counted_as_skipped` przechodziła bez nowego filtra; wartość niósł tylko `skipped_generated == 0`.
- MON-200: testy treści skilli `next` i `ticket-review` bez asercji na `done` i na wiersz tabeli.
- MON-207: test `\b` na `XMON-5` i test commita nie zabijały mutantów (-25, runda 4 qa-testera). Retro sprintu „Zależności między ticketami”: reguła istnieje i jest łamana w 5 ticketach, a trend mutacji nie jest prowadzony (`narzedzie: brak` na stronie `toolchain`) - eskalacja do instalacji narzędzia mutacyjnego.

## What to check

- Test sortowania z seedem wstawianym w tej samej kolejności, w jakiej asercja oczekuje wyniku.
- `assert response.status_code in (...)` z więcej niż jedną wartością.
- Parametr z wartością domyślną w nowej sygnaturze bez testu wywołania bez tego parametru.
- Asercja `in response.text` na stringu, który test sam wysłał w żądaniu.
- Test regexu z `\b` bez wejścia typu `1MON-5` / `MON-5a`.
- Test commita, który czyta dane na osobnym połączeniu pod fixture z rollbackiem, zamiast sprawdzić samo wywołanie `commit`.
- Test ścieżki odrzucenia, gdy odrzuca także inna warstwa, bez asercji na efekt testowanej warstwy.
