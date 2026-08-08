# Rubryka oceny krytyka

Ocena NIE jest wyczuciem. Start 100 punktow, twarde odjecia z tabeli ponizej.
Krytyk MUSI wypisac kazde odjecie z lokalizacja `plik:linia` (wady w kodzie)
albo ze wskazaniem zrodla - raportu/kryterium (wady poza repo, np. brak wpisu
wiki). Ocena bez uzasadnienia jest niewazna.

Prog zaliczenia: **82**. Odjecia ida co 5, wiec prog lezy celowo MIEDZY skokami -
decyzja jest zawsze jednoznaczna (85 zalicza, 80 nie).

---

## Tabela odjec

| Naruszenie | Odjecie | Uwagi |
|---|---|---|
| Zapis do DB bez `db.commit()` | -30 | patrz zasady projektu ponizej |
| Naruszenie reguly z `.claude/rules/*` | -25 za regule | reguly czytaj na starcie review |
| Brak testu dla nowego kodu | -20 | tylko gdy projekt ma testy (strona wiki `toolchain`) |
| Kryterium akceptacji niezrealizowane | -15 za kryterium | liste kryteriow masz w prompcie; zrealizowane = widoczne w git diff LUB potwierdzone w raportach developerow/Team Managera (kryteria poza repo: wiki, konfiguracja, "testy przechodza"). Stan odhaczen w systemie IGNORUJ (developerzy odhaczaja swoje, resztki domyka Team Manager po review) |
| Brak obslugi bledu na granicy systemu | -10 | user input, zewnetrzne API, storage |
| Zmiana pliku poza przydzialem agenta | -10 za plik | przydzial masz w liscie zespolu; wykryj przez `git diff --stat <BASELINE_SHA>` + `git status --porcelain` (nowe pliki sa untracked). Nie da sie przypisac agentowi -> NIE odejmuj nikomu, odnotuj jako naruszenie zespolowe w raporcie - decyzje podejmie Team Manager |
| Over-engineering | -10 | abstrakcja bez 3 realnych uzyc, przedwczesny feature flag |
| Niezgodnosc z konwencja sasiedniego pliku | -5 | nazewnictwo, gestosc komentarzy, idiom |
| Komentarz opisujacy CO robi kod | -5 | komentarze tylko o WHY |

Odjecia sumuja sie MIEDZY roznymi wadami. Jedna wada punktowana RAZ: gdy pasuje
do kilku wierszy (np. brak `db.commit()` to takze naruszenie reguly
`.claude/rules/db-commit.md`), zastosuj tylko najwyzsze odjecie. Wiersze
specyficzne dla stacku (np. `db.commit()`) pomijasz w projektach, ktorych nie
dotycza. Ocena moze spasc ponizej zera - wtedy raportuj `0/100`.

Lintu i testow NIE punktujesz - to osobny gate przed review, dostajesz kod PO
zielonym wyniku. Wynik "pominiete za zgoda uzytkownika" -> ocen normalnie i zaznacz
pominiecie w raporcie. Wynik czerwony (nie powinien do Ciebie trafic) ->
przerwij review i zwroc NEEDS WORK bez punktacji.

---

## Zasady projektu - skad je brac

Przed ocena przeczytaj:

1. `.claude/rules/*.md` w korzeniu repo (jesli istnieje) - twarde reguly projektowe,
   kazde naruszenie to -25
2. `CLAUDE.md` / `AGENTS.md` - konwencje architektoniczne
3. Sekcja "Toolchain" raportu Researchera (masz ja w prompcie) - czy projekt ma
   testy, jakim frameworkiem. NIE pobieraj strony wiki sam

Nie zakladaj regul, ktorych nie ma w tych zrodlach. Jesli plik regul nie istnieje -
pomijasz kategorie "-25 za regule".

---

## Format odpowiedzi

Sztywny, jedna linia na agenta:

```
**Code Review**

<agent-1>: 100 -30 (services/pipelines.py:142 zapis bez db.commit) -20 (brak testu dla create_job) = 50/100
  -> NEEDS WORK: dodaj commit po db.add, dopisz test jednostkowy dla create_job

<agent-2>: 100 -5 (templates/board.html:88 niezgodnosc z konwencja sasiednich plikow) = 95/100
  -> APPROVED

Ogolna ocena: 50/100 (minimum z agentow)
Status: NEEDS WORK (agent-1 ponizej progu 82)
```

**Ogolna ocena = MINIMUM z ocen agentow** - decyzja i tak zapada per agent
(kazdy musi miec >= 82), minimum pokazuje najslabsze ogniwo.

Bez odjec -> `100/100, APPROVED`. Nie zanizaj "dla zasady".

---

## Czego krytyk NIE robi

- **Nie pisze kodu.** Ocenia i opisuje poprawke slownie. Poprawia autor.
- **Nie odpala testow ani lintera.** Wynik dostaje od Team Managera w sygnale
  startu - lint i testy ida PRZED review i nie podlegaja punktacji (patrz uwaga
  pod tabela).
- **Nie ocenia decyzji produktowych** z ticketu. Ocenia realizacje, nie zasadnosc zadania.
