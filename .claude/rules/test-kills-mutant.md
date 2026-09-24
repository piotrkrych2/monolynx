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

## Pitfall

Test pisany w TDD przed implementacją przechodzi po implementacji, więc wygląda na skuteczny. Nikt nie sprawdza, czy przeszedłby także bez niej. Krytyk wykrywa to dopiero, gdy ręcznie usunie chroniony fragment.

## Regression

- MON-202: seed wpisów bloga w kolejności oczekiwanej, test listy przechodził po usunięciu `ORDER BY` (-25, "korekta wraca").
- MON-197: test weryfikował obecność literału w pliku zamiast zachowania skilla.
- MON-191: asercja łapała echo promptu, nie wynik.
- MON-203: mutant `is_public=True` jako wartość domyślna przeżywał, dopiero iteracja dodała jawny test domyślnej wartości i asercję flasha.

## What to check

- Test sortowania z seedem wstawianym w tej samej kolejności, w jakiej asercja oczekuje wyniku.
- `assert response.status_code in (...)` z więcej niż jedną wartością.
- Parametr z wartością domyślną w nowej sygnaturze bez testu wywołania bez tego parametru.
- Asercja `in response.text` na stringu, który test sam wysłał w żądaniu.
