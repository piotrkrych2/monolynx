# Lint i testy - gate przed review

Procedura wspolna dla flow pracy nad ticketem. Krytyk ocenia kod, ktory sie
kompiluje i przechodzi testy - tani gate przed drogim review. Ticket nie trafia
do `in_review` z czerwonymi testami.

Ten plik opisuje SAMA procedure. Moment jej uruchomienia, wysokosc limitu
poprawek i miejsca raportowania naleza do skilla, ktory go czyta.

---

## Skad komendy

Ze strony wiki `toolchain` projektu - z sekcji "Toolchain" raportu Researchera
albo z tresci strony pobranej wprost przez skill.

**Brak strony `toolchain`** - poinformuj uzytkownika:

> Projekt nie ma strony wiki `toolchain`, wiec nie znam komend lint/test.
> Uruchom `/monolynx:project-toolchain` zeby ja utworzyc (jednorazowo).
>
> Kontynuowac bez lintu i testow? (tak / podam komendy recznie)

Poczekaj na odpowiedz. Bez potwierdzenia nie zmieniaj statusu ticketu.

---

## Wybor wariantu: worktree czy glowny checkout

Strona `toolchain` moze miec dwa komplety komend. Zanim cokolwiek uruchomisz,
musisz wiedziec, gdzie pracujesz. Sprawdzasz to NA HOSCIE (kontener nie ma
binarki `git`, a `.git` w worktree to plik ze sciezka hosta) DWIEMA zwyklymi
komendami, porownujac wyniki sam - jednolinijkowiec z
`[ "$(...)" != "$(...)" ]` zostanie odrzucony przez guard izolacji worktree:

```bash
git rev-parse --git-dir
```

```bash
git rev-parse --git-common-dir
```

- **Wyniki rozne** (np. `.../.git/worktrees/MON-166` wobec `.../.git`) -
  pracujesz w git worktree: komendy z sekcji `## Worktree`, pola `lint:`
  i `test:`. Wartosc `jak wyzej` w ktorymkolwiek polu odsyla do `## Lint` /
  `## Test`.
- **Wyniki identyczne** - glowny checkout: komendy z `## Lint` i `## Test`,
  jak dotad.

Skill, ktory ustalil srodowisko wczesniej, tej detekcji nie powtarza - bierze
zapamietana wartosc.

**Podstawienia w komendzie worktree**: sekcja `## Worktree` zapisuje komende
w formie przenosnej, z `"$PWD"`. Przed uruchomieniem podstaw za nia DOSLOWNY
wynik `pwd` (sciezka absolutna). W komendzie `docker`, ktora faktycznie
uruchamiasz, nie moze zostac zaden `$VAR` ani `$(...)` - sesja pracujaca
w worktree odrzuca komendy z wartoscia obliczana w locie. Forma
`sh -c "a && b && c"` z literalna sciezka przechodzi.

**Praca idzie w worktree, a strona nie ma sekcji `## Worktree`** - poinformuj
uzytkownika:

> Strona `toolchain` nie ma sekcji `## Worktree`, a praca idzie w git worktree.
> Komendy z `## Lint` i `## Test` (`make`, `docker compose exec`) sprawdzilyby
> glowny checkout, nie ten kod.
> Uruchom `/monolynx:project-toolchain`, zeby ta sekcje dodac.
>
> Kontynuowac bez lintu i testow? (tak / podam komendy recznie)

Poczekaj na odpowiedz. Bez potwierdzenia nie zmieniaj statusu ticketu. Brak
sekcji `## Worktree` POZA worktree to stan normalny - zadnego komunikatu,
pracujesz komendami z `## Lint` i `## Test`.

---

## Wykonanie

Uruchamiasz komendy WYBRANEGO wariantu.

**`MONOLYNX_AUTOTEST=false`** (domyslnie) - wypisz komendy i poczekaj na wynik:

> Uruchom i wklej wynik:
> ```
> [komenda lint wybranego wariantu]
> [komenda test wybranego wariantu]
> ```

**`MONOLYNX_AUTOTEST=true`** - odpal komendy sam.

Wariant worktree wymaga podniesionego stacku glownego checkoutu - siec, obraz
i baza pochodza stamtad. Stack nie stoi -> to brakujace srodowisko, nie blad
kodu: podnies go albo powiedz o tym uzytkownikowi zamiast raportowac czerwony
wynik.

---

## TRYB = defekt - najpierw czerwony, potem zielony

Developer nie mogl uruchomic testu (hook blokuje testy subagentom), wiec test
regresyjny nigdy nie byl czerwony i sam w sobie niczego nie dowodzi. Zanim
pojdzie pelny przebieg, wykonaj (AUTOTEST=false -> wypisz uzytkownikowi
z prosba o wklejenie OBU wynikow):

```bash
git stash -- <pliki fixu, BEZ pliku testu>
<komenda testu z toolchain zawezona do testu regresyjnego>
git stash pop
<ta sama komenda>
```

Oczekiwane: pierwszy przebieg CZERWONY, drugi ZIELONY. Test zielony takze bez
fixu -> nie przechodzi przez mechanizm: re-run developera z ta informacja
(konsumuje limit poprawek). Projekt bez testow -> zamiast komendy testu kroki
reprodukcji z raportu developera, przed `stash pop` i po nim. Oba wyniki
doslownie do logu instrumentacji lintu i testow oraz do komentarza
podsumowujacego. Uzyj `git stash --`, nie `git stash push` - hook czyta `push`
jak git push i pyta niepotrzebnie.

---

## Decyzja po wyniku

- **Wszystko zielone** -> dalej, do review krytyka.
- **Cokolwiek czerwone** -> uruchom poprawke agenta odpowiedzialnego za wadliwy
  obszar z trescia bledu (mechanika re-run wg tego flow). Po poprawce powtorz
  lint i testy. Kazda poprawka konsumuje limit poprawek obowiazujacy w tym
  flow - po jego wyczerpaniu zapytaj uzytkownika.
- **Uzytkownik swiadomie pominal** -> odnotuj w podsumowaniu koncowym jako
  "lint/testy pominiete" i przekaz te informacje krytykowi. Krytyk lintu
  i testow i tak nie punktuje (to gate, nie kategoria rubryki), ale odjecie
  "-20 brak testu dla nowego kodu" STOSUJE nadal - pominiecie URUCHOMIENIA
  testow nie znosi obowiazku ich NAPISANIA.

**Kto poprawia, gdy blad przecina przydzialy kilku agentow**: poprawia agent,
ktorego zmiana bezposrednio wywolala blad (czytaj traceback/komunikat);
niejednoznaczne -> agent z wiekszym udzialem w diffie wadliwego obszaru. Runda
konsumuje licznik TYLKO poprawianego agenta. Flow z jednym developerem ma tu
zawsze jednego adresata.

---

## Pelny przebieg po OSTATNIEJ zmianie

Pelny przebieg to wszystkie komendy lint i test WYBRANEGO wariantu, bez
zawezania. Zielony wynik z iteracji zawezonej do dotknietego obszaru NIE
wystarcza: jesli od ostatniego PELNEGO przebiegu zmienil sie w repo choc jeden
plik, powtorz pelny przebieg teraz (mechanika jak wyzej; AUTOTEST=false ->
uruchamia uzytkownik i wkleja). Czerwony -> poprawka jak kazda inna, limit
obowiazuje. Dopiero zielony pelny przebieg, po ktorym nikt niczego nie zmienil,
domyka gate.

Bez iteracji poprawkowych warunek spelnia pierwszy pelny przebieg. Zapisz,
ktory przebieg byl ostatni i czy byl pelny.
