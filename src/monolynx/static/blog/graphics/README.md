# Grafiki wpisów bloga

Katalog z nazwanymi komponentami grafik, które wpis osadza blokiem `:::graphic`. Własny JS i CSS żyje wyłącznie tutaj, w repozytorium. Treść wpisu nigdy nie zawiera skryptu ani stylu.

Źródłem grafiki jest tekst: akapit opisu w bloku. Komponent jest warstwą nad opisem. Model AI i czytnik ekranu widzą opis, nie animację.

## Użycie we wpisie

```
:::graphic name="agent-loop"
Agent bierze ticket, pracuje nad nim, otwiera merge request, a na końcu
zapisuje wnioski w wiki. Wiki zasila kolejny ticket.
:::
```

- `name` musi być na zamkniętej liście `BLOG_GRAPHICS` w `src/monolynx/services/blog_blocks.py`. Nazwa spoza listy renderuje sam opis.
- Akapit opisu jest obowiązkowy. Blok bez opisu nie renderuje nic i jest zgłaszany przez `find_block_issues` (reguła `graphic-missing-description`).
- Skrypt i styl komponentu dołącza szablon `page.html` tylko do wpisu, który go używa.

## Kontrakt komponentu

Komponent `<nazwa>` to dwa pliki w tym katalogu: `<nazwa>.js` i `<nazwa>.css`.

`<nazwa>.js` rejestruje jedną funkcję `mount(el)`:

```js
window.BlogGraphics = window.BlogGraphics || {};
window.BlogGraphics["<nazwa>"] = { mount: function (el) { /* ... */ } };
```

- `el` to `<div class="blog-graphic" data-blog-graphic="<nazwa>">` z opisem w środku (`.blog-graphic-description`).
- `blocks.js` woła `mount(el)` po załadowaniu strony. Wyjątek w `mount` nie psuje reszty strony, a wpis zostaje z samym opisem.
- Po udanym `mount` `blocks.js` dodaje do `el` klasę `blog-graphic-mounted`. Opis zostaje wtedy w DOM, ale tylko dla czytników ekranu. Schemat sam dostaje `aria-hidden="true"` albo `role="img"` z etykietą.
- Bez JS widać wyłącznie opis. Nie ukrywaj niczego w CSS bez udziału JS.
- Reguły CSS komponentu zaczynają się od `.blog-graphic[data-blog-graphic="<nazwa>"]`. Motyw ciemny przez `.dark` na `<html>`, w obu motywach kontrast ma być czytelny.
- `prefers-reduced-motion: reduce` oznacza statyczny schemat, bez animacji.

Czego komponentowi nie wolno:

- żadnych żądań zewnętrznych: `fetch`, `XMLHttpRequest`, obrazów, fontów ani skryptów z sieci,
- bibliotek i zależności spoza pliku komponentu,
- `innerHTML` z treścią pobraną z DOM wpisu (opis czytaj przez `textContent`),
- zmian poza `el`: globalnych stylów, `document.body`, cudzych elementów,
- nazwy komponentu pochodzącej z treści wpisu w ścieżce pliku. Pliki ładuje szablon wyłącznie dla nazw z `BLOG_GRAPHICS`.

## Dodanie nowego komponentu

1. Utwórz `<nazwa>.js` i `<nazwa>.css` w tym katalogu. Wzorzec: `agent-loop.js` i `agent-loop.css`.
2. Dopisz `<nazwa>` do `BLOG_GRAPHICS` w `src/monolynx/services/blog_blocks.py`. Bez wpisu komponent się nie załaduje.
3. Opisz komponent w MR. Przegląd obejmuje kontrakt powyżej, w tym brak żądań zewnętrznych i obsługę `prefers-reduced-motion`.
4. Sprawdź ręcznie w obu motywach, przy wyłączonym JS i z włączonym ograniczeniem animacji w systemie.

## Komponenty

- `agent-loop` - animowany schemat pętli pracy agenta: ticket, agent, merge request, wiki. Podpisy w schemacie są po polsku także na wpisie anglojęzycznym (blog jest polski). Animacja zatrzymuje się, gdy karta jest ukryta, a timer kończy się po usunięciu elementu z dokumentu.
