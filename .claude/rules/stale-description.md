---
paths:
  - "src/**"
  - "plugin/**"
  - "scripts/**"
  - "tests/**"
---

# Opis zmienionego elementu aktualizujesz w tym samym diffie

Docstring modułu, docstring funkcji, docstring testu, podsumowanie sekcji skilla i tabela "Ważne zasady" deklarują zachowanie. Po zmianie zachowania stara deklaracja kłamie i wprowadza w błąd następnego agenta, który czyta opis zamiast kodu.

## Rule

1. Zmieniając zachowanie funkcji, modułu, testu albo skilla, w tym samym diffie aktualizujesz każdy opis, który to zachowanie deklaruje: docstring modułu i funkcji, docstring testu, podsumowanie sekcji, wiersz tabeli zasad.
2. Przed raportem grepujesz w zmienianym pliku starą nazwę, stary parametr albo stare zachowanie. Każde trafienie w opisie poprawiasz albo uzasadniasz w raporcie.
3. Nowy router, endpoint albo sekcja dopisana do istniejącego modułu wymaga aktualizacji docstringu modułu, jeśli ten wylicza, co moduł zawiera.

## Pitfall

Diff zmienia ciało funkcji, a docstring kilkadziesiąt linii wyżej zostaje poza kontekstem edycji. Testy tego nie łapią, lint też nie. Rozjazd widać dopiero w review albo gdy kolejny agent zaufa opisowi.

## Regression

- MON-202: docstring `src/monolynx/blog.py:1` opisywał tylko render pojedynczego wpisu po dodaniu listy `/blog/` (-5).
- MON-197: docstring zmienionej funkcji po zmianie brancha bazowego zostawiony jako follow-up (-5).
- MON-191: docstring testu opisywał inne zachowanie niż asercja.

## What to check

- Diff zmienia sygnaturę albo zachowanie, a docstring tej funkcji lub modułu nie jest w diffie.
- Grep starej nazwy w zmienianym pliku zwraca trafienia w komentarzach lub docstringach.
- Sekcja "Ważne zasady" skilla sprzeczna z krokiem, który diff zmienił.
