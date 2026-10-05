---
name: monolynx-project-toolchain
description: "Wykryj komendy lint i testow biezacego projektu, opcjonalnie narzedzie do testow mutacyjnych, i zapisz je jako strone wiki 'toolchain' w Monolynx. Jednorazowa konfiguracja per projekt - skille work i work-simple czytaja te strone, zeby uruchomic lint i testy przed zamknieciem ticketu. Uzyj przy pierwszym setupie projektu albo gdy zmienil sie stack."
user-invocable: true
argument-hint: (bez argumentow)
---

# Toolchain projektu - konfiguracja lint i testow

Przed pierwszą operacją obowiązkowo przeczytaj [Kontrakt runtime](references/runtime.md) i stosuj go w całym przebiegu. Narzędzia, argumenty, pytania i uruchamianie zależnych skilli dobieraj do faktycznie dostępnych możliwości klienta. Nazwy `/monolynx:*` oznaczają wariant Claude Code; w Codex użyj nazwy z katalogu zainstalowanych skilli albo odczytaj dostarczony `SKILL.md`, bez wymyślania komend.

Wykrywasz, jak w tym projekcie uruchamia sie linter i testy, potwierdzasz to z
uzytkownikiem i zapisujesz jako strone wiki `toolchain`. Skille `/monolynx:work`
i `/monolynx:work-simple` czytaja te strone i egzekwuja lint + testy przed
zmiana statusu ticketu na `in_review`.

Uruchamiane **raz per projekt**. Ponownie tylko gdy zmienil sie stack.

---

## Ustalenie slug projektu

Ustal `project_slug` zgodnie z kontraktem runtime: jawny argument użytkownika, następnie `MONOLYNX_PROJECT_SLUG` w środowisku procesu, następnie jawnie odczytana konfiguracja klienta lub `.env` projektu. Konfigurację czytaj jako dane, bez `source` i bez wypisywania sekretów. Brak sluga: zapytaj użytkownika i zatrzymaj operacje projektowe; nie zgaduj go. W przykładach MCP używaj `<PROJECT-SLUG>`.

## Transport: CLI albo MCP

<!-- shared:transport -->
Operacje na platformie Monolynx wykonujesz jednym z dwoch transportow: CLI `monolynx` (zawsze przez wrapper) albo tools MCP. Zapis `mcp__monolynx__<tool>(...)` w tym skillu oznacza operacje `<tool>` z podanymi argumentami, niezaleznie od transportu. Regula jest ta sama w Claude Code i w Codex.

**Preflight - raz, na starcie skilla** (nie przed kazda operacja):

```bash
python3 <katalog skilla>/scripts/monolynx_transport.py
```

`<katalog skilla>` to absolutna sciezka katalogu tego pliku, wpisana literalnie. Komendy obu skryptow pisz bez zmiennych powloki, bez `~`, bez pipe, bez przekierowan i bez nawiasow `(` `)`: hook pluginu i sesje w worktree odrzucaja takie formy. Ostatnia linia wyniku jest zamrozona:

`TRANSPORT: mode=cli|mcp cli=<wersja|none> auth=ok|missing latest=<wersja|unknown> update=yes|no install=brew|pipx|uv|unknown|none`

| Wynik preflightu | Postepowanie |
|---|---|
| `mode=cli` | Operacje wykonujesz wrapperem `monolynx_op.py` (nizej). |
| `mode=mcp` | Tylko tools MCP, wrappera nie wolasz. |
| Jest linia `TRANSPORT-HINT:` (brak CLI, CLI niezalogowane albo nowsza wersja) | Przepisz ja uzytkownikowi jedna linia i pracuj dalej wedlug `mode`. Nie pytaj, nie czekaj, niczego nie instaluj. |
| `mode=mcp` i runtime nie udostepnia tooli MCP Monolynx | STOP, bez zgadywania. Podaj instrukcje: `pipx install monolynx-cli` i `monolynx auth login`, albo podpiecie connectora MCP Monolynx. |
| Preflight sie nie uruchomil (brak `python3`, odmowa uprawnienia) | Pracuj jak przy `mode=mcp`. |

**Operacja przy `mode=cli`** - pierwszy argument to nazwa toola, flagi to dokladne nazwy jego parametrow:

```bash
python3 <katalog skilla>/scripts/monolynx_op.py get_ticket --project_slug <PROJECT-SLUG> --ticket_id <KEY>
```

Wartosc bool to `true` albo `false`, lista to tablica JSON w jednej wartosci. Dluga tresc (komentarz, opis, strona wiki) i kazda wartosc ze znakami specjalnymi powloki: zapisz do pliku i podaj jako `@<sciezka pliku>`. Komend `monolynx ...` nie skladaj samodzielnie - mapowanie operacji na komendy CLI zyje we wrapperze.

Tryb zwiezly: `--compact true` przy `list_tickets`, gdy lista sluzy do przegladu statusow i blokerow bez czytania opisow (elementy bez pola `description`), oraz `--brief true` przy `create_ticket` i `update_ticket`, gdy po zapisie nie czytasz opisu (wynik to tylko pola `id`, `key`, `title`, `status`, `sprint_id`, `blocked_by`, `blocks`). `compact` i `brief` to parametry wrappera, a nie tooli MCP: przy `mode=mcp` i przy fallbacku (kod 3) wywolujesz tool MCP bez nich. Wrapper sam niczego nie skraca - bez tych flag wynik jest pelny.

Kody wyjscia wrappera:

- `0` - stdout to JSON API v2 z CLI. Niesie te same dane co tekstowa odpowiedz toola MCP, pod nazwami pol JSON.
- `3` z ostatnia linia `OP-FALLBACK: mcp tool=<nazwa>` - wywolaj ten tool MCP z tymi samymi argumentami, z pominieciem parametrow wrappera `compact` i `brief` (tool MCP ich nie ma); jego aktualny schemat poznaj mechanizmem runtime przed wywolaniem. To zwykla sciezka operacji bez komendy CLI (Pipelines, `log_time`, zapis grafu, LLM Wiki). Brak tego toola w runtime: stop z nazwa brakujacej operacji.
- `2` - blad operacji (API, siec, przekroczony czas). Przy zapisie nie ponawiaj na slepo przez MCP: najpierw odczytem sprawdz, czy zapis doszedl.
- `64` - blad uzycia wrappera, CLI nie bylo wolane. Popraw argumenty.

Subagent pracuje przez MCP, chyba ze dostal w prompcie wynik `mode=cli` i literalna sciezke wrappera. `MONOLYNX_TRANSPORT=mcp` w srodowisku wymusza MCP w preflighcie i we wrapperze (zachowanie sprzed wprowadzenia CLI).
<!-- /shared:transport -->

## KROK 1: Zaladuj narzedzia i sprawdz stan

W katalogu dostępnych narzędzi znajdź funkcje Monolynx potrzebne poniżej i sprawdź ich schematy. Discovery jest opcjonalne, jeśli narzędzia są już dostępne.

<!-- runtime:claude-code -->
Opcjonalny wariant discovery w Claude Code:
```
ToolSearch(query="+monolynx wiki page list get create update", max_results=10)
```
<!-- /runtime:claude-code -->

Sprawdz, czy strona juz istnieje - po slugu, nie semantycznie:

```
mcp__monolynx__list_wiki_pages(project_slug="<PROJECT-SLUG>")
```

`list_wiki_pages` zwraca ID, Title i Updated, bez pola `slug`. Wybierz kandydata o tytule `Toolchain`, odczytaj `get_wiki_page` po jego ID i potwierdź `slug == "toolchain"`. Nie używaj tu wyszukiwania semantycznego: słabe trafienie mogłoby spowodować duplikat. Błąd odczytu nie oznacza braku strony.

**Strona `toolchain` istnieje** - pobierz jej tresc przez `get_wiki_page`, pokaz
uzytkownikowi i zapytaj:

> Projekt ma juz strone `toolchain`:
> ```
> [tresc]
> ```
> Zaktualizowac ja? (tak / nie)

Odpowiedz "nie" -> zakoncz bez zmian.

## KROK 2: Wykryj stack

Sprawdz obecnosc plikow konfiguracyjnych w korzeniu repo (Glob, jedno przejscie):

| Plik | Stack | Typowy lint | Typowy test |
|---|---|---|---|
| `Makefile` | dowolny | `make lint` | `make test` |
| `pyproject.toml` | Python | `ruff check . && ruff format --check .` | `pytest` |
| `setup.cfg` / `tox.ini` | Python | `flake8` | `pytest` / `tox` |
| `package.json` | Node | `npm run lint` | `npm test` |
| `Cargo.toml` | Rust | `cargo clippy` | `cargo test` |
| `go.mod` | Go | `go vet ./...` | `go test ./...` |
| `composer.json` | PHP | `composer lint` | `composer test` |
| `Gemfile` | Ruby | `rubocop` | `rspec` |
| `pom.xml` / `build.gradle` | Java | `mvn checkstyle:check` | `mvn test` |

**Priorytet**: jesli istnieje `Makefile` - przeczytaj go i wyciagnij faktyczne
nazwy targetow (`grep -E '^[a-zA-Z-]+:' Makefile`). Target zdefiniowany w repo
bije komende domyslna z tabeli.

**Docker**: sprawdz `docker-compose.yml` / `compose.yaml`. Jesli projekt uruchamia
sie w kontenerze, komendy musza miec prefiks (np. `docker compose exec app ...`).
Sprawdz tez `CLAUDE.md` / `AGENTS.md` - moga zawierac jawna regule "nigdy nie
uruchamiaj lokalnie".

**Wariant worktree**: wykryty prefiks `docker compose exec <service>` (wprost albo
opakowany w target Makefile) wymaga DODATKOWEJ komendy. `exec` wchodzi do
dzialajacego kontenera, a ten ma zamontowany glowny checkout - ta sama komenda
uruchomiona z kopii repo (git worktree) sprawdzilaby cudzy kod, nie ten, nad
ktorym pracujesz. Wariant worktree montuje biezacy katalog do jednorazowego
kontenera. Wyprowadz go tak:

1. Rozpakuj target Makefile do komendy wykonywanej WEWNATRZ kontenera (bez
   prefiksu). Target o kilku krokach sklej w `sh -c "a && b && c"`.
2. Zbuduj prefiks:

   ```bash
   docker compose -p <nazwa-projektu-compose> [--profile <profil>] run --rm --no-deps -v "$PWD":<workdir> <service> <komenda>
   ```

Jak dobrac czesci:

- `-p <nazwa-projektu-compose>` - **wymagane**. Nazwa projektu compose glownego
  checkoutu: z `docker compose ls` przy dzialajacym stacku, z pola `name:` pliku
  compose, albo z nazwy katalogu checkoutu w postaci, w jakiej normalizuje ja
  compose. Bez tego compose w worktree wezmie nazwe katalogu worktree i postawi
  DRUGI komplet kontenerow razem z druga baza.
- `--no-deps` - **wymagane**. Bez tego `run` podnosi zaleznosci serwisu.
- `<workdir>` - WORKDIR serwisu odczytany z Dockerfile. Nie zakladaj `/app`.
- `--profile <profil>` - ten sam, ktorego uzywa Makefile.

Nie dokladaj `--env-file`. Worktree nie ma wlasnego `.env` (plik bywa
gitignorowany), a wariant opiera sie na wartosciach domyslnych pliku compose
i aplikacji. Projekt, ktory bez konkretnych zmiennych nie wstanie - uzytkownik
dopisze przy potwierdzeniu w KROK 3 `--env-file <absolutna sciezka>` LITERALNIE,
bez podstawien. Komenda zapisana na stronie nie moze zawierac `$(...)`: sesja
pracujaca w worktree odrzuca komendy z podstawieniem obliczanym w locie.

Wariant dziala tylko przy podniesionym stacku glownego checkoutu - siec, obraz
i baza pochodza z projektu wskazanego przez `-p`. Projekt bez Dockera nie
potrzebuje osobnego wariantu: w sekcji `## Worktree` wpisz wtedy `jak wyzej`.

**Czy sa testy**: policz pliki testowe (`tests/`, `test/`, `*_test.go`,
`*.test.ts`, `spec/`). Zero plikow -> projekt nie ma testow.

**Izolacja testow miedzy worktree**: ustal, czy dwa przebiegi testow uruchomione
rownolegle z dwoch kopii repo (git worktree) dziela stan. Dyspozytor sprintu
prowadzi kilka ticketow naraz, kazdy w osobnym worktree; wspolny stan sprawia,
ze przebiegi psuja sobie nawzajem wyniki, a czerwony test nie mowi nic o kodzie.
Konfiguracje testow czytasz jako dane (fixture'y i pliki konfiguracyjne runnera,
np. `conftest.py`, `jest.config.*`, `phpunit.xml`, plik compose, `.env.example`,
Makefile). Testow nie uruchamiasz. Szukasz trzech rzeczy:

- **baza danych o stalej nazwie** - nazwa zaszyta w fixture'ach albo
  w konfiguracji testow (literal w URL-u polaczenia, `CREATE DATABASE` /
  `DROP DATABASE` na stalej nazwie), ta sama dla kazdego przebiegu;
- **staly port** - testy podnosza serwer albo usluge na numerze portu wpisanym
  na sztywno;
- **wspolny katalog** - cache, pliki tymczasowe albo artefakty pod stala sciezka
  poza katalogiem repo (np. `/tmp/<nazwa>`), albo wolumen wspolny dla kontenerow.

Projekt bez testow (zero plikow wyzej) dostaje `izolacja testów: tak` - nie ma
przebiegow, ktore moglyby sie zderzyc. W pozostalych przypadkach:

| Co ustaliles | Pole `izolacja testów` | Komenda `test:` w `## Worktree` |
|---|---|---|
| Zadnego z trzech wspolnych zasobow | `tak` | bez zmian |
| Testy izoluja sie same (np. losowa nazwa bazy na kazdy przebieg, port przydzielany przez system, katalog tymczasowy tworzony przez runner) | `tak` | bez zmian |
| Wspolny zasob jest, a projekt ma parametr izolacji | `tak` | z dopisanym parametrem (nizej) |
| Wspolny zasob jest, parametru izolacji nie ma | `brak` | bez zmian |
| Nie da sie ustalic z plikow (konfiguracja rozproszona, nazwa skladana w kodzie, ktorego nie rozumiesz) | pytanie do uzytkownika w KROK 3 | bez zmian do czasu odpowiedzi |

**Parametr izolacji** to zmienna srodowiskowa albo opcja runnera, ktora
konfiguracja testow faktycznie ODCZYTUJE i ktora zmienia nazwe wspolnego zasobu
(np. zmienna z nazwa bazy testowej, czytana w fixture przed zalozeniem bazy).
Rozpoznajesz go po odczycie w kodzie konfiguracji testow, nie po nazwie.
Zmiennej, ktorej testy nie czytaja, nie dopisujesz - niczego by nie izolowala,
a pole `tak` byloby wtedy nieprawda.

Strona `toolchain` jest jedna na projekt, a wartosc parametru ma byc inna dla
kazdego ticketu. Zapisujesz ja wiec z placeholderem `<KEY>` w miejscu klucza
ticketu. Przy stacku Dockera parametr idzie jako `-e` zaraz po `--no-deps`
(opcja podkomendy `run`, przed `-v`):

```bash
docker compose -p <nazwa-projektu-compose> [--profile <profil>] run --rm --no-deps -e <ZMIENNA>=<staly-prefiks>_<KEY> -v "$PWD":<workdir> <service> <komenda testow>
```

Przyklad wartosci: `-e TEST_DATABASE_NAME=open_sentry_test_<KEY>`. Parametr
dopisujesz tylko do `test:` - `lint:` zostaje bez niego. Gdy `test:` mialoby
wartosc `jak wyzej` (projekt bez Dockera), a parametr izolacji istnieje,
zapisujesz w `test:` pelna komende z parametrem zamiast `jak wyzej`; forme
przekazania parametru (opcja runnera albo zmienna ustawiana przed komenda)
potwierdzasz z uzytkownikiem w KROK 3 i nie zgadujesz jej.

`<KEY>` podstawia LITERALNIE ten, kto uruchamia komende: wpisuje klucz ticketu
wprost (np. `MON-252`), bez `$VAR` i bez `$(...)` - sesja pracujaca w worktree
odrzuca komendy z wartoscia obliczana w locie. Komenda z niepodstawionym `<KEY>`
nie moze trafic do shella.

**Pelny przebieg testow: lokalnie czy w CI**: ustal, gdzie ma isc pelny zestaw
testow przed zamknieciem ticketu. `work` i `work-simple` domyslnie uruchamiaja
go lokalnie po ostatniej zmianie. Przy duzym zestawie taki przebieg trwa dlugo
i blokuje kolejke testow innych sesji, a ten sam zestaw i tak biegnie
w pipeline merge requesta. Pole ma dwie wartosci:

- `lokalnie` (domyslna) - pelny zestaw idzie lokalnie, w sesji ticketu;
- `ci` - lokalnie ida tylko testy zawezone do zmienionego obszaru, a pelny
  zestaw weryfikuje pipeline merge requesta.

Proponujesz `ci` tylko wtedy, gdy spelnione sa WSZYSTKIE trzy warunki; kazdy
inny przypadek to `lokalnie`:

1. projekt ma plik `.gitlab-ci.yml` z jobem, ktory uruchamia testy na merge
   requescie (czytasz plik jako dane, niczego nie uruchamiasz). `work`
   i `work-simple` zakladaja merge request przez `glab`, wiec inny system CI
   nie da im pipeline'u do wskazania;
2. zestaw jest duzy: ponad 100 plikow testowych z policzenia wyzej albo
   informacja w repo lub od uzytkownika, ze pelny przebieg trwa dluzej niz
   kilkanascie minut;
3. komenda testow przyjmuje sciezki (np. `pytest <sciezki>`), bo w trybie `ci`
   skille podstawiaja sciezki zmienionego obszaru w miejsce domyslnej sciezki
   zestawu. Komenda bez miejsca na sciezki (sam cel `make`) tego nie spelnia.

Projekt bez testow dostaje `lokalnie`. Wartosc potwierdza uzytkownik w KROK 3 -
to propozycja, nie decyzja skilla.

## KROK 3: Potwierdz z uzytkownikiem

Nie zapisuj wykrytych komend bez potwierdzenia - wykrywanie zgaduje, uzytkownik wie.

> Wykryty stack: **[stack]**
>
> | | Komenda |
> |---|---|
> | Lint | `[wykryta komenda]` |
> | Test | `[wykryta komenda]` |
> | Worktree | `[wariant worktree lint i test, albo "jak wyzej"]` |
> | Izolacja testow miedzy worktree | **[tak / brak / nie ustalono]** - [powod jednym zdaniem; przy parametrze izolacji jego nazwa] |
> | Pelny przebieg testow | **[lokalnie / ci]** - [powod jednym zdaniem] |
>
> Testy w repo: **[znaleziono N plikow / brak]**
>
> 1. Komendy sie zgadzaja? (tak / podaj poprawne)
> 2. Kto uruchamia lint i testy: **user** (domyslnie, agent wypisuje komendy)
>    czy **agent** (odpala sam)?
> 3. Stosowac TDD - testy pisane przed implementacja? (tak / nie)
> 4. Izolacja testow sie zgadza? (tak / podaj poprawna)
> 5. Pelny przebieg testow przed zamknieciem ticketu: **lokalnie** (domyslnie)
>    czy **ci** (lokalnie tylko testy zmienionego obszaru, pelny zestaw
>    w pipeline merge requesta)?

Poczekaj na odpowiedzi. Przy braku testow w repo pytanie o TDD pomin (wpisz
`nie`), a pytanie o pelny przebieg tez pomin (wpisz `lokalnie`).

Wiersz `Pelny przebieg testow` wypelniasz propozycja z KROK 2 i jej powodem
(np. "ponad 100 plikow testowych i job testow w `.gitlab-ci.yml`", "brak CI").
Przy propozycji `ci` powiedz od razu dwie rzeczy: ze `work` i `work-simple`
zamkna wtedy ticket tylko z wystawionym merge requestem, czyli potrzebuja
`MONOLYNX_AUTOCOMMIT`, `MONOLYNX_AUTOPUSH` i `MONOLYNX_AUTOMR` ustawionych na
`true` oraz dzialajacego `glab`, i ze blad w obszarze, ktorego lokalne testy
nie objely, wyjdzie dopiero w pipeline. Odpowiedz inna niz `lokalnie` albo `ci`
-> dopytaj; na stronie stoi wylacznie jedna z tych dwoch wartosci.

Wiersz `Izolacja testow miedzy worktree` wypelniasz ustaleniem z KROK 2 i
podajesz przy nim, co je uzasadnia (np. "fixture zaklada baze o stalej nazwie
`app_test`", "nazwa bazy losowana na kazdy przebieg"). Gdy wykryles parametr
izolacji, pokaz komende `test:` juz z dopisanym parametrem i `<KEY>` i powiedz
jednym zdaniem, ze `<KEY>` podstawia literalnie uruchamiajacy komende.

Gdy w KROK 2 wyszlo `nie ustalono`, pytanie 4 zastap dwoma jawnymi pytaniami
i nie zapisuj strony bez odpowiedzi na pierwsze:

> 4. Czy testy uruchomione rownolegle z dwoch kopii repo dziela stan (ta sama
>    baza, ten sam port, ten sam katalog)? (tak / nie)
> 5. Jesli tak: czy jest zmienna albo opcja, ktora ten stan rozdziela (np. nazwa
>    bazy testowej)? Podaj jej nazwe i przykladowa wartosc, albo "nie ma".

Pytanie o pelny przebieg testow dostaje wtedy numer 6, z ta sama trescia.

Odpowiedzi mapujesz tak samo jak w tabeli z KROK 2: brak wspolnego stanu ->
`tak`; wspolny stan z parametrem -> `tak` i parametr w `test:`; wspolny stan bez
parametru -> `brak`. Pola nie zostawiasz pustego i nie wpisujesz trzeciej
wartosci - na stronie stoi wylacznie `tak` albo `brak`.

Wiersz `Worktree` wypelniasz wariantem wyprowadzonym w KROK 2. Powiedz przy nim
jednym zdaniem, ze wariant worktree dziala tylko przy podniesionym stacku
glownego checkoutu - uzytkownik ma to wiedziec, zanim komenda trafi na strone.
Zapytaj przy tym, czy projekt wstanie na wartosciach domyslnych; jesli nie,
uzytkownik podaje `--env-file <absolutna sciezka>` do dopisania LITERALNIE
(zadnych `$(...)` w komendzie, ktora trafia na strone).

## KROK 4: Wykryj narzedzie mutacyjne (opcjonalnie)

Testy mutacyjne sprawdzaja, czy testy faktycznie lapia zmiany w kodzie. Ten krok
jest **opcjonalny** - jego pominiecie nie blokuje zapisu strony ani reszty skilla.

Ekosystem wynika z tych samych plikow markerowych co w KROK 2. Narzedzie jest
per-jezyk i per-runner, wiec kazdy wiersz ma wlasny sposob sprawdzenia obecnosci:

| Marker | Ekosystem | Narzedzie | Sprawdzenie obecnosci | Instalacja |
|---|---|---|---|---|
| `pyproject.toml` / `setup.cfg` | Python | mutmut 3 | `command -v mutmut` | `pip install mutmut` |
| `package.json` | JS/TS | Stryker + plugin runnera | `npx stryker --version` | `npm i -D @stryker-mutator/core` |
| `pom.xml` / `build.gradle` | Java/Kotlin | PIT | wpis `pitest` w pliku builda | plugin maven/gradle + `pitest-junit5-plugin` |
| `*.csproj` | C# / .NET | Stryker.NET | `dotnet tool list -g` | `dotnet tool install -g dotnet-stryker` |
| `go.mod` | Go | gremlins | `command -v gremlins` | `go install github.com/go-gremlins/gremlins/cmd/gremlins@latest` |
| `composer.json` | PHP | Infection | `vendor/bin/infection --version` | `composer require --dev infection/infection` |
| `Cargo.toml` | Rust | cargo-mutants | `cargo mutants --version` | `cargo install cargo-mutants` |
| `build.sbt` | Scala | Stryker4s | wpis w `project/plugins.sbt` | plugin sbt |
| `Gemfile` | Ruby | mutant | `bundle exec mutant --version` | `gem install mutant` |

**Ruby / mutant - licencja**: mutant jest darmowy dla kodu otwartego, ale dla
kodu zamknietego wymaga **licencji KOMERCYJNEJ**. Wykryles projekt Ruby -
powiedz o tym uzytkownikowi ZANIM podasz mu komende instalacji, i dopiero potem
prowadz reszte kroku. Zaden inny wiersz tabeli nie ma tego zastrzezenia.

Uzyj dokladnie tego sprawdzenia, ktore stoi w kolumnie "Sprawdzenie obecnosci".
`command -v` znajdzie tylko samodzielna binarke w PATH, a PIT jest pluginem
Mavena, Stryker.NET narzedziem `dotnet tool`, cargo-mutants podkomenda `cargo`,
Stryker JS pakietem node - dla nich `command -v` zawsze zwroci "brak".

**Ekosystem nierozpoznany albo kilka naraz** (np. `package.json` obok
`pyproject.toml`) - NIE zgaduj. Zapytaj uzytkownika (przez dostępne narzędzie pytań albo wiadomość), ktory
ekosystem jest rdzeniem projektu, i dla niego prowadz reszte kroku. Mutacja
konfigurowana jest dla jednego runnera.

**Brak narzedzia**: pokaz instrukcje instalacji z tabeli dla wykrytego ekosystemu
i **zakoncz krok**. Nie instaluj niczego sam - to jednorazowy setup wlasciciela
repo (ten sam precedens co graphify w `/monolynx:graph-sync`). Uzytkownik nie
chce instalowac -> w sekcji `## Mutacje` wpisz `narzedzie: brak` oraz
`powod: nie zainstalowane` albo `powod: uzytkownik zrezygnowal` i przejdz do
KROK 5. Sekcja `## Mutacje` powstaje **zawsze**, takze pusta - stala struktura
strony jest stabilniejsza do odczytu przez kolejne skille niz sekcja pojawiajaca
sie warunkowo.

**Flagi diffowe**: gdy narzedzie JEST, uruchom `<narzedzie> --help` i ODCZYTAJ z
wyjscia faktyczna flage trybu diffowego (przebieg ograniczony do zmiany, nie do
calego repo). Spotykane warianty to `--since`, `--in-diff`, `--git-diff-lines`,
cel `scmMutationCoverage`, ale NIE zakladaj zadnego z nich z pamieci - roznia sie
miedzy narzedziami i miedzy wersjami tego samego narzedzia. Do strony zapisz
komende, ktorej postac faktycznie potwierdziles w `--help`. Ta wersja nie ma
trybu diffowego -> wpisz `komenda diffowa: brak`.

**Moduly rdzenia**: zapytaj uzytkownika, na ktorych sciezkach ma isc pelny
przebieg mutacji (ten drozszy, uruchamiany przy zamykaniu sprintu). Nie zgaduj -
deweloper wie, ktory kod jest krytyczny. Przy okazji dopytaj, kto odpala mutacje
(`user` czy `agent`) - domyslnie `user`, tak jak dla lintu i testow.

**Baseline zostaw pusty**. Pierwszy realny przebieg zapisze faktyczny wynik jako
punkt odniesienia. Nie wpisuj tu progu z glowy - mutation score to trend, nie
bramka.

## KROK 5: Zapisz strone wiki

```
mcp__monolynx__create_wiki_page(
  project_slug="<PROJECT-SLUG>",
  title="Toolchain",
  content="<tresc ponizej>"
)
```

`create_wiki_page` nie ma parametru `slug` - slug powstaje z tytulu. Dlatego
tytul musi brzmiec dokladnie `Toolchain`, zeby strona dostala slug `toolchain`.
Kazdy inny tytul da inny slug i `work` oraz `work-simple` nie znajda strony.

Strona istniala -> `update_wiki_page` z tym samym `page_id`. Tytulu wtedy nie
zmieniaj - podmieniasz tylko `content`.

Format tresci - sztywny, skille parsuja go wzrokowo. Naglowek `#` w tresci jest
opisowy i nie ma wplywu na slug - ten wynika wylacznie z tytulu strony:

```markdown
# Toolchain projektu

Stack: [wykryty stack]
Uruchamianie: [lokalnie / docker]

## Lint

komenda: [dokladna komenda]
kto odpala: [user | agent]

## Test

komenda: [dokladna komenda]
kto odpala: [user | agent]
testy istnieja: [tak | nie]
framework: [pytest | vitest | go test | ...]
pełny przebieg: [lokalnie | ci]

## Worktree

lint: [komenda lint dla kopii repo, albo "jak wyzej"]
test: [komenda test dla kopii repo, albo "jak wyzej"; z parametrem izolacji i <KEY>, gdy projekt go ma]
izolacja testów: [tak | brak]

## TDD

stosowac: [tak | nie]

## Mutacje

narzedzie: [nazwa + wersja, albo "brak"]
powod: [tylko gdy narzedzie: brak - "nie zainstalowane" albo "uzytkownik zrezygnowal"]
komenda pelna: [przebieg na wskazanym module]
komenda diffowa: [przebieg ograniczony do zmiany, flaga zweryfikowana przez --help]
raport: [sciezka pliku] (format: mte-json | pit-xml | mutmut | cargo-mutants)
moduly rdzenia: [lista sciezek na pelny przebieg]
baseline: [score + data pomiaru, puste przy pierwszym zapisie]
cel: [orientacyjny, nie bramka]
kto odpala: [user | agent]

## Uwagi

[np. "Wszystko przez docker compose exec app - nigdy lokalnie" albo "Brak"]
```

Trzymaj sie tych naglowkow i etykiet. Zmiana formatu psuje odczyt w `work`.

Sekcja `## Worktree` powstaje zawsze, tak jak `## Mutacje`, i ma zawsze trzy
pola w tej kolejnosci: `lint:`, `test:`, `izolacja testów:`. Projekt bez Dockera
dostaje w `lint:` i `test:` wartosc `jak wyzej` - to odeslanie do `## Lint`
i `## Test`, nie brak danych (wyjatek: `test:` z parametrem izolacji, KROK 2).

Pole `izolacja testów:` stoi zaraz pod `test:` i ma dokladnie jedna z dwoch
wartosci, takze wtedy, gdy `lint:` i `test:` to `jak wyzej`:

- `izolacja testów: tak` - rownolegle przebiegi testow z roznych worktree nie
  dziela stanu: nie ma wspolnego zasobu, testy izoluja sie same, albo izoluje
  je parametr dopisany do `test:`;
- `izolacja testów: brak` - przebiegi dziela stan i nie ma parametru, ktory by
  go rozdzielil; rownolegle testy z dwoch worktree moga sie zderzyc.

Etykiete pisz doslownie `izolacja testów:` (z `ó`) - tej linii szuka
`/monolynx:setup`. Placeholder `<KEY>` w `test:` zostaje na stronie jako
`<KEY>`: klucz ticketu podstawia literalnie uruchamiajacy komende, bez `$VAR`
i bez `$(...)`.

Pole `pełny przebieg:` stoi w sekcji `## Test`, zaraz pod `framework:` - tam
czytaja je `work`, `work-simple` i `/monolynx:setup`; to samo pole w innej
sekcji jest ignorowane. Zapisujesz je zawsze, z dokladnie jedna z dwoch
wartosci:

- `pełny przebieg: lokalnie` - pelny zestaw testow idzie lokalnie, w sesji
  ticketu, po ostatniej zmianie. To takze zachowanie strony bez tego pola;
- `pełny przebieg: ci` - lokalnie ida lint i testy zawezone do zmienionego
  obszaru, pelny zestaw weryfikuje pipeline merge requesta, a ticket trafia do
  `in_review` dopiero po wystawieniu merge requesta.

Etykiete pisz doslownie `pełny przebieg:` (z `ł`). Czytajacy przyjmuja tez
zapis `pelny przebieg:`, a wartosc inna niz `lokalnie` i `ci` traktuja jak
`lokalnie` z ostrzezeniem - dlatego nie wpisujesz trzeciej wartosci ani
komentarza w tej linii.

## KROK 6: Podsumowanie

Wyswietl uzytkownikowi:

- Zapisana komende lint i test
- Wariant worktree obu komend (albo `jak wyzej`)
- Izolacje testow miedzy worktree (`tak` albo `brak`) z powodem; przy `brak`
  napisz wprost, ze testy uruchomione rownolegle z dwoch worktree moga sie
  zderzyc, a przy parametrze izolacji - ze `<KEY>` w komendzie `test:` trzeba
  przed uruchomieniem zastapic literalnym kluczem ticketu
- Pelny przebieg testow (`lokalnie` albo `ci`); przy `ci` napisz wprost, ze
  lokalnie pojda tylko testy zmienionego obszaru, pelny zestaw zweryfikuje
  pipeline merge requesta, a `work` i `work-simple` potrzebuja do tego flag
  `MONOLYNX_AUTOCOMMIT`, `MONOLYNX_AUTOPUSH` i `MONOLYNX_AUTOMR` na `true`
  oraz dzialajacego `glab`
- Kto je odpala
- Czy TDD wlaczone
- Narzedzie mutacyjne i jego komende diffowa (albo `brak` wraz z powodem)
- Link do strony wiki

Jesli uzytkownik wybral **agent** jako uruchamiajacego, przypomnij o fladze:

> Zeby `work` sam odpalal lint i testy, ustaw w `.claude/settings.local.json`:
> ```json
> { "env": { "MONOLYNX_AUTOTEST": "true" } }
> ```
> Bez niej skill wypisze komendy i poczeka na Twoj wynik.

---

## WAZNE ZASADY

1. **Nie zgaduj komend** - tabela w KROK 2 to punkt wyjscia, nie odpowiedz. Zawsze potwierdz z uzytkownikiem
2. **Nie uruchamiaj wykrytych komend** zeby je zweryfikowac - to konfiguracja, nie test. Lint na brudnym drzewie roboczym zwroci mylacy wynik. **Wyjatek**: `--help` i `--version` wolno uruchomic. To odpytanie narzedzia o wlasny interfejs, nie przebieg testow ani mutacji - KROK 4 tego wprost wymaga
3. **Format strony jest kontraktem** - `work` i `work-simple` czytaja naglowki i etykiety doslownie. Dotyczy to takze sekcji `## Worktree`: pracujac w git worktree oba skille biora komendy z jej pol `lint:` i `test:`, a wartosc `jak wyzej` odsyla je do `## Lint` i `## Test`. Trzecie pole, `izolacja testów:`, ma wylacznie wartosc `tak` albo `brak` i jest obecne zawsze - `/monolynx:setup` szuka tej linii doslownie i przy jej braku pokazuje BRAK. `<KEY>` w `test:` to placeholder klucza ticketu, podstawiany literalnie przez uruchamiajacego. Pole `pełny przebieg:` w sekcji `## Test` ma wylacznie wartosc `lokalnie` albo `ci`; `work`, `work-simple` i `/monolynx:setup` czytaja je tylko z tej sekcji, a strone bez pola traktuja jak `lokalnie`
4. **Respektuj reguly repo** - jesli `CLAUDE.md` mowi "tylko przez docker" albo "Claude nigdy nie odpala testow", zapisz to w sekcji Uwagi i domyslnie ustaw `kto odpala: user`
5. **Jedna strona per projekt** - slug zawsze `toolchain`, bez wariantow. Slug wynika z tytulu strony, wiec tytul musi brzmiec dokladnie `Toolchain` - zmiana tytulu zrywa odczyt w `work` i `work-simple`, ktore szukaja strony po slugu `toolchain`
6. **Nie instaluj narzedzia mutacyjnego** - to jednorazowy setup wlasciciela repo, per jezyk i per runner. Skill je wykrywa i podaje instrukcje, nie sciaga (precedens: graphify)
7. **Krok mutacji jest opcjonalny** - jego pominiecie nie blokuje zapisu strony ani reszty skilla. Sekcja `## Mutacje` powstaje zawsze, chocby z samym `narzedzie: brak`
