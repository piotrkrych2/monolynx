# Commit, push i merge request

Procedura wspolna dla flow pracy nad ticketem. Wykonujesz ja po zamknieciu
pracy zespolu, w miejscu wskazanym przez skill. Kazda zgoda jest jawna:
domyslnie NIC nie commitujesz, nie pushujesz i nie zakladasz merge requesta.
Ostatnia sekcja (odczyt pipeline'u merge requesta) dotyczy tylko trybu
`pełny przebieg: ci`.

---

## Commit

**`MONOLYNX_AUTOCOMMIT=false`** (domyslnie) - wypisz gotowa komende, nie
wykonuj:

> Zmiany gotowe do commita:
> ```bash
> git add -A && git commit -m "[KEY] [tytul ticketu]"
> ```

`PLIKI_SPRZED_STARTU` niepuste (uzytkownik nie zrobil commit/stash przed
startem pracy)? NIE uzywaj `git add -A` - dodaj imiennie tylko pliki objete
praca nad tym ticketem (`git add <pliki z przydzialow>`), inaczej commit ticketu
wciagnie cudza albo wczesniejsza prace. Jesli przed startem zrobiono `git stash` - przypomnij
uzytkownikowi o `git stash pop` w podsumowaniu koncowym.

**`MONOLYNX_AUTOCOMMIT=true`** - wykonaj commit, ale TYLKO po zielonym lincie
i testach ORAZ po pozytywnym werdykcie krytyka wg progu z `review-rubric.md`
(warunek dokladny podaje skill). Zasada imiennego `git add` przy niepustych
`PLIKI_SPRZED_STARTU` obowiazuje TAKZE tutaj - nigdy `git add -A` na brudnym
drzewie. Lint i testy pominiete za zgoda uzytkownika, ticket zamkniety
z czerwonymi testami LUB z ocena ponizej progu na zadanie uzytkownika -> nie
commituj sam: wypisz komende i zostaw decyzje uzytkownikowi.

---

## Push

**`MONOLYNX_AUTOPUSH=true`** - dodatkowo `git push`, ale TYLKO gdy
`AUTOCOMMIT=true` i commit faktycznie sie odbyl. Przy `AUTOCOMMIT=false`
ignoruj AUTOPUSH (nie pushuj zastanych/cudzych commitow). Przy `AUTOPUSH=false`
nigdy nie pushuj, nawet gdy uzytkownik commituje sam - push zostaje jego
decyzja.

---

## Merge request

**`MONOLYNX_AUTOMR=true`** - po FAKTYCZNYM pushu (`AUTOCOMMIT=true`, commit sie
odbyl, `git push` sie powiodl) zaloz merge request.

Branch docelowy (`<branch_docelowy>`) ustal w tej kolejnosci, kazda komende
uruchamiajac OSOBNO i wpisujac wynik do komendy MR jako literal (bez `$VAR` i
`$(...)` - sesja w worktree je odrzuca):

a) `MONOLYNX_MR_TARGET` z linii konfiguracji niepusta -> ta wartosc.
b) `git symbolic-ref refs/remotes/origin/HEAD` -> wynik
   `refs/remotes/origin/<nazwa>`, bierzesz `<nazwa>`. Blad (np. "is not a
   symbolic ref" w klonie bez `git remote set-head origin -a`)
   albo pusty wynik -> c).
c) `glab repo view -F json --jq .default_branch` -> nazwa domyslnego brancha
   projektu w GitLabie.
d) Nic nie dalo wyniku -> NIE zgaduj `main`: MR nie powstaje, wypisz gotowa
   komende z `<branch_docelowy>` do uzupelnienia i odnotuj "MR nieutworzony:
   nie ustalono brancha docelowego (ustaw MONOLYNX_MR_TARGET)". MR do zlego
   brancha trzeba poprawiac recznie, brak MR tylko opoznia.

Tytul i opis MR podajesz wprost, nigdy przez `--fill`: `--fill` bierze je
z commitow, a na branchu z jednym commitem albo z nazwa w stylu
`worktree-MON-150` daje tytul "worktree MON 150" (MON-150).

- `<KEY>` i `<tytul ticketu>` - pola `key` i `title` pobranego ticketu.
- `<podsumowanie>` - zwiezla tresc z podsumowania zadania (lista
  "Zrealizowane" / co zrobiono) zaczynajaca sie od klucza ticketu, np.
  `MON-178: [zmiana 1]; [zmiana 2]; [zmiana 3].` Jedna linia, punkty
  rozdzielone `; `, bez `$`, bez em-dash. Nigdy sam `-`: dla glab to
  polecenie otwarcia edytora, a w sesji bez terminala MR nie powstanie.

Oba wpisujesz jako literal w apostrofach. Apostrof w tytule albo w tresci
zamien na cudzyslow `"`, bo apostrof zamyka napis. Bez `$VAR` i `$(...)` -
sesja w worktree je odrzuca.

```bash
glab mr create --title '<KEY>: <tytul ticketu>' --description '<podsumowanie>' --yes --source-branch <aktualny_branch> --target-branch <branch_docelowy>
```

Zwrocony URL zapisz jako `MR_URL` i podaj go razem z ustalonym branchem
docelowym w komentarzu podsumowujacym oraz w podsumowaniu dla uzytkownika.
`glab` nieobecny w PATH, komenda zwrocila blad albo pushu nie bylo -> NIE
powtarzaj: wypisz gotowa komende uzytkownikowi i odnotuj
"MR nieutworzony: [powod]". Przy `AUTOMR=false` (domyslnie) nigdy nie zakladaj
merge requesta.

---

## Pipeline merge requesta (tryb `ci`)

Tylko gdy skill pracuje w trybie `pełny przebieg: ci` (ustalenie trybu:
`lint-test.md`). W trybie `lokalnie` te sekcje pomijasz w calosci.

W trybie `ci` pelnego zestawu testow nie uruchomiono lokalnie - weryfikuje go
pipeline merge requesta. Dlatego:

- **Merge request nie powstal** ("MR nieutworzony: [powod]", brak pusha,
  `AUTOMR=false`) -> nie ma dowodu pelnego przebiegu. NIE przechodz dalej
  z tej sekcji; o tym, co wtedy, decyduje skill (ticket nie trafia do
  `in_review`).
- **Merge request powstal** (`MR_URL` zapisany) -> odczytaj jego pipeline.

Numer merge requesta (`<iid>`) to liczba na koncu `MR_URL`
(`.../-/merge_requests/<iid>`). Wpisz go doslownie:

```bash
glab mr view <iid> -F json
```

Wynik to jeden obiekt JSON. Czytasz z niego pole `head_pipeline`: obiekt
z polami `id` (liczba) i `status` (napis, np. `running`, `success`, `failed`).
Zapisz je jako `PIPELINE_ID` i `PIPELINE_STATUS`. Statusu nie interpretujesz
i nie tlumaczysz - przepisujesz go doslownie, takze wartosc, ktorej nie znasz.

Pipeline powstaje asynchronicznie po pushu:

- `head_pipeline` puste (`null`) albo nie ma go w wyniku -> JEDEN ponowny
  odczyt ta sama komenda.
- Dalej puste -> zapisz `PIPELINE_ID` = "pipeline w tworzeniu" i idz dalej.
  NIGDY petla czekania na pipeline.
- `glab mr view` zwrocil blad -> zapisz "pipeline nieodczytany: [powod]"
  i idz dalej. Merge request istnieje, `MR_URL` pozostaje wazny.

**Nie czekasz na wynik pipeline'u i go nie naprawiasz.** Czerwony pipeline
merge requesta ma jednego wlasciciela: `/monolynx:mr-queue`, ktory poprawia CI
na branchu merge requesta. Dwie poprawki tego samego bledu z dwoch sesji to
konflikt na branchu, dlatego sesja ticketu konczy sie na wystawieniu merge
requesta i raporcie. Wyjatek, tylko w trybie interaktywnym: uzytkownik zleca
poprawke czerwonego CI w TEJ sesji -> robisz ja tutaj i mowisz mu wprost, zeby
dla tego merge requesta nie uruchamial `/monolynx:mr-queue`, dopoki poprawka
nie jest wypchnieta.

`MR_URL`, `PIPELINE_ID` i `PIPELINE_STATUS` podajesz w miejscach wskazanych
przez skill: jako dowod pelnego przebiegu, razem ze zdaniem, ze czerwony
pipeline naprawia `/monolynx:mr-queue`.
