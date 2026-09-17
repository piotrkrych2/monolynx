---
name: brief
description: "Kontrakt epizodu bez ticketu: w jednej turze ustala, jak wyglada 'gotowe' (obserwowalny stan), czego nie ruszac i co agent robi sam, a przed czym sie zatrzymuje; po jawnej akceptacji od razu zaczyna prace. Dziala bez MCP i bez sluga. Uzyj na starcie kazdej pracy, ktora nie jest ticketem: konfiguracja, investigation, dokumentacja, drobna zmiana."
user-invocable: true
argument-hint: [goly opis zadania, np. "napraw build na CI"]
---

# Brief - kontrakt epizodu

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Uzytkownik otwiera epizod jednym zdaniem w trybie rozkazujacym. To zdanie
mowi, CO zrobic, a nie: po czym poznac, ze zrobione; czego przy okazji nie
psuc; co wolno zdecydowac samemu. Ten skill dopisuje te trzy rzeczy w JEDNEJ
turze i zapisuje je jako kontrakt sesji. Po akceptacji praca zaczyna sie od
razu, w tej samej turze, wg kontraktu.

Nie wymaga MCP ani sluga projektu. Chcesz kontrakt jako ticket -> wklej go do
`/monolynx:ticket-create` (sekcje Cel, Nie ruszac, Odwracalnosc); ten skill
tego nie robi, zeby nie zadawac drugiego pytania.

---

## KROK 1: Opis zadania

Argumentem jest faktyczny opis zadania z wiadomości użytkownika lub wejścia wywołania. Brak opisu -> zapytaj jednym zdaniem:
**"Co chcesz zrobic? Jedno zdanie wystarczy."** i poczekaj.

Nie dopytuj o szczegoly. Zwiezlosc opisu jest normalna - kontrakt ma ja
uzupelnic propozycja, nie przesluchaniem.

## KROK 2: Szybkie rozpoznanie (bez pytania uzytkownika)

Zanim zaproponujesz kontrakt, zbierz to, co widac bez rozmowy - jedno
wywolanie, wyniki do glowy:

```bash
git branch --show-current 2>/dev/null; git status --porcelain 2>/dev/null | head -20; git log --oneline -3 2>/dev/null; ls CLAUDE.md AGENTS.md .claude/rules 2>/dev/null
```

- `CLAUDE.md` / `AGENTS.md` / `.claude/rules/*.md` istnieja -> przeczytaj
  naglowki i reguly typu "nigdy X", "zawsze przez Y" - to gotowe pozycje
  "Nie ruszac" i "Stop przed".
- Brudne pliki w `git status` -> kandydaci do "Nie ruszac" (cudza / wczesniejsza
  praca).
- Branch `main`/`master` -> pozycja "Stop przed: zapis na main".
- Opis wspomina plik/modul -> `Grep` po nazwie, kto go jeszcze uzywa (sasiedzi
  = kandydaci do "Nie ruszac").

Rozpoznanie ma zajac sekundy, nie minuty. Nie uruchamiaj subagentow.

## KROK 3: Zaproponuj kontrakt - jedno pytanie

Wypelnij szablon konkretami z KROK 1-2 (nie zostawiaj placeholderow) i pokaz:

> **Kontrakt epizodu: [opis w 5-8 slowach]**
>
> **Gotowe, gdy** (stan, ktory da sie sprawdzic - nie lista edycji):
> - [np. `make test` zielony na CI, albo: strona /x renderuje liste bez bledu 500, albo: dokument ma sekcje A, B, C i nie zawiera dlugiego myslnika (U+2014)]
> - Sprawdzenie: [komenda / URL / plik, ktorym to potwierdzisz - kto uruchamia: ja / Ty]
>
> **Nie ruszac:**
> - [pliki/moduly/zachowania z KROK 2, kazde z powodem - albo "nic poza zakresem powyzej"]
>
> **Autonomia:**
> - Sam: [edycje w zakresie, nowe testy, czytanie wszystkiego, komendy read-only]
> - Stop przed: [z KROK 2 + domyslne: commit/push, migracja, wysylka poza system, nowa zaleznosc, usuwanie plikow, zmiana shared config/infra]
> - Eskaluje, gdy: [zadanie okaze sie wieksze niz opis, sprzecznosc z regulami repo, 2 nieudane podejscia]
>
> jawne „tak” = akceptujesz. Albo napisz, co zmienic (np. "commit sam", "gotowe = tez PR").

**Poczekaj na odpowiedź.** Użyj narzędzia pytań dostępnego w bieżącym trybie albo wiadomości. Cisza, timeout i brak odpowiedzi nigdy nie oznaczają zgody. Korekta uzytkownika nadpisuje propozycje. Nie
zadawaj drugiego pytania - jesli czegos nie wiesz, wpisz to do "Stop przed"
zamiast pytac teraz.

Zasady wypelniania:

1. **"Gotowe" jest obserwowalne przez osobe trzecia.** "Zrefaktorowany serwis"
   to nie stan; "testy serwisu zielone, publiczne sygnatury bez zmian" - tak.
   Zadanie badawcze (investigation) tez ma stan koncowy: "znana przyczyna
   z dowodem plik:linia" albo "lista 3 hipotez z kosztem sprawdzenia kazdej".
2. **Sprawdzenie jest nazwane.** Komenda, URL, plik. I kto je uruchamia -
   jesli uzytkownik, kontrakt to mowi, zeby agent nie udawal, ze sprawdzil.
3. **"Nie ruszac" ma powody.** Pozycja bez powodu jest zgadywaniem.
4. **"Stop przed" jest lista operacji, nie nastroju.** Kazda pozycja to
   konkretna czynnosc, przy ktorej agent przerywa i pyta.

## KROK 4: Zapisz kontrakt i zacznij

Po akceptacji wypisz koncowy kontrakt jeszcze raz w tym samym formacie
(to on zyje w kontekscie sesji do konca epizodu), zakonczony linia:

> Pracuje wg kontraktu. Zatrzymam sie przed: [lista "Stop przed" w jednej linii].

i w TEJ SAMEJ turze zacznij prace nad zadaniem. Zadnego "napisz, kiedy mam
zaczac" - jawna akceptacja była zgodą na kontrakt i na start.

## W trakcie pracy

- kazda pozycja "Stop przed" = przerwij i zapytaj ZANIM, nie po;
- "Gotowe" sprawdz nazwana metoda przed zgloszeniem konca; sprawdzenie nalezy
  do uzytkownika -> wypisz komende i poczekaj, nie raportuj "gotowe" na
  podstawie tego, ze kod wyglada dobrze;
- uzytkownik przerywa w trakcie -> kontrakt byl niekompletny: dopisz brakujaca
  pozycje do "Stop przed" i powiedz to jednym zdaniem.

---

## WAZNE ZASADY

1. **Jedno pytanie, jedna tura.** Skill nie prowadzi wywiadu. Propozycja
   z konkretami + jawne „tak”, potem od razu praca. Brak informacji laduje
   w "Stop przed", nie w pytaniu
2. **Nie zaczynaj zadania przed akceptacja** - nawet gdy jest trywialne.
   Kontrakt najpierw, praca po jawnej akceptacji
3. **Bez MCP** - skill nie wola tooli Monolynx; ticket z kontraktu robi
   `ticket-create`, jesli uzytkownik tego chce
4. **Placeholdery w propozycji sa bledem** - "[np. ...]" w wypisanym kontrakcie
   oznacza, ze KROK 2 nie zostal zrobiony
5. **Jezyk**: polski, z pelnymi diakrytykami w tresci dla uzytkownika
   (szablony w tym pliku sa technicznie ASCII)
