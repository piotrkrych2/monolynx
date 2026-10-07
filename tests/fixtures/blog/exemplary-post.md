# Jak agent AI czyta wpis na blogu

> [!TLDR]
> - Wpis zaczyna się od streszczenia z kilkoma punktami.
> - Każdy obrazek i każda grafika ma opis tekstowy.
> - Nagłówki idą po kolei, a sekcje są samodzielne.

## Dlaczego struktura wpisu ma znaczenie?

Agent AI czyta wpis od góry do dołu i wycina z niego fragmenty. Sekcja, która zaczyna się od odwołania do poprzedniej, traci sens po wycięciu.

![Schemat obiegu pracy agenta w projekcie](agent-loop.png)

Opis procedury znajdziesz w [dokumentacji klienta CLI Monolynx](https://monolynx.com/docs/cli).

## Jak wygląda obieg pracy agenta?

Ticket trafia do agenta, agent otwiera zmianę, a wynik ląduje w wiki projektu.

:::graphic name="agent-loop"
Schemat pokazuje obieg: ticket trafia do agenta, agent otwiera zmianę, a wynik trafia do wiki.
:::

### Przykład polecenia

Przykład uruchomienia lintu z poziomu terminala:

```bash
# komentarz w kodzie nie jest nagłówkiem
monolynx blog lint 3f2a9c1e
```

## Najczęstsze pytania

Odpowiedzi na pytania czytelników zebraliśmy w jednym miejscu.

:::faq
### Czy lint zmienia wpis?
Nie, lint tylko czyta treść i zwraca listę problemów.

### Czy każdy problem blokuje publikację?
Blokują tylko błędy, ostrzeżenia i informacje nie.
:::
