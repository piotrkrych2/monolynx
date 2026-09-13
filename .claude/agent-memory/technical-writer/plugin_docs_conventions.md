---
name: plugin-docs-conventions
description: Konwencje pisania dokumentacji pod plugin/ - diakrytyki tak/nie w zaleznosci od pliku, walidator, sync kopii skilli
metadata:
  type: project
---

Dokumentacja pod `plugin/` ma DWIE rozne konwencje diakrytykow:

- `plugin/skills/*/SKILL.md` - polski **BEZ** polskich znakow (`narzedzie`, `zaleznosci`). Tresc trafia do promptow.
- `plugin/README.md` i `plugin/.claude-plugin/plugin.json` - polski **Z pelnymi** diakrytykami. To dokumentacja dla czlowieka.

Wspolne dla obu: **zero em-dash `-` (U+2014) i en-dash (U+2013)** - `scripts/validate_plugin_consistency.py` skanuje kazdy `.md` pod `plugin/` i wywala exit 1.

**Why:** walidator sprawdza tylko dash, nie diakrytyki - te druga konwencje trzeba znac z obserwacji plikow, inaczej latwo napisac SKILL.md z ogonkami i nikt tego nie zlapie automatem.

**How to apply:** przed zgloszeniem gotowosci uruchom oba lokalne skrypty (hook ich nie blokuje, to nie testy projektu):
```
python3 scripts/sync_skills.py            # regeneruje src/monolynx/static/skills/monolynx-*/
python3 scripts/validate_plugin_consistency.py
python3 scripts/sync_skills.py --check
```
`src/monolynx/static/skills/**` jest **generowany** - nigdy nie edytuj recznie (porownanie jest bajt w bajt). W zrodle pisz `<PROJECT_SLUG>` z podkreslnikiem; transformacja sama robi z tego `<PROJECT-SLUG>`.

Pozostale reguly walidatora: referencje `KROK N` / `FAZA N` musza miec pokrycie w naglowku (pula globalna dla calego `plugin/`, nie per plik); komendy pisz `/monolynx:<nazwa>`, nigdy `/monolynx-<nazwa>`; `version` w `plugin.json` musi rownac sie pierwszemu wpisowi `### X.Y.Z` pod `## Changelog` w `plugin/README.md`.

Przy bumpie wersji pluginu: nowy wpis changelogu idzie na **poczatek** sekcji, a historycznych wpisow `### X.Y.Z` nie przepisuje sie (opisuja, co weszlo w tamtej wersji). Opis skilla aktualizuje sie w tabeli "Skille (komendy `/monolynx:*`)" pod `## Zawartosc pluginu`.

Powiazane: [[konwencje-pisania-pl]]
