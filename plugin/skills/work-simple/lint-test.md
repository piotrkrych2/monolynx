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
> python3 [katalog skilla]/scripts/test_lock.py -- [komenda test wybranego wariantu]
> ```

**`MONOLYNX_AUTOTEST=true`** - odpal komendy sam.

Wariant worktree wymaga podniesionego stacku glownego checkoutu - siec, obraz
i baza pochodza stamtad. Stack nie stoi -> to brakujace srodowisko, nie blad
kodu: podnies go albo powiedz o tym uzytkownikowi zamiast raportowac czerwony
wynik.

### Testy ida przez blokade

Rownolegle sesje w kilku worktree dziela jedno srodowisko testow (baza, porty,
kontenery glownego checkoutu). KAZDA komende testow - pelny przebieg, przebieg
zawezony, oba przebiegi trybu defektu i pelny przebieg po ostatniej zmianie -
uruchamiasz przez skrypt z katalogu tego skilla:

```bash
python3 <katalog skilla>/scripts/test_lock.py -- <komenda testow>
```

Skrypt bierze blokade na pliku `<git-common-dir>/monolynx/test.lock`, wspolnym
dla glownego checkoutu i wszystkich worktree, i dopiero potem uruchamia komende.
Zajeta blokada to kolejka: skrypt czeka, az poprzedni przebieg sie skonczy.
Lint NIE idzie przez blokade.

- **`<katalog skilla>`** to absolutna sciezka wpisana DOSLOWNIE - bez `$VAR`,
  `~` i `$(...)`. To samo ograniczenie co `"$PWD"` w komendzie worktree: sesja
  w worktree odrzuca wartosci liczone w locie.
- **Komenda po `--` jest uruchamiana bez powloki** (jako lista argumentow),
  wiec nie moze zawierac skladni powloki: `|`, `;`, `&&`, przekierowan,
  zmiennych. Komenda testow w formie `docker compose ... run ... pytest ...`
  to spelnia. Komenda, ktora powloki wymaga, dostaje jawne `sh -c "..."` po
  `--`.
- **Hook ocenia owinieta komende tak samo jak nieowinieta**: prosta forma
  wrappera jest rozpakowywana i podlega regulom samej komendy testow (pytanie
  o zgode bez `MONOLYNX_AUTOTEST=true`). Wrapper niczego nie odblokowuje. Przy
  skladni powloki w komendzie (takze w `sh -c "a && b"`) hook wrappera nie
  rozpakowuje i ocenia caly napis starymi regulami.
- **`--timeout <s>`** (liczba calkowita 0..86400, czyli do 24 h, przed `--`;
  wartosc spoza zakresu to kod 64) zmienia limit czekania
  na blokade; domyslnie 3600 s (60 min), `0` to jedna proba bez czekania.
  Bez powodu go nie podajesz.
- **`MONOLYNX_AUTOTEST=false`** - uzytkownikowi wypisujesz komende testow JUZ
  owinieta, jak w bloku wyzej.

**Wylacznik**: pole `izolacja testów: tak` w sekcji `## Test` strony `toolchain`
(zapis `izolacja testow: tak` liczy sie tak samo) oznacza, ze kazdy przebieg ma
wlasne srodowisko - testy ida wtedy BEZ blokady, sama komenda. Brak pola albo
kazda inna wartosc = `brak` = blokada wlaczona.

### Linie `TEST-LOCK:` i kody wyjscia

Skrypt pisze na stderr linie `TEST-LOCK:` i zwraca kod komendy testow.

| Co widzisz | Znaczenie | Co robisz |
|---|---|---|
| `TEST-LOCK: acquired` | blokada zdobyta, komenda ruszyla | czytasz wynik testow jak zwykle |
| `TEST-LOCK: waiting holder=<pid>/<worktree>` albo `holder=unknown` | blokade trzyma inny przebieg, czekasz w kolejce | nic - to nie blad |
| `TEST-LOCK: timeout` i kod 75 | limit czekania minal, komenda NIE zostala uruchomiona | postepowanie nizej |
| `test_lock: ...` i kod 64 | wrapper nie mogl ruszyc, komenda NIE zostala uruchomiona: blad skladni (brak `--`, zla flaga, pusta komenda), `--timeout` poza zakresem 0..86400, katalog poza repozytorium git albo blad gita, blad otwarcia pliku blokady | przeczytaj komunikat `test_lock: ...` i zdecyduj wg niego: blad skladni albo zakresu -> popraw komende i uruchom ponownie; git albo plik blokady -> to brakujace srodowisko, poprawianie komendy nie pomoze |

Kod 75 z linia `TEST-LOCK: timeout` to brakujace srodowisko, nie czerwony test:
NIE uruchamiasz poprawki agenta i NIE zuzywasz limitu poprawek.

- **Tryb interaktywny** - powiedz uzytkownikowi, kto trzyma blokade (`holder`
  z linii `waiting`), i zapytaj, czy ponowic.
- **Sesja w tle** (`MONOLYNX_SPRINT_RUN=1`) - koniec tury wg reguly trybu sesji
  w tle skilla, ktory czyta ten plik, z powodem `kolizja testów`. Ostatnia
  linia odpowiedzi: `SPRINT-RUN STOP: kolizja testów`.

Kod 75 albo 64 BEZ linii `TEST-LOCK: timeout` / `test_lock:` to kod samej
komendy testow - traktujesz go jak kazdy inny wynik testow. Pozostale kody
skryptu: 127 (komendy nie ma), 126 (brak uprawnien do jej uruchomienia),
128+N (komenda zabita sygnalem N) - to tez nie sa czerwone testy, tylko
komenda, ktora nie zadzialala.

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

Oba przebiegi testu ida przez blokade, jak kazda komenda testow (sekcja
"Wykonanie"); `git stash` i `git stash pop` sa poza nia.

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
- **Cokolwiek czerwone** -> najpierw sprawdz, czy to nie kolizja srodowiska
  (sekcja "Kolizja srodowiska testow") ani timeout blokady (kod 75 z linia
  `TEST-LOCK: timeout`) - oba przypadki maja wlasne postepowanie i nie sa
  czerwonym wynikiem. W kazdym innym razie uruchom poprawke agenta
  odpowiedzialnego za wadliwy obszar z trescia bledu (mechanika re-run wg tego
  flow). Po poprawce powtorz lint i testy. Kazda poprawka konsumuje limit
  poprawek obowiazujacy w tym flow - po jego wyczerpaniu zapytaj uzytkownika.
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

## Kolizja srodowiska testow

Blokada szereguje przebiegi, ktore przez nia ida. Przebieg spoza niej (reczny,
z innego narzedzia, ze starszej wersji skilla) nadal moze wejsc na wspoldzielony
stan: baze, port, katalog.

**Rozpoznanie** - oba warunki naraz:

1. Pelny przebieg konczy sie masowymi `errors` w setupie: bledy fixture,
   polaczenia albo bazy w wielu niezwiazanych ze soba testach. Nie `failed`
   z asercji.
2. Testy zawezone do zmienionego obszaru sa ZIELONE.

To kolizja srodowiska, nie wada kodu: NIE uruchamiasz poprawki agenta i NIE
zuzywasz limitu poprawek. Zawezone testy czerwone -> to zwykly czerwony wynik,
nie kolizja; obowiazuje "Decyzja po wyniku".

**Postepowanie** - JEDNA powtorka pelnego przebiegu przez blokade:

- **Zielono** -> dalej, jak po kazdym zielonym przebiegu.
- **Dalej czerwono w ten sam sposob**:
  - sesja w tle (`MONOLYNX_SPRINT_RUN=1`) - koniec tury wg reguly trybu sesji
    w tle skilla, ktory czyta ten plik (komentarz do ticketu, domkniecie
    instrumentacji), z powodem `kolizja testów`. Ostatnia linia odpowiedzi:
    `SPRINT-RUN STOP: kolizja testów`;
  - tryb interaktywny - jedno pytanie do uzytkownika, z opisem objawu
    i propozycja ponowienia, gdy srodowisko bedzie wolne.

NIGDY petla czekania (np. na znikniecie cudzych kontenerow) ani kolejne
powtorki. Kolejke obsluguje blokada, nie ty.

---

## Pelny przebieg po OSTATNIEJ zmianie

Pelny przebieg to wszystkie komendy lint i test WYBRANEGO wariantu, bez
zawezania; testy ida przez blokade (sekcja "Wykonanie"). Zielony wynik z iteracji zawezonej do dotknietego obszaru NIE
wystarcza: jesli od ostatniego PELNEGO przebiegu zmienil sie w repo choc jeden
plik, powtorz pelny przebieg teraz (mechanika jak wyzej; AUTOTEST=false ->
uruchamia uzytkownik i wkleja). Czerwony -> poprawka jak kazda inna, limit
obowiazuje. Dopiero zielony pelny przebieg, po ktorym nikt niczego nie zmienil,
domyka gate.

Bez iteracji poprawkowych warunek spelnia pierwszy pelny przebieg. Zapisz,
ktory przebieg byl ostatni i czy byl pelny.
