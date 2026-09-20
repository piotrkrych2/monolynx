---
name: patterns-markdown-marker-parsing
description: Parsowanie parowanych znacznikow HTML-comment w SKILL.md (shared:*, runtime:*) i weryfikacja bez odpalania walidatora
metadata:
  type: project
---

Wzorzec z MON-189 (`check_shared_blocks` w `scripts/validate_plugin_consistency.py`,
obok istniejacego `check_runtime_contract`): parsowanie parowanych znacznikow
`<!-- shared:<nazwa> -->` / `<!-- /shared:<nazwa> -->` linia-po-linii, ze stosem
(3 rownolegle listy: nazwy, linie startu, bufory tresci - nie `dict[str, object]`,
bo mypy). Dwie formy: blokowa (caly wiersz to znacznik, `^\s*<!--...-->\s*$`) i
liniowa (znacznik na KONCU wiersza z trescia przed nim - potrzebna w wierszach
tabel GFM, bo samodzielna linia komentarza HTML konczy tabele). Normalizacja do
porownania: WYLACZNIE `re.sub(r"\s+", " ", text).strip()` - zadnego fuzzy
matchingu, bo szersza normalizacja przepuszcza rozjazd znaczeniowy.

W GFM, tresc dopisana po ostatnim `|` wiersza tabeli (np. `... | <!-- shared:x -->`)
staje sie trescia dodatkowej "kolumny bez naglowka", nie zrywa tabeli - to bezpieczny
sposob znakowania wierszy tabeli bez lamania renderowania.

**Reflow przy wstawianiu znacznikow blokowych w istniejacy tekst**: gdy granica
bloku wypada w SRODKU istniejacej fizycznej linii (typowe gdy jeden plik ma tekst
zawiniety na kilka linii, a odpowiednik w drugim pliku to jedna dluga linia),
jedyny dozwolony reflow to przelamanie DOKLADNIE w tym miejscu - zero zmiany
slow/kolejnosci. Nie "upiekszaj" formatowania calego akapitu przy okazji.

**Weryfikacja bez odpalania whatever-jest-pod-testem**: gdy instrukcja zabrania
uruchamiania samego walidatora/testow (bo to zadanie kogos innego w pipeline),
napisanie WLASNEGO niezaleznego skryptu-reimplementacji w katalogu tymczasowym
(job tmp dir, nie repo) i porownanie nim tresci recznie jest akceptowalna
weryfikacja - to nie jest "odpalanie testu", to odpalanie wlasnego narzedzia
pomocniczego. Uzyteczne gdy recznie porownywanie dlugich fragmentow tekstu jest
zawodne (latwo przeoczyc pojedyncza spacje/slowo).

Related: [[patterns_edit_tool]]
