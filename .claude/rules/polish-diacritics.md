---
paths:
  - "src/monolynx/**"
  - "cli/src/**"
  - "plugin/**"
  - "tests/**"
  - "cli/tests/**"
---

# Tekst po polsku widoczny dla użytkownika ma pełne diakrytyki

Komunikat błędu, pole `reason`, tekst w UI, pomoc komendy CLI i treść skilla to tekst, który czyta człowiek. Polski tekst bez `ą ć ę ł ń ó ś ź ż` wygląda na błąd i rozjeżdża się z resztą interfejsu, a testy porównujące komunikat z kontraktem ticketu padają.

## Rule

1. Każdy nowy albo zmieniony literał po polsku, który trafia do użytkownika albo do integracji (komunikat `ValueError` zwracany przez MCP i API v2, `detail` w problem+json, flash, `reason` w wyniku skryptu, help CLI, treść `SKILL.md`), ma pełne polskie znaki.
2. Tekst przepisujesz z kontraktu ticketu znak w znak. Komunikat podany w tickecie z diakrytykami nie traci ich w kodzie.
3. Przed raportem grepujesz nowe literały z diffu pod kątem słów bez diakrytyków (np. `uzytkownik`, `bledny`, `postepu`, `ticketow`, `czlonkiem`).
4. Wyjątki: identyfikatory kodu, klucze JSON, nazwy flag, zamrożone linie kontraktu (`SPRINT-RUN: ...`, `TRANSPORT: ...`) i istniejące komunikaty, od których zależy dopasowanie tekstu w innym miejscu (np. lista not-found w `api/v2/common.py::service_error()`) - tych nie zmieniasz przy okazji.

## Pitfall

Agent pisze komunikat „z ręki” w ASCII, bo sąsiedni starszy kod tak wygląda, a test pisany równolegle przez drugiego agenta asertuje tekst z kontraktu. Wynik to czerwone testy w gate albo odrzucony raport i dodatkowa runda, choć logika jest poprawna.

## Regression

- MON-238: komunikat błędu nieznanego assignee bez diakrytyków, 21 czerwonych testów w gate i dodatkowa iteracja.
- MON-252: `reason` frazy oczekiwania w `plugin/scripts/sprint_run_state.py` bez diakrytyków, niezgodny z kontraktem ticketu, poprawka po rekoncyliacji.
- Retro sprintu „Fix uprawnień”: dwa tickety, reguła zapisana.

## What to check

- Nowy literał po polsku w `raise ValueError(...)`, `flash(...)`, `detail=`, `reason=`, `help=` bez żadnego znaku diakrytycznego, choć słowa ich wymagają.
- Komunikat w kodzie różni się od komunikatu w tickecie tylko diakrytykami.
- Test asertuje tekst z diakrytykami, a kod zwraca wersję ASCII (albo odwrotnie).
